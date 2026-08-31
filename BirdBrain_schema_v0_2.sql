-- BirdBrain SQLite schema
-- Version 0.2
-- Reconciled against BirdBrain Master Engine Specification v5.1
-- SQLite 3.x
--
-- Deferred by design:
--   1. Exact SHAM slope/rating/linear-model intermediate structure.
--   2. Removal of round_results.sham_adjustment.
--   3. Removal or normalization of round_results.pool.
--   4. Dedicated payout-calculation table; v0.2 uses audit_log.details_json pending R code inventory.
--
-- Monetary fields store whole-dollar INTEGER amounts.
-- Financial transaction convention: contributions/inflows are positive;
-- payouts/outflows are negative. Competitive result payout_amount fields are
-- stored as positive award amounts.

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ============================================================
-- IDENTITY AND SEASON MEMBERSHIP
-- ============================================================

CREATE TABLE leagues (
    league_id      INTEGER PRIMARY KEY,
    league_name    TEXT NOT NULL,
    short_name     TEXT NOT NULL UNIQUE,
    created_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE players (
    player_id              INTEGER PRIMARY KEY,
    display_name           TEXT NOT NULL,
    username               TEXT,
    password_hash          TEXT,
    email                  TEXT,
    phone                  TEXT,
    pdga_number            INTEGER,
    public_profile_visible INTEGER NOT NULL DEFAULT 1
                           CHECK (public_profile_visible IN (0, 1)),
    active                 INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at             TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at             TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    CHECK (
        (username IS NULL AND password_hash IS NULL)
        OR
        (username IS NOT NULL AND password_hash IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_players_username
    ON players(username)
    WHERE username IS NOT NULL;

CREATE UNIQUE INDEX ux_players_email
    ON players(email)
    WHERE email IS NOT NULL;

CREATE UNIQUE INDEX ux_players_pdga_number
    ON players(pdga_number)
    WHERE pdga_number IS NOT NULL;

CREATE TABLE seasons (
    season_id                  INTEGER PRIMARY KEY,
    league_id                  INTEGER NOT NULL,
    season_name                TEXT NOT NULL,
    start_date                 TEXT NOT NULL,
    end_date                   TEXT,
    status                     TEXT NOT NULL DEFAULT 'planned'
                               CHECK (status IN (
                                   'planned',
                                   'active',
                                   'regular_season_closed',
                                   'postseason_active',
                                   'completed',
                                   'archived'
                               )),
    postseason_field_locked_at TEXT,
    created_at                 TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at                 TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, season_name),
    CHECK (end_date IS NULL OR end_date >= start_date)
);

-- Only one season may be operational for a league at a time.
CREATE UNIQUE INDEX ux_one_operational_season_per_league
    ON seasons(league_id)
    WHERE status IN ('active', 'regular_season_closed', 'postseason_active');

CREATE TABLE season_memberships (
    season_membership_id       INTEGER PRIMARY KEY,
    season_id                  INTEGER NOT NULL,
    player_id                  INTEGER NOT NULL,
    tag_purchased              INTEGER NOT NULL DEFAULT 0
                               CHECK (tag_purchased IN (0, 1)),
    is_guest                   INTEGER NOT NULL DEFAULT 0
                               CHECK (is_guest IN (0, 1)),
    is_admin                   INTEGER NOT NULL DEFAULT 0
                               CHECK (is_admin IN (0, 1)),
    default_captain_volunteer  INTEGER NOT NULL DEFAULT 0
                               CHECK (default_captain_volunteer IN (0, 1)),
    public_phone_visible       INTEGER NOT NULL DEFAULT 0
                               CHECK (public_phone_visible IN (0, 1)),
    status                     TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active', 'inactive', 'pending')),
    joined_at                  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    left_at                    TEXT,
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, player_id),
    CHECK (left_at IS NULL OR left_at >= joined_at),
    CHECK (NOT (is_guest = 1 AND tag_purchased = 1)),
    CHECK (is_admin = 0 OR is_guest = 0),
    CHECK (public_phone_visible = 0 OR is_admin = 1)
);

-- A historical player may exist without login credentials.
-- account_registration claims add credentials to a historical identity.
-- guest_history_merge claims merge a guest identity into an existing player.
CREATE TABLE player_claims (
    claim_id                         INTEGER PRIMARY KEY,
    claim_type                       TEXT NOT NULL DEFAULT 'account_registration'
                                     CHECK (claim_type IN (
                                         'account_registration',
                                         'guest_history_merge'
                                     )),
    source_player_id                 INTEGER NOT NULL,
    merge_target_player_id           INTEGER,
    requested_username               TEXT,
    requested_email                  TEXT,
    requested_password_hash          TEXT,
    supplied_pdga_number             INTEGER,
    status                           TEXT NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'approved', 'rejected')),
    auto_matched                     INTEGER NOT NULL DEFAULT 0
                                     CHECK (auto_matched IN (0, 1)),
    submitted_at                     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    reviewed_at                      TEXT,
    reviewed_by_season_membership_id INTEGER,
    FOREIGN KEY (source_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (merge_target_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (reviewed_by_season_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CHECK (
        (claim_type = 'account_registration'
         AND requested_username IS NOT NULL
         AND requested_password_hash IS NOT NULL
         AND merge_target_player_id IS NULL)
        OR
        (claim_type = 'guest_history_merge'
         AND merge_target_player_id IS NOT NULL
         AND merge_target_player_id <> source_player_id)
    )
);

CREATE UNIQUE INDEX ux_pending_claim_per_source_player
    ON player_claims(source_player_id)
    WHERE status = 'pending';

-- ============================================================
-- LEAGUE CONFIGURATION
-- ============================================================

CREATE TABLE league_settings (
    league_id                           INTEGER PRIMARY KEY,
    handicap_method                     TEXT NOT NULL DEFAULT 'sham',
    handicap_low                        REAL NOT NULL,
    handicap_high                       REAL NOT NULL,
    slope_activation_rounds             INTEGER NOT NULL DEFAULT 11
                                        CHECK (slope_activation_rounds > 0),
    slope_activation_unique_players     INTEGER NOT NULL DEFAULT 40
                                        CHECK (slope_activation_unique_players > 0),
    handicap_minimum_rounds             INTEGER NOT NULL DEFAULT 5
                                        CHECK (handicap_minimum_rounds > 0),
    handicap_trim_fraction              REAL NOT NULL DEFAULT 0.20
                                        CHECK (
                                            handicap_trim_fraction >= 0
                                            AND handicap_trim_fraction < 0.50
                                        ),
    default_card_size                   INTEGER NOT NULL DEFAULT 4
                                        CHECK (default_card_size IN (3, 4)),
    postseason_qualification_points     INTEGER NOT NULL DEFAULT 300
                                        CHECK (postseason_qualification_points >= 0),
    seasonal_postseason_contribution    INTEGER NOT NULL DEFAULT 5
                                        CHECK (seasonal_postseason_contribution >= 0),
    single_postseason_contribution      INTEGER NOT NULL DEFAULT 1
                                        CHECK (single_postseason_contribution >= 0),
    double_postseason_contribution      INTEGER NOT NULL DEFAULT 2
                                        CHECK (double_postseason_contribution >= 0),
    created_at                          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at                          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CHECK (handicap_low <= handicap_high)
);

CREATE TABLE payout_scales (
    payout_scale_id INTEGER PRIMARY KEY,
    league_id       INTEGER NOT NULL,
    scale_name      TEXT NOT NULL,
    minimum_players INTEGER NOT NULL CHECK (minimum_players > 0),
    maximum_players INTEGER
                    CHECK (
                        maximum_players IS NULL
                        OR maximum_players >= minimum_players
                    ),
    places_paid     INTEGER NOT NULL CHECK (places_paid > 0),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, scale_name, minimum_players)
);

CREATE TABLE payout_distributions (
    payout_scale_id INTEGER NOT NULL,
    place           INTEGER NOT NULL CHECK (place > 0),
    payout_fraction REAL NOT NULL
                    CHECK (payout_fraction > 0 AND payout_fraction <= 1),
    PRIMARY KEY (payout_scale_id, place),
    FOREIGN KEY (payout_scale_id) REFERENCES payout_scales(payout_scale_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

-- The finalization engine must verify that the fractions for the chosen
-- active scale sum to exactly 1.0 before calculating payouts.

CREATE TABLE payout_contribution_rules (
    payout_rule_id     INTEGER PRIMARY KEY,
    league_id          INTEGER NOT NULL,
    rule_name          TEXT NOT NULL,
    contribution_amount INTEGER NOT NULL CHECK (contribution_amount >= 0),
    active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, rule_name)
);

-- ============================================================
-- COURSES AND LAYOUTS
-- ============================================================

CREATE TABLE courses (
    course_id      INTEGER PRIMARY KEY,
    league_id      INTEGER NOT NULL,
    course_name    TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, course_name)
);

CREATE TABLE layouts (
    layout_id       INTEGER PRIMARY KEY,
    course_id       INTEGER NOT NULL,
    layout_name     TEXT NOT NULL,
    layout_version  INTEGER NOT NULL DEFAULT 1 CHECK (layout_version > 0),
    is_temporary    INTEGER NOT NULL DEFAULT 0 CHECK (is_temporary IN (0, 1)),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (course_id, layout_name, layout_version)
);

CREATE TABLE holes (
    hole_id         INTEGER PRIMARY KEY,
    layout_id       INTEGER NOT NULL,
    hole_number     TEXT NOT NULL,
    display_order   INTEGER NOT NULL CHECK (display_order > 0),
    par             INTEGER NOT NULL CHECK (par > 0),
    distance        REAL CHECK (distance IS NULL OR distance > 0),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (layout_id) REFERENCES layouts(layout_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (layout_id, display_order),
    UNIQUE (layout_id, hole_number)
);

CREATE TABLE starting_positions (
    starting_position_id INTEGER PRIMARY KEY,
    layout_id             INTEGER NOT NULL,
    hole_id               INTEGER NOT NULL,
    priority_rank         INTEGER NOT NULL CHECK (priority_rank > 0),
    admin_preferred       INTEGER NOT NULL DEFAULT 0
                          CHECK (admin_preferred IN (0, 1)),
    active                INTEGER NOT NULL DEFAULT 1
                          CHECK (active IN (0, 1)),
    FOREIGN KEY (layout_id) REFERENCES layouts(layout_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (hole_id) REFERENCES holes(hole_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (layout_id, hole_id),
    UNIQUE (layout_id, priority_rank)
);

-- ============================================================
-- COMPETITION
-- ============================================================

CREATE TABLE rounds (
    round_id                         INTEGER PRIMARY KEY,
    season_id                        INTEGER NOT NULL,
    layout_id                        INTEGER,
    payout_rule_id                   INTEGER,
    round_number                     INTEGER NOT NULL CHECK (round_number > 0),
    round_name                       TEXT,
    round_date                       TEXT NOT NULL,
    check_in_open                    TEXT,
    check_in_close                   TEXT,
    status                           TEXT NOT NULL DEFAULT 'draft'
                                     CHECK (status IN (
                                         'draft',
                                         'scheduled',
                                         'check_in_open',
                                         'cards_locked',
                                         'live',
                                         'scoring_closed',
                                         'results_review',
                                         'finalized',
                                         'archived'
                                     )),
    round_type                       TEXT NOT NULL DEFAULT 'regular'
                                     CHECK (round_type IN (
                                         'regular',
                                         'saturday',
                                         'double_points',
                                         'playoff_round_1',
                                         'playoff_round_2',
                                         'semifinal',
                                         'finals',
                                         'special'
                                     )),
    standings_points_multiplier      INTEGER NOT NULL DEFAULT 1
                                     CHECK (
                                         standings_points_multiplier IN (0, 1, 2)
                                     ),
    notes                            TEXT,
    results_published_at             TEXT,
    results_published_by_membership_id INTEGER,
    created_at                       TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at                       TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (layout_id) REFERENCES layouts(layout_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (payout_rule_id)
        REFERENCES payout_contribution_rules(payout_rule_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (results_published_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE (season_id, round_number),
    CHECK (
        (round_type IN ('regular', 'saturday')
         AND standings_points_multiplier = 1)
        OR
        (round_type = 'double_points'
         AND standings_points_multiplier = 2)
        OR
        (round_type IN (
            'playoff_round_1',
            'playoff_round_2',
            'semifinal',
            'finals'
        ) AND standings_points_multiplier = 0)
        OR
        round_type = 'special'
    ),
    CHECK (
        results_published_at IS NULL
        OR status IN ('finalized', 'archived')
    )
);

-- A null layout is reserved for secret postseason layouts before disclosure.
-- Regular unknown schedule entries should use a designated TBD layout.
CREATE TRIGGER trg_round_requires_layout_before_start
BEFORE UPDATE OF status ON rounds
FOR EACH ROW
WHEN NEW.status IN (
    'check_in_open',
    'cards_locked',
    'live',
    'scoring_closed',
    'results_review',
    'finalized',
    'archived'
) AND NEW.layout_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'A round must have a layout before it can start.');
END;

-- BirdBrain does not reopen finalized regular-season rounds once postseason
-- begins. Later historical corrections are recorded offline or as audit notes.
CREATE TRIGGER trg_block_regular_round_reopen_after_postseason
BEFORE UPDATE OF status ON rounds
FOR EACH ROW
WHEN OLD.status IN ('finalized', 'archived')
 AND NEW.status NOT IN ('finalized', 'archived')
 AND OLD.round_type IN ('regular', 'saturday', 'double_points')
 AND EXISTS (
     SELECT 1
     FROM seasons s
     WHERE s.season_id = OLD.season_id
       AND s.status IN ('postseason_active', 'completed', 'archived')
 )
BEGIN
    SELECT RAISE(ABORT, 'Regular-season rounds cannot be reopened after postseason begins.');
END;

CREATE TABLE round_participants (
    participant_id        INTEGER PRIMARY KEY,
    round_id              INTEGER NOT NULL,
    season_membership_id  INTEGER NOT NULL,
    checked_in_at         TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    captain_volunteer     INTEGER NOT NULL DEFAULT 0
                          CHECK (captain_volunteer IN (0, 1)),
    status                TEXT NOT NULL DEFAULT 'checked_in'
                          CHECK (status IN (
                              'checked_in',
                              'completed',
                              'withdrawn',
                              'dnf',
                              'disqualified'
                          )),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (round_id, season_membership_id)
);

CREATE TABLE cards (
    card_id                         INTEGER PRIMARY KEY,
    round_id                        INTEGER NOT NULL,
    card_number                     INTEGER NOT NULL CHECK (card_number > 0),
    starting_position_id            INTEGER NOT NULL,
    scorecard_status                TEXT NOT NULL DEFAULT 'not_started'
                                    CHECK (scorecard_status IN (
                                        'not_started',
                                        'in_progress',
                                        'submitted',
                                        'admin_resolved'
                                    )),
    revision_number                 INTEGER NOT NULL DEFAULT 0
                                    CHECK (revision_number >= 0),
    submitted_at                    TEXT,
    submitted_by_player_id          INTEGER,
    reopened_at                     TEXT,
    reopened_by_membership_id       INTEGER,
    created_at                      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at                      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (starting_position_id)
        REFERENCES starting_positions(starting_position_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (submitted_by_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (reopened_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE (round_id, card_number),
    UNIQUE (round_id, starting_position_id)
);

CREATE TABLE card_assignments (
    assignment_id   INTEGER PRIMARY KEY,
    card_id         INTEGER NOT NULL,
    participant_id  INTEGER NOT NULL,
    is_captain      INTEGER NOT NULL DEFAULT 0 CHECK (is_captain IN (0, 1)),
    assigned_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (card_id) REFERENCES cards(card_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (participant_id) REFERENCES round_participants(participant_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (participant_id)
);

CREATE UNIQUE INDEX ux_one_captain_per_card
    ON card_assignments(card_id)
    WHERE is_captain = 1;


-- Enforce that a round participant belongs to the same season as the round.
CREATE TRIGGER trg_round_participant_season_match_insert
BEFORE INSERT ON round_participants
FOR EACH ROW
WHEN (
    SELECT season_id FROM rounds WHERE round_id = NEW.round_id
) <> (
    SELECT season_id
    FROM season_memberships
    WHERE season_membership_id = NEW.season_membership_id
)
BEGIN
    SELECT RAISE(ABORT, 'Round participant membership must belong to the round season.');
END;

CREATE TRIGGER trg_round_participant_season_match_update
BEFORE UPDATE OF round_id, season_membership_id ON round_participants
FOR EACH ROW
WHEN (
    SELECT season_id FROM rounds WHERE round_id = NEW.round_id
) <> (
    SELECT season_id
    FROM season_memberships
    WHERE season_membership_id = NEW.season_membership_id
)
BEGIN
    SELECT RAISE(ABORT, 'Round participant membership must belong to the round season.');
END;

-- ============================================================
-- CARDING PREFERENCES AND REQUESTS
-- ============================================================

CREATE TABLE player_card_preferences (
    preference_id       INTEGER PRIMARY KEY,
    league_id           INTEGER NOT NULL,
    player_id           INTEGER NOT NULL,
    related_player_id   INTEGER NOT NULL,
    preference_type     TEXT NOT NULL
                        CHECK (preference_type IN ('prefer', 'avoid')),
    priority_rank       INTEGER NOT NULL DEFAULT 1 CHECK (priority_rank > 0),
    active              INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (related_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, player_id, related_player_id, preference_type),
    CHECK (player_id <> related_player_id)
);

CREATE TABLE round_requests (
    request_id          INTEGER PRIMARY KEY,
    round_id            INTEGER NOT NULL,
    player_id           INTEGER NOT NULL,
    related_player_id   INTEGER,
    request_type        TEXT NOT NULL
                        CHECK (request_type IN (
                            'prefer',
                            'avoid',
                            'early_start',
                            'late_start',
                            'other'
                        )),
    request_note        TEXT,
    priority_rank       INTEGER NOT NULL DEFAULT 1 CHECK (priority_rank > 0),
    created_at          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (related_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CHECK (related_player_id IS NULL OR player_id <> related_player_id)
);

-- ============================================================
-- SCORING AND RESULTS
-- ============================================================

CREATE TABLE hole_scores (
    score_id            INTEGER PRIMARY KEY,
    assignment_id       INTEGER NOT NULL,
    hole_id              INTEGER NOT NULL,
    strokes              INTEGER NOT NULL CHECK (strokes > 0),
    entered_by_player_id INTEGER NOT NULL,
    entered_at           TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at           TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (assignment_id) REFERENCES card_assignments(assignment_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (hole_id) REFERENCES holes(hole_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (entered_by_player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (assignment_id, hole_id)
);

-- UI note: the mobile score entry control should offer stroke buttons 1-7
-- plus a More option. The database continues to store the actual positive
-- integer stroke count and does not require par-relative storage.

CREATE TRIGGER trg_prevent_score_insert_after_finalization
BEFORE INSERT ON hole_scores
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM card_assignments ca
    JOIN cards c ON c.card_id = ca.card_id
    JOIN rounds r ON r.round_id = c.round_id
    WHERE ca.assignment_id = NEW.assignment_id
      AND r.status IN ('finalized', 'archived')
)
BEGIN
    SELECT RAISE(ABORT, 'Scores cannot be added after round finalization.');
END;

CREATE TRIGGER trg_prevent_score_update_after_finalization
BEFORE UPDATE ON hole_scores
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM card_assignments ca
    JOIN cards c ON c.card_id = ca.card_id
    JOIN rounds r ON r.round_id = c.round_id
    WHERE ca.assignment_id = OLD.assignment_id
      AND r.status IN ('finalized', 'archived')
)
BEGIN
    SELECT RAISE(ABORT, 'Scores cannot be edited after round finalization.');
END;

CREATE TRIGGER trg_prevent_score_delete_after_finalization
BEFORE DELETE ON hole_scores
FOR EACH ROW
WHEN EXISTS (
    SELECT 1
    FROM card_assignments ca
    JOIN cards c ON c.card_id = ca.card_id
    JOIN rounds r ON r.round_id = c.round_id
    WHERE ca.assignment_id = OLD.assignment_id
      AND r.status IN ('finalized', 'archived')
)
BEGIN
    SELECT RAISE(ABORT, 'Scores cannot be deleted after round finalization.');
END;

CREATE TABLE round_results (
    result_id         INTEGER PRIMARY KEY,
    participant_id    INTEGER NOT NULL UNIQUE,
    raw_score         INTEGER CHECK (raw_score IS NULL OR raw_score > 0),
    score_to_par      INTEGER,
    sham_adjustment   REAL,  -- deferred denormalized field review
    handicap_used     INTEGER,
    net_score         INTEGER,
    calculated_rank   INTEGER CHECK (
                          calculated_rank IS NULL OR calculated_rank > 0
                      ),
    official_finish   INTEGER CHECK (
                          official_finish IS NULL OR official_finish > 0
                      ),
    points            INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    payout_amount     INTEGER NOT NULL DEFAULT 0 CHECK (payout_amount >= 0),
    pool              TEXT,  -- retained pending SHAM/code inventory
    result_version    INTEGER NOT NULL DEFAULT 1 CHECK (result_version > 0),
    finalized_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (participant_id) REFERENCES round_participants(participant_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE finish_resolutions (
    resolution_id                    INTEGER PRIMARY KEY,
    round_id                         INTEGER NOT NULL,
    participant_id                   INTEGER NOT NULL,
    resolution_group                 TEXT NOT NULL,
    resolution_type                  TEXT NOT NULL
                                     CHECK (resolution_type IN (
                                         'cash_position',
                                         'playoff_advancement',
                                         'championship_match',
                                         'final_placement',
                                         'other'
                                     )),
    resolved_order                   INTEGER NOT NULL CHECK (resolved_order > 0),
    notes                            TEXT,
    resolved_by_membership_id        INTEGER NOT NULL,
    resolved_at                      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (participant_id) REFERENCES round_participants(participant_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (resolved_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (round_id, resolution_group, participant_id),
    UNIQUE (round_id, resolution_group, resolved_order)
);

-- ============================================================
-- HANDICAP HISTORY
-- ============================================================

CREATE TABLE handicap_adjustments (
    adjustment_id       INTEGER PRIMARY KEY,
    season_id           INTEGER NOT NULL,
    player_id           INTEGER NOT NULL,
    round_id            INTEGER NOT NULL,
    adjustment_value    REAL NOT NULL,
    included_in_average INTEGER NOT NULL CHECK (included_in_average IN (0, 1)),
    trim_side           TEXT CHECK (
                            trim_side IN ('high', 'low') OR trim_side IS NULL
                        ),
    calculated_at       TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, player_id, round_id)
);

CREATE TABLE player_handicap_history (
    handicap_history_id     INTEGER PRIMARY KEY,
    season_id               INTEGER NOT NULL,
    player_id               INTEGER NOT NULL,
    effective_after_round_id INTEGER,
    precise_handicap        REAL NOT NULL,
    applied_handicap        INTEGER NOT NULL,
    source_type             TEXT NOT NULL
                            CHECK (source_type IN (
                                'season_first_round_zero',
                                'round_finalization',
                                'season_close_snapshot',
                                'historical_import'
                            )),
    calculated_at           TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (effective_after_round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, player_id, effective_after_round_id)
);

CREATE UNIQUE INDEX ux_handicap_history_season_start
    ON player_handicap_history(season_id, player_id)
    WHERE effective_after_round_id IS NULL;

-- ============================================================
-- POSTSEASON AND FINAL SEASON SNAPSHOTS
-- ============================================================

CREATE TABLE postseason_entries (
    postseason_entry_id INTEGER PRIMARY KEY,
    season_id           INTEGER NOT NULL,
    player_id           INTEGER NOT NULL,
    qualification_points INTEGER NOT NULL CHECK (qualification_points >= 0),
    regular_season_seed INTEGER CHECK (
                            regular_season_seed IS NULL
                            OR regular_season_seed > 0
                        ),
    qualified_at        TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, player_id)
);

CREATE TABLE playoff_aggregate_results (
    season_id                INTEGER NOT NULL,
    player_id                INTEGER NOT NULL,
    round_1_net_score        INTEGER,
    round_2_net_score        INTEGER,
    cumulative_net_score     INTEGER,
    eligible_for_round_2     INTEGER NOT NULL DEFAULT 1
                             CHECK (eligible_for_round_2 IN (0, 1)),
    eligible_for_advancement INTEGER NOT NULL DEFAULT 1
                             CHECK (eligible_for_advancement IN (0, 1)),
    calculated_rank          INTEGER CHECK (
                                 calculated_rank IS NULL
                                 OR calculated_rank > 0
                             ),
    official_finish          INTEGER CHECK (
                                 official_finish IS NULL
                                 OR official_finish > 0
                             ),
    advanced_to_top_8        INTEGER NOT NULL DEFAULT 0
                             CHECK (advanced_to_top_8 IN (0, 1)),
    PRIMARY KEY (season_id, player_id),
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CHECK (
        cumulative_net_score IS NULL
        OR (
            round_1_net_score IS NOT NULL
            AND round_2_net_score IS NOT NULL
            AND cumulative_net_score = round_1_net_score + round_2_net_score
        )
    ),
    CHECK (advanced_to_top_8 = 0 OR eligible_for_advancement = 1)
);

-- This table models the four Semifinal pairings only.
-- The Finals use the ordinary rounds/round_results structure.
CREATE TABLE championship_matchups (
    matchup_id             INTEGER PRIMARY KEY,
    season_id              INTEGER NOT NULL,
    round_id               INTEGER NOT NULL,
    higher_seed_player_id  INTEGER NOT NULL,
    lower_seed_player_id   INTEGER NOT NULL,
    higher_seed            INTEGER NOT NULL CHECK (higher_seed > 0),
    lower_seed             INTEGER NOT NULL CHECK (lower_seed > 0),
    winner_player_id       INTEGER,
    decided_at             TEXT,
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id, higher_seed_player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id, lower_seed_player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id, winner_player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, higher_seed),
    UNIQUE (season_id, lower_seed),
    CHECK (higher_seed_player_id <> lower_seed_player_id),
    CHECK (
        winner_player_id IS NULL
        OR winner_player_id IN (
            higher_seed_player_id,
            lower_seed_player_id
        )
    )
);

CREATE TABLE season_standings_snapshots (
    snapshot_id                   INTEGER PRIMARY KEY,
    season_id                     INTEGER NOT NULL,
    player_id                     INTEGER NOT NULL,
    regular_points                INTEGER NOT NULL DEFAULT 0
                                  CHECK (regular_points >= 0),
    regular_season_rank           INTEGER CHECK (
                                      regular_season_rank IS NULL
                                      OR regular_season_rank > 0
                                  ),
    rounds_played                 INTEGER NOT NULL DEFAULT 0
                                  CHECK (rounds_played >= 0),
    wins                          INTEGER NOT NULL DEFAULT 0 CHECK (wins >= 0),
    top_three_finishes            INTEGER NOT NULL DEFAULT 0
                                  CHECK (top_three_finishes >= 0),
    cash_finishes                 INTEGER NOT NULL DEFAULT 0
                                  CHECK (cash_finishes >= 0),
    season_payout_amount          INTEGER NOT NULL DEFAULT 0
                                  CHECK (season_payout_amount >= 0),
    final_precise_handicap        REAL,
    final_applied_handicap        INTEGER,
    qualified_for_postseason      INTEGER NOT NULL DEFAULT 0
                                  CHECK (qualified_for_postseason IN (0, 1)),
    regular_season_seed           INTEGER CHECK (
                                      regular_season_seed IS NULL
                                      OR regular_season_seed > 0
                                  ),
    postseason_tier               TEXT NOT NULL DEFAULT 'none'
                                  CHECK (postseason_tier IN (
                                      'champion',
                                      'runner_up',
                                      'third',
                                      'fourth',
                                      'semifinalist',
                                      'none'
                                  )),
    final_placement               INTEGER CHECK (
                                      final_placement IS NULL
                                      OR final_placement BETWEEN 1 AND 4
                                  ),
    seed_tiebreak_type            TEXT CHECK (
                                      seed_tiebreak_type IS NULL
                                      OR seed_tiebreak_type IN (
                                          'points',
                                          'handicap',
                                          'manual'
                                      )
                                  ),
    seed_resolution_note          TEXT,
    seed_resolved_by_membership_id INTEGER,
    snapshot_at                   TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (seed_resolved_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE (season_id, player_id),
    CHECK (
        (final_placement = 1 AND postseason_tier = 'champion')
        OR (final_placement = 2 AND postseason_tier = 'runner_up')
        OR (final_placement = 3 AND postseason_tier = 'third')
        OR (final_placement = 4 AND postseason_tier = 'fourth')
        OR (final_placement IS NULL
            AND postseason_tier IN ('semifinalist', 'none'))
    )
);

CREATE TABLE season_awards (
    season_award_id          INTEGER PRIMARY KEY,
    season_id                INTEGER NOT NULL,
    recipient_player_id      INTEGER NOT NULL,
    award_type               TEXT NOT NULL,
    award_title              TEXT NOT NULL,
    source_entity_type       TEXT,
    source_entity_id         INTEGER,
    source_value             REAL,
    public                   INTEGER NOT NULL DEFAULT 1 CHECK (public IN (0, 1)),
    note                     TEXT,
    awarded_by_membership_id INTEGER,
    awarded_at               TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id, recipient_player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (awarded_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

-- ============================================================
-- FINANCE
-- ============================================================

CREATE TABLE funds (
    fund_id        INTEGER PRIMARY KEY,
    league_id      INTEGER NOT NULL,
    fund_name      TEXT NOT NULL,
    fund_type      TEXT NOT NULL
                   CHECK (fund_type IN (
                       'round_payout',
                       'ace_pot',
                       'postseason_pot',
                       'other'
                   )),
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, fund_name)
);

CREATE TABLE financial_transactions (
    transaction_id                 INTEGER PRIMARY KEY,
    fund_id                         INTEGER NOT NULL,
    season_id                       INTEGER NOT NULL,
    round_id                        INTEGER,
    season_membership_id            INTEGER,
    amount                          INTEGER NOT NULL CHECK (amount <> 0),
    transaction_type                TEXT NOT NULL
                                    CHECK (transaction_type IN (
                                        'seasonal_postseason_contribution',
                                        'round_postseason_contribution',
                                        'round_payout_contribution',
                                        'donation',
                                        'ace_payout',
                                        'postseason_payout',
                                        'round_payout',
                                        'opening_balance',
                                        'closing_disposition',
                                        'manual_adjustment',
                                        'other'
                                    )),
    description                     TEXT,
    source_key                      TEXT,
    created_by_membership_id        INTEGER,
    created_at                      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (fund_id) REFERENCES funds(fund_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (created_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE (fund_id, source_key)
);


CREATE TRIGGER trg_financial_transaction_scope_insert
BEFORE INSERT ON financial_transactions
FOR EACH ROW
WHEN (
    NEW.round_id IS NOT NULL
    AND (SELECT season_id FROM rounds WHERE round_id = NEW.round_id) <> NEW.season_id
) OR (
    NEW.season_membership_id IS NOT NULL
    AND (
        SELECT season_id
        FROM season_memberships
        WHERE season_membership_id = NEW.season_membership_id
    ) <> NEW.season_id
)
BEGIN
    SELECT RAISE(ABORT, 'Financial transaction round and membership must match the transaction season.');
END;

CREATE TRIGGER trg_financial_transactions_immutable_update
BEFORE UPDATE ON financial_transactions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Financial transactions are immutable; create an adjustment transaction.');
END;

CREATE TRIGGER trg_financial_transactions_immutable_delete
BEFORE DELETE ON financial_transactions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Financial transactions are immutable and cannot be deleted.');
END;

CREATE TABLE season_fund_snapshots (
    season_fund_snapshot_id INTEGER PRIMARY KEY,
    season_id               INTEGER NOT NULL,
    fund_id                 INTEGER NOT NULL,
    opening_amount          INTEGER NOT NULL DEFAULT 0,
    closing_amount          INTEGER NOT NULL DEFAULT 0,
    disposition_status      TEXT NOT NULL DEFAULT 'open'
                            CHECK (disposition_status IN (
                                'open',
                                'carried_offline',
                                'resolved',
                                'not_applicable'
                            )),
    disposition_note        TEXT,
    snapshot_at             TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (fund_id) REFERENCES funds(fund_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (season_id, fund_id)
);

CREATE TABLE aces (
    ace_id                   INTEGER PRIMARY KEY,
    season_id                INTEGER NOT NULL,
    player_id                INTEGER NOT NULL,
    round_id                 INTEGER NOT NULL,
    hole_id                  INTEGER NOT NULL,
    award_amount             INTEGER NOT NULL DEFAULT 0
                             CHECK (award_amount >= 0),
    status                   TEXT NOT NULL DEFAULT 'recorded'
                             CHECK (status IN ('recorded', 'voided')),
    detected_at              TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    created_by_membership_id INTEGER,
    FOREIGN KEY (season_id, player_id)
        REFERENCES season_memberships(season_id, player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (hole_id) REFERENCES holes(hole_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (created_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE (round_id, player_id, hole_id)
);

-- The finalization engine inserts an ace when a finalized hole score has
-- strokes = 1. No separate manual confirmation is required.

-- ============================================================
-- RECORDS, MILESTONES, GENERATED CONTENT, AND AUDIT
-- ============================================================

CREATE TABLE career_milestones (
    milestone_id       INTEGER PRIMARY KEY,
    league_id          INTEGER NOT NULL,
    player_id          INTEGER NOT NULL,
    milestone_category TEXT NOT NULL,
    threshold_value    INTEGER NOT NULL,
    achieved_round_id  INTEGER,
    achieved_at        TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    announced          INTEGER NOT NULL DEFAULT 0 CHECK (announced IN (0, 1)),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (achieved_round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (league_id, player_id, milestone_category, threshold_value)
);

CREATE TABLE record_history (
    record_id                INTEGER PRIMARY KEY,
    league_id                INTEGER NOT NULL,
    season_id                INTEGER,
    course_id                INTEGER,
    layout_id                INTEGER,
    player_id                INTEGER NOT NULL,
    record_category          TEXT NOT NULL,
    record_scope             TEXT NOT NULL
                             CHECK (record_scope IN (
                                 'league',
                                 'season',
                                 'course',
                                 'layout',
                                 'career'
                             )),
    value_numeric            REAL,
    value_text               TEXT,
    achieved_round_id        INTEGER,
    achieved_at              TEXT NOT NULL,
    is_current               INTEGER NOT NULL DEFAULT 1
                             CHECK (is_current IN (0, 1)),
    is_tied                  INTEGER NOT NULL DEFAULT 0
                             CHECK (is_tied IN (0, 1)),
    superseded_at            TEXT,
    superseded_by_record_id  INTEGER,
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (layout_id) REFERENCES layouts(layout_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (achieved_round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (superseded_by_record_id) REFERENCES record_history(record_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CHECK (value_numeric IS NOT NULL OR value_text IS NOT NULL)
);

CREATE TABLE generated_content (
    content_id                       INTEGER PRIMARY KEY,
    league_id                        INTEGER NOT NULL,
    season_id                        INTEGER,
    round_id                         INTEGER,
    content_type                     TEXT NOT NULL
                                     CHECK (content_type IN (
                                         'results_report',
                                         'round_recap',
                                         'social_post',
                                         'season_recap',
                                         'playoff_recap',
                                         'championship_recap',
                                         'other'
                                     )),
    title                            TEXT,
    body                             TEXT NOT NULL,
    status                           TEXT NOT NULL DEFAULT 'draft'
                                     CHECK (status IN (
                                         'draft',
                                         'reviewed',
                                         'published',
                                         'withdrawn',
                                         'stale',
                                         'archived'
                                     )),
    result_version                   INTEGER,
    template_version                 TEXT,
    generation_parameters           TEXT,
    generated_by_membership_id       INTEGER,
    reviewed_at                      TEXT,
    published_at                     TEXT,
    withdrawn_at                     TEXT,
    stale_at                         TEXT,
    generated_at                     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at                       TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (generated_by_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE audit_log (
    audit_id               INTEGER PRIMARY KEY,
    league_id              INTEGER NOT NULL,
    season_membership_id   INTEGER,
    action_type            TEXT NOT NULL,
    entity_type            TEXT,
    entity_id              INTEGER,
    details_json           TEXT,
    details_schema_version INTEGER NOT NULL DEFAULT 1
                           CHECK (details_schema_version > 0),
    created_at             TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (season_membership_id)
        REFERENCES season_memberships(season_membership_id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TRIGGER trg_audit_log_immutable_update
BEFORE UPDATE ON audit_log
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Audit records are immutable.');
END;

CREATE TRIGGER trg_audit_log_immutable_delete
BEFORE DELETE ON audit_log
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Audit records are immutable.');
END;

-- ============================================================
-- CURRENT STANDINGS VIEW
-- ============================================================

CREATE VIEW season_standings_current AS
WITH standings_base AS (
    SELECT
        sm.season_id,
        sm.player_id,
        p.display_name,
        p.pdga_number,
        p.public_profile_visible,
        sm.tag_purchased,
        COUNT(DISTINCT CASE
            WHEN r.status = 'finalized'
             AND r.round_type IN ('regular', 'saturday', 'double_points')
             AND rp.status IN ('completed', 'dnf')
            THEN r.round_id
        END) AS rounds_played,
        COALESCE(SUM(CASE
            WHEN r.status = 'finalized'
             AND r.round_type IN ('regular', 'saturday', 'double_points')
            THEN rr.points
            ELSE 0
        END), 0) AS points,
        (
            SELECT phh.applied_handicap
            FROM player_handicap_history phh
            WHERE phh.season_id = sm.season_id
              AND phh.player_id = sm.player_id
            ORDER BY phh.calculated_at DESC, phh.handicap_history_id DESC
            LIMIT 1
        ) AS current_applied_handicap
    FROM season_memberships sm
    JOIN players p
      ON p.player_id = sm.player_id
    LEFT JOIN round_participants rp
      ON rp.season_membership_id = sm.season_membership_id
    LEFT JOIN rounds r
      ON r.round_id = rp.round_id
    LEFT JOIN round_results rr
      ON rr.participant_id = rp.participant_id
    WHERE sm.is_guest = 0
    GROUP BY
        sm.season_id,
        sm.player_id,
        p.display_name,
        p.pdga_number,
        p.public_profile_visible,
        sm.tag_purchased
)
SELECT
    season_id,
    player_id,
    display_name,
    pdga_number,
    public_profile_visible,
    tag_purchased,
    rounds_played,
    points,
    current_applied_handicap,
    RANK() OVER (
        PARTITION BY season_id
        ORDER BY points DESC
    ) AS standings_rank
FROM standings_base;

-- ============================================================
-- SUPPORTING INDEXES
-- ============================================================

CREATE INDEX ix_seasons_league_status
    ON seasons(league_id, status);

CREATE INDEX ix_season_memberships_season
    ON season_memberships(season_id, is_guest, tag_purchased);

CREATE INDEX ix_season_memberships_player
    ON season_memberships(player_id, season_id);

CREATE INDEX ix_layouts_course
    ON layouts(course_id);

CREATE INDEX ix_holes_layout
    ON holes(layout_id);

CREATE INDEX ix_rounds_season_date
    ON rounds(season_id, round_date);

CREATE INDEX ix_rounds_season_type_status
    ON rounds(season_id, round_type, status);

CREATE INDEX ix_round_participants_player_round
    ON round_participants(season_membership_id, round_id);

CREATE INDEX ix_cards_round
    ON cards(round_id);

CREATE INDEX ix_card_assignments_card
    ON card_assignments(card_id);

CREATE INDEX ix_hole_scores_assignment
    ON hole_scores(assignment_id);

CREATE INDEX ix_hole_scores_hole
    ON hole_scores(hole_id);

CREATE INDEX ix_round_results_finish
    ON round_results(official_finish, points, payout_amount);

CREATE INDEX ix_finish_resolutions_round
    ON finish_resolutions(round_id, resolution_group);

CREATE INDEX ix_handicap_adjustments_player_season
    ON handicap_adjustments(player_id, season_id);

CREATE INDEX ix_handicap_history_player_season
    ON player_handicap_history(season_id, player_id, calculated_at);

CREATE INDEX ix_postseason_entries_season
    ON postseason_entries(season_id, regular_season_seed);

CREATE INDEX ix_season_standings_snapshots_order
    ON season_standings_snapshots(
        season_id,
        postseason_tier,
        final_placement,
        regular_points DESC,
        final_applied_handicap
    );

CREATE INDEX ix_financial_transactions_fund
    ON financial_transactions(fund_id, season_id, created_at);

CREATE INDEX ix_financial_transactions_round
    ON financial_transactions(round_id, transaction_type);

CREATE INDEX ix_aces_player
    ON aces(player_id, season_id, detected_at);

CREATE INDEX ix_generated_content_round
    ON generated_content(round_id, content_type, status);

CREATE INDEX ix_record_history_current
    ON record_history(league_id, record_category, record_scope, is_current);

CREATE INDEX ix_audit_log_entity
    ON audit_log(entity_type, entity_id, created_at);

COMMIT;
