from __future__ import annotations

import parity_check

# UDisc hole identifiers are labels, not guaranteed integers (for example 9G).
parity_check.INTEGER_FIELDS.discard("Hole")


if __name__ == "__main__":
    parity_check.main()
