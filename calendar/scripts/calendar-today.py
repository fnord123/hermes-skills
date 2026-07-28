#!/usr/bin/env python3
"""calendar-today.py — list events on today's calendar.

Read-only. Sources data from the user's Google Calendar iCal URL
(GCAL_ICAL_KEY in .env). Stdlib-only.

Usage:
  python3 calendar-today.py

Output: JSON `{ok: true, date, timezone, events: [Event, ...]}` on success,
`{ok: false, error}` with exit 1 on failure. Events are sorted by start time.
Each Event has the same shape as `ical_lib.Event.to_dict()`.

Used to answer questions like:
  - "What's on my calendar today?"
  - "Do I have anything this morning?"
  - "What's my next meeting?"  (use calendar-next.py for that one — this
    script returns the whole day, not just the next item)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ical_lib  # noqa: E402
from skill_json import ok, fail, guard  # noqa: E402


@guard
def main() -> None:
    env = ical_lib.load_env(SCRIPT_DIR / ".env")
    feeds = ical_lib.resolve_feeds(env)
    if not feeds:
        fail("No calendar feeds configured (set GCAL_ICAL_KEY in .env).")

    tz = ical_lib.resolve_tz(env)
    today = datetime.now(tz=tz).date()

    people_file = ical_lib.env_value(env, "CALENDAR_PEOPLE_JSON") or None
    events, feed_errors = ical_lib.fetch_and_parse_multi(
        feeds, tz, min_date=today, max_date=today, people_file=people_file
    )

    ok(
        date=today.isoformat(),
        timezone=str(tz),
        count=len(events),
        events=[e.to_dict() for e in events],
        feed_errors=feed_errors,
    )


if __name__ == "__main__":
    main()
