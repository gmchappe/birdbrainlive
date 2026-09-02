from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from core import FinalizerValidationError
from db_preflight import connect_admin, fetch_round
from udisc import UDiscRound, parse_udisc_xlsx


IMPORTER_VERSION = "0.1.0"


class DryRunRollback(RuntimeError):
    """Internal sentinel used to execute a complete import and force rollback."""


@dataclass(frozen=True)
class ImportTarget:
    round_id: int
    season_id: int
    league_id: int
    round_no: int
    season_year: int | None
    status: str
    layout_id: int
    course: str
    layout: str
    layout_par: int
    hole_count: int


@dataclass(frozen=True)
class ImportSummary:
    round_id: int
    round_no: int
    season_year: int | None
    source_filename: str
    source_sha256: str
    data_fingerprint: str
    participant_count: int
    hole_score_count: int
    skipped_duplicate_count: int
    skipped_non_gen_count: int
    status: str


def source_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_import_payload(target: ImportTarget, parsed: UDiscRound) -> dict[str, Any]:
    return {
        "importer_version": IMPORTER_VERSION,
        "target": {
            "round_id": target.round_id,
            "season_id": target.season_id,
            "round_no": target.round_no,
            "layout_id": target.layout_id,
            "layout_par": target.layout_par,
            "hole_count": target.hole_count,
        },
        "participants": [
            {
                "name": person.normalized_name,
                "status": person.status,
                "gross_score": person.gross_score,
                "relative_score": person.relative_score,
                "hole_scores": [list(item) for item in person.hole_scores],
            }
            for person in sorted(parsed.participants, key=lambda item: item.normalized_name)
        ],
        "skipped_duplicates": parsed.skipped_duplicates,
        "skipped_non_gen": parsed.skipped_non_gen,
    }


