from __future__ import annotations

import argparse
import math
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from analyze_staging import (
    analyze,
    connect_db,
    get_sheet_metadata,
    get_sheet_rows,
    latest_snapshot_id,
    nonblank,
    normalized_name,
    parse_int,
)

DEFAULT_LEAGUE_NAME = "BirdBrain Disc Golf Club"


def parse_decimal(value: Any) -> Decimal | None:
    text = nonblank(value)
    if not text or text.upper() in {"NA", "N/A", "NULL"}:
        return None
    text = text.replace("$", "").replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Expected numeric value, got {value!r}") from exc


def decimal_int(value: Any) -> int | None:
    number = parse_decimal(value)
    if number is None:
        return None
    if number != number.to_integral_value():
        raise ValueError(f"Expected whole-number value, got {value!r}")
    return int(number)


def parse_date(value: Any) -> date | None:
    text = nonblank(value)
    if not text:
        return None
    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date value: {value!r}")


def parse_time(value: Any) -> time | None:
    text = nonblank(value)
    if not text:
        return None
    formats = ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    # Keep the original display text even if the typed value cannot be parsed.
    return None


def parse_holes(value: Any) -> list[int]:
    text = nonblank(value)
    if not text or text.upper() in {"NA", "N/A"}:
        return []
    return sorted({int(x) for x in re.findall(r"\d+", text)})


def infer_hole_count(par: int, fours: list[int], fives: list[int]) -> int:
    numerator = par - len(fours) - 2 * len(fives)
    inferred = numerator // 3 if numerator > 0 and numerator % 3 == 0 else 18
    referenced = max(fours + fives, default=0)
    hole_count = max(inferred, referenced)
    if hole_count <= 0:
        hole_count = 18
    calculated_par = 3 * hole_count + len(fours) + 2 * len(fives)
    if calculated_par != par:
        raise ValueError(
            f"Cannot reconcile par={par} from default-par-3 layout with "
            f"ParFours={fours} and ParFives={fives}; inferred holes={hole_count}, "
            f"calculated par={calculated_par}."
        )
    return hole_count


def parse_applied_handicap(value: Any) -> int | None:
    text = nonblank(value)
    if not text:
        return None
    if text.upper() == "E":
        return 0
    text = text.replace("+", "")
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid leaderboard handicap: {value!r}") from exc


def half_up(value: float) -> int:
    if value == 0:
        return 0
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def display_applied_from_precise(precise: Decimal, completed_rounds: int) -> int:
    applied = half_up(float(precise))
    if completed_rounds <= 5:
        return min(8, max(-5, applied))
    return applied


def season_year_from_label(value: Any) -> int | None:
    text = nonblank(value)
    match = re.search(r"(?:19|20)\d{2}", text)
    return int(match.group(0)) if match else None


def first_valid_schedule_year(schedule_rows: list[dict[str, str]]) -> int:
    years = [parse_date(row.get("Datend")) for row in schedule_rows]
    years = [d.year for d in years if d is not None]
    if not years:
        raise RuntimeError("Could not infer the current season year from League Schedule.")
    unique = sorted(set(years))
    if len(unique) != 1:
        raise RuntimeError(f"League Schedule spans multiple years unexpectedly: {unique}")
    return unique[0]


def get_snapshot_rows(cur, snapshot_id: int) -> dict[str, list[dict[str, str]]]:
    metadata = get_sheet_metadata(cur, snapshot_id)
    return {
        sheet_name: get_sheet_rows(cur, snapshot_id, sheet_name, meta["headers"])
        for sheet_name, meta in metadata.items()
    }


