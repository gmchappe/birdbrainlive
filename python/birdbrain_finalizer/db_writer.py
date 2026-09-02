from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from statistics import stdev
from typing import Any, Iterable, Mapping

import psycopg

from core import (
    FinalizerValidationError,
    ParticipantInput,
    RoundPlan,
    applied_handicap_for_next_round,
    plan_round,
)
from db_preflight import connect_admin, fetch_round

FINALIZER_VERSION = "0.2.0"


class DryRunRollback(RuntimeError):
    """Internal sentinel used to exercise the full write path then roll back."""


@dataclass(frozen=True)
class RoundContext:
    round_id: int
    season_id: int
    league_id: int
    layout_id: int
    round_no: int
    status: str
    season_name: str
    season_year: int | None
    course: str
    layout: str
    scheduled_date: Any
    layout_par: int
    hole_count: int
    points_multiplier: int
    payout_contribution: int
    postseason_contribution: int
    ace_contribution: int
    ace_pot_start: int | None


@dataclass(frozen=True)
class LoadedParticipant:
    input: ParticipantInput
    player_id: int | None
    score_to_par: int | None
    holes_scored: int
    ace_holes: tuple[int, ...]
    prior_current_season_rounds: int


@dataclass(frozen=True)
class ShamPoolStat:
    course_name: str
    layout_name: str
    par: int
    pool: str
    players: int
    strokes: int
    par_strokes: int


@dataclass(frozen=True)
class ShamLayoutModel:
    course_name: str
    layout_name: str
    par: int
    grand_rounds: int
    grand_strokes: int
    grand_par: int
    grand_mean: Decimal
    rating: Decimal
    slope: Decimal
    standardized_slope: Decimal
    weight: Decimal


