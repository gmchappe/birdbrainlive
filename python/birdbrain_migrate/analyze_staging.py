from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

REPORT_ROOT = Path("data/reports")

STATIC_REQUIRED_HEADERS: dict[str, list[str]] = {
    "League Schedule": [
        "Course", "Layout", "Datend", "Date", "StartTime", "Note",
        "RoundNo", "AcePot", "Par", "ParFours", "ParFives",
    ],
    "Leaderboard": ["Name", "Points", "Rounds", "Handicap"],
    "Course Slopes and Ratings": [
        "Course", "Layout", "Par", "TotRds", "StrokeTotal", "ParTotal",
        "GM", "Rating", "Slope", "StdSlope", "Weight",
    ],
    "Player Pool Assignments": ["Name", "Pool"],
    "Past All Time": ["Name", "Points", "Rounds", "Season"],
    "Current All Time": ["Name", "Seasons", "Rounds", "Points", "milestonen"],
    "Aces": ["Name", "Date", "Course", "Layout", "Hole", "Payout"],
    "Course Records": ["Course", "Layout", "Name", "Score", "Date"],
    "Hall of Champions": ["Event", "Year", "Division", "Name", "Score"],
}

POOLWISE_BASE_HEADERS = [
    "Course", "Layout", "RdNo", "Par", "Pool", "Players", "Strokes"
]
POOLWISE_MODERN_HEADERS = ["Avg", "StdDev", "ParStrokes"]
POOLWISE_LEGACY_HEADERS = ["totalpar", "RoundNo"]


