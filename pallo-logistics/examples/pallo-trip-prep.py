#!/usr/bin/env python3
"""pallo-trip-prep.py — full trip prep for Pallo in one call (CUJ-6 composite).

Composes the other scripts: check whether the trip's boarding is already set up
(pallo-trip-status), and if not, build the plan (pallo-trip-plan). Without
--commit it stops there and returns the plan-of-plans (boarding window + activity
slate + the Gina-coordination messages it WOULD send) for the user to approve.
With --commit it books the stay (pallo-book-trip), which also fires the Gina
notifications.

Usage:
  # preview (read-only):
  python3 pallo-trip-prep.py --trip-name London
  # after the user says yes:
  python3 pallo-trip-prep.py --trip-name London --commit \\
      --confirm-drop-date 2026-07-11 --confirm-pickup-date 2026-07-31

Identify the trip by --trip-name OR explicit --trip-start / --trip-end.
Forwards --simple-slate, --drop-time, --pickup-time to the booking on --commit.

Output: JSON `status`:
  all_set | planned | booked | booked_with_notification_warnings | already_set |
  ambiguous_trip | no_trip_found | conflict | confirm_mismatch |
  session_expired | error  (child payloads under `trip_status` / `plan` / `book`).

SAFETY: --commit places a real reservation and messages Gina. Show the user the
plan from the no-commit preview first and get an explicit in-turn "yes".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
    ap.add_argument("--trip-name", default=None)
    ap.add_argument("--trip-start", default=None, help="ISO (with --trip-end)")
    ap.add_argument("--trip-end", default=None, help="ISO (with --trip-start)")
    ap.add_argument("--commit", action="store_true", help="Actually book (else preview only).")
    ap.add_argument("--confirm-drop-date", default=None, help="required with --commit")
    ap.add_argument("--confirm-pickup-date", default=None, help="required with --commit")
    ap.add_argument("--simple-slate", action="store_true")
    ap.add_argument("--drop-time", default=None)
    ap.add_argument("--pickup-time", default=None)
    args = ap.parse_args()

    # how to identify the trip, forwarded to status + plan
    if args.trip_name:
        trip_args = ["--trip-name", args.trip_name]
    elif args.trip_start and args.trip_end:
        trip_args = ["--trip-start", args.trip_start, "--trip-end", args.trip_end]
    else:
        return out({"status": "error",
                    "reason": "identify the trip with --trip-name or both --trip-start and --trip-end"}, 2)

    # 1) already set up?
    status = _run("pallo-trip-status.py", trip_args)
    if status.get("status") == "all_set":
        return out({"status": "all_set",
                    "detail": status.get("detail"), "trip_status": status})

    # 2) build the plan
    plan = _run("pallo-trip-plan.py", trip_args)
    if plan.get("status") != "ok":
        # ambiguous_trip / no_trip_found / calendar_error / dates_invalid
        return out({"status": plan.get("status", "error"), "plan": plan},
                   0 if plan.get("status") in ("ambiguous_trip", "no_trip_found") else 1)

    drop, pick = plan["drop_off"], plan["pick_up"]
    preview = {
        "trip_name": plan.get("trip_name"),
        "trip_status": status.get("status"),
        "drop_off": drop, "pick_up": pick, "nights": plan.get("nights"),
        "activity_slate": plan.get("activity_slate"),
        "gina_messages": plan.get("gina_messages"),
    }

    if not args.commit:
        return out({**preview, "status": "planned",
                    "plan_json": plan.get("plan_json"),
                    "note": "Re-run with --commit --confirm-drop-date "
                            f"{drop} --confirm-pickup-date {pick} to book."})

    # 3) commit -> book
    if not (args.confirm_drop_date and args.confirm_pickup_date):
        return out({**preview, "status": "error",
                    "reason": "--commit requires --confirm-drop-date and --confirm-pickup-date"}, 2)
    if args.confirm_drop_date != drop or args.confirm_pickup_date != pick:
        return out({**preview, "status": "confirm_mismatch",
                    "confirm_drop_date": args.confirm_drop_date,
                    "confirm_pickup_date": args.confirm_pickup_date}, 1)

    book_args = ["--plan", plan["plan_json"],
                 "--confirm-drop-date", drop, "--confirm-pickup-date", pick]
    if args.simple_slate:
        book_args.append("--simple-slate")
    if args.drop_time:
        book_args += ["--drop-time", args.drop_time]
    if args.pickup_time:
        book_args += ["--pickup-time", args.pickup_time]
    book = _run("pallo-book-trip.py", book_args)
    return out({**preview, "status": book.get("status", "error"), "book": book},
               0 if book.get("status", "").startswith("booked") else 1)


if __name__ == "__main__":
    sys.exit(main())
