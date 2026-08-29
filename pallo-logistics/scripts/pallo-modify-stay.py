#!/usr/bin/env python3
"""pallo-modify-stay.py — change Pallo's stay dates or activity slate (MUTATING).

The Gingr customer portal exposes no in-place edit/reschedule — only cancel — so
a modify is done as **book-the-new-stay-first, then cancel-the-old**. That order
means a failure mid-way never leaves Pallo with no booking. This script just
orchestrates pallo-book-trip.py + pallo-cancel.py; all portal automation lives
there.

The caller states the replacement dates TWICE: once as --new-drop-date /
--new-pickup-date and again as --confirm-drop-date / --confirm-pickup-date. Both
pairs must agree, and the confirm pair is what gets forwarded to the booking, so
the invariant the agent typed is the invariant the portal is driven with.

Usage:
  # preview the date change:
  python3 pallo-modify-stay.py --stay-id 2026-12-11/2026-12-20 \
      --new-drop-date 2026-12-12 --new-pickup-date 2026-12-21 \
      --confirm-drop-date 2026-12-12 --confirm-pickup-date 2026-12-21 --dry-run

  # do it for real, after the user approves:
  python3 pallo-modify-stay.py --stay-id 2026-12-11/2026-12-20 \
      --new-drop-date 2026-12-12 --new-pickup-date 2026-12-21 \
      --confirm-drop-date 2026-12-12 --confirm-pickup-date 2026-12-21 --confirm

  # change only the activity slate (keep dates): pass the same dates + --simple-slate

Options forwarded to the new booking: --drop-time, --pickup-time, --simple-slate.
--dry-run previews both steps (book estimate + cancel verification) and mutates
nothing.

Output: JSON `status`:
  dry_run_ok | modified | modified_old_not_cancelled | book_failed |
  confirm_required | confirm_mismatch | error  (plus the child statuses under
  `book`/`cancel`).

SAFETY: this books a real new reservation and cancels the old one. Echo BOTH the
new dates and the old stay to the user and get an explicit in-turn "yes" before
calling with --confirm.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_json import ok, fail, guard  # noqa: E402


def _run(script: str, args: list[str]) -> dict:
    r = subprocess.run(["python3", str(SCRIPT_DIR / script), *args],
                       capture_output=True, text=True, timeout=900)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "error",
                "reason": f"{script} returned non-JSON: {(r.stdout or r.stderr)[:200]}"}


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stay-id", required=True, help='old stay, "<start>/<end>" ISO')
    ap.add_argument("--new-drop-date", required=True, help="ISO drop-off for the replacement stay")
    ap.add_argument("--new-pickup-date", required=True, help="ISO pickup for the replacement stay")
    ap.add_argument("--confirm-drop-date", required=True,
                    help="ISO; must equal --new-drop-date. Type the date the user approved.")
    ap.add_argument("--confirm-pickup-date", required=True,
                    help="ISO; must equal --new-pickup-date. Type the date the user approved.")
    ap.add_argument("--drop-time", default=None)
    ap.add_argument("--pickup-time", default=None)
    ap.add_argument("--simple-slate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually re-book and cancel. Pass it ONLY after the "
                         "user has explicitly approved this exact change.")
    args = ap.parse_args()

    # Footgun guard — refuse BEFORE any side effect (no child script is run).
    if not args.confirm and not args.dry_run:
        fail("modifying books a real new reservation and cancels the existing "
             "one. Re-run with --confirm ONLY after the user has explicitly "
             "approved this exact change, or use --dry-run to preview.",
             status="confirm_required")
        return

    try:
        old_start, old_end = args.stay_id.split("/", 1)
        date.fromisoformat(old_start); date.fromisoformat(old_end)
        date.fromisoformat(args.new_drop_date); date.fromisoformat(args.new_pickup_date)
        date.fromisoformat(args.confirm_drop_date); date.fromisoformat(args.confirm_pickup_date)
    except ValueError as e:
        fail(f"bad dates: {e}", status="error")
        return

    # The confirm invariant must come FROM THE CALLER. Synthesising it here from
    # --new-*-date would make the booking script's own guard unfireable.
    if (args.confirm_drop_date != args.new_drop_date
            or args.confirm_pickup_date != args.new_pickup_date):
        fail("confirm dates do not match the requested new dates; "
             "refusing to re-book.",
             status="confirm_mismatch",
             new_drop_date=args.new_drop_date,
             new_pickup_date=args.new_pickup_date,
             confirm_drop_date=args.confirm_drop_date,
             confirm_pickup_date=args.confirm_pickup_date)
        return

    book_args = [
        "--drop-date", args.new_drop_date, "--pickup-date", args.new_pickup_date,
        # forwarded verbatim from the caller — never re-derived from the new dates
        "--confirm-drop-date", args.confirm_drop_date,
        "--confirm-pickup-date", args.confirm_pickup_date,
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
        if book.get("status") == "dry_run_ok" and cancel.get("status") == "dry_run_ok":
            ok(status="dry_run_ok",
               plan="book the new stay, then cancel the old one",
               new_stay={"drop_off": args.new_drop_date, "pick_up": args.new_pickup_date},
               old_stay_id=args.stay_id,
               book=book, cancel=cancel)
        else:
            fail("dry-run preview did not pass; see book/cancel for detail",
                 status="error",
               new_stay={"drop_off": args.new_drop_date, "pick_up": args.new_pickup_date},
               old_stay_id=args.stay_id,
               book=book, cancel=cancel)
        return

    # REAL: book new first. --confirm is forwarded because this script's own
    # --confirm guard above already required the user's explicit approval.
    book = _run("pallo-book-trip.py", book_args + ["--confirm"])
    if book.get("status") not in ("booked", "booked_with_notification_warnings"):
        fail("New stay was not booked; the old stay was left untouched.",
             status="book_failed", book=book)
        return
    # then cancel old
    cancel = _run("pallo-cancel.py", cancel_args + ["--confirm"])
    if cancel.get("status") == "cancelled":
        ok(status="modified",
           new_stay={"drop_off": args.new_drop_date, "pick_up": args.new_pickup_date},
           old_stay_id=args.stay_id, book=book, cancel=cancel)
        return
    fail("New stay booked, but cancelling the old stay did not confirm. "
         "Cancel the old stay manually to avoid a double booking.",
         status="modified_old_not_cancelled",
         old_stay_id=args.stay_id, book=book, cancel=cancel)


if __name__ == "__main__":
    main()
