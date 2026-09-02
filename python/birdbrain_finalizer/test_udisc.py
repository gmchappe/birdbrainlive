from __future__ import annotations

import unittest
from pathlib import Path

from udisc import parse_udisc_xlsx


REPO_ROOT = Path(__file__).resolve().parents[2]


class UDiscFixtureTests(unittest.TestCase):
    def test_round_01_fixture(self) -> None:
        parsed = parse_udisc_xlsx(
            REPO_ROOT / "fixtures/round_01/udisc_round_01_shady_oaks.xlsx"
        )
        self.assertEqual(len(parsed.participants), 59)
        self.assertEqual(parsed.hole_score_count, 1062)

        david = next(p for p in parsed.participants if p.name == "David Garb")
        self.assertEqual(david.ace_holes, (2,))

        best = min(
            p.gross_score for p in parsed.participants if p.gross_score is not None
        )
        self.assertEqual(best, 52)
        self.assertIn(
            "Simon Torres",
            [p.name for p in parsed.participants if p.gross_score == best],
        )

    def test_round_02_fixture(self) -> None:
        parsed = parse_udisc_xlsx(
            REPO_ROOT / "fixtures/round_02/udisc_round_02_black_bear.xlsx"
        )
        self.assertEqual(len(parsed.participants), 35)
        self.assertEqual(parsed.hole_score_count, 630)

        best = min(
            p.gross_score for p in parsed.participants if p.gross_score is not None
        )
        self.assertEqual(best, 45)
        record_holders = {
            p.name for p in parsed.participants if p.gross_score == best
        }
        self.assertEqual(record_holders, {"Jamey Papanek", "Dan Schlitter"})


if __name__ == "__main__":
    unittest.main()
