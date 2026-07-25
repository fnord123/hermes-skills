#!/usr/bin/env python3
"""pallo-book-trip.py — execute Pallo's boarding + activity reservation (MUTATING).

Drives the Laurel Acres / Tail Wag Inn Gingr customer portal through its
New Booking Request wizard (Dates -> Services -> Notes -> Review) using the
saved session, books a Boarding | Dog stay for Pallo across the plan's
drop-off..pickup window, and adds the per-day activity slate (§5) as three
frequency-based add-on services. Stops at Review for --dry-run; otherwise
submits and captures the confirmation, then sends any Gina-coordination
messages the plan carries.

Activity slate (§5), expressed as three Gingr "frequency" rules so we configure
the whole stay in three add-service operations instead of one-per-day:
  - Nature Walk : "Every Day Except First Day"        @ 11:30 AM  (full days + pickup day)
  - Play Yard #1: "Every Day Except Last Day"         @ 07:30 AM  (drop-off + full days)
  - Play Yard #2: "Every Day Except First & Last Day" @ 03:30 PM  (full days only)
Net: drop-off day = 1 Play Yard; full days = 2 Play Yard + 1 Nature Walk;
pickup day = 1 Nature Walk. (Two Play Yards get distinct times so Gingr counts
them as two sessions, per the facility's own instructions.)

Usage:
  # From a CUJ-1 plan blob (preferred):
  python3 pallo-book-trip.py --plan '<plan_json>' \\
      --confirm-drop-date 2026-07-10 --confirm-pickup-date 2026-07-31 [--dry-run]

  # Or with explicit dates (no plan):
  python3 pallo-book-trip.py --drop-date 2026-07-10 --pickup-date 2026-07-31 \\
      --confirm-drop-date 2026-07-10 --confirm-pickup-date 2026-07-31 --dry-run

Options:
  --drop-time   "3pm"        drop-off clock time. Accepts loose forms ('3pm',
                             '3:00 PM', '15:00'). Overrides the plan's drop_time;
                             falls back to the plan's, then 08:00 AM.
  --pickup-time "11am"       pickup clock time, same parsing/precedence as above
                             (plan's pickup_time, then 09:00 AM).
  --dry-run                  fill the whole wizard, stop at Review, DO NOT submit
  --allow-overlap            skip the "already booked for these dates" guard
  --no-gina                  do not send the plan's Gina-coordination messages

Output: JSON with a `status` field:
  dry_run_ok | booked | booked_with_notification_warnings |
  conflict | confirm_mismatch | session_expired | not_logged_in | error
This is a REAL, paid reservation. The agent must echo dates + slate and get an
explicit in-turn "yes" before calling this without --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

import gingr_lib  # noqa: E402
import triplib  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORTAL = gingr_lib.PORTAL
HOME_URL = f"{PORTAL}/secure/home"

# §5 activity slate — all via frequency rules (no per-day date-picker adds).
# Gingr dedupes a same-day same-activity session ONLY when the TIME matches, so a
# SECOND Play Yard just needs a DIFFERENT time — it does NOT need a per-day "Once"
# add. Three bulk frequency ops give the whole slate:
#   - Nature Walk : "Every Day Except First Day"        @ 11:30 AM (full days + pickup)
#   - Play Yard #1: "Every Day Except Last Day"         @ 07:30 AM (drop-off + full days)
#   - Play Yard #2: "Every Day Except First & Last Day" @ 03:30 PM (full days only)
# Net: drop-off = 1 PY; each full day = 2 PY + 1 Nature Walk; pickup = 1 Nature Walk.
BULK_OPS = [
    ("Activity | Nature Walk", "Every Day Except First Day", "11:30 AM"),
    ("Activity | Play Yard", "Every Day Except Last Day", "07:30 AM"),
]
# Second daily Play Yard, full days only, at a distinct time (added unless --simple-slate).
SECOND_PLAY_YARD_OP = ("Activity | Play Yard", "Every Day Except First & Last Day", "03:30 PM")
_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def out(d: dict, code: int = 0) -> int:
    print(json.dumps(d, indent=2))
    return code


# ── plan / invariants ────────────────────────────────────────────────────────

def resolve_dates(args) -> tuple[date, date, str | None, str | None, list, str | None]:
    """Return (drop_date, pick_date, plan_drop_time, plan_pickup_time,
    gina_messages, trip_name). The plan times are None when not carried by the
    plan (or when booking from explicit --drop-date/--pickup-date)."""
    if args.plan:
        try:
            plan = json.loads(args.plan)
        except json.JSONDecodeError as e:
            raise ValueError(f"--plan is not valid JSON: {e}")
        d = plan.get("drop_off")
        p = plan.get("pick_up")
        if not (d and p):
            raise ValueError("--plan missing drop_off/pick_up")
        return (date.fromisoformat(d), date.fromisoformat(p),
                plan.get("drop_time"), plan.get("pickup_time"),
                plan.get("gina_messages", []), plan.get("trip_name"))
    if args.drop_date and args.pickup_date:
        return (date.fromisoformat(args.drop_date),
                date.fromisoformat(args.pickup_date), None, None, [], None)
    raise ValueError("supply --plan OR both --drop-date and --pickup-date")


def expected_activity_counts(drop_date: date, pick_date: date, simple: bool) -> dict:
    nights = (pick_date - drop_date).days
    full_days = max(0, nights - 1)
    play_yard = nights + (0 if simple else full_days)  # bulk(EDXLast) + per-day Once
    return {
        "nature_walk": full_days + 1,          # Every Day Except First Day
        "play_yard": play_yard,
        "nights": nights,
        "full_days": full_days,
    }


# ── portal navigation helpers (React-Native-Web SPA: locate by content) ──────

def _dbg(page, tag: str, note: str = ""):
    """Diagnostic capture, active only when PALLO_DEBUG is set. Writes a
    screenshot + a line to artifacts/once_debug.log so we can see what the
    per-day date picker actually does (e.g. whether it reached the target
    month). No-op in normal runs."""
    if not os.environ.get("PALLO_DEBUG"):
        return
    try:
        d = Path.home() / "pallo-boarding" / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(d / f"dbg_{tag}.png"), full_page=True)
        with open(d / "once_debug.log", "a") as f:
            f.write(f"{tag}: {note}\n")
    except Exception:
        pass


def _month_label(page):
    return page.evaluate(r"""() => {const m=[...document.querySelectorAll('div')]
        .find(d=>/^[A-Z][a-z]+ 20\d\d$/.test((d.innerText||'').trim()));
        return m?m.innerText.trim():null;}""")


def _click_next_month(page) -> bool:
    # Find the "Month YYYY" header and press the icon-div immediately to its
    # right (the next-month chevron). Anchoring to the label keeps this correct
    # for the main wizard calendar AND the date-range popup in an activity panel.
    # The chevron is an RNW pressable that IGNORES a bare .click(), so dispatch a
    # full pointer sequence (this was the cause of intermittent month-nav fails).
    return bool(page.evaluate(r"""() => {
        const all=[...document.querySelectorAll('div')];
        const m=all.find(d=>/^[A-Z][a-z]+ 20\d\d$/.test((d.innerText||'').trim()));
        if(!m) return false;
        const mr=m.getBoundingClientRect();
        let best=null;
        for(const el of all){
            const s=el.querySelectorAll(':scope > svg');
            if(s.length!==1 || (el.innerText||'').trim()!=='') continue;
            const r=el.getBoundingClientRect();
            if(r.width>=60 || Math.abs(r.y-mr.y)>40 || r.x<=mr.x) continue;
            if(!best || r.x>best.x){best={el, x:r.x};}   // rightmost = next chevron
        }
        if(!best) return false;
        best.el.click();   // bare click advances this picker; pointer-dispatch did not
        return true;
    }"""))


def _goto_month(page, target: date, max_steps: int = 30) -> bool:
    """Advance the date picker to `target`'s month. The popup chevron click only
    lands intermittently, so verify the month label after each press and keep
    re-pressing; only give up after many consecutive no-advance presses. Returns
    whether the target month was reached (caller must NOT click a day if False)."""
    want = f"{_MONTHS[target.month-1]} {target.year}"
    last = None
    stuck = 0
    for _ in range(max_steps):
        cur = _month_label(page)
        if cur == want:
            return True
        if cur is not None and cur == last:
            stuck += 1
            if stuck >= 10:
                return False
        else:
            stuck = 0
        last = cur
        _click_next_month(page)
        page.wait_for_timeout(700)
    return _month_label(page) == want


def _click_day(page, day: int):
    # A calendar day cell: a leaf div whose text is exactly the day number, in
    # the calendar/popup region (x>340). Broad y covers the popup picker too.
    page.evaluate("""(day)=>{const c=[...document.querySelectorAll('div')].filter(x=>{
        const t=(x.innerText||'').trim();const r=x.getBoundingClientRect();
        return t===String(day)&&r.y>380&&r.x>340&&x.children.length===0;});
        if(c.length)c[0].click();}""", day)


def _open_date_field(page):
    """Open the 'Once' DATE range picker (placeholder 'Select a date range', or
    a date value once chosen). Scoped between the DATE and TIME labels."""
    page.evaluate(r"""() => {
        const all=[...document.querySelectorAll('div,span')];
        const label=all.find(d=>(d.innerText||'').trim()==='DATE');
        if(!label) return;
        const ly=label.getBoundingClientRect().y;
        const tl=all.find(d=>(d.innerText||'').trim()==='TIME');
        const maxY=tl?tl.getBoundingClientRect().y:ly+80;
        let best=null;
        for(const d of all){
            const t=(d.innerText||'').trim();
            const r=d.getBoundingClientRect();
            if(r.y>ly && r.y<maxY && d.children.length===0 &&
               (t==='Select a date range' || /\b20\d\d\b/.test(t))){
                if(!best || r.y<best.y){best={el:d, y:r.y};}
            }
        }
        if(best) best.el.click();
    }""")


def _pick_dropdown(page, placeholder: str, value: str):
    """Open a custom dropdown by its placeholder/current text and click `value`."""
    page.get_by_text(placeholder, exact=False).first.click(timeout=8000)
    page.wait_for_timeout(900)
    page.get_by_text(value, exact=True).first.click(timeout=8000)
    page.wait_for_timeout(700)


def _click_bottom_nav(page, label: str):
    """Click an uppercase bottom-bar wizard button (SERVICES/NOTES/REVIEW/BACK).

    After the last ADD SERVICE a success toast / full-screen backdrop lingers and
    intercepts this click, so let the panel settle, then route through _press_text
    (real Playwright click, then a pointer-dispatch fallback that ignores the
    overlay). prefer_last=False: the bottom-nav 'NOTES' is distinct from the
    'Notes' step-indicator by case (exact match)."""
    _wait_panel_closed(page, timeout_ms=4000)
    if not _press_text(page, label, exact=True, prefer_last=False, timeout=8000):
        raise RuntimeError(f"could not click bottom-nav {label!r}")


def _open_activity_time(page):
    """Open the activity TIME dropdown. It shows either the 'Select a time'
    placeholder or a defaulted value (e.g. '7:00 AM'). Crucially we must NOT
    click a time printed in the panel's 'Existing Add-on Services' list, so we
    scope to the control sitting just below the 'TIME' label and above that
    list."""
    page.evaluate(r"""() => {
        const all=[...document.querySelectorAll('div,span')];
        const label=all.find(d=>(d.innerText||'').trim()==='TIME');
        if(!label) return;
        const ly=label.getBoundingClientRect().y;
        const ex=all.find(d=>(d.innerText||'').trim().startsWith('Existing Add-on'));
        const maxY=ex?ex.getBoundingClientRect().y:1e9;
        let best=null;
        for(const d of all){
            const t=(d.innerText||'').trim();
            const r=d.getBoundingClientRect();
            if(r.y>ly && r.y<maxY && d.children.length===0 &&
               (t==='Select a time' || /^\d{1,2}:\d{2}\s?(AM|PM)$/i.test(t))){
                if(!best || r.y<best.y){best={el:d, y:r.y};}
            }
        }
        if(best) best.el.click();
    }""")


def _current_activity_time(page):
    """Read the TIME field's current value (the control below the 'TIME' label,
    above 'Existing Add-on Services'). Returns e.g. '7:30 AM' or None."""
    return page.evaluate(r"""() => {
        const all=[...document.querySelectorAll('div,span')];
        const label=all.find(d=>(d.innerText||'').trim()==='TIME');
        if(!label) return null;
        const ly=label.getBoundingClientRect().y;
        const ex=all.find(d=>(d.innerText||'').trim().startsWith('Existing Add-on'));
        const maxY=ex?ex.getBoundingClientRect().y:1e9;
        let best=null;
        for(const d of all){
            const t=(d.innerText||'').trim();
            const r=d.getBoundingClientRect();
            if(r.y>ly && r.y<maxY && d.children.length===0 &&
               /^\d{1,2}:\d{2}\s?(AM|PM)$/i.test(t)){
                if(!best || r.y<best.y) best={t, y:r.y};
            }
        }
        return best ? best.t : null;
    }""")


def _norm_time(s: str) -> str:
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([AP]M)", (s or "").strip(), re.I)
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}" if m else (s or "").strip()


def _set_activity_time(page, target: str) -> bool:
    """Set the activity TIME to `target`, VERIFYING it actually changed. This is
    load-bearing for the second daily Play Yard: it must differ from the first or
    Gingr dedupes the two same-day sessions into one. Retries because opening the
    dropdown / picking the option flakes on a re-opened panel."""
    want = _norm_time(target)
    for _ in range(4):
        if _norm_time(_current_activity_time(page)) == want:
            return True
        _open_activity_time(page)
        page.wait_for_timeout(900)
        _press_text(page, target)          # click the option (padded, e.g. '03:30 PM')
        page.wait_for_timeout(900)
    return _norm_time(_current_activity_time(page)) == want


def _terms_checked(page) -> bool:
    """True if the 'I agree to the terms and conditions' box is ticked — detected
    by its filled (blue-dominant) background. Unchecked is white/transparent."""
    return bool(page.evaluate(r"""() => {
        const rows=[...document.querySelectorAll('div,span')]
            .filter(x=>/I agree to the terms/i.test(x.innerText||''));
        const row=rows[rows.length-1]; if(!row) return false;
        const ry=row.getBoundingClientRect();
        for(const el of document.querySelectorAll('div')){
            const r=el.getBoundingClientRect();
            if(r.width>12 && r.width<34 && r.height>12 && r.height<34 &&
               Math.abs(r.y+r.height/2-(ry.y+ry.height/2))<40 && r.x<ry.x+12){
                const m=(getComputedStyle(el).backgroundColor||'')
                    .match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                if(m){const rr=+m[1],gg=+m[2],bb=+m[3];
                      if(bb>120 && bb>rr+40 && bb>gg+40) return true;}
            }
        }
        return false;
    }"""))


def _check_terms(page) -> bool:
    """Tick the required terms checkbox and VERIFY it's ticked. It's an RNW
    pressable that ignores a bare JS .click(), so use a real mouse click at its
    coordinates (proven to toggle it). Retries; returns whether it ended checked."""
    for _ in range(3):
        if _terms_checked(page):
            return True
        box = page.evaluate(r"""() => {
            const rows=[...document.querySelectorAll('div,span')]
                .filter(x=>/I agree to the terms/i.test(x.innerText||''));
            const row=rows[rows.length-1]; if(!row) return null;
            const ry=row.getBoundingClientRect();
            let best=null;
            for(const el of document.querySelectorAll('div')){
                const c=(el.className||'').toString();
                const r=el.getBoundingClientRect();
                if(c.includes('r-1loqt21') && r.width>10 && r.width<45 &&
                   Math.abs(r.y+r.height/2-(ry.y+ry.height/2))<40 && r.x<ry.x+30){
                    if(!best || r.x<best.x) best={x:r.x+r.width/2, y:r.y+r.height/2};
                }
            }
            return best;
        }""")
        if not box:
            break
        try:
            page.mouse.click(box["x"], box["y"])
        except Exception:
            pass
        page.wait_for_timeout(900)
    return _terms_checked(page)


def _wait_panel_closed(page, timeout_ms: int = 9000) -> bool:
    """After an ADD SERVICE the activity panel closes back to the Services list,
    leaving a transient full-screen backdrop that briefly intercepts clicks.
    Poll until the panel (identified by its 'ADD SERVICE' action button) is gone
    so the next row click lands on a clean list. Returns True once closed."""
    waited = 0
    while waited < timeout_ms:
        present = page.evaluate(r"""() =>
            [...document.querySelectorAll('div,span,button,a')]
              .some(e => /^\+?\s*ADD SERVICE$/i.test((e.innerText||'').trim()))""")
        if not present:
            page.wait_for_timeout(600)  # let the backdrop detach
            return True
        page.wait_for_timeout(400)
        waited += 400
    return False


def _wait_panel_open(page, timeout_ms: int = 9000) -> bool:
    """Wait until an activity panel is actually open — identified by its
    'ADD SERVICE' action button — so we don't try to read FREQUENCY/TIME from a
    list that never transitioned. Returns False if it never opens."""
    waited = 0
    while waited < timeout_ms:
        present = page.evaluate(r"""() =>
            [...document.querySelectorAll('div,span,button,a')]
              .some(e => /^\+?\s*ADD SERVICE$/i.test((e.innerText||'').trim()))""")
        if present:
            return True
        page.wait_for_timeout(400)
        waited += 400
    return False


def _open_frequency(page):
    """Open the FREQUENCY dropdown. Every activity panel reliably shows the
    'Select a frequency' placeholder, so click it with a real Playwright event
    (retries through any settling) — a one-shot JS click flaked on the second
    panel. Fall back to a position-based JS click only if the text moved."""
    try:
        page.get_by_text("Select a frequency", exact=False).first.click(timeout=8000)
        return
    except Exception:
        pass
    page.evaluate(r"""() => {
        const all=[...document.querySelectorAll('div,span')];
        const lab=all.find(d=>(d.innerText||'').trim()==='FREQUENCY');
        if(!lab) return;
        const ly=lab.getBoundingClientRect().y;
        const tlab=all.find(d=>(d.innerText||'').trim()==='TIME');
        const maxY=tlab?tlab.getBoundingClientRect().y:1e9;
        let best=null;
        for(const d of all){
            const t=(d.innerText||'').trim();
            const r=d.getBoundingClientRect();
            if(r.y>ly && r.y<maxY && d.children.length===0 && t.length>0){
                if(!best || r.y<best.y) best={el:d,y:r.y};
            }
        }
        if(best) best.el.click();
    }""")


def _press_text(page, text: str, exact: bool = True,
                prefer_last: bool = True, timeout: int = 6000) -> bool:
    """Click a dropdown OPTION / control by text, getting through the full-screen
    backdrop that overlays an OPEN dropdown (it intercepts Playwright clicks on
    the option even though the option is visible). Real Playwright click first;
    if intercepted, dispatch a pointer press directly on the element. `prefer_last`
    picks the option (rendered after the field) over an identically-texted field."""
    loc = page.get_by_text(text, exact=exact)
    target = loc.last if prefer_last else loc.first
    try:
        target.click(timeout=timeout)
        return True
    except Exception:
        pass
    return bool(page.evaluate(r"""(args) => {
        const [txt, exact, preferLast] = args;
        let els=[...document.querySelectorAll('div,span,a,button')].filter(e=>{
            const t=(e.innerText||'').trim();
            return exact ? t===txt : t.includes(txt);
        });
        // keep leaf-most matches so we click the option text, not a wrapper
        els = els.filter(e => !els.some(o => o!==e && e.contains(o)));
        if(!els.length) return false;
        const el = preferLast ? els[els.length-1] : els[0];
        const r=el.getBoundingClientRect();
        const o={bubbles:true, cancelable:true,
                 clientX:r.x+r.width/2, clientY:r.y+r.height/2, pointerId:1};
        el.dispatchEvent(new PointerEvent('pointerdown', o));
        el.dispatchEvent(new MouseEvent('mousedown', o));
        el.dispatchEvent(new PointerEvent('pointerup', o));
        el.dispatchEvent(new MouseEvent('mouseup', o));
        el.dispatchEvent(new MouseEvent('click', o));
        return true;
    }""", [text, exact, prefer_last]))


def _panel_shows(page, row_label: str) -> bool:
    """True if the OPEN activity panel is for `row_label` — i.e. its title (top
    of the right-hand panel) equals the label. Used to detect a mis-targeted
    row click that opened the wrong service."""
    return bool(page.evaluate(r"""(label) => {
        const W=window.innerWidth, H=window.innerHeight;
        return [...document.querySelectorAll('div,span')].some(e => {
            if((e.innerText||'').trim() !== label) return false;
            const r=e.getBoundingClientRect();
            // panel title sits in the upper portion of the right-hand panel
            return r.width>0 && r.x > W*0.45 && r.y < H*0.30;
        });
    }""", row_label))


def _close_panel(page):
    """Dismiss an open activity panel (e.g. when the wrong service opened).
    Try the panel's top-right 'X', then Escape, then click the dimmed backdrop."""
    page.evaluate(r"""() => {
        const W=window.innerWidth;
        let best=null;
        for(const e of document.querySelectorAll('div,span,button,svg')){
            const r=e.getBoundingClientRect();
            if(r.y<80 && r.x>W-80 && r.width>0 && r.width<60 && r.height<60){
                if(!best || r.x>best.x) best={el:e, x:r.x};
            }
        }
        if(best){
            let t=best.el;
            for(let i=0;i<3 && t;i++){
                const role=t.getAttribute && t.getAttribute('role');
                if(t.tagName==='BUTTON' || role==='button') break;
                if(t.parentElement) t=t.parentElement; else break;
            }
            t.click();
        }
    }""")
    page.wait_for_timeout(400)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(600)


