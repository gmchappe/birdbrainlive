from __future__ import annotations

import uuid

from db_preflight import connect_admin
from db_writer import apply_finalization, input_fingerprint, load_participants, load_round_context
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
from udisc import parse_udisc_xlsx
from udisc_stage_probe import stage_udisc


class ProbeRollback(RuntimeError):
    pass


def clone_round2_layout(cur, source_layout_id: int, token: str) -> int:
    """Clone the R2 layout into a temporary course so later real records cannot contaminate the rehearsal."""
    cur.execute(
        """
        SELECT l.name, l.par, l.hole_count
        FROM layouts l
        WHERE l.layout_id = %s
        """,
        (source_layout_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Could not load the Round 2 template layout.")
    layout_name, par, hole_count = row
    if par is None:
        raise RuntimeError("Round 2 template layout has no authoritative par.")

    cur.execute(
        "INSERT INTO courses (name) VALUES (%s) RETURNING course_id",
        (f"__birdbrain_round2_record_probe_{token}",),
    )
    course_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO layouts (course_id, name, par, hole_count)
        VALUES (%s,%s,%s,%s)
        RETURNING layout_id
        """,
        (course_id, layout_name, int(par), int(hole_count)),
    )
    return int(cur.fetchone()[0])


def with_layout(template, layout_id: int):
    return (layout_id, *template[1:])


def main() -> None:
    round1 = parse_udisc_xlsx(ROUND1_FIXTURE)
    round2 = parse_udisc_xlsx(ROUND2_FIXTURE)
    all_names = {p.name for p in round1.participants} | {p.name for p in round2.participants}
    token = uuid.uuid4().hex
    probe_name = f"__birdbrain_round2_e2e_isolated_{token}"

    conn = connect_admin()
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    league_id, templates = current_league_and_templates(cur)
                    players = resolve_players(cur, all_names)
                    season_id = create_probe_season(cur, league_id, probe_name)

                    isolated_r2_layout_id = clone_round2_layout(cur, int(templates[2][0]), token)
                    isolated_r2_template = with_layout(templates[2], isolated_r2_layout_id)

                    round1_id = create_round(cur, season_id, 1, templates[1], "finalized")
                    round2_id = create_round(cur, season_id, 2, isolated_r2_template, "in_progress")

                    seed_round1_baseline(cur, season_id, round1_id, round1, players)
                    add_round2_only_memberships(cur, season_id, round2, players)
                    seed_postseason_opening_balance(cur, league_id, season_id, round1_id)

                    staged_participants, staged_scores = stage_udisc(
                        cur,
                        round_id=round2_id,
                        season_id=season_id,
                        layout_id=isolated_r2_layout_id,
                        parsed=round2,
                    )
                    insert_round2_playoff_resolutions(cur, round2_id)

                    context = load_round_context(cur, 2, PROBE_SEASON_YEAR, for_update=True)
                    participants = load_participants(cur, context)
                    first_timers = sum(
                        1 for p in participants
                        if p.input.is_field_eligible and p.prior_current_season_rounds == 0
                    )
                    returning = sum(
                        1 for p in participants
                        if p.input.is_field_eligible and p.prior_current_season_rounds > 0
                    )
                    if first_timers != 15 or returning != 20:
                        raise RuntimeError(
                            f"Expected 20 returning + 15 first-time players; got "
                            f"{returning} returning + {first_timers} first-time."
                        )

                    fingerprint = input_fingerprint(context, participants, {})
                    summary = apply_finalization(cur, context, participants, {}, fingerprint)

                    print("BirdBrain Round 2 end-to-end rehearsal (isolated records)")
                    print("========================================================")
                    print(f"Round 1 fixture players: {len(round1.participants)}")
                    print(f"Round 2 fixture players: {len(round2.participants)}")
                    print(f"Returning players:       {returning}")
                    print(f"First-time players:      {first_timers}")
                    print(f"Staged participants:     {staged_participants}")
                    print(f"Staged hole scores:      {staged_scores}")
                    print(f"Finalizer result rows:   {summary.result_count}")
                    print(f"Finalizer finance rows:  {summary.financial_transaction_count}")
                    print(f"Handicap adjustments:    {summary.handicap_adjustment_count}")

                    verify_round2(cur, season_id, round2_id, summary)
                    print("PASS full UDisc -> results_review -> finalizer path matches Round 2 fixture expectations")
                    print("Forcing rollback of temporary season, layout, staged scores, and finalization...")
                    raise ProbeRollback()
        except ProbeRollback:
            pass

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM seasons WHERE season_name = %s", (probe_name,))
            remaining_seasons = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM courses WHERE name = %s", (f"__birdbrain_round2_record_probe_{token}",))
            remaining_courses = int(cur.fetchone()[0])
        conn.rollback()
        if remaining_seasons or remaining_courses:
            raise RuntimeError(
                "ROLLBACK FAILURE: temporary Round 2 rehearsal state persisted. "
                f"seasons={remaining_seasons}, courses={remaining_courses}"
            )

        print("PASS temporary season/layout and all rehearsal rows are absent")
        print("\nPASS: isolated Round 2 end-to-end rehearsal left no persistent database changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
