from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal

from core import FinalizerValidationError
from db_preflight import connect_admin
from db_writer import finalize, load_participants, load_round_context, print_summary
from udisc import normalized_name
from udisc_import import import_udisc


class PreviewRollback(RuntimeError):
    """Internal sentinel used to force rollback of temporary preview state."""


@dataclass(frozen=True)
class PlayoffSpec:
    normalized_player_name: str
    display_name: str
    finish: int


@dataclass(frozen=True)
class AceSpec:
    normalized_player_name: str
    display_name: str
    hole: int
    amount: int


def parse_playoff_specs(values: list[str]) -> list[PlayoffSpec]:
    specs: list[PlayoffSpec] = []
    for raw in values:
        try:
            name, finish_text = raw.rsplit("=", 1)
            name = name.strip()
            finish = int(finish_text)
        except (ValueError, TypeError):
            raise FinalizerValidationError(
                f"Invalid --playoff {raw!r}; expected PLAYER NAME=FINISH."
            ) from None
        if not name or finish <= 0:
            raise FinalizerValidationError(
                f"Invalid --playoff {raw!r}; player name and positive finish are required."
            )
        specs.append(
            PlayoffSpec(
                normalized_player_name=normalized_name(name),
                display_name=name,
                finish=finish,
            )
        )
    return specs


def parse_ace_specs(values: list[str]) -> list[AceSpec]:
    specs: list[AceSpec] = []
    for raw in values:
        try:
            left, amount_text = raw.rsplit("=", 1)
            name, hole_text = left.rsplit(":", 1)
            name = name.strip()
            hole = int(hole_text)
            amount = int(amount_text)
        except (ValueError, TypeError):
            raise FinalizerValidationError(
                f"Invalid --ace-award {raw!r}; expected PLAYER NAME:HOLE=AMOUNT."
            ) from None
        if not name or hole <= 0 or amount < 0:
            raise FinalizerValidationError(
                f"Invalid --ace-award {raw!r}; name/hole are required and amount must be non-negative."
            )
        specs.append(
            AceSpec(
                normalized_player_name=normalized_name(name),
                display_name=name,
                hole=hole,
                amount=amount,
            )
        )
    return specs


def participant_lookup(cur, round_id: int) -> dict[str, tuple[int, str]]:
    cur.execute(
        """
        SELECT rp.round_participant_id, rp.display_name
        FROM round_participants rp
        WHERE rp.round_id = %s
        ORDER BY rp.round_participant_id
        """,
        (round_id,),
    )
    lookup: dict[str, tuple[int, str]] = {}
    duplicates: set[str] = set()
    for round_participant_id, display_name in cur.fetchall():
        key = normalized_name(display_name)
        if key in lookup:
            duplicates.add(key)
        lookup[key] = (int(round_participant_id), display_name)
    if duplicates:
        raise FinalizerValidationError(
            "Preview cannot resolve name-based operator decisions because duplicate normalized "
            "participant names exist in the round: " + ", ".join(sorted(duplicates))
        )
    return lookup


def apply_temporary_playoffs(
    cur,
    round_id: int,
    lookup: dict[str, tuple[int, str]],
    specs: list[PlayoffSpec],
) -> None:
    seen_ids: set[int] = set()
    for spec in specs:
        if spec.normalized_player_name not in lookup:
            raise FinalizerValidationError(
                f"--playoff player {spec.display_name!r} is not present in the staged round."
            )
        round_participant_id, canonical_name = lookup[spec.normalized_player_name]
        if round_participant_id in seen_ids:
            raise FinalizerValidationError(
                f"Duplicate --playoff decision supplied for {canonical_name}."
            )
        seen_ids.add(round_participant_id)
        cur.execute(
            """
            INSERT INTO playoff_resolutions (
              round_id, round_participant_id, resolved_finish
            ) VALUES (%s,%s,%s)
            ON CONFLICT (round_id, round_participant_id)
            DO UPDATE SET resolved_finish = EXCLUDED.resolved_finish
            """,
            (round_id, round_participant_id, spec.finish),
        )


def ace_allocations(
    lookup: dict[str, tuple[int, str]],
    specs: list[AceSpec],
) -> dict[tuple[int, int], int]:
    allocations: dict[tuple[int, int], int] = {}
    for spec in specs:
        if spec.normalized_player_name not in lookup:
            raise FinalizerValidationError(
                f"--ace-award player {spec.display_name!r} is not present in the staged round."
            )
        round_participant_id, canonical_name = lookup[spec.normalized_player_name]
        key = (round_participant_id, spec.hole)
        if key in allocations:
            raise FinalizerValidationError(
                f"Duplicate --ace-award supplied for {canonical_name} hole {spec.hole}."
            )
        allocations[key] = spec.amount
    return allocations


