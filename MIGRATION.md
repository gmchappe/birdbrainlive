# BirdBrain database migration

This branch preserves the current R/Shiny implementation while replacing Google Sheets with a persistent PostgreSQL database in development.

## Safety boundary

- `main` remains the current deployed/reference app.
- `legacy-r/` is a frozen copy of the current R implementation.
- The live 2026 Google Sheet is reference-only during migration and must not be mutated by migration scripts.
- `database/reference/` contains prior SQLite/schema design artifacts only; do not apply the SQLite schema directly to Supabase/PostgreSQL.
- `database/migrations/` contains the production-target PostgreSQL schema.
- `fixtures/` contains regression data for validating the migration.

## Migration sequence

1. Create a development Supabase/PostgreSQL project.
2. Apply `database/migrations/001_initial_postgres.sql` only to the development database.
3. Export a read-only snapshot of the current league workbook and load it into development tables with a dedicated migration script.
4. Compare PostgreSQL compatibility views with the current Google Sheet outputs: schedule, leaderboard, current all-time, course records, aces, and hall of champions.
5. Wire a private Shiny development deployment to PostgreSQL using `shiny-db/R/db.R`.
6. Only after view parity is verified, build transactional Round Finalizer writes.
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

## Current production-read views

The initial schema exposes compatibility views intended to replace the six Google Sheet reads in the current Shiny app:

- `v_schedule`
- `v_leaderboard`
- `v_current_all_time`
- `v_course_records`
- `v_aces`
- `v_hall_of_champions`

These views are read-only application interfaces; authoritative facts live in normalized tables.
