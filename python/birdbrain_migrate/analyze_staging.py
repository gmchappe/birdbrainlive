from __future__ import annotations

import argparse
import json
import os
import re
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
    "Poolwise Strokes by Round": [
        "RdNo", "Course", "Layout", "Par", "Pool", "Players", "Strokes",
        "Avg", "StdDev", "ParStrokes",
    ],
    "Past All Time": ["Name", "Points", "Rounds", "Season"],
    "Current All Time": ["Name", "Seasons", "Rounds", "Points", "milestonen"],
    "Aces": ["Name", "Date", "Course", "Layout", "Hole", "Payout"],
    "Course Records": ["Course", "Layout", "Name", "Score", "Date"],
    "Hall of Champions": ["Event", "Year", "Division", "Name", "Score"],
}


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


def duplicate_names(rows: list[dict[str, str]]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: set[str] = set()
    for row in rows:
        display = nonblank(row.get("Name"))
        if not display:
            continue
        key = normalized_name(display)
        if key in seen:
            duplicates.add(display)
            duplicates.add(seen[key])
        else:
            seen[key] = display
    return sorted(duplicates, key=str.casefold)


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

            for sheet_name in [
                "Leaderboard", "Handicap", "Full Season Scores", "Past All Time",
                "Current All Time", "Aces", "Course Records", "Hall of Champions",
            ]:
                rows = rows_by_sheet.get(sheet_name, [])
                if "Name" not in sheets.get(sheet_name, {}).get("headers", []):
                    continue
                duplicates = duplicate_names(rows)
                # Multiple Aces/Records/Championships by the same player are legitimate.
                if duplicates and sheet_name in {
                    "Leaderboard", "Handicap", "Full Season Scores", "Current All Time"
                }:
                    hard_errors.append(
                        f"{sheet_name}: duplicate case-insensitive player names: {duplicates}"
                    )

            schedule_rows = rows_by_sheet.get("League Schedule", [])
            score_headers = sheets.get("Full Season Scores", {}).get("headers", [])
            handicap_headers = sheets.get("Handicap", {}).get("headers", [])

            round_mappings: list[dict[str, Any]] = []
            for row in schedule_rows:
                round_no = parse_int(row.get("RoundNo"))
                if round_no is None:
                    continue
                expected_column = legacy_round_column(row.get("Course", ""), row.get("Date", ""))
                score_match = expected_column in score_headers
                handicap_match = expected_column in handicap_headers
                round_mappings.append(
                    {
                        "round_no": round_no,
                        "course": row.get("Course", ""),
                        "layout": row.get("Layout", ""),
                        "date": row.get("Datend", ""),
                        "date_label": row.get("Date", ""),
                        "legacy_column": expected_column,
                        "has_season_score_column": score_match,
                        "has_handicap_adjustment_column": handicap_match,
                    }
                )

            completed = [m for m in round_mappings if m["has_season_score_column"]]
            unmatched_score_columns = [
                header
                for header in score_headers[1:]
                if header and header not in {m["legacy_column"] for m in round_mappings}
            ]
            unmatched_handicap_columns = [
                header
                for header in handicap_headers[1:]
                if header not in {"Handicap"}
                and header not in {m["legacy_column"] for m in round_mappings}
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
        print(
            f"  R{mapping['round_no']:>2}: {mapping['legacy_column']} "
            f"[{state}, {handicap}]"
        )

    if report["warnings"]:
        print("\nWarnings:")
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
