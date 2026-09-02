# BirdBrain transactional Round Finalizer contract

This document defines the write boundary for the PostgreSQL-backed Round Finalizer. It is intentionally stricter than the legacy Google Sheets workflow.

## Safety state machine

A round may be finalized only from `results_review`.

The writer must:

1. Begin one database transaction.
2. Lock the target `rounds` row with `SELECT ... FOR UPDATE`.
3. Re-read and validate the round state after acquiring the lock.
4. Refuse a round that is already `finalized`, `cancelled`, or otherwise not in `results_review`.
5. Validate every participant, score, tie resolution, ace award, contribution, and derived total before inserting any finalization rows.
6. Write all finalization facts in the same transaction.
7. Reconcile financial balances and expected row counts before changing the round status.
8. Set `rounds.status='finalized'` and `finalized_at` only as the final data-changing step.
9. Insert one `audit_events` row describing the finalized round.
10. Commit only if every preceding step succeeds; otherwise roll back the entire transaction.

Finalized regular-season rounds are immutable in the application. No in-app reopen path is permitted after postseason begins.

## Participant eligibility

The official field contains participants who:

- are `member` participants,
- actually started the round, and
- finish with status `active` or `dnf`.

Guests may have scores but are excluded from field size, points, payout contributions, payouts, SHAM, ace-pot contributions, and postseason contributions.

DNF participants remain in the official field and retain their contributions, but receive zero points, zero payout, and no SHAM adjustment.

`removed`, `withdrawn`, and `disqualified` participants are excluded from the official field. A participant removed before play must not retain contributions.

## Ranking and points

- Net score = gross score - applied handicap.
- First played season round uses applied handicap 0.
- Non-cash ties use standard competition ranking and share the maximum points for that rank.
- Any tie touching a paid cash position must have explicit playoff resolution before finalization.
- Cash-resolved ties receive distinct official finishes and therefore distinct points.
- Points = `(eligible field size - official finish + 1) * points multiplier`.
- DNF and non-eligible participants receive zero points.

## Payouts

The round purse is `eligible field size * payout_contribution`.

Normal payout bands:

| Eligible field | Shares |
|---|---|
| 1-5 | 100% |
| 6-10 | 60% / 40% |
| 11-15 | 50% / 30% / 20% |
| 16-20 | 40% / 30% / 20% / 10% |
| 21+ | 40% / 25% / 15% / 12% / 8% |

Lower shares are rounded half-up to whole dollars. First place receives the exact remainder so the purse reconciles to the dollar. If fewer cash-eligible finishers exist than normal paid positions, unavailable shares roll to first place. A positive purse with zero cash-eligible finishers is a blocking validation error.

## Contributions

For each eligible member participant:

- round payout fund: `rounds.payout_contribution`,
- ace pot: `rounds.ace_contribution`,
- postseason fund: `rounds.postseason_contribution`.

In addition, the first eligible finalized appearance of the season charges the one-time $5 postseason contribution. DNF counts as that first eligible appearance; guests do not.

Regression checks:

- Round 1: 59 eligible, $5 payout + $1 ace + $2 postseason + $5 first-season contribution -> purse $295, ace contribution $59, postseason $413.
- Round 2: 35 eligible, 15 first-season contributors -> purse $140, ace contribution $35, postseason increment $110; cumulative postseason balance $523.

## Handicap

Each finalized eligible non-DNF member receives one handicap adjustment.

- Pre-SHAM adjustment: round score relative to par.
- SHAM adjustment: `(round relative score - layout rating) * layout weight`.
- SHAM activates only after at least 11 completed league rounds and 40 unique league players.
- A player is pool-eligible after 5 completed league-history rounds.
- Handicap calculation trims `floor(n / 5)` adjustments from each end and averages the remainder.
- The precise value is retained in PostgreSQL.
- Applied handicap uses symmetric half-up rounding.
- Bounds `[-5, +8]` apply only while the player has 5 or fewer completed current-season rounds. After the sixth completed round, no bounds apply.

The handicap stored after a round is for future rounds; it never retroactively changes the applied handicap used for the round being finalized.

## Aces

An ace is inferred from a finalized hole score of 1, but the finalizer requires explicit whole-dollar award allocations before clearing the pot when multiple recipients exist.

Future native ace awards must sum exactly to the full ace pot. Historical fractional payouts remain historical data and are not rewritten.

Hole identifiers may be alphanumeric in historical data. The future native scoring schema should separate a text `hole_label` from integer `hole_order`; this finalizer phase must not guess unresolved hole labels or layout hole pars.

## Course records

Gross scores only determine course records. All players tied at the best all-time gross score for the layout remain current record holders. Historical records are preserved; current-record compatibility views select the minimum score.

## Required transaction writes

A successful finalized round may write:

- `round_results`,
- `handicap_adjustments`,
- `handicap_calculations`,
- `handicap_calculation_adjustments`,
- `financial_transactions`,
- `ace_awards`,
- `course_records`,
- `sham_pool_round_stats` / `sham_layout_models` when applicable,
- `audit_events`,
- final `rounds.status`, `rounds.finalized_at`, and publication timestamps when appropriate.

No public Shiny write control is enabled until the deterministic planner, fixture regression tests, database dry-run preflight, transaction rollback test, and least-privilege writer design all pass.
