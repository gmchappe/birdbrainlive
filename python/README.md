# BirdBrain migration utilities

These scripts are for the one-time/staged migration from the live Google Sheets workbook to the development PostgreSQL database.

They do **not** modify the Google Sheet.

## 1. Create a read-only snapshot

From the repository root:

```bash
python -m venv .venv
```

Activate the environment, then install the database dependency:

```bash
pip install -r python/requirements.txt
```

Create the snapshot:

```bash
python python/birdbrain_migrate/snapshot_google.py
```

The script reads the public workbook through Google's CSV export endpoint and creates:

```text
data/snapshots/<UTC timestamp>/
    manifest.json
    League_Schedule.csv
    Leaderboard.csv
    ...
```

`data/snapshots/` is intentionally gitignored. Live-season snapshots should not be committed to the public repository.

## 2. Load the exact snapshot into PostgreSQL staging

Before loading, apply the staging migration in `supabase/migrations/` and set database environment variables locally.

Preferred variables:

```text
BB_DB_HOST
BB_DB_PORT
BB_DB_NAME
BB_DB_USER
BB_DB_PASSWORD
BB_DB_SSLMODE
```

Alternatively, set one private `BB_DATABASE_URL` connection string.

Never commit any of those values.

Then run:

```bash
python python/birdbrain_migrate/load_staging.py data/snapshots/<UTC timestamp>
```

The loader verifies every file's SHA-256 hash, then inserts the unchanged source rows into the private `migration_staging` schema. It does not write to normalized BirdBrain application tables.

## Why staging first?

Google Sheets is the current production source during the ongoing season. Before translating it into normalized PostgreSQL records, BirdBrain stores an exact source snapshot so that:

- the migration can be reproduced;
- row/column mappings can be audited;
- parity errors can be traced back to exact source values;
- no migration script needs to alter the live workbook.

The next migration step is a deterministic transformer from one `snapshot_id` into the normalized BirdBrain tables.
