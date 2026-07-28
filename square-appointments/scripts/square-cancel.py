#!/usr/bin/env python3
"""square-cancel.py — cancel an existing Square appointment.

The agent calls this only after the user explicitly confirms ("yes, cancel
it"). Takes the `booking_handle` URL emitted by square-list.py (the manage
link from the confirmation email) and walks Square's manage page through
the Cancel button + confirmation dialog.

Usage:
  python3 square-cancel.py --merchant <alias> --booking-handle '<URL>' \\
                           --confirm-date <YYYY-MM-DD> \\
                           --confirm-time '<HH:MM AM/PM>' --confirm
  python3 square-cancel.py ... --dry-run     # verifies only, cancels nothing

Required invariants:
  * --confirm is mandatory for a real cancellation. Pass it only after the
    user has explicitly approved canceling this exact appointment.
  * --confirm-date AND --confirm-time must both match what the manage page
    displays. If either disagrees, the script refuses — that's what stops a
    confused model from canceling the wrong booking. Times repeat every day,
    so the date is what distinguishes two same-time bookings.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MERCHANTS = Path.home() / ".config" / "square-appointments" / "merchants.json"

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

from playwright.sync_api import sync_playwright  # noqa: E402
from playwright_stealth import Stealth  # noqa: E402


_TIME_LABEL_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", re.IGNORECASE)

# Square's own post-cancel copy. Deliberately specific: a bare "canceled"
# also appears in the merchant's cancellation-POLICY blurb, which is on the
# page BEFORE anything is canceled.
_CANCELED_RE = re.compile(
    r"\b(?:Appointment|Booking)\s+(?:(?:has\s+been|have\s+been|was|is)\s+)?cancell?ed\b",
    re.I,
)

# Containers holding the appointment summary on the manage page. The
# date/time verification is scoped here rather than to the whole body, where
# policy copy and unrelated dates can satisfy the match.
_SUMMARY_SELECTORS = (
    '[data-testid="appointment-summary"]',
    '[data-testid="confirmation-page_appointment-summary"]',
    '[data-testid*="appointment-summary"]',
    '[data-testid="booking-details"]',
)


def _normalize_time(s: str) -> str | None:
    m = _TIME_LABEL_RE.match(s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
    return f"{h}:{mi} {ap}"


def _has_clock_time(txt: str) -> bool:
    """A candidate summary region is only usable if it actually carries the
    appointment's time; otherwise we'd refuse every appointment."""
    return bool(re.search(r"\d{1,2}:\d{2}", txt or ""))


def _summary_text(page, body: str) -> tuple[str, str]:
    """Return (text, source) for the appointment-summary region of the manage
    page, falling back to the full body when the template is unrecognised."""
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
                .filter(e => /Appointment (summary|details)/i.test(e.innerText || ''));
            if (!els.length) return '';
            els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
            return els[0].innerText || '';
        }""")
    except Exception:
        txt = ""
    if txt and _has_clock_time(txt):
        return txt, "appointment-summary-heading"
    return body, "body_fallback"


def _verify_time_on_page(text: str, confirm_time: str) -> str | None:
    norm = _normalize_time(confirm_time)
    if not norm:
        return (f"--confirm-time {confirm_time!r} isn't a display time like '1:15 PM'; "
                f"a time without AM/PM is ambiguous and can't be verified")
    h, _, rest = norm.partition(":")
    minute, _, ap = rest.partition(" ")
    pat = re.compile(
        rf"\b{int(h)}:{minute}\s*(?:[–\-—]\s*\d{{1,2}}:\d{{2}}\s*)?{ap}\b",
        re.I,
    )
    return None if pat.search(text) else f"appointment summary doesn't show {norm}"


def _match_appointment_date(text: str, d: date) -> tuple[bool, bool, list[str]]:
    """Look for `d` rendered the way Square renders it — "Wednesday, Jun 24,
    2026" / "Wed, June 24". Returns (matched, year_verified, wrong_years)."""
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


def _verify_date_on_page(text: str, confirm_date: str) -> tuple[str | None, bool]:
    """Return (None, year_verified) when the summary shows `confirm_date`,
    else (diagnostic, False)."""
    try:
        d = date.fromisoformat(confirm_date)
    except ValueError:
        return f"--confirm-date not ISO: {confirm_date!r}", False
    matched, year_verified, wrong_years = _match_appointment_date(text, d)
    if matched:
        return None, year_verified
    if wrong_years:
        return (f"appointment summary shows {d.strftime('%A, %b %-d')} in "
                f"{'/'.join(sorted(set(wrong_years)))}, not {d.year}"), False
    return (f"appointment summary doesn't show "
            f"{d.strftime('%A, %b %-d, %Y')}"), False


