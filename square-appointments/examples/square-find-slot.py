#!/usr/bin/env python3
"""square-find-slot.py — find available slots near a target date at a configured merchant.

Behavior:
1. Runs square-list.py to fetch existing bookings; if any falls within
   ±window-days of --around, returns status="already_have" and stops.
2. Otherwise opens the merchant's booking URL with Playwright to capture the
   widget's own availability API request, replays that API for the target
   window, and returns up to 5 bookable slots nearest the target.

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
from datetime import date, datetime, timedelta, timezone
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


def _slots_via_availability_api(
    page, post_data: str, api_url: str, target_date, window_days: int, today,
    origin: str, obs: dict | None = None,
) -> "list[tuple[date, str]] | None":
    """Replay Square's buyer/availability API for the target window and return
    [(date, 'H:MM AM/PM'), ...]. Returns None if the template can't be parsed
    or the call fails; the caller then surfaces the booking-URL fallback.

    The API takes an explicit ``start_at_range`` (≈32-day max) and returns each
    bookable slot's ``start`` as a Unix timestamp — exact, and immune to all the
    week-strip rendering quirks. We reuse the captured request body verbatim
    (it already carries the right service_variation_id, location, team filter,
    and tz offset) and only rewrite the date range.
    """
    try:
        body = json.loads(post_data)
        rng = body["search_availability_request"]["query"]["filter"]["start_at_range"]
    except Exception:
        return None
    m = re.search(r"([+-]\d{2}:\d{2})$", str(rng.get("start_at", "")))
    offset = m.group(1) if m else "+00:00"
    earliest = max(today, target_date - timedelta(days=window_days))
    latest = target_date + timedelta(days=window_days)
    if (latest - earliest).days > 31:  # respect the API's range cap
        latest = earliest + timedelta(days=31)
    rng["start_at"] = f"{earliest.isoformat()}T00:00:00.000{offset}"
    rng["end_at"] = f"{latest.isoformat()}T23:59:59.999{offset}"
    try:
        resp = page.request.post(
            api_url, data=json.dumps(body),
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                # REQUIRED: the endpoint 422s without an Origin matching the
                # booking site. The browser adds it automatically (it's a
                # forbidden header), so a naive replay omits it.
                "origin": origin,
                "referer": origin + "/",
            },
        )
        if obs is not None:
            obs["api_replay_status"] = resp.status
        if not resp.ok:
            return None
        data = resp.json()
    except Exception as e:
        if obs is not None:
            obs["api_replay_error"] = str(e).splitlines()[0][:200]
        return None
    sign = 1 if offset[0] == "+" else -1
    tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[date, str]] = []
    for a in data.get("availability") or []:
        if a.get("available") is False:
            continue
        ts = a.get("start")
        if ts is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts), tz)
        except Exception:
            continue
        d = dt.date()
        if not (earliest <= d <= latest):
            continue
        label = dt.strftime("%-I:%M %p")
        key = (d.isoformat(), label)
        if key in seen:
            continue
        seen.add(key)
        out.append((d, label))
    return out


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

        # Capture the booking widget's own availability API call. Square's
        # buyer/availability endpoint takes an explicit date range and returns
        # bookable slots as Unix timestamps — replaying it for the target
        # window is exact and reliable, vs. scraping the flaky week-strip DOM.
        avail_template: dict = {}

        def _capture_avail(resp):
            try:
                if "buyer/availability" not in resp.url:
                    return
                if avail_template.get("post_data"):
                    return
                pd = resp.request.post_data
                if pd:
                    avail_template["url"] = resp.url
                    avail_template["post_data"] = pd
                    try:
                        avail_template["headers"] = dict(resp.request.headers)
                    except Exception:
                        avail_template["headers"] = {}
            except Exception:
                pass

        page.on("response", _capture_avail)

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

        # ── Availability + slot extraction ────────────────────────────────
        # Square's availability page renders a 3-week strip with one date
        # auto-selected and time slots for that date below. Important
        # behaviours we discovered empirically:
        #   • Clicking `date-N` only takes effect if that date has availability.
        #     Dates without slots silently ignore the click and the selection
        #     reverts to whatever was selected before.
        #   • The "Go to next available" link is a reliable entry point: it
        #     jumps the selection to the soonest date that actually has slots
        #     within the visible window.
        #   • Time slots render as elements with `data-testid="time-slot"`
        #     whose text is the start time, e.g. "9:30 AM".
        #   • The currently-selected date carries `data-testid="date-<n>-selected"`.
        #   • The visible month is parseable from the `availability-page`
        #     element's text header (e.g. "Jun 2026").
        # The strategy below: click "Go to next available", read what landed,
        # and emit those slots if the landing date is within window_days of
        # the user's target. If it's beyond window, fall through to the URL
        # fallback so the user can drive a wider browser session themselves.
        try:
            page.locator('[data-testid="availability-page"]').first.wait_for(timeout=8000)
        except Exception as e:
            obs["error_at_availability"] = f"didn't reach availability page: {str(e).splitlines()[0][:160]}"
            browser.close()
            return obs

        today = datetime.now().date()
        target_date = target_dt.date()
        window_days = 14  # acceptance window around target for "around X" semantics

        # ── Fast, reliable path: replay the availability API for the window ──
        # The page fires buyer/availability on load; wait briefly for the
        # captured template, then query the target window directly.
        waited = 0
        while not avail_template.get("post_data") and waited < 8000:
            page.wait_for_timeout(500)
            waited += 500
        if avail_template.get("post_data"):
            api_origin = re.match(r"https?://[^/]+", url)
            api_slots = _slots_via_availability_api(
                page, avail_template["post_data"], avail_template["url"],
                target_date, window_days, today,
                api_origin.group(0) if api_origin else "https://book.squareup.com",
                obs,
            )
            if api_slots is not None:
                # Nearest-to-target first; earliest time as the within-day tiebreak.
                def _mins(lbl: str) -> int:
                    mm = _TIME_SLOT_RE.search(lbl)
                    if not mm:
                        return 0
                    hh, mi = mm.group(1).split(":")
                    hh = int(hh) % 12
                    if mm.group(2).upper() == "PM":
                        hh += 12
                    return hh * 60 + int(mi)

                api_slots.sort(key=lambda s: (
                    abs((s[0] - target_date).days), s[0].toordinal(), _mins(s[1]),
                ))
                for d, lab in api_slots[:5]:
                    obs["slots"].append({
                        "slot_handle": json.dumps({
                            "service_url": url, "date": d.isoformat(), "time": lab,
                        }, sort_keys=True, separators=(",", ":")),
                        "start_time": f"{d.isoformat()} {lab}",
                        "label": lab,
                        "date": d.isoformat(),
                    })
        # Template missing or the API call failed → obs["slots"] stays empty and
        # main() surfaces the booking-URL fallback. The former DOM week-walk was
        # removed: Square's calendar widget is unscriptable headless, so the
        # availability-API replay above is the only reliable path.
        browser.close()
        return obs

def discover_merchant_config(alias: str) -> tuple[str, str, str]:
    """Derive a merchant's booking_url + default_service_id from their most
    recent confirmation email when those fields aren't set in merchants.json.

    Returns (booking_url, default_service_id, service_name) on success;
    raises SystemExit with a descriptive message otherwise.

    Strategy:
      1. Re-use square-list.py with a wide --days-back to find ANY past
         booking for this merchant.
      2. Take the most recent booking_handle (the Square manage URL).
      3. Navigate to it. Square redirects to a confirmation page whose URL
         encodes merchant_id and location_id; that's enough to compose
         the public booking URL.
      4. Scrape the displayed service name from the confirmation page,
         then click on a matching service tile from /services and read the
         resulting `/services/<id>` segment.

    This costs roughly one extra browser session relative to the normal
    flow and saves the user from a manual merchants.json setup step. It is
    only triggered when at least one of booking_url / default_service_id
    is missing.
    """
    sl = SCRIPT_DIR / "square-list.py"
    try:
        res = subprocess.run(
            [sys.executable, str(sl), "--merchant", alias, "--days-back", "730"],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"auto-discover: square-list timed out looking up '{alias}'")
    if res.returncode != 0:
        raise SystemExit(f"auto-discover: square-list failed: {(res.stderr or res.stdout)[:200]}")
    try:
        bookings = json.loads(res.stdout).get("bookings", [])
    except json.JSONDecodeError:
        raise SystemExit("auto-discover: square-list returned non-JSON output")
    if not bookings:
        raise SystemExit(
            f"auto-discover: no past or upcoming bookings found for merchant '{alias}'. "
            f"Either configure booking_url and default_service_id in merchants.json, "
            f"or book at least once at this merchant so a confirmation email exists."
        )

    bookings.sort(key=lambda b: b.get("start_time_iso") or "", reverse=True)
    handle = bookings[0].get("booking_handle")
    if not handle:
        raise SystemExit("auto-discover: latest booking had no manage URL to follow")

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
        )
        page = ctx.new_page()
        try:
            page.goto(handle, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            browser.close()
            raise SystemExit(f"auto-discover: couldn't open manage URL: {str(e).splitlines()[0][:200]}")
        page.wait_for_timeout(4000)
        final = page.url
        m_url = re.search(
            r"book\.squareup\.com/appointments/([^/]+)/location/([^/]+)/",
            final,
        )
        if not m_url:
            browser.close()
            raise SystemExit(
                f"auto-discover: redirect URL not in expected shape: {final[:200]}"
            )
        merchant_id, location_id = m_url.group(1), m_url.group(2)
        booking_url = (
            f"https://book.squareup.com/appointments/"
            f"{merchant_id}/location/{location_id}/services"
        )

        # Read service name from the confirmation page body. Heuristic:
        # the service name shows just above the "Location" header, sandwiched
        # between an action-button label ("Book next appointment"), a staff
        # line ("with <name>"), and possibly staff initials. We walk
        # backwards from "Location" and skip known UI noise until we find
        # what looks like a service name.
        _SKIP_LINES = {
            "Paid", "Thank you for your payment.",
            "Book next appointment", "Cancel", "Reschedule",
            "Cancellation policy", "Appointment passed", "Upcoming",
        }
        service_name: str | None = None
        try:
            body_text = page.locator("body").inner_text()
            lines = [l.strip() for l in body_text.splitlines() if l.strip()]
            if "Location" in lines:
                loc_idx = lines.index("Location")
                for back in range(1, 10):
                    if loc_idx - back < 0:
                        break
                    candidate = lines[loc_idx - back]
                    if (
                        candidate
                        and len(candidate) > 2  # skip staff initials like "lu"
                        and not candidate.startswith("$")
                        and not candidate.startswith("with ")
                        and candidate not in _SKIP_LINES
                        and "Cancellation" not in candidate
                        and "policy" not in candidate.lower()
                    ):
                        service_name = candidate
                        break
        except Exception:
            pass

        # Navigate to the /services catalog and try to find a tile whose
        # visible text contains the service name. We use a substring match
        # rather than equality because Square shows e.g. "Buzz cut (all one
        # length)" and the user-facing displayed name can differ slightly.
        service_id: str | None = None
        try:
            page.goto(booking_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            if service_name:
                page.get_by_text(service_name).first.click(timeout=5000)
                page.wait_for_timeout(3000)
                m_sid = re.search(r"/services/([^/?#]+)", page.url)
                if m_sid:
                    service_id = m_sid.group(1)
        except Exception:
            pass
        browser.close()

    if not service_id:
        raise SystemExit(
            f"auto-discover: found merchant_id={merchant_id} location_id={location_id} "
            f"but couldn't infer default_service_id from the past booking's service "
            f"name ({service_name!r}). Set default_service_id manually in merchants.json."
        )

    return booking_url, service_id, service_name or ""


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
    discovered_note: str | None = None
    if not booking_url or not service_id:
        # Auto-discover from the most recent confirmation email rather than
        # forcing the user to fill out merchants.json by hand. Costs one
        # extra browser session; ignored when both fields are already set.
        try:
            d_url, d_sid, d_svc = discover_merchant_config(args.merchant)
            if not booking_url:
                booking_url = d_url
            if not service_id:
                service_id = d_sid
            discovered_note = (
                f"Auto-discovered merchant config from past booking "
                f"(service: {d_svc!r}). Consider copying booking_url and "
                f"default_service_id into merchants.json to skip this step "
                f"on future calls."
            )
        except SystemExit as e:
            print(json.dumps({
                "status": "error",
                "reason": str(e),
                "merchant_alias": args.merchant,
            }, indent=2))
            return 2

    url = _service_url(booking_url, service_id)
    obs = scrape_service_page(url, target, probe=args.probe)
    if discovered_note:
        obs["discovered_note"] = discovered_note

    if args.probe:
        print(json.dumps({"status": "probe", "observations": obs}, indent=2))
        return 0

    if obs.get("slots"):
        out = {
            "status": "ok",
            "target_date": target.date().isoformat(),
            "slots": obs["slots"],
        }
        if discovered_note:
            out["discovered_note"] = discovered_note
        print(json.dumps(out, indent=2))
        return 0

    # No collision AND no slots scraped within window — surface the URL so
    # the user can drive a wider browser session themselves. The most common
    # cause is that the next available date is beyond ±14 days of the
    # target; less commonly the calendar didn't render anything Square
    # considered selectable.
    out = {
        "status": "no_slots_in_window_use_url",
        "target_date": target.date().isoformat(),
        "merchant_alias": args.merchant,
        "merchant_name": merchants[args.merchant].get("name"),
        "booking_url": url,
        "next_available_date": obs.get("selected_date"),
        "message": (
            "No appointment in the ±{}-day collision window, and the "
            "merchant's next available date {} isn't within ±14 days of "
            "the target. Open the booking URL in a browser to pick a time."
        ).format(
            args.window_days,
            f"({obs.get('selected_date')})" if obs.get("selected_date") else "(unknown)",
        ),
    }
    if discovered_note:
        out["discovered_note"] = discovered_note
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
