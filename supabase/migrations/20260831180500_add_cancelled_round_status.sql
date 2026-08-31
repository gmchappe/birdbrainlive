-- BirdBrain historical round-status correction
-- Past schedule rows with no score history should be preserved as cancelled/non-played,
-- not left looking like future scheduled rounds.

BEGIN;

ALTER TABLE rounds
  DROP CONSTRAINT IF EXISTS rounds_status_check;

ALTER TABLE rounds
  ADD CONSTRAINT rounds_status_check
  CHECK (status IN (
    'scheduled',
    'check_in',
    'in_progress',
    'results_review',
    'finalized',
    'cancelled'
  ));

COMMIT;
