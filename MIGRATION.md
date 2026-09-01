# BirdBrain database migration

This branch preserves the current R/Shiny implementation while replacing Google Sheets with a persistent PostgreSQL database in development.

## Safety boundary

- `main` remains the current deployed/reference app.
- `legacy-r/` is a frozen copy of the current R implementation.
- The live 2026 Google Sheet is reference-only during migration and must not be mutated by migration scripts.
- `database/reference/` contains prior SQLite/schema design artifacts only; do not apply the SQLite schema directly to Supabase/PostgreSQL.
- `database/migrations/` contains the production-target PostgreSQL schema.
- `fixtures/` contains regression data for validating the migration.
- `shiny-db/` is the PostgreSQL-backed development app. It must not replace production until local/private deployment validation is complete.

## Migration status

Completed:

- Development Supabase/PostgreSQL schema and migration staging.
- Immutable Google Sheet snapshot load (`snapshot_id=1`).
- Deterministic normalization and one-shot bootstrap import.
- Historical cancelled-round cleanup for R18 and R35.
- Compatibility-view parity against the staged source:
  - League Schedule: 50 / 50
  - Leaderboard: 157 / 157
  - Current All Time: 704 / 704
  - Course Records: 80 / 80
  - Aces: 79 / 79
  - Hall of Champions: 338 / 338 complete records
- Eleven incomplete Hall of Champions source rows are retained as documented historical gaps and are not fabricated into player records.

Current phase:

- Validate the PostgreSQL-backed Shiny app locally and in a private deployment before any production read cutover.

## Migration sequence

1. Create a development Supabase/PostgreSQL project. **Complete.**
2. Apply PostgreSQL migrations to the development database. **Complete.**
3. Capture and load an immutable read-only snapshot of the current league workbook. **Complete.**
4. Compare PostgreSQL compatibility views with the canonicalized staged outputs: schedule, leaderboard, current all-time, course records, aces, and hall of champions. **Complete.**
5. Wire and validate a private Shiny development deployment against PostgreSQL using `shiny-db/R/db.R`. **In progress.**
6. Only after the DB-backed Shiny read path is validated, build transactional Round Finalizer writes.
7. Port calculation engines from R to Python incrementally, using the same PostgreSQL schema and regression fixtures.

## Credentials

Never commit database passwords, connection URLs containing passwords, Supabase service-role keys, `.env`, or `.Renviron` files. The Shiny app reads connection settings from environment variables.

Expected environment variables:

- `BB_DB_HOST`
- `BB_DB_PORT`
- `BB_DB_NAME`
- `BB_DB_USER`
- `BB_DB_PASSWORD`
- `BB_DB_SSLMODE`

For local development, `shiny-db/run_dev.R` and `shiny-db/smoke_test.R` may read the gitignored repo-root `.env`. A deployed app must receive credentials through its runtime environment instead of bundling `.env`.

## Current production-read views

The schema exposes compatibility views intended to replace the six Google Sheet reads in the current Shiny app:

- `v_schedule`
- `v_leaderboard`
- `v_current_all_time`
- `v_course_records`
- `v_aces`
- `v_hall_of_champions`

These views are read-only application interfaces; authoritative facts live in normalized tables.
