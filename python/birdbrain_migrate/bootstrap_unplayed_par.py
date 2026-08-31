from __future__ import annotations

import argparse
from collections import defaultdict

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
# the one-shot migration allow NULL par for unplayed rounds without weakening
# validation for rounds that actually have score history.
migration_cli.mark_past_unplayed_missing_par = mark_unplayed_missing_par


_original_normalize_rows = migration_cli.normalize_rows_for_bootstrap


def normalize_rows_with_sham_semantic_check(report: dict, rows: dict) -> dict:
    """Validate the real legacy SHAM identifier semantics without rewriting them.

    bbsham.R defines RdNo as an all-time monotonically increasing SHAM observation
    sequence and RoundNo as the round number within that season. Neither is used
    as a PostgreSQL identity. We only verify that a source RdNo/Pool pair is not
    repeated, because bbsham.R writes at most one row per pool for each RdNo.
    """
    normalized = _original_normalize_rows(report, rows)
    sham_rows = normalized.get("Poolwise Strokes by Round", [])

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    sequences: set[int] = set()
    for row in sham_rows:
        rdno = nonblank(row.get("RdNo"))
        pool = nonblank(row.get("Pool")).upper()
        if not rdno or pool not in {"A", "B", "C", "D", "E"}:
            continue
        grouped[(rdno, pool)].append(row)
        try:
            sequences.add(int(rdno))
        except ValueError:
            raise ValueError(f"Poolwise Strokes has non-integer legacy RdNo {rdno!r}.")

    duplicates = [key for key, group in grouped.items() if len(group) > 1]
    if duplicates:
        preview = ", ".join(f"RdNo={rdno}/Pool={pool}" for rdno, pool in duplicates[:10])
        raise ValueError(
            "Poolwise Strokes contains repeated true legacy (RdNo, Pool) pairs; "
            f"migration will not collapse them: {preview}"
        )

    if sequences:
        print(
            "SHAM history semantics verified: "
            f"{len(sham_rows)} pool rows across {len(sequences)} all-time RdNo values "
            f"({min(sequences)}..{max(sequences)}). RoundNo remains season-local source "
            "metadata and is not used as a database key.",
            flush=True,
        )

    return normalized


migration_cli.normalize_rows_for_bootstrap = normalize_rows_with_sham_semantic_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "BirdBrain normalized bootstrap with NULL-par support for unplayed "
            "rounds and legacy SHAM identifier validation. Dry-run is the default."
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
