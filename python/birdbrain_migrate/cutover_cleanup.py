from __future__ import annotations

import argparse

from dotenv import load_dotenv

# Keep CLI behavior consistent with the bootstrap utilities: local database
# credentials live in the repository-root .env and are never committed.
load_dotenv()

from analyze_staging import analyze, connect_db  # noqa: E402


def find_past_unplayed_rounds(snapshot_id: int | None) -> tuple[int, list[tuple[int, str, str]]]:
    report = analyze(snapshot_id)
    if not report["ready_for_transform"]:
        raise RuntimeError("Staging contract has blocking errors; cleanup refused.")

    completed = [
        int(m["round_no"])
        for m in report["round_mappings"]
        if m["has_season_score_column"]
    ]
    cutoff = max(completed, default=0)
    if cutoff <= 0:
        raise RuntimeError("No completed-round cutoff could be determined.")

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.round_no, c.name, l.name
                FROM rounds r
                JOIN layouts l ON l.layout_id = r.layout_id
                JOIN courses c ON c.course_id = l.course_id
                WHERE r.status = 'scheduled'
                  AND r.round_no <= %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM round_participants rp
                    WHERE rp.round_id = r.round_id
                  )
                ORDER BY r.round_no
                """,
                (cutoff,),
            )
            rows = [(int(r[0]), str(r[1]), str(r[2])) for r in cur.fetchall()]
    finally:
        conn.close()

    return cutoff, rows


def apply_cleanup(snapshot_id: int | None) -> int:
    cutoff, rows = find_past_unplayed_rounds(snapshot_id)
    if not rows:
        return 0

    round_nos = [r[0] for r in rows]
    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE rounds
                    SET status = 'cancelled'
                    WHERE status = 'scheduled'
                      AND round_no = ANY(%s)
                      AND round_no <= %s
                      AND NOT EXISTS (
                        SELECT 1
                        FROM round_participants rp
                        WHERE rp.round_id = rounds.round_id
                      )
                    """,
                    (round_nos, cutoff),
                )
                changed = cur.rowcount
        return int(changed)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mark pre-cutover scheduled rounds with no participants as cancelled. "
            "Dry-run is the default."
        )
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cutoff, rows = find_past_unplayed_rounds(args.snapshot_id)
    print(f"Migration cutoff round: {cutoff}")
    if not rows:
        print("No pre-cutover scheduled rounds without participants were found.")
        return

    print("Rounds to mark cancelled:")
    for round_no, course, layout in rows:
        print(f"  R{round_no}: {course} - {layout}")

    if not args.apply:
        print("\nDRY RUN ONLY: no round statuses were changed.")
        return

    changed = apply_cleanup(args.snapshot_id)
    print(f"\nUpdated {changed} round(s) to status='cancelled'.")


if __name__ == "__main__":
    main()
