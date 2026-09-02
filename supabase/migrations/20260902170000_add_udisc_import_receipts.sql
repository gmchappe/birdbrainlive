-- Add an idempotency/audit receipt for future persistent UDisc imports.
-- This migration does not import scores or change any round lifecycle state.

BEGIN;

CREATE TABLE IF NOT EXISTS round_udisc_import_receipts (
  round_id bigint PRIMARY KEY REFERENCES rounds(round_id) ON DELETE RESTRICT,
  data_fingerprint text NOT NULL CHECK (data_fingerprint ~ '^[0-9a-f]{64}$'),
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_filename text NOT NULL,
  importer_version text NOT NULL,
  participant_count integer NOT NULL CHECK (participant_count >= 0),
  hole_score_count integer NOT NULL CHECK (hole_score_count >= 0),
  skipped_duplicate_count integer NOT NULL DEFAULT 0 CHECK (skipped_duplicate_count >= 0),
  skipped_non_gen_count integer NOT NULL DEFAULT 0 CHECK (skipped_non_gen_count >= 0),
  imported_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE round_udisc_import_receipts IS
  'One immutable receipt per BirdBrain-native UDisc import. The receipt and staged participant/hole-score rows are committed atomically.';

COMMENT ON COLUMN round_udisc_import_receipts.data_fingerprint IS
  'Canonical fingerprint of the parsed UDisc facts bound to the target round; used for idempotent retry detection.';

COMMENT ON COLUMN round_udisc_import_receipts.source_sha256 IS
  'SHA-256 of the exact XLSX bytes used for the first committed import; retained for audit even if an equivalent workbook is retried later.';

COMMIT;
