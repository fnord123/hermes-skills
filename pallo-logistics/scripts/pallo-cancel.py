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
  # preview:
  python3 pallo-cancel.py --stay-id 2026-12-11/2026-12-20 \\
      --confirm-drop-date 2026-12-11 --confirm-pickup-date 2026-12-20 --dry-run
  # for real, after the user approves this exact stay:
  python3 pallo-cancel.py --stay-id 2026-12-11/2026-12-20 \\
      --confirm-drop-date 2026-12-11 --confirm-pickup-date 2026-12-20 --confirm

Output: JSON `status`:
  dry_run_ok | cancelled | already_canceled | not_found | confirm_required |
  confirm_mismatch | not_cancellable | uncertain | session_expired |
  not_logged_in | error

SAFETY: cancelling is irreversible. The agent must echo the stay's dates and get
an explicit in-turn "yes" before calling this with --confirm. The stay is located
by weekday+date so a same-month/day stay in a DIFFERENT YEAR can never be the one
opened, and the confirmation dialog is only clicked inside the dialog itself.
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
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# "12/11" or "12/11/2026" — the year is optional because the portal prints it
# inconsistently, but when it IS printed it must match.
_MD_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
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


def _day_frag(d: date) -> str:
    """Month/day fragment only, e.g. date(2026,12,11) -> 'Dec. 11th'. Ambiguous
    across years — use _display_frag to locate a stay."""
    return f"{_MON[d.month - 1]}. {d.day}{_ordinal(d.day)}"


def _display_frag(d: date) -> str:
    """Portal date fragment INCLUDING the weekday, e.g. date(2026,12,11) ->
    'Fri, Dec. 11th'.

    The portal omits the year from its labels, so the weekday is what pins the
    year: the same month/day in another year falls on a different weekday. Match
    on the weekday-bearing fragment or a stay from a different year can be opened
    (and cancelled) while the month/day check still passes."""
    return f"{_WD[d.weekday()]}, {_day_frag(d)}"


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
    mds = _MD_RE.findall(text)  # drop, then pickup; year group may be ''
    cancel_present = "CANCEL BOOKING" in text
    return {"status": status, "mds": mds, "cancel_present": cancel_present,
            "text": text}


def _weekday_conflict(text: str, want: date) -> bool:
    """True if the page prints this month/day carrying a weekday that is NOT
    `want`'s — i.e. the opened booking is the same month/day in another year."""
    for m in re.finditer(rf"\b([A-Z][a-z]{{2}}),\s*{re.escape(_day_frag(want))}", text):
        if m.group(1) != _WD[want.weekday()]:
            return True
    return False


def _md_matches(info: dict, drop: date, pick: date) -> bool:
    """True only if the OPENED booking is this exact stay, YEAR INCLUDED.

    Month/day alone is not an identity: the same Dec. 11th recurs every year, so
    a match on month/day would happily cancel next year's stay. The year is
    checked three ways — the numeric label's year when the portal prints one, the
    weekday the portal attaches to a date (a different year gives a different
    weekday), and, upstream of this, the weekday-bearing fragment used to pick the
    card in the first place."""
    mds = info.get("mds") or []
    if len(mds) < 2:
        return False
    for (mm, dd, yy), want in zip(mds[:2], (drop, pick)):
        if (int(mm), int(dd)) != (want.month, want.day):
            return False
        if yy:
            year = int(yy)
            if year < 100:
                year += 2000
            if year != want.year:
                return False
    text = info.get("text") or ""
    return not (_weekday_conflict(text, drop) or _weekday_conflict(text, pick))


