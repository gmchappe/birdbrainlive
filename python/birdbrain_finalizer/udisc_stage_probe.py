from __future__ import annotations

import argparse
from pathlib import Path

from core import FinalizerValidationError
from db_preflight import connect_admin
from udisc import UDiscRound, parse_udisc_xlsx


REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_ROUND_NO = 9998


class ProbeRollback(RuntimeError):
    pass


def resolve_members(cur, season_id: int, parsed: UDiscRound) -> dict[str, tuple[int, str]]:
    cur.execute(
        """
        SELECT p.normalized_name, p.player_id, p.display_name
        FROM season_memberships sm
        JOIN players p ON p.player_id = sm.player_id
        WHERE sm.season_id = %s
        """,
        (season_id,),
    )
    lookup = {row[0]: (int(row[1]), row[2]) for row in cur.fetchall()}
    unresolved = [p.name for p in parsed.participants if p.normalized_name not in lookup]
    if unresolved:
        preview = ", ".join(sorted(unresolved)[:10])
        suffix = "..." if len(unresolved) > 10 else ""
        raise FinalizerValidationError(
            f"{len(unresolved)} UDisc names do not resolve to current-season members: "
            f"{preview}{suffix}. Unknown identities must be resolved explicitly; "
            "the importer will not guess or auto-create guests."
        )
    return lookup


def create_synthetic_round(cur, season_year: int, template_round_no: int) -> tuple[int, int, int]:
    cur.execute(
        """
        SELECT
          r.season_id,
          r.layout_id,
          r.scheduled_date,
          r.points_multiplier,
          r.payout_contribution,
          r.postseason_contribution,
          r.ace_contribution
        FROM rounds r
        JOIN seasons s ON s.season_id = r.season_id
        WHERE s.season_year = %s
          AND s.closed_at IS NULL
          AND r.round_no = %s
        """,
        (season_year, template_round_no),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"Could not find template R{template_round_no} in open season {season_year}."
        )
    season_id, layout_id, scheduled_date, points_multiplier, payout_contribution, postseason_contribution, ace_contribution = row

    cur.execute(
        "SELECT 1 FROM rounds WHERE season_id = %s AND round_no = %s",
        (season_id, SYNTHETIC_ROUND_NO),
    )
    if cur.fetchone() is not None:
        raise RuntimeError(f"Synthetic R{SYNTHETIC_ROUND_NO} already exists unexpectedly.")

    cur.execute(
        """
        INSERT INTO rounds (
          season_id, round_no, layout_id, scheduled_date,
          points_multiplier, payout_contribution,
          postseason_contribution, ace_contribution,
          status, note
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'in_progress','UDisc staging rollback probe')
        RETURNING round_id
        """,
        (
            season_id,
            SYNTHETIC_ROUND_NO,
            layout_id,
            scheduled_date,
            points_multiplier,
            payout_contribution,
            postseason_contribution,
            ace_contribution,
        ),
    )
    return int(cur.fetchone()[0]), int(season_id), int(layout_id)


