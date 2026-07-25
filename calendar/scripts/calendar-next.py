#!/usr/bin/env python3
"""calendar-next.py — list the next upcoming event(s).

Read-only. Stdlib-only.

Use this for:
  - "What's next on my calendar?"
  - "Do I have anything in the next hour?"
  - "What's my next meeting today?"

Usage:
  python3 calendar-next.py                        # next 3 events within 48h
  python3 calendar-next.py --within 24 --limit 5  # next 5 events within 24h
  python3 calendar-next.py --within 168           # next event(s) in the next week

"Next" is measured from the current wall-clock time in the configured
timezone (SCHEDULE_TZ or BRIEFING_TZ). All-day events are considered to
start at 00:00 local time of their date — so they count as "upcoming" until
the day ends, and "in progress" during their day.

Output: JSON `{now, within_hours, timezone, events: [Event, ...]}` with up
to --limit events, sorted by start time.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ical_lib  # noqa: E402


def _event_start_dt(event: ical_lib.Event, tz):
    """Project an Event's start into a tz-aware datetime for comparison.
    All-day events get midnight on their date."""
    s = event.start
    if event.all_day:
        d = datetime.fromisoformat(s).date() if "T" not in s else datetime.fromisoformat(s).date()
        return datetime.combine(d, time.min, tzinfo=tz)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--within", type=float, default=48.0,
                    help="Look ahead this many hours. Default 48.")
    ap.add_argument("--limit", type=int, default=3,
                    help="Maximum number of events to return. Default 3.")
    args = ap.parse_args()

    env = ical_lib.load_env(SCRIPT_DIR / ".env")
    feeds = ical_lib.resolve_feeds(env)
    if not feeds:
        ical_lib.emit_json({"error": "No calendar feeds configured (set GCAL_ICAL_KEY in .env)."})
        return 2

    tz = ical_lib.resolve_tz(env)
    now = datetime.now(tz=tz)
    horizon = now + timedelta(hours=args.within)

    # We pull a slightly wider date band than the strict hour window so we
    # catch all-day events on the horizon day, and we filter by wall-clock
    # time afterwards.
    people_file = ical_lib.env_value(env, "CALENDAR_PEOPLE_JSON") or None
    events, feed_errors = ical_lib.fetch_and_parse_multi(
        feeds, tz,
        min_date=now.date(),
        max_date=horizon.date(),
        people_file=people_file,
    )

    upcoming = []
    for ev in events:
        start_dt = _event_start_dt(ev, tz)
        if start_dt < now or start_dt > horizon:
            continue
        upcoming.append(ev.to_dict())

    upcoming.sort(key=lambda e: e["start"])
    upcoming = upcoming[: max(0, args.limit)]

    ical_lib.emit_json({
        "now": now.isoformat(),
        "within_hours": args.within,
        "limit": args.limit,
        "timezone": str(tz),
        "count": len(upcoming),
        "events": upcoming,
        "feed_errors": feed_errors,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
