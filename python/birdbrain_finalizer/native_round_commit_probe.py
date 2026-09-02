from __future__ import annotations

import uuid

from db_preflight import connect_admin
from db_writer import finalize
from round2_e2e_probe import (
    PROBE_SEASON_YEAR,
    ROUND1_FIXTURE,
    ROUND2_FIXTURE,
    add_round2_only_memberships,
    create_probe_season,
    create_round,
    current_league_and_templates,
    insert_round2_playoff_resolutions,
    resolve_players,
    seed_postseason_opening_balance,
    seed_round1_baseline,
    verify_round2,
)
from round2_e2e_probe_isolated import clone_round2_layout, with_layout
from udisc import parse_udisc_xlsx
from udisc_import import import_udisc


class ProbeRollback(RuntimeError):
    pass


def main() -> None:
    round1 = parse_udisc_xlsx(ROUND1_FIXTURE)
    round2 = parse_udisc_xlsx(ROUND2_FIXTURE)
    all_names = {p.name for p in round1.participants} | {
        p.name for p in round2.participants
    }
    token = uuid.uuid4().hex
    probe_name = f"__birdbrain_native_round_commit_probe_{token}"
    probe_course = f"__birdbrain_native_round_commit_course_{token}"

    conn = connect_admin()
    season_id: int | None = None
    round2_id: int | None = None
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    league_id, templates = current_league_and_templates(cur)
                    players = resolve_players(cur, all_names)
                    season_id = create_probe_season(cur, league_id, probe_name)

                    isolated_layout_id = clone_round2_layout(
                        cur, int(templates[2][0]), token
                    )
                    # Give this probe's cloned course a distinct searchable name.
                    cur.execute(
                        """
                        UPDATE courses c
                        SET name = %s
                        FROM layouts l
                        WHERE l.course_id = c.course_id
                          AND l.layout_id = %s
                        """,
                        (probe_course, isolated_layout_id),
                    )
                    isolated_r2_template = with_layout(
                        templates[2], isolated_layout_id
                    )

                    round1_id = create_round(
                        cur, season_id, 1, templates[1], "finalized"
                    )
                    round2_id = create_round(
                        cur, season_id, 2, isolated_r2_template, "scheduled"
                    )

                    seed_round1_baseline(
                        cur, season_id, round1_id, round1, players
                    )
                    add_round2_only_memberships(
                        cur, season_id, round2, players
                    )
                    seed_postseason_opening_balance(
                        cur, league_id, season_id, round1_id
                    )

                imported, import_mode = import_udisc(
                    conn,
                    round_no=2,
                    season_year=PROBE_SEASON_YEAR,
                    xlsx_path=ROUND2_FIXTURE,
                    commit=True,
                )
                if import_mode != "committed":
                    raise RuntimeError(
                        f"Expected committed UDisc import, got {import_mode!r}."
                    )
                if imported.participant_count != 35 or imported.hole_score_count != 630:
                    raise RuntimeError(
                        "Round 2 UDisc import counts did not match fixture expectations."
                    )

                with conn.cursor() as cur:
                    insert_round2_playoff_resolutions(cur, round2_id)

                first, first_mode = finalize(
                    conn,
                    round_no=2,
                    season_year=PROBE_SEASON_YEAR,
                    ace_allocations={},
                    commit=True,
                )
                if first is None or first_mode != "committed":
                    raise RuntimeError(
                        f"Expected committed finalization, got mode={first_mode!r}."
                    )

                with conn.cursor() as cur:
                    verify_round2(cur, season_id, round2_id, first)
                    cur.execute(
                        "SELECT COUNT(*) FROM round_udisc_import_receipts WHERE round_id=%s",
                        (round2_id,),
                    )
                    if int(cur.fetchone()[0]) != 1:
                        raise RuntimeError("Expected exactly one UDisc import receipt.")
                    cur.execute(
                        "SELECT COUNT(*) FROM round_finalization_receipts WHERE round_id=%s",
                        (round2_id,),
                    )
                    if int(cur.fetchone()[0]) != 1:
                        raise RuntimeError("Expected exactly one finalization receipt.")
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM audit_events
                        WHERE round_id=%s
                          AND event_type IN ('udisc_imported','round_finalized')
                        """,
                        (round2_id,),
                    )
                    if int(cur.fetchone()[0]) != 2:
                        raise RuntimeError(
                            "Expected one import and one finalization audit event."
                        )

                second, second_mode = finalize(
                    conn,
                    round_no=2,
                    season_year=PROBE_SEASON_YEAR,
                    ace_allocations={},
                    commit=True,
                )
                if second is None or second_mode != "already-finalized no-op":
                    raise RuntimeError(
                        "Expected identical finalizer retry to return "
                        f"already-finalized no-op; got {second_mode!r}."
                    )
                if second.fingerprint != first.fingerprint:
                    raise RuntimeError(
                        "Idempotent finalizer retry fingerprint changed unexpectedly."
                    )

                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM round_finalization_receipts WHERE round_id=%s",
                        (round2_id,),
                    )
                    if int(cur.fetchone()[0]) != 1:
                        raise RuntimeError(
                            "Idempotent finalizer retry duplicated the receipt."
                        )
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM audit_events
                        WHERE round_id=%s AND event_type='round_finalized'
                        """,
                        (round2_id,),
                    )
                    if int(cur.fetchone()[0]) != 1:
                        raise RuntimeError(
                            "Idempotent finalizer retry duplicated the audit event."
                        )

                print("BirdBrain native round persistent-path rehearsal")
                print("==============================================")
                print(f"Fixture:                 {ROUND2_FIXTURE.name}")
                print(f"Synthetic season:        {PROBE_SEASON_YEAR}")
                print(f"Imported participants:   {imported.participant_count}")
                print(f"Imported hole scores:    {imported.hole_score_count}")
                print(f"Finalizer result rows:   {first.result_count}")
                print(f"Finalizer finance rows:  {first.financial_transaction_count}")
                print(f"Handicap adjustments:    {first.handicap_adjustment_count}")
                print("PASS persistent import committed inside outer rollback")
                print("PASS persistent finalization committed inside outer rollback")
                print("PASS Round 2 payouts / balances / records match fixture")
                print("PASS import + finalization receipts/audits written exactly once")
                print("PASS identical finalizer retry returned already-finalized no-op")
                print("Forcing outer rollback of the complete synthetic native round...")
                raise ProbeRollback()
        except ProbeRollback:
            pass

        if season_id is None or round2_id is None:
            raise RuntimeError("Synthetic native-round state was never created.")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM seasons WHERE season_id=%s", (season_id,))
            remaining_season = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM rounds WHERE round_id=%s", (round2_id,))
            remaining_round = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM courses WHERE name=%s", (probe_course,))
            remaining_course = int(cur.fetchone()[0])
        conn.rollback()

        if remaining_season or remaining_round or remaining_course:
            raise RuntimeError(
                "OUTER ROLLBACK FAILURE: synthetic persistent-path state remained: "
                f"season={remaining_season}, round={remaining_round}, "
                f"course={remaining_course}."
            )

        print("PASS outer rollback removed synthetic season/layout/import/finalization")
        print()
        print("PASS: native round persistent-path rehearsal left no database changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
