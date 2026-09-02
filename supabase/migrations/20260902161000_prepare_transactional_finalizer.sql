-- Prepare BirdBrain normalized tables/views for future transactional finalization.
-- This migration does not finalize any round or mutate historical round facts.

BEGIN;

-- One receipt per natively finalized round. The round row remains the lifecycle
-- authority; this receipt records exactly which immutable input fingerprint was
-- committed by the transactional finalizer.
CREATE TABLE IF NOT EXISTS round_finalization_receipts (
  round_id bigint PRIMARY KEY REFERENCES rounds(round_id) ON DELETE RESTRICT,
  input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
  finalizer_version text NOT NULL,
  result_count integer NOT NULL CHECK (result_count >= 0),
  financial_transaction_count integer NOT NULL CHECK (financial_transaction_count >= 0),
  handicap_adjustment_count integer NOT NULL CHECK (handicap_adjustment_count >= 0),
  ace_award_count integer NOT NULL CHECK (ace_award_count >= 0),
  course_record_count integer NOT NULL CHECK (course_record_count >= 0),
  finalized_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE round_finalization_receipts IS
  'Idempotency receipt for future BirdBrain-native round finalizations. Imported historical finalized rounds are not backfilled.';

-- Financial transactions do not otherwise have a natural uniqueness key. Every
-- finalizer-generated ledger row receives a deterministic key so an accidental
-- duplicate insert is rejected at the database boundary. Migration/opening
-- balance rows remain NULL and are preserved exactly as imported.
ALTER TABLE financial_transactions
  ADD COLUMN IF NOT EXISTS finalization_key text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_financial_transactions_finalization_key
  ON financial_transactions(finalization_key)
  WHERE finalization_key IS NOT NULL;

COMMENT ON COLUMN financial_transactions.finalization_key IS
  'Deterministic idempotency key for transactional finalizer-generated ledger rows; NULL for imported/migration history.';

-- Current standings count an appearance only for the settled eligible statuses:
-- active finishers and DNF participants. Withdrawn/disqualified/removed players
-- are excluded from field and round totals.
CREATE OR REPLACE VIEW v_leaderboard AS
SELECT
  p.player_id,
  p.display_name AS "Name",
  (
    COALESCE(sps.points, 0) +
    COALESCE(
      SUM(rr.points) FILTER (
        WHERE rp.participant_type = 'member'
          AND rp.status IN ('active','dnf')
          AND r.status = 'finalized'
      ),
      0
    )
  )::integer AS "Points",
  (
    COALESCE(sps.rounds, 0) +
    COUNT(rr.round_result_id) FILTER (
      WHERE rp.participant_type = 'member'
        AND rp.status IN ('active','dnf')
        AND r.status = 'finalized'
    )
  )::integer AS "Rounds",
  CASE
    WHEN hc.applied_handicap IS NULL OR hc.applied_handicap = 0 THEN 'E'
    WHEN hc.applied_handicap > 0 THEN '+' || hc.applied_handicap::text
    ELSE hc.applied_handicap::text
  END AS "Handicap"
FROM season_memberships sm
JOIN seasons s ON s.season_id = sm.season_id
JOIN players p ON p.player_id = sm.player_id
LEFT JOIN season_player_summaries sps
  ON sps.season_id = sm.season_id
 AND sps.player_id = sm.player_id
LEFT JOIN rounds r
  ON r.season_id = s.season_id
 AND (
      sps.season_player_summary_id IS NULL
      OR (
        sps.through_round_no IS NOT NULL
        AND r.round_no > sps.through_round_no
      )
 )
LEFT JOIN round_participants rp
  ON rp.round_id = r.round_id
 AND rp.player_id = p.player_id
LEFT JOIN round_results rr ON rr.round_participant_id = rp.round_participant_id
LEFT JOIN LATERAL (
  SELECT h.applied_handicap
  FROM handicap_calculations h
  JOIN rounds hr ON hr.round_id = h.effective_after_round_id
  WHERE h.player_id = p.player_id
    AND hr.season_id = s.season_id
  ORDER BY hr.round_no DESC, h.calculated_at DESC, h.handicap_calculation_id DESC
  LIMIT 1
) hc ON true
WHERE s.closed_at IS NULL
GROUP BY
  p.player_id,
  p.display_name,
  sps.season_player_summary_id,
  sps.points,
  sps.rounds,
  sps.through_round_no,
  hc.applied_handicap;

-- Preserve all migration baselines exactly, while allowing players whose first
-- BirdBrain season appearance occurs after the migration cutoff to enter the
-- all-time view from normalized finalized results alone.
CREATE OR REPLACE VIEW v_current_all_time AS
WITH season_sources AS (
  SELECT
    sps.season_id,
    sps.player_id,
    sps.rounds AS base_rounds,
    sps.points AS base_points,
    sps.through_round_no,
    TRUE AS has_summary
  FROM season_player_summaries sps

  UNION ALL

  SELECT DISTINCT
    r.season_id,
    rp.player_id,
    0 AS base_rounds,
    0 AS base_points,
    NULL::integer AS through_round_no,
    FALSE AS has_summary
  FROM round_participants rp
  JOIN rounds r ON r.round_id = rp.round_id
  JOIN round_results rr ON rr.round_participant_id = rp.round_participant_id
  WHERE rp.participant_type = 'member'
    AND rp.player_id IS NOT NULL
    AND rp.status IN ('active','dnf')
    AND r.status = 'finalized'
    AND NOT EXISTS (
      SELECT 1
      FROM season_player_summaries sps
      WHERE sps.season_id = r.season_id
        AND sps.player_id = rp.player_id
    )
),
season_rows AS (
  SELECT
    ss.season_id,
    ss.player_id,
    (
      ss.base_rounds +
      COUNT(rr.round_result_id) FILTER (
        WHERE rp.participant_type = 'member'
          AND rp.status IN ('active','dnf')
          AND r.status = 'finalized'
      )
    )::integer AS rounds,
    (
      ss.base_points +
      COALESCE(
        SUM(rr.points) FILTER (
          WHERE rp.participant_type = 'member'
            AND rp.status IN ('active','dnf')
            AND r.status = 'finalized'
        ),
        0
      )
    )::integer AS points
  FROM season_sources ss
  LEFT JOIN rounds r
    ON r.season_id = ss.season_id
   AND (
        (ss.has_summary AND ss.through_round_no IS NOT NULL AND r.round_no > ss.through_round_no)
        OR NOT ss.has_summary
   )
  LEFT JOIN round_participants rp
    ON rp.round_id = r.round_id
   AND rp.player_id = ss.player_id
  LEFT JOIN round_results rr
    ON rr.round_participant_id = rp.round_participant_id
  GROUP BY
    ss.season_id,
    ss.player_id,
    ss.base_rounds,
    ss.base_points,
    ss.through_round_no,
    ss.has_summary
)
SELECT
  p.display_name AS "Name",
  COUNT(sr.season_id)::integer AS "Seasons",
  COALESCE(SUM(sr.rounds), 0)::integer AS "Rounds",
  COALESCE(SUM(sr.points), 0)::integer AS "Points",
  (FLOOR(COALESCE(SUM(sr.points), 0) / 500.0) * 500)::integer AS milestonen
FROM season_rows sr
JOIN players p ON p.player_id = sr.player_id
GROUP BY p.player_id, p.display_name;

COMMIT;
