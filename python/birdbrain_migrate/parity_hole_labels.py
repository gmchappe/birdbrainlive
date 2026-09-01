from __future__ import annotations

from dotenv import load_dotenv

# Keep CLI behavior consistent with bootstrap/cleanup: read local Supabase
# credentials from the repository-root .env before importing database helpers.
load_dotenv()

# Import cli first because it installs the migration-tested Google CSV date parser
# onto bootstrap_normalized before parity_check imports parse_date from that module.
import cli as migration_cli  # noqa: E402
import parity_check  # noqa: E402

# Be explicit as well: parity_check imported parse_date by name, so point that
# reference at the same parser used by bootstrap. This accepts date-only values
# and Google-export datetime strings such as '2026-04-12 00:00:00'.
parity_check.parse_date = migration_cli.parse_google_export_date

# UDisc hole identifiers are labels, not guaranteed integers (for example 9G).
parity_check.INTEGER_FIELDS.discard("Hole")


if __name__ == "__main__":
    parity_check.main()
