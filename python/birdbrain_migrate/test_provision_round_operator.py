from __future__ import annotations

import unittest

from provision_round_operator import (
    DENIED_MUTATION_TABLES,
    INSERT_TABLES,
    READ_TABLES,
    UPDATE_TABLES,
)


class RoundOperatorPermissionContractTests(unittest.TestCase):
    def test_every_write_table_is_readable_for_reconciliation(self) -> None:
        self.assertTrue(set(INSERT_TABLES).issubset(set(READ_TABLES)))
        self.assertTrue(set(UPDATE_TABLES).issubset(set(READ_TABLES)))

    def test_identity_and_config_tables_are_never_insert_targets(self) -> None:
        self.assertTrue(set(DENIED_MUTATION_TABLES).isdisjoint(set(INSERT_TABLES)))

    def test_rounds_is_update_only_not_insertable(self) -> None:
        self.assertIn("rounds", UPDATE_TABLES)
        self.assertNotIn("rounds", INSERT_TABLES)

    def test_receipts_and_audit_are_append_only(self) -> None:
        for table in (
            "round_udisc_import_receipts",
            "round_finalization_receipts",
            "audit_events",
        ):
            self.assertIn(table, INSERT_TABLES)
            self.assertNotIn(table, UPDATE_TABLES)

    def test_sham_model_is_only_append_update_exception(self) -> None:
        self.assertEqual(set(UPDATE_TABLES), {"rounds", "sham_layout_models"})


if __name__ == "__main__":
    unittest.main()