@dataclass(frozen=True)
class FinalizationSummary:
    fingerprint: str
    round_id: int
    round_no: int
    field_size: int
    purse: int
    result_count: int
    financial_transaction_count: int
    handicap_adjustment_count: int
    ace_award_count: int
    course_record_count: int
    status: str


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _quantize_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def load_round_context(
    cur: psycopg.Cursor,
    round_no: int,
    season_year: int | None,
    *,
    for_update: bool,
) -> RoundContext:
    info = fetch_round(cur, round_no, season_year, for_update=for_update)
    cur.execute(
        """
        SELECT
          r.round_id,
          r.season_id,
          s.league_id,
          r.layout_id,
          r.round_no,
          r.status,
          s.season_name,
          s.season_year,
          c.name,
          l.name,
          r.scheduled_date,
          l.par,
          l.hole_count,
          r.points_multiplier,
          r.payout_contribution,
          r.postseason_contribution,
          r.ace_contribution,
          r.ace_pot_start
        FROM rounds r
        JOIN seasons s ON s.season_id = r.season_id
        JOIN layouts l ON l.layout_id = r.layout_id
        JOIN courses c ON c.course_id = l.course_id
        WHERE r.round_id = %s
        """,
        (info.round_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Locked round disappeared unexpectedly.")
    context = RoundContext(*row)
    if context.layout_par is None:
        raise FinalizerValidationError(
            f"R{context.round_no} layout {context.course} / {context.layout} has no authoritative par."
        )
    if context.hole_count <= 0:
        raise FinalizerValidationError("Layout hole_count must be positive.")
    return context


def _prior_current_season_rounds(
    cur: psycopg.Cursor, season_id: int, player_id: int, round_no: int
) -> int:
    cur.execute(
        """
        SELECT
          COALESCE(sps.rounds, 0)::integer
          +
          COUNT(rr.round_result_id) FILTER (
            WHERE r.status = 'finalized'
              AND r.round_no < %s
              AND rp.participant_type = 'member'
              AND rp.status IN ('active','dnf')
          )::integer
        FROM players p
        LEFT JOIN season_player_summaries sps
          ON sps.player_id = p.player_id
         AND sps.season_id = %s
        LEFT JOIN rounds r
          ON r.season_id = %s
         AND r.round_no < %s
         AND (
              sps.season_player_summary_id IS NULL
              OR (
                sps.through_round_no IS NOT NULL
                AND r.round_no > sps.through_round_no
              )
         )
        LEFT JOIN round_participants rp
          ON rp.round_id = r.round_id
         AND rp.player_id = p.player_id
        LEFT JOIN round_results rr
          ON rr.round_participant_id = rp.round_participant_id
        WHERE p.player_id = %s
        GROUP BY
          p.player_id,
          sps.rounds,
          sps.season_player_summary_id,
          sps.through_round_no
        """,
        (round_no, season_id, season_id, round_no, player_id),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _applied_handicap_for_round(
    cur: psycopg.Cursor,
    context: RoundContext,
    player_id: int,
    prior_rounds: int,
) -> Decimal:
    if prior_rounds == 0:
        return Decimal("0")
    cur.execute(
        """
        SELECT hc.applied_handicap
        FROM handicap_calculations hc
        JOIN rounds hr ON hr.round_id = hc.effective_after_round_id
        WHERE hc.player_id = %s
          AND hr.season_id = %s
          AND hr.round_no < %s
        ORDER BY
          hr.round_no DESC,
          hc.calculated_at DESC,
          hc.handicap_calculation_id DESC
        LIMIT 1
        """,
        (player_id, context.season_id, context.round_no),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return Decimal("0")
    return _decimal(row[0])


def load_participants(
    cur: psycopg.Cursor, context: RoundContext
) -> list[LoadedParticipant]:
    cur.execute(
        """
        SELECT
          rp.round_participant_id,
          rp.player_id,
          rp.display_name,
          rp.participant_type,
          rp.status,
          rp.started_round,
          COUNT(hs.hole_score_id)::integer AS holes_scored,
          COALESCE(SUM(hs.strokes), 0)::integer AS gross_score,
          ARRAY_REMOVE(
            ARRAY_AGG(hs.hole_number ORDER BY hs.hole_number)
              FILTER (WHERE hs.strokes = 1),
            NULL
          ) AS ace_holes,
          pr.resolved_finish
        FROM round_participants rp
        LEFT JOIN hole_scores hs
          ON hs.round_participant_id = rp.round_participant_id
        LEFT JOIN playoff_resolutions pr
          ON pr.round_id = rp.round_id
         AND pr.round_participant_id = rp.round_participant_id
        WHERE rp.round_id = %s
        GROUP BY
          rp.round_participant_id,
          rp.player_id,
          rp.display_name,
          rp.participant_type,
          rp.status,
          rp.started_round,
          pr.resolved_finish
        ORDER BY rp.round_participant_id
        """,
        (context.round_id,),
    )
    rows = cur.fetchall()
    if not rows:
        raise FinalizerValidationError("Round has no participants.")

    loaded: list[LoadedParticipant] = []
    for row in rows:
        (
            round_participant_id,
            player_id,
            display_name,
            participant_type,
            status,
            started_round,
            holes_scored,
            gross_score_sum,
            ace_holes,
            playoff_finish,
        ) = row
        holes_scored = int(holes_scored)
        gross_score = int(gross_score_sum) if holes_scored > 0 else None

        if participant_type == "member" and player_id is None:
            raise FinalizerValidationError(
                f"Member participant {display_name!r} has no player_id."
            )

        prior_rounds = 0
        applied = Decimal("0")
        if player_id is not None and participant_type == "member":
            prior_rounds = _prior_current_season_rounds(
                cur, context.season_id, int(player_id), context.round_no
            )
            applied = _applied_handicap_for_round(
                cur, context, int(player_id), prior_rounds
            )

        is_active_finisher = (
            participant_type == "member"
            and bool(started_round)
            and status == "active"
        )
        if is_active_finisher and holes_scored != context.hole_count:
            raise FinalizerValidationError(
                f"{display_name} is active but has "
                f"{holes_scored}/{context.hole_count} hole scores."
            )

        score_to_par = (
            gross_score - context.layout_par
            if gross_score is not None and status == "active"
            else None
        )
        loaded.append(
            LoadedParticipant(
                input=ParticipantInput(
                    round_participant_id=int(round_participant_id),
                    name=display_name,
                    participant_type=participant_type,
                    status=status,
                    started_round=bool(started_round),
                    gross_score=gross_score,
                    applied_handicap=applied,
                    playoff_finish=(
                        int(playoff_finish)
                        if playoff_finish is not None
                        else None
                    ),
                    prior_season_eligible_rounds=prior_rounds,
                ),
                player_id=int(player_id) if player_id is not None else None,
                score_to_par=score_to_par,
                holes_scored=holes_scored,
                ace_holes=tuple(int(x) for x in (ace_holes or [])),
                prior_current_season_rounds=prior_rounds,
            )
        )

    eligible_field = [p for p in loaded if p.input.is_field_eligible]
    if not eligible_field:
        raise FinalizerValidationError(
            "Round has zero eligible member participants."
        )
    return loaded


def _sham_is_active(cur: psycopg.Cursor, context: RoundContext) -> bool:
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT r.round_id)::integer,
          COUNT(DISTINCT rp.player_id)::integer
        FROM rounds r
        LEFT JOIN round_participants rp
          ON rp.round_id = r.round_id
         AND rp.participant_type = 'member'
         AND rp.started_round
         AND rp.status IN ('active','dnf')
        WHERE r.season_id = %s
          AND r.status = 'finalized'
          AND r.round_no < %s
        """,
        (context.season_id, context.round_no),
    )
    completed_rounds, unique_players = cur.fetchone()
    return int(completed_rounds) >= 11 and int(unique_players) >= 40


def _latest_pool(
    cur: psycopg.Cursor, context: RoundContext, player_id: int
) -> str | None:
    cur.execute(
        """
        SELECT pool
        FROM player_pool_assignments
        WHERE season_id = %s
          AND player_id = %s
          AND effective_round_no <= %s
          AND pool IS NOT NULL
        ORDER BY
          effective_round_no DESC,
          player_pool_assignment_id DESC
        LIMIT 1
        """,
        (context.season_id, player_id, context.round_no),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _sample_sd(values: list[int]) -> Decimal | None:
    if len(values) < 2:
        return None
    return Decimal(str(stdev(values)))


def build_current_pool_stats(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: Iterable[LoadedParticipant],
) -> list[tuple[ShamPoolStat, Decimal | None]]:
    by_pool: dict[str, list[int]] = {}
    for participant in participants:
        if (
            participant.player_id is None
            or not participant.input.is_scoring_finisher
        ):
            continue
        pool = _latest_pool(cur, context, participant.player_id)
        if pool is None:
            continue
        by_pool.setdefault(pool, []).append(
            int(participant.input.gross_score)
        )

    stats: list[tuple[ShamPoolStat, Decimal | None]] = []
    for pool, scores in sorted(by_pool.items()):
        players = len(scores)
        strokes = sum(scores)
        stats.append(
            (
                ShamPoolStat(
                    course_name=context.course,
                    layout_name=context.layout,
                    par=context.layout_par,
                    pool=pool,
                    players=players,
                    strokes=strokes,
                    par_strokes=context.layout_par * players,
                ),
                _sample_sd(scores),
            )
        )
    if not stats:
        raise FinalizerValidationError(
            "SHAM is active but no scoring finishers have eligible "
            "player-pool assignments."
        )
    return stats


def load_historical_pool_stats(
    cur: psycopg.Cursor,
) -> list[ShamPoolStat]:
    cur.execute(
        """
        SELECT
          course_name,
          layout_name,
          par,
          pool,
          players,
          strokes,
          COALESCE(par_strokes, par * players)
        FROM sham_pool_round_stats
        WHERE par IS NOT NULL
          AND players > 0
        """
    )
    return [
        ShamPoolStat(
            course_name=row[0],
            layout_name=row[1],
            par=int(row[2]),
            pool=row[3],
            players=int(row[4]),
            strokes=int(row[5]),
            par_strokes=int(row[6]),
        )
        for row in cur.fetchall()
    ]


def _linear_slope(xs: list[Decimal], ys: list[Decimal]) -> Decimal:
    if len(xs) < 2:
        raise FinalizerValidationError(
            "SHAM layout requires at least two represented pools."
        )
    xbar = sum(xs, Decimal("0")) / Decimal(len(xs))
    ybar = sum(ys, Decimal("0")) / Decimal(len(ys))
    denom = sum((x - xbar) ** 2 for x in xs)
    if denom == 0:
        raise FinalizerValidationError(
            "SHAM layout pool regression has zero x variance."
        )
    return (
        sum(
            (x - xbar) * (y - ybar)
            for x, y in zip(xs, ys)
        )
        / denom
    )


def compute_sham_models(
    stats: Iterable[ShamPoolStat],
) -> dict[tuple[str, str, int], ShamLayoutModel]:
    all_stats = list(stats)
    if not all_stats:
        raise FinalizerValidationError(
            "Cannot compute SHAM without pool history."
        )

    grand_players = sum(s.players for s in all_stats)
    grand_strokes = sum(s.strokes for s in all_stats)
    grand_par = sum(s.par_strokes for s in all_stats)
    if grand_players <= 0:
        raise FinalizerValidationError(
            "SHAM history has zero player observations."
        )
    grand_mean = (
        Decimal(grand_strokes - grand_par) / Decimal(grand_players)
    )

    pool_number = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
    grouped: dict[
        tuple[str, str, int], dict[str, list[int]]
    ] = {}
    for stat in all_stats:
        key = (stat.course_name, stat.layout_name, stat.par)
        bucket = grouped.setdefault(key, {})
        aggregate = bucket.setdefault(stat.pool, [0, 0, 0])
        aggregate[0] += stat.players
        aggregate[1] += stat.strokes
        aggregate[2] += stat.par_strokes

    prelim: dict[
        tuple[str, str, int], tuple[Decimal, Decimal]
    ] = {}
    slopes: list[Decimal] = []
    for key, pools in grouped.items():
        xs: list[Decimal] = []
        ys: list[Decimal] = []
        layout_players = 0
        layout_strokes = 0
        layout_par = 0
        for pool, (players, strokes, par_strokes) in sorted(
            pools.items(), key=lambda item: pool_number[item[0]]
        ):
            if players <= 0:
                continue
            xs.append(Decimal(pool_number[pool]))
            ys.append(
                Decimal(strokes - par_strokes) / Decimal(players)
            )
            layout_players += players
            layout_strokes += strokes
            layout_par += par_strokes
        if layout_players <= 0 or len(xs) < 2:
            continue
        slope = _linear_slope(xs, ys)
        prerating = (
            Decimal(layout_strokes - layout_par)
            / Decimal(layout_players)
        )
        rating = prerating - grand_mean
        prelim[key] = (rating, slope)
        slopes.append(slope)

    if not prelim:
        raise FinalizerValidationError(
            "SHAM history produced no estimable layout models."
        )

    slope_mean = sum(slopes, Decimal("0")) / Decimal(len(slopes))
    if len(slopes) > 1:
        variance = (
            sum((slope - slope_mean) ** 2 for slope in slopes)
            / Decimal(len(slopes) - 1)
        )
        slope_sd = variance.sqrt()
    else:
        slope_sd = Decimal("0")

    models: dict[tuple[str, str, int], ShamLayoutModel] = {}
    for key, (rating, slope) in prelim.items():
        standardized = (
            Decimal("0")
            if slope_sd == 0
            else (slope - slope_mean) / slope_sd
        )
        if standardized >= Decimal("3"):
            weight = Decimal("1.2")
        elif standardized >= Decimal("1"):
            weight = Decimal("1.1")
        elif standardized >= Decimal("0.5"):
            weight = Decimal("1.05")
        elif standardized >= Decimal("-0.5"):
            weight = Decimal("1")
        elif standardized >= Decimal("-1"):
            weight = Decimal("0.95")
        elif standardized >= Decimal("-3"):
            weight = Decimal("0.90")
        else:
            weight = Decimal("0.80")
        models[key] = ShamLayoutModel(
            course_name=key[0],
            layout_name=key[1],
            par=key[2],
            grand_rounds=grand_players,
            grand_strokes=grand_strokes,
            grand_par=grand_par,
            grand_mean=grand_mean,
            rating=rating,
            slope=slope,
            standardized_slope=standardized,
            weight=weight,
        )
    return models


def _adjustments_for_player(
    cur: psycopg.Cursor, player_id: int, season_id: int
) -> list[tuple[int, Decimal]]:
    cur.execute(
        """
        SELECT ha.handicap_adjustment_id, ha.adjustment
        FROM handicap_adjustments ha
        JOIN rounds r ON r.round_id = ha.round_id
        WHERE ha.player_id = %s
          AND r.season_id = %s
        ORDER BY ha.adjustment ASC, ha.handicap_adjustment_id ASC
        """,
        (player_id, season_id),
    )
    return [
        (int(row[0]), _decimal(row[1]))
        for row in cur.fetchall()
    ]


def _trimmed_handicap(
    rows: list[tuple[int, Decimal]],
) -> tuple[Decimal, dict[int, tuple[bool, str | None]]]:
    if not rows:
        raise FinalizerValidationError(
            "Cannot calculate handicap without adjustments."
        )
    cut = len(rows) // 5
    flags: dict[int, tuple[bool, str | None]] = {}
    kept: list[Decimal] = []
    for index, (adjustment_id, value) in enumerate(rows):
        if cut and index < cut:
            flags[adjustment_id] = (False, "low")
        elif cut and index >= len(rows) - cut:
            flags[adjustment_id] = (False, "high")
        else:
            flags[adjustment_id] = (True, None)
            kept.append(value)
    precise = sum(kept, Decimal("0")) / Decimal(len(kept))
    return precise, flags


def _financial_key(
    context: RoundContext,
    participant_id: int | None,
    purpose: str,
) -> str:
    who = (
        f"rp{participant_id}"
        if participant_id is not None
        else "round"
    )
    return f"birdbrain:v1:r{context.round_id}:{who}:{purpose}"


def _ace_available_balance(
    cur: psycopg.Cursor, context: RoundContext
) -> int:
    cur.execute(
        """
        SELECT COALESCE(SUM(amount), 0)::integer
        FROM financial_transactions
        WHERE league_id = %s
          AND fund_type = 'ace_pot'
          AND (season_id = %s OR season_id IS NULL)
        """,
        (context.league_id, context.season_id),
    )
    return int(cur.fetchone()[0])


def parse_ace_allocations(
    values: Iterable[str],
) -> dict[tuple[int, int], int]:
    allocations: dict[tuple[int, int], int] = {}
    for value in values:
        try:
            lhs, amount_text = value.split("=", 1)
            participant_text, hole_text = lhs.split(":", 1)
            key = (int(participant_text), int(hole_text))
            amount = int(amount_text)
        except Exception as exc:
            raise FinalizerValidationError(
                f"Invalid --ace-award {value!r}; expected "
                "ROUND_PARTICIPANT_ID:HOLE=AMOUNT."
            ) from exc
        if amount < 0:
            raise FinalizerValidationError(
                "Ace award amounts cannot be negative."
            )
        if key in allocations:
            raise FinalizerValidationError(
                f"Duplicate ace allocation for {key}."
            )
        allocations[key] = amount
    return allocations


def canonical_input(
    context: RoundContext,
    participants: Iterable[LoadedParticipant],
    ace_allocations: Mapping[tuple[int, int], int],
) -> dict[str, Any]:
    return {
        "finalizer_version": FINALIZER_VERSION,
        "round": {
            "round_id": context.round_id,
            "season_id": context.season_id,
            "layout_id": context.layout_id,
            "round_no": context.round_no,
            "scheduled_date": str(context.scheduled_date),
            "layout_par": context.layout_par,
            "hole_count": context.hole_count,
            "points_multiplier": context.points_multiplier,
            "payout_contribution": context.payout_contribution,
            "postseason_contribution": context.postseason_contribution,
            "ace_contribution": context.ace_contribution,
            "ace_pot_start": context.ace_pot_start,
        },
        "participants": [
            {
                "round_participant_id": p.input.round_participant_id,
                "player_id": p.player_id,
                "name": p.input.name,
                "participant_type": p.input.participant_type,
                "status": p.input.status,
                "started_round": p.input.started_round,
                "gross_score": p.input.gross_score,
                "applied_handicap": _quantize_text(
                    _decimal(p.input.applied_handicap)
                ),
                "playoff_finish": p.input.playoff_finish,
                "prior_current_season_rounds": (
                    p.prior_current_season_rounds
                ),
                "ace_holes": list(p.ace_holes),
            }
            for p in sorted(
                participants,
                key=lambda item: item.input.round_participant_id,
            )
        ],
        "ace_allocations": [
            {
                "round_participant_id": key[0],
                "hole": key[1],
                "amount": amount,
            }
            for key, amount in sorted(ace_allocations.items())
        ],
    }


def input_fingerprint(
    context: RoundContext,
    participants: Iterable[LoadedParticipant],
    ace_allocations: Mapping[tuple[int, int], int],
) -> str:
    payload = canonical_input(context, participants, ace_allocations)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_receipt(
    cur: psycopg.Cursor, round_id: int
) -> tuple | None:
    cur.execute(
        """
        SELECT
          input_fingerprint,
          finalizer_version,
          result_count,
          financial_transaction_count,
          handicap_adjustment_count,
          ace_award_count,
          course_record_count
        FROM round_finalization_receipts
        WHERE round_id = %s
        """,
        (round_id,),
    )
    return cur.fetchone()


def _insert_result_rows(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
    plan: RoundPlan,
) -> int:
    by_id = {
        p.input.round_participant_id: p for p in participants
    }
    count = 0
    for result in plan.results:
        source = by_id[result.round_participant_id]
        cur.execute(
            """
            INSERT INTO round_results (
              round_participant_id,
              gross_score,
              score_to_par,
              applied_handicap,
              net_score,
              competition_rank,
              official_finish,
              cash_position,
              points,
              payout_award,
              finalized_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            """,
            (
                result.round_participant_id,
                result.gross_score,
                source.score_to_par,
                result.applied_handicap,
                result.net_score,
                result.competition_rank,
                result.official_finish,
                result.cash_position,
                result.points,
                result.payout_award,
            ),
        )
        count += 1
    return count


def _insert_financial_rows(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
    plan: RoundPlan,
) -> int:
    by_result = {
        result.round_participant_id: result
        for result in plan.results
    }
    count = 0
    for participant in participants:
        item = participant.input
        if (
            not item.is_field_eligible
            or participant.player_id is None
        ):
            continue
        base = (
            context.league_id,
            context.season_id,
            context.round_id,
            participant.player_id,
        )
        entries: list[tuple[str, str, int, str, str]] = []
        if context.payout_contribution:
            entries.append(
                (
                    "round_payout",
                    "contribution",
                    context.payout_contribution,
                    "Round payout contribution",
                    _financial_key(
                        context,
                        item.round_participant_id,
                        "round-payout-contribution",
                    ),
                )
            )
        if context.ace_contribution:
            entries.append(
                (
                    "ace_pot",
                    "ace_pot_contribution",
                    context.ace_contribution,
                    "Ace-pot contribution",
                    _financial_key(
                        context,
                        item.round_participant_id,
                        "ace-contribution",
                    ),
                )
            )
        if context.postseason_contribution:
            entries.append(
                (
                    "postseason",
                    "contribution",
                    context.postseason_contribution,
                    "Round postseason contribution",
                    _financial_key(
                        context,
                        item.round_participant_id,
                        "postseason-contribution",
                    ),
                )
            )
        if participant.prior_current_season_rounds == 0:
            entries.append(
                (
                    "postseason",
                    "season_contribution",
                    5,
                    "First eligible season appearance postseason contribution",
                    _financial_key(
                        context,
                        item.round_participant_id,
                        "first-season-contribution",
                    ),
                )
            )
        payout = by_result[item.round_participant_id].payout_award
        if payout:
            entries.append(
                (
                    "round_payout",
                    "award",
                    -int(payout),
                    "Round payout award",
                    _financial_key(
                        context,
                        item.round_participant_id,
                        "round-payout-award",
                    ),
                )
            )
        for (
            fund_type,
            transaction_type,
            amount,
            memo,
            key,
        ) in entries:
            cur.execute(
                """
                INSERT INTO financial_transactions (
                  league_id,
                  season_id,
                  round_id,
                  player_id,
                  fund_type,
                  transaction_type,
                  amount,
                  memo,
                  finalization_key
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    *base,
                    fund_type,
                    transaction_type,
                    amount,
                    memo,
                    key,
                ),
            )
            count += 1
    return count


def _insert_aces(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
    ace_allocations: Mapping[tuple[int, int], int],
) -> tuple[int, int]:
    ace_occurrences: list[tuple[LoadedParticipant, int]] = []
    for participant in participants:
        if not participant.input.is_field_eligible:
            continue
        for hole in participant.ace_holes:
            ace_occurrences.append((participant, hole))

    # Contribution rows have already been inserted in this transaction, so the
    # current ledger balance is the full pot available for this round. This also
    # preserves the migration opening-balance adjustment already attached to R41.
    pot = _ace_available_balance(cur, context)

    if not ace_occurrences:
        if ace_allocations:
            raise FinalizerValidationError(
                "Ace allocations supplied but no hole score of 1 exists."
            )
        return 0, 0

    occurrence_keys = {
        (participant.input.round_participant_id, hole)
        for participant, hole in ace_occurrences
    }
    if len(ace_occurrences) == 1 and not ace_allocations:
        only = next(iter(occurrence_keys))
        allocations = {only: pot}
    else:
        allocations = dict(ace_allocations)
        if set(allocations) != occurrence_keys:
            raise FinalizerValidationError(
                "Multiple aces require one explicit allocation for every "
                "ace occurrence."
            )
        if sum(allocations.values()) != pot:
            raise FinalizerValidationError(
                f"Ace allocations must sum to the full ${pot} pot; "
                f"got ${sum(allocations.values())}."
            )

    award_count = 0
    financial_count = 0
    by_key = {
        (participant.input.round_participant_id, hole): participant
        for participant, hole in ace_occurrences
    }
    for key, amount in sorted(allocations.items()):
        participant = by_key[key]
        if participant.player_id is None:
            raise FinalizerValidationError(
                "Ace recipient must have a player_id."
            )
        cur.execute(
            """
            INSERT INTO ace_awards (
              round_id,
              round_participant_id,
              player_id,
              layout_id,
              achieved_on,
              hole_number,
              payout
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                context.round_id,
                participant.input.round_participant_id,
                participant.player_id,
                context.layout_id,
                context.scheduled_date,
                key[1],
                amount,
            ),
        )
        award_count += 1
        if amount:
            cur.execute(
                """
                INSERT INTO financial_transactions (
                  league_id,
                  season_id,
                  round_id,
                  player_id,
                  fund_type,
                  transaction_type,
                  amount,
                  memo,
                  finalization_key
                ) VALUES (
                  %s,%s,%s,%s,'ace_pot','award',%s,%s,%s
                )
                """,
                (
                    context.league_id,
                    context.season_id,
                    context.round_id,
                    participant.player_id,
                    -amount,
                    f"Ace award: hole {key[1]}",
                    _financial_key(
                        context,
                        participant.input.round_participant_id,
                        f"ace-award-hole-{key[1]}",
                    ),
                ),
            )
            financial_count += 1
    return award_count, financial_count


def _insert_course_records(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
) -> int:
    candidates = [
        participant
        for participant in participants
        if participant.input.status == "active"
        and participant.input.started_round
        and participant.input.gross_score is not None
        and participant.player_id is not None
    ]
    if not candidates:
        return 0

    round_best = min(
        int(participant.input.gross_score)
        for participant in candidates
    )
    cur.execute(
        "SELECT MIN(score) FROM course_records WHERE layout_id = %s",
        (context.layout_id,),
    )
    row = cur.fetchone()
    historical_best = (
        int(row[0]) if row and row[0] is not None else None
    )
    if historical_best is not None and round_best > historical_best:
        return 0

    count = 0
    for participant in candidates:
        if int(participant.input.gross_score) != round_best:
            continue
        cur.execute(
            """
            INSERT INTO course_records (
              layout_id,
              round_id,
              player_id,
              score,
              achieved_on
            ) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            (
                context.layout_id,
                context.round_id,
                participant.player_id,
                int(participant.input.gross_score),
                context.scheduled_date,
            ),
        )
        count += cur.rowcount
    return count


def _insert_sham_and_handicaps(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
) -> tuple[int, int]:
    active = _sham_is_active(cur, context)
    current_model: ShamLayoutModel | None = None
    if active:
        current_stats = build_current_pool_stats(
            cur, context, participants
        )
        history = load_historical_pool_stats(cur)
        models = compute_sham_models(
            history + [item[0] for item in current_stats]
        )
        current_key = (
            context.course,
            context.layout,
            context.layout_par,
        )
        current_model = models.get(current_key)
        if current_model is None:
            raise FinalizerValidationError(
                "Current layout did not produce an estimable SHAM model."
            )

        for stat, standard_deviation in current_stats:
            cur.execute(
                """
                INSERT INTO sham_pool_round_stats (
                  season_id,
                  round_id,
                  legacy_round_no,
                  course_name,
                  layout_name,
                  par,
                  pool,
                  players,
                  strokes,
                  average,
                  stddev,
                  par_strokes
                ) VALUES (
                  %s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    context.season_id,
                    context.round_id,
                    stat.course_name,
                    stat.layout_name,
                    stat.par,
                    stat.pool,
                    stat.players,
                    stat.strokes,
                    Decimal(stat.strokes) / Decimal(stat.players),
                    standard_deviation,
                    stat.par_strokes,
                ),
            )

        # Legacy bbsham rewrites the full model sheet each round, so retain a
        # normalized model snapshot for every estimable layout.
        for model in models.values():
            cur.execute(
                """
                SELECT l.layout_id
                FROM layouts l
                JOIN courses c ON c.course_id = l.course_id
                WHERE c.name = %s
                  AND l.name = %s
                LIMIT 1
                """,
                (model.course_name, model.layout_name),
            )
            layout_row = cur.fetchone()
            if layout_row is None:
                continue
            cur.execute(
                """
                INSERT INTO sham_layout_models (
                  season_id,
                  layout_id,
                  effective_after_round_no,
                  total_rounds,
                  stroke_total,
                  par_total,
                  grand_mean,
                  rating,
                  slope,
                  standardized_slope,
                  weight
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (
                  season_id, layout_id, effective_after_round_no
                )
                DO UPDATE SET
                  total_rounds = EXCLUDED.total_rounds,
                  stroke_total = EXCLUDED.stroke_total,
                  par_total = EXCLUDED.par_total,
                  grand_mean = EXCLUDED.grand_mean,
                  rating = EXCLUDED.rating,
                  slope = EXCLUDED.slope,
                  standardized_slope = EXCLUDED.standardized_slope,
                  weight = EXCLUDED.weight
                """,
                (
                    context.season_id,
                    int(layout_row[0]),
                    context.round_no,
                    model.grand_rounds,
                    model.grand_strokes,
                    model.grand_par,
                    model.grand_mean,
                    model.rating,
                    model.slope,
                    model.standardized_slope,
                    model.weight,
                ),
            )

    adjustment_count = 0
    calculation_count = 0
    for participant in participants:
        if (
            participant.player_id is None
            or not participant.input.is_scoring_finisher
            or participant.score_to_par is None
        ):
            continue

        if active:
            assert current_model is not None
            adjustment = (
                Decimal(participant.score_to_par)
                - current_model.rating
            ) * current_model.weight
            method = "sham"
        else:
            adjustment = Decimal(participant.score_to_par)
            method = "pre_sham_par"

        cur.execute(
            """
            INSERT INTO handicap_adjustments (
              player_id,
              round_id,
              adjustment,
              method
            ) VALUES (%s,%s,%s,%s)
            """,
            (
                participant.player_id,
                context.round_id,
                adjustment,
                method,
            ),
        )
        adjustment_count += 1

        adjustment_rows = _adjustments_for_player(
            cur, participant.player_id, context.season_id
        )
        precise, flags = _trimmed_handicap(adjustment_rows)
        completed_after = participant.prior_current_season_rounds + 1
        applied = applied_handicap_for_next_round(
            precise, completed_after
        )
        cur.execute(
            """
            INSERT INTO handicap_calculations (
              player_id,
              effective_after_round_id,
              precise_handicap,
              applied_handicap,
              trim_fraction
            ) VALUES (%s,%s,%s,%s,0.20)
            RETURNING handicap_calculation_id
            """,
            (
                participant.player_id,
                context.round_id,
                precise,
                applied,
            ),
        )
        calculation_id = int(cur.fetchone()[0])
        calculation_count += 1
        for adjustment_id, (included, trim_side) in flags.items():
            cur.execute(
                """
                INSERT INTO handicap_calculation_adjustments (
                  handicap_calculation_id,
                  handicap_adjustment_id,
                  included,
                  trim_side
                ) VALUES (%s,%s,%s,%s)
                """,
                (
                    calculation_id,
                    adjustment_id,
                    included,
                    trim_side,
                ),
            )
    return adjustment_count, calculation_count


def _reconcile(
    cur: psycopg.Cursor,
    context: RoundContext,
    plan: RoundPlan,
    result_count: int,
    financial_count: int,
    adjustment_count: int,
    ace_count: int,
    course_record_count: int,
) -> None:
    if result_count != len(plan.results):
        raise RuntimeError("Result row reconciliation failed.")
    if sum(result.payout_award for result in plan.results) != plan.purse:
        raise RuntimeError(
            "Planned payout awards do not reconcile to purse."
        )

    cur.execute(
        """
        SELECT COALESCE(SUM(amount), 0)::integer
        FROM financial_transactions
        WHERE round_id = %s
          AND fund_type = 'round_payout'
          AND finalization_key IS NOT NULL
        """,
        (context.round_id,),
    )
    if int(cur.fetchone()[0]) != 0:
        raise RuntimeError(
            "Round-payout ledger does not net to zero."
        )

    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM financial_transactions
        WHERE round_id = %s
          AND finalization_key IS NOT NULL
        """,
        (context.round_id,),
    )
    if int(cur.fetchone()[0]) != financial_count:
        raise RuntimeError(
            "Finalizer financial row count reconciliation failed."
        )

    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM handicap_adjustments
        WHERE round_id = %s
        """,
        (context.round_id,),
    )
    if int(cur.fetchone()[0]) != adjustment_count:
        raise RuntimeError(
            "Handicap adjustment row count reconciliation failed."
        )

    cur.execute(
        "SELECT COUNT(*)::integer FROM ace_awards WHERE round_id = %s",
        (context.round_id,),
    )
    if int(cur.fetchone()[0]) != ace_count:
        raise RuntimeError("Ace award row count reconciliation failed.")

    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM course_records
        WHERE round_id = %s
        """,
        (context.round_id,),
    )
    if int(cur.fetchone()[0]) != course_record_count:
        raise RuntimeError(
            "Course-record row count reconciliation failed."
        )


def apply_finalization(
    cur: psycopg.Cursor,
    context: RoundContext,
    participants: list[LoadedParticipant],
    ace_allocations: Mapping[tuple[int, int], int],
    fingerprint: str,
) -> FinalizationSummary:
    if context.status != "results_review":
        raise FinalizerValidationError(
            f"R{context.round_no} is {context.status!r}; only "
            "'results_review' can finalize."
        )
    receipt = _existing_receipt(cur, context.round_id)
    if receipt is not None:
        raise RuntimeError(
            "Finalization receipt exists while round is not finalized."
        )

    plan = plan_round(
        [participant.input for participant in participants],
        payout_contribution=context.payout_contribution,
        points_multiplier=context.points_multiplier,
    )
    result_count = _insert_result_rows(
        cur, context, participants, plan
    )
    financial_count = _insert_financial_rows(
        cur, context, participants, plan
    )
    ace_count, ace_financial_count = _insert_aces(
        cur, context, participants, ace_allocations
    )
    financial_count += ace_financial_count
    course_record_count = _insert_course_records(
        cur, context, participants
    )
    adjustment_count, _ = _insert_sham_and_handicaps(
        cur, context, participants
    )

    _reconcile(
        cur,
        context,
        plan,
        result_count,
        financial_count,
        adjustment_count,
        ace_count,
        course_record_count,
    )

    cur.execute(
        """
        INSERT INTO round_finalization_receipts (
          round_id,
          input_fingerprint,
          finalizer_version,
          result_count,
          financial_transaction_count,
          handicap_adjustment_count,
          ace_award_count,
          course_record_count
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            context.round_id,
            fingerprint,
            FINALIZER_VERSION,
            result_count,
            financial_count,
            adjustment_count,
            ace_count,
            course_record_count,
        ),
    )
    cur.execute(
        """
        INSERT INTO audit_events (
          league_id,
          season_id,
          round_id,
          event_type,
          event_payload
        ) VALUES (%s,%s,%s,'round_finalized',%s::jsonb)
        """,
        (
            context.league_id,
            context.season_id,
            context.round_id,
            json.dumps(
                {
                    "input_fingerprint": fingerprint,
                    "finalizer_version": FINALIZER_VERSION,
                    "field_size": plan.field_size,
                    "purse": plan.purse,
                    "result_count": result_count,
                    "financial_transaction_count": financial_count,
                    "handicap_adjustment_count": adjustment_count,
                    "ace_award_count": ace_count,
                    "course_record_count": course_record_count,
                },
                sort_keys=True,
            ),
        ),
    )
    cur.execute(
        """
        UPDATE rounds
        SET status = 'finalized',
            finalized_at = now(),
            results_published_at = COALESCE(results_published_at, now())
        WHERE round_id = %s
          AND status = 'results_review'
        """,
        (context.round_id,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("Final round status transition failed.")

    return FinalizationSummary(
        fingerprint=fingerprint,
        round_id=context.round_id,
        round_no=context.round_no,
        field_size=plan.field_size,
        purse=plan.purse,
        result_count=result_count,
        financial_transaction_count=financial_count,
        handicap_adjustment_count=adjustment_count,
        ace_award_count=ace_count,
        course_record_count=course_record_count,
        status="finalized",
    )


def finalize(
    conn: psycopg.Connection,
    round_no: int,
    season_year: int | None,
    ace_allocations: Mapping[tuple[int, int], int],
    *,
    commit: bool,
) -> tuple[FinalizationSummary | None, str]:
    summary: FinalizationSummary | None = None
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                context = load_round_context(
                    cur, round_no, season_year, for_update=True
                )
                receipt = _existing_receipt(cur, context.round_id)

                if context.status == "finalized" and receipt is None:
                    raise FinalizerValidationError(
                        "Round is finalized without a native finalization "
                        "receipt; it is historical/imported and cannot be replayed."
                    )

                participants = load_participants(cur, context)
                fingerprint = input_fingerprint(
                    context, participants, ace_allocations
                )

                if context.status == "finalized":
                    assert receipt is not None
                    if receipt[0] != fingerprint:
                        raise FinalizerValidationError(
                            "Round is already finalized but current inputs "
                            "do not match the committed finalization fingerprint."
                        )
                    summary = FinalizationSummary(
                        fingerprint=receipt[0],
                        round_id=context.round_id,
                        round_no=context.round_no,
                        field_size=0,
                        purse=0,
                        result_count=int(receipt[2]),
                        financial_transaction_count=int(receipt[3]),
                        handicap_adjustment_count=int(receipt[4]),
                        ace_award_count=int(receipt[5]),
                        course_record_count=int(receipt[6]),
                        status="already_finalized",
                    )
                    return summary, "already-finalized no-op"

                summary = apply_finalization(
                    cur,
                    context,
                    participants,
                    ace_allocations,
                    fingerprint,
                )
                if not commit:
                    raise DryRunRollback()
                return summary, "committed"
    except DryRunRollback:
        return summary, "dry-run rolled back"


def print_summary(
    summary: FinalizationSummary, mode: str
) -> None:
    print("BirdBrain transactional Round Finalizer")
    print("=======================================")
    print(f"Round:                 R{summary.round_no}")
    print(f"Mode:                  {mode}")
    print(f"Fingerprint:           {summary.fingerprint}")
    print(f"Eligible field:        {summary.field_size}")
    print(f"Purse:                 ${summary.purse}")
    print(f"Round results:         {summary.result_count}")
    print(
        "Financial rows:        "
        f"{summary.financial_transaction_count}"
    )
    print(
        "Handicap adjustments:  "
        f"{summary.handicap_adjustment_count}"
    )
    print(f"Ace awards:            {summary.ace_award_count}")
    print(
        "Course record rows:    "
        f"{summary.course_record_count}"
    )
    print(f"Final state:           {summary.status}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Transactional BirdBrain round finalizer. Defaults to a full "
            "SQL dry-run that is forcibly rolled back."
        )
    )
    parser.add_argument("--round-no", type=int, required=True)
    parser.add_argument("--season-year", type=int)
    parser.add_argument(
        "--ace-award",
        action="append",
        default=[],
        help=(
            "For multiple aces: ROUND_PARTICIPANT_ID:HOLE=AMOUNT. "
            "Repeat per ace."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Persist the finalization. Without this flag the full write "
            "path rolls back."
        ),
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required with --commit: FINALIZE-R<round-no>",
    )
    args = parser.parse_args()

    if (
        args.commit
        and args.confirm != f"FINALIZE-R{args.round_no}"
    ):
        raise SystemExit(
            f"--commit requires --confirm FINALIZE-R{args.round_no}. "
            "No database changes were attempted."
        )

    allocations = parse_ace_allocations(args.ace_award)
    conn = connect_admin()
    try:
        summary, mode = finalize(
            conn,
            args.round_no,
            args.season_year,
            allocations,
            commit=args.commit,
        )
        if summary is None:
            raise RuntimeError("Finalizer produced no summary.")
        print_summary(summary, mode)
        if mode == "dry-run rolled back":
            print()
            print(
                "PASS: full transaction write path executed and was rolled back."
            )
            print(
                "No finalization rows or round status changes were committed."
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
