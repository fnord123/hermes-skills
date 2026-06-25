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
# The availability detail panel renders the selected date as a full string,
# e.g. "Wednesday, Aug 5, 2026" — the one drift-proof source of truth for
# which date is actually selected (the week-strip counter can desync).
_FULL_DATE_RE = re.compile(
    r"([A-Z][a-z]+),\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})")


def _slots_via_availability_api(
    page, post_data: str, api_url: str, target_date, window_days: int, today,
    origin: str, obs: dict | None = None,
) -> "list[tuple[date, str]] | None":
    """Replay Square's buyer/availability API for the target window and return
    [(date, 'H:MM AM/PM'), ...]. Returns None if the template can't be parsed
    or the call fails, so the caller can fall back to DOM scraping.

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
                obs["source"] = "availability_api"

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
                if not probe:
                    browser.close()
                    return obs
                obs["api_slot_count"] = len(api_slots)
        # If the API path didn't yield (template missing / call failed), fall
        # through to the legacy DOM week-walk below as a best-effort fallback.

        if probe:
            obs["available_date_testids"] = [
                d.get_attribute("data-testid")
                for d in page.locator('[data-testid^="date-"]').all()[:25]
                if d.get_attribute("data-testid")
            ]

        # Click "Go to next available" to advance to the soonest date with slots.
        # If it's not present (the default-selected date already has slots),
        # the click silently fails and we proceed with the current selection.
        try:
            gtn = page.locator('text="Go to next available"').first
            if gtn.count():
                gtn.click(timeout=4000)
                page.wait_for_timeout(4000)
                obs["click_log"].append({"label": "go_to_next_available",
                                         "url_after": page.url})
        except Exception as e:
            obs["click_log"].append({"label": "go_to_next_available",
                                     "error": str(e).splitlines()[0][:160]})

        # Read currently-selected date (day-of-month) and visible month.
        selected_date = None
        for sel_loc in page.locator('[data-testid$="-selected"]').all():
            tid = sel_loc.get_attribute("data-testid") or ""
            m_day = re.match(r"date-(\d+)-selected", tid)
            if not m_day:
                continue
            vis = _visible_month(page)
            if not vis:
                continue
            try:
                selected_date = date(vis[0], vis[1], int(m_day.group(1)))
            except ValueError:
                continue
            break
        obs["selected_date"] = selected_date.isoformat() if selected_date else None

        # Anchor: the Sunday at or before today. Square's week-view rows
        # always start on Sunday, so we can resolve a `date-N` testid to a
        # calendar date by counting week advances from this anchor — even
        # across month transitions where the visible header is ambiguous.
        anchor_sunday = today - timedelta(days=(today.weekday() + 1) % 7)

        def _resolve_date_testid(day_n: int, advances: int) -> "date | None":
            """Given `date-<day_n>` and how many weeks we've advanced from
            initial, return the actual calendar date. Checks the past /
            present / future weeks of the current view in turn (Square's
            picker shows three weeks at a time)."""
            present_start = anchor_sunday + timedelta(weeks=advances)
            for week_offset in (0, 1, -1):
                week_start = present_start + timedelta(weeks=week_offset)
                for day_offset in range(7):
                    d = week_start + timedelta(days=day_offset)
                    if d.day == day_n:
                        return d
            return None

        def _strip_ids() -> str:
            """Ordered fingerprint of the visible date cells (day-of-month
            testids, selection suffix stripped). Changes iff the strip
            physically moves — the reliable signal that an advance landed."""
            ids: list[str] = []
            for el in page.locator('[data-testid^="date-"]').all():
                tid = el.get_attribute("data-testid") or ""
                if re.match(r"date-\d+(?:-selected)?$", tid):
                    ids.append(re.sub(r"-selected$", "", tid))
            return "|".join(ids)

        def _wait_strip_settle(timeout_ms: int = 6000) -> None:
            """Square enables date cells lazily as availability fetches land,
            so a fixed sleep races the data. Poll the full (testid+disabled)
            fingerprint until it stops changing for two consecutive reads."""
            prev = None
            waited = 0
            while waited < timeout_ms:
                cur = []
                for el in page.locator('[data-testid^="date-"]').all():
                    tid = el.get_attribute("data-testid") or ""
                    if not re.match(r"date-\d+(?:-selected)?$", tid):
                        continue
                    dis = "1" if el.get_attribute("disabled") is not None else "0"
                    cur.append(f"{tid}:{dis}")
                cur_s = "|".join(cur)
                if cur_s and cur_s == prev:
                    return
                prev = cur_s
                page.wait_for_timeout(700)
                waited += 700

        def _advance_week_via_js() -> bool:
            """Advance the strip one week and CONFIRM it moved. Square's
            `next-week-button` is `display:none` in headless but its click
            handler still fires via JS. The click is unreliable (it can no-op
            while still "succeeding"), so we fingerprint the strip, click, and
            poll for the fingerprint to change — retrying a few times — then
            wait for the new week's availability to settle. Returns True only
            on a confirmed advance."""
            before = _strip_ids()
            for attempt in range(5):
                try:
                    # Exactly ONE click per attempt. A multi-event dispatch
                    # fires the handler more than once and the strip jumps two
                    # weeks, overshooting the target week. If the button is
                    # `disabled` we've hit the merchant's booking horizon — no
                    # point clicking. Verify-and-retry handles the no-op case.
                    result = page.evaluate("""() => {
                        const btn = document.querySelector('[data-testid="next-week-button"]');
                        if (!btn) return 'not_found';
                        if (btn.disabled) return 'disabled';
                        btn.click();
                        return 'clicked';
                    }""")
                except Exception:
                    return False
                if result == "disabled":
                    return False
                if result != "clicked":
                    return False
                waited = 0
                while waited < 7000:
                    page.wait_for_timeout(500)
                    waited += 500
                    if _strip_ids() != before:
                        _wait_strip_settle()
                        return True
                # Strip didn't move this attempt; pause and try the click again.
                page.wait_for_timeout(800)
            # All attempts failed — capture why, once, for diagnosis.
            try:
                obs.setdefault("advance_failures", []).append(page.evaluate("""() => {
                    const out = {};
                    const nw = document.querySelector('[data-testid="next-week-button"]');
                    out.next_week = nw ? {disabled: nw.disabled, aria_disabled: nw.getAttribute('aria-disabled'), html: nw.outerHTML.slice(0,200)} : null;
                    out.nav_testids = Array.from(document.querySelectorAll('[data-testid*="month"], [data-testid*="next"], [data-testid*="nav"], [data-testid*="arrow"]')).map(e => e.getAttribute('data-testid'));
                    const page_el = document.querySelector('[data-testid="availability-page"]');
                    out.header = page_el ? (page_el.textContent || '').slice(0,60) : null;
                    return out;
                }"""))
            except Exception:
                pass
            return False

        # Helper: read time-slot labels currently rendered on the page.
        def _read_slot_labels() -> list[str]:
            out: list[str] = []
            seen_local: set[str] = set()
            for n in page.locator('[data-testid="time-slot"]').all():
                try:
                    txt = (n.text_content() or "").strip()
                except Exception:
                    continue
                m = _TIME_SLOT_RE.search(txt)
                if not m:
                    continue
                lab = f"{m.group(1)} {m.group(2).upper()}"
                if lab in seen_local:
                    continue
                seen_local.add(lab)
                out.append(lab)
            return out

        # Helper: read the currently-selected date in the picker.
        def _read_selected_date() -> "date | None":
            for sel_loc_inner in page.locator('[data-testid$="-selected"]').all():
                tid_inner = sel_loc_inner.get_attribute("data-testid") or ""
                m_inner = re.match(r"date-(\d+)-selected", tid_inner)
                if not m_inner:
                    continue
                vis_inner = _visible_month(page)
                if not vis_inner:
                    continue
                try:
                    return date(vis_inner[0], vis_inner[1], int(m_inner.group(1)))
                except ValueError:
                    return None
            return None

        # First date's slots: read what 'Go to next available' landed on.
        target_emit = 5
        earliest_acceptable = target_date - timedelta(days=window_days)
        latest_acceptable = target_date + timedelta(days=window_days)

        # Accumulate every in-window (date, label) we observe. The final result
        # is sorted by proximity to the target, so an "around Aug 5" query
        # returns Aug 5 and its nearest neighbours first — not the earliest
        # edge of the ±window band (the old emit-and-stop behaviour returned
        # slots up to two weeks before the date the user actually asked about).
        window_slots: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()
        scanned_through: "date | None" = None

        def _collect(d: "date | None", labels: list[str]) -> None:
            nonlocal scanned_through
            if d is not None and (scanned_through is None or d > scanned_through):
                scanned_through = d
            if not d or not labels:
                return
            if not (earliest_acceptable <= d <= latest_acceptable):
                return
            for lab in labels:
                key = (d.isoformat(), lab)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                window_slots.append({"date": d, "label": lab})

        first_labels = _read_slot_labels()
        obs["slot_count_on_selected"] = len(first_labels)
        _collect(selected_date, first_labels)

        # Walk the week-strip forward until slots near the target are found.
        #
        # We DO NOT trust a week counter to know where the strip is: the
        # CSS-hidden `next-week-button`, clicked via JS, advances the strip by
        # an inconsistent number of weeks (observed drift of a full week over
        # a long walk). Resolving `date-N` cells from such a counter mislabels
        # every visible date once it desyncs, so real, selectable slots get
        # filtered out and the search falls through to the use-url branch even
        # though the target date is open.
        #
        # Instead we drive everything off the page's own ground truth:
        #   • `_visible_month()` — the header month actually rendered, used to
        #     decide when the strip has reached the acceptance window's months
        #     (and when it has walked past them).
        #   • `_read_selected_full_date()` — the detail panel's full date
        #     string, the authoritative identity of a clicked cell.
        # Only non-disabled cells are clicked (disabled = no availability, so a
        # click is silently ignored); a non-disabled cell always selects, so
        # the post-click full-date read identifies exactly what we landed on.
        obs["candidate_dates_tried"] = []
        clicked_dates: set[str] = set()
        early_ord = earliest_acceptable.year * 12 + earliest_acceptable.month
        late_ord = latest_acceptable.year * 12 + latest_acceptable.month

        def _js_click_testid(tid: str) -> str:
            """Click a date cell via JS dispatch. Like the next-week button,
            these cells no-op under Playwright's normal .click() in headless,
            so we fire their handler directly."""
            try:
                return page.evaluate(
                    """(t) => {
                        const el = document.querySelector('[data-testid="' + t + '"]');
                        if (!el) return 'not_found';
                        el.click();
                        return 'clicked';
                    }""",
                    tid,
                )
            except Exception as e:
                return f"error:{str(e).splitlines()[0][:80]}"

        def _visible_strip_dates() -> list[tuple["date", str]]:
            """Reconstruct the real date of every visible strip cell. The strip
            is ~21 contiguous days; the header gives the leftmost month and we
            roll the month forward each time the day-of-month resets.

            `disabled` is deliberately IGNORED. Once any date is selected Square
            marks the rest of the strip disabled, yet those cells stay clickable
            and real — gating on `disabled` made the walk skip every week after
            the first selection, which is exactly why far-future targets failed.
            """
            vis = _visible_month(page)
            if vis is None:
                return []
            cells: list[tuple[int, str]] = []
            for el in page.locator('[data-testid^="date-"]').all():
                tid = el.get_attribute("data-testid") or ""
                m = re.match(r"date-(\d+)(?:-selected)?$", tid)
                if m:
                    cells.append((int(m.group(1)), re.sub(r"-selected$", "", tid)))
            out: list[tuple["date", str]] = []
            y, mo = vis
            prev = None
            for day_n, tid in cells:
                if prev is not None and day_n < prev:
                    mo += 1
                    if mo > 12:
                        mo, y = 1, y + 1
                prev = day_n
                try:
                    out.append((date(y, mo, day_n), tid))
                except ValueError:
                    continue
            return out

        def _scan_visible() -> None:
            # Click every visible cell whose reconstructed date is in-window and
            # not yet seen; trust the detail-panel date for what we landed on.
            for d, tid in _visible_strip_dates():
                # Soft cap so a dense week can't make us click forever; plenty
                # to pick the nearest target_emit from afterwards.
                if len(window_slots) >= target_emit * 6:
                    return
                if not (earliest_acceptable <= d <= latest_acceptable):
                    continue
                if d.isoformat() in clicked_dates:
                    continue
                res = _js_click_testid(tid)
                if res != "clicked":
                    obs["candidate_dates_tried"].append({"testid": tid, "click": res})
                    continue
                clicked_dates.add(d.isoformat())
                page.wait_for_timeout(1500)
                real = _read_selected_full_date(page) or d
                labels = _read_slot_labels()
                obs["candidate_dates_tried"].append({
                    "testid": tid, "expected": d.isoformat(),
                    "resolved": real.isoformat(), "n_slots": len(labels),
                })
                _collect(real, labels)

        def _strip_dates_cached() -> list["date"]:
            return [d for d, _ in _visible_strip_dates()]

        def _strip_overlaps_window() -> bool:
            return any(
                earliest_acceptable <= d <= latest_acceptable
                for d in _strip_dates_cached()
            )

        def _strip_past_window() -> bool:
            ds = _strip_dates_cached()
            return bool(ds) and min(ds) > latest_acceptable

        # Stop once we've actually read slots a few days past the target: that
        # guarantees the target and its near neighbours on both sides are in
        # hand, so the proximity sort below can pick the closest ones.
        enough_after = target_date + timedelta(days=3)

        def _have_enough() -> bool:
            return (
                scanned_through is not None
                and scanned_through >= enough_after
                and len(window_slots) >= target_emit
            )

        # Backstop cap; the loop's own window gate stops it as soon as the strip
        # walks past the window (or the next-week button hits the merchant's
        # booking horizon and disables).
        max_week_advances = 20
        advances = 0
        while not _have_enough() and advances <= max_week_advances:
            if _strip_past_window():
                break  # walked entirely past the window
            if _strip_overlaps_window():
                _scan_visible()
                if _have_enough():
                    break
            if not _advance_week_via_js():
                # The page intermittently swallows an advance; give it one
                # full second-chance pass before abandoning the walk.
                if not _advance_week_via_js():
                    break
            advances += 1
        obs["week_advances_used"] = advances

        # Proximity sort: nearest-to-target date first, earliest time as the
        # within-day tiebreak. Then materialise the closest target_emit slots.
        def _minutes(label: str) -> int:
            m = _TIME_SLOT_RE.search(label)
            if not m:
                return 0
            hh, mm = m.group(1).split(":")
            hh = int(hh) % 12
            if m.group(2).upper() == "PM":
                hh += 12
            return hh * 60 + int(mm)

        window_slots.sort(key=lambda s: (
            abs((s["date"] - target_date).days), s["date"].toordinal(), _minutes(s["label"]),
        ))
        for s in window_slots[:target_emit]:
            d, lab = s["date"], s["label"]
            obs["slots"].append({
                "slot_handle": json.dumps({
                    "service_url": url, "date": d.isoformat(), "time": lab,
                }, sort_keys=True, separators=(",", ":")),
                "start_time": f"{d.isoformat()} {lab}",
                "label": lab,
                "date": d.isoformat(),
            })

        if probe:
            try:
                obs["body_text_head"] = page.locator("body").inner_text()[:2500]
            except Exception:
                obs["body_text_head"] = ""
            # End-state strip diagnostics: did the calendar actually advance,
            # and are the now-visible far-future cells selectable or stuck
            # disabled (= availability never fetched)?
            try:
                vm = _visible_month(page)
                obs["final_visible_month"] = f"{vm[0]}-{vm[1]:02d}" if vm else None
            except Exception:
                obs["final_visible_month"] = None
            try:
                final_cells = []
                for el in page.locator('[data-testid^="date-"]').all():
                    tid = el.get_attribute("data-testid") or ""
                    if not re.match(r"date-\d+(?:-selected)?$", tid):
                        continue
                    final_cells.append({
                        "testid": tid,
                        "disabled": el.get_attribute("disabled") is not None,
                    })
                obs["final_date_cells"] = final_cells
            except Exception as e:
                obs["final_date_cells_error"] = str(e).splitlines()[0][:160]

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


def _read_selected_full_date(page) -> "date | None":
    """Parse the detail panel's full date (e.g. 'Wednesday, Aug 5, 2026') into
    a ``date``. This is the ground truth for the currently-selected day and is
    immune to week-strip / counter drift. Falls back from the availability-page
    element to the whole body."""
    text = ""
    for sel in ('[data-testid="availability-page"]', "body"):
        try:
            text = page.locator(sel).first.text_content() or ""
        except Exception:
            text = ""
        if text:
            m = _FULL_DATE_RE.search(text)
            if m:
                mon = _MONTH_NUM.get(m.group(2))
                if mon:
                    try:
                        return date(int(m.group(4)), mon, int(m.group(3)))
                    except ValueError:
                        return None
    return None


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
