#!/usr/bin/env python3
"""schedule-find.py — find events matching a keyword over a lookback/lookahead window.

Read-only. Stdlib-only. Case-insensitive substring search across title,
location, organizer, and description fields.

Use this for:
  - "Do I have a dentist appointment in the next month?"
  - "When is my standup with Sarah?"
  - "Find anything about 'flight' on my calendar."

Usage:
  python3 schedule-find.py --query <text>
  python3 schedule-find.py --query <text> --days-ahead 60 --days-back 7

Defaults: --days-back 7 (lookback), --days-ahead 30 (lookahead). The
small lookback is useful because the agent often asks about events that
happened recently ("when did I meet with so-and-so?") and a too-tight
window would miss them.

Output: JSON `{query, days_back, days_ahead, timezone, matches: [Event, ...]}`,
sorted by start time ascending (so past matches come first, then upcoming).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import ical_lib  # noqa: E402


def _matches(event: ical_lib.Event, q: str) -> bool:
    """Case-insensitive substring match against title, location, organizer,
    and description. None values skipped safely."""
    ql = q.lower()
    for field in (event.title, event.location, event.organizer, event.description):
        if field and ql in field.lower():
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True, help="Search term (case-insensitive substring).")
    ap.add_argument("--days-ahead", type=int, default=30, help="Days into the future to search. Default 30.")
    ap.add_argument("--days-back", type=int, default=7, help="Days into the past to search. Default 7.")
    args = ap.parse_args()

    env = ical_lib.load_env(SCRIPT_DIR / ".env")
    url = ical_lib.env_value(env, "GCAL_ICAL_KEY")
    if not url:
        ical_lib.emit_json({"error": "GCAL_ICAL_KEY is not set in .env or environment."})
        return 2

    tz = ical_lib.resolve_tz(env)
    today = datetime.now(tz=tz).date()
    start = today - timedelta(days=max(0, args.days_back))
    end = today + timedelta(days=max(0, args.days_ahead))

    people_file = ical_lib.env_value(env, "CALENDAR_PEOPLE_JSON") or None
    events = ical_lib.fetch_and_parse(
        url, tz, min_date=start, max_date=end, people_file=people_file
    )

    matches = [e.to_dict() for e in events if _matches(e, args.query)]

    ical_lib.emit_json({
        "query": args.query,
        "days_back": args.days_back,
        "days_ahead": args.days_ahead,
        "timezone": str(tz),
        "search_start": start.isoformat(),
        "search_end": end.isoformat(),
        "count": len(matches),
        "matches": matches,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
