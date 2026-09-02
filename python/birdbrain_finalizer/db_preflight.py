from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


class ProbeRollback(RuntimeError):
    """Internal sentinel used to force rollback after transient writes."""


@dataclass(frozen=True)
class RoundInfo:
    round_id: int
    round_no: int
    status: str
    finalized_at: Any
    results_published_at: Any
    season_id: int
    season_name: str
    season_year: int | None
    league_id: int
    league_name: str
    course: str
    layout: str
    note: str | None


def connect_admin() -> psycopg.Connection:
    required = [
        "BB_DB_HOST",
        "BB_DB_PORT",
        "BB_DB_NAME",
        "BB_DB_USER",
        "BB_DB_PASSWORD",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing migration/admin database variables: " + ", ".join(missing)
        )

    configured_user = os.environ["BB_DB_USER"]
    if configured_user.startswith("birdbrain_shiny_reader"):
        raise RuntimeError(
            "Rollback probe requires migration/admin BB_DB_* credentials, not the "
            "least-privilege Shiny reader."
        )

    return psycopg.connect(
        host=os.environ["BB_DB_HOST"],
        port=int(os.environ["BB_DB_PORT"]),
        dbname=os.environ["BB_DB_NAME"],
        user=configured_user,
        password=os.environ["BB_DB_PASSWORD"],
        sslmode=os.getenv("BB_DB_SSLMODE", "require"),
    )


def fetch_round(
    cur: psycopg.Cursor,
    round_no: int,
    season_year: int | None = None,
    *,
    for_update: bool = False,
) -> RoundInfo:
    year_clause = "AND s.season_year = %s" if season_year is not None else ""
    lock_clause = "FOR UPDATE OF r" if for_update else ""
    params: tuple[Any, ...] = (round_no, season_year) if season_year is not None else (round_no,)

    cur.execute(
        f"""
        SELECT
          r.round_id,
          r.round_no,
          r.status,
          r.finalized_at,
          r.results_published_at,
          s.season_id,
          s.season_name,
          s.season_year,
          lg.league_id,
          lg.name,
          c.name,
          lay.name,
          r.note
        FROM rounds r
        JOIN seasons s ON s.season_id = r.season_id
        JOIN leagues lg ON lg.league_id = s.league_id
        JOIN layouts lay ON lay.layout_id = r.layout_id
        JOIN courses c ON c.course_id = lay.course_id
        WHERE r.round_no = %s
          AND s.closed_at IS NULL
          {year_clause}
        ORDER BY s.season_year DESC NULLS LAST, s.season_id DESC
        {lock_clause}
        """,
        params,
    )
    rows = cur.fetchall()
    if not rows:
        suffix = f" in season {season_year}" if season_year is not None else " in an open season"
        raise RuntimeError(f"Could not find Round {round_no}{suffix}.")
    if len(rows) > 1:
        years = [row[7] for row in rows]
        raise RuntimeError(
            f"Round {round_no} exists in multiple open seasons {years}; pass --season-year."
        )
    return RoundInfo(*rows[0])


