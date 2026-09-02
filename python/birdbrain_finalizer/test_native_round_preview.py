from __future__ import annotations

import unittest

from core import FinalizerValidationError
from native_round_preview import parse_ace_specs, parse_playoff_specs


class NativeRoundPreviewArgumentTests(unittest.TestCase):
    def test_playoff_spec_parses_name_and_finish(self) -> None:
        specs = parse_playoff_specs(["Corinno Torres=3", "Drake Zollers=4"])
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].display_name, "Corinno Torres")
        self.assertEqual(specs[0].finish, 3)
        self.assertEqual(specs[1].finish, 4)

    def test_playoff_spec_rejects_nonpositive_finish(self) -> None:
        with self.assertRaises(FinalizerValidationError):
            parse_playoff_specs(["Player=0"])

    def test_ace_spec_parses_name_hole_amount(self) -> None:
        specs = parse_ace_specs(["David Garb:7=59"])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].display_name, "David Garb")
        self.assertEqual(specs[0].hole, 7)
        self.assertEqual(specs[0].amount, 59)

    def test_ace_spec_rejects_negative_amount(self) -> None:
        with self.assertRaises(FinalizerValidationError):
            parse_ace_specs(["Player:1=-1"])

    def test_invalid_forms_fail_closed(self) -> None:
        for raw in ("Player", "Player=abc", "=3"):
            with self.subTest(playoff=raw):
                with self.assertRaises(FinalizerValidationError):
                    parse_playoff_specs([raw])
        for raw in ("Player", "Player:abc=5", "Player:1=abc", ":1=5"):
            with self.subTest(ace=raw):
                with self.assertRaises(FinalizerValidationError):
                    parse_ace_specs([raw])


if __name__ == "__main__":
    unittest.main()
