connect_db <- function() {
  DBI::dbConnect(
    RSQLite::SQLite(),
    "database/birdbrain.sqlite"
  )
}