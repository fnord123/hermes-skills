#!/usr/bin/env python3
"""pallo-stays.py — list Pallo's boarding reservations from the Gingr portal.

Read-only. Reads the authoritative bookings list from the Laurel Acres /
Tail Wag Inn customer portal using the saved session (see gingr-login.py).
The portal omits the year from its date labels; we recover it from the
weekday each label carries.

Usage:
  python3 pallo-stays.py                       # upcoming + in-progress stays
  python3 pallo-stays.py --include-past         # also Completed / past stays
  python3 pallo-stays.py --include-canceled     # also Canceled stays
  python3 pallo-stays.py --all                  # everything the portal shows

Output: JSON `{anchor_date, count, stays: [stay, ...]}` sorted by start date.
Each stay: {stay_id, pet, service_type, location, start_date, end_date,
nights, status}. `stay_id` is "<start>/<end>" (ISO) — a stable, re-locatable
handle for cancel/modify.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

import gingr_lib  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _anchor_date():
    tz_name = os.environ.get("SCHEDULE_TZ") or "America/Los_Angeles"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    return datetime.now(tz=tz).date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-past", action="store_true",
                    help="Include Completed / already-ended stays.")
    ap.add_argument("--include-canceled", action="store_true",
                    help="Include Canceled stays.")
    ap.add_argument("--all", action="store_true",
                    help="Include everything (past + canceled).")
    args = ap.parse_args()

    if not gingr_lib.state_exists():
        print(json.dumps({
            "status": "not_logged_in",
            "reason": "No saved Gingr session. Run gingr-login.py first.",
        }, indent=2))
        return 2

    anchor = _anchor_date()
    try:
        with sync_playwright() as p:
            browser, ctx = gingr_lib.new_logged_in_context(p)
            page = ctx.new_page()
            try:
                cards = gingr_lib.fetch_booking_cards(page)
            finally:
                browser.close()
    except gingr_lib.SessionExpired:
        print(json.dumps({
            "status": "session_expired",
            "reason": "Saved Gingr session no longer authenticates. Refresh via gingr-import-session.py (see skill: session-refresh instructions).",
        }, indent=2))
        return 1
    except gingr_lib.BookingsPageAnomaly as e:
        print(json.dumps({"status": "error", "reason": str(e)}, indent=2))
        return 1
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "reason": f"{type(e).__name__}: {e}"}, indent=2))
        return 1

    stays = []
    for text in cards:
        parsed = gingr_lib.parse_card(text, anchor)
        if parsed:
            stays.append(parsed)

    # de-dupe (same stay can render twice in the SPA) and sort
    seen = set()
    deduped = []
    for s in stays:
        key = (s["stay_id"], s["status"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    deduped.sort(key=lambda s: s["start_date"])

    show_past = args.include_past or args.all
    show_canceled = args.include_canceled or args.all
    out = []
    for s in deduped:
        if s["status"] == "Canceled" and not show_canceled:
            continue
        ended = s["end_date"] < anchor.isoformat()
        if (s["status"] == "Completed" or ended) and not show_past:
            continue
        out.append(s)

    print(json.dumps({
        "status": "ok",
        "anchor_date": anchor.isoformat(),
        "count": len(out),
        "stays": out,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
