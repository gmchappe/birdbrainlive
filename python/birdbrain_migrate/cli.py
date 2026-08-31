from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

# Load the repository-local, gitignored .env before importing modules that open
# database connections.
load_dotenv()

from analyze_staging import analyze, connect_db, nonblank  # noqa: E402
import bootstrap_normalized as bootstrap_module  # noqa: E402


def parse_google_export_date(value) -> date | None:
    """Accept date-only and datetime-shaped values emitted by Google CSV exports."""
    text = nonblank(value)
    if not text:
        return None

    # Google's CSV export commonly emits midnight timestamps such as
    # 2026-04-12 00:00:00 even when the Sheet cell is conceptually a date.
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # Also accept ISO 8601 values with timezone offsets or a trailing Z.
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_text).date()
    except ValueError as exc:
        raise ValueError(f"Unrecognized date value: {value!r}") from exc


# Patch the shared migration module before importing parity_check. apply_bootstrap
# resolves parse_date from bootstrap_normalized at runtime, and parity_check imports
# the same function below, so both code paths use the exact same robust parser.
bootstrap_module.parse_date = parse_google_export_date

from bootstrap_normalized import (  # noqa: E402
    DEFAULT_LEAGUE_NAME,
    apply_bootstrap,
    get_snapshot_rows,
    plan_summary,
)
from load_staging import load_snapshot  # noqa: E402
from parity_check import run_parity  # noqa: E402
from source_normalization import normalize_rows_for_bootstrap  # noqa: E402

REPORT_ROOT = Path("data/reports")


def write_report(prefix: str, snapshot_id: int, payload: dict) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORT_ROOT / f"{prefix}_snapshot_{snapshot_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def cmd_load(args: argparse.Namespace) -> None:
    snapshot_id = load_snapshot(args.snapshot)
    print(f"Loaded snapshot_id={snapshot_id} into migration_staging.")
    print("No normalized BirdBrain application tables were changed.")


def cmd_analyze(args: argparse.Namespace) -> None:
    report = analyze(args.snapshot_id)
    path = write_report("staging", int(report["snapshot_id"]), report)
    print(f"Snapshot ID: {report['snapshot_id']}")
    print(f"Captured: {report['captured_at']}")
    print(f"Completed score-history rounds: {report['completed_round_count']}")
    print(f"Pool history schema: {report.get('poolwise_schema_variant')}")
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
        hcp = "hcp=yes" if mapping["has_handicap_adjustment_column"] else "hcp=no"
        actual = mapping.get("season_score_column")
        suffix = "" if not actual or actual == mapping["legacy_column"] else f" -> {actual}"
        print(
            f"  R{mapping['round_no']:>2}: {mapping['legacy_column']}{suffix} "
            f"[{state}, {hcp}]"
        )
    if report["warnings"]:
        print("\nWarnings / planned normalizations:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    if report["hard_errors"]:
        print("\nBLOCKING ERRORS:")
        for error in report["hard_errors"]:
            print(f"  - {error}")
        print(f"\nFull report: {path}")
        raise SystemExit(2)
    print("\nContract check PASSED. No normalized tables were changed.")
    print(f"Full report: {path}")


def cmd_bootstrap(args: argparse.Namespace) -> None:
    report = analyze(args.snapshot_id)
    if not report["ready_for_transform"]:
        raise SystemExit("Staging contract has blocking errors; bootstrap refused.")
    snapshot_id = int(report["snapshot_id"])
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            source_rows = get_snapshot_rows(cur, snapshot_id)
    finally:
        conn.close()

    rows = normalize_rows_for_bootstrap(report, source_rows)
    summary = plan_summary(report, rows)
    summary["identity_merges"] = sum(
        len(groups) for groups in report.get("identity_merges", {}).values()
    )
    summary["poolwise_schema"] = report.get("poolwise_schema_variant")

    print(f"Snapshot ID: {snapshot_id}")
    print("Bootstrap plan (after deterministic source normalization):")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    if not args.apply:
        print("\nDRY RUN ONLY: no normalized tables were changed.")
        print("Re-run with --apply after reviewing this plan.")
        return
    apply_bootstrap(snapshot_id, report, rows, args.league_name)
    print("\nBootstrap import COMMITTED.")
    print(f"Migration standings baseline is through round {summary['latest_completed_round']}.")
    print("Run parity next; do not wire Shiny to Postgres until parity passes.")


def cmd_parity(args: argparse.Namespace) -> None:
    report = run_parity(args.snapshot_id)
    path = write_report("parity", int(report["snapshot_id"]), report)
    print(f"Parity check for snapshot_id={report['snapshot_id']}")
    for sheet, result in report["checks"].items():
        state = "PASS" if result["passed"] else "FAIL"
        print(
            f"  {state:4} {sheet}: source={result['source_rows']} "
            f"db={result['database_rows']} missing={result['missing_count']} "
            f"extra={result['extra_count']}"
        )
    print(f"\nFull report: {path}")
    if not report["all_passed"]:
        raise SystemExit(2)
    print("All public compatibility views match the staged Google Sheet snapshot.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BirdBrain migration utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load-staging", help="Load the newest local Sheet snapshot into staging")
    load.add_argument("snapshot", nargs="?", type=Path)
    load.set_defaults(func=cmd_load)

    analyze_cmd = sub.add_parser("analyze", help="Validate staged Sheet contracts and round mappings")
    analyze_cmd.add_argument("--snapshot-id", type=int)
    analyze_cmd.set_defaults(func=cmd_analyze)

    bootstrap = sub.add_parser("bootstrap", help="Plan or apply the normalized bootstrap import")
    bootstrap.add_argument("--snapshot-id", type=int)
    bootstrap.add_argument("--league-name", default=DEFAULT_LEAGUE_NAME)
    bootstrap.add_argument("--apply", action="store_true")
    bootstrap.set_defaults(func=cmd_bootstrap)

    parity = sub.add_parser("parity", help="Compare Postgres compatibility views to the staged Sheet")
    parity.add_argument("--snapshot-id", type=int)
    parity.set_defaults(func=cmd_parity)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
