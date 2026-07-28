#!/usr/bin/env python3
"""square-book.py — book an appointment slot returned by square-find-slot.py.

The agent invokes this after the user explicitly confirms ("yes, book it").
Takes the `slot_handle` JSON blob from find-slot, drives Square's public
booking flow back to the same slot, fills in the customer info from
`~/.config/square-appointments/customer.json`, and submits.

Two paths depending on the merchant:
  * No-card checkouts (deRosso pattern): fully automated end-to-end.
  * Card-required checkouts (Sugar Mama / spa pattern): detects the
    Stripe iframe and returns status="card_required" without submitting.
    The user finishes in their browser using the booking_url we return.

Usage:
  python3 square-book.py --merchant <alias> --slot-handle '<json>' \
                         --confirm-date <YYYY-MM-DD> \
                         --confirm-time '<HH:MM AM/PM>' \
                         --confirm [--note '<text>']
  python3 square-book.py ... --dry-run          # fills the form, books nothing

Required invariants:
  * --confirm is mandatory for a real booking. Pass it only after the user
    has explicitly approved booking this exact date and time.
  * --confirm-date and --confirm-time MUST match what the script reads off
    the checkout page exactly. If they don't, the script refuses to submit.
    This is what stops a confused model from booking the wrong slot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MERCHANTS = Path.home() / ".config" / "square-appointments" / "merchants.json"
DEFAULT_CUSTOMER = Path.home() / ".config" / "square-appointments" / "customer.json"

# Self-bootstrap to local venv so the agent can invoke as `python3 <path>`.
_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

from playwright.sync_api import sync_playwright  # noqa: E402
from playwright_stealth import Stealth  # noqa: E402


# ── env loading (small dup) ──────────────────────────────────────────────────

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


def env_value(env: dict[str, str], key: str) -> str | None:
    v = env.get(key) or os.environ.get(key)
    return v.strip() if v else None


# ── helpers shared with find-slot in spirit ──────────────────────────────────

_TIME_LABEL_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", re.IGNORECASE)


def _normalize_time(s: str) -> str | None:
    """Normalize '1:15 PM', '1:15PM', '01:15 pm' → '1:15 PM'."""
    m = _TIME_LABEL_RE.match(s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
    return f"{h}:{mi} {ap}"


# ── core: drive the booking flow ─────────────────────────────────────────────

def _service_url_from_handle(handle_json: str) -> tuple[str, str, str]:
    """Parse the JSON `slot_handle` find-slot emits. Returns
    (service_url, iso_date, time_label)."""
    try:
        d = json.loads(handle_json)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--slot-handle isn't valid JSON: {e}")
    surl = d.get("service_url")
    iso = d.get("date")
    tlabel = d.get("time")
    if not (surl and iso and tlabel):
        raise SystemExit(
            f"--slot-handle missing required keys; got {sorted(d.keys())}, "
            f"need service_url + date + time"
        )
    return surl, iso, tlabel


def book(
    *,
    service_url: str,
    target_date: date,
    target_time: str,
    confirm_date: str,
    confirm_time: str,
    customer: dict,
    note: str | None,
    dry_run: bool,
) -> dict:
    """Drive playwright from service detail through `/checkout` and (unless
    dry_run or card_required) submit the booking. Returns a structured
    payload describing the outcome."""
    obs: dict[str, Any] = {
        "service_url": service_url,
        "target_date": target_date.isoformat(),
        "target_time": target_time,
        "click_log": [],
    }
    today = datetime.now().date()
    anchor_sunday = today - timedelta(days=(today.weekday() + 1) % 7)

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1200},
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        page = ctx.new_page()

        def click(label, *sels, timeout=4000, wait_ms=2500):
            for sel in sels:
                try:
                    loc = page.locator(sel).first
                    if loc.count() == 0:
                        continue
                    loc.click(timeout=timeout)
                    page.wait_for_timeout(wait_ms)
                    obs["click_log"].append({"label": label, "sel": sel, "url": page.url})
                    return True
                except Exception as e:
                    obs["click_log"].append({"label": label, "sel": sel,
                                             "err": str(e).splitlines()[0][:160]})
            return False

        try:
            page.goto(service_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            return {"status": "error", "reason": f"goto service_url failed: {e}",
                    "click_log": obs["click_log"]}
        page.wait_for_timeout(5000)

        # Service-detail panel. Sugar Mama style has Regular + Any staff
        # radios; deRosso style has just Add. Both branches are best-effort.
        click("price_regular", 'text="Regular"', timeout=3000, wait_ms=1200)
        click("staff_any", 'text="Any staff"', timeout=3000, wait_ms=1200)
        if not click("add_service", 'market-button:has-text("Add")', 'text="Add"'):
            browser.close()
            return {"status": "error", "reason": "Couldn't click 'Add' on service detail",
                    "click_log": obs["click_log"]}
        # Upsell step → Next. Some merchants skip the upsell page entirely.
        click("skip_upsell",
              'market-button:has-text("Next")',
              'market-button:has-text("Continue")',
              'market-button:has-text("Skip")')

        # Wait for availability page.
        try:
            page.locator('[data-testid="availability-page"]').first.wait_for(timeout=8000)
        except Exception:
            browser.close()
            return {"status": "error", "reason": "didn't reach /availability",
                    "click_log": obs["click_log"]}

        # Advance the visible window forward until the target date is in the
        # present-week or future-week, then click it.
        advances = 0
        while advances <= 4:
            # Is target_date currently visible (and non-disabled)?
            cell = _find_visible_date_cell(page, target_date, anchor_sunday, advances)
            if cell is not None:
                try:
                    cell.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    obs["click_log"].append({"label": "target_date_clicked",
                                             "date": target_date.isoformat(),
                                             "advances": advances})
                    break
                except Exception as e:
                    return {"status": "error",
                            "reason": f"target-date click failed: {str(e)[:160]}",
                            "click_log": obs["click_log"]}
            # Not visible yet — try a week-advance.
            advanced = page.evaluate("""() => {
                const btn = document.querySelector('[data-testid="next-week-button"]');
                if (!btn) return false;
                btn.click();
                return true;
            }""")
            if not advanced:
                break
            page.wait_for_timeout(2800)
            advances += 1
        else:
            browser.close()
            return {"status": "error",
                    "reason": f"couldn't reach target date {target_date.isoformat()} "
                              f"within {advances} week advances",
                    "click_log": obs["click_log"]}

        # Confirm the right date is now selected.
        sel = _read_selected_date(page, anchor_sunday, advances)
        obs["selected_date"] = sel.isoformat() if sel else None
        if sel != target_date:
            browser.close()
            return {"status": "error",
                    "reason": f"date click didn't land on target; expected "
                              f"{target_date.isoformat()}, got {sel.isoformat() if sel else None}",
                    "click_log": obs["click_log"]}

        # Click the time slot. An un-normalizable label (no AM/PM) cannot be
        # matched against the page unambiguously, so refuse rather than guess.
        target_norm = _normalize_time(target_time)
        if not target_norm:
            browser.close()
            return {"status": "error",
                    "reason": f"slot time {target_time!r} isn't a display time like "
                              f"'1:15 PM'; nothing was booked.",
                    "click_log": obs["click_log"]}
        slot_clicked = False
        for n in page.locator('[data-testid="time-slot"]').all():
            try:
                txt = (n.text_content() or "").strip()
            except Exception:
                continue
            norm = _normalize_time(txt)
            if norm and norm == target_norm:
                try:
                    n.click(timeout=4000)
                    page.wait_for_timeout(4500)
                    obs["click_log"].append({"label": "time_slot_clicked",
                                             "label_text": txt})
                    slot_clicked = True
                    break
                except Exception as e:
                    obs["click_log"].append({"label": "time_slot_click_error",
                                             "err": str(e)[:160]})
        if not slot_clicked:
            visible_slots = [(n.text_content() or "").strip()
                             for n in page.locator('[data-testid="time-slot"]').all()]
            browser.close()
            return {"status": "error",
                    "reason": f"didn't find time slot {target_time!r} on selected date",
                    "visible_slots": visible_slots,
                    "click_log": obs["click_log"]}

        # We're now on /checkout. Verify and (maybe) submit.
        return _handle_checkout(
            page, browser,
            confirm_date=confirm_date,
            confirm_time=confirm_time,
            customer=customer,
            note=note,
            dry_run=dry_run,
            obs=obs,
        )


def _find_visible_date_cell(page, target_date, anchor_sunday, advances):
    """Return the playwright element for `target_date` if it's in the visible
    window AND not disabled; None otherwise."""
    for el in page.locator('[data-testid^="date-"]').all():
        tid = el.get_attribute("data-testid") or ""
        m = re.match(r"date-(\d+)(?:-selected)?$", tid)
        if not m:
            continue
        if el.get_attribute("disabled") is not None:
            continue
        day_n = int(m.group(1))
        resolved = _resolve_date_n(day_n, anchor_sunday, advances)
        if resolved == target_date:
            return el
    return None


def _resolve_date_n(day_n: int, anchor_sunday: date, advances: int) -> date | None:
    present_start = anchor_sunday + timedelta(weeks=advances)
    for week_offset in (0, 1, -1):
        week_start = present_start + timedelta(weeks=week_offset)
        for day_offset in range(7):
            d = week_start + timedelta(days=day_offset)
            if d.day == day_n:
                return d
    return None


def _read_selected_date(page, anchor_sunday, advances) -> date | None:
    for sl in page.locator('[data-testid$="-selected"]').all():
        tid = sl.get_attribute("data-testid") or ""
        m = re.match(r"date-(\d+)-selected", tid)
        if not m:
            continue
        return _resolve_date_n(int(m.group(1)), anchor_sunday, advances)
    return None


# ── checkout-page handling ───────────────────────────────────────────────────

def _checkout_summary_text(page) -> str:
    """Read the whole checkout page body (amounts, policy, confirmation copy)."""
    try:
        return page.locator("body").inner_text()
    except Exception:
        return ""


# Containers that hold the 'Appointment summary' block. The date/time
# verification is scoped to this region: matching against the whole page body
# lets unrelated copy (other dates in a policy blurb, a promo banner) satisfy
# the check.
_SUMMARY_SELECTORS = (
    '[data-testid="appointment-summary"]',
    '[data-testid="checkout-summary"]',
    '[data-testid="cart-summary"]',
    '[data-testid*="appointment-summary"]',
)


def _has_clock_time(txt: str) -> bool:
    """A candidate summary region is only usable if it actually carries the
    appointment's time; otherwise we'd refuse every booking."""
    return bool(re.search(r"\d{1,2}:\d{2}", txt or ""))


def _appointment_summary_text(page) -> tuple[str, str]:
    """Return (text, source) for the checkout's appointment-summary region.

    Tries Square's summary containers, then the smallest element whose text
    contains the 'Appointment summary' heading, then falls back to the whole
    body (source='body_fallback') so an unknown page template still verifies
    rather than hard-failing a booking the user asked for."""
    for sel in _SUMMARY_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            txt = loc.inner_text()
        except Exception:
            continue
        if txt and _has_clock_time(txt):
            return txt, sel
    try:
        txt = page.evaluate(r"""() => {
            const els = [...document.querySelectorAll('div,section,aside,main')]
                .filter(e => /Appointment summary/i.test(e.innerText || ''));
            if (!els.length) return '';
            els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
            return els[0].innerText || '';
        }""")
    except Exception:
        txt = ""
    if txt and _has_clock_time(txt):
        return txt, "appointment-summary-heading"
    return _checkout_summary_text(page), "body_fallback"


def _detect_card_required(page) -> tuple[bool, str | None]:
    """Sugar Mama-style merchants require a credit card. Detection signals:
      • literal text 'credit or debit card is required' OR 'A credit or debit
        card is required' on the page
      • a Stripe iframe in the document (src contains 'stripe' or
        'js.squareup.com')
    Either is sufficient.

    Returns (card_required, error). A non-None error means the detection could
    not run — the caller must NOT submit, because this merchant may be one the
    script cannot complete a booking for."""
    try:
        body = page.locator("body").inner_text()
    except Exception as e:
        return False, f"couldn't read the checkout page: {str(e).splitlines()[0][:160]}"
    if re.search(r"credit or debit card is required", body, re.I):
        return True, None
    if "Secure your appointment" in body:
        return True, None
    # Stripe / Square card iframes
    try:
        for f in page.frames:
            if any(k in (f.url or "") for k in ("stripe", "js.squareup.com", "card-form")):
                return True, None
    except Exception as e:
        return False, f"couldn't inspect the checkout page's card frames: {str(e).splitlines()[0][:160]}"
    return False, None


_AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")


def _extract_amounts(body: str) -> dict[str, str]:
    """Pull 'Due today' and 'Due at appointment' from the checkout body."""
    out: dict[str, str] = {}
    m = re.search(r"Due today\s*\$\s?(\d+(?:\.\d{2})?)", body)
    if m:
        out["due_today"] = f"${m.group(1)}"
    m = re.search(r"Due at appointment\s*\$\s?(\d+(?:\.\d{2})?)", body)
    if m:
        out["due_at_appointment"] = f"${m.group(1)}"
    m = re.search(r"Total\s*\$\s?(\d+(?:\.\d{2})?)", body)
    if m:
        out["total"] = f"${m.group(1)}"
    return out


def _extract_cancel_policy(body: str) -> str | None:
    m = re.search(
        r"(Please cancel or reschedule before [^.]+\.[^.]*\.)",
        body,
    )
    return m.group(1).strip() if m else None


def _match_appointment_date(text: str, d: date) -> tuple[bool, bool, list[str]]:
    """Look for `d` rendered the way Square renders it — "Wednesday, Jun 24,
    2026" / "Wed, June 24" — inside `text`.

    Returns (matched, year_verified, years_seen_that_are_wrong). A rendering
    that carries the year is checked against it; a rendering with no year at
    all matches but reports year_verified=False so the caller can say so."""
    pat = re.compile(
        rf"(?:{d.strftime('%A')}|{d.strftime('%a')})\.?,?\s+"
        rf"(?:{d.strftime('%B')}|{d.strftime('%b')})\.?\s+"
        rf"0?{d.day}(?:st|nd|rd|th)?(?!\d)"
        rf"(?:\s*,?\s*(\d{{4}}))?",
        re.I,
    )
    bare = False
    wrong: list[str] = []
    for m in pat.finditer(text):
        year = m.group(1)
        if year is None:
            bare = True
        elif int(year) == d.year:
            return True, True, []
        else:
            wrong.append(year)
    if bare:
        return True, False, wrong
    return False, False, wrong


def _verify_summary_matches(text: str, confirm_date: str,
                            confirm_time: str) -> tuple[str | None, bool]:
    """Return (None, year_verified) if the appointment summary shows the
    expected date+time; else (diagnostic string, False)."""
    # confirm_date is YYYY-MM-DD. Square displays "Wednesday, Jun 24, 2026".
    try:
        d = date.fromisoformat(confirm_date)
    except ValueError:
        return f"--confirm-date isn't an ISO date: {confirm_date!r}", False
    matched, year_verified, wrong_years = _match_appointment_date(text, d)
    if not matched:
        if wrong_years:
            return (f"appointment summary shows {d.strftime('%A, %b %-d')} in "
                    f"{'/'.join(sorted(set(wrong_years)))}, not {d.year}"), False
        return (f"appointment summary doesn't show "
                f"{d.strftime('%A, %b %-d, %Y')}"), False

    norm_time = _normalize_time(confirm_time)
    if not norm_time:
        return (f"--confirm-time {confirm_time!r} isn't a display time like "
                f"'1:15 PM'; a time without AM/PM can't be verified"), False
    h, _, rest = norm_time.partition(":")
    minute, _, ap = rest.partition(" ")
    # Square displays appointment times as a range, e.g. "1:15 – 2:00 PM"
    # with an en-dash (or hyphen) and the AM/PM following the end time.
    # Accept either the standalone "1:15 PM" form OR the range form where
    # PM is shared with the end time ("1:15 – 2:00 PM").
    time_pat = re.compile(
        rf"\b{int(h)}:{minute}\s*(?:[–\-—]\s*\d{{1,2}}:\d{{2}}\s*)?{ap}\b",
        re.I,
    )
    if not time_pat.search(text):
        return f"appointment summary doesn't show {norm_time}", False
    return None, year_verified


def _fill_field(page, label_text: str, value: str) -> bool:
    """Square's form fields are <market-input>/labelled inputs. Find by
    associated label text and type into it."""
    selectors = [
        f'input[aria-label="{label_text}"]',
        f'input[name="{label_text.lower().replace(" ", "_")}"]',
        f'[data-testid="checkout-form-{label_text.lower().replace(" ","-")}-input"] input',
        f'label:has-text("{label_text}") >> input',
        f'market-input:has-text("{label_text}") input',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.fill(value, timeout=2500)
            return True
        except Exception:
            continue
    # Fallback: input next to a label whose text matches.
    try:
        page.get_by_label(label_text).first.fill(value, timeout=2500)
        return True
    except Exception:
        pass
    return False


# Copy Square shows once a booking has actually landed. Used together with the
# reservation-URL match as the positive proof of a completed submit.
_CONFIRMED_RE = re.compile(
    r"(Appointment confirmed|Booking confirmed|You'?re all set|"
    r"Your appointment (?:is|has been) (?:confirmed|booked)|"
    r"Thanks for booking|We'?ll see you)",
    re.I,
)


def _book_button_visible(page) -> bool | None:
    """Is a visible 'Book appointment' button still on the page? True/False,
    or None when the page couldn't be inspected at all (unknown, not 'no')."""
    try:
        return bool(page.evaluate("""() => {
            const btns = document.querySelectorAll(
                'market-button[data-testid^="book-appointment-button"]'
            );
            for (const b of btns) {
                const r = b.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return true;
            }
            return false;
        }"""))
    except Exception:
        return None


