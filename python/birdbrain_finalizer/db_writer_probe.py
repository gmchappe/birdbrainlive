from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import date, timedelta

from db_preflight import connect_admin
from db_writer import finalize


class OuterProbeRollback(RuntimeError):
    pass


def score_vector(total: int, holes: int) -> list[int]:
    if holes <= 0 or total < holes * 2:
        raise ValueError(
            "Synthetic score must support at least 2 strokes per hole."
        )
    base, remainder = divmod(total, holes)
    values = [
        base + (1 if index < remainder else 0)
        for index in range(holes)
    ]
    if min(values) < 2:
        raise ValueError(
            "Synthetic integration scores must not accidentally create aces."
        )
    if sum(values) != total:
        raise AssertionError(
            "Synthetic score vector did not reconcile."
        )
    return values


def choose_players(
    cur, season_id: int, needed: int = 5
) -> list[tuple[int, str, str]]:
    cur.execute(
        """
        WITH latest AS (
          SELECT
            ppa.player_id,
            ppa.pool,
            ROW_NUMBER() OVER (
              PARTITION BY ppa.player_id
              ORDER BY ppa.effective_round_no DESC,
                       ppa.player_pool_assignment_id DESC
            ) AS rn
          FROM player_pool_assignments ppa
          WHERE ppa.season_id = %s
            AND ppa.pool IS NOT NULL
        )
        SELECT l.player_id, p.display_name, l.pool
        FROM latest l
        JOIN players p ON p.player_id = l.player_id
        WHERE l.rn = 1
        ORDER BY l.pool, l.player_id
        """,
        (season_id,),
    )
    rows = [
        (int(row[0]), row[1], row[2])
        for row in cur.fetchall()
    ]
    by_pool: OrderedDict[
        str, tuple[int, str, str]
    ] = OrderedDict()
    for row in rows:
        by_pool.setdefault(row[2], row)

    chosen = list(by_pool.values())
    used = {row[0] for row in chosen}
    for row in rows:
        if len(chosen) >= needed:
            break
        if row[0] not in used:
            chosen.append(row)
            used.add(row[0])

    if len(chosen) < needed:
        raise RuntimeError(
            f"Need at least {needed} pool-assigned players for integration "
            f"probe; found {len(chosen)}."
        )
    if len({row[2] for row in chosen[:4]}) < 2:
        raise RuntimeError(
            "Integration probe needs at least two represented SHAM pools."
        )
    return chosen[:needed]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a synthetic results-review round inside an outer "
            "transaction, exercise the full finalizer dry-run, verify inner "
            "rollback, then roll back the synthetic setup. No probe data is "
            "committed."
        )
    )
    parser.add_argument("--season-year", type=int, default=2026)
    parser.add_argument(
        "--template-round-no",
        type=int,
        default=41,
        help=(
            "Use this scheduled round's layout and financial configuration."
        ),
    )
    parser.add_argument(
        "--synthetic-round-no",
        type=int,
        default=9999,
        help=(
            "Temporary round number used only inside the rollback probe."
        ),
    )
    args = parser.parse_args()

    conn = connect_admin()
    synthetic_round_id: int | None = None
    try:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                          r.season_id,
                          s.league_id,
                          r.layout_id,
                          l.par,
                          l.hole_count,
                          r.points_multiplier,
                          r.payout_contribution,
                          r.postseason_contribution,
                          r.ace_contribution
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        JOIN layouts l ON l.layout_id = r.layout_id
                        WHERE r.round_no = %s
                          AND s.season_year = %s
                          AND s.closed_at IS NULL
                        """,
                        (args.template_round_no, args.season_year),
                    )
                    template = cur.fetchone()
                    if template is None:
                        raise RuntimeError(
                            "Could not find template round."
                        )
                    (
                        season_id,
                        league_id,
                        layout_id,
                        layout_par,
                        hole_count,
                        points_multiplier,
                        payout_contribution,
                        postseason_contribution,
                        ace_contribution,
                    ) = template
                    if layout_par is None:
                        raise RuntimeError(
                            "Template layout has no par."
                        )

                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM rounds
                        WHERE season_id = %s AND round_no = %s
                        """,
                        (season_id, args.synthetic_round_no),
                    )
                    if int(cur.fetchone()[0]) != 0:
                        raise RuntimeError(
                            f"Synthetic R{args.synthetic_round_no} already exists."
                        )

                    players = choose_players(
                        cur, int(season_id), needed=5
                    )
                    cur.execute(
                        """
                        SELECT MAX(scheduled_date)
                        FROM rounds
                        WHERE season_id = %s
                        """,
                        (season_id,),
                    )
                    max_date = (
                        cur.fetchone()[0]
                        or date(args.season_year, 1, 1)
                    )
                    synthetic_date = max_date + timedelta(days=7)

                    cur.execute(
                        """
                        INSERT INTO rounds (
                          season_id,
                          round_no,
                          layout_id,
                          scheduled_date,
                          points_multiplier,
                          payout_contribution,
                          postseason_contribution,
                          ace_contribution,
                          status,
                          note
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,%s,
                          'results_review',
                          'ROLLBACK-ONLY transactional finalizer integration probe'
                        )
                        RETURNING round_id
                        """,
                        (
                            season_id,
                            args.synthetic_round_no,
                            layout_id,
                            synthetic_date,
                            points_multiplier,
                            payout_contribution,
                            postseason_contribution,
                            ace_contribution,
                        ),
                    )
                    synthetic_round_id = int(cur.fetchone()[0])

                    active_players = players[:4]
                    dnf_player = players[4]
                    gross_targets = [
                        int(layout_par) - 4,
                        int(layout_par) + 8,
                        int(layout_par) + 20,
                        int(layout_par) + 32,
                    ]

                    for (
                        player_id,
                        display_name,
                        _pool,
                    ), gross in zip(active_players, gross_targets):
                        cur.execute(
                            """
                            INSERT INTO round_participants (
                              round_id,
                              player_id,
                              display_name,
                              participant_type,
                              started_round,
                              status
                            ) VALUES (
                              %s,%s,%s,'member',TRUE,'active'
                            )
                            RETURNING round_participant_id
                            """,
                            (
                                synthetic_round_id,
                                player_id,
                                display_name,
                            ),
                        )
                        round_participant_id = int(
                            cur.fetchone()[0]
                        )
                        for hole_number, strokes in enumerate(
                            score_vector(gross, int(hole_count)),
                            start=1,
                        ):
                            cur.execute(
                                """
                                INSERT INTO hole_scores (
                                  round_participant_id,
                                  hole_number,
                                  strokes,
                                  source
                                ) VALUES (%s,%s,%s,'admin')
                                """,
                                (
                                    round_participant_id,
                                    hole_number,
                                    strokes,
                                ),
                            )

                    player_id, display_name, _pool = dnf_player
                    cur.execute(
                        """
                        INSERT INTO round_participants (
                          round_id,
                          player_id,
                          display_name,
                          participant_type,
                          started_round,
                          status
                        ) VALUES (
                          %s,%s,%s,'member',TRUE,'dnf'
                        )
                        RETURNING round_participant_id
                        """,
                        (
                            synthetic_round_id,
                            player_id,
                            display_name,
                        ),
                    )
                    dnf_participant_id = int(cur.fetchone()[0])
                    for hole_number in range(
                        1, min(6, int(hole_count) + 1)
                    ):
                        cur.execute(
                            """
                            INSERT INTO hole_scores (
                              round_participant_id,
                              hole_number,
                              strokes,
                              source
                            ) VALUES (%s,%s,3,'admin')
                            """,
                            (
                                dnf_participant_id,
                                hole_number,
                            ),
                        )

                summary, mode = finalize(
                    conn,
                    args.synthetic_round_no,
                    args.season_year,
                    {},
                    commit=False,
                )
                if (
                    summary is None
                    or mode != "dry-run rolled back"
                ):
                    raise RuntimeError(
                        "Expected inner dry-run rollback; "
                        f"got mode={mode!r}."
                    )

                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM rounds WHERE round_id = %s",
                        (synthetic_round_id,),
                    )
                    status = cur.fetchone()[0]
                    if status != "results_review":
                        raise RuntimeError(
                            "Inner dry-run failed to restore synthetic "
                            "round status."
                        )

                    checks = {
                        "round_results": """
                            SELECT COUNT(*)
                            FROM round_results rr
                            JOIN round_participants rp
                              ON rp.round_participant_id = rr.round_participant_id
                            WHERE rp.round_id = %s
                        """,
                        "handicap_adjustments": """
                            SELECT COUNT(*)
                            FROM handicap_adjustments
                            WHERE round_id = %s
                        """,
                        "finalizer_financial_rows": """
                            SELECT COUNT(*)
                            FROM financial_transactions
                            WHERE round_id = %s
                              AND finalization_key IS NOT NULL
                        """,
                        "ace_awards": """
                            SELECT COUNT(*)
                            FROM ace_awards
                            WHERE round_id = %s
                        """,
                        "course_records": """
                            SELECT COUNT(*)
                            FROM course_records
                            WHERE round_id = %s
                        """,
                        "sham_pool_round_stats": """
                            SELECT COUNT(*)
                            FROM sham_pool_round_stats
                            WHERE round_id = %s
                        """,
                        "finalization_receipt": """
                            SELECT COUNT(*)
                            FROM round_finalization_receipts
                            WHERE round_id = %s
                        """,
                        "finalization_audit": """
                            SELECT COUNT(*)
                            FROM audit_events
                            WHERE round_id = %s
                              AND event_type = 'round_finalized'
                        """,
                    }
                    for label, query in checks.items():
                        cur.execute(query, (synthetic_round_id,))
                        count = int(cur.fetchone()[0])
                        if count != 0:
                            raise RuntimeError(
                                "Inner rollback failure: "
                                f"{label} count={count}."
                            )

                print(
                    "BirdBrain transactional writer integration probe"
                )
                print(
                    "================================================"
                )
                print(
                    f"Synthetic round: R{args.synthetic_round_no}"
                )
                print(f"Template:        R{args.template_round_no}")
                print(f"Eligible field:  {summary.field_size}")
                print(f"Purse:           ${summary.purse}")
                print(
                    "Result rows exercised: "
                    f"{summary.result_count}"
                )
                print(
                    "Financial rows exercised: "
                    f"{summary.financial_transaction_count}"
                )
                print(
                    "Handicap adjustments exercised: "
                    f"{summary.handicap_adjustment_count}"
                )
                print(
                    "PASS inner transaction: full finalizer executed "
                    "and rolled back"
                )
                print(
                    "PASS synthetic state: no finalizer-generated "
                    "rows remained"
                )
                print(
                    "Forcing outer rollback of synthetic "
                    "participants/scores/round..."
                )
                raise OuterProbeRollback()
        except OuterProbeRollback:
            pass

        if synthetic_round_id is None:
            raise RuntimeError(
                "Synthetic round was never created."
            )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM rounds WHERE round_id = %s",
                (synthetic_round_id,),
            )
            remaining = int(cur.fetchone()[0])
        conn.rollback()
        if remaining != 0:
            raise RuntimeError(
                "OUTER ROLLBACK FAILURE: synthetic round persisted."
            )

        print(
            "PASS outer transaction: synthetic round and scores are absent"
        )
        print()
        print(
            "PASS: full transactional writer integration probe left "
            "no persistent changes."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
