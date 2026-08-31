from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from analyze_staging import (
    connect_db,
    get_sheet_metadata,
    get_sheet_rows,
    latest_snapshot_id,
    nonblank,
    parse_int,
)
from bootstrap_normalized import parse_date, parse_decimal

REPORT_ROOT = Path("data/reports")

VIEW_SPECS = {
    "League Schedule": {
        "view": "v_schedule",
        "columns": [
            "Course", "Layout", "Datend", "Date", "StartTime", "Note", "RoundNo", "AcePot"
        ],
    },
    "Leaderboard": {
        "view": "v_leaderboard",
        "columns": ["Name", "Points", "Rounds", "Handicap"],
    },
    "Current All Time": {
        "view": "v_current_all_time",
        "columns": ["Name", "Seasons", "Rounds", "Points"],
    },
    "Course Records": {
        "view": "v_course_records",
        "columns": ["Course", "Layout", "Name", "Score", "Date"],
    },
    "Aces": {
        "view": "v_aces",
        "columns": ["Name", "Date", "Course", "Layout", "Hole", "Payout"],
    },
    "Hall of Champions": {
        "view": "v_hall_of_champions",
        "columns": ["Event", "Year", "Division", "Name", "Score"],
    },
}

INTEGER_FIELDS = {"RoundNo", "AcePot", "Points", "Rounds", "Seasons", "Score", "Hole", "Year"}
DATE_FIELDS = {"Datend", "Date"}
DECIMAL_FIELDS = {"Payout"}


def canonical_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def canonical_date(value: Any) -> str:
    if value is None or canonical_text(value) == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else ""


def canonical_decimal(value: Any) -> str:
    if value is None or canonical_text(value) == "":
        return ""
    if isinstance(value, Decimal):
        number = value
    else:
        number = parse_decimal(value)
    if number is None:
        return ""
    normalized = number.normalize()
    # Decimal('0E-2') should compare as 0.
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def canonical_integer(value: Any) -> str:
    if value is None or canonical_text(value) == "":
        return ""
    if isinstance(value, int):
        return str(value)
    parsed = parse_int(value)
    if parsed is None:
        # Course-record Score is an integer, but Hall Score is intentionally text;
        # only fields routed here should be numeric.
        raise ValueError(f"Expected integer-compatible value, got {value!r}")
    return str(parsed)


def canonical_row(sheet: str, row: dict[str, Any], columns: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for column in columns:
        value = row.get(column)
        if sheet == "League Schedule" and column == "Date":
            # This is a display label (e.g. Apr 12), not a typed date.
            result.append(canonical_text(value))
        elif column in DATE_FIELDS:
            result.append(canonical_date(value))
        elif column in DECIMAL_FIELDS:
            result.append(canonical_decimal(value))
        elif column in INTEGER_FIELDS and not (sheet == "Hall of Champions" and column == "Score"):
            result.append(canonical_integer(value))
        else:
            result.append(canonical_text(value))
    return tuple(result)


def fetch_view_rows(cur, view: str, columns: list[str]) -> list[dict[str, Any]]:
    quoted = ", ".join(f'"{column}"' for column in columns)
    cur.execute(f"SELECT {quoted} FROM {view}")
    names = [desc.name for desc in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def multiset_diff(
    source_rows: list[tuple[str, ...]], db_rows: list[tuple[str, ...]]
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    from collections import Counter

    source_counter = Counter(source_rows)
    db_counter = Counter(db_rows)
    missing: list[tuple[str, ...]] = []
    extra: list[tuple[str, ...]] = []
    for row, count in (source_counter - db_counter).items():
        missing.extend([row] * count)
    for row, count in (db_counter - source_counter).items():
        extra.extend([row] * count)
    return missing, extra


def run_parity(snapshot_id: int | None) -> dict[str, Any]:
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            snapshot_id = snapshot_id or latest_snapshot_id(cur)
            metadata = get_sheet_metadata(cur, snapshot_id)
            report: dict[str, Any] = {
                "snapshot_id": snapshot_id,
                "checks": {},
                "all_passed": True,
            }

            for sheet, spec in VIEW_SPECS.items():
                columns = spec["columns"]
                headers = metadata[sheet]["headers"]
                source_dicts = get_sheet_rows(cur, snapshot_id, sheet, headers)
                db_dicts = fetch_view_rows(cur, spec["view"], columns)

                source_rows = sorted(canonical_row(sheet, row, columns) for row in source_dicts)
                db_rows = sorted(canonical_row(sheet, row, columns) for row in db_dicts)
                missing, extra = multiset_diff(source_rows, db_rows)
                passed = not missing and not extra
                report["checks"][sheet] = {
                    "view": spec["view"],
                    "source_rows": len(source_rows),
                    "database_rows": len(db_rows),
                    "passed": passed,
                    "missing_from_database": [list(row) for row in missing[:20]],
                    "extra_in_database": [list(row) for row in extra[:20]],
                    "missing_count": len(missing),
                    "extra_count": len(extra),
                }
                if not passed:
                    report["all_passed"] = False

            return report
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare normalized BirdBrain compatibility views with a staged Google snapshot."
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_parity(args.snapshot_id)
    args.report_root.mkdir(parents=True, exist_ok=True)
    report_path = args.report_root / f"parity_snapshot_{report['snapshot_id']}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Parity check for snapshot_id={report['snapshot_id']}")
    for sheet, result in report["checks"].items():
        state = "PASS" if result["passed"] else "FAIL"
        print(
            f"  {state:4} {sheet}: source={result['source_rows']} "
            f"db={result['database_rows']} missing={result['missing_count']} "
            f"extra={result['extra_count']}"
        )

    print(f"\nFull report: {report_path}")
    if not report["all_passed"]:
        raise SystemExit(2)
    print("All public compatibility views match the staged Google Sheet snapshot.")


if __name__ == "__main__":
    main()
