# BirdBrain SQLite Schema v0.2 — Change Log

Reconciled against **BirdBrain Master Engine Specification v5.1** and the finalized **Schema Reconciliation Matrix v2**.

## Accepted structural changes

- Replaced league-wide membership assumptions with season-specific membership records and flags supporting tag-only members, competitors, admins, and guests.
- Included zero-round tag holders in the current standings view with zero rounds and zero points.
- Added public-profile and contact-visibility controls, including optional public PDGA number and admin-controlled public phone visibility.
- Aligned round statuses and event types with the finalized workflow; removed cancelled as a permanent round status.
- Added `results_published_at` so finalization and publication remain separate actions.
- Replaced monetary `*_cents` fields with whole-dollar integer amounts.
- Added the explicit `round_payout` fund type and renamed the former playoff-pot concept to `postseason_pot`.
- Kept regular-round payout contributions and payout transactions in the immutable finance ledger, partitioned and reconciled by `round_id`.
- Added handicap-history support and a permanent final-season standings snapshot.
- Made the final-season snapshot the authoritative source for postseason handicap and seed values.
- Represented Semifinals and Finals as linked round rows without a parent championship-event table.
- Preserved the shared, unordered Semifinalist tier for the four first-round Finals losers.
- Added views and indexes for public standings, payout history, postseason lookup, and final-season snapshots.
- Added `aces` support based on finalized hole scores of one.
- Added audit JSON storage for structured round-finalization and payout reconciliation details.

## UI-only decision

Score entry uses buttons for strokes **1 through 7**, plus a **More** option for larger positive integer scores. This requires no additional schema field.

## Intentionally deferred until R code inventory

- Exact SHAM slope, rating, weighting, and linear-model intermediate structure.
- Whether to remove the denormalized `round_results.sham_adjustment` field.
- Whether to remove or normalize the legacy `round_results.pool` field.
- Whether payout-calculation details should remain in `audit_log.details_json` or move to a dedicated payout-calculation table.

## Validation

The schema was executed against a fresh SQLite database using Python's `sqlite3` module.

- Foreign-key check: passed
- Integrity check: passed
- Created schema objects: 81
