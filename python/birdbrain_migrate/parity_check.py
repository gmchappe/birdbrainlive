from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from analyze_staging import (
    analyze,
    connect_db,
    get_sheet_metadata,
    get_sheet_rows,
    latest_snapshot_id,
    nonblank,
    parse_int,
)
from bootstrap_normalized import parse_date, parse_decimal
from source_normalization import normalize_rows_for_bootstrap

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
        raise ValueError(f"Expected integer-compatible value, got {value!r}")
    return str(parsed)


def canonical_row(sheet: str, row: dict[str, Any], columns: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for column in columns:
        value = row.get(column)
        if sheet == "League Schedule" and column == "Date":
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


def hall_historical_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe source Hall rows that are not yet complete champion facts."""
    gaps: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        event = nonblank(row.get("Event"))
        year = parse_int(row.get("Year"))
        name = nonblank(row.get("Name"))
        if event and year is not None and name:
            continue
        missing_fields = [
            field
            for field, value in (("Event", event), ("Year", year), ("Name", name))
            if value in (None, "")
        ]
        gaps.append(
            {
                "source_row_number": row_number,
                "Event": event,
                "Year": nonblank(row.get("Year")),
                "Division": nonblank(row.get("Division")),
                "Name": name,
                "Score": nonblank(row.get("Score")),
                "missing_fields": missing_fields,
                "reason": (
                    "missing_name_unconfirmed_historical_finisher"
                    if missing_fields == ["Name"]
                    else "incomplete_historical_champion_record"
                ),
            }
        )
    return gaps


def authoritative_source_rows(
    sheet: str, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Return rows representable as normalized application facts.

    The legacy Hall sheet contains label/placeholder rows with an Event and Year
    but no Name. Legacy Shiny displayed the sheet verbatim, but the normalized
    hall_of_champions table intentionally requires a player_id. Those placeholders
    are retained in the immutable staging snapshot for auditability and excluded
    from normalized champion-fact parity rather than being fabricated as players.
    """
    if sheet != "Hall of Champions":
        return rows, 0

    kept: list[dict[str, Any]] = []
    ignored = 0
    for row in rows:
        if nonblank(row.get("Event")) and parse_int(row.get("Year")) is not None and nonblank(row.get("Name")):
            kept.append(row)
        else:
            ignored += 1
    return kept, ignored


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
    analysis = analyze(snapshot_id)
    if not analysis["ready_for_transform"]:
        raise RuntimeError("Cannot run parity against a staging snapshot with blocking errors.")
    snapshot_id = int(analysis["snapshot_id"])

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            metadata = get_sheet_metadata(cur, snapshot_id)
            raw_source = {
                sheet: get_sheet_rows(cur, snapshot_id, sheet, meta["headers"])
                for sheet, meta in metadata.items()
            }
            hall_gaps = hall_historical_gaps(raw_source.get("Hall of Champions", []))
            source = normalize_rows_for_bootstrap(analysis, raw_source)

            report: dict[str, Any] = {
                "snapshot_id": snapshot_id,
                "source_mode": (
                    "canonicalized_staged_snapshot_with_documented_historical_gaps"
                    if hall_gaps
                    else "canonicalized_staged_snapshot"
                ),
                "identity_merges": analysis.get("identity_merges", {}),
                "historical_data_gaps": {"Hall of Champions": hall_gaps},
                "checks": {},
                "all_passed": True,
            }

            for sheet, spec in VIEW_SPECS.items():
                columns = spec["columns"]
                source_dicts, ignored_source_rows = authoritative_source_rows(
                    sheet, source.get(sheet, [])
                )
                db_dicts = fetch_view_rows(cur, spec["view"], columns)

                source_rows = sorted(canonical_row(sheet, row, columns) for row in source_dicts)
                db_rows = sorted(canonical_row(sheet, row, columns) for row in db_dicts)
                missing, extra = multiset_diff(source_rows, db_rows)
                passed = not missing and not extra
                report["checks"][sheet] = {
                    "view": spec["view"],
                    "source_rows": len(source_rows),
                    "ignored_non_authoritative_source_rows": ignored_source_rows,
                    "ignored_source_details": hall_gaps if sheet == "Hall of Champions" else [],
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
        description=(
            "Compare normalized BirdBrain compatibility views with authoritative facts "
            "from the canonicalized staged Google snapshot. Incomplete Hall placeholders "
            "are documented rather than fabricated."
        )
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
    print(f"Source mode: {report['source_mode']}")
    for sheet, result in report["checks"].items():
        state = "PASS" if result["passed"] else "FAIL"
        ignored = result.get("ignored_non_authoritative_source_rows", 0)
        ignored_text = f" flagged_incomplete={ignored}" if ignored else ""
        print(
            f"  {state:4} {sheet}: source={result['source_rows']} "
            f"db={result['database_rows']} missing={result['missing_count']} "
            f"extra={result['extra_count']}{ignored_text}"
        )

    gaps = report["historical_data_gaps"].get("Hall of Champions", [])
    if gaps:
        print("\nDocumented Hall of Champions historical gaps:")
        for item in gaps:
            print(
                f"  source row {item['source_row_number']}: {item['Year']} | "
                f"{item['Event']} | {item['Division']} | "
                f"Name={item['Name'] or '[UNCONFIRMED]'} | {item['Score']} | "
                f"{item['reason']}"
            )

    print(f"\nFull report: {report_path}")
    if not report["all_passed"]:
        raise SystemExit(2)
    print("All normalized public compatibility views match the authoritative staged source facts.")


if __name__ == "__main__":
    main()
