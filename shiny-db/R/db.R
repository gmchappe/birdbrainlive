# Database-backed Shiny helpers
# Target: shinyapps.io -> managed PostgreSQL/Supabase
# Requires packages DBI, RPostgres, and pool.

bb_db_pool <- function() {
  required <- c(
    "BB_DB_HOST",
    "BB_DB_PORT",
    "BB_DB_NAME",
    "BB_DB_USER",
    "BB_DB_PASSWORD"
  )

  missing <- required[Sys.getenv(required) == ""]
  if (length(missing) > 0) {
    stop(
      "Missing BirdBrain database environment variables: ",
      paste(missing, collapse = ", ")
    )
  }

  sslmode <- Sys.getenv("BB_DB_SSLMODE", unset = "require")

  pool::dbPool(
    drv = RPostgres::Postgres(),
    host = Sys.getenv("BB_DB_HOST"),
    port = as.integer(Sys.getenv("BB_DB_PORT")),
    dbname = Sys.getenv("BB_DB_NAME"),
    user = Sys.getenv("BB_DB_USER"),
    password = Sys.getenv("BB_DB_PASSWORD"),
    sslmode = sslmode
  )
}

bb_get_schedule <- function(pool) {
  DBI::dbGetQuery(
    pool,
    paste(
      'SELECT "Course", "Layout", "Date", "StartTime", "Note", "RoundNo"',
      'FROM v_schedule',
      'WHERE "Datend" >= CURRENT_DATE - 1',
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
    'SELECT * FROM v_hall_of_champions ORDER BY "Season", "Date", "Name"'
  )
}
