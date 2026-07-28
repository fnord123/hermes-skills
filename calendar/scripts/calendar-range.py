#!/usr/bin/env python3
"""calendar-range.py — list events in a date range.

Read-only. Stdlib-only. Use this for:
  - "What does my week look like?"
  - "What's on next Wednesday?"
  - "Anything scheduled June 20–22?"

Date inputs are inclusive on both ends. Both `--start` and `--end` must be
ISO dates (YYYY-MM-DD); we don't accept natural-language phrases here on
purpose — the agent should resolve relative phrases to ISO before calling.

The span is capped at MAX_SPAN_DAYS. Every recurring series has to be expanded
day by day across the window, so an unbounded range (`1900-01-01` to
`2100-01-01`) does hundreds of years of work and never returns.

Output: JSON `{ok: true, start, end, timezone, days: [{date, events: [Event,
...]}, ...]}` with days sorted ascending and events within each day sorted by
start time; `{ok: false, error}` with exit 1 on failure.
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
from skill_json import ok, fail, guard  # noqa: E402

# Widest window we will expand. Comfortably covers "the next year" while
# refusing a request that would expand every recurring series for centuries.
MAX_SPAN_DAYS = 400


def _parse_iso_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        fail(f"--start/--end must be a date like 2026-06-20; got {s!r}")


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="Start date (ISO, inclusive).")
    ap.add_argument("--end", required=True, help="End date (ISO, inclusive).")
    args = ap.parse_args()

    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)
    if end < start:
        fail("--end is before --start", start=str(start), end=str(end))

    span = (end - start).days + 1
    if span > MAX_SPAN_DAYS:
        fail(f"the requested range covers {span} days; ask for a window of "
             f"{MAX_SPAN_DAYS} days or fewer", start=str(start), end=str(end))

    env = ical_lib.load_env(SCRIPT_DIR / ".env")
    feeds = ical_lib.resolve_feeds(env)
    if not feeds:
        fail("No calendar feeds configured (set GCAL_ICAL_KEY in .env).")

    tz = ical_lib.resolve_tz(env)
    people_file = ical_lib.env_value(env, "CALENDAR_PEOPLE_JSON") or None

    events, feed_errors = ical_lib.fetch_and_parse_multi(
        feeds, tz, min_date=start, max_date=end, people_file=people_file
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

    ok(
        start=start.isoformat(),
        end=end.isoformat(),
        timezone=str(tz),
        total_events=sum(len(day["events"]) for day in days),
        days=days,
        feed_errors=feed_errors,
    )


if __name__ == "__main__":
    main()
