#!/usr/bin/env python3
"""pallo-trip-plan.py — build the boarding + activity plan for a trip (read-only).

Resolves a trip to dates (preferring the Kayak feed), computes the drop-off /
pickup window (afternoon-before / morning-after), generates the activity slate
per the activity rules, and proposes Gina-coordination messages for any handoff
day she'd be at her mother's. Proposes only — sends nothing.

Usage:
  python3 pallo-trip-plan.py --trip-name London
  python3 pallo-trip-plan.py --trip-start 2026-07-12 --trip-end 2026-07-14

  # When the user gives explicit boarding dates (not trip/travel dates), bypass
  # the automatic afternoon-before / morning-after buffer:
  python3 pallo-trip-plan.py --drop-date 2026-09-30 --pickup-date 2026-10-04

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
from skill_json import ok, fail, guard  # noqa: E402


def _build_plan(trip_name: str, trip_start: date, trip_end: date,
                drop_off: date | None = None, pick_up: date | None = None,
                drop_time: str = "08:00 AM", pickup_time: str = "09:00 AM") -> dict:
    if drop_off is None:
        drop_off = trip_start - timedelta(days=1)   # afternoon before departure
    if pick_up is None:
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
        "drop_time": drop_time,
        "pickup_time": pickup_time,
        "nights": (pick_up - drop_off).days,
        "activity_slate": slate,
        "gina_messages": gina_messages,
    }
    return plan


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trip-name", default=None)
    ap.add_argument("--trip-start", default=None, help="ISO date (with --trip-end). Buffer (day before/after) is applied automatically.")
    ap.add_argument("--trip-end", default=None, help="ISO date (with --trip-start). Buffer (day before/after) is applied automatically.")
    ap.add_argument("--drop-date", default=None,
                    help="Explicit kennel drop-off date (no buffer added). Use when the user gives boarding dates directly.")
    ap.add_argument("--pickup-date", default=None,
                    help="Explicit kennel pickup date (no buffer added). Use when the user gives boarding dates directly.")
    ap.add_argument("--drop-time", default="08:00 AM",
                    help="Drop-off clock time, e.g. '3pm' or '03:00 PM' (default 08:00 AM).")
    ap.add_argument("--pickup-time", default="09:00 AM",
                    help="Pickup clock time, e.g. '11am' or '11:00 AM' (default 09:00 AM).")
    args = ap.parse_args()

    try:
        drop_time = triplib.normalize_clock_time(args.drop_time)
        pickup_time = triplib.normalize_clock_time(args.pickup_time)
    except triplib.TimeFormatError as e:
        fail(str(e), status="dates_invalid")

    # Direct boarding dates — bypass the trip-date buffer entirely.
    explicit_drop = None
    explicit_pickup = None
    if args.drop_date or args.pickup_date:
        if not (args.drop_date and args.pickup_date):
            fail("supply both --drop-date and --pickup-date together",
                 status="error")
        try:
            explicit_drop = date.fromisoformat(args.drop_date)
            explicit_pickup = date.fromisoformat(args.pickup_date)
        except ValueError:
            fail("invalid ISO date in --drop-date / --pickup-date",
                 status="dates_invalid")
        # Same rule as pallo-book-trip.py: a stay is at least one night, so
        # pickup == drop is rejected here rather than at booking time.
        if explicit_pickup <= explicit_drop:
            fail("pickup must be after drop-off", status="dates_invalid")

    trip_name = args.trip_name
    if explicit_drop is not None:
        # No trip-date buffer: use the boarding dates directly as both
        # the "trip" window and the actual drop/pickup.
        ts = explicit_drop
        te = explicit_pickup
        trip_name = trip_name or f"{ts.isoformat()}..{te.isoformat()}"
    elif args.trip_start and args.trip_end:
        try:
            ts = date.fromisoformat(args.trip_start)
            te = date.fromisoformat(args.trip_end)
        except ValueError:
            fail("invalid ISO date in --trip-start / --trip-end",
                 status="dates_invalid")
        if te < ts:
            fail("trip-end before trip-start", status="dates_invalid")
        trip_name = trip_name or f"{ts.isoformat()}..{te.isoformat()}"
    elif trip_name:
        try:
            res = triplib.resolve_trip_by_name(trip_name)
        except triplib.CalendarError as e:
            fail(str(e), status="calendar_error")
        if res["status"] != "ok":
            # Informational outcomes (ambiguous_trip / no_trip_found), not
            # a script failure — ok:true with the status carried in the
            # payload, as before the JSON-contract conversion.
            ok(**res)
            return
        ts = date.fromisoformat(res["trip_start"])
        te = date.fromisoformat(res["trip_end"])
        trip_name = res["trip_name"]
    else:
        fail("supply --trip-name or both --trip-start and --trip-end",
             status="error")

    plan = _build_plan(trip_name, ts, te,
                       drop_off=explicit_drop, pick_up=explicit_pickup,
                       drop_time=drop_time, pickup_time=pickup_time)

    # Validate the window the booking would actually use — the same two rules
    # pallo-book-trip.py enforces, so a plan that validates here always books.
    plan_drop = date.fromisoformat(plan["drop_off"])
    plan_pick = date.fromisoformat(plan["pick_up"])
    today = date.today()
    if plan_pick <= plan_drop:
        fail("pickup must be after drop-off", status="dates_invalid",
             drop_off=plan["drop_off"], pick_up=plan["pick_up"])
    if plan_drop < today:
        fail(f"drop-off {plan['drop_off']} is in the past "
             f"(today is {today.isoformat()})",
             status="dates_invalid",
             drop_off=plan["drop_off"], pick_up=plan["pick_up"])

    plan["status"] = "ok"
    plan["plan_json"] = json.dumps({k: plan[k] for k in (
        "trip_name", "trip_start", "trip_end", "drop_off", "pick_up",
        "drop_time", "pickup_time", "activity_slate", "gina_messages")})
    ok(**plan)


if __name__ == "__main__":
    main()
