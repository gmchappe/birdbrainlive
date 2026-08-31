from __future__ import annotations

import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from analyze_staging import nonblank, normalized_name


def _decimal(value: Any) -> Decimal | None:
    text = nonblank(value)
    if not text or text.upper() in {"NA", "N/A", "NULL"}:
        return None
    text = text.replace("$", "").replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Expected numeric value, got {value!r}") from exc


def _whole(value: Any) -> int:
    number = _decimal(value)
    if number is None:
        return 0
    if number != number.to_integral_value():
        raise ValueError(f"Expected whole-number value, got {value!r}")
    return int(number)


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _half_up(value: Decimal) -> int:
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    return sign * int(math.floor(float(magnitude) + 0.5))


def _display_handicap(precise: Decimal, rounds: int) -> str:
    applied = _half_up(precise)
    if rounds <= 5:
        applied = min(8, max(-5, applied))
    if applied == 0:
        return "E"
    if applied > 0:
        return f"+{applied}"
    return str(applied)


def _casing_quality(name: str) -> tuple[int, int, int, str]:
    """Prefer conventional capitalization without rewriting a person's name."""
    tokens = [t for t in re.split(r"\s+", name.strip()) if t]
    conventional = 0
    odd_internal_caps = 0
    for token in tokens:
        letters = re.sub(r"[^A-Za-z]", "", token)
        if not letters:
            continue
        if letters[:1].isupper() and letters[1:].islower():
            conventional += 2
        elif letters[:1].isupper():
            conventional += 1
        odd_internal_caps += sum(1 for ch in letters[1:] if ch.isupper())
    return (conventional, -odd_internal_caps, len(tokens), name)


def canonical_display_names(rows: dict[str, list[dict[str, str]]]) -> dict[str, str]:
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
    candidates: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for rank, sheet in enumerate(precedence):
        for row in rows.get(sheet, []):
            display = nonblank(row.get("Name"))
            if display:
                candidates[normalized_name(display)][rank].add(display)

    result: dict[str, str] = {}
    for key, by_rank in candidates.items():
        best_rank = min(by_rank)
        result[key] = max(by_rank[best_rank], key=_casing_quality)
    return result