def round_row_json(cur: psycopg.Cursor, round_id: int) -> str:
    cur.execute(
        "SELECT to_jsonb(r)::text FROM rounds r WHERE r.round_id = %s",
        (round_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Round id {round_id} disappeared unexpectedly.")
    return row[0]


def scoped_counts(cur: psycopg.Cursor, round_id: int) -> dict[str, int]:
    queries = {
        "round_participants": (
            "SELECT COUNT(*) FROM round_participants WHERE round_id = %s",
            (round_id,),
        ),
        "round_results": (
            """
            SELECT COUNT(*)
            FROM round_results rr
            JOIN round_participants rp
              ON rp.round_participant_id = rr.round_participant_id
            WHERE rp.round_id = %s
            """,
            (round_id,),
        ),
        "playoff_resolutions": (
            "SELECT COUNT(*) FROM playoff_resolutions WHERE round_id = %s",
            (round_id,),
        ),
        "handicap_adjustments": (
            "SELECT COUNT(*) FROM handicap_adjustments WHERE round_id = %s",
            (round_id,),
        ),
        "handicap_calculations": (
            "SELECT COUNT(*) FROM handicap_calculations WHERE effective_after_round_id = %s",
            (round_id,),
        ),
        "financial_transactions": (
            "SELECT COUNT(*) FROM financial_transactions WHERE round_id = %s",
            (round_id,),
        ),
        "ace_awards": (
            "SELECT COUNT(*) FROM ace_awards WHERE round_id = %s",
            (round_id,),
        ),
        "course_records": (
            "SELECT COUNT(*) FROM course_records WHERE round_id = %s",
            (round_id,),
        ),
        "round_warning_acknowledgements": (
            "SELECT COUNT(*) FROM round_warning_acknowledgements WHERE round_id = %s",
            (round_id,),
        ),
        "audit_events": (
            "SELECT COUNT(*) FROM audit_events WHERE round_id = %s",
            (round_id,),
        ),
        "sham_pool_round_stats": (
            "SELECT COUNT(*) FROM sham_pool_round_stats WHERE round_id = %s",
            (round_id,),
        ),
    }
    counts: dict[str, int] = {}
    for name, (query, params) in queries.items():
        cur.execute(query, params)
        counts[name] = int(cur.fetchone()[0])
    return counts


def readiness(cur: psycopg.Cursor, info: RoundInfo) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
          COUNT(*)::integer AS participants,
          COUNT(*) FILTER (
            WHERE participant_type = 'member'
              AND started_round
              AND status IN ('active','dnf')
          )::integer AS eligible_field,
          COUNT(*) FILTER (WHERE participant_type = 'guest')::integer AS guests,
          COUNT(*) FILTER (WHERE status = 'dnf')::integer AS dnfs
        FROM round_participants
        WHERE round_id = %s
        """,
        (info.round_id,),
    )
    participants, eligible_field, guests, dnfs = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM hole_scores hs
        JOIN round_participants rp
          ON rp.round_participant_id = hs.round_participant_id
        WHERE rp.round_id = %s
        """,
        (info.round_id,),
    )
    hole_scores = int(cur.fetchone()[0])

    counts = scoped_counts(cur, info.round_id)
    blockers: list[str] = []
    if info.status != "results_review":
        blockers.append(f"round status is {info.status!r}, not 'results_review'")
    if info.finalized_at is not None:
        blockers.append("finalized_at is already populated")
    if participants == 0:
        blockers.append("round has no participants")
    if counts["round_results"] != 0:
        blockers.append("round_results already exist for this round")
    if counts["handicap_adjustments"] != 0:
        blockers.append("handicap adjustments already exist for this round")
    if counts["handicap_calculations"] != 0:
        blockers.append("handicap calculations already exist for this round")
    if counts["ace_awards"] != 0:
        blockers.append("ace awards already exist for this round")

    return {
        "participants": int(participants),
        "eligible_field": int(eligible_field),
        "guests": int(guests),
        "dnfs": int(dnfs),
        "hole_scores": hole_scores,
        "scoped_counts": counts,
        "finalizable_now": not blockers,
        "blockers": blockers,
    }


def rollback_probe(conn: psycopg.Connection, info: RoundInfo) -> None:
    probe_id = f"round-finalizer-rollback-probe:{uuid.uuid4()}"

    with conn.cursor() as cur:
        baseline_round = round_row_json(cur, info.round_id)
        baseline_counts = scoped_counts(cur, info.round_id)
    conn.rollback()

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                locked = fetch_round(
                    cur,
                    info.round_no,
                    info.season_year,
                    for_update=True,
                )
                if locked.round_id != info.round_id:
                    raise RuntimeError("Locked a different round than the preflight target.")

                marker = f" [ROLLBACK-PROBE {probe_id}]"
                cur.execute(
                    "UPDATE rounds SET note = COALESCE(note, '') || %s WHERE round_id = %s",
                    (marker, info.round_id),
                )
                cur.execute(
                    """
                    INSERT INTO audit_events (
                      league_id, season_id, round_id, event_type, event_payload
                    ) VALUES (%s, %s, %s, 'round_finalizer_rollback_probe', %s::jsonb)
                    """,
                    (
                        info.league_id,
                        info.season_id,
                        info.round_id,
                        json.dumps({"probe_id": probe_id}),
                    ),
                )

                cur.execute(
                    "SELECT note FROM rounds WHERE round_id = %s",
                    (info.round_id,),
                )
                transient_note = cur.fetchone()[0] or ""
                if probe_id not in transient_note:
                    raise RuntimeError("Transient round update was not visible inside transaction.")

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM audit_events
                    WHERE round_id = %s
                      AND event_type = 'round_finalizer_rollback_probe'
                      AND event_payload->>'probe_id' = %s
                    """,
                    (info.round_id, probe_id),
                )
                if int(cur.fetchone()[0]) != 1:
                    raise RuntimeError("Transient audit sentinel was not visible inside transaction.")

                raise ProbeRollback(probe_id)
    except ProbeRollback:
        pass

    with conn.cursor() as cur:
        after_round = round_row_json(cur, info.round_id)
        after_counts = scoped_counts(cur, info.round_id)
        cur.execute(
            """
            SELECT COUNT(*)
            FROM audit_events
            WHERE round_id = %s
              AND event_type = 'round_finalizer_rollback_probe'
              AND event_payload->>'probe_id' = %s
            """,
            (info.round_id, probe_id),
        )
        sentinel_count = int(cur.fetchone()[0])
    conn.rollback()

    if after_round != baseline_round:
        raise RuntimeError("ROLLBACK FAILURE: rounds row changed after forced rollback.")
    if after_counts != baseline_counts:
        raise RuntimeError(
            "ROLLBACK FAILURE: round-scoped table counts changed after forced rollback. "
            f"before={baseline_counts}, after={after_counts}"
        )
    if sentinel_count != 0:
        raise RuntimeError("ROLLBACK FAILURE: transient audit sentinel persisted.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only finalizer preflight plus a forced-rollback transaction probe. "
            "This utility never commits its transient writes."
        )
    )
    parser.add_argument("--round-no", type=int, required=True)
    parser.add_argument("--season-year", type=int)
    parser.add_argument(
        "--skip-rollback-probe",
        action="store_true",
        help="Run only readiness checks; do not exercise transient writes/rollback.",
    )
    args = parser.parse_args()

    conn = connect_admin()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_setting('TimeZone')")
            database, user, timezone = cur.fetchone()
            info = fetch_round(cur, args.round_no, args.season_year)
            state = readiness(cur, info)
        conn.rollback()

        print("BirdBrain Round Finalizer database preflight")
        print("============================================")
        print(f"Round:       R{info.round_no} - {info.course} / {info.layout}")
        print(f"Season:      {info.season_name}")
        print(f"Status:      {info.status}")
        print(f"Database:    {database}")
        print(f"DB user:     {user}")
        print(f"DB timezone: {timezone}")
        print()
        print("Readiness")
        print(f"  participants:   {state['participants']}")
        print(f"  eligible field: {state['eligible_field']}")
        print(f"  guests:         {state['guests']}")
        print(f"  DNFs:           {state['dnfs']}")
        print(f"  hole scores:    {state['hole_scores']}")

        if state["finalizable_now"]:
            print("  finalizable:    YES")
        else:
            print("  finalizable:    NO")
            for blocker in state["blockers"]:
                print(f"    - {blocker}")

        print()
        print("Existing round-scoped rows")
        for name, count in state["scoped_counts"].items():
            print(f"  {name:<31} {count}")

        if args.skip_rollback_probe:
            print("\nRollback probe skipped. No database writes were attempted.")
            return

        print("\nRollback probe")
        print("  acquiring row lock...")
        print("  applying transient round-note update + audit sentinel...")
        rollback_probe(conn, info)
        print("  forced rollback complete")
        print("  PASS rounds row restored exactly")
        print("  PASS round-scoped counts unchanged")
        print("  PASS transient audit sentinel absent")
        print("\nPASS: transaction/rollback harness made no persistent database changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
