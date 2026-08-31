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


_original_normalize_rows = migration_cli.normalize_rows_for_bootstrap


def _meaningful_sham_signature(row: dict) -> tuple[str, ...]:
    """Fields that define one historical SHAM pool observation.

    LegacyRdNo is intentionally excluded: in the live legacy sheet it is the old
    completed-round sequence, while RdNo has been normalized to the actual league
    schedule RoundNo. If two source rows map to the same actual round/pool and all
    statistical content agrees, they are duplicate representations of one fact.
    """
    fields = (
        "Course",
        "Layout",
        "Par",
        "Players",
        "Strokes",
        "Avg",
        "StdDev",
        "ParStrokes",
    )
    return tuple(nonblank(row.get(field)) for field in fields)


def _dedupe_sham_pool_rows(rows: list[dict]) -> tuple[list[dict], int]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    passthrough: list[dict] = []

    for row in rows:
        rdno = nonblank(row.get("RdNo"))
        pool = nonblank(row.get("Pool")).upper()
        if not rdno or not pool:
            passthrough.append(row)
            continue
        grouped[(rdno, pool)].append(row)

    result = list(passthrough)
    collapsed = 0
    conflicts: list[str] = []

    for (rdno, pool), group in grouped.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        signatures = {_meaningful_sham_signature(row) for row in group}
        if len(signatures) == 1:
            result.append(group[0])
            collapsed += len(group) - 1
            continue

        details = []
        for index, row in enumerate(group, start=1):
            details.append(
                f"row{index}(LegacyRdNo={nonblank(row.get('LegacyRdNo'))!r}, "
                f"Course={nonblank(row.get('Course'))!r}, "
                f"Layout={nonblank(row.get('Layout'))!r}, "
                f"Par={nonblank(row.get('Par'))!r}, "
                f"Players={nonblank(row.get('Players'))!r}, "
                f"Strokes={nonblank(row.get('Strokes'))!r}, "
                f"ParStrokes={nonblank(row.get('ParStrokes'))!r})"
            )
        conflicts.append(f"Round {rdno}, pool {pool}: " + "; ".join(details))

    if conflicts:
        raise ValueError(
            "Conflicting duplicate SHAM rows map to the same (round, pool); "
            "migration will not guess which is authoritative:\n  - "
            + "\n  - ".join(conflicts)
        )

    return result, collapsed


def normalize_rows_with_sham_dedupe(report: dict, rows: dict) -> dict:
    normalized = _original_normalize_rows(report, rows)
    deduped, collapsed = _dedupe_sham_pool_rows(
        normalized.get("Poolwise Strokes by Round", [])
    )
    normalized["Poolwise Strokes by Round"] = deduped
    if collapsed:
        print(
            f"Normalized SHAM history: collapsed {collapsed} exact duplicate "
            "round/pool row(s).",
            flush=True,
        )
    return normalized


migration_cli.normalize_rows_for_bootstrap = normalize_rows_with_sham_dedupe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "BirdBrain normalized bootstrap with NULL-par support for any unplayed "
            "schedule round and guarded SHAM duplicate normalization. Dry-run is "
            "the default."
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
