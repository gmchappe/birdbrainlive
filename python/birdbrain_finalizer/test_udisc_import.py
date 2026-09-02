from __future__ import annotations

import unittest
from pathlib import Path

from udisc import UDiscRound, parse_udisc_xlsx
from udisc_import import ImportTarget, data_fingerprint, source_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]
ROUND2_FIXTURE = REPO_ROOT / "fixtures/round_02/udisc_round_02_black_bear.xlsx"


def target(round_id: int = 42) -> ImportTarget:
    return ImportTarget(
        round_id=round_id,
        season_id=1,
        league_id=1,
        round_no=2,
        season_year=2026,
        status="scheduled",
        layout_id=7,
        course="Black Bear",
        layout="Default",
        layout_par=54,
        hole_count=18,
    )


class UDiscImportFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_order_independent_for_participant_rows(self) -> None:
        parsed = parse_udisc_xlsx(ROUND2_FIXTURE)
        reversed_round = UDiscRound(
            source_path=parsed.source_path,
            participants=tuple(reversed(parsed.participants)),
            skipped_duplicates=parsed.skipped_duplicates,
            skipped_non_gen=parsed.skipped_non_gen,
        )
        self.assertEqual(
            data_fingerprint(target(), parsed),
            data_fingerprint(target(), reversed_round),
        )

    def test_fingerprint_is_bound_to_target_round(self) -> None:
        parsed = parse_udisc_xlsx(ROUND2_FIXTURE)
        self.assertNotEqual(
            data_fingerprint(target(round_id=42), parsed),
            data_fingerprint(target(round_id=43), parsed),
        )

    def test_source_hash_is_sha256(self) -> None:
        digest = source_sha256(ROUND2_FIXTURE)
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))


if __name__ == "__main__":
    unittest.main()