def _handle_checkout(page, browser, *, confirm_date, confirm_time, customer, note, dry_run, obs):
    page.wait_for_timeout(2000)
    body = _checkout_summary_text(page)
    obs["checkout_body_head"] = body[:1500]
    obs["amounts"] = _extract_amounts(body)
    obs["cancellation_policy"] = _extract_cancel_policy(body)
    obs["checkout_url"] = page.url

    # First: did we even reach a checkout?
    if "checkout" not in page.url.lower() and "Checkout" not in body:
        browser.close()
        return {"status": "error", "reason": "didn't reach /checkout page",
                "actual_url": page.url, "click_log": obs["click_log"]}

    # Verify the date+time on the appointment summary match the asserted
    # values. Scoped to the summary region, not the whole page.
    summary_text, summary_source = _appointment_summary_text(page)
    obs["summary_source"] = summary_source
    mismatch, year_verified = _verify_summary_matches(summary_text, confirm_date, confirm_time)
    if mismatch:
        browser.close()
        return {"status": "confirm_mismatch", "reason": mismatch,
                "checkout_url": page.url, "amounts": obs["amounts"],
                "summary_source": summary_source}
    obs["date_year_verified"] = year_verified

    # Detect card-required. If detection itself failed we must not submit:
    # this could be a merchant whose checkout the script cannot complete.
    card_required, card_detect_error = _detect_card_required(page)
    if card_detect_error:
        browser.close()
        return {"status": "error",
                "reason": (f"couldn't determine whether this merchant requires a card "
                           f"({card_detect_error}); did NOT submit. Nothing was booked."),
                "checkout_url": obs["checkout_url"], "click_log": obs["click_log"]}
    if card_required:
        browser.close()
        return {
            "status": "card_required",
            "checkout_url": obs["checkout_url"],
            "amounts": obs["amounts"],
            "cancellation_policy": obs["cancellation_policy"],
            "message": ("This merchant requires a credit/debit card to book. "
                        "The v1 script can't fill a Stripe card iframe. Open "
                        "the checkout URL in a browser to finish the booking; "
                        "all other fields will already be there for you to "
                        "fill in."),
        }

    # Fill in customer fields.
    filled: dict[str, bool] = {}
    filled["phone"] = _fill_field(page, "Phone number", customer.get("phone", ""))
    filled["first_name"] = _fill_field(page, "First name", customer.get("first_name", ""))
    filled["last_name"] = _fill_field(page, "Last name", customer.get("last_name", ""))
    filled["email"] = _fill_field(page, "Email", customer.get("email", ""))
    if note:
        _fill_field(page, "Appointment note", note)
    obs["fields_filled"] = filled

    missing = [k for k, ok in filled.items() if not ok]
    if missing:
        browser.close()
        return {"status": "error", "reason": f"couldn't fill fields: {missing}",
                "checkout_url": obs["checkout_url"], "click_log": obs["click_log"]}

    if dry_run:
        browser.close()
        return {
            "status": "dry_run_ok",
            "checkout_url": obs["checkout_url"],
            "amounts": obs["amounts"],
            "cancellation_policy": obs["cancellation_policy"],
            "date_year_verified": year_verified,
            "would_submit_fields": {
                "phone": f"…{(customer.get('phone') or '')[-4:]}",
                "first_name": customer.get("first_name"),
                "last_name": customer.get("last_name"),
                "email": customer.get("email"),
                "note": note,
            },
            "message": "Dry run successful — all fields filled, would have clicked 'Book appointment'.",
        }

    # Submit. Square renders two copies of the "Book appointment" button —
    # one for mobile viewports (data-testid="book-appointment-button-mobile",
    # hidden on md+ screens) and one for desktop. Selecting just
    # `market-button:has-text("Book appointment")` can latch onto the
    # hidden mobile copy and time out. We instead find the first VISIBLE
    # one via JS bounding-rect inspection.
    try:
        clicked_kind = page.evaluate("""() => {
            const btns = document.querySelectorAll(
                'market-button[data-testid^="book-appointment-button"]'
            );
            for (const b of btns) {
                const r = b.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    b.click();
                    return b.getAttribute('data-testid') || 'unknown';
                }
            }
            return null;
        }""")
        if not clicked_kind:
            browser.close()
            return {"status": "error",
                    "reason": "No visible 'Book appointment' button found on /checkout.",
                    "checkout_url": obs["checkout_url"], "click_log": obs["click_log"]}
        obs["click_log"].append({"label": "book_submit", "via": "js", "testid": clicked_kind})
    except Exception as e:
        browser.close()
        return {"status": "error", "reason": f"submit click failed: {e}",
                "checkout_url": obs["checkout_url"], "click_log": obs["click_log"]}
    page.wait_for_timeout(8000)

    # Confirmation page.
    confirmation_url = page.url
    confirmation_body = _checkout_summary_text(page)
    # Square sometimes routes through a /pending state; wait a bit more if
    # we're still on /checkout.
    if "/checkout" in confirmation_url:
        page.wait_for_timeout(5000)
        confirmation_url = page.url
        confirmation_body = _checkout_summary_text(page)

    # VERIFY the submit actually went through before claiming a booking. A
    # successful submit leaves the checkout step; if a visible 'Book
    # appointment' button is still on the page, nothing was booked.
    book_button_visible = _book_button_visible(page)
    browser.close()

    m_handle = re.search(
        r"https?://[^\s'\"<>]*squareup\.com/[^\s'\"<>]*reservations?/[A-Za-z0-9]+",
        confirmation_body + " " + confirmation_url,
    )
    confirmed = bool(m_handle) or bool(_CONFIRMED_RE.search(confirmation_body))

    if not confirmed:
        if book_button_visible is True:
            return {
                "status": "submit_failed",
                "reason": ("clicked 'Book appointment' but the checkout page is still "
                           "showing that button — the booking did NOT go through and "
                           "NOTHING WAS BOOKED."),
                "checkout_url": obs["checkout_url"],
                "final_url": confirmation_url,
                "page_body_head": confirmation_body[:1200],
                "click_log": obs["click_log"],
            }
        if book_button_visible is None:
            return {
                "status": "uncertain",
                "reason": ("clicked 'Book appointment' but the page could not be read "
                           "afterwards, so the booking is UNCONFIRMED. Do not tell the "
                           "user it is booked; re-check with square-list.py for this "
                           "merchant."),
                "checkout_url": obs["checkout_url"],
                "final_url": confirmation_url,
                "click_log": obs["click_log"],
            }

    return {
        "status": "booked",
        "booking_handle": m_handle.group(0) if m_handle else confirmation_url,
        "amounts": obs["amounts"],
        "cancellation_policy": obs["cancellation_policy"],
        "confirmation_url": confirmation_url,
        "confirmation_body_head": confirmation_body[:1200],
        "date_year_verified": year_verified,
        "confirmation_marker": confirmed,
    }