def current_round_mappings(report: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(report["round_mappings"], key=lambda x: x["round_no"])


def sham_activation_round(
    score_rows: list[dict[str, str]], mappings: list[dict[str, Any]]
) -> int | None:
    unique_players: set[str] = set()
    completed_count = 0
    for mapping in mappings:
        if not mapping["has_season_score_column"]:
            continue
        completed_count += 1
        col = mapping["legacy_column"]
        for row in score_rows:
            if nonblank(row.get(col)):
                name = nonblank(row.get("Name"))
                if name:
                    unique_players.add(normalized_name(name))
        if completed_count >= 11 and len(unique_players) >= 40:
            return int(mapping["round_no"])
    return None


def collect_players(rows: dict[str, list[dict[str, str]]]) -> dict[str, str]:
    # Prefer current public names, then current history, then all-time history.
    precedence = [
        "Leaderboard",
        "Current All Time",
        "Full Season Scores",
        "Handicap",
        "Player Pool Assignments",
        "Past All Time",
        "Aces",
        "Course Records",
        "Hall of Champions",
    ]
    players: dict[str, str] = {}
    for sheet in precedence:
        for row in rows.get(sheet, []):
            display = nonblank(row.get("Name"))
            if not display:
                continue
            key = normalized_name(display)
            players.setdefault(key, display)
    return players


def plan_summary(report: dict[str, Any], rows: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    mappings = current_round_mappings(report)
    completed = [m for m in mappings if m["has_season_score_column"]]
    latest_completed = max((m["round_no"] for m in completed), default=0)
    score_rows = rows.get("Full Season Scores", [])
    players = collect_players(rows)
    participant_rows = sum(
        1
        for mapping in completed
        for row in score_rows
        if nonblank(row.get(mapping["legacy_column"]))
    )
    handicap_adjustments = sum(
        1
        for mapping in completed
        for row in rows.get("Handicap", [])
        if nonblank(row.get(mapping["legacy_column"]))
    )
    activation = sham_activation_round(score_rows, mappings)
    return {
        "players": len(players),
        "schedule_rounds": len(mappings),
        "completed_rounds": len(completed),
        "latest_completed_round": latest_completed,
        "historical_round_results": participant_rows,
        "handicap_adjustments": handicap_adjustments,
        "sham_activation_round": activation,
        "past_all_time_rows": len(rows.get("Past All Time", [])),
        "aces": len(rows.get("Aces", [])),
        "course_records": len(rows.get("Course Records", [])),
        "champions": len(rows.get("Hall of Champions", [])),
    }


def ensure_empty_target(cur) -> None:
    cur.execute("SELECT COUNT(*) FROM leagues")
    count = int(cur.fetchone()[0])
    if count != 0:
        raise RuntimeError(
            "Normalized target is not empty (leagues already contains rows). "
            "Bootstrap import is intentionally one-shot; do not merge into an existing target."
        )


def get_or_create_player(cur, cache: dict[str, int], display_name: str) -> int:
    key = normalized_name(display_name)
    if key in cache:
        return cache[key]
    cur.execute(
        """
        INSERT INTO players (display_name, normalized_name)
        VALUES (%s, %s)
        ON CONFLICT (normalized_name)
        DO UPDATE SET display_name = EXCLUDED.display_name
        RETURNING player_id
        """,
        (display_name, key),
    )
    player_id = int(cur.fetchone()[0])
    cache[key] = player_id
    return player_id


def get_or_create_course(cur, cache: dict[str, int], name: str) -> int:
    key = name.strip()
    if key in cache:
        return cache[key]
    cur.execute(
        """
        INSERT INTO courses (name) VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING course_id
        """,
        (key,),
    )
    course_id = int(cur.fetchone()[0])
    cache[key] = course_id
    return course_id


def get_or_create_layout(
    cur,
    cache: dict[tuple[str, str], int],
    course_cache: dict[str, int],
    course_name: str,
    layout_name: str,
    par: int | None,
    hole_count: int = 18,
) -> int:
    key = (course_name.strip(), layout_name.strip())
    if key in cache:
        if par is not None:
            cur.execute(
                "UPDATE layouts SET par = COALESCE(par, %s) WHERE layout_id = %s",
                (par, cache[key]),
            )
        return cache[key]
    course_id = get_or_create_course(cur, course_cache, key[0])
    cur.execute(
        """
        INSERT INTO layouts (course_id, name, par, hole_count)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (course_id, name)
        DO UPDATE SET
          par = COALESCE(EXCLUDED.par, layouts.par),
          hole_count = GREATEST(layouts.hole_count, EXCLUDED.hole_count)
        RETURNING layout_id
        """,
        (course_id, key[1], par, hole_count),
    )
    layout_id = int(cur.fetchone()[0])
    cache[key] = layout_id
    return layout_id


def apply_bootstrap(
    snapshot_id: int,
    report: dict[str, Any],
    rows: dict[str, list[dict[str, str]]],
    league_name: str,
) -> dict[str, Any]:
    summary = plan_summary(report, rows)
    mappings = current_round_mappings(report)
    completed_mappings = [m for m in mappings if m["has_season_score_column"]]
    latest_completed = int(summary["latest_completed_round"])
    activation_round = summary["sham_activation_round"]
    schedule_rows = rows["League Schedule"]
    current_year = first_valid_schedule_year(schedule_rows)
    current_season_name = str(current_year)

    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                ensure_empty_target(cur)

                cur.execute(
                    "INSERT INTO leagues (name) VALUES (%s) RETURNING league_id",
                    (league_name,),
                )
                league_id = int(cur.fetchone()[0])

                season_ids: dict[str, int] = {}

                def ensure_season(label: str, year: int | None = None) -> int:
                    label = nonblank(label) or (str(year) if year else "Unknown")
                    if label in season_ids:
                        return season_ids[label]
                    resolved_year = year if year is not None else season_year_from_label(label)
                    closed_at = None
                    if resolved_year is not None and resolved_year < current_year:
                        closed_at = datetime(resolved_year, 12, 31, 23, 59, tzinfo=timezone.utc)
                    cur.execute(
                        """
                        INSERT INTO seasons
                          (league_id, season_name, season_year, closed_at)
                        VALUES (%s, %s, %s, %s)
                        RETURNING season_id
                        """,
                        (league_id, label, resolved_year, closed_at),
                    )
                    season_id = int(cur.fetchone()[0])
                    season_ids[label] = season_id
                    return season_id

                current_season_id = ensure_season(current_season_name, current_year)

                for row in rows.get("Past All Time", []):
                    label = nonblank(row.get("Season"))
                    if label:
                        ensure_season(label)
                for row in rows.get("Hall of Champions", []):
                    year = parse_int(row.get("Year"))
                    if year is not None:
                        ensure_season(str(year), year)

                player_cache: dict[str, int] = {}
                for key, display in collect_players(rows).items():
                    player_cache[key] = get_or_create_player(cur, player_cache, display)

                def player_id_for(name: str) -> int:
                    display = nonblank(name)
                    if not display:
                        raise ValueError("Encountered a blank player name in an authoritative row.")
                    return get_or_create_player(cur, player_cache, display)

                # Memberships: leaderboard defines current tag-holder/public roster;
                # score-only rows are retained as current members but not tag holders.
                leaderboard_names = {
                    normalized_name(row["Name"]): row["Name"]
                    for row in rows.get("Leaderboard", [])
                    if nonblank(row.get("Name"))
                }
                current_member_names = dict(leaderboard_names)
                for row in rows.get("Full Season Scores", []):
                    if nonblank(row.get("Name")):
                        current_member_names.setdefault(normalized_name(row["Name"]), row["Name"])

                for key, display in current_member_names.items():
                    cur.execute(
                        """
                        INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (season_id, player_id) DO NOTHING
                        """,
                        (current_season_id, player_id_for(display), key in leaderboard_names),
                    )

                for row in rows.get("Past All Time", []):
                    name = nonblank(row.get("Name"))
                    label = nonblank(row.get("Season"))
                    if not name or not label:
                        continue
                    season_id = ensure_season(label)
                    pid = player_id_for(name)
                    cur.execute(
                        """
                        INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (season_id, player_id) DO NOTHING
                        """,
                        (season_id, pid),
                    )
                    cur.execute(
                        """
                        INSERT INTO season_player_summaries
                          (season_id, player_id, rounds, points, through_round_no, source)
                        VALUES (%s, %s, %s, %s, NULL, 'past_all_time')
                        ON CONFLICT (season_id, player_id)
                        DO UPDATE SET rounds = EXCLUDED.rounds, points = EXCLUDED.points,
                                      through_round_no = NULL, source = EXCLUDED.source
                        """,
                        (
                            season_id,
                            pid,
                            decimal_int(row.get("Rounds")) or 0,
                            decimal_int(row.get("Points")) or 0,
                        ),
                    )

                # The current leaderboard is the migration standings baseline.
                for row in rows.get("Leaderboard", []):
                    name = nonblank(row.get("Name"))
                    if not name:
                        continue
                    cur.execute(
                        """
                        INSERT INTO season_player_summaries
                          (season_id, player_id, rounds, points, through_round_no, source)
                        VALUES (%s, %s, %s, %s, %s, 'leaderboard_migration_baseline')
                        """,
                        (
                            current_season_id,
                            player_id_for(name),
                            decimal_int(row.get("Rounds")) or 0,
                            decimal_int(row.get("Points")) or 0,
                            latest_completed,
                        ),
                    )

                course_cache: dict[str, int] = {}
                layout_cache: dict[tuple[str, str], int] = {}
                round_ids: dict[int, int] = {}
                schedule_by_round = {
                    parse_int(row.get("RoundNo")): row
                    for row in schedule_rows
                    if parse_int(row.get("RoundNo")) is not None
                }

                # Current season schedule, layouts, holes, and rounds.
                for mapping in mappings:
                    round_no = int(mapping["round_no"])
                    row = schedule_by_round[round_no]
                    course = nonblank(row.get("Course"))
                    layout = nonblank(row.get("Layout"))
                    scheduled_date = parse_date(row.get("Datend"))
                    if not course or not layout or scheduled_date is None:
                        raise ValueError(f"Round {round_no} has incomplete schedule identity: {row}")
                    par = decimal_int(row.get("Par"))
                    if par is None:
                        raise ValueError(f"Round {round_no} is missing Par.")
                    fours = parse_holes(row.get("ParFours"))
                    fives = parse_holes(row.get("ParFives"))
                    hole_count = infer_hole_count(par, fours, fives)
                    layout_id = get_or_create_layout(
                        cur, layout_cache, course_cache, course, layout, par, hole_count
                    )
                    for hole_no in range(1, hole_count + 1):
                        hole_par = 5 if hole_no in fives else 4 if hole_no in fours else 3
                        cur.execute(
                            """
                            INSERT INTO layout_holes (layout_id, hole_number, par)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (layout_id, hole_number)
                            DO UPDATE SET par = EXCLUDED.par
                            """,
                            (layout_id, hole_no, hole_par),
                        )

                    note = nonblank(row.get("Note")) or None
                    is_double = bool(note and "DOUBLE POINTS" in note.upper())
                    payout_contribution = 5 if is_double or scheduled_date.weekday() == 5 else 4
                    postseason_contribution = 2 if is_double else 1
                    status = "finalized" if mapping["has_season_score_column"] else "scheduled"
                    cur.execute(
                        """
                        INSERT INTO rounds (
                          season_id, round_no, layout_id, scheduled_date, start_time,
                          note, points_multiplier, payout_contribution,
                          postseason_contribution, ace_contribution, status,
                          display_date, display_start_time, ace_pot_start
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
                        RETURNING round_id
                        """,
                        (
                            current_season_id,
                            round_no,
                            layout_id,
                            scheduled_date,
                            parse_time(row.get("StartTime")),
                            note,
                            2 if is_double else 1,
                            payout_contribution,
                            postseason_contribution,
                            status,
                            nonblank(row.get("Date")) or None,
                            nonblank(row.get("StartTime")) or None,
                            decimal_int(row.get("AcePot")),
                        ),
                    )
                    round_ids[round_no] = int(cur.fetchone()[0])

                score_rows = rows.get("Full Season Scores", [])
                layout_par_by_round = {
                    int(mapping["round_no"]): decimal_int(schedule_by_round[int(mapping["round_no"])].get("Par"))
                    for mapping in mappings
                }

                # Detailed gross history is preserved, but historical points remain
                # zero because standings use the migration baseline through cutoff.
                for mapping in completed_mappings:
                    round_no = int(mapping["round_no"])
                    col = mapping["legacy_column"]
                    for row in score_rows:
                        gross = decimal_int(row.get(col))
                        name = nonblank(row.get("Name"))
                        if gross is None or not name:
                            continue
                        pid = player_id_for(name)
                        cur.execute(
                            """
                            INSERT INTO round_participants
                              (round_id, player_id, display_name, participant_type,
                               started_round, status)
                            VALUES (%s, %s, %s, 'member', TRUE, 'active')
                            RETURNING round_participant_id
                            """,
                            (round_ids[round_no], pid, name),
                        )
                        rpid = int(cur.fetchone()[0])
                        par = layout_par_by_round[round_no]
                        cur.execute(
                            """
                            INSERT INTO round_results
                              (round_participant_id, gross_score, score_to_par, points,
                               payout_award)
                            VALUES (%s, %s, %s, 0, 0)
                            """,
                            (rpid, gross, gross - par if par is not None else None),
                        )

                # Handicap adjustment history.
                adjustment_ids: dict[int, list[tuple[int, int, Decimal]]] = {}
                for row in rows.get("Handicap", []):
                    name = nonblank(row.get("Name"))
                    if not name:
                        continue
                    pid = player_id_for(name)
                    for mapping in completed_mappings:
                        value = parse_decimal(row.get(mapping["legacy_column"]))
                        if value is None:
                            continue
                        round_no = int(mapping["round_no"])
                        method = (
                            "sham"
                            if activation_round is not None and round_no >= int(activation_round)
                            else "pre_sham_par"
                        )
                        cur.execute(
                            """
                            INSERT INTO handicap_adjustments
                              (player_id, round_id, adjustment, method)
                            VALUES (%s, %s, %s, %s)
                            RETURNING handicap_adjustment_id
                            """,
                            (pid, round_ids[round_no], value, method),
                        )
                        aid = int(cur.fetchone()[0])
                        adjustment_ids.setdefault(pid, []).append((aid, round_no, value))

                leaderboard_by_name = {
                    normalized_name(row["Name"]): row
                    for row in rows.get("Leaderboard", [])
                    if nonblank(row.get("Name"))
                }
                score_round_counts: dict[str, int] = {}
                for row in score_rows:
                    name = nonblank(row.get("Name"))
                    if not name:
                        continue
                    score_round_counts[normalized_name(name)] = sum(
                        1 for m in completed_mappings if nonblank(row.get(m["legacy_column"]))
                    )

                if latest_completed > 0:
                    effective_round_id = round_ids[latest_completed]
                    for row in rows.get("Handicap", []):
                        name = nonblank(row.get("Name"))
                        precise = parse_decimal(row.get("Handicap"))
                        if not name or precise is None:
                            continue
                        pid = player_id_for(name)
                        board = leaderboard_by_name.get(normalized_name(name))
                        applied = (
                            parse_applied_handicap(board.get("Handicap"))
                            if board is not None
                            else None
                        )
                        if applied is None:
                            applied = display_applied_from_precise(
                                precise,
                                score_round_counts.get(normalized_name(name), 0),
                            )
                        cur.execute(
                            """
                            INSERT INTO handicap_calculations
                              (player_id, effective_after_round_id, precise_handicap,
                               applied_handicap, trim_fraction)
                            VALUES (%s, %s, %s, %s, 0.20)
                            RETURNING handicap_calculation_id
                            """,
                            (pid, effective_round_id, precise, applied),
                        )
                        calc_id = int(cur.fetchone()[0])
                        adjustments = sorted(
                            adjustment_ids.get(pid, []), key=lambda x: (x[2], x[1])
                        )
                        n = len(adjustments)
                        cut = math.floor(n * 0.20) if n >= 5 else 0
                        for index, (aid, _round_no, _value) in enumerate(adjustments):
                            included = not (index < cut or index >= n - cut)
                            trim_side = "low" if index < cut else "high" if index >= n - cut else None
                            cur.execute(
                                """
                                INSERT INTO handicap_calculation_adjustments
                                  (handicap_calculation_id, handicap_adjustment_id,
                                   included, trim_side)
                                VALUES (%s, %s, %s, %s)
                                """,
                                (calc_id, aid, included, trim_side),
                            )

                # Current pool assignment applies to the next round after cutoff.
                for row in rows.get("Player Pool Assignments", []):
                    name = nonblank(row.get("Name"))
                    pool = nonblank(row.get("Pool")).upper()
                    if not name or pool not in {"A", "B", "C", "D", "E"}:
                        continue
                    cur.execute(
                        """
                        INSERT INTO player_pool_assignments
                          (season_id, player_id, pool, effective_round_no)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (current_season_id, player_id_for(name), pool, max(1, latest_completed + 1)),
                    )

                for row in rows.get("Poolwise Strokes by Round", []):
                    rdno = decimal_int(row.get("RdNo"))
                    pool = nonblank(row.get("Pool")).upper()
                    if rdno is None or pool not in {"A", "B", "C", "D", "E"}:
                        continue
                    cur.execute(
                        """
                        INSERT INTO sham_pool_round_stats (
                          season_id, round_id, legacy_round_no, course_name, layout_name,
                          par, pool, players, strokes, average, stddev, par_strokes
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            current_season_id,
                            round_ids.get(rdno),
                            rdno,
                            nonblank(row.get("Course")),
                            nonblank(row.get("Layout")),
                            decimal_int(row.get("Par")),
                            pool,
                            decimal_int(row.get("Players")) or 0,
                            decimal_int(row.get("Strokes")) or 0,
                            parse_decimal(row.get("Avg")),
                            parse_decimal(row.get("StdDev")),
                            decimal_int(row.get("ParStrokes")),
                        ),
                    )

                for row in rows.get("Course Slopes and Ratings", []):
                    course = nonblank(row.get("Course"))
                    layout = nonblank(row.get("Layout"))
                    if not course or not layout:
                        continue
                    par = decimal_int(row.get("Par"))
                    layout_id = get_or_create_layout(
                        cur, layout_cache, course_cache, course, layout, par, 18
                    )
                    cur.execute(
                        """
                        INSERT INTO sham_layout_models (
                          season_id, layout_id, effective_after_round_no, total_rounds,
                          stroke_total, par_total, grand_mean, rating, slope,
                          standardized_slope, weight
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (season_id, layout_id, effective_after_round_no)
                        DO UPDATE SET
                          total_rounds=EXCLUDED.total_rounds,
                          stroke_total=EXCLUDED.stroke_total,
                          par_total=EXCLUDED.par_total,
                          grand_mean=EXCLUDED.grand_mean,
                          rating=EXCLUDED.rating,
                          slope=EXCLUDED.slope,
                          standardized_slope=EXCLUDED.standardized_slope,
                          weight=EXCLUDED.weight
                        """,
                        (
                            current_season_id,
                            layout_id,
                            max(1, latest_completed),
                            decimal_int(row.get("TotRds")),
                            parse_decimal(row.get("StrokeTotal")),
                            parse_decimal(row.get("ParTotal")),
                            parse_decimal(row.get("GM")),
                            parse_decimal(row.get("Rating")),
                            parse_decimal(row.get("Slope")),
                            parse_decimal(row.get("StdSlope")),
                            parse_decimal(row.get("Weight")),
                        ),
                    )

                # Historical records and aces do not require synthetic round rows.
                for row in rows.get("Course Records", []):
                    course = nonblank(row.get("Course"))
                    layout = nonblank(row.get("Layout"))
                    name = nonblank(row.get("Name"))
                    score = decimal_int(row.get("Score"))
                    achieved = parse_date(row.get("Date"))
                    if not course or not layout or not name or score is None or achieved is None:
                        continue
                    layout_id = get_or_create_layout(
                        cur, layout_cache, course_cache, course, layout, None, 18
                    )
                    cur.execute(
                        """
                        INSERT INTO course_records
                          (layout_id, round_id, player_id, score, achieved_on)
                        VALUES (%s, NULL, %s, %s, %s)
                        """,
                        (layout_id, player_id_for(name), score, achieved),
                    )

                for row in rows.get("Aces", []):
                    course = nonblank(row.get("Course"))
                    layout = nonblank(row.get("Layout"))
                    name = nonblank(row.get("Name"))
                    hole = decimal_int(row.get("Hole"))
                    achieved = parse_date(row.get("Date"))
                    payout = parse_decimal(row.get("Payout"))
                    if not course or not layout or not name or hole is None or achieved is None:
                        continue
                    layout_id = get_or_create_layout(
                        cur, layout_cache, course_cache, course, layout, None, 18
                    )
                    cur.execute(
                        """
                        INSERT INTO ace_awards
                          (round_id, round_participant_id, player_id, layout_id,
                           achieved_on, hole_number, payout)
                        VALUES (NULL, NULL, %s, %s, %s, %s, %s)
                        """,
                        (player_id_for(name), layout_id, achieved, hole, payout or Decimal(0)),
                    )

                for row in rows.get("Hall of Champions", []):
                    name = nonblank(row.get("Name"))
                    event = nonblank(row.get("Event"))
                    year = parse_int(row.get("Year"))
                    if not name or not event or year is None:
                        continue
                    season_id = ensure_season(str(year), year)
                    pid = player_id_for(name)
                    cur.execute(
                        """
                        INSERT INTO season_memberships (season_id, player_id, is_tag_holder)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (season_id, player_id) DO NOTHING
                        """,
                        (season_id, pid),
                    )
                    cur.execute(
                        """
                        INSERT INTO hall_of_champions
                          (season_id, player_id, title, event_name, event_year,
                           division, score_text)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (season_id, player_id, title)
                        DO UPDATE SET
                          event_name=EXCLUDED.event_name,
                          event_year=EXCLUDED.event_year,
                          division=EXCLUDED.division,
                          score_text=EXCLUDED.score_text
                        """,
                        (
                            season_id,
                            pid,
                            event,
                            event,
                            year,
                            nonblank(row.get("Division")) or None,
                            nonblank(row.get("Score")) or None,
                        ),
                    )

                # Seed the rolling ace-pot ledger at the cutover using the first
                # scheduled round after the latest completed one.
                next_round_numbers = sorted(
                    n for n in schedule_by_round if n > latest_completed
                )
                if next_round_numbers:
                    next_no = next_round_numbers[0]
                    current_pot = decimal_int(schedule_by_round[next_no].get("AcePot")) or 0
                    if current_pot > 0:
                        cur.execute(
                            """
                            INSERT INTO financial_transactions (
                              league_id, season_id, round_id, fund_type,
                              transaction_type, amount, memo
                            ) VALUES (%s,%s,%s,'ace_pot','adjustment',%s,%s)
                            """,
                            (
                                league_id,
                                current_season_id,
                                round_ids[next_no],
                                current_pot,
                                f"Migration opening ace-pot balance through round {latest_completed}",
                            ),
                        )

                cur.execute(
                    """
                    INSERT INTO audit_events
                      (league_id, season_id, event_type, event_payload)
                    VALUES (%s, %s, 'migration_bootstrap', jsonb_build_object(
                      'snapshot_id', %s,
                      'through_round_no', %s,
                      'source', 'google_sheet_read_only_snapshot'
                    ))
                    """,
                    (league_id, current_season_id, snapshot_id, latest_completed),
                )

        return summary
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap an empty BirdBrain normalized dev database from a validated "
            "migration_staging snapshot. Dry-run is the default."
        )
    )
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--league-name", default=DEFAULT_LEAGUE_NAME)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write normalized data. Without this flag only the plan is printed.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # analyze() uses the latest staged snapshot when snapshot_id is omitted.
    report = analyze(args.snapshot_id)
    if not report["ready_for_transform"]:
        raise SystemExit(
            "Staging contract has blocking errors. Run analyze_staging.py and fix them first."
        )
    snapshot_id = int(report["snapshot_id"])

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            rows = get_snapshot_rows(cur, snapshot_id)
    finally:
        conn.close()

    summary = plan_summary(report, rows)
    print(f"Snapshot ID: {snapshot_id}")
    print("Bootstrap plan:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if not args.apply:
        print("\nDRY RUN ONLY: no normalized tables were changed.")
        print("Run again with --apply only after reviewing the staging analysis.")
        return

    applied = apply_bootstrap(snapshot_id, report, rows, args.league_name)
    print("\nBootstrap import COMMITTED.")
    print(f"Migration standings baseline is through round {applied['latest_completed_round']}.")
    print("Next step: run parity_check.py before wiring the Shiny dev app.")


if __name__ == "__main__":
    main()
