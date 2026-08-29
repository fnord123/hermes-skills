#!/usr/bin/env python3
"""gina-pending.py — read / clear the Gina-coordination ledger.

Stdlib-only. The agent calls this FIRST whenever it sees a message from Gina
in the coordination channel, so it knows which outstanding ask her reply is
answering — independent of how much Discord history is loaded that turn.

Usage:
  python3 gina-pending.py                 # list outstanding asks
  python3 gina-pending.py --resolve <id>  # clear one after incorporating her answer

Output: JSON. List form: {ok, count, pending: [...]}. Resolve form:
{ok, resolved, found, remaining_count, pending: [...]}.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import coord_lib  # noqa: E402
from skill_json import ok, fail, guard  # noqa: E402


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolve", metavar="ID", default=None,
                    help="Mark the ledger entry with this id resolved.")
    args = ap.parse_args()

    if args.resolve:
        found, data = coord_lib.resolve_pending(args.resolve)
        ok(resolved=args.resolve,
           found=found,
           remaining_count=len(data["pending"]),
           pending=data["pending"])

    data = coord_lib.read_ledger()
    ok(count=len(data["pending"]),
       pending=data["pending"])


if __name__ == "__main__":
    main()
