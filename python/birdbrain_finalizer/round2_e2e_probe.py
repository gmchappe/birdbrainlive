from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

from core import applied_handicap_for_next_round
from db_preflight import connect_admin
from db_writer import (
    apply_finalization,
    input_fingerprint,
    load_participants,
    load_round_context,
)
from udisc import normalized_name, parse_udisc_xlsx
from udisc_stage_probe import stage_udisc


REPO_ROOT = Path(__file__).resolve().parents[2]
ROUND1_FIXTURE = REPO_ROOT / "fixtures/round_01/udisc_round_01_shady_oaks.xlsx"
ROUND2_FIXTURE = REPO_ROOT / "fixtures/round_02/udisc_round_02_black_bear.xlsx"
PROBE_SEASON_YEAR = 2099


class ProbeRollback(RuntimeError):
    pass


def current_league_and_templates(cur):
    cur.execute(
        """
        SELECT s.league_id, s.season_id
        FROM seasons s
        WHERE s.season_year = 2026
          AND s.closed_at IS NULL
        ORDER BY s.season_id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Could not find the open 2026 season.")
    league_id, source_season_id = map(int, row)

    templates = {}
    for round_no in (1, 2):
        cur.execute(
            """
            SELECT
              r.layout_id,
              r.scheduled_date,
              r.points_multiplier,
              r.payout_contribution,
              r.postseason_contribution,
              r.ace_contribution
            FROM rounds r
            WHERE r.season_id = %s
              AND r.round_no = %s
            """,
            (source_season_id, round_no),
        )
        template = cur.fetchone()
        if template is None:
            raise RuntimeError(f"Could not find source template R{round_no}.")
        templates[round_no] = template
    return league_id, templates


def create_probe_season(cur, league_id: int, probe_name: str) -> int:
    cur.execute(
        """
        INSERT INTO seasons (league_id, season_name, season_year)
        VALUES (%s, %s, %s)
        RETURNING season_id
        """,
        (league_id, probe_name, PROBE_SEASON_YEAR),
    )
    return int(cur.fetchone()[0])


def create_round(cur, season_id: int, round_no: int, template, status: str) -> int:
    (
        layout_id,
        scheduled_date,
        points_multiplier,
        payout_contribution,
        postseason_contribution,
        ace_contribution,
    ) = template
    cur.execute(
        """
        INSERT INTO rounds (
          season_id, round_no, layout_id, scheduled_date,
          points_multiplier, payout_contribution,
          postseason_contribution, ace_contribution,
          status, note
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'Round 2 end-to-end rollback probe')
        RETURNING round_id
        """,
        (
            season_id,
            round_no,
            layout_id,
            scheduled_date,
            points_multiplier,
            payout_contribution,
            postseason_contribution,
            ace_contribution,
            status,
        ),
    )
    return int(cur.fetchone()[0])


def resolve_players(cur, names: set[str]) -> dict[str, tuple[int, str]]:
    cur.execute("SELECT player_id, display_name, normalized_name FROM players")
    lookup = {
        row[2]: (int(row[0]), row[1])
        for row in cur.fetchall()
    }
    missing = [name for name in sorted(names) if normalized_name(name) not in lookup]
    if missing:
        raise RuntimeError(
            "Fixture identities missing from normalized players: " + ", ".join(missing)
        )
    return {normalized_name(name): lookup[normalized_name(name)] for name in names}


def seed_round1_baseline(cur, season_id: int, round1_id: int, round1, players) -> None:
    # Round 1 finalized under pre-SHAM rules. Its score-to-par adjustment is the
    # precise handicap basis for Round 2; the first-round applied handicap was 0.
    cur.execute("SELECT par FROM layouts l JOIN rounds r ON r.layout_id=l.layout_id WHERE r.round_id=%s", (round1_id,))
    par = cur.fetchone()[0]
    if par is None:
        raise RuntimeError("Round 1 template layout has no par.")
    par = int(par)

    for person in round1.participants:
        player_id, _ = players[person.normalized_name]
        cur.execute(
            """
            INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
            VALUES (%s,%s,TRUE)
            ON CONFLICT (season_id, player_id) DO NOTHING
            """,
            (season_id, player_id),
        )
        cur.execute(
            """
            INSERT INTO season_player_summaries (
              season_id, player_id, rounds, points, through_round_no, source
            ) VALUES (%s,%s,1,0,1,'round2_e2e_probe_round1')
            """,
            (season_id, player_id),
        )

        if person.status != "active" or person.gross_score is None:
            continue
        adjustment = Decimal(int(person.gross_score) - par)
        cur.execute(
            """
            INSERT INTO handicap_adjustments (player_id, round_id, adjustment, method)
            VALUES (%s,%s,%s,'pre_sham_par')
            RETURNING handicap_adjustment_id
            """,
            (player_id, round1_id, adjustment),
        )
        adjustment_id = int(cur.fetchone()[0])
        applied = applied_handicap_for_next_round(adjustment, 1)
        cur.execute(
            """
            INSERT INTO handicap_calculations (
              player_id, effective_after_round_id, precise_handicap,
              applied_handicap, trim_fraction
            ) VALUES (%s,%s,%s,%s,0.20)
            RETURNING handicap_calculation_id
            """,
            (player_id, round1_id, adjustment, applied),
        )
        calculation_id = int(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO handicap_calculation_adjustments (
              handicap_calculation_id, handicap_adjustment_id, included, trim_side
            ) VALUES (%s,%s,TRUE,NULL)
            """,
            (calculation_id, adjustment_id),
        )


