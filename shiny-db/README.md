# PostgreSQL-backed Shiny development app

This directory is the private development copy of BirdBrain Shiny that reads from the normalized PostgreSQL/Supabase database through compatibility views. It does not replace the live Google-Sheets-backed app until the development deployment is explicitly validated.

## Safety

- The six public data tabs are read-only database queries.
- The Results Finalizer remains preview-only; it does not write round results.
- Do not deploy the repo-root `.env` or any password file.
- The live Google Sheet remains reference-only during migration.

## Local prerequisites

The existing Shiny dependencies are still required, plus:

```r
install.packages(c("DBI", "RPostgres", "pool"))
```

The repo-root `.env` may contain the same `BB_DB_*` variables used by the Python migration utilities. It is gitignored and is loaded only by the local launcher/smoke test.

## 1. Smoke-test the database read contracts

From the repository root:

```powershell
Rscript shiny-db/smoke_test.R
```

The test connects through `R/db.R`, queries all six compatibility views, verifies the column contracts expected by Shiny, prints row counts, and performs no writes.

## 2. Run the database-backed app locally

From the repository root:

```powershell
Rscript shiny-db/run_dev.R
```

This reads the local `.env` when present and launches only `shiny-db/`.

## 3. Private deployment

For a private shinyapps.io development deployment, provide the `BB_DB_*` values as deployment/runtime environment variables. Do not bundle `.env`. Keep the current production app unchanged until the private deployment has been visually and behaviorally checked against it.

## Read interfaces

- `v_schedule`
- `v_leaderboard`
- `v_current_all_time`
- `v_course_records`
- `v_aces`
- `v_hall_of_champions`

The authoritative application state remains in normalized PostgreSQL tables; these views are compatibility interfaces for the legacy Shiny UI.
