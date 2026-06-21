#!/usr/bin/env python3
"""pallo-modify-stay.py — change Pallo's stay dates or activity slate (MUTATING).

The Gingr customer portal exposes no in-place edit/reschedule — only cancel — so
a modify is done as **book-the-new-stay-first, then cancel-the-old**. That order
means a failure mid-way never leaves Pallo with no booking. This script just
orchestrates pallo-book-trip.py + pallo-cancel.py; all portal automation lives
there.

Usage:
  # change the dates of an existing stay:
  python3 pallo-modify-stay.py --stay-id 2026-12-11/2026-12-20 \\
      --new-drop-date 2026-12-12 --new-pickup-date 2026-12-21 [--dry-run]

  # change only the activity slate (keep dates): pass the same dates + --simple-slate
  python3 pallo-modify-stay.py --stay-id 2026-12-11/2026-12-20 \\
      --new-drop-date 2026-12-11 --new-pickup-date 2026-12-20 --simple-slate

Options forwarded to the new booking: --drop-time, --pickup-time, --simple-slate.
--dry-run previews both steps (book estimate + cancel verification) and mutates
nothing.

Output: JSON `status`:
  dry_run_ok | modified | modified_old_not_cancelled | book_failed |
  confirm_mismatch | error  (plus the child statuses under `book`/`cancel`).

SAFETY: this books a real new reservation and cancels the old one. Echo BOTH the
new dates and the old stay to the user and get an explicit in-turn "yes" before
calling without --dry-run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def out(d: dict, code: int = 0) -> int:
    print(json.dumps(d, indent=2))
    return code


def _run(script: str, args: list[str]) -> dict:
    r = subprocess.run(["python3", str(SCRIPT_DIR / script), *args],
                       capture_output=True, text=True, timeout=900)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "error",
                "reason": f"{script} returned non-JSON: {(r.stdout or r.stderr)[:200]}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stay-id", required=True, help='old stay, "<start>/<end>" ISO')
    ap.add_argument("--new-drop-date", required=True, help="ISO drop-off for the replacement stay")
    ap.add_argument("--new-pickup-date", required=True, help="ISO pickup for the replacement stay")
    ap.add_argument("--drop-time", default=None)
    ap.add_argument("--pickup-time", default=None)
    ap.add_argument("--simple-slate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        old_start, old_end = args.stay_id.split("/", 1)
        date.fromisoformat(old_start); date.fromisoformat(old_end)
        date.fromisoformat(args.new_drop_date); date.fromisoformat(args.new_pickup_date)
    except ValueError as e:
        return out({"status": "error", "reason": f"bad dates: {e}"}, 2)

    book_args = [
        "--drop-date", args.new_drop_date, "--pickup-date", args.new_pickup_date,
        "--confirm-drop-date", args.new_drop_date, "--confirm-pickup-date", args.new_pickup_date,
        "--allow-overlap",          # the new stay usually overlaps the old one
        "--no-gina",                # a modify shouldn't re-ping Gina
    ]
    if args.drop_time:
        book_args += ["--drop-time", args.drop_time]
    if args.pickup_time:
        book_args += ["--pickup-time", args.pickup_time]
    if args.simple_slate:
        book_args.append("--simple-slate")
    cancel_args = [
        "--stay-id", args.stay_id,
        "--confirm-drop-date", old_start, "--confirm-pickup-date", old_end,
    ]

    if args.dry_run:
        book = _run("pallo-book-trip.py", book_args + ["--dry-run"])
        cancel = _run("pallo-cancel.py", cancel_args + ["--dry-run"])
        ok = book.get("status") == "dry_run_ok" and cancel.get("status") == "dry_run_ok"
        return out({
            "status": "dry_run_ok" if ok else "error",
            "plan": "book the new stay, then cancel the old one",
            "new_stay": {"drop_off": args.new_drop_date, "pick_up": args.new_pickup_date},
            "old_stay_id": args.stay_id,
            "book": book, "cancel": cancel,
        }, 0 if ok else 1)

    # REAL: book new first
    book = _run("pallo-book-trip.py", book_args)
    if book.get("status") not in ("booked", "booked_with_notification_warnings"):
        return out({"status": "book_failed",
                    "reason": "New stay was not booked; the old stay was left untouched.",
                    "book": book}, 1)
    # then cancel old
    cancel = _run("pallo-cancel.py", cancel_args)
    if cancel.get("status") == "cancelled":
        return out({"status": "modified",
                    "new_stay": {"drop_off": args.new_drop_date, "pick_up": args.new_pickup_date},
                    "old_stay_id": args.stay_id, "book": book, "cancel": cancel})
    return out({"status": "modified_old_not_cancelled",
                "reason": "New stay booked, but cancelling the old stay did not confirm. "
                          "Cancel the old stay manually to avoid a double booking.",
                "old_stay_id": args.stay_id, "book": book, "cancel": cancel}, 1)


if __name__ == "__main__":
    sys.exit(main())
