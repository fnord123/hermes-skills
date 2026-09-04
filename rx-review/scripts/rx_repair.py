#!/usr/bin/env python3
"""DELIBERATELY EMPTY. See repair_db.py — the same tombstone, under the other name it was given.

This file held a second copy of the kanban-database repair script. Two names for the same idea is
itself part of the pattern: the first was deleted, the need recurred, and it was written again
under a new name rather than found.

The rule, in one line: **nothing in this pipeline opens the kanban database directly, and nothing
opens it read-write anywhere, repair scripts included.** The reasoning, the failure history and
the correct recovery are in `repair_db.py`; they are not duplicated here, because two copies of a
rationale drift and this one would end up disagreeing with the other.

`rx_test.py` fails the build if any module here opens that database read-write.
"""

import sys

if __name__ == "__main__":
    sys.exit(__doc__)
