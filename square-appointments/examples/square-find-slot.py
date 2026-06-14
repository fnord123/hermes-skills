#!/usr/bin/env python3
"""square-find-slot.py — find available slots near a target date at a configured merchant.

Behavior:
1. Runs square-list.py to fetch existing bookings; if any falls within
   ±window-days of --around, returns status="already_have" and stops.
2. Otherwise opens the merchant's booking URL with Playwright, drills into
   default_service_id, and scrapes up to 5 candidate slots near the target.

Returns: small structured JSON. The agent passes slot_handle back to
square-cancel.py / square-move.py opaquely.

Usage:
  python3 square-find-slot.py --merchant <alias> --around <YYYY-MM-DD>
  python3 square-find-slot.py --merchant <alias> --around "next week"
  python3 square-find-slot.py --merchant <alias> --around 2026-07-15 --probe
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MERCHANTS = Path.home() / ".config" / "square-appointments" / "merchants.json"

# Self-bootstrap: if a local venv exists and we're not already in it, re-exec.
# Lets the agent invoke every script in this skill as `python3 <path>` even
# when the script needs playwright (which lives in the venv).
_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

# Below this point we are running under the venv interpreter.
from playwright.sync_api import sync_playwright  # noqa: E402
from playwright_stealth import Stealth  # noqa: E402


# ── target-date parsing ──────────────────────────────────────────────────────

_RELATIVE_RES = [
    (re.compile(r"^in\s+(\d+)\s+days?$"), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r"^in\s+(\d+)\s+weeks?$"), lambda m: timedelta(days=int(m.group(1)) * 7)),
    (re.compile(r"^(\d+)\s+days?\s+from\s+now$"), lambda m: timedelta(days=int(m.group(1)))),
]


def parse_around(s: str) -> datetime:
    # Try ISO first.
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    sl = s.lower().strip()
    if sl == "today":
        return today
    if sl == "tomorrow":
        return today + timedelta(days=1)
    if sl == "next week":
        return today + timedelta(days=7)
    if sl == "next month":
        return today + timedelta(days=30)
    for rx, fn in _RELATIVE_RES:
        m = rx.match(sl)
        if m:
            return today + fn(m)
    raise SystemExit(f"could not parse --around value: {s!r}. "
                     "Use ISO date (e.g. 2026-07-15) or simple relative ('tomorrow', 'next week', 'in 3 days').")


# ── collision check (via square-list.py) ─────────────────────────────────────

def get_existing_bookings(alias: str) -> list[dict]:
    """Re-use square-list.py as the source of truth for current bookings."""
    sl = SCRIPT_DIR / "square-list.py"
    try:
        res = subprocess.run(
            [sys.executable, str(sl), "--merchant", alias],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return []
    if res.returncode != 0:
        # Don't block find-slot on a list failure — agent will hear no collision
        # and proceed to slot search; surface the error in the response.
        return []
    try:
        return json.loads(res.stdout).get("bookings", [])
    except json.JSONDecodeError:
        return []


def find_collision(bookings: list[dict], target_dt: datetime, window_days: int) -> dict | None:
    window = timedelta(days=window_days)
    for b in bookings:
        iso = b.get("start_time_iso")
        if not iso:
            continue
        try:
            bdt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if abs(bdt - target_dt) <= window:
            return b
    return None


# ── playwright scrape ────────────────────────────────────────────────────────

def _service_url(booking_url: str, service_id: str) -> str:
    """Compose `<booking_url>/<service_id>` cleanly even if booking_url already
    has a `/services` suffix. Square's URLs look like:
       `…/location/<lid>/services` (catalogue)  → append `/<service_id>` for detail
       `…/location/<lid>/services/<sid>` (detail) → already there
    """
    base = booking_url.rstrip("/")
    if base.endswith(f"/services/{service_id}"):
        return base
    if base.endswith("/services"):
        return f"{base}/{service_id}"
    if "/services/" in base:
        # base already points at a (possibly wrong) service detail
        return re.sub(r"/services/[^/]+$", f"/services/{service_id}", base)
    return f"{base}/services/{service_id}"


_TIME_SLOT_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*([AP]M)\b", re.IGNORECASE)


def scrape_service_page(url: str, target_dt: datetime, probe: bool) -> dict:
    """Drive Square's public booking flow:
      service-detail → click Regular pricing + Any staff + Add → upsell page
      → click Next → availability page (week-view date picker)
      → navigate weeks forward to target → click target date → read time slots

    Returns small structured observations. In probe mode we stop after the
    click chain and dump the page state so we can refine the scraper.
    """
    obs: dict[str, Any] = {"url": url, "target_date": target_dt.date().isoformat(),
                           "click_log": [], "slots": []}

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        page = ctx.new_page()
        try:
            r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            obs["http_status"] = r.status if r else None
        except Exception as e:
            obs["error"] = f"goto failed: {str(e).splitlines()[0][:200]}"
            browser.close()
            return obs
        page.wait_for_timeout(5000)
        obs["final_url"] = page.url

        def try_click(label: str, *selectors: str) -> bool:
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.count() == 0:
                        continue
                    loc.click(timeout=4000)
                    obs["click_log"].append({"label": label, "selector": sel,
                                             "url_after": page.url})
                    page.wait_for_timeout(2500)
                    return True
                except Exception as e:
                    obs["click_log"].append({"label": label, "selector": sel,
                                             "error": str(e).splitlines()[0][:160]})
            return False

        # Service-detail panel: select pricing radio + staff radio + Add.
        # The radios live behind <label> elements; clicking the label toggles
        # them silently. "Add" is a Square <market-button> custom element.
        try_click("price_regular", 'text="Regular"')
        try_click("staff_any", 'text="Any staff"')
        try_click("add_service",
                  'market-button:has-text("Add")', 'text="Add"')
        # Upsell page → advance.
        try_click("skip_upsell",
                  'market-button:has-text("Next")',
                  'market-button:has-text("Continue")',
                  'market-button:has-text("Skip")',
                  'market-button:has-text("No thanks")')

        obs["url_after_clicks"] = page.url

        # ── Date-picker navigation ─────────────────────────────────────────
        # Square's availability page renders a week-view picker. data-testids:
        #   weekview-date-picker, prior-week-button, next-week-button,
        #   past-week, present-week, future-week,
        #   date-<day-of-month>  (e.g. date-15)
        # Today's cell is `date-<day>-selected`. Each visible week spans 7
        # day cells. We advance the week until the target date is selectable,
        # then click it.
        try:
            page.locator('[data-testid="availability-page"]').first.wait_for(timeout=8000)
        except Exception as e:
            obs["error_at_availability"] = f"didn't reach availability page: {str(e).splitlines()[0][:160]}"
            browser.close()
            return obs

        today = datetime.now().date()
        target_date = target_dt.date()
        clicked_date = False
        if probe:
            obs["available_date_testids"] = [
                d.get_attribute("data-testid")
                for d in page.locator('[data-testid^="date-"]').all()[:25]
                if d.get_attribute("data-testid")
            ]

        # Cap iterations so we never hang if the picker doesn't advance.
        max_week_advances = 20
        for _ in range(max_week_advances):
            target_tid = f"date-{target_date.day}"
            target_loc = page.locator(f'[data-testid="{target_tid}"], [data-testid="{target_tid}-selected"]').first
            # The week-view shows 3 weeks at a time. We check whether the
            # target day-of-month is visible; if multiple months share the
            # same day-of-month visible, we cross-check using the week-row
            # labels which include short month names.
            if target_loc.count() and _date_in_current_view(page, target_date):
                try:
                    target_loc.click(timeout=4000)
                    obs["click_log"].append({"label": "target_date",
                                             "selector": target_tid,
                                             "url_after": page.url})
                    clicked_date = True
                    page.wait_for_timeout(2500)
                    break
                except Exception as e:
                    obs["click_log"].append({"label": "target_date",
                                             "error": str(e).splitlines()[0][:160]})
                    break
            # Not yet visible: jump a week forward (or backward if target is
            # in the past, which shouldn't happen given parse_around but is
            # safe to handle).
            direction = "next-week-button" if target_date >= today else "prior-week-button"
            try:
                page.locator(f'[data-testid="{direction}"]').first.click(timeout=4000)
                page.wait_for_timeout(800)
            except Exception:
                break

        if not clicked_date:
            obs["error_date_not_reachable"] = (
                f"could not reach {target_date.isoformat()} from today "
                f"({today.isoformat()}) in {max_week_advances} week advances"
            )

        # ── Time slots ─────────────────────────────────────────────────────
        # After selecting a date the page lists available start times. They
        # render as <market-button>s with text like "2:00 PM". We collect
        # every distinct HH:MM AM/PM seen, dedupe in order, and return up to 5
        # that are >= the target time (the agent picks "around the 20th" so
        # we don't try to micro-optimise which slot of the day).
        slot_buttons = page.locator(
            'market-button:has-text("AM"), market-button:has-text("PM")'
        ).all()
        seen: set[str] = set()
        for btn in slot_buttons:
            try:
                txt = (btn.text_content() or "").strip()
            except Exception:
                continue
            m = _TIME_SLOT_RE.search(txt)
            if not m:
                continue
            time_label = f"{m.group(1)} {m.group(2).upper()}"
            if time_label in seen:
                continue
            seen.add(time_label)
            # slot_handle encodes everything cancel-step would need to
            # *re-find* this slot deterministically: target date + time +
            # service URL. For now we keep it as a small JSON blob the
            # caller passes through opaquely.
            slot_handle = json.dumps({
                "service_url": url,
                "date": target_date.isoformat(),
                "time": time_label,
            }, sort_keys=True, separators=(",", ":"))
            obs["slots"].append({
                "slot_handle": slot_handle,
                "start_time": f"{target_date.isoformat()} {time_label}",
                "label": time_label,
            })
            if len(obs["slots"]) >= 5:
                break

        if probe:
            try:
                obs["body_text_head"] = page.locator("body").inner_text()[:2500]
            except Exception:
                obs["body_text_head"] = ""

        browser.close()
    return obs


_MONTH_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _visible_month(page) -> tuple[int, int] | None:
    """Return (year, month) currently shown by the availability-page header,
    e.g. ('Jul 2026' → (2026, 7)). Returns None if not parseable."""
    try:
        text = page.locator('[data-testid="availability-page"]').first.text_content() or ""
    except Exception:
        return None
    m = re.match(r"\s*([A-Z][a-z]{2})\s+(\d{4})", text)
    if not m:
        return None
    mon = _MONTH_NUM.get(m.group(1))
    return (int(m.group(2)), mon) if mon else None


def _date_in_current_view(page, target_date) -> bool:
    """Gate on (a) visible month matching the target's month-year AND (b) the
    target day-of-month appearing in one of the three visible week rows.
    Without the month gate, a `date-20` cell for June 20 would be mistaken
    for August 20."""
    vis = _visible_month(page)
    if vis != (target_date.year, target_date.month):
        return False
    try:
        rows_text = " ".join(
            (page.locator(f'[data-testid="{r}"]').first.text_content() or "")
            for r in ("past-week", "present-week", "future-week")
        )
    except Exception:
        return False
    return bool(re.search(rf"\b{target_date.day}\b", rows_text))


# ── env / merchants loading (small dup of square-list internals) ─────────────

def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--around", required=True, help="ISO date (2026-07-15) or relative ('tomorrow', 'next week', 'in 3 days').")
    ap.add_argument("--window-days", type=int, default=7, help="Collision-check window. Default ±7 days.")
    ap.add_argument("--probe", action="store_true", help="Skip the collision check and return raw page observations from the booking site.")
    args = ap.parse_args()

    env = load_env(SCRIPT_DIR / ".env")
    merchants_file = Path(env.get("MERCHANTS_FILE") or os.environ.get("MERCHANTS_FILE") or DEFAULT_MERCHANTS)
    if not merchants_file.exists():
        print(json.dumps({"error": "merchants file not found", "path": str(merchants_file)}, indent=2))
        return 2
    merchants = json.loads(merchants_file.read_text())
    cfg = merchants.get(args.merchant)
    if not cfg:
        print(json.dumps({"error": f"merchant '{args.merchant}' not configured",
                          "configured_aliases": sorted(merchants.keys())}, indent=2))
        return 2

    target = parse_around(args.around)

    # Step 1: collision check (skipped in probe mode so we can isolate page scraping).
    if not args.probe:
        existing = get_existing_bookings(args.merchant)
        coll = find_collision(existing, target, args.window_days)
        if coll:
            print(json.dumps({"status": "already_have", "existing": coll, "target_date": target.date().isoformat()}, indent=2))
            return 0

    booking_url = (cfg.get("booking_url") or "").strip()
    service_id = (cfg.get("default_service_id") or "").strip()
    if not booking_url or not service_id:
        print(json.dumps({
            "status": "error",
            "reason": f"merchant '{args.merchant}' needs booking_url and default_service_id configured in merchants.json",
        }, indent=2))
        return 2

    url = _service_url(booking_url, service_id)
    obs = scrape_service_page(url, target, probe=args.probe)

    if args.probe:
        print(json.dumps({"status": "probe", "observations": obs}, indent=2))
        return 0

    if obs.get("slots"):
        print(json.dumps({
            "status": "ok",
            "target_date": target.date().isoformat(),
            "slots": obs["slots"],
        }, indent=2))
        return 0

    # No collision AND no slot scrape: degrade gracefully and return the
    # booking URL so the user can finish the booking themselves. This is
    # honest about Square's date-picker fragility — the controls Square
    # exposes (`next-week-button`, etc.) are CSS-hidden in current pages,
    # and there isn't an obvious public alternative for the agent to drive.
    print(json.dumps({
        "status": "no_collision_use_url",
        "target_date": target.date().isoformat(),
        "merchant_alias": args.merchant,
        "merchant_name": merchants[args.merchant].get("name"),
        "booking_url": url,
        "message": (
            "No existing appointment in the ±{}-day window. The agent can't "
            "scrape time slots automatically (Square's calendar advances are "
            "hidden behind CSS), so open the booking URL in a browser to pick "
            "a time."
        ).format(args.window_days),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
