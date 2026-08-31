-- Correct SHAM historical row identity semantics.
--
-- Legacy bbsham.R meanings:
--   RdNo    = monotonically increasing all-time SHAM observation sequence.
--   RoundNo = round number within that historical season.
--
-- Historical Poolwise Strokes rows span multiple seasons, so they must not be
-- assigned to the current season or linked to the current season's round_ids.
-- PostgreSQL's surrogate primary key identifies each row. The legacy counter is
-- retained only for lineage/debugging and is not a relational key.

BEGIN;

ALTER TABLE sham_pool_round_stats
  ALTER COLUMN season_id DROP NOT NULL,
  ALTER COLUMN legacy_round_no DROP NOT NULL;

ALTER TABLE sham_pool_round_stats
  DROP CONSTRAINT IF EXISTS sham_pool_round_stats_season_id_legacy_round_no_pool_key;

COMMENT ON COLUMN sham_pool_round_stats.legacy_round_no IS
  'Legacy RdNo from bbsham.R: all-time SHAM observation sequence, not a season round number and not a relational key.';

COMMENT ON COLUMN sham_pool_round_stats.season_id IS
  'NULL for imported legacy all-time SHAM observations whose historical season is not explicitly encoded in the source row; populated for future normalized BirdBrain rows.';

COMMENT ON COLUMN sham_pool_round_stats.round_id IS
  'NULL for imported legacy all-time SHAM observations; populated only when BirdBrain has a real normalized round_id.';

-- Legacy bootstrap code supplies the current season_id and may accidentally find
-- a current round_id when an all-time RdNo happens to be <= the current schedule
-- length. A legacy RdNo marks a migration-history row, so strip those false links
-- at the database boundary. Future normalized rows should leave legacy_round_no
-- NULL and use real season_id/round_id values.
CREATE OR REPLACE FUNCTION normalize_legacy_sham_history_links()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.legacy_round_no IS NOT NULL THEN
    NEW.season_id := NULL;
    NEW.round_id := NULL;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalize_legacy_sham_history_links
  ON sham_pool_round_stats;

CREATE TRIGGER trg_normalize_legacy_sham_history_links
BEFORE INSERT OR UPDATE OF legacy_round_no, season_id, round_id
ON sham_pool_round_stats
FOR EACH ROW
EXECUTE FUNCTION normalize_legacy_sham_history_links();

-- Real normalized rows have an actual round_id. Enforce one pool observation per
-- normalized round/pool without imposing source-counter uniqueness on history.
CREATE UNIQUE INDEX IF NOT EXISTS ux_sham_pool_round_stats_round_pool
  ON sham_pool_round_stats(round_id, pool)
  WHERE round_id IS NOT NULL;

COMMIT;
