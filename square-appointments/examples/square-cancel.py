#!/usr/bin/env python3
"""square-cancel.py — cancel an existing Square appointment.

The agent calls this only after the user explicitly confirms ("yes, cancel
it"). Takes the `booking_handle` URL emitted by square-list.py (the manage
link from the confirmation email) and walks Square's manage page through
the Cancel button + confirmation dialog.

Usage:
  python3 square-cancel.py --merchant <alias> --booking-handle '<URL>' \\
                           --confirm-time '<HH:MM AM/PM>' [--confirm-date <YYYY-MM-DD>]

Required invariant: --confirm-time must match the start time displayed on
the manage page. If it doesn't, the script refuses — that's what stops a
confused model from canceling the wrong booking.
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


def _normalize_time(s: str) -> str | None:
    m = _TIME_LABEL_RE.match(s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
    return f"{h}:{mi} {ap}"


def _verify_time_on_page(body: str, confirm_time: str) -> str | None:
    norm = _normalize_time(confirm_time) or confirm_time
    h, _, rest = norm.partition(":")
    minute, _, ap = rest.partition(" ")
    pat = re.compile(
        rf"\b{int(h)}:{minute}\s*(?:[–\-—]\s*\d{{1,2}}:\d{{2}}\s*)?{ap}\b",
        re.I,
    )
    return None if pat.search(body) else f"manage page doesn't show {norm}"


def _verify_date_on_page(body: str, confirm_date: str | None) -> str | None:
    if not confirm_date:
        return None
    try:
        d = date.fromisoformat(confirm_date)
    except ValueError:
        return f"--confirm-date not ISO: {confirm_date!r}"
    pat = re.compile(
        rf"(?:{d.strftime('%A')}|{d.strftime('%a')}),?\s+{d.strftime('%b')}\s+0?{d.day}\b",
        re.I,
    )
    return None if pat.search(body) else (
        f"manage page doesn't show {d.strftime('%A')}, {d.strftime('%b')} {d.day}"
    )


def cancel(booking_handle: str, confirm_time: str, confirm_date: str | None) -> dict:
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
        except Exception:
            body = ""
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

        # Verify time + date match the agent's assertion.
        mismatch = _verify_time_on_page(body, confirm_time)
        if mismatch:
            browser.close()
            return {"status": "confirm_mismatch", "reason": mismatch, "final_url": obs["final_url"]}
        mismatch = _verify_date_on_page(body, confirm_date)
        if mismatch:
            browser.close()
            return {"status": "confirm_mismatch", "reason": mismatch, "final_url": obs["final_url"]}

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
            except Exception:
                body2 = ""
            if re.search(r"(Appointment canceled|Booking canceled|canceled)", body2, re.I):
                browser.close()
                return {"status": "canceled", "detail": "Single-click cancel succeeded.",
                        "final_url": page.url, "body_head": body2[:1000]}
            browser.close()
            return {"status": "error",
                    "reason": "Cancel button clicked but no confirmation dialog and no canceled state detected",
                    "final_url": page.url,
                    "click_log": obs["click_log"]}

        # Read the post-cancel state.
        try:
            body3 = page.locator("body").inner_text()
        except Exception:
            body3 = ""
        if re.search(r"(Appointment canceled|Booking canceled|canceled)", body3, re.I):
            browser.close()
            return {"status": "canceled",
                    "final_url": page.url,
                    "body_head": body3[:1000]}
        browser.close()
        return {"status": "uncertain",
                "detail": "Cancel + confirm clicks ran but the post-cancel page didn't include 'canceled'. "
                          "Re-check by running square-list.py for this merchant.",
                "final_url": page.url,
                "body_head": body3[:1200],
                "click_log": obs["click_log"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--booking-handle", required=True,
                    help="The manage URL from square-list.py output.")
    ap.add_argument("--confirm-time", required=True,
                    help="Display time the agent asserts, e.g. '1:15 PM'.")
    ap.add_argument("--confirm-date", default=None,
                    help="ISO date the agent asserts (optional but recommended).")
    args = ap.parse_args()

    # Light validation that the merchant alias exists, so the agent gets a
    # consistent error shape for typos.
    if DEFAULT_MERCHANTS.exists():
        try:
            merchants = json.loads(DEFAULT_MERCHANTS.read_text())
            if args.merchant not in merchants:
                print(json.dumps({
                    "status": "error",
                    "reason": f"merchant '{args.merchant}' not configured",
                    "configured_aliases": sorted(merchants.keys()),
                }, indent=2))
                return 2
        except Exception:
            pass

    result = cancel(args.booking_handle, args.confirm_time, args.confirm_date)
    result["merchant_alias"] = args.merchant
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
