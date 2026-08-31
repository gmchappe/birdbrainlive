# Legacy R implementation

This directory freezes the current BirdBrain R/Shiny and Google Sheets implementation as the reference behavior for the database and Python migration.

Do not refactor these files merely to make the new architecture cleaner. When a business rule differs from the legacy implementation, the approved engine specification in `docs/` governs the new implementation and the difference should be covered by an explicit regression/behavior test.

The live Google Sheet remains read-only from migration code while the 2026 season is ongoing.
