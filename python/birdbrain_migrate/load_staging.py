from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_SNAPSHOT_ROOT = Path("data/snapshots")


def connect_db() -> psycopg.Connection:
    database_url = os.getenv("BB_DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)

    required = [
        "BB_DB_HOST",
        "BB_DB_NAME",
        "BB_DB_USER",
        "BB_DB_PASSWORD",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing database environment variables: " + ", ".join(missing)
        )

    return psycopg.connect(
        host=os.environ["BB_DB_HOST"],
        port=int(os.getenv("BB_DB_PORT", "5432")),
        dbname=os.environ["BB_DB_NAME"],
        user=os.environ["BB_DB_USER"],
        password=os.environ["BB_DB_PASSWORD"],
        sslmode=os.getenv("BB_DB_SSLMODE", "require"),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_snapshot(root: Path = DEFAULT_SNAPSHOT_ROOT) -> Path:
    if not root.exists():
        raise FileNotFoundError(f"Snapshot root not found: {root}")
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and (path / "manifest.json").exists()),
        key=lambda path: path.name,
    )
    if not candidates:
        raise FileNotFoundError(f"No snapshots with manifest.json found under {root}")
    return candidates[-1]


def resolve_manifest(path: Path | None) -> Path:
    if path is None:
        path = latest_snapshot()
    if path.is_dir():
        path = path / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Snapshot manifest not found: {path}")
    return path


def load_snapshot(manifest_path: Path | None) -> int:
    manifest_path = resolve_manifest(manifest_path)
    snapshot_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("read_only") is not True:
        raise RuntimeError("Snapshot manifest is not marked read_only=true.")

    sheets = manifest.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise RuntimeError("Snapshot manifest contains no sheets.")

    conn = connect_db()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                # Loading the same captured snapshot twice is almost certainly accidental.
                cur.execute(
                    """
                    SELECT snapshot_id
                    FROM migration_staging.snapshots
                    WHERE spreadsheet_id = %s AND captured_at = %s
                    """,
                    (manifest["spreadsheet_id"], manifest["captured_at"]),
                )
                existing = cur.fetchone()
                if existing:
                    raise RuntimeError(
                        f"This snapshot is already loaded as snapshot_id={existing[0]}."
                    )

                cur.execute(
                    """
                    INSERT INTO migration_staging.snapshots
                      (spreadsheet_id, source_url, captured_at, manifest)
                    VALUES (%s, %s, %s, %s)
                    RETURNING snapshot_id
                    """,
                    (
                        manifest["spreadsheet_id"],
                        manifest["source_url"],
                        manifest["captured_at"],
                        Jsonb(manifest),
                    ),
                )
                snapshot_id = cur.fetchone()[0]

                for sheet in sheets:
                    file_path = snapshot_dir / sheet["file_name"]
                    if not file_path.exists():
                        raise FileNotFoundError(
                            f"Snapshot file missing for {sheet['sheet_name']!r}: {file_path}"
                        )

                    actual_hash = sha256_file(file_path)
                    if actual_hash != sheet["sha256"]:
                        raise RuntimeError(
                            f"SHA-256 mismatch for {sheet['sheet_name']!r}. "
                            "The snapshot changed after capture."
                        )

                    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                        reader = csv.reader(handle)
                        rows = list(reader)

                    headers = rows[0] if rows else []
                    data_rows = rows[1:] if rows else []

                    if headers != sheet["headers"]:
                        raise RuntimeError(
                            f"Header mismatch for {sheet['sheet_name']!r}."
                        )
                    if len(data_rows) != int(sheet["row_count"]):
                        raise RuntimeError(
                            f"Row-count mismatch for {sheet['sheet_name']!r}."
                        )

                    cur.execute(
                        """
                        INSERT INTO migration_staging.snapshot_sheets
                          (snapshot_id, sheet_name, file_name, row_count, headers, sha256)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            snapshot_id,
                            sheet["sheet_name"],
                            sheet["file_name"],
                            sheet["row_count"],
                            Jsonb(headers),
                            actual_hash,
                        ),
                    )

                    cur.executemany(
                        """
                        INSERT INTO migration_staging.google_sheet_rows
                          (snapshot_id, sheet_name, row_number, row_data)
                        VALUES (%s, %s, %s, %s)
                        """,
                        [
                            (
                                snapshot_id,
                                sheet["sheet_name"],
                                row_number,
                                Jsonb(row),
                            )
                            for row_number, row in enumerate(data_rows, start=2)
                        ],
                    )

        print(f"Source snapshot: {snapshot_dir}")
        return snapshot_id
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a BirdBrain Google snapshot into migration_staging."
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        type=Path,
        help=(
            "Snapshot directory or manifest.json path. If omitted, the newest "
            "snapshot under data/snapshots is used."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    snapshot_id = load_snapshot(args.snapshot)
    print(f"Loaded snapshot_id={snapshot_id} into migration_staging.")
    print("No normalized BirdBrain application tables were changed.")


if __name__ == "__main__":
    main()