def stage_udisc(
    cur,
    *,
    round_id: int,
    season_id: int,
    layout_id: int,
    parsed: UDiscRound,
) -> tuple[int, int]:
    members = resolve_members(cur, season_id, parsed)

    cur.execute("SELECT hole_count, par FROM layouts WHERE layout_id = %s", (layout_id,))
    layout_row = cur.fetchone()
    if layout_row is None:
        raise RuntimeError("Synthetic layout disappeared unexpectedly.")
    hole_count, layout_par = int(layout_row[0]), layout_row[1]
    if layout_par is None:
        raise FinalizerValidationError("Target layout has no authoritative par.")

    participant_count = 0
    score_count = 0
    for person in parsed.participants:
        player_id, canonical_name = members[person.normalized_name]
        if person.status == "active" and len(person.hole_scores) != hole_count:
            raise FinalizerValidationError(
                f"{person.name} has {len(person.hole_scores)} hole scores but target layout expects {hole_count}."
            )
        if person.relative_score is not None and person.gross_score is not None:
            expected_relative = int(person.gross_score) - int(layout_par)
            if int(person.relative_score) != expected_relative:
                raise FinalizerValidationError(
                    f"{person.name} round_relative_score={person.relative_score} but "
                    f"gross-par={expected_relative} for the selected layout."
                )

        cur.execute(
            """
            INSERT INTO round_participants (
              round_id, player_id, display_name, participant_type,
              checked_in_at, started_round, status
            ) VALUES (%s,%s,%s,'member',now(),TRUE,%s)
            RETURNING round_participant_id
            """,
            (round_id, player_id, canonical_name, person.status),
        )
        round_participant_id = int(cur.fetchone()[0])
        participant_count += 1

        for hole_number, strokes in person.hole_scores:
            cur.execute(
                """
                INSERT INTO hole_scores (
                  round_participant_id, hole_number, strokes, source
                ) VALUES (%s,%s,%s,'udisc')
                """,
                (round_participant_id, hole_number, strokes),
            )
            score_count += 1

    cur.execute(
        "UPDATE rounds SET status = 'results_review' WHERE round_id = %s AND status = 'in_progress'",
        (round_id,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("Synthetic round did not transition in_progress -> results_review.")
    return participant_count, score_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exercise UDisc XLSX parsing and DB staging inside a forced rollback."
    )
    parser.add_argument("--season-year", type=int, default=2026)
    parser.add_argument("--template-round-no", type=int, default=2)
    parser.add_argument(
        "--fixture",
        default=str(REPO_ROOT / "fixtures/round_02/udisc_round_02_black_bear.xlsx"),
    )
    args = parser.parse_args()

    parsed = parse_udisc_xlsx(args.fixture)
    conn = connect_admin()
    synthetic_round_id = None
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    synthetic_round_id, season_id, layout_id = create_synthetic_round(
                        cur, args.season_year, args.template_round_no
                    )
                    participant_count, score_count = stage_udisc(
                        cur,
                        round_id=synthetic_round_id,
                        season_id=season_id,
                        layout_id=layout_id,
                        parsed=parsed,
                    )
                    cur.execute(
                        "SELECT status FROM rounds WHERE round_id = %s",
                        (synthetic_round_id,),
                    )
                    status = cur.fetchone()[0]
                    cur.execute(
                        "SELECT COUNT(*) FROM round_participants WHERE round_id = %s",
                        (synthetic_round_id,),
                    )
                    db_participants = int(cur.fetchone()[0])
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM hole_scores hs
                        JOIN round_participants rp
                          ON rp.round_participant_id = hs.round_participant_id
                        WHERE rp.round_id = %s
                        """,
                        (synthetic_round_id,),
                    )
                    db_scores = int(cur.fetchone()[0])

                    if status != "results_review":
                        raise RuntimeError(f"Expected results_review, got {status!r}.")
                    if db_participants != participant_count:
                        raise RuntimeError("Participant staging count mismatch.")
                    if db_scores != score_count:
                        raise RuntimeError("Hole-score staging count mismatch.")

                    print("BirdBrain UDisc staging integration probe")
                    print("========================================")
                    print(f"Fixture:          {Path(args.fixture).name}")
                    print(f"Synthetic round:  R{SYNTHETIC_ROUND_NO}")
                    print(f"Template layout:  R{args.template_round_no}")
                    print(f"Parsed players:   {len(parsed.participants)}")
                    print(f"Parsed hole rows: {parsed.hole_score_count}")
                    print(f"Skipped DUP rows: {parsed.skipped_duplicates}")
                    print(f"Skipped non-GEN:  {parsed.skipped_non_gen}")
                    print(f"DB participants:  {db_participants}")
                    print(f"DB hole scores:   {db_scores}")
                    print("PASS staged round transitioned to results_review")
                    print("Forcing rollback of synthetic UDisc staging...")
                    raise ProbeRollback()
        except ProbeRollback:
            pass

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM rounds r
                JOIN seasons s ON s.season_id = r.season_id
                WHERE s.season_year = %s AND r.round_no = %s
                """,
                (args.season_year, SYNTHETIC_ROUND_NO),
            )
            remaining = int(cur.fetchone()[0])
        conn.rollback()
        if remaining != 0:
            raise RuntimeError("ROLLBACK FAILURE: synthetic UDisc round persisted.")

        print("PASS synthetic round, participants, and hole scores are absent")
        print("\nPASS: UDisc staging integration probe left no persistent changes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
