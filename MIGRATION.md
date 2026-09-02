# BirdBrain database migration

This branch preserves the current R/Shiny implementation while replacing Google Sheets with a persistent PostgreSQL database in development.

## Safety boundary

- `main` remains the current deployed/reference app.
- `legacy-r/` is a frozen copy of the current R implementation.
- The live 2026 Google Sheet is reference-only during migration and must not be mutated by migration scripts.
- `database/reference/` contains prior SQLite/schema design artifacts only; do not apply the SQLite schema directly to Supabase/PostgreSQL.
- `database/migrations/` contains the production-target PostgreSQL schema.
- `fixtures/` contains regression data for validating the migration.
- `shiny-db/` is the PostgreSQL-backed development app. It must not replace production until a deliberate production cutover is approved.

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
- Least-privilege `birdbrain_shiny_reader` PostgreSQL login provisioned and verified against all six compatibility views; direct `players` table SELECT is denied.
- Local PostgreSQL-backed Shiny smoke test passed under `birdbrain_shiny_reader` rather than the migration/admin role.
- Separate `birdbrain-db-dev` app successfully deployed to Posit Connect Cloud with encrypted database environment variables.
- Hosted `birdbrain-db-dev` UI was manually validated successfully, including reactive refresh behavior.
- Transactional Round Finalizer core implemented in Python with regression coverage for payout, DNF/guest handling, ties, handicap trimming/bounds, ace accounting, SHAM calculations, and deterministic idempotency fingerprints.
- Database finalization guard migration applied, including one native finalization receipt per round and deterministic finalizer ledger keys.
- Full finalizer write path validated against the real development database with forced rollback and zero persistent residue.
- UDisc XLSX parser and append-once persistent import boundary implemented with immutable import receipts, source/data fingerprints, audit events, and explicit no-overwrite behavior.
- UDisc import receipt migration applied to the development database.
- Persistent-import commit path validated inside an outer rollback, including preservation of pre-existing round ledger rows and identical-retry no-op behavior.
- Round 2 regression fixture reproduced end to end from the real UDisc export through PostgreSQL: 35 participants, 630 hole scores, known paid order and payouts, $35 ace-pot balance, $523 postseason balance, and the two expected 45 course records.
- Complete native persistent-path rehearsal passed: UDisc commit -> playoff resolution -> finalizer commit -> fixture reconciliation -> identical finalizer retry/no-op -> outer rollback, leaving no persistent database changes.

Current phase:

- Operationalize the first real future round on the development database using the guarded native workflow: UDisc preview, explicit import commit, results review, tie/ace resolution when needed, finalizer dry-run, and explicit finalization commit.
- Preserve the validated read-only Connect Cloud app while operator controls are developed and tested separately.
- Keep the current production shinyapps.io/Google-Sheets app unchanged until an explicit production cutover decision.

## Migration sequence

1. Create a development Supabase/PostgreSQL project. **Complete.**
2. Apply PostgreSQL migrations to the development database. **Complete.**
3. Capture and load an immutable read-only snapshot of the current league workbook. **Complete.**
4. Compare PostgreSQL compatibility views with the canonicalized staged outputs: schedule, leaderboard, current all-time, course records, aces, and hall of champions. **Complete.**
5. Wire and validate the PostgreSQL-backed Shiny read path locally. **Complete.**
6. Provision a least-privilege hosted Shiny database login and validate all six read contracts with it. **Complete.**
7. Deploy a separate Connect Cloud development app with encrypted DB environment variables and validate it before any production read cutover. **Complete.**
8. Build transactional UDisc import and Round Finalizer writes with regression coverage, idempotency receipts, and failure-safe rollback semantics. **Complete.**
9. Operationalize and validate the native workflow against the first real future round in development. **In progress.**
10. Port remaining R calculation/write workflows and application endpoints to Python incrementally, using the same PostgreSQL schema and regression fixtures.

## Native round operator boundary

The native database write path remains an administrator/development workflow until a separate authenticated API/UI is built.

- UDisc import is append-once and defaults to rollback-only dry-run.
- A persistent import requires an explicit `--commit --confirm IMPORT-R<round-no>` confirmation.
- Import and finalization are intentionally separate irreversible actions; a committed import transitions the round to `results_review`.
- Existing participant/score/result facts are never silently overwritten or merged by the importer.
- An identical committed UDisc retry returns an idempotent no-op; a changed data fingerprint is rejected.
- Round finalization defaults to rollback-only dry-run and only accepts a `results_review` round.
- A persistent finalization requires an explicit `--commit --confirm FINALIZE-R<round-no>` confirmation.
- An identical native finalization retry returns an `already-finalized` no-op; imported historical finalized rounds without native receipts cannot be replayed.
- Production Shiny and the live Google Sheet are not part of this write path.

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
