#!/usr/bin/env python3
"""calrt.py — list the team events in a date range (read-only).

Answers "what is on the shared team calendar for a day or a range". The range
is inclusive on both ends; a single day passes the same date twice.

  python3 calrt.py --start 2026-09-01 --end 2026-09-03

The calendar data is read from the file named by the CALRT_TEAM_CALENDAR
environment variable — a JSON array of event objects, each with at least the
keys title, start, end, people and description, where start/end are ISO
datetime strings. If that variable is unset, or the file is missing, or the
data can't be read, the script fails with exit 1. Nothing else is a valid
input source.

Output: exactly one JSON object on stdout. Success is
{"ok": true, "count": <int>, "events": [...]} with exit 0 — `count` is the
number of team events in the range and `events` is the list of those event
objects, sorted by start time. A failure is {"ok": false, "error": "..."}
with exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_json import ok, fail, guard  # noqa: E402

CALENDAR_ENV = "CALRT_TEAM_CALENDAR"


def _load_events(path: str) -> list:
    """Read the calendar file and return its event objects, or fail.

    Fails (exit 1) when the file is missing, unreadable, is not a JSON array,
    or an element lacks a parseable start/end — a calendar we can't interpret
    is an error, never a silent partial list.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("the team calendar can't be found")
    except OSError:
        fail("the team calendar can't be read")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fail("the team calendar data is malformed")
    if not isinstance(data, list):
        fail("the team calendar data is malformed")
    events = []
    for ev in data:
        if not isinstance(ev, dict):
            fail("the team calendar data is malformed")
        for key in ("start", "end"):
            if key not in ev:
                fail("the team calendar data is malformed")
            try:
                datetime.fromisoformat(ev[key])
            except (TypeError, ValueError):
                fail("the team calendar data is malformed")
        events.append(ev)
    return events


def _window(start_d: date, end_d: date):
    """Closed date window: the start day at 00:00:00 through the end day's last instant."""
    return datetime.combine(start_d, time.min), datetime.combine(end_d, time.max)


def _overlaps(ev: dict, lo: datetime, hi: datetime) -> bool:
    """True when the event's [start, end] span overlaps the [lo, hi] window (inclusive)."""
    es = datetime.fromisoformat(ev["start"])
    ee = datetime.fromisoformat(ev["end"])
    return es <= hi and ee >= lo


def _sort_key(ev: dict):
    return (datetime.fromisoformat(ev["start"]),
            datetime.fromisoformat(ev["end"]),
            str(ev.get("title", "")))


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="Range start, ISO date (YYYY-MM-DD, inclusive).")
    ap.add_argument("--end", required=True, help="Range end, ISO date (YYYY-MM-DD, inclusive).")
    args = ap.parse_args()

    try:
        start_d = date.fromisoformat(args.start)
    except ValueError:
        fail("--start must be an ISO date like 2026-09-01; got %r" % args.start)
    try:
        end_d = date.fromisoformat(args.end)
    except ValueError:
        fail("--end must be an ISO date like 2026-09-01; got %r" % args.end)
    if end_d < start_d:
        fail("--end is before --start")

    env_path = os.environ.get(CALENDAR_ENV)
    if not env_path:
        fail("the team calendar isn't connected yet")
    events = _load_events(env_path)

    lo, hi = _window(start_d, end_d)
    matched = sorted((ev for ev in events if _overlaps(ev, lo, hi)), key=_sort_key)
    ok(count=len(matched), events=matched)


if __name__ == "__main__":
    main()
