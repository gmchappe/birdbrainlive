# BirdBrain database migration

This branch preserves the current R/Shiny implementation while replacing Google Sheets with a persistent PostgreSQL database in development.

## Safety boundary

- `main` remains the current deployed/reference app.
- `legacy-r/` is a frozen copy of the current R implementation.
- The live 2026 Google Sheet is reference-only during migration and must not be mutated by migration scripts.
- `database/reference/` contains prior SQLite/schema design artifacts only; do not apply the SQLite schema directly to Supabase/PostgreSQL.
- `database/migrations/` contains the production-target PostgreSQL schema.
- `fixtures/` contains regression data for validating the migration.
- `shiny-db/` is the PostgreSQL-backed development app. It must not replace production until hosted deployment validation is complete.

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
- Local PostgreSQL-backed Shiny smoke test passed all six read contracts.
- Local PostgreSQL-backed Shiny UI was manually validated against the current live app with no read-path or pool-lifecycle errors.

Current phase:

- Provision the least-privilege `birdbrain_shiny_reader` PostgreSQL login.
- Re-run the local smoke test using that reader rather than the migration/admin role.
- Deploy `shiny-db/` as a separate BirdBrain DB development app on Posit Connect Cloud using encrypted environment variables.
- Keep the current production shinyapps.io/Google-Sheets app unchanged during hosted validation.

## Migration sequence

1. Create a development Supabase/PostgreSQL project. **Complete.**
2. Apply PostgreSQL migrations to the development database. **Complete.**
3. Capture and load an immutable read-only snapshot of the current league workbook. **Complete.**
4. Compare PostgreSQL compatibility views with the canonicalized staged outputs: schedule, leaderboard, current all-time, course records, aces, and hall of champions. **Complete.**
5. Wire and validate the PostgreSQL-backed Shiny read path locally. **Complete.**
6. Provision a least-privilege hosted Shiny database login and validate all six read contracts with it. **In progress.**
7. Deploy a separate Connect Cloud development app with encrypted DB environment variables and validate it before any production read cutover.
8. Only after the DB-backed hosted Shiny read path is validated, build transactional Round Finalizer writes.
9. Port calculation engines from R to Python incrementally, using the same PostgreSQL schema and regression fixtures.

## Credentials

Never commit database passwords, connection URLs containing passwords, Supabase service-role keys, `.env`, or `.Renviron` files.

Migration/admin utilities use:

- `BB_DB_HOST`
- `BB_DB_PORT`
- `BB_DB_NAME`
- `BB_DB_USER`
- `BB_DB_PASSWORD`
- `BB_DB_SSLMODE`

The Shiny app prefers separate least-privilege credentials:

- `BB_SHINY_DB_USER`
- `BB_SHINY_DB_PASSWORD`

Create/verify that role with:

```text
python python/birdbrain_migrate/provision_shiny_reader.py --apply
```

The provisioning utility stores the generated Shiny-only credentials in the gitignored repo-root `.env` without printing the password. Migration/admin `BB_DB_*` credentials are left unchanged.

For local development, `shiny-db/run_dev.R` and `shiny-db/smoke_test.R` may read the gitignored repo-root `.env`. A deployed app must receive credentials through encrypted runtime environment variables instead of bundling `.env` or database passwords with the source.

`shiny-db/deploy_connect_cloud.R` bundles only `ui.R`, `server.R`, and `R/db.R` and synchronizes the required `BB_*` variables to Posit Connect Cloud.

## Current production-read views

The schema exposes compatibility views intended to replace the six Google Sheet reads in the current Shiny app:

- `v_schedule`
- `v_leaderboard`
- `v_current_all_time`
- `v_course_records`
- `v_aces`
- `v_hall_of_champions`

These views are read-only application interfaces; authoritative facts live in normalized tables.
