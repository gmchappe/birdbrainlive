# BirdBrain migration utilities

These scripts perform the staged migration from the ongoing read-only Google Sheets workbook to the BirdBrain development PostgreSQL database.

They do **not** modify the Google Sheet.

## Local setup

From the repository root, create the virtual environment if you have not already:

```powershell
py -m venv .venv
```

Install/update dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r python\requirements.txt
```

Copy the safe example configuration:

```powershell
Copy-Item .env.example .env
```

Open `.env` locally and replace the placeholder with the PostgreSQL URI shown by Supabase's **Connect** dialog for the BirdBrain **development** project. `.env` is gitignored and must never be committed.

## Migration CLI

All database migration operations should now use:

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py <command>
```

The CLI loads `.env` automatically.

### 1. Capture the live Sheet read-only

The snapshot utility is deliberately separate because it does not need database credentials:

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\snapshot_google.py
```

It uses Google's GET-only CSV export endpoint and writes a timestamped folder under `data/snapshots/`. That directory is gitignored.

### 2. Load the newest snapshot into private staging

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py load-staging
```

The loader verifies SHA-256 hashes and writes only to `migration_staging`. It refuses to load the identical captured snapshot twice.

### 3. Validate the staged source contract

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py analyze
```

This validates:

- required sheet/header contracts;
- schedule-to-score-history round column mapping;
- schedule-to-handicap-history mapping;
- duplicate player identities in authoritative sheets;
- current roster/history count diagnostics.

No normalized tables are changed. A detailed JSON report is written to the gitignored `data/reports/` directory.

### 4. Preview the normalized bootstrap

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py bootstrap
```

Dry-run is the default. It reports the number of players, rounds, historical results, handicap adjustments, records, aces, etc. that would be imported.

The current Leaderboard is intentionally preserved as a migration baseline through the latest completed round. Detailed historical gross-score and handicap facts are imported, but the migration does not fabricate missing historical playoff-resolution detail.

### 5. Apply the normalized bootstrap

Only after the analysis and dry-run are clean:

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py bootstrap --apply
```

The bootstrap is one-shot and transaction-protected. It refuses to merge into a target where normalized league rows already exist.

### 6. Prove public-output parity

```powershell
.\.venv\Scripts\python.exe python\birdbrain_migrate\cli.py parity
```

This compares PostgreSQL compatibility views against the exact staged Google snapshot for:

- League Schedule
- Leaderboard
- Current All Time
- Course Records
- Aces
- Hall of Champions

The Shiny development app should not be wired to PostgreSQL until all six checks pass.

## Migration architecture

```text
Live Google Sheet (read-only)
        |
        v
local timestamped snapshot
        |
        v
migration_staging (exact rows)
        |
        v
normalized BirdBrain tables
        |
        v
compatibility views
        |
        v
parity check against source snapshot
```

This separation keeps the ongoing season safe and makes every transformation auditable.
