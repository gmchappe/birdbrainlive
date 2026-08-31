from __future__ import annotations

import bootstrap_unplayed_par as base_bootstrap
import bootstrap_normalized as bootstrap_module
import cli as migration_cli
from analyze_staging import nonblank


class HoleLabel(str):
    """Migration-only marker for UDisc hole labels such as 9G or 12A."""


_original_decimal_int = bootstrap_module.decimal_int


def decimal_int_with_hole_labels(value):
    """Preserve marked ace-hole labels while keeping all other numeric parsing strict."""
    if isinstance(value, HoleLabel):
        return str(value)
    return _original_decimal_int(value)


bootstrap_module.decimal_int = decimal_int_with_hole_labels

_original_normalize_rows = migration_cli.normalize_rows_for_bootstrap


def normalize_rows_with_hole_labels(report: dict, rows: dict) -> dict:
    normalized = _original_normalize_rows(report, rows)
    for row in normalized.get("Aces", []):
        hole = nonblank(row.get("Hole"))
        if hole:
            row["Hole"] = HoleLabel(hole)
    return normalized


migration_cli.normalize_rows_for_bootstrap = normalize_rows_with_hole_labels


def main() -> None:
    args = base_bootstrap.build_parser().parse_args()
    migration_cli.cmd_bootstrap(args)


if __name__ == "__main__":
    main()