def add_round2_only_memberships(cur, season_id: int, round2, players) -> None:
    for person in round2.participants:
        player_id, _ = players[person.normalized_name]
        cur.execute(
            """
            INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
            VALUES (%s,%s,TRUE)
            ON CONFLICT (season_id, player_id) DO NOTHING
            """,
            (season_id, player_id),
        )


def seed_postseason_opening_balance(cur, league_id: int, season_id: int, round1_id: int) -> None:
    # Round 1 fixture closes at $413 postseason. Recreate that cumulative state
    # without replaying Round 1 finance rows; Round 2 should add $110 to reach $523.
    cur.execute(
        """
        INSERT INTO financial_transactions (
          league_id, season_id, round_id, fund_type,
          transaction_type, amount, memo
        ) VALUES (%s,%s,%s,'postseason','adjustment',413,
                  'Round 2 e2e probe opening postseason balance after Round 1')
        """,
        (league_id, season_id, round1_id),
    )


def insert_round2_playoff_resolutions(cur, round2_id: int) -> None:
    expected = {
        normalized_name("Corinno Torres"): 3,
        normalized_name("Drake Zollers"): 4,
    }
    cur.execute(
        """
        SELECT rp.round_participant_id, p.normalized_name
        FROM round_participants rp
        JOIN players p ON p.player_id = rp.player_id
        WHERE rp.round_id = %s
        """,
        (round2_id,),
    )
    ids = {row[1]: int(row[0]) for row in cur.fetchall()}
    for key, finish in expected.items():
        if key not in ids:
            raise RuntimeError(f"Expected playoff participant {key!r} was not staged.")
        cur.execute(
            """
            INSERT INTO playoff_resolutions (
              round_id, round_participant_id, resolved_finish
            ) VALUES (%s,%s,%s)
            """,
            (round2_id, ids[key], finish),
        )


def verify_round2(cur, season_id: int, round2_id: int, summary) -> None:
    expected_payouts = [
        ("Elmo Jones", 1, 56),
        ("J. Carlos Marin", 2, 35),
        ("Corinno Torres", 3, 21),
        ("Drake Zollers", 4, 17),
        ("Jamey Papanek", 5, 11),
    ]
    if summary.field_size != 35 or summary.purse != 140:
        raise RuntimeError(
            f"Round 2 field/purse mismatch: field={summary.field_size}, purse={summary.purse}."
        )
    if summary.result_count != 35:
        raise RuntimeError(f"Expected 35 result rows, got {summary.result_count}.")
    if summary.financial_transaction_count != 125:
        raise RuntimeError(
            f"Expected 125 Round 2 financial rows, got {summary.financial_transaction_count}."
        )
    if summary.handicap_adjustment_count != 35:
        raise RuntimeError(
            f"Expected 35 handicap adjustments, got {summary.handicap_adjustment_count}."
        )
    if summary.ace_award_count != 0:
        raise RuntimeError(f"Expected no Round 2 ace awards, got {summary.ace_award_count}.")
    if summary.course_record_count != 2:
        raise RuntimeError(
            f"Expected 2 Round 2 course-record rows, got {summary.course_record_count}."
        )

    cur.execute(
        """
        SELECT rp.display_name, rr.official_finish, rr.payout_award
        FROM round_results rr
        JOIN round_participants rp
          ON rp.round_participant_id = rr.round_participant_id
        WHERE rp.round_id = %s
          AND rr.payout_award > 0
        ORDER BY rr.official_finish
        """,
        (round2_id,),
    )
    paid = [(row[0], int(row[1]), int(row[2])) for row in cur.fetchall()]
    if paid != expected_payouts:
        raise RuntimeError(f"Round 2 paid order mismatch: {paid!r}")

    cur.execute(
        """
        SELECT COUNT(*)
        FROM round_participants rp
        JOIN round_results rr ON rr.round_participant_id = rp.round_participant_id
        WHERE rp.round_id = %s
        """,
        (round2_id,),
    )
    if int(cur.fetchone()[0]) != 35:
        raise RuntimeError("Round 2 result reconciliation failed.")

    cur.execute(
        """
        SELECT COALESCE(SUM(amount),0)::integer
        FROM financial_transactions
        WHERE season_id = %s AND fund_type = 'ace_pot'
        """,
        (season_id,),
    )
    ace_balance = int(cur.fetchone()[0])
    if ace_balance != 35:
        raise RuntimeError(f"Expected ace-pot balance $35, got ${ace_balance}.")

    cur.execute(
        """
        SELECT COALESCE(SUM(amount),0)::integer
        FROM financial_transactions
        WHERE season_id = %s AND fund_type = 'postseason'
        """,
        (season_id,),
    )
    postseason_balance = int(cur.fetchone()[0])
    if postseason_balance != 523:
        raise RuntimeError(
            f"Expected postseason balance $523, got ${postseason_balance}."
        )

    cur.execute(
        """
        SELECT p.display_name, cr.score
        FROM course_records cr
        JOIN players p ON p.player_id = cr.player_id
        WHERE cr.round_id = %s
        ORDER BY p.display_name
        """,
        (round2_id,),
    )
    records = [(row[0], int(row[1])) for row in cur.fetchall()]
    expected_records = [("Dan Schlitter", 45), ("Jamey Papanek", 45)]
    if records != expected_records:
        raise RuntimeError(f"Round 2 course-record mismatch: {records!r}")

    cur.execute(
        "SELECT status FROM rounds WHERE round_id = %s",
        (round2_id,),
    )
    if cur.fetchone()[0] != "finalized":
        raise RuntimeError("Round 2 did not reach finalized state inside probe.")

    cur.execute(
        "SELECT COUNT(*) FROM round_finalization_receipts WHERE round_id = %s",
        (round2_id,),
    )
    if int(cur.fetchone()[0]) != 1:
        raise RuntimeError("Round 2 finalization receipt was not written.")

    print(f"Paid order:        {', '.join(name for name, _, _ in paid)}")
    print("Payouts:           $56 / $35 / $21 / $17 / $11")
    print(f"Ace-pot balance:   ${ace_balance}")
    print(f"Postseason balance:${postseason_balance}")
    print("Course records:    Dan Schlitter 45; Jamey Papanek 45")


