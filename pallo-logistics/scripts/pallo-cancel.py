#!/usr/bin/env python3
"""pallo-cancel.py — cancel one of Pallo's boarding reservations (MUTATING).

Locates a stay in the Gingr portal by its dates (the `stay_id` from
pallo-stays.py is "<start>/<end>" ISO), opens its Booking Details page, verifies
the displayed drop-off / pickup dates match the --confirm-* invariants, then
clicks CANCEL BOOKING and confirms. --dry-run stops at the verified detail page
without cancelling.

The portal exposes no per-customer "edit/reschedule" — only CANCEL BOOKING — so
date/activity changes are a cancel + re-book (see pallo-modify-stay.py).

Usage:
  python3 pallo-cancel.py --stay-id 2026-12-11/2026-12-20 \\
      --confirm-drop-date 2026-12-11 --confirm-pickup-date 2026-12-20 [--dry-run]

Output: JSON `status`:
  dry_run_ok | cancelled | already_canceled | not_found | confirm_mismatch |
  not_cancellable | uncertain | session_expired | not_logged_in | error

SAFETY: cancelling is irreversible. The agent must echo the stay's dates and get
an explicit in-turn "yes" before calling this without --dry-run. The real-cancel
confirm-dialog step is best-effort (validate it the first time you cancel a stay
you actually intend to).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

import gingr_lib  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# dialog button texts that mean "yes, really cancel"
_CONFIRM_RE = re.compile(
    r"^(yes|confirm|yes,? cancel|confirm cancellation|cancel booking|cancel reservation)$", re.I)


def out(d: dict, code: int = 0) -> int:
    print(json.dumps(d, indent=2))
    return code


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _display_frag(d: date) -> str:
    """Portal date fragment, e.g. date(2026,12,11) -> 'Dec. 11th'."""
    return f"{_MON[d.month - 1]}. {d.day}{_ordinal(d.day)}"


def _parse_stay_id(stay_id: str) -> tuple[date, date]:
    a, b = stay_id.split("/", 1)
    return date.fromisoformat(a), date.fromisoformat(b)


def _open_stay_detail(page, start: date, end: date) -> bool:
    """Open the bookings list and click the card matching both date fragments."""
    page.goto(gingr_lib.BOOKINGS_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    if "public/login" in page.url:
        raise gingr_lib.SessionExpired("storage_state no longer authenticates")
    sfrag, efrag = _display_frag(start), _display_frag(end)
    clicked = page.evaluate("""([sf, ef]) => {
        const cands=[...document.querySelectorAll('div')].filter(d=>{
            const t=d.innerText||''; return t.includes(sf)&&t.includes(ef)&&t.length<300;});
        cands.sort((a,b)=>a.innerText.length-b.innerText.length);
        if(cands.length){cands[0].click(); return true;}
        return false;
    }""", [sfrag, efrag])
    page.wait_for_timeout(4500)
    return bool(clicked) and "booking-details" in page.url


def _detail_info(page) -> dict:
    text = page.inner_text("body")
    sm = re.search(r"\b(Confirmed|Canceled|Cancelled|Pending|Completed)\b", text)
    status = sm.group(1) if sm else None
    if status == "Cancelled":
        status = "Canceled"
    mds = re.findall(r"\b(\d{1,2})/(\d{1,2})\b", text)  # drop, then pickup
    cancel_present = "CANCEL BOOKING" in text
    return {"status": status, "mds": mds, "cancel_present": cancel_present}


def _md_matches(mds, drop: date, pick: date) -> bool:
    if len(mds) < 2:
        return False
    (dm, dd), (pm, pdd) = (int(mds[0][0]), int(mds[0][1])), (int(mds[1][0]), int(mds[1][1]))
    return (dm, dd) == (drop.month, drop.day) and (pm, pdd) == (pick.month, pick.day)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stay-id", required=True, help='"<start>/<end>" ISO, from pallo-stays.py')
    ap.add_argument("--confirm-drop-date", required=True, help="ISO; must match the stay's drop-off")
    ap.add_argument("--confirm-pickup-date", required=True, help="ISO; must match the stay's pickup")
    ap.add_argument("--dry-run", action="store_true",
                    help="Open + verify the booking, report what would be cancelled. No cancel.")
    args = ap.parse_args()

    if not gingr_lib.state_exists():
        return out({"status": "not_logged_in",
                    "reason": "No saved Gingr session. Run gingr-login.py first."}, 2)
    try:
        start, end = _parse_stay_id(args.stay_id)
    except ValueError:
        return out({"status": "error", "reason": f'bad --stay-id {args.stay_id!r}; expect "<ISO>/<ISO>"'}, 2)

    if (args.confirm_drop_date != start.isoformat()
            or args.confirm_pickup_date != end.isoformat()):
        return out({"status": "confirm_mismatch",
                    "reason": "confirm dates do not match the stay_id",
                    "stay_drop_off": start.isoformat(), "stay_pick_up": end.isoformat(),
                    "confirm_drop_date": args.confirm_drop_date,
                    "confirm_pickup_date": args.confirm_pickup_date}, 1)

    try:
        with sync_playwright() as p:
            browser, ctx = gingr_lib.new_logged_in_context(p)
            page = ctx.new_page()
            try:
                if not _open_stay_detail(page, start, end):
                    return out({"status": "not_found",
                                "reason": f"no booking card matching {args.stay_id} on the bookings list."}, 1)
                info = _detail_info(page)
                # safety: the OPENED booking's displayed dates must match --confirm-*
                if not _md_matches(info["mds"], start, end):
                    return out({"status": "confirm_mismatch",
                                "reason": "opened booking's displayed dates do not match the requested stay; "
                                          "refusing to cancel.",
                                "displayed_month_day": info["mds"][:2],
                                "expected": [f"{start.month}/{start.day}", f"{end.month}/{end.day}"]}, 1)
                if info["status"] == "Canceled":
                    return out({"status": "already_canceled", "stay_id": args.stay_id}, 0)

                base = {"stay_id": args.stay_id, "drop_off": start.isoformat(),
                        "pick_up": end.isoformat(), "current_status": info["status"],
                        "detail_url": page.url}

                if not info["cancel_present"]:
                    return out({**base, "status": "not_cancellable",
                                "reason": "No CANCEL BOOKING control on the detail page "
                                          "(cancellation window may have passed). Cancel via the facility."}, 1)
                if args.dry_run:
                    artifacts = Path.home() / "pallo-boarding" / "artifacts"
                    artifacts.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(artifacts / "cancel_detail_dryrun.png"), full_page=True)
                    return out({**base, "status": "dry_run_ok",
                                "note": "Verified the booking; did NOT cancel."})

                # REAL cancel
                page.get_by_text("CANCEL BOOKING", exact=True).first.click(timeout=10000)
                page.wait_for_timeout(2500)
                # best-effort: click a confirmation control in the resulting dialog
                page.evaluate(r"""() => {
                    const els=[...document.querySelectorAll('div,button,span,a')];
                    const t=el=>(el.innerText||'').trim();
                    const re=/^(yes|confirm|yes,? cancel|confirm cancellation)$/i;
                    let hit=els.find(el=>re.test(t(el)));
                    if(hit){hit.click();}
                }""")
                page.wait_for_timeout(4000)
                after = _detail_info(page)
                base["detail_url"] = page.url
                if after["status"] == "Canceled":
                    return out({**base, "status": "cancelled"})
                return out({**base, "status": "uncertain",
                            "post_status": after["status"],
                            "reason": "Cancel was attempted but the booking does not read as Canceled. "
                                      "Verify in the portal before assuming it was cancelled."}, 1)
            finally:
                ctx.storage_state(path=str(gingr_lib.STATE_FILE))
                browser.close()
    except gingr_lib.SessionExpired:
        return out({"status": "session_expired",
                    "reason": "Saved Gingr session expired. Re-run gingr-login.py."}, 1)
    except Exception as e:  # noqa: BLE001
        return out({"status": "error", "reason": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    sys.exit(main())
