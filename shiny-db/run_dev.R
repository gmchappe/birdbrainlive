# Local launcher for the PostgreSQL-backed BirdBrain Shiny app.
#
# This file is development-only. It reads the repo-root .env file when present
# so local R uses the same BB_DB_* connection settings as the migration Python
# utilities. shinyapps.io should receive BB_DB_* values as deployment environment
# variables instead; no credential file is bundled with the app.

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

repo_root <- find_repo_root()
env_path <- file.path(repo_root, ".env")

if (file.exists(env_path)) {
  readRenviron(env_path)
}

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
    paste(missing, collapse = ", "),
    ". Put them in the repo-root .env for local development or export them before running."
  )
}

shiny::runApp(
  appDir = file.path(repo_root, "shiny-db"),
  launch.browser = TRUE
)