def _press_row(page, row_label: str) -> bool:
    """Open a service's row in the Services list. The list is a React-Native-Web
    responder surface: a bare JS .click() does NOT trigger its press handler, so
    scroll the row into view and use a real Playwright click first; only if that
    is intercepted fall back to dispatching a full pointer sequence on the row
    element (preferring the list row, data-testid 'addonsList.*')."""
    # Scroll the row into view so a stale/scrolled layout can't mis-target.
    page.evaluate(r"""(label) => {
        const row=[...document.querySelectorAll('[data-testid^="addonsList."]')]
            .find(e => (e.innerText||'').trim()===label)
          || [...document.querySelectorAll('div,span')]
            .find(e => (e.innerText||'').trim()===label);
        if(row && row.scrollIntoView) row.scrollIntoView({block:'center'});
    }""", row_label)
    page.wait_for_timeout(300)
    try:
        page.get_by_text(row_label, exact=True).first.click(timeout=6000)
        return True
    except Exception:
        pass
    return bool(page.evaluate(r"""(label) => {
        const els=[...document.querySelectorAll('div,span,a,button')];
        let row=els.find(e => (e.getAttribute && /^addonsList\./.test(
                e.getAttribute('data-testid')||'')) &&
                (e.innerText||'').trim()===label);
        if(!row) row=els.find(e => (e.innerText||'').trim()===label);
        if(!row) return false;
        const r=row.getBoundingClientRect();
        const opts={bubbles:true, cancelable:true,
                    clientX:r.x+r.width/2, clientY:r.y+r.height/2, pointerId:1};
        row.dispatchEvent(new PointerEvent('pointerdown', opts));
        row.dispatchEvent(new MouseEvent('mousedown', opts));
        row.dispatchEvent(new PointerEvent('pointerup', opts));
        row.dispatchEvent(new MouseEvent('mouseup', opts));
        row.dispatchEvent(new MouseEvent('click', opts));
        return true;
    }""", row_label))


