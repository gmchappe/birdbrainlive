from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from core import FinalizerValidationError


_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")
_HOLE_RE = re.compile(r"^hole_(.+)$", re.IGNORECASE)


def normalized_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _col_index(cell_ref: str) -> int:
    match = _CELL_REF_RE.match(cell_ref)
    if not match:
        raise FinalizerValidationError(f"Invalid XLSX cell reference {cell_ref!r}.")
    letters = match.group(1)
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext())


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        raw = book.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    strings: list[str] = []
    for item in root.findall(f"{{{_NS_MAIN}}}si"):
        strings.append("".join(item.itertext()))
    return strings


def _first_sheet_path(book: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    sheet = workbook.find(f"{{{_NS_MAIN}}}sheets/{{{_NS_MAIN}}}sheet")
    if sheet is None:
        raise FinalizerValidationError("UDisc workbook has no worksheets.")
    rel_id = sheet.attrib.get(f"{{{_NS_REL_DOC}}}id")
    if not rel_id:
        raise FinalizerValidationError("Could not resolve the first UDisc worksheet.")

    rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall(f"{{{_NS_REL_PKG}}}Relationship"):
        if rel.attrib.get("Id") != rel_id:
            continue
        target = rel.attrib.get("Target", "")
        if target.startswith("/"):
            return target.lstrip("/")
        if target.startswith("xl/"):
            return target
        return "xl/" + target.lstrip("/")
    raise FinalizerValidationError("Could not locate the first UDisc worksheet XML.")


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _xml_text(cell.find(f"{{{_NS_MAIN}}}is"))

    value_node = cell.find(f"{{{_NS_MAIN}}}v")
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text

    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise FinalizerValidationError("Invalid shared-string reference in UDisc XLSX.") from exc
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"

    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def read_first_sheet(path: str | Path) -> list[list[Any]]:
    workbook_path = Path(path)
    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)
    if workbook_path.suffix.lower() != ".xlsx":
        raise FinalizerValidationError("UDisc import currently requires an .xlsx export.")

    try:
        with zipfile.ZipFile(workbook_path) as book:
            shared = _shared_strings(book)
            sheet_path = _first_sheet_path(book)
            root = ET.fromstring(book.read(sheet_path))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise FinalizerValidationError(f"Could not parse UDisc XLSX {workbook_path}.") from exc

    rows: list[list[Any]] = []
    sheet_data = root.find(f"{{{_NS_MAIN}}}sheetData")
    if sheet_data is None:
        return rows
    for row in sheet_data.findall(f"{{{_NS_MAIN}}}row"):
        values: dict[int, Any] = {}
        max_col = -1
        for cell in row.findall(f"{{{_NS_MAIN}}}c"):
            ref = cell.attrib.get("r", "")
            index = _col_index(ref)
            max_col = max(max_col, index)
            values[index] = _cell_value(cell, shared)
        if max_col >= 0:
            rows.append([values.get(i, "") for i in range(max_col + 1)])
    return rows


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any, *, label: str, allow_blank: bool = False) -> int | None:
    text = _as_text(value)
    if not text:
        if allow_blank:
            return None
        raise FinalizerValidationError(f"Missing integer value for {label}.")
    try:
        number = float(text)
    except ValueError as exc:
        raise FinalizerValidationError(f"Expected integer {label}; got {value!r}.") from exc
    if not number.is_integer():
        raise FinalizerValidationError(f"Expected integer {label}; got {value!r}.")
    return int(number)


@dataclass(frozen=True)
class UDiscParticipant:
    name: str
    normalized_name: str
    position: str
    status: str
    gross_score: int | None
    relative_score: int | None
    hole_scores: tuple[tuple[int, int], ...]

    @property
    def ace_holes(self) -> tuple[int, ...]:
        return tuple(hole for hole, strokes in self.hole_scores if strokes == 1)


@dataclass(frozen=True)
class UDiscRound:
    source_path: str
    participants: tuple[UDiscParticipant, ...]
    skipped_duplicates: int
    skipped_non_gen: int

    @property
    def hole_score_count(self) -> int:
        return sum(len(p.hole_scores) for p in self.participants)


