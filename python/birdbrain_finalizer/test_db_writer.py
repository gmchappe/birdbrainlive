from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from core import ParticipantInput
from db_writer import (
    LoadedParticipant,
    RoundContext,
    ShamPoolStat,
    compute_sham_models,
    input_fingerprint,
    parse_ace_allocations,
)


class DbWriterUnitTests(unittest.TestCase):
    def _context(self, status: str) -> RoundContext:
        return RoundContext(
            round_id=41,
            season_id=1,
            league_id=1,
            layout_id=9,
            round_no=41,
            status=status,
            season_name="2026",
            season_year=2026,
            course="Test Course",
            layout="Test Layout",
            scheduled_date="2026-09-03",
            layout_par=54,
            hole_count=18,
            points_multiplier=1,
            payout_contribution=4,
            postseason_contribution=1,
            ace_contribution=1,
            ace_pot_start=25,
        )

    def _participant(self) -> LoadedParticipant:
        return LoadedParticipant(
            input=ParticipantInput(
                round_participant_id=101,
                name="Player One",
                participant_type="member",
                status="active",
                started_round=True,
                gross_score=54,
                applied_handicap=Decimal("2"),
                playoff_finish=None,
                prior_season_eligible_rounds=3,
            ),
            player_id=1001,
            score_to_par=0,
            holes_scored=18,
            ace_holes=(),
            prior_current_season_rounds=3,
        )

    def test_fingerprint_ignores_lifecycle_status(self) -> None:
        participant = self._participant()
        review = input_fingerprint(
            self._context("results_review"), [participant], {}
        )
        finalized = input_fingerprint(
            self._context("finalized"), [participant], {}
        )
        self.assertEqual(review, finalized)

    def test_fingerprint_changes_when_score_changes(self) -> None:
        participant = self._participant()
        changed = replace(
            participant,
            input=replace(participant.input, gross_score=55),
            score_to_par=1,
        )
        base = input_fingerprint(
            self._context("results_review"), [participant], {}
        )
        other = input_fingerprint(
            self._context("results_review"), [changed], {}
        )
        self.assertNotEqual(base, other)

    def test_parse_ace_allocations(self) -> None:
        self.assertEqual(
            parse_ace_allocations(["101:2=31", "102:7=30"]),
            {(101, 2): 31, (102, 7): 30},
        )

    def test_simple_sham_model_matches_linear_pattern(self) -> None:
        stats = [
            ShamPoolStat("A", "L", 54, "A", 10, 540, 540),
            ShamPoolStat("A", "L", 54, "B", 10, 550, 540),
            ShamPoolStat("A", "L", 54, "C", 10, 560, 540),
        ]
        model = compute_sham_models(stats)[("A", "L", 54)]
        self.assertEqual(model.slope, Decimal("1"))
        self.assertEqual(model.rating, Decimal("0"))
        self.assertEqual(model.standardized_slope, Decimal("0"))
        self.assertEqual(model.weight, Decimal("1"))
        self.assertEqual(model.grand_mean, Decimal("1"))

    def test_sham_rating_is_centered_on_grand_mean(self) -> None:
        stats = [
            ShamPoolStat("A", "L1", 54, "A", 10, 540, 540),
            ShamPoolStat("A", "L1", 54, "B", 10, 550, 540),
            ShamPoolStat("A", "L2", 54, "A", 10, 560, 540),
            ShamPoolStat("A", "L2", 54, "B", 10, 580, 540),
        ]
        models = compute_sham_models(stats)
        l1 = models[("A", "L1", 54)]
        l2 = models[("A", "L2", 54)]
        self.assertLess(l1.rating, Decimal("0"))
        self.assertGreater(l2.rating, Decimal("0"))
        self.assertEqual(l1.grand_mean, l2.grand_mean)


if __name__ == "__main__":
    unittest.main()
