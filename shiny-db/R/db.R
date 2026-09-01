# Database-backed Shiny helpers
# Target: managed PostgreSQL/Supabase development database.
# Requires packages DBI, RPostgres, and pool.
#
# Migration/admin utilities continue to use BB_DB_USER/BB_DB_PASSWORD. The Shiny
# app prefers BB_SHINY_DB_USER/BB_SHINY_DB_PASSWORD when present so hosted Shiny
# never needs the PostgreSQL admin credential.

bb_db_pool <- function() {
  required <- c(
    "BB_DB_HOST",
    "BB_DB_PORT",
    "BB_DB_NAME"
  )

  missing <- required[Sys.getenv(required) == ""]
  if (length(missing) > 0) {
    stop(
      "Missing BirdBrain database environment variables: ",
      paste(missing, collapse = ", ")
    )
  }

  user <- Sys.getenv(
    "BB_SHINY_DB_USER",
    unset = Sys.getenv("BB_DB_USER", unset = "")
  )
  password <- Sys.getenv(
    "BB_SHINY_DB_PASSWORD",
    unset = Sys.getenv("BB_DB_PASSWORD", unset = "")
  )

  if (user == "" || password == "") {
    stop(
      "Missing BirdBrain Shiny database credentials. Set ",
      "BB_SHINY_DB_USER/BB_SHINY_DB_PASSWORD (preferred) or ",
      "BB_DB_USER/BB_DB_PASSWORD for local migration-only fallback."
    )
  }

  sslmode <- Sys.getenv("BB_DB_SSLMODE", unset = "require")

  pool::dbPool(
    drv = RPostgres::Postgres(),
    host = Sys.getenv("BB_DB_HOST"),
    port = as.integer(Sys.getenv("BB_DB_PORT")),
    dbname = Sys.getenv("BB_DB_NAME"),
    user = user,
    password = password,
    sslmode = sslmode
  )
}

bb_get_schedule <- function(pool) {
  DBI::dbGetQuery(
    pool,
    paste(
      'SELECT "Course", "Layout", "Date", "StartTime", "Note", "RoundNo", "AcePot"',
      'FROM v_schedule',
      'WHERE "Datend" >= (',
      "  timezone(",
      "    COALESCE((SELECT timezone FROM leagues ORDER BY league_id LIMIT 1), 'America/Chicago'),",
      "    CURRENT_TIMESTAMP",
      "  )::date - 1",
      ')',
      'ORDER BY "Datend", "RoundNo"'
    )
  )
}

bb_get_standings <- function(pool) {
  DBI::dbGetQuery(
    pool,
    'SELECT "Name", "Points", "Rounds", "Handicap" FROM v_leaderboard ORDER BY "Points" DESC, "Rounds", "Name"'
  )
}

bb_get_alltime <- function(pool) {
  DBI::dbGetQuery(
    pool,
    'SELECT "Name", "Seasons", "Rounds", "Points" FROM v_current_all_time ORDER BY "Points" DESC, "Name"'
  )
}

bb_get_records <- function(pool) {
  DBI::dbGetQuery(
    pool,
    'SELECT "Course", "Layout", "Name", "Score", "Date" FROM v_course_records ORDER BY "Course", "Layout", "Date" DESC'
  )
}

bb_get_aces <- function(pool) {
  DBI::dbGetQuery(
    pool,
    'SELECT "Name", "Date", "Course", "Layout", "Hole", "Payout" FROM v_aces ORDER BY "Date" DESC'
  )
}

bb_get_champions <- function(pool) {
  DBI::dbGetQuery(
    pool,
    'SELECT "Event", "Year", "Division", "Name", "Score" FROM v_hall_of_champions ORDER BY "Year", "Event", "Division", "Name"'
  )
}