def print_input_summary(cur, context, participants) -> None:
    eligible = [item for item in participants if item.input.is_field_eligible]
    finishers = [item for item in participants if item.input.is_scoring_finisher]
    dnfs = [item for item in participants if item.input.status == "dnf"]
    guests = [item for item in participants if item.input.participant_type == "guest"]
    aces = [
        (item.input.name, hole)
        for item in participants
        for hole in item.ace_holes
        if item.input.is_field_eligible
    ]

    cur.execute(
        """
        SELECT COALESCE(SUM(amount),0)::integer
        FROM financial_transactions
        WHERE league_id = %s AND fund_type = 'ace_pot'
        """,
        (context.league_id,),
    )
    current_ace_balance = int(cur.fetchone()[0])
    projected_ace_balance = current_ace_balance + len(eligible) * int(context.ace_contribution)

    print("BirdBrain native round operator preview")
    print("=======================================")
    print(f"Round:                 R{context.round_no} - {context.course} / {context.layout}")
    print(f"Season:                {context.season_year}")
    print(f"Eligible field:        {len(eligible)}")
    print(f"Scoring finishers:     {len(finishers)}")
    print(f"DNFs:                  {len(dnfs)}")
    print(f"Guests:                {len(guests)}")
    print(f"Projected purse:       ${len(eligible) * int(context.payout_contribution)}")
    print(f"Ace occurrences:       {len(aces)}")
    print(f"Projected ace pot:     ${projected_ace_balance}")
    if dnfs:
        print("DNF players:           " + ", ".join(sorted(item.input.name for item in dnfs)))
    if guests:
        print("Guest players:         " + ", ".join(sorted(item.input.name for item in guests)))
    if aces:
        print(
            "Ace holes:             "
            + ", ".join(f"{name} H{hole}" for name, hole in sorted(aces))
        )

    print("\nParticipant review")
    for item in sorted(
        participants,
        key=lambda row: (
            row.input.status != "active",
            (Decimal(row.input.gross_score) - Decimal(row.input.applied_handicap))
            if row.input.gross_score is not None
            else Decimal("999999"),
            row.input.name.casefold(),
        ),
    ):
        if item.input.gross_score is None:
            net_text = "-"
            gross_text = "-"
        else:
            net = Decimal(item.input.gross_score) - Decimal(item.input.applied_handicap)
            gross_text = str(item.input.gross_score)
            net_text = format(net.normalize(), "f")
        print(
            f"  id={item.input.round_participant_id:<6} "
            f"{item.input.name:<28} "
            f"status={item.input.status:<8} "
            f"gross={gross_text:<4} "
            f"hcp={format(Decimal(item.input.applied_handicap).normalize(), 'f'):<5} "
            f"net={net_text}"
        )


def run_preview(
    *,
    round_no: int,
    season_year: int | None,
    xlsx: str,
    playoff_specs: list[PlayoffSpec],
    ace_specs: list[AceSpec],
) -> int:
    conn = connect_admin()
    try:
        try:
            with conn.transaction():
                import_summary, import_mode = import_udisc(
                    conn,
                    round_no=round_no,
                    season_year=season_year,
                    xlsx_path=xlsx,
                    commit=True,
                )
                if import_mode not in {"committed", "already-imported no-op"}:
                    raise RuntimeError(f"Unexpected import mode {import_mode!r}.")

                with conn.cursor() as cur:
                    context = load_round_context(cur, round_no, season_year, for_update=True)
                    lookup = participant_lookup(cur, context.round_id)
                    apply_temporary_playoffs(cur, context.round_id, lookup, playoff_specs)
                    participants = load_participants(cur, context)
                    print_input_summary(cur, context, participants)
                    allocations = ace_allocations(lookup, ace_specs)

                try:
                    final_summary, final_mode = finalize(
                        conn,
                        round_no,
                        season_year,
                        allocations,
                        commit=False,
                    )
                except FinalizerValidationError as exc:
                    print("\nFINALIZATION BLOCKED")
                    print(f"  {exc}")
                    print("\nPreview import and temporary operator decisions will be rolled back.")
                    raise PreviewRollback(2) from exc

                if final_summary is None or final_mode != "dry-run rolled back":
                    raise RuntimeError(
                        f"Expected finalizer dry-run rollback; got mode={final_mode!r}."
                    )
                print("\nProjected finalization")
                print_summary(final_summary, final_mode)
                print("\nPASS: import + finalization preview completed successfully.")
                print("All temporary preview state will now be rolled back.")
                raise PreviewRollback(0)
        except PreviewRollback as exc:
            return int(exc.args[0]) if exc.args else 0
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rollback-only operator preview for a real BirdBrain round. Temporarily stages "
            "the UDisc export, applies optional playoff/ace decisions, executes the full "
            "finalizer dry-run, then rolls all preview changes back."
        )
    )
    parser.add_argument("--round-no", type=int, required=True)
    parser.add_argument("--season-year", type=int)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument(
        "--playoff",
        action="append",
        default=[],
        help="Temporary playoff decision for preview: PLAYER NAME=FINISH. Repeat as needed.",
    )
    parser.add_argument(
        "--ace-award",
        action="append",
        default=[],
        help="Temporary multiple-ace allocation for preview: PLAYER NAME:HOLE=AMOUNT.",
    )
    args = parser.parse_args()

    try:
        playoff_specs = parse_playoff_specs(args.playoff)
        ace_specs = parse_ace_specs(args.ace_award)
        exit_code = run_preview(
            round_no=args.round_no,
            season_year=args.season_year,
            xlsx=args.xlsx,
            playoff_specs=playoff_specs,
            ace_specs=ace_specs,
        )
    except FinalizerValidationError as exc:
        raise SystemExit(f"Preview validation failed: {exc}") from None

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