def cancel(booking_handle: str, confirm_time: str, confirm_date: str,
           dry_run: bool = False) -> dict:
    obs: dict[str, Any] = {"booking_handle": booking_handle, "click_log": []}

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
        )
        page = ctx.new_page()
        try:
            page.goto(booking_handle, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            browser.close()
            return {"status": "error", "reason": f"goto manage URL failed: {e}"}
        page.wait_for_timeout(5000)
        obs["final_url"] = page.url
        try:
            body = page.locator("body").inner_text()
        except Exception as e:
            browser.close()
            return {"status": "error",
                    "reason": f"couldn't read the manage page: {str(e).splitlines()[0][:160]}. "
                              f"Nothing was canceled.",
                    "final_url": obs["final_url"]}
        obs["body_head"] = body[:1200]

        # Past appointments: Square shows "Appointment passed" and disables
        # cancel. Surface this rather than trying to click.
        if re.search(r"Appointment passed", body, re.I):
            browser.close()
            return {"status": "already_passed",
                    "detail": "Square reports this appointment has already passed; nothing to cancel.",
                    "final_url": obs["final_url"]}

        # Cancellation-window-expired: "This appointment can't be canceled
        # or rescheduled as the cancellation window has passed."
        if re.search(r"can.t be canceled or rescheduled", body, re.I):
            browser.close()
            return {"status": "outside_window",
                    "detail": "Square refuses cancellation: the merchant's cancellation window has passed.",
                    "final_url": obs["final_url"]}

        # Verify time + date match the agent's assertion, against the
        # appointment summary rather than the whole page.
        summary, summary_source = _summary_text(page, body)
        obs["summary_source"] = summary_source
        mismatch = _verify_time_on_page(summary, confirm_time)
        if mismatch:
            browser.close()
            return {"status": "confirm_mismatch", "reason": mismatch,
                    "final_url": obs["final_url"], "summary_source": summary_source}
        mismatch, year_verified = _verify_date_on_page(summary, confirm_date)
        if mismatch:
            browser.close()
            return {"status": "confirm_mismatch", "reason": mismatch,
                    "final_url": obs["final_url"], "summary_source": summary_source}
        obs["date_year_verified"] = year_verified

        if dry_run:
            browser.close()
            return {"status": "dry_run_ok",
                    "detail": "Manage page shows this exact appointment; a real run "
                              "would click Cancel. Nothing was canceled.",
                    "final_url": obs["final_url"],
                    "summary_source": summary_source,
                    "date_year_verified": year_verified,
                    "summary_head": summary[:600]}

        # Click Cancel. Square's confirmation page exposes the cancel action
        # via `data-testid="confirmation-page_cancel-button"` (a
        # <market-button> whose visible label lives in a child element, so
        # text-based selectors miss it). On other manage-page templates
        # the action may be labelled "Cancel appointment". We try the
        # canonical testid first and fall back to visible-text variants.
        cancel_clicked_via = None
        try:
            cancel_clicked_via = page.evaluate("""() => {
                const candidates = [
                    '[data-testid="confirmation-page_cancel-button"]',
                    '[data-testid="cancel-button"]',
                    'market-button[data-testid*="cancel"]',
                ];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            el.click();
                            return sel;
                        }
                    }
                }
                return null;
            }""")
        except Exception:
            pass
        if cancel_clicked_via:
            obs["click_log"].append({"label": "cancel", "sel": cancel_clicked_via})
            page.wait_for_timeout(3500)
        else:
            # Fall back to text-based searches.
            for sel in (
                'market-button:has-text("Cancel appointment")',
                'text="Cancel appointment"',
            ):
                try:
                    loc = page.locator(sel).first
                    if loc.count() == 0:
                        continue
                    loc.click(timeout=4000)
                    obs["click_log"].append({"label": "cancel", "sel": sel})
                    cancel_clicked_via = sel
                    page.wait_for_timeout(3500)
                    break
                except Exception as e:
                    obs["click_log"].append({"label": "cancel_err", "sel": sel,
                                             "err": str(e).splitlines()[0][:160]})
        if not cancel_clicked_via:
            browser.close()
            return {"status": "error",
                    "reason": "couldn't find a Cancel button on the manage page",
                    "final_url": obs["final_url"], "click_log": obs["click_log"]}

        # Confirmation dialog. Square typically asks "Are you sure you want
        # to cancel?" → "Yes, cancel" or similar. Click any confirm option.
        confirm_clicked = False
        for sel in (
            'market-button:has-text("Yes, cancel")',
            'market-button:has-text("Confirm")',
            'market-button:has-text("Cancel appointment")',
            'market-button:has-text("Yes")',
            'text="Yes, cancel"',
            'text="Confirm cancel"',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.click(timeout=4000)
                obs["click_log"].append({"label": "confirm_cancel", "sel": sel})
                confirm_clicked = True
                page.wait_for_timeout(5000)
                break
            except Exception as e:
                obs["click_log"].append({"label": "confirm_cancel_err", "sel": sel,
                                         "err": str(e).splitlines()[0][:160]})
        if not confirm_clicked:
            # Some manage pages skip the dialog and cancel immediately on the
            # first click. Check whether the page now reflects cancellation.
            try:
                body2 = page.locator("body").inner_text()
            except Exception as e:
                # The Cancel click already went out — a read failure here says
                # nothing about whether it took effect.
                browser.close()
                return {"status": "uncertain",
                        "detail": f"Cancel was clicked but the page couldn't be read "
                                  f"afterwards ({str(e).splitlines()[0][:120]}), so the "
                                  f"outcome is UNKNOWN — the appointment may or may not "
                                  f"be canceled. Re-check by running square-list.py for "
                                  f"this merchant.",
                        "final_url": page.url,
                        "click_log": obs["click_log"]}
            if _CANCELED_RE.search(body2):
                browser.close()
                return {"status": "canceled", "detail": "Single-click cancel succeeded.",
                        "final_url": page.url, "body_head": body2[:1000]}
            browser.close()
            return {"status": "error",
                    "reason": "Cancel button clicked but no confirmation dialog and no canceled state detected",
                    "final_url": page.url,
                    "click_log": obs["click_log"]}

        # Read the post-cancel state.
        read_error = None
        try:
            body3 = page.locator("body").inner_text()
        except Exception as e:
            body3 = ""
            read_error = str(e).splitlines()[0][:120]
        if _CANCELED_RE.search(body3):
            browser.close()
            return {"status": "canceled",
                    "final_url": page.url,
                    "body_head": body3[:1000]}
        browser.close()
        return {"status": "uncertain",
                "detail": (f"Cancel + confirm clicks ran but the page couldn't be read "
                           f"afterwards ({read_error}), so the outcome is UNKNOWN. "
                           f"Re-check by running square-list.py for this merchant."
                           if read_error else
                           "Cancel + confirm clicks ran but the post-cancel page didn't "
                           "confirm the cancellation. Re-check by running square-list.py "
                           "for this merchant."),
                "final_url": page.url,
                "body_head": body3[:1200],
                "click_log": obs["click_log"]}


def _run() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--booking-handle", required=True,
                    help="The manage URL from square-list.py output.")
    ap.add_argument("--confirm-time", required=True,
                    help="Display time the agent asserts, e.g. '1:15 PM'.")
    ap.add_argument("--confirm-date", required=True,
                    help="ISO date the agent asserts, e.g. 2026-06-24. Required: "
                         "times repeat every day, so the time alone cannot tell two "
                         "same-time appointments apart.")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually cancel. Pass ONLY after the user has "
                         "explicitly approved canceling this exact appointment.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Verify the manage page shows this exact appointment, but do "
                         "NOT click Cancel. Cancels nothing, so it does not need "
                         "--confirm.")
    args = ap.parse_args()

    # Footgun guard — refuse BEFORE anything else happens.
    if not args.confirm and not args.dry_run:
        print(json.dumps({
            "ok": False,
            "error": "canceling removes a real appointment from the merchant's calendar "
                     "and cannot be undone by this tool. Re-run with --confirm ONLY after "
                     "the user has explicitly approved canceling this exact appointment, "
                     "or use --dry-run to check it first. Nothing was canceled.",
        }, indent=2))
        return 1

    # Validate the merchant alias, so the agent gets a consistent error shape
    # for typos. A merchants file we cannot parse means we cannot validate at
    # all — refuse rather than cancel unvalidated.
    if DEFAULT_MERCHANTS.exists():
        try:
            merchants = json.loads(DEFAULT_MERCHANTS.read_text())
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "error": f"couldn't read the merchant configuration at "
                         f"{DEFAULT_MERCHANTS}: {str(e).splitlines()[0][:160]}. "
                         f"The merchant alias could not be validated, so nothing "
                         f"was canceled.",
            }, indent=2))
            return 1
        if not isinstance(merchants, dict):
            print(json.dumps({
                "ok": False,
                "error": f"the merchant configuration at {DEFAULT_MERCHANTS} isn't a "
                         f"JSON object of aliases. Nothing was canceled.",
            }, indent=2))
            return 1
        if args.merchant not in merchants:
            print(json.dumps({
                "status": "error",
                "reason": f"merchant '{args.merchant}' not configured",
                "configured_aliases": sorted(merchants.keys()),
            }, indent=2))
            return 2

    result = cancel(args.booking_handle, args.confirm_time, args.confirm_date,
                    dry_run=args.dry_run)
    result["merchant_alias"] = args.merchant
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "uncertain" else 0


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