def _click_service_row(page, row_label: str, attempts: int = 3):
    """Open `row_label`'s panel and VERIFY the right service opened. After the
    bulk adds the list can scroll, so a click may mis-target an adjacent row
    (observed: asking for Play Yard, getting Romp n' Rassle). Verify the panel
    title; if it's wrong or nothing opened, close and retry."""
    for i in range(attempts):
        _press_row(page, row_label)
        if _wait_panel_open(page, timeout_ms=7000) and _panel_shows(page, row_label):
            return True
        # wrong service or no panel — reset and retry
        _close_panel(page)
        _wait_panel_closed(page, timeout_ms=5000)
    raise RuntimeError(f"could not open the correct panel for {row_label!r} "
                       f"after {attempts} attempts")


def _click_add_service(page) -> bool:
    """Click the panel's '+ ADD SERVICE' button.

    Two things make a naive get_by_text('ADD SERVICE', exact=True) flaky:
      • the label renders with a leading '+' icon, so the element's text is
        '+ ADD SERVICE' and an exact match misses it;
      • an open TIME/FREQUENCY dropdown expands over the bottom-right of the
        panel, covering the button, so Playwright's not-obscured actionability
        check fails even when the locator resolves.
    Try a couple of forgiving Playwright locators, then fall back to a direct
    JS click (which ignores both the '+' prefix and the overlay)."""
    for loc in (
        page.locator("button:has-text('ADD SERVICE')"),
        page.get_by_text(re.compile(r"^\+?\s*ADD SERVICE\s*$", re.I)),
    ):
        try:
            loc.first.click(timeout=4000)
            return True
        except Exception:
            continue
    # JS fallback: pick the lowest on-page element whose text is (optionally a
    # '+' then) 'ADD SERVICE' — that's the panel action button, not stray text —
    # and click its nearest button-like ancestor.
    return bool(page.evaluate(r"""() => {
        const els=[...document.querySelectorAll('button,div,span,a')];
        let best=null;
        for(const e of els){
            const t=(e.innerText||'').trim();
            if(/^\+?\s*ADD SERVICE$/i.test(t)){
                const r=e.getBoundingClientRect();
                if(r.width>0 && r.height>0 && (!best || r.y>best.y)) best={el:e,y:r.y};
            }
        }
        if(!best) return false;
        let t=best.el;
        for(let i=0;i<3 && t;i++){
            const role=t.getAttribute && t.getAttribute('role');
            if(t.tagName==='BUTTON' || role==='button') break;
            if(t.parentElement) t=t.parentElement; else break;
        }
        t.click();
        return true;
    }"""))