def _confirm_dialog_click(page, tries: int = 8) -> dict:
    """Confirm the cancellation INSIDE the confirmation dialog.

    Returns {"dialog": bool, "clicked": bool, "label": str|None}: whether the
    dialog was found at all, and whether a confirm control inside it was pressed.
    The search is scoped to the dialog container and the dialog must exist first —
    an unscoped page-wide text match would press the first `yes`/`confirm`-looking
    element anywhere on the page."""
    for _ in range(tries):
        res = page.evaluate(r"""(pattern) => {
            const RE = new RegExp(pattern, 'i');
            const txt = el => (el.innerText || '').trim();
            // The dialog: an explicit ARIA dialog when the SPA provides one,
            // else the innermost floating overlay that holds a confirm control.
            let dialogs = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')];
            if (!dialogs.length) {
                dialogs = [...document.querySelectorAll('div')].filter(el => {
                    const st = getComputedStyle(el);
                    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 120 || r.height < 60) return false;
                    const t = txt(el);
                    if (!t || t.length > 600) return false;
                    return [...el.querySelectorAll('div,span,button,a')].some(e => RE.test(txt(e)));
                });
                // innermost wins: drop any candidate that contains another one
                dialogs = dialogs.filter(el => !dialogs.some(o => o !== el && el.contains(o)));
            }
            dialogs = dialogs.filter(el => el.getBoundingClientRect().height > 0);
            if (!dialogs.length) return {dialog: false, clicked: false, label: null};
            const dlg = dialogs[dialogs.length - 1];
            let hits = [...dlg.querySelectorAll('div,span,button,a')].filter(e => RE.test(txt(e)));
            hits = hits.filter(e => !hits.some(o => o !== e && e.contains(o)));  // leaf-most
            if (!hits.length) return {dialog: true, clicked: false, label: null};
            const hit = hits[0];
            const label = txt(hit);
            const r = hit.getBoundingClientRect();
            const o = {bubbles: true, cancelable: true,
                       clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1};
            hit.dispatchEvent(new PointerEvent('pointerdown', o));
            hit.dispatchEvent(new MouseEvent('mousedown', o));
            hit.dispatchEvent(new PointerEvent('pointerup', o));
            hit.dispatchEvent(new MouseEvent('mouseup', o));
            hit.dispatchEvent(new MouseEvent('click', o));
            return {dialog: true, clicked: true, label: label};
        }""", _CONFIRM_RE.pattern)
        if res.get("clicked"):
            return res
        if res.get("dialog"):
            return res          # dialog is up but has no confirm control we know
        page.wait_for_timeout(500)   # dialog may still be animating in
    return {"dialog": False, "clicked": False, "label": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stay-id", required=True, help='"<start>/<end>" ISO, from pallo-stays.py')
    ap.add_argument("--confirm-drop-date", required=True, help="ISO; must match the stay's drop-off")
    ap.add_argument("--confirm-pickup-date", required=True, help="ISO; must match the stay's pickup")
    ap.add_argument("--dry-run", action="store_true",
                    help="Open + verify the booking, report what would be cancelled. No cancel.")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually cancel. Pass it ONLY after the user has "
                         "explicitly approved cancelling this exact stay.")
    args = ap.parse_args()

    # Footgun guard — refuse BEFORE any side effect (no browser is launched).
    if not args.confirm and not args.dry_run:
        return out({"ok": False, "status": "confirm_required",
                    "error": "cancelling a reservation is irreversible. Re-run with --confirm "
                             "ONLY after the user has explicitly approved cancelling this exact "
                             "stay, or use --dry-run to verify which stay it is.",
                    "reason": "cancelling a reservation is irreversible. Re-run with --confirm "
                              "ONLY after the user has explicitly approved cancelling this exact "
                              "stay, or use --dry-run to verify which stay it is."}, 1)

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
                if not _md_matches(info, start, end):
                    return out({"ok": False, "status": "confirm_mismatch",
                                "error": "opened booking's displayed dates do not match the "
                                         "requested stay; refusing to cancel.",
                                "reason": "opened booking's displayed dates do not match the "
                                          "requested stay; refusing to cancel.",
                                "displayed_dates": ["/".join(p for p in m if p)
                                                    for m in info["mds"][:2]],
                                "expected": [start.isoformat(), end.isoformat()]}, 1)
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
                dlg = _confirm_dialog_click(page)
                page.wait_for_timeout(4000)
                after = _detail_info(page)
                base["detail_url"] = page.url
                base["confirm_dialog"] = dlg
                if after["status"] == "Canceled":
                    return out({**base, "status": "cancelled"})
                if not dlg.get("dialog"):
                    return out({**base, "status": "uncertain",
                                "post_status": after["status"],
                                "reason": "CANCEL BOOKING was clicked but no confirmation dialog "
                                          "appeared and the booking does not read as Canceled. "
                                          "Nothing else was clicked. Verify in the portal."}, 1)
                if not dlg.get("clicked"):
                    return out({**base, "status": "uncertain",
                                "post_status": after["status"],
                                "reason": "The confirmation dialog opened but carried no "
                                          "recognisable confirm control, so nothing was clicked. "
                                          "Verify in the portal."}, 1)
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
