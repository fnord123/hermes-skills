#!/usr/bin/env python3
"""calrt.py — list team events in a date range from the team calendar feed.

Read-only. Stdlib-only.

Usage:
  python3 calrt.py --start 2026-09-01 --end 2026-09-03
  python3 calrt.py --start 2026-09-01 --end 2026-09-01   (single day)

Output: exactly one JSON object on stdout. Success:
  {"ok": true, "count": <int>, "events": [...]} with exit 0. Failure:
  {"ok": false, "error": "..."} with exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_json import ok, fail, guard  # noqa: E402

MISFORMED = "the team calendar data is malformed"


def _parse_iso_date(value: str) -> datetime:
    """Parse an ISO YYYY-MM-DD date, or raise ValueError."""
    return datetime.strptime(value, "%Y-%m-%d")


def _load_events(path: Path) -> list:
    """Read and validate the team calendar feed.

    The feed is a JSON array of event objects, each carrying parseable ISO
    "start" and "end" datetimes. Any violation — an unreadable or unparseable
    file, a non-list, an event that is not an object, or a missing /
    unparseable start or end — fails the contract with one domain string.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable or not valid JSON
        fail(MISFORMED)
    if not isinstance(raw, list):
        fail(MISFORMED)
    for ev in raw:
        if not isinstance(ev, dict):
            fail(MISFORMED)
        try:
            datetime.fromisoformat(ev["start"])
            datetime.fromisoformat(ev["end"])
        except Exception:  # noqa: BLE001 - missing or unparseable datetime
            fail(MISFORMED)
    return raw


@guard
def main() -> None:
    # Stock argparse on purpose: a missing required flag raises SystemExit(2),
    # which @guard turns into the house "bad arguments" failure envelope.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True,
                    help="Start date (ISO YYYY-MM-DD), inclusive.")
    ap.add_argument("--end", required=True,
                    help="End date (ISO YYYY-MM-DD), inclusive.")
    args = ap.parse_args()

    try:
        start_dt = _parse_iso_date(args.start)
    except ValueError:
        fail("--start must be an ISO date like 2026-09-01; got '%s'" % args.start)
    try:
        end_dt = _parse_iso_date(args.end)
    except ValueError:
        fail("--end must be an ISO date like 2026-09-01; got '%s'" % args.end)
    if end_dt < start_dt:
        fail("--end is before --start")

    src = os.environ.get("CALRT_TEAM_CALENDAR")
    if not src:
        fail("the team calendar isn't connected yet")
    path = Path(src)
    if not path.is_file():
        fail("the team calendar can't be found")

    events = _load_events(path)

    # The window covers both endpoint days in full: the start day from
    # 00:00:00 and the end day through 23:59:59.999999. An event is IN iff it
    # overlaps that window (starts on/before the window end AND ends on/after
    # the window start).
    lo = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    hi = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    rows = []
    for ev in events:
        s = datetime.fromisoformat(ev["start"])
        e = datetime.fromisoformat(ev["end"])
        if s <= hi and e >= lo:
            rows.append((ev, s, e))
    rows.sort(key=lambda r: (r[1], r[2], r[0].get("title", "")))
    out = [r[0] for r in rows]
    ok(count=len(out), events=out)


if __name__ == "__main__":
    main()