def _add_activity(page, row_label: str, frequency: str, time_slot: str):
    _tag = (row_label.split("|")[-1].strip() + "_" + frequency).replace(" ", "").replace("&", "and")[:40]
    _click_service_row(page, row_label)
    if not _wait_panel_open(page):
        raise RuntimeError(f"service panel did not open for {row_label}")
    page.wait_for_timeout(800)
    _dbg(page, f"add_{_tag}_1panel", f"panel open for {row_label} / {frequency}")
    # FREQUENCY — open by position (placeholder OR pre-filled value), then pick
    # the option. Options render below the field, so .last is the option even
    # when the field already shows the same text.
    _open_frequency(page)
    page.wait_for_timeout(800)
    if not _press_text(page, frequency):
        raise RuntimeError(f"could not select frequency {frequency!r} for {row_label}")
    page.wait_for_timeout(800)
    _dbg(page, f"add_{_tag}_2freq", f"after selecting {frequency}")
    # TIME — must actually land on the target (a wrong/duplicate time makes Gingr
    # dedupe a second same-activity add). Verify-and-retry rather than one-shot.
    if not _set_activity_time(page, time_slot):
        raise RuntimeError(
            f"could not set time {time_slot!r} for {row_label} "
            f"(stuck on {_current_activity_time(page)!r})")
    _dbg(page, f"add_{_tag}_3time", f"time now {_current_activity_time(page)!r}")
    if not _click_add_service(page):
        raise RuntimeError(f"could not click ADD SERVICE for {row_label}")
    _wait_panel_closed(page)
    _dbg(page, f"add_{_tag}_4done", "after ADD SERVICE")