def data_fingerprint(target: ImportTarget, parsed: UDiscRound) -> str:
    encoded = json.dumps(
        canonical_import_payload(target, parsed),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_target(
    cur: psycopg.Cursor,
    round_no: int,
    season_year: int | None,
    *,
    for_update: bool,
) -> ImportTarget:
    info = fetch_round(cur, round_no, season_year, for_update=for_update)
    cur.execute(
        """
        SELECT r.layout_id, l.par, l.hole_count
        FROM rounds r
        JOIN layouts l ON l.layout_id = r.layout_id
        WHERE r.round_id = %s
        """,
        (info.round_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Locked target round disappeared unexpectedly.")
    layout_id, layout_par, hole_count = row
    if layout_par is None:
        raise FinalizerValidationError(
            f"R{info.round_no} layout {info.course} / {info.layout} has no authoritative par."
        )
    if int(hole_count) <= 0:
        raise FinalizerValidationError("Target layout hole_count must be positive.")
    return ImportTarget(
        round_id=info.round_id,
        season_id=info.season_id,
        league_id=info.league_id,
        round_no=info.round_no,
        season_year=info.season_year,
        status=info.status,
        layout_id=int(layout_id),
        course=info.course,
        layout=info.layout,
        layout_par=int(layout_par),
        hole_count=int(hole_count),
    )


def existing_import_receipt(cur: psycopg.Cursor, round_id: int) -> tuple | None:
    cur.execute(
        """
        SELECT
          data_fingerprint,
          source_sha256,
          source_filename,
          importer_version,
          participant_count,
          hole_score_count,
          skipped_duplicate_count,
          skipped_non_gen_count
        FROM round_udisc_import_receipts
        WHERE round_id = %s
        """,
        (round_id,),
    )
    return cur.fetchone()


def current_staged_counts(cur: psycopg.Cursor, round_id: int) -> tuple[int, int, int, int]:
    cur.execute(
        "SELECT COUNT(*)::integer FROM round_participants WHERE round_id = %s",
        (round_id,),
    )
    participants = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM hole_scores hs
        JOIN round_participants rp
          ON rp.round_participant_id = hs.round_participant_id
        WHERE rp.round_id = %s
        """,
        (round_id,),
    )
    hole_scores = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT COUNT(*)::integer
        FROM round_results rr
        JOIN round_participants rp
          ON rp.round_participant_id = rr.round_participant_id
        WHERE rp.round_id = %s
        """,
        (round_id,),
    )
    results = int(cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*)::integer FROM playoff_resolutions WHERE round_id = %s",
        (round_id,),
    )
    playoffs = int(cur.fetchone()[0])
    return participants, hole_scores, results, playoffs


def resolve_members(
    cur: psycopg.Cursor,
    season_id: int,
    parsed: UDiscRound,
) -> dict[str, tuple[int, str]]:
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
    unresolved = [
        person.name
        for person in parsed.participants
        if person.normalized_name not in lookup
    ]
    if unresolved:
        preview = ", ".join(sorted(unresolved)[:10])
        suffix = "..." if len(unresolved) > 10 else ""
        raise FinalizerValidationError(
            f"{len(unresolved)} UDisc names do not resolve to current-season members: "
            f"{preview}{suffix}. The first persistent importer will not guess identities "
            "or auto-create guests; resolve them explicitly before import."
        )
    return lookup


def stage_parsed_udisc(
    cur: psycopg.Cursor,
    target: ImportTarget,
    parsed: UDiscRound,
) -> tuple[int, int]:
    members = resolve_members(cur, target.season_id, parsed)
    participant_count = 0
    score_count = 0

    for person in parsed.participants:
        player_id, canonical_name = members[person.normalized_name]
        if person.status == "active" and len(person.hole_scores) != target.hole_count:
            raise FinalizerValidationError(
                f"{person.name} has {len(person.hole_scores)} hole scores but "
                f"R{target.round_no} expects {target.hole_count}."
            )
        if person.relative_score is not None and person.gross_score is not None:
            expected_relative = int(person.gross_score) - target.layout_par
            if int(person.relative_score) != expected_relative:
                raise FinalizerValidationError(
                    f"{person.name} round_relative_score={person.relative_score} but "
                    f"gross-par={expected_relative} for {target.course} / {target.layout}."
                )

        cur.execute(
            """
            INSERT INTO round_participants (
              round_id, player_id, display_name, participant_type,
              checked_in_at, started_round, status
            ) VALUES (%s,%s,%s,'member',now(),TRUE,%s)
            RETURNING round_participant_id
            """,
            (target.round_id, player_id, canonical_name, person.status),
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
        """
        UPDATE rounds
        SET status = 'results_review'
        WHERE round_id = %s
          AND status IN ('scheduled','check_in','in_progress')
        """,
        (target.round_id,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("Target round did not transition to results_review.")
    return participant_count, score_count


def import_udisc(
    conn: psycopg.Connection,
    *,
    round_no: int,
    season_year: int | None,
    xlsx_path: str | Path,
    commit: bool,
) -> tuple[ImportSummary, str]:
    source_path = Path(xlsx_path)
    parsed = parse_udisc_xlsx(source_path)
    file_hash = source_sha256(source_path)
    summary: ImportSummary | None = None

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                target = load_target(cur, round_no, season_year, for_update=True)
                fingerprint = data_fingerprint(target, parsed)
                receipt = existing_import_receipt(cur, target.round_id)
                participants_now, scores_now, results_now, playoffs_now = current_staged_counts(
                    cur, target.round_id
                )

                if receipt is not None:
                    if receipt[0] != fingerprint:
                        raise FinalizerValidationError(
                            "This round already has a committed UDisc import receipt, but "
                            "the parsed data fingerprint differs. Existing imported scores "
                            "will not be overwritten."
                        )
                    if participants_now != int(receipt[4]) or scores_now != int(receipt[5]):
                        raise RuntimeError(
                            "Committed UDisc receipt exists, but current participant/hole-score "
                            "counts no longer match the receipt. Refusing idempotent no-op."
                        )
                    summary = ImportSummary(
                        round_id=target.round_id,
                        round_no=target.round_no,
                        season_year=target.season_year,
                        source_filename=receipt[2],
                        source_sha256=receipt[1],
                        data_fingerprint=receipt[0],
                        participant_count=int(receipt[4]),
                        hole_score_count=int(receipt[5]),
                        skipped_duplicate_count=int(receipt[6]),
                        skipped_non_gen_count=int(receipt[7]),
                        status=target.status,
                    )
                    return summary, "already-imported no-op"

                if target.status not in {"scheduled", "check_in", "in_progress"}:
                    raise FinalizerValidationError(
                        f"R{target.round_no} is {target.status!r}; a first UDisc import is "
                        "only allowed from scheduled/check_in/in_progress."
                    )
                if participants_now or scores_now or results_now or playoffs_now:
                    raise FinalizerValidationError(
                        "Target round already contains participant/score/result/playoff facts. "
                        "The persistent importer is append-once and will not overwrite or merge them."
                    )

                cur.execute(
                    "SELECT COUNT(*) FROM round_finalization_receipts WHERE round_id = %s",
                    (target.round_id,),
                )
                if int(cur.fetchone()[0]) != 0:
                    raise FinalizerValidationError(
                        "Target round already has a finalization receipt and cannot accept a first import."
                    )

                participant_count, hole_score_count = stage_parsed_udisc(cur, target, parsed)
                if participant_count != len(parsed.participants):
                    raise RuntimeError("Participant staging reconciliation failed.")
                if hole_score_count != parsed.hole_score_count:
                    raise RuntimeError("Hole-score staging reconciliation failed.")

                cur.execute(
                    """
                    INSERT INTO round_udisc_import_receipts (
                      round_id, data_fingerprint, source_sha256, source_filename,
                      importer_version, participant_count, hole_score_count,
                      skipped_duplicate_count, skipped_non_gen_count
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        target.round_id,
                        fingerprint,
                        file_hash,
                        source_path.name,
                        IMPORTER_VERSION,
                        participant_count,
                        hole_score_count,
                        parsed.skipped_duplicates,
                        parsed.skipped_non_gen,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO audit_events (
                      league_id, season_id, round_id, event_type, event_payload
                    ) VALUES (%s,%s,%s,'udisc_imported',%s::jsonb)
                    """,
                    (
                        target.league_id,
                        target.season_id,
                        target.round_id,
                        json.dumps(
                            {
                                "data_fingerprint": fingerprint,
                                "source_sha256": file_hash,
                                "source_filename": source_path.name,
                                "importer_version": IMPORTER_VERSION,
                                "participant_count": participant_count,
                                "hole_score_count": hole_score_count,
                                "skipped_duplicate_count": parsed.skipped_duplicates,
                                "skipped_non_gen_count": parsed.skipped_non_gen,
                            },
                            sort_keys=True,
                        ),
                    ),
                )

                summary = ImportSummary(
                    round_id=target.round_id,
                    round_no=target.round_no,
                    season_year=target.season_year,
                    source_filename=source_path.name,
                    source_sha256=file_hash,
                    data_fingerprint=fingerprint,
                    participant_count=participant_count,
                    hole_score_count=hole_score_count,
                    skipped_duplicate_count=parsed.skipped_duplicates,
                    skipped_non_gen_count=parsed.skipped_non_gen,
                    status="results_review",
                )
                if not commit:
                    raise DryRunRollback()
                return summary, "committed"
    except DryRunRollback:
        assert summary is not None
        return summary, "dry-run rolled back"


def print_summary(summary: ImportSummary, mode: str) -> None:
    print("BirdBrain persistent UDisc importer")
    print("===================================")
    print(f"Round:                 R{summary.round_no}")
    print(f"Season:                {summary.season_year}")
    print(f"Mode:                  {mode}")
    print(f"Source file:           {summary.source_filename}")
    print(f"Source SHA-256:        {summary.source_sha256}")
    print(f"Data fingerprint:      {summary.data_fingerprint}")
    print(f"Participants:          {summary.participant_count}")
    print(f"Hole scores:           {summary.hole_score_count}")
    print(f"Skipped DUP rows:      {summary.skipped_duplicate_count}")
    print(f"Skipped non-GEN rows:  {summary.skipped_non_gen_count}")
    print(f"Round state:           {summary.status}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Append-once UDisc XLSX importer. Defaults to a complete database dry-run "
            "that is forcibly rolled back. Import and round finalization are separate actions."
        )
    )
    parser.add_argument("--round-no", type=int, required=True)
    parser.add_argument("--season-year", type=int)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist the import. Without this flag all staged rows are rolled back.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required with --commit: IMPORT-R<round-no>",
    )
    args = parser.parse_args()

    if args.commit and args.confirm != f"IMPORT-R{args.round_no}":
        raise SystemExit(
            f"--commit requires --confirm IMPORT-R{args.round_no}. "
            "No database changes were attempted."
        )

    conn = connect_admin()
    try:
        summary, mode = import_udisc(
            conn,
            round_no=args.round_no,
            season_year=args.season_year,
            xlsx_path=args.xlsx,
            commit=args.commit,
        )
        print_summary(summary, mode)
        if mode == "dry-run rolled back":
            print()
            print("PASS: complete UDisc staging transaction executed and was rolled back.")
            print("No participants, hole scores, import receipt, audit row, or status change was committed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
