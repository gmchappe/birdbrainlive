"""Deterministic BirdBrain round-finalization domain logic."""

from .core import (
    FinalizerValidationError,
    ParticipantInput,
    ParticipantResult,
    RoundPlan,
    financial_contributions,
    half_up,
    payout_schedule,
    plan_round,
    precise_handicap,
    applied_handicap_for_next_round,
    validate_ace_awards,
)

__all__ = [
    "FinalizerValidationError",
    "ParticipantInput",
    "ParticipantResult",
    "RoundPlan",
    "financial_contributions",
    "half_up",
    "payout_schedule",
    "plan_round",
    "precise_handicap",
    "applied_handicap_for_next_round",
    "validate_ace_awards",
]