def _navigate_to_wizard(page):
    """home -> pet BOOK -> Other -> Boarding | Dog. Returns False if it stalls."""
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4500)
    if "public/login" in page.url:
        return False
    # Single-pet (Pallo) flow: the left-nav "Book" opens the New Booking Request
    # for Pallo directly; then pick the service category and the dog service.
    page.get_by_text("Book", exact=False).first.click(timeout=10000)
    page.wait_for_timeout(3500)
    page.get_by_text("Other", exact=False).first.click(timeout=10000)
    page.wait_for_timeout(3500)
    page.get_by_text("Boarding | Dog", exact=False).first.click(timeout=10000)
    page.wait_for_timeout(4500)
    return "reservation-request" in page.url


def _addon_rule_count(page, activity_label: str) -> int:
    """Count how many add-on RULES are attached to an activity on the Services
    list — the '+N' badge next to the row (e.g. Play Yard shows '+2' when both
    the 07:30 AM and 03:30 PM frequency rules are attached). This is the ground
    truth for the slate; the Review estimate instead collapses to unique days.
    Returns 0 if the row isn't badged."""
    return int(page.evaluate(r"""(label) => {
        const rows=[...document.querySelectorAll('div,span')].filter(
            e => (e.innerText||'').trim()===label);
        let best=0;
        for(const row of rows){
            const rr=row.getBoundingClientRect();
            // scan elements on the same row line for a '+N' badge
            for(const e of document.querySelectorAll('div,span')){
                const t=(e.innerText||'').trim();
                const m=t.match(/^\+\s*(\d+)$/);
                if(!m) continue;
                const r=e.getBoundingClientRect();
                if(Math.abs(r.y-rr.y)<25 && r.x>rr.x){
                    best=Math.max(best, parseInt(m[1],10));
                }
            }
        }
        return best;
    }""", activity_label))


