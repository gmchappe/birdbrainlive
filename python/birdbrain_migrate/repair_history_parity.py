from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

load_dotenv()

from analyze_staging import connect_db, nonblank, normalized_name  # noqa: E402


def parse_year(value) -> int | None:
    text = nonblank(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid Hall of Champions year: {value!r}") from exc
    if number != number.to_integral_value():
        raise ValueError(f"Invalid Hall of Champions year: {value!r}")
    return int(number)


def latest_snapshot(cur) -> int:
    cur.execute("SELECT MAX(snapshot_id) FROM migration_staging.snapshots")
    value = cur.fetchone()[0]
    if value is None:
        raise RuntimeError("No migration staging snapshot is loaded.")
    return int(value)


def staged_hall_rows(cur, snapshot_id: int) -> list[tuple[int, dict[str, str]]]:
    """Reconstruct Hall rows from the staging array + recorded header contract."""
    cur.execute(
        """
        SELECT headers
        FROM migration_staging.snapshot_sheets
        WHERE snapshot_id = %s
          AND sheet_name = 'Hall of Champions'
        """,
        (snapshot_id,),
    )
    header_row = cur.fetchone()
    if not header_row:
        raise RuntimeError(
            f"Snapshot {snapshot_id} has no staged 'Hall of Champions' sheet metadata."
        )
    headers = [str(value) for value in list(header_row[0])]

    cur.execute(
        """
        SELECT row_number, row_data
        FROM migration_staging.google_sheet_rows
        WHERE snapshot_id = %s
          AND sheet_name = 'Hall of Champions'
        ORDER BY row_number
        """,
        (snapshot_id,),
    )

    reconstructed: list[tuple[int, dict[str, str]]] = []
    for row_number, raw_row in cur.fetchall():
        values = list(raw_row)
        values += [""] * max(0, len(headers) - len(values))
        data = {
            header: nonblank(values[i]) if i < len(values) else ""
            for i, header in enumerate(headers)
        }
        reconstructed.append((int(row_number), data))
    return reconstructed


def hall_repair_plan(snapshot_id: int | None):
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            if snapshot_id is None:
                snapshot_id = latest_snapshot(cur)

            cur.execute("SELECT player_id, normalized_name FROM players")
            player_ids = {str(name): int(pid) for pid, name in cur.fetchall()}

            cur.execute(
                "SELECT season_id, season_year FROM seasons WHERE season_year IS NOT NULL"
            )
            seasons_by_year: dict[int, list[int]] = {}
            for season_id, season_year in cur.fetchall():
                seasons_by_year.setdefault(int(season_year), []).append(int(season_id))

            staged = staged_hall_rows(cur, int(snapshot_id))

            source: list[dict] = []
            incomplete: list[dict] = []
            for row_number, data in staged:
                event = nonblank(data.get("Event"))
                year = parse_year(data.get("Year"))
                name = nonblank(data.get("Name"))
                division = nonblank(data.get("Division"))
                score = nonblank(data.get("Score"))

                if not event or year is None or not name:
                    missing_fields = [
                        field
                        for field, value in (("Event", event), ("Year", year), ("Name", name))
                        if value in (None, "")
                    ]
                    incomplete.append(
                        {
                            "row_number": int(row_number),
                            "event": event,
                            "year": year,
                            "division": division,
                            "name": name,
                            "score": score,
                            "missing_fields": missing_fields,
                            "reason": (
                                "missing_name_unconfirmed_historical_finisher"
                                if missing_fields == ["Name"]
                                else "incomplete_historical_champion_record"
                            ),
                        }
                    )
                    continue

                pid = player_ids.get(normalized_name(name))
                if pid is None:
                    raise RuntimeError(
                        f"Hall source row {row_number} references unknown player {name!r}."
                    )
                season_candidates = seasons_by_year.get(year, [])
                if len(season_candidates) != 1:
                    raise RuntimeError(
                        f"Hall source row {row_number} year {year} maps to "
                        f"{len(season_candidates)} normalized seasons; repair will not guess."
                    )
                season_id = season_candidates[0]
                key = (season_id, pid, event, division, score)
                source.append(
                    {
                        "row_number": int(row_number),
                        "season_id": season_id,
                        "player_id": pid,
                        "event": event,
                        "year": year,
                        "name": name,
                        "division": division,
                        "score": score,
                        "key": key,
                    }
                )

            cur.execute(
                """
                SELECT
                  hc.season_id,
                  hc.player_id,
                  COALESCE(hc.event_name, hc.title),
                  COALESCE(hc.division, ''),
                  COALESCE(hc.score_text, '')
                FROM hall_of_champions hc
                """
            )
            db_counter = Counter(
                (int(sid), int(pid), str(event), str(division), str(score))
                for sid, pid, event, division, score in cur.fetchall()
            )

            remaining = db_counter.copy()
            missing: list[dict] = []
            for item in source:
                key = item["key"]
                if remaining[key] > 0:
                    remaining[key] -= 1
                    if remaining[key] == 0:
                        del remaining[key]
                else:
                    missing.append(item)

            extras = list(remaining.elements())
            return int(snapshot_id), source, incomplete, missing, extras
    finally:
        conn.close()


def apply_repair(missing: list[dict]) -> int:
    if not missing:
        return 0
    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                for item in missing:
                    cur.execute(
                        """
                        INSERT INTO hall_of_champions
                          (season_id, player_id, title, event_name, event_year,
                           division, score_text)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            item["season_id"],
                            item["player_id"],
                            item["event"],
                            item["event"],
                            item["year"],
                            item["division"] or None,
                            item["score"] or None,
                        ),
                    )
        return len(missing)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check complete Hall of Champions history against the normalized database. "
            "Incomplete source placeholders are documented but are not fabricated."
        )
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    snapshot_id, source, incomplete, missing, extras = hall_repair_plan(args.snapshot_id)
    print(f"History repair check for snapshot_id={snapshot_id}")
    print(f"Complete Hall source rows: {len(source)}")
    print(f"Flagged incomplete Hall rows: {len(incomplete)}")
    print(f"Missing complete Hall rows: {len(missing)}")
    print(f"Unexpected database Hall rows: {len(extras)}")

    if incomplete:
        print("\nFlagged historical gaps (preserved in staging; not inserted):")
        for item in incomplete:
            print(
                f"  source row {item['row_number']}: {item['year'] or ''} | "
                f"{item['event']} | {item['division']} | "
                f"Name={item['name'] or '[UNCONFIRMED]'} | {item['score']} | "
                f"{item['reason']}"
            )

    if extras:
        print("\nRepair refused: database contains complete Hall rows not present in the staged source.")
        for key in extras[:20]:
            print(f"  extra={key}")
        raise SystemExit(2)

    if missing:
        print("\nComplete Hall rows to restore:")
        for item in missing:
            print(
                f"  source row {item['row_number']}: {item['year']} | "
                f"{item['event']} | {item['division']} | {item['name']} | {item['score']}"
            )
    else:
        print("\nAll complete Hall records already match the staged source.")
        return

    if not args.apply:
        print("\nDRY RUN ONLY: no Hall rows were inserted.")
        return

    inserted = apply_repair(missing)
    print(f"\nInserted {inserted} missing complete Hall row(s).")


if __name__ == "__main__":
    main()
