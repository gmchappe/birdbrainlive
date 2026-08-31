from __future__ import annotations

from dotenv import load_dotenv

# Keep CLI behavior consistent with bootstrap/cleanup: read local Supabase
# credentials from the repository-root .env before importing database helpers.
load_dotenv()

import parity_check  # noqa: E402

# UDisc hole identifiers are labels, not guaranteed integers (for example 9G).
parity_check.INTEGER_FIELDS.discard("Hole")


if __name__ == "__main__":
    parity_check.main()