def main() -> None:
    round1 = parse_udisc_xlsx(ROUND1_FIXTURE)
    round2 = parse_udisc_xlsx(ROUND2_FIXTURE)
    all_names = {p.name for p in round1.participants} | {p.name for p in round2.participants}
    probe_name = f"__birdbrain_round2_e2e_probe_{uuid.uuid4().hex}"

    conn = connect_admin()
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    league_id, templates = current_league_and_templates(cur)
                    players = resolve_players(cur, all_names)
                    season_id = create_probe_season(cur, league_id, probe_name)
                    round1_id = create_round(cur, season_id, 1, templates[1], "finalized")
                    round2_id = create_round(cur, season_id, 2, templates[2], "in_progress")

                    seed_round1_baseline(cur, season_id, round1_id, round1, players)
                    add_round2_only_memberships(cur, season_id, round2, players)
                    seed_postseason_opening_balance(cur, league_id, season_id, round1_id)

                    _, layout_id, *_ = templates[2]
                    staged_participants, staged_scores = stage_udisc(
                        cur,
                        round_id=round2_id,
                        season_id=season_id,
                        layout_id=int(layout_id),
                        parsed=round2,
                    )
                    insert_round2_playoff_resolutions(cur, round2_id)

                    context = load_round_context(
                        cur, 2, PROBE_SEASON_YEAR, for_update=True
                    )
                    participants = load_participants(cur, context)
                    first_timers = sum(
                        1 for p in participants
                        if p.input.is_field_eligible and p.prior_current_season_rounds == 0
                    )
                    returning = sum(
                        1 for p in participants
                        if p.input.is_field_eligible and p.prior_current_season_rounds > 0
                    )
                    if first_timers != 15 or returning != 20:
                        raise RuntimeError(
                            f"Expected 20 returning + 15 first-time players; got "
                            f"{returning} returning + {first_timers} first-time."
                        )

                    fingerprint = input_fingerprint(context, participants, {})
                    summary = apply_finalization(
                        cur, context, participants, {}, fingerprint
                    )

                    print("BirdBrain Round 2 end-to-end rehearsal")
                    print("======================================")
                    print(f"Round 1 fixture players: {len(round1.participants)}")
                    print(f"Round 2 fixture players: {len(round2.participants)}")
                    print(f"Returning players:       {returning}")
                    print(f"First-time players:      {first_timers}")
                    print(f"Staged participants:     {staged_participants}")
                    print(f"Staged hole scores:      {staged_scores}")
                    print(f"Finalizer result rows:   {summary.result_count}")
                    print(f"Finalizer finance rows:  {summary.financial_transaction_count}")
                    print(f"Handicap adjustments:    {summary.handicap_adjustment_count}")
                    verify_round2(cur, season_id, round2_id, summary)
                    print("PASS full UDisc -> results_review -> finalizer path matches Round 2 fixture expectations")
                    print("Forcing rollback of the entire synthetic season...")
                    raise ProbeRollback()
        except ProbeRollback:
            pass

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM seasons WHERE season_name = %s",
                (probe_name,),
            )
            remaining = int(cur.fetchone()[0])
        conn.rollback()
        if remaining != 0:
            raise RuntimeError("ROLLBACK FAILURE: synthetic rehearsal season persisted.")

        print("PASS synthetic season, rounds, staging rows, and finalizer rows are absent")
        print("\nPASS: Round 2 end-to-end rehearsal left no persistent database changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
