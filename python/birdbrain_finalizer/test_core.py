from __future__ import annotations

import unittest
from decimal import Decimal

from core import (
    FinalizerValidationError,
    ParticipantInput,
    applied_handicap_for_next_round,
    financial_contributions,
    payout_schedule,
    plan_round,
    precise_handicap,
    validate_ace_awards,
)


class PayoutTests(unittest.TestCase):
    def test_round1_double_purse(self) -> None:
        self.assertEqual(payout_schedule(59, 5), [118, 74, 44, 35, 24])

    def test_round2_standard_purse(self) -> None:
        self.assertEqual(payout_schedule(35, 4), [56, 35, 21, 17, 11])

    def test_missing_paid_finisher_rolls_to_first(self) -> None:
        self.assertEqual(payout_schedule(10, 4, cash_eligible_finishers=1), [40])


class ContributionTests(unittest.TestCase):
    def test_round1_postseason_total(self) -> None:
        participants = [
            ParticipantInput(i, f"P{i}", prior_season_eligible_rounds=0)
            for i in range(1, 60)
        ]
        totals = financial_contributions(
            participants,
            payout_contribution=5,
            ace_contribution=1,
            postseason_contribution=2,
        )
        self.assertEqual(totals["eligible_field"], 59)
        self.assertEqual(totals["round_payout"], 295)
        self.assertEqual(totals["ace_pot"], 59)
        self.assertEqual(totals["postseason"], 413)

    def test_round2_increment(self) -> None:
        participants = [
            ParticipantInput(i, f"Returning{i}", prior_season_eligible_rounds=1)
            for i in range(1, 21)
        ] + [
            ParticipantInput(i, f"New{i}", prior_season_eligible_rounds=0)
            for i in range(21, 36)
        ]
        totals = financial_contributions(
            participants,
            payout_contribution=4,
            ace_contribution=1,
            postseason_contribution=1,
        )
        self.assertEqual(totals["round_payout"], 140)
        self.assertEqual(totals["ace_pot"], 35)
        self.assertEqual(totals["first_season_contributors"], 15)
        self.assertEqual(totals["postseason"], 110)
        self.assertEqual(413 + totals["postseason"], 523)

    def test_guest_excluded_and_dnf_retained(self) -> None:
        participants = [
            ParticipantInput(1, "Active", status="active", gross_score=50),
            ParticipantInput(2, "DNF", status="dnf", gross_score=None),
            ParticipantInput(3, "Guest", participant_type="guest", gross_score=45),
            ParticipantInput(4, "Never Started", started_round=False, gross_score=None),
        ]
        totals = financial_contributions(
            participants,
            payout_contribution=4,
            ace_contribution=1,
            postseason_contribution=1,
        )
        self.assertEqual(totals["eligible_field"], 2)
        self.assertEqual(totals["round_payout"], 8)
        self.assertEqual(totals["ace_pot"], 2)


class RankingTests(unittest.TestCase):
    def test_noncash_tie_uses_competition_rank(self) -> None:
        participants = [
            ParticipantInput(1, "A", gross_score=50),
            ParticipantInput(2, "B", gross_score=51),
            ParticipantInput(3, "C", gross_score=60),
            ParticipantInput(4, "D", gross_score=60),
            ParticipantInput(5, "E", gross_score=65),
            ParticipantInput(6, "F", gross_score=70),
        ]
        plan = plan_round(participants, payout_contribution=4)
        result = {row.name: row for row in plan.results}
        self.assertEqual(plan.field_size, 6)
        self.assertEqual(plan.payouts, (14, 10))
        self.assertEqual(result["C"].competition_rank, 3)
        self.assertEqual(result["D"].competition_rank, 3)
        self.assertEqual(result["C"].points, 4)
        self.assertEqual(result["D"].points, 4)

    def test_cash_tie_requires_resolution(self) -> None:
        participants = [
            ParticipantInput(1, "A", gross_score=50),
            ParticipantInput(2, "B", gross_score=51),
            ParticipantInput(3, "C", gross_score=51),
            ParticipantInput(4, "D", gross_score=60),
            ParticipantInput(5, "E", gross_score=65),
            ParticipantInput(6, "F", gross_score=70),
        ]
        with self.assertRaises(FinalizerValidationError):
            plan_round(participants, payout_contribution=4)

    def test_cash_tie_resolution_drives_points_and_payout(self) -> None:
        participants = [
            ParticipantInput(1, "A", gross_score=50),
            ParticipantInput(2, "B", gross_score=51, playoff_finish=2),
            ParticipantInput(3, "C", gross_score=51, playoff_finish=3),
            ParticipantInput(4, "D", gross_score=60),
            ParticipantInput(5, "E", gross_score=65),
            ParticipantInput(6, "F", gross_score=70),
        ]
        plan = plan_round(participants, payout_contribution=4)
        result = {row.name: row for row in plan.results}
        self.assertEqual(result["A"].payout_award, 14)
        self.assertEqual(result["B"].payout_award, 10)
        self.assertEqual(result["C"].payout_award, 0)
        self.assertEqual(result["A"].points, 6)
        self.assertEqual(result["B"].points, 5)
        self.assertEqual(result["C"].points, 4)

    def test_dnf_counts_in_field_but_scores_zero(self) -> None:
        participants = [
            ParticipantInput(1, "A", gross_score=50),
            ParticipantInput(2, "B", gross_score=55),
            ParticipantInput(3, "DNF", status="dnf"),
        ]
        plan = plan_round(participants, payout_contribution=4)
        result = {row.name: row for row in plan.results}
        self.assertEqual(plan.field_size, 3)
        self.assertEqual(plan.purse, 12)
        self.assertEqual(result["A"].points, 3)
        self.assertEqual(result["B"].points, 2)
        self.assertEqual(result["DNF"].points, 0)
        self.assertEqual(result["DNF"].payout_award, 0)


class HandicapTests(unittest.TestCase):
    def test_trim_floor_one_fifth_each_side(self) -> None:
        self.assertEqual(
            precise_handicap([-10, -5, 0, 5, 10]),
            Decimal("0"),
        )

    def test_bounds_apply_only_through_five_completed_rounds(self) -> None:
        self.assertEqual(applied_handicap_for_next_round(Decimal("9.2"), 5), 8)
        self.assertEqual(applied_handicap_for_next_round(Decimal("-6.1"), 5), -5)
        self.assertEqual(applied_handicap_for_next_round(Decimal("9.2"), 6), 9)


class AceTests(unittest.TestCase):
    def test_multiple_awards_must_sum_exactly(self) -> None:
        validate_ace_awards(59, {10: 30, 20: 29})
        with self.assertRaises(FinalizerValidationError):
            validate_ace_awards(59, {10: 30, 20: 28})


if __name__ == "__main__":
    unittest.main()
