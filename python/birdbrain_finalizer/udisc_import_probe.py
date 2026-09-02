from __future__ import annotations

import uuid
from pathlib import Path

from db_preflight import connect_admin
from udisc import parse_udisc_xlsx
from udisc_import import import_udisc


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures/round_02/udisc_round_02_black_bear.xlsx"
PROBE_SEASON_YEAR = 2098
PROBE_ROUND_NO = 2


class ProbeRollback(RuntimeError):
    pass


def source_context(cur):
    cur.execute(
        """
        SELECT s.league_id, r.layout_id, r.scheduled_date,
               r.points_multiplier, r.payout_contribution,
               r.postseason_contribution, r.ace_contribution
        FROM rounds r
        JOIN seasons s ON s.season_id = r.season_id
        WHERE s.season_year = 2026
          AND s.closed_at IS NULL
          AND r.round_no = 2
        ORDER BY s.season_id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Could not find 2026 R2 template.")
    return row


def resolve_fixture_players(cur, parsed):
    names = {person.normalized_name for person in parsed.participants}
    cur.execute(
        "SELECT player_id, normalized_name FROM players WHERE normalized_name = ANY(%s)",
        (list(names),),
    )
    found = {row[1]: int(row[0]) for row in cur.fetchall()}
    missing = sorted(names - set(found))
    if missing:
        raise RuntimeError(
            "Fixture players missing from normalized player table: " + ", ".join(missing)
        )
    return found


def create_probe_state(cur, parsed) -> tuple[int, int, int]:
    (
        league_id,
        layout_id,
        scheduled_date,
        points_multiplier,
        payout_contribution,
        postseason_contribution,
        ace_contribution,
    ) = source_context(cur)
    probe_name = f"__birdbrain_udisc_import_probe_{uuid.uuid4().hex}"

    cur.execute(
        """
        INSERT INTO seasons (league_id, season_name, season_year)
        VALUES (%s,%s,%s)
        RETURNING season_id
        """,
        (league_id, probe_name, PROBE_SEASON_YEAR),
    )
    season_id = int(cur.fetchone()[0])

    players = resolve_fixture_players(cur, parsed)
    for player_id in players.values():
        cur.execute(
            """
            INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
            VALUES (%s,%s,TRUE)
            """,
            (season_id, player_id),
        )

    cur.execute(
        """
        INSERT INTO rounds (
          season_id, round_no, layout_id, scheduled_date,
          points_multiplier, payout_contribution,
          postseason_contribution, ace_contribution,
          status, note
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'scheduled',
                  'Persistent UDisc importer rollback probe')
        RETURNING round_id
        """,
        (
            season_id,
            PROBE_ROUND_NO,
            layout_id,
            scheduled_date,
            points_multiplier,
            payout_contribution,
            postseason_contribution,
            ace_contribution,
        ),
    )
    round_id = int(cur.fetchone()[0])

    # Model R41's migration carry-in: import must leave unrelated pre-existing
    # financial history attached to the target round untouched.
    cur.execute(
        """
        INSERT INTO financial_transactions (
          league_id, season_id, round_id, fund_type,
          transaction_type, amount, memo
        ) VALUES (%s,%s,%s,'ace_pot','adjustment',17,
                  'Synthetic pre-import ace-pot carry-in')
        RETURNING financial_transaction_id
        """,
        (league_id, season_id, round_id),
    )
    carry_id = int(cur.fetchone()[0])
    return season_id, round_id, carry_id


def verify_committed_import_inside_outer_transaction(
    cur,
    *,
    round_id: int,
    carry_id: int,
    participant_count: int,
    hole_score_count: int,
) -> None:
    cur.execute("SELECT status FROM rounds WHERE round_id = %s", (round_id,))
    status = cur.fetchone()[0]
    if status != "results_review":
        raise RuntimeError(f"Expected results_review, got {status!r}.")

    cur.execute(
        "SELECT COUNT(*) FROM round_participants WHERE round_id = %s",
        (round_id,),
    )
    if int(cur.fetchone()[0]) != participant_count:
        raise RuntimeError("Participant count mismatch after importer commit.")

    cur.execute(
        """
        SELECT COUNT(*)
        FROM hole_scores hs
        JOIN round_participants rp
          ON rp.round_participant_id = hs.round_participant_id
        WHERE rp.round_id = %s
        """,
        (round_id,),
    )
    if int(cur.fetchone()[0]) != hole_score_count:
        raise RuntimeError("Hole-score count mismatch after importer commit.")

    cur.execute(
        "SELECT COUNT(*) FROM round_udisc_import_receipts WHERE round_id = %s",
        (round_id,),
    )
    if int(cur.fetchone()[0]) != 1:
        raise RuntimeError("Expected exactly one UDisc import receipt.")

    cur.execute(
        """
        SELECT COUNT(*)
        FROM audit_events
        WHERE round_id = %s AND event_type = 'udisc_imported'
        """,
        (round_id,),
    )
    if int(cur.fetchone()[0]) != 1:
        raise RuntimeError("Expected exactly one udisc_imported audit event.")

    cur.execute(
        """
        SELECT COUNT(*), amount, memo
        FROM financial_transactions
        WHERE financial_transaction_id = %s
        GROUP BY amount, memo
        """,
        (carry_id,),
    )
    carry = cur.fetchone()
    if carry is None or int(carry[0]) != 1 or int(carry[1]) != 17:
        raise RuntimeError("Pre-existing ace-pot carry-in was changed or removed.")


def main() -> None:
    parsed = parse_udisc_xlsx(FIXTURE)
    conn = connect_admin()
    probe_season_id = None
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    probe_season_id, round_id, carry_id = create_probe_state(cur, parsed)

                first, first_mode = import_udisc(
                    conn,
                    round_no=PROBE_ROUND_NO,
                    season_year=PROBE_SEASON_YEAR,
                    xlsx_path=FIXTURE,
                    commit=True,
                )
                if first_mode != "committed":
                    raise RuntimeError(f"Expected committed first import, got {first_mode!r}.")

                with conn.cursor() as cur:
                    verify_committed_import_inside_outer_transaction(
                        cur,
                        round_id=round_id,
                        carry_id=carry_id,
                        participant_count=len(parsed.participants),
                        hole_score_count=parsed.hole_score_count,
                    )

                second, second_mode = import_udisc(
                    conn,
                    round_no=PROBE_ROUND_NO,
                    season_year=PROBE_SEASON_YEAR,
                    xlsx_path=FIXTURE,
                    commit=True,
                )
                if second_mode != "already-imported no-op":
                    raise RuntimeError(
                        f"Expected idempotent already-imported no-op, got {second_mode!r}."
                    )
                if second.data_fingerprint != first.data_fingerprint:
                    raise RuntimeError("Idempotent retry fingerprint changed unexpectedly.")

                with conn.cursor() as cur:
                    verify_committed_import_inside_outer_transaction(
                        cur,
                        round_id=round_id,
                        carry_id=carry_id,
                        participant_count=len(parsed.participants),
                        hole_score_count=parsed.hole_score_count,
                    )

                print("BirdBrain persistent UDisc importer integration probe")
                print("==================================================")
                print(f"Fixture:              {FIXTURE.name}")
                print(f"Synthetic season:     {PROBE_SEASON_YEAR}")
                print(f"Participants:         {first.participant_count}")
                print(f"Hole scores:          {first.hole_score_count}")
                print("PASS first import committed inside outer rollback transaction")
                print("PASS import receipt + audit event written exactly once")
                print("PASS pre-existing ace-pot carry-in preserved")
                print("PASS identical retry returned already-imported no-op")
                print("Forcing outer rollback of synthetic season and committed import...")
                raise ProbeRollback()
        except ProbeRollback:
            pass

        if probe_season_id is None:
            raise RuntimeError("Probe season was never created.")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM seasons WHERE season_id = %s",
                (probe_season_id,),
            )
            remaining = int(cur.fetchone()[0])
        conn.rollback()
        if remaining != 0:
            raise RuntimeError("ROLLBACK FAILURE: synthetic importer probe season persisted.")

        print("PASS outer rollback removed synthetic season/import state")
        print("\nPASS: persistent UDisc importer probe left no database changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
