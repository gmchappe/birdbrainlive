from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Mapping


class FinalizerValidationError(ValueError):
    """Raised when a round cannot be finalized without administrative resolution."""


def _d(value: int | float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def half_up(value: int | float | str | Decimal) -> int:
    """BirdBrain half-up rounding, symmetric for positive/negative values."""
    number = _d(value)
    sign = -1 if number < 0 else 1
    magnitude = abs(number)
    return sign * int((magnitude + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def _payout_fractions(field_size: int) -> tuple[Decimal, ...]:
    if field_size <= 0:
        return tuple()
    if field_size <= 5:
        return (Decimal("1.00"),)
    if field_size <= 10:
        return (Decimal("0.60"), Decimal("0.40"))
    if field_size <= 15:
        return (Decimal("0.50"), Decimal("0.30"), Decimal("0.20"))
    if field_size <= 20:
        return (
            Decimal("0.40"),
            Decimal("0.30"),
            Decimal("0.20"),
            Decimal("0.10"),
        )
    return (
        Decimal("0.40"),
        Decimal("0.25"),
        Decimal("0.15"),
        Decimal("0.12"),
        Decimal("0.08"),
    )


def payout_schedule(
    field_size: int,
    contribution_per_player: int,
    cash_eligible_finishers: int | None = None,
) -> list[int]:
    """Return whole-dollar awards in cash-position order.

    Lower places are rounded half-up; first place receives the remainder so the
    purse always reconciles exactly. If fewer eligible finishers exist than the
    normal number of paid positions, unavailable shares roll into first place.
    """
    if field_size < 0:
        raise FinalizerValidationError("Field size cannot be negative.")
    if contribution_per_player < 0:
        raise FinalizerValidationError("Payout contribution cannot be negative.")

    fractions = _payout_fractions(field_size)
    if not fractions:
        return []

    purse = field_size * contribution_per_player
    lower = [half_up(Decimal(purse) * fraction) for fraction in fractions[1:]]
    awards = [purse - sum(lower), *lower]

    if cash_eligible_finishers is None:
        return awards
    if cash_eligible_finishers < 0:
        raise FinalizerValidationError("Cash-eligible finisher count cannot be negative.")
    if cash_eligible_finishers == 0:
        if purse == 0:
            return []
        raise FinalizerValidationError(
            "A positive purse cannot be finalized with zero cash-eligible finishers."
        )
    if cash_eligible_finishers >= len(awards):
        return awards

    missing = sum(awards[cash_eligible_finishers:])
    kept = awards[:cash_eligible_finishers]
    kept[0] += missing
    return kept


@dataclass(frozen=True)
class ParticipantInput:
    round_participant_id: int
    name: str
    participant_type: str = "member"
    status: str = "active"
    started_round: bool = True
    gross_score: int | None = None
    applied_handicap: Decimal | int | float | str = Decimal("0")
    playoff_finish: int | None = None
    prior_season_eligible_rounds: int = 0

    @property
    def is_field_eligible(self) -> bool:
        return (
            self.participant_type == "member"
            and self.started_round
            and self.status in {"active", "dnf"}
        )

    @property
    def is_scoring_finisher(self) -> bool:
        return self.is_field_eligible and self.status == "active" and self.gross_score is not None


@dataclass(frozen=True)
class ParticipantResult:
    round_participant_id: int
    name: str
    gross_score: int | None
    applied_handicap: Decimal
    net_score: Decimal | None
    competition_rank: int | None
    official_finish: int | None
    cash_position: int | None
    points: int
    payout_award: int


@dataclass(frozen=True)
class RoundPlan:
    field_size: int
    purse: int
    payouts: tuple[int, ...]
    results: tuple[ParticipantResult, ...]


def _competition_ranks(
    finishers: list[ParticipantInput],
) -> tuple[dict[int, int], dict[int, Decimal]]:
    ordered = sorted(
        finishers,
        key=lambda p: (_d(p.gross_score) - _d(p.applied_handicap), p.name.casefold()),
    )
    ranks: dict[int, int] = {}
    net_scores: dict[int, Decimal] = {}
    previous_net: Decimal | None = None
    current_rank = 0
    for index, participant in enumerate(ordered, start=1):
        net = _d(participant.gross_score) - _d(participant.applied_handicap)
        if previous_net is None or net != previous_net:
            current_rank = index
            previous_net = net
        ranks[participant.round_participant_id] = current_rank
        net_scores[participant.round_participant_id] = net
    return ranks, net_scores


def _resolve_cash_ties(
    finishers: list[ParticipantInput],
    competition_ranks: Mapping[int, int],
    net_scores: Mapping[int, Decimal],
    paid_positions: int,
) -> dict[int, int]:
    official = dict(competition_ranks)
    groups: dict[Decimal, list[ParticipantInput]] = {}
    for participant in finishers:
        groups.setdefault(net_scores[participant.round_participant_id], []).append(participant)

    for tied in groups.values():
        if len(tied) == 1:
            continue
        rank = competition_ranks[tied[0].round_participant_id]
        occupied = set(range(rank, rank + len(tied)))
        if not any(position <= paid_positions for position in occupied):
            # Non-cash ties retain competition rank and equal points.
            continue

        resolutions = [p.playoff_finish for p in tied]
        if any(value is None for value in resolutions):
            names = ", ".join(sorted(p.name for p in tied))
            raise FinalizerValidationError(
                f"Cash-position tie requires playoff resolution: {names}."
            )
        resolved = {int(value) for value in resolutions if value is not None}
        if resolved != occupied:
            names = ", ".join(sorted(p.name for p in tied))
            raise FinalizerValidationError(
                f"Playoff finishes for {names} must resolve exactly to positions "
                f"{sorted(occupied)}; got {sorted(resolved)}."
            )
        for participant in tied:
            official[participant.round_participant_id] = int(participant.playoff_finish)

    return official


def plan_round(
    participants: Iterable[ParticipantInput],
    *,
    payout_contribution: int,
    points_multiplier: int = 1,
) -> RoundPlan:
    """Build deterministic results without touching the database."""
    participant_list = list(participants)
    if points_multiplier <= 0:
        raise FinalizerValidationError("Points multiplier must be positive.")

    field = [p for p in participant_list if p.is_field_eligible]
    finishers = [p for p in field if p.is_scoring_finisher]
    field_size = len(field)

    normal_paid_positions = len(_payout_fractions(field_size))
    payouts = payout_schedule(field_size, payout_contribution, len(finishers))
    competition_ranks, net_scores = _competition_ranks(finishers)
    official_finishes = _resolve_cash_ties(
        finishers,
        competition_ranks,
        net_scores,
        normal_paid_positions,
    )

    payout_by_position = {position: amount for position, amount in enumerate(payouts, start=1)}
    results: list[ParticipantResult] = []

    for participant in participant_list:
        handicap = _d(participant.applied_handicap)
        if participant.is_scoring_finisher:
            participant_id = participant.round_participant_id
            competition_rank = competition_ranks[participant_id]
            official_finish = official_finishes[participant_id]
            cash_position = official_finish if official_finish in payout_by_position else None
            points = (field_size - official_finish + 1) * points_multiplier
            payout_award = payout_by_position.get(official_finish, 0)
            net_score = net_scores[participant_id]
        else:
            competition_rank = None
            official_finish = None
            cash_position = None
            points = 0
            payout_award = 0
            net_score = None

        results.append(
            ParticipantResult(
                round_participant_id=participant.round_participant_id,
                name=participant.name,
                gross_score=participant.gross_score,
                applied_handicap=handicap,
                net_score=net_score,
                competition_rank=competition_rank,
                official_finish=official_finish,
                cash_position=cash_position,
                points=points,
                payout_award=payout_award,
            )
        )

    results.sort(
        key=lambda r: (
            r.official_finish is None,
            r.official_finish if r.official_finish is not None else 10**9,
            r.name.casefold(),
        )
    )
    return RoundPlan(
        field_size=field_size,
        purse=field_size * payout_contribution,
        payouts=tuple(payouts),
        results=tuple(results),
    )


def financial_contributions(
    participants: Iterable[ParticipantInput],
    *,
    payout_contribution: int,
    ace_contribution: int,
    postseason_contribution: int,
    first_season_contribution: int = 5,
) -> dict[str, int]:
    """Return round contribution totals before awards are posted."""
    for value, label in (
        (payout_contribution, "payout"),
        (ace_contribution, "ace"),
        (postseason_contribution, "postseason"),
        (first_season_contribution, "first-season postseason"),
    ):
        if value < 0:
            raise FinalizerValidationError(f"{label} contribution cannot be negative.")

    field = [p for p in participants if p.is_field_eligible]
    first_timers = [p for p in field if p.prior_season_eligible_rounds == 0]
    return {
        "eligible_field": len(field),
        "first_season_contributors": len(first_timers),
        "round_payout": len(field) * payout_contribution,
        "ace_pot": len(field) * ace_contribution,
        "postseason": (
            len(field) * postseason_contribution
            + len(first_timers) * first_season_contribution
        ),
    }


def precise_handicap(adjustments: Iterable[int | float | str | Decimal]) -> Decimal:
    values = sorted(_d(value) for value in adjustments)
    if not values:
        raise FinalizerValidationError("Cannot calculate a handicap without adjustments.")
    cut = len(values) // 5
    kept = values[cut : len(values) - cut] if cut > 0 else values
    return sum(kept, Decimal("0")) / Decimal(len(kept))


def applied_handicap_for_next_round(
    precise: int | float | str | Decimal,
    completed_rounds_after_finalization: int,
) -> int:
    if completed_rounds_after_finalization <= 0:
        raise FinalizerValidationError("Completed-round count must be positive.")
    applied = half_up(precise)
    if completed_rounds_after_finalization <= 5:
        return min(8, max(-5, applied))
    return applied


def validate_ace_awards(pot: int, awards: Mapping[int, int]) -> None:
    """Validate future native ace awards: whole dollars summing exactly to the pot."""
    if pot < 0:
        raise FinalizerValidationError("Ace pot cannot be negative.")
    if not awards:
        if pot != 0:
            raise FinalizerValidationError(
                "An ace pot can clear only when explicit ace awards are supplied."
            )
        return
    if any(amount < 0 or int(amount) != amount for amount in awards.values()):
        raise FinalizerValidationError("Ace awards must be non-negative whole dollars.")
    if sum(awards.values()) != pot:
        raise FinalizerValidationError(
            f"Ace awards must sum to the full pot (${pot}); got ${sum(awards.values())}."
        )
