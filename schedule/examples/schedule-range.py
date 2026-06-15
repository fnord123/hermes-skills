#!/usr/bin/env python3
"""schedule-range.py — list events in a date range.

Read-only. Stdlib-only. Use this for:
  - "What does my week look like?"
  - "What's on next Wednesday?"
  - "Anything scheduled June 20–22?"

Date inputs are inclusive on both ends. Both `--start` and `--end` must be
ISO dates (YYYY-MM-DD); we don't accept natural-language phrases here on
purpose — the agent should resolve relative phrases to ISO before calling.

Output: JSON `{start, end, timezone, days: [{date, events: [Event, ...]}, ...]}`
with days sorted ascending and events within each day sorted by start time.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ical_lib  # noqa: E402


def _parse_iso_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SystemExit(f"--start/--end must be ISO date YYYY-MM-DD; got {s!r} ({e})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="Start date (ISO, inclusive).")
    ap.add_argument("--end", required=True, help="End date (ISO, inclusive).")
    args = ap.parse_args()

    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)
    if end < start:
        ical_lib.emit_json({"error": "--end is before --start", "start": str(start), "end": str(end)})
        return 2

    env = ical_lib.load_env(SCRIPT_DIR / ".env")
    url = ical_lib.env_value(env, "GCAL_ICAL_KEY")
    if not url:
        ical_lib.emit_json({"error": "GCAL_ICAL_KEY is not set in .env or environment."})
        return 2

    tz = ical_lib.resolve_tz(env)
    people_file = ical_lib.env_value(env, "CALENDAR_PEOPLE_JSON") or None

    events = ical_lib.fetch_and_parse(
        url, tz, min_date=start, max_date=end, people_file=people_file
    )

    by_day: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        # ev.start is ISO; take the date prefix
        by_day[ev.start[:10]].append(ev.to_dict())

    # Always include a stub for every day in [start, end], even if empty.
    days: list[dict] = []
    d = start
    while d <= end:
        days.append({"date": d.isoformat(), "events": by_day.get(d.isoformat(), [])})
        d = d.fromordinal(d.toordinal() + 1)

    ical_lib.emit_json({
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": str(tz),
        "total_events": sum(len(day["events"]) for day in days),
        "days": days,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
