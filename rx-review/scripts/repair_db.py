#!/usr/bin/env python3
"""DELIBERATELY EMPTY. Nothing in this pipeline may open the kanban database directly.

This file held a repair script that opened `kanban/boards/rx-review/kanban.db` read-write,
rebuilt its indexes and copied the result over the live file. It is kept as an empty tombstone
because deleting it is not enough: the script has been written three times now, each time by
someone reasonable looking at a corrupt board and a database they could obviously fix.

WHY IT MUST NOT EXIST
  * The board is live. The dispatcher and both gateways hold open handles to that file while a
    review runs, and a second writer that copies a rebuilt database over it discards whatever
    they wrote in between. Repair scripts of exactly this kind corrupted the board repeatedly,
    which is why they were deleted on 2026-08-07.
  * Rebuilding "fixed" the symptom and hid the cause. On 2026-08-10 a script like this ran while
    four workers were dispatching, and the run continued on a database nobody could account for.
  * The failure it treats is not a database bug. Index divergence came from the writer being
    SIGKILLed mid-transaction, and no amount of REINDEX prevents the next one.

WHAT TO DO INSTEAD
  Read the board only through the CLI:            hermes kanban --board rx-review list
  A genuinely corrupt board is replaced, not patched — the old one is archived, not edited:
      hermes kanban boards rm rx-review           # archives it
      hermes kanban boards create rx-review
      hermes gateway restart --all
  Then `python3 rx.py reset --confirm` and start the review again. `salvage/` and the archived board
  keep the evidence.

  If a read-only inspection is genuinely needed, open it with `mode=ro&immutable=1` in a throwaway
  script and delete that script afterwards — but note that `immutable=1` ignores the WAL, so what
  you read is the last checkpoint and not necessarily the current state.

`rx_test.py` fails the build if any module here opens that database read-write, so re-adding one
breaks CI rather than the board.
"""

import sys

if __name__ == "__main__":
    sys.exit(__doc__)