def _group_by_name(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        name = nonblank(row.get("Name"))
        if name:
            grouped[normalized_name(name)].append(dict(row))
    return grouped


def _merge_nonconflicting_rows(
    rows: list[dict[str, str]], canonical_names: dict[str, str]
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for key, group in _group_by_name(rows).items():
        columns = []
        for row in group:
            for column in row:
                if column not in columns:
                    columns.append(column)
        out: dict[str, str] = {column: "" for column in columns}
        out["Name"] = canonical_names.get(key, nonblank(group[0].get("Name")))
        for column in columns:
            if column == "Name":
                continue
            values = [nonblank(row.get(column)) for row in group if nonblank(row.get(column))]
            if values:
                # analyze_staging blocks conflicting dynamic round values before this point.
                out[column] = values[0]
        merged.append(out)
    return merged


def _trimmed_precise(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    cut = math.floor(len(ordered) * 0.20) if len(ordered) >= 5 else 0
    kept = ordered[cut: len(ordered) - cut] if cut else ordered
    if not kept:
        return None
    return sum(kept, Decimal(0)) / Decimal(len(kept))


def _normalize_round_columns(
    report: dict[str, Any], rows: dict[str, list[dict[str, str]]]
) -> None:
    for mapping in report["round_mappings"]:
        expected = mapping["legacy_column"]
        score_actual = mapping.get("season_score_column")
        handicap_actual = mapping.get("handicap_adjustment_column")
        if score_actual:
            for row in rows.get("Full Season Scores", []):
                row[expected] = nonblank(row.get(score_actual))
        if handicap_actual:
            for row in rows.get("Handicap", []):
                row[expected] = nonblank(row.get(handicap_actual))


def _normalize_handicap(
    report: dict[str, Any],
    source_rows: list[dict[str, str]],
    canonical_names: dict[str, str],
) -> list[dict[str, str]]:
    merged = _merge_nonconflicting_rows(source_rows, canonical_names)
    adjustment_columns = [
        m["legacy_column"]
        for m in report["round_mappings"]
        if m["has_handicap_adjustment_column"]
    ]
    for row in merged:
        values = [
            value
            for column in adjustment_columns
            if (value := _decimal(row.get(column))) is not None
        ]
        precise = _trimmed_precise(values)
        if precise is not None:
            row["Handicap"] = _format_decimal(precise)
    return merged


def _normalize_leaderboard(
    source_rows: list[dict[str, str]],
    handicap_rows: list[dict[str, str]],
    canonical_names: dict[str, str],
) -> list[dict[str, str]]:
    handicap_by_name = {
        normalized_name(row["Name"]): row for row in handicap_rows if nonblank(row.get("Name"))
    }
    result: list[dict[str, str]] = []
    for key, group in _group_by_name(source_rows).items():
        points = sum(_whole(row.get("Points")) for row in group)
        rounds = sum(_whole(row.get("Rounds")) for row in group)
        precise = _decimal(handicap_by_name.get(key, {}).get("Handicap"))
        if precise is not None:
            handicap = _display_handicap(precise, rounds)
        else:
            candidates = [nonblank(row.get("Handicap")) for row in group if nonblank(row.get("Handicap"))]
            handicap = candidates[0] if candidates else "E"
        result.append(
            {
                "Name": canonical_names.get(key, nonblank(group[0].get("Name"))),
                "Points": str(points),
                "Rounds": str(rounds),
                "Handicap": handicap,
            }
        )
    return result


def _normalize_past_all_time(
    source_rows: list[dict[str, str]], canonical_names: dict[str, str]
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        name = nonblank(row.get("Name"))
        season = nonblank(row.get("Season"))
        if name and season:
            grouped[(normalized_name(name), season)].append(row)
    result: list[dict[str, str]] = []
    for (key, season), group in grouped.items():
        result.append(
            {
                "Name": canonical_names.get(key, nonblank(group[0].get("Name"))),
                "Points": str(sum(_whole(row.get("Points")) for row in group)),
                "Rounds": str(sum(_whole(row.get("Rounds")) for row in group)),
                "Season": season,
            }
        )
    return result


def _current_year(rows: dict[str, list[dict[str, str]]]) -> str:
    for row in rows.get("League Schedule", []):
        text = nonblank(row.get("Datend"))
        match = re.search(r"(?:19|20)\d{2}", text)
        if match:
            return match.group(0)
    return "Current"


def _normalize_current_all_time(
    past_rows: list[dict[str, str]],
    leaderboard_rows: list[dict[str, str]],
    canonical_names: dict[str, str],
    current_year: str,
) -> list[dict[str, str]]:
    season_facts: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for row in past_rows:
        key = normalized_name(row.get("Name", ""))
        if not key:
            continue
        season_facts[key][nonblank(row.get("Season"))] = (
            _whole(row.get("Rounds")), _whole(row.get("Points"))
        )
    for row in leaderboard_rows:
        key = normalized_name(row.get("Name", ""))
        if not key:
            continue
        season_facts[key][current_year] = (
            _whole(row.get("Rounds")), _whole(row.get("Points"))
        )

    result: list[dict[str, str]] = []
    for key, seasons in season_facts.items():
        rounds = sum(v[0] for v in seasons.values())
        points = sum(v[1] for v in seasons.values())
        result.append(
            {
                "Name": canonical_names.get(key, key),
                "Seasons": str(len(seasons)),
                "Rounds": str(rounds),
                "Points": str(points),
                "milestonen": str((points // 500) * 500) if points >= 500 else "",
            }
        )
    return result


def _normalize_poolwise(report: dict[str, Any], rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if report.get("poolwise_schema_variant") != "legacy_live":
        return [dict(row) for row in rows]
    result: list[dict[str, str]] = []
    for source in rows:
        row = dict(source)
        # IMPORTANT legacy semantics:
        #   RdNo    = monotonically increasing all-time SHAM observation sequence.
        #   RoundNo = round number within that historical season.
        # Earlier migration code overwrote RdNo with RoundNo, which collapsed
        # unrelated seasons onto the same apparent round. Preserve both exactly;
        # neither is a relational key in PostgreSQL.
        row["RdNo"] = nonblank(row.get("RdNo"))
        row["RoundNo"] = nonblank(row.get("RoundNo"))
        row["ParStrokes"] = nonblank(row.get("totalpar"))
        row["Avg"] = ""
        row["StdDev"] = ""
        result.append(row)
    return result


def normalize_rows_for_bootstrap(
    report: dict[str, Any], rows: dict[str, list[dict[str, str]]]
) -> dict[str, list[dict[str, str]]]:
    """Return a migration-normalized copy; the staged source remains unchanged."""
    normalized = {
        sheet: [dict(row) for row in sheet_rows]
        for sheet, sheet_rows in rows.items()
    }
    _normalize_round_columns(report, normalized)
    canonical_names = canonical_display_names(normalized)

    normalized["Full Season Scores"] = _merge_nonconflicting_rows(
        normalized.get("Full Season Scores", []), canonical_names
    )
    normalized["Handicap"] = _normalize_handicap(
        report, normalized.get("Handicap", []), canonical_names
    )
    normalized["Leaderboard"] = _normalize_leaderboard(
        normalized.get("Leaderboard", []), normalized["Handicap"], canonical_names
    )
    normalized["Past All Time"] = _normalize_past_all_time(
        normalized.get("Past All Time", []), canonical_names
    )
    normalized["Current All Time"] = _normalize_current_all_time(
        normalized["Past All Time"],
        normalized["Leaderboard"],
        canonical_names,
        _current_year(normalized),
    )
    normalized["Player Pool Assignments"] = _merge_nonconflicting_rows(
        normalized.get("Player Pool Assignments", []), canonical_names
    )
    normalized["Poolwise Strokes by Round"] = _normalize_poolwise(
        report, normalized.get("Poolwise Strokes by Round", [])
    )

    for sheet in ["Aces", "Course Records", "Hall of Champions"]:
        for row in normalized.get(sheet, []):
            name = nonblank(row.get("Name"))
            if name:
                row["Name"] = canonical_names.get(normalized_name(name), name)

    return normalized
