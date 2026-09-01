# Read-only smoke test for the PostgreSQL-backed BirdBrain Shiny data helpers.
# Run from anywhere inside the repository with:
#   Rscript shiny-db/smoke_test.R
#
# The script loads the repo-root .env for local development, opens the same
# connection pool used by Shiny, calls all six compatibility-view helpers, and
# verifies their public column contracts. It performs no writes.

find_repo_root <- function(start = getwd()) {
  current <- normalizePath(start, winslash = "/", mustWork = TRUE)

  repeat {
    if (dir.exists(file.path(current, "shiny-db")) &&
        file.exists(file.path(current, "MIGRATION.md"))) {
      return(current)
    }

    parent <- dirname(current)
    if (identical(parent, current)) {
      stop("Could not locate the BirdBrain repository root.")
    }
    current <- parent
  }
}

main <- function() {
  repo_root <- find_repo_root()
  env_path <- file.path(repo_root, ".env")
  if (file.exists(env_path)) {
    readRenviron(env_path)
  }

  suppressPackageStartupMessages({
    library(DBI)
    library(RPostgres)
    library(pool)
  })

  source(file.path(repo_root, "shiny-db", "R", "db.R"))

  db_pool <- bb_db_pool()
  on.exit(pool::poolClose(db_pool), add = TRUE)

  checks <- list(
    schedule = list(
      data = bb_get_schedule(db_pool),
      columns = c("Course", "Layout", "Date", "StartTime", "Note", "RoundNo", "AcePot")
    ),
    standings = list(
      data = bb_get_standings(db_pool),
      columns = c("Name", "Points", "Rounds", "Handicap")
    ),
    alltime = list(
      data = bb_get_alltime(db_pool),
      columns = c("Name", "Seasons", "Rounds", "Points")
    ),
    records = list(
      data = bb_get_records(db_pool),
      columns = c("Course", "Layout", "Name", "Score", "Date")
    ),
    aces = list(
      data = bb_get_aces(db_pool),
      columns = c("Name", "Date", "Course", "Layout", "Hole", "Payout")
    ),
    champions = list(
      data = bb_get_champions(db_pool),
      columns = c("Event", "Year", "Division", "Name", "Score")
    )
  )

  cat("BirdBrain Shiny DB smoke test\n")
  cat("=============================\n")

  for (name in names(checks)) {
    result <- checks[[name]]$data
    expected <- checks[[name]]$columns

    if (!identical(names(result), expected)) {
      stop(
        sprintf(
          "%s column contract mismatch. Expected [%s], got [%s]",
          name,
          paste(expected, collapse = ", "),
          paste(names(result), collapse = ", ")
        )
      )
    }

    cat(sprintf("PASS %-10s rows=%d columns=%d\n", name, nrow(result), ncol(result)))
  }

  connection_info <- DBI::dbGetQuery(
    db_pool,
    paste(
      "SELECT current_database() AS database_name,",
      "current_user AS database_user,",
      "current_setting('TimeZone') AS database_timezone"
    )
  )

  cat("\nConnection\n")
  cat(sprintf("  database: %s\n", connection_info$database_name[[1]]))
  cat(sprintf("  user:     %s\n", connection_info$database_user[[1]]))
  cat(sprintf("  timezone: %s\n", connection_info$database_timezone[[1]]))
  cat("\nAll six Shiny read contracts passed. No database writes were attempted.\n")
}

main()
