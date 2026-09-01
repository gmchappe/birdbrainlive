-- Fix post-bootstrap parity for all-time summaries and Hall of Champions history.
-- Historical all-time output is defined by season_player_summaries, not by every
-- ancillary season membership (e.g. Hall-only memberships). Hall history is a
-- ledger and must permit multiple rows for the same player/year/event.

BEGIN;

ALTER TABLE hall_of_champions
  DROP CONSTRAINT IF EXISTS hall_of_champions_season_id_player_id_title_key;

DROP VIEW IF EXISTS v_current_all_time;

CREATE VIEW v_current_all_time AS
WITH season_rows AS (
  SELECT
    sps.season_id,
    sps.player_id,
    (
      sps.rounds +
      COUNT(rr.round_result_id) FILTER (
        WHERE rp.participant_type = 'member'
          AND rp.status <> 'removed'
          AND r.status = 'finalized'
      )
    )::integer AS rounds,
    (
      sps.points +
      COALESCE(
        SUM(rr.points) FILTER (
          WHERE rp.participant_type = 'member'
            AND rp.status <> 'removed'
            AND r.status = 'finalized'
        ),
        0
      )
    )::integer AS points
  FROM season_player_summaries sps
  LEFT JOIN rounds r
    ON r.season_id = sps.season_id
   AND sps.through_round_no IS NOT NULL
   AND r.round_no > sps.through_round_no
  LEFT JOIN round_participants rp
    ON rp.round_id = r.round_id
   AND rp.player_id = sps.player_id
  LEFT JOIN round_results rr
    ON rr.round_participant_id = rp.round_participant_id
  GROUP BY
    sps.season_id,
    sps.player_id,
    sps.rounds,
    sps.points,
    sps.through_round_no
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
