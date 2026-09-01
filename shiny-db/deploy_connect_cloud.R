# Secure development deployment for the PostgreSQL-backed BirdBrain Shiny app.
#
# Why Connect Cloud instead of a new shinyapps.io deployment?
# Connect Cloud supports encrypted environment-variable synchronization, so the
# database password is not bundled with the application source. The current
# shinyapps.io platform does not provide equivalent secret-variable management.
#
# One-time account registration (interactive R session):
#   install.packages("rsconnect")
#   rsconnect::connectCloudUser()
#
# Then run from the repository root with Rscript:
#   Rscript shiny-db/deploy_connect_cloud.R

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

if (!requireNamespace("rsconnect", quietly = TRUE)) {
  stop(
    "Package 'rsconnect' is required. Install it with install.packages('rsconnect')."
  )
}

required <- c(
  "BB_DB_HOST",
  "BB_DB_PORT",
  "BB_DB_NAME",
  "BB_SHINY_DB_USER",
  "BB_SHINY_DB_PASSWORD"
)
missing <- required[Sys.getenv(required) == ""]
if (length(missing) > 0) {
  stop(
    "Missing secure Shiny deployment variables: ",
    paste(missing, collapse = ", "),
    ". Run python/birdbrain_migrate/provision_shiny_reader.py --apply first."
  )
}

cloud_accounts <- rsconnect::accounts(server = "connect.posit.cloud")
if (nrow(cloud_accounts) == 0) {
  stop(
    "No Posit Connect Cloud account is registered locally. Start an interactive R ",
    "session and run rsconnect::connectCloudUser(), complete browser authentication, ",
    "then rerun this script."
  )
}

requested_account <- Sys.getenv("BB_CONNECT_CLOUD_ACCOUNT", unset = "")
if (requested_account != "") {
  if (!requested_account %in% cloud_accounts$name) {
    stop(
      "BB_CONNECT_CLOUD_ACCOUNT='", requested_account,
      "' is not one of the registered Connect Cloud accounts: ",
      paste(cloud_accounts$name, collapse = ", ")
    )
  }
  account <- requested_account
} else if (nrow(cloud_accounts) == 1) {
  account <- cloud_accounts$name[[1]]
} else {
  stop(
    "Multiple Connect Cloud accounts are registered: ",
    paste(cloud_accounts$name, collapse = ", "),
    ". Set BB_CONNECT_CLOUD_ACCOUNT in the local .env to the account to use."
  )
}

app_dir <- file.path(repo_root, "shiny-db")
app_files <- c(
  "ui.R",
  "server.R",
  file.path("R", "db.R")
)

env_vars <- c(
  "BB_DB_HOST",
  "BB_DB_PORT",
  "BB_DB_NAME",
  "BB_SHINY_DB_USER",
  "BB_SHINY_DB_PASSWORD"
)
if (Sys.getenv("BB_DB_SSLMODE", unset = "") != "") {
  env_vars <- c(env_vars, "BB_DB_SSLMODE")
}

cat("BirdBrain Connect Cloud dev deployment\n")
cat("=======================================\n")
cat(sprintf("Account: %s\n", account))
cat("App:     birdbrain-db-dev\n")
cat("Bundle:  ui.R, server.R, R/db.R only\n")
cat("DB user: least-privilege BB_SHINY_DB_USER\n")
cat("Secrets: synchronized as encrypted Connect environment variables\n")
cat(sprintf("Local R: %s\n\n", R.version.string))

if (getRversion() > "4.6.0") {
  warning(
    "Current Connect Cloud documentation lists R support through 4.6.0. ",
    "This machine is running ", R.version.string,
    ". If the build rejects the R version, deploy from a side-by-side R 4.6.0 installation."
  )
}

rsconnect::deployApp(
  appDir = app_dir,
  appFiles = app_files,
  appName = "birdbrain-db-dev",
  appTitle = "BirdBrain DB Dev",
  account = account,
  server = "connect.posit.cloud",
  envVars = env_vars,
  appMode = "shiny",
  launch.browser = TRUE,
  logLevel = "normal",
  dependencyResolution = "library"
)