def connect_db() -> psycopg.Connection:
    database_url = os.getenv("BB_DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)

    required = ["BB_DB_HOST", "BB_DB_NAME", "BB_DB_USER", "BB_DB_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing database environment variables: " + ", ".join(missing)
        )

    return psycopg.connect(
        host=os.environ["BB_DB_HOST"],
        port=int(os.getenv("BB_DB_PORT", "5432")),
        dbname=os.environ["BB_DB_NAME"],
        user=os.environ["BB_DB_USER"],
        password=os.environ["BB_DB_PASSWORD"],
        sslmode=os.getenv("BB_DB_SSLMODE", "require"),
    )


def normalized_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def nonblank(value: Any) -> str:
    return "" if value is None else str(value).strip()


def legacy_round_column(course: str, date_label: str) -> str:
    return f"{nonblank(course)}_{nonblank(date_label)}".replace(" ", "_")


def canonical_round_header(value: str) -> str:
    """Treat punctuation-only differences in legacy dynamic headers as equivalent."""
    return re.sub(r"[^a-z0-9]+", "_", nonblank(value).casefold()).strip("_")


def resolve_round_header(expected: str, headers: list[str]) -> tuple[str | None, list[str]]:
    if expected in headers:
        return expected, []
    key = canonical_round_header(expected)
    matches = [h for h in headers if canonical_round_header(h) == key]
    if len(matches) == 1:
        return matches[0], matches
    return None, matches


def parse_int(value: Any) -> int | None:
    text = nonblank(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def latest_snapshot_id(cur: psycopg.Cursor) -> int:
    cur.execute(
        """
        SELECT snapshot_id
        FROM migration_staging.snapshots
        ORDER BY captured_at DESC, snapshot_id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No snapshots are loaded in migration_staging.")
    return int(row[0])


def get_sheet_metadata(cur: psycopg.Cursor, snapshot_id: int) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT sheet_name, row_count, headers, sha256
        FROM migration_staging.snapshot_sheets
        WHERE snapshot_id = %s
        ORDER BY sheet_name
        """,
        (snapshot_id,),
    )
    return {
        row[0]: {
            "row_count": int(row[1]),
            "headers": list(row[2]),
            "sha256": row[3],
        }
        for row in cur.fetchall()
    }


def get_sheet_rows(
    cur: psycopg.Cursor,
    snapshot_id: int,
    sheet_name: str,
    headers: list[str],
) -> list[dict[str, str]]:
    cur.execute(
        """
        SELECT row_data
        FROM migration_staging.google_sheet_rows
        WHERE snapshot_id = %s AND sheet_name = %s
        ORDER BY row_number
        """,
        (snapshot_id, sheet_name),
    )
    result: list[dict[str, str]] = []
    for (raw_row,) in cur.fetchall():
        values = list(raw_row)
        values += [""] * max(0, len(headers) - len(values))
        result.append(
            {header: nonblank(values[i]) for i, header in enumerate(headers)}
        )
    return result


def identity_groups(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        display = nonblank(row.get("Name"))
        if display:
            groups[normalized_name(display)].append(row)
    return groups


def duplicate_identity_details(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for key, group in identity_groups(rows).items():
        if len(group) < 2:
            continue
        details.append(
            {
                "normalized_name": key,
                "variants": sorted({nonblank(row.get("Name")) for row in group}, key=str.casefold),
                "rows": group,
            }
        )
    return details


def conflicting_values(
    rows: list[dict[str, str]], columns: list[str]
) -> dict[str, list[str]]:
    conflicts: dict[str, list[str]] = {}
    for column in columns:
        values = sorted({nonblank(row.get(column)) for row in rows if nonblank(row.get(column))})
        if len(values) > 1:
            conflicts[column] = values
    return conflicts


def analyze(snapshot_id: int | None) -> dict[str, Any]:
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            snapshot_id = snapshot_id or latest_snapshot_id(cur)
            cur.execute(
                """
                SELECT spreadsheet_id, source_url, captured_at, loaded_at
                FROM migration_staging.snapshots
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            snapshot = cur.fetchone()
            if not snapshot:
                raise RuntimeError(f"snapshot_id={snapshot_id} does not exist.")

            sheets = get_sheet_metadata(cur, snapshot_id)
            hard_errors: list[str] = []
            warnings: list[str] = []

            for sheet_name, required in STATIC_REQUIRED_HEADERS.items():
                meta = sheets.get(sheet_name)
                if not meta:
                    hard_errors.append(f"Missing required sheet: {sheet_name}")
                    continue
                missing = [name for name in required if name not in meta["headers"]]
                if missing:
                    hard_errors.append(
                        f"{sheet_name}: missing required headers {missing}; "
                        f"actual={meta['headers']}"
                    )

            poolwise_variant: str | None = None
            pool_meta = sheets.get("Poolwise Strokes by Round")
            if not pool_meta:
                hard_errors.append("Missing required sheet: Poolwise Strokes by Round")
            else:
                headers = pool_meta["headers"]
                missing_base = [h for h in POOLWISE_BASE_HEADERS if h not in headers]
                if missing_base:
                    hard_errors.append(
                        "Poolwise Strokes by Round: missing base headers "
                        f"{missing_base}; actual={headers}"
                    )
                elif all(h in headers for h in POOLWISE_MODERN_HEADERS):
                    poolwise_variant = "modern"
                elif all(h in headers for h in POOLWISE_LEGACY_HEADERS):
                    poolwise_variant = "legacy_live"
                    warnings.append(
                        "Poolwise Strokes by Round uses the live legacy SHAM schema "
                        "(totalpar/RoundNo). totalpar will map to par_strokes; Avg and "
                        "StdDev are not present and will remain NULL."
                    )
                else:
                    hard_errors.append(
                        "Poolwise Strokes by Round is neither the modern nor known live "
                        f"legacy schema; actual={headers}"
                    )

            handicap_meta = sheets.get("Handicap")
            if not handicap_meta:
                hard_errors.append("Missing required sheet: Handicap")
            else:
                handicap_headers = handicap_meta["headers"]
                if not handicap_headers or handicap_headers[0] != "Name":
                    hard_errors.append(
                        f"Handicap: first header must be Name; actual={handicap_headers}"
                    )
                if "Handicap" not in handicap_headers:
                    hard_errors.append(
                        f"Handicap: current Handicap column not found; actual={handicap_headers}"
                    )

            score_meta = sheets.get("Full Season Scores")
            if not score_meta:
                hard_errors.append("Missing required sheet: Full Season Scores")
            else:
                score_headers = score_meta["headers"]
                if not score_headers or score_headers[0] != "Name":
                    hard_errors.append(
                        f"Full Season Scores: first header must be Name; actual={score_headers}"
                    )

            rows_by_sheet: dict[str, list[dict[str, str]]] = {}
            for sheet_name in sheets:
                rows_by_sheet[sheet_name] = get_sheet_rows(
                    cur, snapshot_id, sheet_name, sheets[sheet_name]["headers"]
                )

            schedule_rows = rows_by_sheet.get("League Schedule", [])
            score_headers = sheets.get("Full Season Scores", {}).get("headers", [])
            handicap_headers = sheets.get("Handicap", {}).get("headers", [])

            round_mappings: list[dict[str, Any]] = []
            used_score_columns: set[str] = set()
            used_handicap_columns: set[str] = set()
            expected_header_groups: dict[str, list[int]] = defaultdict(list)

            for row in schedule_rows:
                round_no = parse_int(row.get("RoundNo"))
                if round_no is None:
                    continue
                expected_column = legacy_round_column(row.get("Course", ""), row.get("Date", ""))
                expected_header_groups[canonical_round_header(expected_column)].append(round_no)
                score_column, score_matches = resolve_round_header(expected_column, score_headers)
                handicap_column, handicap_matches = resolve_round_header(expected_column, handicap_headers)

                if len(score_matches) > 1:
                    hard_errors.append(
                        f"Round {round_no}: score header {expected_column!r} has ambiguous "
                        f"punctuation-normalized matches {score_matches}."
                    )
                if len(handicap_matches) > 1:
                    hard_errors.append(
                        f"Round {round_no}: handicap header {expected_column!r} has ambiguous "
                        f"punctuation-normalized matches {handicap_matches}."
                    )

                if score_column:
                    used_score_columns.add(score_column)
                    if score_column != expected_column:
                        warnings.append(
                            f"Round {round_no} score column {score_column!r} matched schedule "
                            f"column {expected_column!r} after punctuation normalization."
                        )
                if handicap_column:
                    used_handicap_columns.add(handicap_column)
                    if handicap_column != expected_column:
                        warnings.append(
                            f"Round {round_no} handicap column {handicap_column!r} matched schedule "
                            f"column {expected_column!r} after punctuation normalization."
                        )

                round_mappings.append(
                    {
                        "round_no": round_no,
                        "course": row.get("Course", ""),
                        "layout": row.get("Layout", ""),
                        "date": row.get("Datend", ""),
                        "date_label": row.get("Date", ""),
                        "legacy_column": expected_column,
                        "season_score_column": score_column,
                        "handicap_adjustment_column": handicap_column,
                        "has_season_score_column": score_column is not None,
                        "has_handicap_adjustment_column": handicap_column is not None,
                    }
                )

            for key, round_numbers in expected_header_groups.items():
                if len(round_numbers) > 1:
                    warnings.append(
                        f"Schedule rounds {round_numbers} share the same legacy Sheet column "
                        f"identity {key!r}. PostgreSQL can distinguish them by round number, "
                        "but the legacy Sheet cannot represent both with one unsuffixed header."
                    )

            completed = [m for m in round_mappings if m["has_season_score_column"]]
            unmatched_score_columns = [
                header for header in score_headers[1:]
                if header and header not in used_score_columns
            ]
            unmatched_handicap_columns = [
                header for header in handicap_headers[1:]
                if header != "Handicap" and header not in used_handicap_columns
            ]

            if unmatched_score_columns:
                hard_errors.append(
                    "Full Season Scores contains round columns that do not map to the schedule: "
                    + repr(unmatched_score_columns)
                )
            if unmatched_handicap_columns:
                hard_errors.append(
                    "Handicap contains adjustment columns that do not map to the schedule: "
                    + repr(unmatched_handicap_columns)
                )

            for mapping in completed:
                if not mapping["has_handicap_adjustment_column"]:
                    warnings.append(
                        f"Round {mapping['round_no']} has gross scores but no matching "
                        f"Handicap history column {mapping['legacy_column']!r}."
                    )

            identity_merges: dict[str, list[dict[str, Any]]] = {}
            for sheet_name in ["Leaderboard", "Handicap", "Full Season Scores", "Current All Time"]:
                rows = rows_by_sheet.get(sheet_name, [])
                details = duplicate_identity_details(rows)
                if not details:
                    continue
                identity_merges[sheet_name] = details
                variants = [d["variants"] for d in details]

                if sheet_name == "Leaderboard":
                    warnings.append(
                        f"Leaderboard has case/spacing aliases {variants}. They will be merged "
                        "as one player identity and split Points/Rounds will be summed."
                    )
                elif sheet_name == "Current All Time":
                    warnings.append(
                        f"Current All Time has case/spacing aliases {variants}. It is a derived "
                        "parity target; normalized all-time totals will be rebuilt from Past All "
                        "Time plus the current-season migration baseline."
                    )
                else:
                    dynamic_columns: list[str]
                    if sheet_name == "Handicap":
                        dynamic_columns = sorted(used_handicap_columns)
                    else:
                        dynamic_columns = sorted(used_score_columns)
                    for detail in details:
                        conflicts = conflicting_values(detail["rows"], dynamic_columns)
                        if conflicts:
                            hard_errors.append(
                                f"{sheet_name}: aliases {detail['variants']} contain conflicting "
                                f"values for the same round columns: {conflicts}"
                            )
                    if not any(
                        conflicting_values(d["rows"], dynamic_columns) for d in details
                    ):
                        warnings.append(
                            f"{sheet_name} has case/spacing aliases {variants}. Their nonconflicting "
                            "round histories will be merged before bootstrap."
                        )

            leaderboard_names = {
                normalized_name(row.get("Name", ""))
                for row in rows_by_sheet.get("Leaderboard", [])
                if nonblank(row.get("Name"))
            }
            score_names = {
                normalized_name(row.get("Name", ""))
                for row in rows_by_sheet.get("Full Season Scores", [])
                if nonblank(row.get("Name"))
            }
            handicap_names = {
                normalized_name(row.get("Name", ""))
                for row in rows_by_sheet.get("Handicap", [])
                if nonblank(row.get("Name"))
            }

            zero_round_members = sorted(leaderboard_names - score_names)
            scores_not_on_board = sorted(score_names - leaderboard_names)
            board_without_handicap_row = sorted(leaderboard_names - handicap_names)

            if scores_not_on_board:
                warnings.append(
                    f"{len(scores_not_on_board)} Full Season Scores players are not on Leaderboard."
                )
            if board_without_handicap_row:
                warnings.append(
                    f"{len(board_without_handicap_row)} Leaderboard players lack a Handicap row."
                )

            report: dict[str, Any] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_id": snapshot_id,
                "spreadsheet_id": snapshot[0],
                "source_url": snapshot[1],
                "captured_at": snapshot[2].isoformat(),
                "loaded_at": snapshot[3].isoformat(),
                "sheet_contracts": {
                    name: {
                        "row_count": meta["row_count"],
                        "headers": meta["headers"],
                    }
                    for name, meta in sheets.items()
                },
                "poolwise_schema_variant": poolwise_variant,
                "identity_merges": identity_merges,
                "round_mappings": sorted(round_mappings, key=lambda x: x["round_no"]),
                "completed_round_count": len(completed),
                "player_counts": {
                    "leaderboard": len(leaderboard_names),
                    "full_season_scores": len(score_names),
                    "handicap": len(handicap_names),
                    "zero_round_leaderboard_members": len(zero_round_members),
                },
                "hard_errors": hard_errors,
                "warnings": warnings,
                "ready_for_transform": not hard_errors,
            }
            return report
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a staged BirdBrain snapshot against legacy sheet contracts."
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument(
        "--report-root", type=Path, default=REPORT_ROOT,
        help="Local gitignored directory for JSON analysis reports.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = analyze(args.snapshot_id)
    args.report_root.mkdir(parents=True, exist_ok=True)
    report_path = args.report_root / f"staging_snapshot_{report['snapshot_id']}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Snapshot ID: {report['snapshot_id']}")
    print(f"Captured: {report['captured_at']}")
    print(f"Completed score-history rounds: {report['completed_round_count']}")
    print(f"Pool history schema: {report['poolwise_schema_variant']}")
    counts = report["player_counts"]
    print(
        "Players: "
        f"leaderboard={counts['leaderboard']}, "
        f"scores={counts['full_season_scores']}, "
        f"handicap={counts['handicap']}, "
        f"zero-round-board={counts['zero_round_leaderboard_members']}"
    )

    print("\nRound mappings:")
    for mapping in report["round_mappings"]:
        state = "SCORED" if mapping["has_season_score_column"] else "scheduled"
        handicap = "hcp=yes" if mapping["has_handicap_adjustment_column"] else "hcp=no"
        actual = mapping.get("season_score_column")
        suffix = "" if not actual or actual == mapping["legacy_column"] else f" -> {actual}"
        print(
            f"  R{mapping['round_no']:>2}: {mapping['legacy_column']}{suffix} "
            f"[{state}, {handicap}]"
        )

    if report["warnings"]:
        print("\nWarnings / planned normalizations:")
        for warning in report["warnings"]:
            print(f"  - {warning}")

    if report["hard_errors"]:
        print("\nBLOCKING ERRORS:")
        for error in report["hard_errors"]:
            print(f"  - {error}")
        print(f"\nFull report: {report_path}")
        raise SystemExit(2)

    print("\nContract check PASSED. No normalized tables were changed.")
    print(f"Full report: {report_path}")


if __name__ == "__main__":
    main()
