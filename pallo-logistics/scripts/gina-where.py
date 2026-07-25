#!/usr/bin/env python3
"""gina-where.py — where is Gina (or Sky) on a given date?

Read-only, stdlib-only. Thin wrapper over the merged calendar's 2Houses feed.
Classifies residency from the "<child> with <parent>" event titles.

Usage:
  python3 gina-where.py --date 2026-07-12
  python3 gina-where.py --date 2026-07-12 --who Sky

Output: JSON {date, who, residency, event_title}. residency is one of
user_home | gina_mom | traveling_with_user | unknown.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import triplib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="ISO date YYYY-MM-DD.")
    ap.add_argument("--who", default="Gina", help="Person to locate (default Gina).")
    args = ap.parse_args()

    try:
        date.fromisoformat(args.date)
    except ValueError:
        print(json.dumps({"error": f"--date must be ISO YYYY-MM-DD; got {args.date!r}"}, indent=2))
        return 2

    try:
        res = triplib.gina_residency(args.date, who=args.who)
    except triplib.CalendarError as e:
        print(json.dumps({"status": "calendar_error", "reason": str(e)}, indent=2))
        return 1

    res["who"] = args.who
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
