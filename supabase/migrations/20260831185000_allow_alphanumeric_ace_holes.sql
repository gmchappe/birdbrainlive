-- UDisc hole identifiers are labels, not guaranteed integers (for example 9G or 12A).
-- Preserve historical ace hole labels exactly as text.

BEGIN;

DROP VIEW IF EXISTS v_aces;

ALTER TABLE ace_awards
  DROP CONSTRAINT IF EXISTS ace_awards_hole_number_check;

ALTER TABLE ace_awards
  ALTER COLUMN hole_number TYPE text USING hole_number::text;

ALTER TABLE ace_awards
  DROP CONSTRAINT IF EXISTS ace_awards_hole_number_nonblank;

ALTER TABLE ace_awards
  ADD CONSTRAINT ace_awards_hole_number_nonblank
  CHECK (btrim(hole_number) <> '');

CREATE VIEW v_aces AS
SELECT
  COALESCE(p.display_name, pp.display_name, rp.display_name) AS "Name",
  COALESCE(r.scheduled_date, aa.achieved_on) AS "Date",
  c.name AS "Course",
  l.name AS "Layout",
  aa.hole_number AS "Hole",
  aa.payout AS "Payout"
FROM ace_awards aa
LEFT JOIN round_participants rp
  ON rp.round_participant_id = aa.round_participant_id
LEFT JOIN players pp ON pp.player_id = rp.player_id
LEFT JOIN players p ON p.player_id = aa.player_id
LEFT JOIN rounds r ON r.round_id = aa.round_id
JOIN layouts l ON l.layout_id = COALESCE(aa.layout_id, r.layout_id)
JOIN courses c ON c.course_id = l.course_id;

COMMIT;
