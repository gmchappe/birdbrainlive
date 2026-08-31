from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()

import cli as migration_cli  # noqa: E402
import bootstrap_normalized as bootstrap_module  # noqa: E402
from analyze_staging import nonblank  # noqa: E402


def mark_unplayed_missing_par(rows: dict, report: dict) -> list[dict]:
    """Mark blank Par for any unplayed schedule row during migration.

    Played rounds remain strict because historical score-to-par depends on a real
    aggregate par. Unplayed rounds, including future scheduled rounds, may still
    have incomplete layout setup and can be represented with layouts.par = NULL
    until that round is configured.
    """
    mapping_by_round = {
        int(mapping["round_no"]): mapping for mapping in report["round_mappings"]
    }
    marked: list[dict] = []

    for row in rows.get("League Schedule", []):
        round_no = bootstrap_module.decimal_int(row.get("RoundNo"))
        if round_no is None or nonblank(row.get("Par")):
            continue

        mapping = mapping_by_round.get(round_no)
        if mapping is None or mapping["has_season_score_column"]:
            # Missing Par on a played round is still a hard error. Leave the row
            # untouched so bootstrap_normalized fails rather than guessing.
            continue

        # Reuse the existing CLI migration sentinel and compatibility context.
        row["Par"] = migration_cli.MISSING_PAST_UNPLAYED_PAR
        marked.append(
            {
                "round_no": round_no,
                "course": nonblank(row.get("Course")),
                "layout": nonblank(row.get("Layout")),
            }
        )

    return marked


# cmd_bootstrap resolves this global function at runtime. Replacing it here lets
# the one-shot migration use the corrected policy without changing staged data or
# weakening validation for rounds that actually have score history.
migration_cli.mark_past_unplayed_missing_par = mark_unplayed_missing_par


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "BirdBrain normalized bootstrap with NULL-par support for any unplayed "
            "schedule round. Dry-run is the default."
        )
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--league-name", default=migration_cli.DEFAULT_LEAGUE_NAME)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    migration_cli.cmd_bootstrap(args)


if __name__ == "__main__":
    main()