def _review_summary(page) -> dict:
    text = page.inner_text("body")
    total = None
    m = re.search(r"ESTIMATED TOTAL:\s*\$?\s?([\d,]+\.\d{2})", text, re.I)
    if m:
        total = f"${m.group(1)}"
    services = None
    m = re.search(r"Addon Services\s*\((\d+)\)", text, re.I)
    if m:
        services = int(m.group(1))
    # per-activity scheduled lines, e.g. "ACTIVITY | PLAY YARD (4)"
    activity_lines = re.findall(r"ACTIVITY \| [A-Z ']+\(\d+\)", text)
    # the real submit button — Gingr labels it "SUBMIT REQUEST"
    submit = page.evaluate(r"""() => {
        for (const el of document.querySelectorAll('div,button')) {
            const t=(el.innerText||'').trim();
            if (/^SUBMIT REQUEST$/i.test(t)) return t;
        }
        return null;
    }""")
    terms_present = "terms and conditions" in text.lower()
    return {
        "estimated_total": total,
        "addon_services_count": services,
        "activity_lines": activity_lines,
        "submit_button_label": submit,
        "terms_checkbox_present": terms_present,
    }


# ── overlap guard ────────────────────────────────────────────────────────────

def _overlap_check(drop_date: date, pick_date: date, anchor: date) -> list:
    """Return active (non-canceled) stays that overlap [drop_date, pick_date]."""
    overlaps = []
    with sync_playwright() as p:
        browser, ctx = gingr_lib.new_logged_in_context(p)
        page = ctx.new_page()
        try:
            cards = gingr_lib.fetch_booking_cards(page)
        finally:
            browser.close()
    for text in cards:
        s = gingr_lib.parse_card(text, anchor)
        if not s or s.get("status") == "Canceled":
            continue
        a = date.fromisoformat(s["start_date"])
        b = date.fromisoformat(s["end_date"])
        if a <= pick_date and b >= drop_date:
            overlaps.append(s)
    return overlaps


# ── main ─────────────────────────────────────────────────────────────────────

