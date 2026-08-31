"""gingr_lib.py — shared helpers for the Gingr customer-portal scripts.

Centralises the bits every Gingr script needs: locating the saved
storage_state, launching a headless browser already logged in, and reading
the bookings list. The portal is a React-Native-Web SPA — no anchors, no
element IDs, no data attributes on the booking cards — so everything is
text-scraped and re-located by content rather than by stable selector.

Credentials are never handled here; gingr-login.py captures the session and
this module just reuses it. If the session is missing or expired, callers get
a clear `session_expired` signal and should re-run gingr-login.py.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "pallo-logistics"
STATE_FILE = CONFIG_DIR / "gingr-storage-state.json"

PORTAL = "https://tailwaginn.portal.gingrapp.com"
BOOKINGS_URL = f"{PORTAL}/secure/book/bookings-deposits/bookings"

_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MONTH = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


class SessionExpired(Exception):
    """Raised when the saved storage_state no longer authenticates."""


def state_exists() -> bool:
    return STATE_FILE.exists()


def new_logged_in_context(p, *, viewport_h: int = 1400):
    """Launch chromium with the saved session. Caller closes the browser."""
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(
        storage_state=str(STATE_FILE),
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": viewport_h},
        locale="en-US",
    )
    return browser, ctx


class BookingsPageAnomaly(Exception):
    """Raised when the bookings page renders but has no recognisable
    bookings header — the page is not the expected bookings list (layout
    change or silent auth bounce), so an empty result cannot be trusted.
    Callers must surface this instead of reporting count 0."""


def fetch_booking_cards(page, *, timeout_ms: int = 45000,
                        in_page_marker: str = "Bookings & Deposits") -> list[str]:
    """Navigate to the bookings list and return one text blob per booking card.

    Each blob looks like:
      "Boarding | Dog | (Pallo) | Laurel Acres Kennels - Hillsboro |
       Tue, Jun. 8th - Mon, Jun. 28th  | Confirmed"
    Raises SessionExpired if the portal bounces us to the public login —
    the redirect is a SPA push that lands 1-2s AFTER domcontentloaded, so
    we wait on the URL itself rather than sampling it once after a fixed
    delay (a sample can land before the push and miss the redirect).
    Raises BookingsPageAnomaly if the rendered page lacks the expected
    in-page header.
    """
    page.goto(BOOKINGS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_url("**/public/login**", timeout=8000)
    except Exception:
        pass  # still on the secure page: session authenticated fine
    if "public/login" in page.url:
        raise SessionExpired("storage_state no longer authenticates")
    # SPA content settles after the URL lands; give it a moment.
    page.wait_for_timeout(3000)
    if "public/login" in page.url:
        raise SessionExpired("storage_state no longer authenticates (late redirect)")
    if not page.evaluate(
            "(m) => (document.body && document.body.innerText || '').includes(m)",
            in_page_marker):
        raise BookingsPageAnomaly(
            "Bookings page loaded but the expected header was not found; "
            "cannot verify the list is complete. Do NOT treat this as "
            "'no reservations'.")
    cards = page.evaluate(r"""() => {
        const all = [...document.querySelectorAll('*')];
        const match = el => {
            const t = el.innerText || '';
            return /Boarding \|/.test(t)
                && /(Confirmed|Canceled|Cancelled|Completed|Pending)/.test(t)
                && t.length < 400
                && el.querySelectorAll('*').length < 40;
        };
        const cands = all.filter(match);
        // keep the outermost-of-similar: drop any whose parent also matches
        return cands.filter(el => !cands.includes(el.parentElement))
                    .map(el => el.innerText.replace(/\n+/g, ' | '));
    }""")
    return cards


def _infer_year(month: int, day: int, weekday: int, anchor: date) -> int:
    """Pick the year in [anchor-400d, anchor+560d] whose (month, day) lands on
    `weekday`. The weekday+month+day triple is unique within any ~5-year span,
    so this resolves the year the portal omits from its labels."""
    best = None
    for yr in range(anchor.year - 2, anchor.year + 3):
        try:
            d = date(yr, month, day)
        except ValueError:
            continue
        if d.weekday() != weekday:
            continue
        if not (anchor - timedelta(days=400) <= d <= anchor + timedelta(days=560)):
            continue
        # prefer the candidate closest to the anchor
        score = abs((d - anchor).days)
        if best is None or score < best[0]:
            best = (score, yr)
    if best:
        return best[1]
    # Fallback: nearest year regardless of window (shouldn't normally hit).
    return anchor.year


_DATE_RE = re.compile(r"([A-Z][a-z]{2}),\s*([A-Z][a-z]{2})\.?\s*(\d{1,2})")


def _parse_one_date(token: str, anchor: date) -> date | None:
    m = _DATE_RE.search(token)
    if not m:
        return None
    wd, mon, day = m.group(1), m.group(2), int(m.group(3))
    if wd not in _WEEKDAY or mon not in _MONTH:
        return None
    month = _MONTH[mon]
    year = _infer_year(month, day, _WEEKDAY[wd], anchor)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_card(text: str, anchor: date) -> dict | None:
    """Turn a booking-card blob into a structured stay dict, or None if it
    doesn't parse. `anchor` is today's date, used for year inference.

    None means UNKNOWN, not "no such stay". A caller making a safety decision
    (e.g. "is there a conflicting reservation?") must treat None as an
    unreadable reservation and refuse — see parse_cards()."""
    parts = [p.strip() for p in text.split("|")]
    parts = [p for p in parts if p]
    status = None
    for p in parts:
        if p in ("Confirmed", "Canceled", "Cancelled", "Completed", "Pending"):
            status = "Canceled" if p == "Cancelled" else p
    pet = None
    for p in parts:
        mm = re.fullmatch(r"\((.+)\)", p)
        if mm:
            pet = mm.group(1)
    location = next((p for p in parts if "Laurel Acres" in p or "Tail Wag" in p), None)
    svc_type = parts[0] if parts else None  # e.g. "Boarding"

    date_part = next((p for p in parts if " - " in p and _DATE_RE.search(p)), None)
    start = end = None
    if date_part:
        halves = date_part.split(" - ")
        if len(halves) == 2:
            start = _parse_one_date(halves[0], anchor)
            end = _parse_one_date(halves[1], anchor)
            # Dec→Jan style spans: if end inferred before start, bump end a year.
            if start and end and end < start:
                try:
                    end = end.replace(year=end.year + 1)
                except ValueError:
                    pass

    if not (start and end):
        # Dates unreadable: we know a reservation card is there, we just can't
        # say WHEN it is. Never report this as "no reservation".
        return None

    nights = (end - start).days
    return {
        "stay_id": f"{start.isoformat()}/{end.isoformat()}",
        "pet": pet,
        "service_type": svc_type,
        "location": location,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "nights": nights,
        "status": status,
    }


def parse_cards(cards: list[str], anchor: date) -> tuple[list[dict], list[str]]:
    """Parse every booking-card blob. Returns (stays, unreadable) where
    `unreadable` holds the raw text of each card that did NOT yield a stay.

    Split out so a safety check can tell "there is no conflicting reservation"
    apart from "I could not read some reservations". Callers deciding whether it
    is safe to book MUST refuse while `unreadable` is non-empty."""
    stays: list[dict] = []
    unreadable: list[str] = []
    for text in cards:
        parsed = parse_card(text, anchor)
        if parsed is None:
            unreadable.append(text)
        else:
            stays.append(parsed)
    return stays, unreadable
