#!/usr/bin/env python3
"""pallo-trip-plan.py — build the boarding + activity plan for a trip (read-only).

Resolves a trip to dates (preferring the Kayak feed), computes the drop-off /
pickup window (afternoon-before / morning-after), generates the activity slate
per the activity rules, and proposes Gina-coordination messages for any handoff
day she'd be at her mother's. Proposes only — sends nothing.

Usage:
  python3 pallo-trip-plan.py --trip-name London
  python3 pallo-trip-plan.py --trip-start 2026-07-12 --trip-end 2026-07-14

Output: JSON plan (see keys below). `plan_json` is an opaque blob carried
forward to pallo-book-trip.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import triplib  # noqa: E402


def _build_plan(trip_name: str, trip_start: date, trip_end: date) -> dict:
    drop_off = trip_start - timedelta(days=1)   # afternoon before departure
    pick_up = trip_end + timedelta(days=1)       # morning after return
    slate = triplib.activity_slate(drop_off, pick_up)

    gina_messages = []
    for label, d in (("dropoff", drop_off), ("pickup", pick_up)):
        try:
            res = triplib.gina_residency(d.isoformat())
        except triplib.CalendarError:
            res = {"residency": "unknown", "event_title": None}
        if res["residency"] in ("gina_mom", "unknown"):
            when = "afternoon" if label == "dropoff" else "morning"
            note = "" if res["residency"] == "gina_mom" else \
                " (couldn't confirm Gina's location — please check)"
            gina_messages.append({
                "date": d.isoformat(),
                "handoff": label,
                "recipient": "gina",
                "topic": f"Pallo {label}",
                "body": f"Need the Model X on {d.strftime('%a %b %-d')} {when} "
                        f"for Pallo {label} at Laurel Acres.{note}",
                "residency": res["residency"],
            })

    plan = {
        "trip_name": trip_name,
        "trip_start": trip_start.isoformat(),
        "trip_end": trip_end.isoformat(),
        "drop_off": drop_off.isoformat(),
        "pick_up": pick_up.isoformat(),
        "nights": (pick_up - drop_off).days,
        "activity_slate": slate,
        "gina_messages": gina_messages,
    }
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trip-name", default=None)
    ap.add_argument("--trip-start", default=None, help="ISO date (with --trip-end).")
    ap.add_argument("--trip-end", default=None, help="ISO date (with --trip-start).")
    args = ap.parse_args()

    trip_name = args.trip_name
    if args.trip_start and args.trip_end:
        try:
            ts = date.fromisoformat(args.trip_start)
            te = date.fromisoformat(args.trip_end)
        except ValueError:
            print(json.dumps({"status": "dates_invalid"}, indent=2))
            return 2
        if te < ts:
            print(json.dumps({"status": "dates_invalid", "reason": "trip-end before trip-start"}, indent=2))
            return 2
        trip_name = trip_name or f"{ts.isoformat()}..{te.isoformat()}"
    elif trip_name:
        try:
            res = triplib.resolve_trip_by_name(trip_name)
        except triplib.CalendarError as e:
            print(json.dumps({"status": "calendar_error", "reason": str(e)}, indent=2))
            return 1
        if res["status"] != "ok":
            print(json.dumps(res, indent=2))
            return 0
        ts = date.fromisoformat(res["trip_start"])
        te = date.fromisoformat(res["trip_end"])
        trip_name = res["trip_name"]
    else:
        print(json.dumps({"status": "error",
                          "reason": "supply --trip-name or both --trip-start and --trip-end"}, indent=2))
        return 2

    plan = _build_plan(trip_name, ts, te)
    plan["status"] = "ok"
    plan["plan_json"] = json.dumps({k: plan[k] for k in (
        "trip_name", "trip_start", "trip_end", "drop_off", "pick_up",
        "activity_slate", "gina_messages")})
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