def _send_gina_messages(messages: list, trip_name: str | None) -> tuple[list, list]:
    sent, failed = [], []
    for msg in messages:
        topic = msg.get("topic") or f"Pallo {msg.get('handoff', 'handoff')}"
        body = msg.get("body", "")
        cmd = ["python3", str(SCRIPT_DIR / "gina-notify.py"),
               "--topic", topic, "--body", body]
        if trip_name:
            cmd += ["--trip-name", trip_name]
        if msg.get("date"):
            cmd += ["--handoff-date", msg["date"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            data = json.loads(r.stdout) if r.stdout.strip() else {"status": "no_output"}
        except Exception as e:  # noqa: BLE001
            data = {"status": "error", "reason": str(e)}
        (sent if data.get("status") == "sent" else failed).append(
            {"topic": topic, "result": data.get("status"), "id": data.get("ledger_entry", {}).get("id")})
    return sent, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=None, help="plan_json blob from pallo-trip-plan.py")
    ap.add_argument("--drop-date", default=None, help="ISO drop-off date (with --pickup-date)")
    ap.add_argument("--pickup-date", default=None, help="ISO pickup date (with --drop-date)")
    ap.add_argument("--confirm-drop-date", required=True, help="ISO; must equal the plan drop-off")
    ap.add_argument("--confirm-pickup-date", required=True, help="ISO; must equal the plan pickup")
    ap.add_argument("--drop-time", default=None,
                    help="Drop-off clock time, e.g. '3pm' or '03:00 PM'. Overrides the "
                         "plan's time; defaults to the plan's, then 08:00 AM.")
    ap.add_argument("--pickup-time", default=None,
                    help="Pickup clock time, e.g. '11am' or '11:00 AM'. Overrides the "
                         "plan's time; defaults to the plan's, then 09:00 AM.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-overlap", action="store_true")
    ap.add_argument("--no-gina", action="store_true")
    ap.add_argument("--no-calendar", action="store_true",
                    help="do not email drop-off/pickup Google-Calendar invites after booking")
    ap.add_argument("--simple-slate", action="store_true",
                    help="One Play Yard + one Nature Walk per day (skip the per-day "
                         "second Play Yard). Faster; use for long stays.")
    args = ap.parse_args()

    if not gingr_lib.state_exists():
        return out({"status": "not_logged_in",
                    "reason": "No saved Gingr session. Run gingr-login.py first."}, 2)

    try:
        drop_date, pick_date, plan_drop_time, plan_pickup_time, gina_messages, trip_name = resolve_dates(args)
    except ValueError as e:
        return out({"status": "error", "reason": str(e)}, 2)

    # Time precedence: explicit CLI flag > plan-carried time > hard default.
    try:
        drop_time = triplib.normalize_clock_time(
            args.drop_time or plan_drop_time or "08:00 AM")
        pickup_time = triplib.normalize_clock_time(
            args.pickup_time or plan_pickup_time or "09:00 AM")
    except triplib.TimeFormatError as e:
        return out({"status": "error", "reason": str(e)}, 2)

    # --confirm-* invariants (the square-appointments safety pattern)
    if (args.confirm_drop_date != drop_date.isoformat()
            or args.confirm_pickup_date != pick_date.isoformat()):
        return out({"status": "confirm_mismatch",
                    "reason": "confirm dates do not match the plan/requested dates",
                    "plan_drop_off": drop_date.isoformat(),
                    "plan_pick_up": pick_date.isoformat(),
                    "confirm_drop_date": args.confirm_drop_date,
                    "confirm_pickup_date": args.confirm_pickup_date}, 1)
    if pick_date <= drop_date:
        return out({"status": "error", "reason": "pickup must be after drop-off"}, 2)

    anchor = datetime.now().astimezone().date()
    counts = expected_activity_counts(drop_date, pick_date, args.simple_slate)

    # idempotency guard
    if not args.allow_overlap:
        try:
            overlaps = _overlap_check(drop_date, pick_date, anchor)
        except gingr_lib.SessionExpired:
            return out({"status": "session_expired",
                        "reason": "Saved Gingr session expired. Re-run gingr-login.py."}, 1)
        except Exception as e:  # noqa: BLE001
            return out({"status": "error", "reason": f"overlap check failed: {type(e).__name__}: {e}"}, 1)
        if overlaps:
            return out({"status": "conflict",
                        "reason": "Pallo already has a non-canceled reservation overlapping these dates.",
                        "overlapping_stays": overlaps,
                        "hint": "Cancel/modify the existing stay, or pass --allow-overlap to book anyway."}, 1)

    # drive the wizard
    try:
        with sync_playwright() as p:
            browser, ctx = gingr_lib.new_logged_in_context(p)
            page = ctx.new_page()
            try:
                if not _navigate_to_wizard(page):
                    if "public/login" in page.url:
                        return out({"status": "session_expired",
                                    "reason": "Saved Gingr session expired. Re-run gingr-login.py."}, 1)
                    return out({"status": "error", "reason": "could not reach the booking wizard"}, 1)

                # Dates step
                if not _goto_month(page, drop_date):
                    return out({"status": "error", "reason": f"could not navigate calendar to {drop_date}"}, 1)
                _click_day(page, drop_date.day)
                page.wait_for_timeout(700)
                if pick_date.month != drop_date.month or pick_date.year != drop_date.year:
                    _goto_month(page, pick_date)
                _click_day(page, pick_date.day)
                page.wait_for_timeout(900)
                _pick_dropdown(page, "Select a drop off time", drop_time)
                _pick_dropdown(page, "Select a pick up time", pickup_time)
                _click_bottom_nav(page, "SERVICES")
                page.wait_for_timeout(4000)

                # Services step — all activities via bulk frequency ops. The second
                # daily Play Yard is just another frequency rule at a DIFFERENT time
                # (full days only), exactly like adding it by hand — no per-day
                # date-picker adds, which is what used to fail.
                for row, freq, slot in BULK_OPS:
                    _add_activity(page, row, freq, slot)
                if not args.simple_slate:
                    _add_activity(page, *SECOND_PLAY_YARD_OP)

                # Ground-truth check on the Services list: count the '+N' rule
                # badges. Play Yard should carry 2 rules (07:30 + 03:30) unless
                # --simple-slate; Nature Walk 1. The Review estimate collapses to
                # unique days, so it can't confirm the second Play Yard.
                py_rules = _addon_rule_count(page, "Activity | Play Yard")
                nw_rules = _addon_rule_count(page, "Activity | Nature Walk")
                expected_py_rules = 1 if args.simple_slate else 2

                # advance to Review
                _click_bottom_nav(page, "NOTES")
                page.wait_for_timeout(2500)
                _click_bottom_nav(page, "REVIEW")
                page.wait_for_timeout(4000)

                summary = _review_summary(page)

                # Verify the portal actually accepted the requested slate. Gingr
                # collapses same-day same-activity sessions, so a per-day SECOND
                # Play Yard does not register as an extra session — surface that
                # rather than silently reporting success.
                def _count_from_lines(lines, name):
                    for ln in lines or []:
                        m = re.search(rf"{name}\s*\((\d+)\)", ln, re.I)
                        if m:
                            return int(m.group(1))
                    return None
                booked_py = _count_from_lines(summary.get("activity_lines"), "PLAY YARD")
                booked_nw = _count_from_lines(summary.get("activity_lines"), "NATURE WALK")

                base = {
                    "dog": "Pallo",
                    "facility": "Laurel Acres Kennels - Hillsboro",
                    "drop_off": f"{drop_date.isoformat()} {drop_time}",
                    "pick_up": f"{pick_date.isoformat()} {pickup_time}",
                    "nights": counts["nights"],
                    "expected_activities": {
                        "play_yard_total": counts["play_yard"],
                        "nature_walk_total": counts["nature_walk"],
                        "per_full_day": ("1x Play Yard + 1x Nature Walk"
                                         if args.simple_slate
                                         else "2x Play Yard + 1x Nature Walk"),
                    },
                    # Rule badges are the real slate signal; estimate_days is the
                    # Review's unique-day count (informational, not sessions).
                    "activity_rules": {"play_yard": py_rules, "nature_walk": nw_rules},
                    "estimate_days": {"play_yard": booked_py, "nature_walk": booked_nw},
                    "review": summary,
                }
                warns = []
                if py_rules < expected_py_rules:
                    warns.append(
                        f"expected {expected_py_rules} Play Yard frequency rule(s) but only "
                        f"{py_rules} attached — the second daily Play Yard (03:30 PM) did not "
                        f"register. Re-run --dry-run to inspect, or use --simple-slate.")
                if nw_rules < 1:
                    warns.append("the Nature Walk activity did not attach.")
                if warns:
                    base["slate_warning"] = " ".join(warns)

                if args.dry_run:
                    artifacts = Path.home() / "pallo-boarding" / "artifacts"
                    artifacts.mkdir(parents=True, exist_ok=True)
                    shot = artifacts / "book_review_dryrun.png"
                    page.screenshot(path=str(shot), full_page=True)
                    base["status"] = "dry_run_ok"
                    base["review_screenshot"] = str(shot)
                    base["note"] = "Filled wizard through Review; did NOT submit."
                    return out(base)

                # REAL submit — Gingr's button is "SUBMIT REQUEST", gated behind
                # the required terms-and-conditions checkbox.
                label = summary.get("submit_button_label")
                if label != "SUBMIT REQUEST":
                    return out({**base, "status": "error",
                                "reason": f"could not find the 'SUBMIT REQUEST' button (saw {label!r}); "
                                          "refusing to submit. Re-run with --dry-run and inspect the review screenshot."}, 1)
                artifacts = Path.home() / "pallo-boarding" / "artifacts"
                artifacts.mkdir(parents=True, exist_ok=True)
                # The terms checkbox gates SUBMIT REQUEST. It MUST end up ticked or
                # the button is inert and the submit silently no-ops.
                if not _check_terms(page):
                    page.screenshot(path=str(artifacts / "book_failure.png"), full_page=True)
                    return out({**base, "status": "error",
                                "reason": "could not tick the terms-and-conditions checkbox; "
                                          "did NOT submit. Nothing was booked.",
                                "screenshot": str(artifacts / "book_failure.png")}, 1)
                page.wait_for_timeout(600)
                # Real submit. SUBMIT REQUEST is an RNW pressable; a real mouse
                # click at its coordinates is what reliably fires it (same as the
                # terms checkbox). Fall back to _press_text.
                sbox = page.evaluate(r"""() => {
                    const el=[...document.querySelectorAll('div,span,button')]
                        .find(e => /^SUBMIT REQUEST$/i.test((e.innerText||'').trim()));
                    if(!el) return null;
                    const r=el.getBoundingClientRect();
                    return {x:r.x+r.width/2, y:r.y+r.height/2};
                }""")
                clicked = False
                if sbox:
                    try:
                        page.mouse.click(sbox["x"], sbox["y"])
                        clicked = True
                    except Exception:
                        pass
                if not clicked and not _press_text(page, "SUBMIT REQUEST", exact=True, prefer_last=False, timeout=10000):
                    raise RuntimeError("could not click SUBMIT REQUEST")
                page.wait_for_timeout(6000)
                page.screenshot(path=str(artifacts / "book_confirmation.png"), full_page=True)
                # VERIFY the submit actually went through. A successful request
                # leaves the Review step; if the 'SUBMIT REQUEST' button is still
                # present we never submitted — do NOT falsely report a booking.
                still_on_review = bool(page.evaluate(
                    r"""() => [...document.querySelectorAll('div,span,button')]
                        .some(e => /^SUBMIT REQUEST$/i.test((e.innerText||'').trim()))"""))
                if still_on_review:
                    return out({**base, "status": "submit_failed",
                                "reason": "clicked SUBMIT REQUEST but the page stayed on the "
                                          "Review step — the request did NOT go through and "
                                          "nothing was booked. Re-run to retry.",
                                "screenshot": str(artifacts / "book_confirmation.png")}, 1)
                confirm_text = page.inner_text("body")
                conf = re.search(r"(Reservation|Confirmation)[^\n]{0,40}?(#?\s?[A-Z0-9]{4,})", confirm_text)
                base["status"] = "booked"
                base["manage_url"] = page.url
                base["confirmation_hint"] = conf.group(0).strip() if conf else None
                base["confirmation_screenshot"] = str(artifacts / "book_confirmation.png")
            except Exception:
                # Capture the page state at the moment of failure for diagnosis.
                try:
                    dbg = Path.home() / "pallo-boarding" / "artifacts"
                    dbg.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(dbg / "book_failure.png"), full_page=True)
                except Exception:
                    pass
                raise
            finally:
                ctx.storage_state(path=str(gingr_lib.STATE_FILE))
                browser.close()
    except gingr_lib.SessionExpired:
        return out({"status": "session_expired",
                    "reason": "Saved Gingr session expired. Re-run gingr-login.py."}, 1)
    except Exception as e:  # noqa: BLE001
        return out({"status": "error", "reason": f"{type(e).__name__}: {e}"}, 1)

    # Gina notifications (after a real booking only)
    if not args.no_gina and gina_messages:
        sent, failed = _send_gina_messages(gina_messages, trip_name)
        base["gina_messages_sent"] = sent
        base["gina_messages_failed"] = failed
        if failed:
            base["status"] = "booked_with_notification_warnings"

    # Google-Calendar handoff invites (after a real booking only). Non-fatal:
    # a send failure never undoes the booking, just surfaces a warning.
    if not args.no_calendar and base.get("status", "").startswith("booked"):
        cmd = ["python3", str(SCRIPT_DIR / "pallo-calendar-invite.py"),
               "--events", "pickup,dropoff",
               "--drop-date", drop_date.isoformat(), "--drop-time", drop_time,
               "--pickup-date", pick_date.isoformat(), "--pickup-time", pickup_time]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            data = json.loads(r.stdout) if r.stdout.strip() else {"status": "no_output"}
        except Exception as e:  # noqa: BLE001
            data = {"status": "error", "reason": str(e)}
        base["calendar_invites"] = data.get("status")
        if data.get("status") not in ("ok",):
            base["calendar_invites_detail"] = data
    return out(base)


if __name__ == "__main__":
    sys.exit(main())