# ── main entry ───────────────────────────────────────────────────────────────

def _run() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--slot-handle", required=True,
                    help="The JSON blob find-slot emits for the chosen slot.")
    ap.add_argument("--confirm-date", required=True,
                    help="ISO date the agent asserts. Must match the checkout summary.")
    ap.add_argument("--confirm-time", required=True,
                    help="Display time the agent asserts, e.g. '1:15 PM'.")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually book. Pass ONLY after the user has "
                         "explicitly approved booking this exact date and time.")
    ap.add_argument("--note", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Fill all fields and verify, but do NOT click Book. "
                         "Books nothing, so it does not need --confirm.")
    args = ap.parse_args()

    # Footgun guard — refuse BEFORE anything else happens.
    if not args.confirm and not args.dry_run:
        print(json.dumps({
            "ok": False,
            "error": "booking creates a real appointment on the merchant's calendar. "
                     "Re-run with --confirm ONLY after the user has explicitly approved "
                     "booking this exact date and time, or use --dry-run to preview. "
                     "Nothing was booked.",
        }, indent=2))
        return 1

    env = load_env(SCRIPT_DIR / ".env")
    merchants_file = Path(env_value(env, "MERCHANTS_FILE") or DEFAULT_MERCHANTS)
    if not merchants_file.exists():
        print(json.dumps({"error": "merchants file not found", "path": str(merchants_file)}, indent=2))
        return 2
    merchants = json.loads(merchants_file.read_text())
    cfg = merchants.get(args.merchant)
    if not cfg:
        print(json.dumps({"error": f"merchant '{args.merchant}' not configured",
                          "configured_aliases": sorted(merchants.keys())}, indent=2))
        return 2

    if not DEFAULT_CUSTOMER.exists():
        print(json.dumps({"error": "customer info not configured",
                          "path": str(DEFAULT_CUSTOMER),
                          "hint": "run customer-info.py set for each field"}, indent=2))
        return 2
    customer = json.loads(DEFAULT_CUSTOMER.read_text())
    missing_cust = [k for k in ("phone", "first_name", "last_name", "email")
                    if not customer.get(k)]
    if missing_cust:
        print(json.dumps({"error": "customer info incomplete",
                          "missing_fields": missing_cust}, indent=2))
        return 2

    service_url, iso_date, time_label = _service_url_from_handle(args.slot_handle)
    try:
        slot_date = date.fromisoformat(iso_date)
    except ValueError:
        print(json.dumps({"error": f"slot_handle.date isn't ISO: {iso_date}"}, indent=2))
        return 2

    # Internal sanity: the agent's --confirm-date must match the slot_handle date.
    if args.confirm_date != iso_date:
        print(json.dumps({
            "status": "confirm_mismatch",
            "reason": f"--confirm-date {args.confirm_date} doesn't match "
                      f"slot_handle date {iso_date}",
        }, indent=2))
        return 2
    confirm_norm = _normalize_time(args.confirm_time)
    if not confirm_norm:
        print(json.dumps({
            "ok": False,
            "error": f"--confirm-time {args.confirm_time!r} isn't a display time like "
                     f"'1:15 PM'. A time without AM/PM is ambiguous and cannot be "
                     f"verified. Nothing was booked.",
        }, indent=2))
        return 1
    slot_norm = _normalize_time(time_label)
    if not slot_norm:
        print(json.dumps({
            "ok": False,
            "error": f"slot_handle time {time_label!r} isn't a display time like "
                     f"'1:15 PM'. Re-run square-find-slot.py to get a fresh slot. "
                     f"Nothing was booked.",
        }, indent=2))
        return 1
    if confirm_norm != slot_norm:
        print(json.dumps({
            "status": "confirm_mismatch",
            "reason": f"--confirm-time {args.confirm_time} doesn't match "
                      f"slot_handle time {time_label}",
        }, indent=2))
        return 2

    result = book(
        service_url=service_url,
        target_date=slot_date,
        target_time=time_label,
        confirm_date=args.confirm_date,
        confirm_time=args.confirm_time,
        customer=customer,
        note=args.note,
        dry_run=args.dry_run,
    )
    result["merchant_alias"] = args.merchant
    result["merchant_name"] = cfg.get("name")
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") in ("submit_failed", "uncertain") else 0


def main() -> int:
    """Wrap _run so an internal `raise SystemExit("sentence")` still produces
    one JSON object on stdout instead of a bare line on stderr."""
    try:
        return _run()
    except SystemExit as e:
        code = e.code
        if code is None or isinstance(code, int):
            return 0 if code is None else code
        print(json.dumps({"ok": False, "error": str(code)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
