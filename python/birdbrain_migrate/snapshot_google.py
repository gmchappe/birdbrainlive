from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_SPREADSHEET_ID = "1_NvpAOZSjCd-hvwM_3MCx6DKh8aHnvdPXY-3zuKeaV0"
DEFAULT_SHEETS = [
    "League Schedule",
    "Leaderboard",
    "Handicap",
    "Full Season Scores",
    "Course Slopes and Ratings",
    "Player Pool Assignments",
    "Poolwise Strokes by Round",
    "Past All Time",
    "Current All Time",
    "Aces",
    "Course Records",
    "Hall of Champions",
]


def safe_filename(sheet_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", sheet_name.strip()).strip("_")
    return f"{name or 'sheet'}.csv"


def fetch_sheet_csv(spreadsheet_id: str, sheet_name: str, attempts: int = 5) -> bytes:
    # Google Visualization CSV export is a GET-only endpoint. This script never
    # authenticates and contains no write/edit operation against Google Sheets.
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={quote(sheet_name)}"
    )
    request = Request(url, headers={"User-Agent": "BirdBrainMigration/1.0"})

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
            if not data:
                raise RuntimeError(f"Google returned an empty export for {sheet_name!r}.")
            return data
        except Exception as exc:  # network/service errors are retried here
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))

    raise RuntimeError(
        f"Unable to read public sheet {sheet_name!r} after {attempts} attempts."
    ) from last_error


def inspect_csv(data: bytes) -> tuple[list[str], int]:
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], 0
    return rows[0], max(len(rows) - 1, 0)


def create_snapshot(
    spreadsheet_id: str,
    output_root: Path,
    sheet_names: list[str],
) -> Path:
    captured_at = datetime.now(timezone.utc)
    snapshot_dir = output_root / captured_at.strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    source_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    manifest: dict[str, object] = {
        "format_version": 1,
        "read_only": True,
        "spreadsheet_id": spreadsheet_id,
        "source_url": source_url,
        "captured_at": captured_at.isoformat(),
        "sheets": [],
    }

    used_names: set[str] = set()

    for sheet_name in sheet_names:
        file_name = safe_filename(sheet_name)
        if file_name in used_names:
            raise RuntimeError(f"Snapshot filename collision for {sheet_name!r}.")
        used_names.add(file_name)

        print(f"Reading {sheet_name} ...")
        data = fetch_sheet_csv(spreadsheet_id, sheet_name)
        headers, row_count = inspect_csv(data)
        sha256 = hashlib.sha256(data).hexdigest()

        (snapshot_dir / file_name).write_bytes(data)
        manifest["sheets"].append(
            {
                "sheet_name": sheet_name,
                "file_name": file_name,
                "headers": headers,
                "row_count": row_count,
                "sha256": sha256,
            }
        )

    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a strictly read-only CSV snapshot of BirdBrain's Google Sheet."
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=DEFAULT_SPREADSHEET_ID,
        help="Google spreadsheet ID. Defaults to the current BirdBrain workbook.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/snapshots"),
        help="Local snapshot directory. This path is gitignored.",
    )
    parser.add_argument(
        "--sheet",
        action="append",
        dest="sheets",
        help="Limit the snapshot to a named sheet. Repeat for multiple sheets.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = create_snapshot(
        spreadsheet_id=args.spreadsheet_id,
        output_root=args.output_root,
        sheet_names=args.sheets or DEFAULT_SHEETS,
    )
    print(f"\nSnapshot complete: {manifest_path}")
    print("No Google Sheet writes were performed.")


if __name__ == "__main__":
    main()