def parse_udisc_xlsx(path: str | Path) -> UDiscRound:
    rows = read_first_sheet(path)
    if not rows:
        raise FinalizerValidationError("UDisc workbook is empty.")

    header_index = next(
        (i for i, row in enumerate(rows) if any(_as_text(value) for value in row)),
        None,
    )
    if header_index is None:
        raise FinalizerValidationError("UDisc workbook has no header row.")

    raw_headers = [_as_text(value) for value in rows[header_index]]
    headers = [header.casefold() for header in raw_headers]
    required = {"name", "position", "round_total_score"}
    missing = sorted(required - set(headers))
    if missing:
        raise FinalizerValidationError(
            "UDisc export is missing required columns: " + ", ".join(missing)
        )

    column = {name: headers.index(name) for name in required}
    division_col = headers.index("division") if "division" in headers else None
    relative_col = headers.index("round_relative_score") if "round_relative_score" in headers else None

    hole_columns: list[tuple[int, int]] = []
    for index, header in enumerate(headers):
        match = _HOLE_RE.match(header)
        if not match:
            continue
        label = match.group(1).strip()
        if not label.isdigit():
            raise FinalizerValidationError(
                f"UDisc hole label {label!r} is not yet supported by normalized hole_scores. "
                "Do not guess an integer mapping; hole_label/hole_order separation is still pending."
            )
        hole_columns.append((index, int(label)))
    hole_columns.sort(key=lambda item: item[1])
    if not hole_columns:
        raise FinalizerValidationError("UDisc export contains no hole_* score columns.")
    hole_numbers = [hole for _, hole in hole_columns]
    if len(hole_numbers) != len(set(hole_numbers)):
        raise FinalizerValidationError("UDisc export contains duplicate hole labels.")

    participants: list[UDiscParticipant] = []
    seen_names: set[str] = set()
    skipped_duplicates = 0
    skipped_non_gen = 0

    for raw_row in rows[header_index + 1 :]:
        row = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
        name = _as_text(row[column["name"]])
        if not name:
            continue

        division = _as_text(row[division_col]).upper() if division_col is not None else "GEN"
        if division and division != "GEN":
            skipped_non_gen += 1
            continue

        position = _as_text(row[column["position"]]).upper()
        if position == "DUP":
            skipped_duplicates += 1
            continue
        status = "dnf" if position == "DNF" else "active"

        key = normalized_name(name)
        if key in seen_names:
            raise FinalizerValidationError(
                f"UDisc export contains duplicate active rows for player {name!r}."
            )
        seen_names.add(key)

        holes: list[tuple[int, int]] = []
        for index, hole_number in hole_columns:
            value = row[index] if index < len(row) else ""
            strokes = _as_int(value, label=f"{name} hole {hole_number}", allow_blank=True)
            if strokes is None:
                continue
            if strokes <= 0:
                raise FinalizerValidationError(
                    f"{name} hole {hole_number} must be a positive integer; got {strokes}."
                )
            holes.append((hole_number, strokes))

        gross = _as_int(
            row[column["round_total_score"]],
            label=f"{name} round_total_score",
            allow_blank=(status == "dnf"),
        )
        relative = None
        if relative_col is not None:
            relative = _as_int(
                row[relative_col],
                label=f"{name} round_relative_score",
                allow_blank=True,
            )

        if status == "active":
            if len(holes) != len(hole_columns):
                raise FinalizerValidationError(
                    f"{name} is an active finisher but has {len(holes)}/{len(hole_columns)} hole scores."
                )
            hole_total = sum(strokes for _, strokes in holes)
            if gross != hole_total:
                raise FinalizerValidationError(
                    f"{name} round_total_score={gross} but hole scores sum to {hole_total}."
                )
        elif gross is not None and holes and gross != sum(strokes for _, strokes in holes):
            raise FinalizerValidationError(
                f"DNF {name} round_total_score={gross} but entered hole scores sum to "
                f"{sum(strokes for _, strokes in holes)}."
            )

        participants.append(
            UDiscParticipant(
                name=name,
                normalized_name=key,
                position=position,
                status=status,
                gross_score=gross,
                relative_score=relative,
                hole_scores=tuple(holes),
            )
        )

    if not participants:
        raise FinalizerValidationError("UDisc export contains no usable GEN participant rows.")

    return UDiscRound(
        source_path=str(Path(path)),
        participants=tuple(participants),
        skipped_duplicates=skipped_duplicates,
        skipped_non_gen=skipped_non_gen,
    )
