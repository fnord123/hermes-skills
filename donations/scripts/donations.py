#!/usr/bin/env python3
"""donations.py — high-level charitable-donation logging on a Google Sheet.

A donation-shaped wrapper over the "Charitable Donations" spreadsheet so the
agent never has to think in cells, ranges, or formulas. Each charity trip is
one tab laid out like:

    |            | Quantity | Value Per | Product   | Total: | =sum(D2:D) |
    | Pants      | 3        | $20.00    | =B2*C2    |        |            |
    | ...        | ...      | ...       | ...       |        |            |

Verbs (each prints ONE JSON object on stdout; exit 1 on error):
  new   --charity Goodwill [--date YYYY-MM-DD]   create today's donation tab
  add   --item pants --quantity 3 --value 5      add an item (merges if it exists)
  more  --item pants [--count 1]                 add more of an existing item
  total                                          the running total (cell F1)
  show                                           list the current tab's items
  use   --tab "<tab title>"                      switch the active donation tab

`add`/`more`/`total`/`show` default to the ACTIVE tab (set by the last `new`
or `use`); pass --tab to target another. Item names are Title-Cased so they
match/merge regardless of dictation casing.

Auth: a Google service account via GOOGLE_APPLICATION_CREDENTIALS (loaded from
~/.hermes/.env if not already in the environment). The spreadsheet id comes
from ~/.config/donations/config.env (DONATIONS_SHEET_ID). The service account
must be shared as Editor on the sheet. No user OAuth.
"""

import os
import sys
from pathlib import Path

# Run under the Hermes venv (has google-api-python-client + google-auth).
_VENV = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
if _VENV.exists() and sys.executable != str(_VENV):
    os.execv(str(_VENV), [str(_VENV), *sys.argv])

import argparse
import datetime
import json

CONFIG_DIR = Path.home() / ".config" / "donations"
CONFIG_ENV = CONFIG_DIR / "config.env"
ACTIVE_FILE = CONFIG_DIR / "active.txt"
HERMES_ENV = Path.home() / ".hermes" / ".env"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column map (0-based): A item, B quantity, C value-per, D product, E label, F total.
HEADER = ["", "Quantity", "Value Per", "Product", "Total:", "=sum(D2:D)"]
CURRENCY = {"type": "CURRENCY", "pattern": "$#,##0.00"}


def out(d, code=0):
    print(json.dumps(d))
    sys.exit(code)


def fail(msg):
    out({"ok": False, "error": str(msg)}, 1)


def _load_env_var(path, key):
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and "=" in line and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _sheet_id():
    sid = os.environ.get("DONATIONS_SHEET_ID") or _load_env_var(CONFIG_ENV, "DONATIONS_SHEET_ID")
    if not sid:
        fail("DONATIONS_SHEET_ID not set — add it to ~/.config/donations/config.env")
    return sid


def _service():
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        gac = _load_env_var(HERMES_ENV, "GOOGLE_APPLICATION_CREDENTIALS")
        if gac:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gac
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        fail("GOOGLE_APPLICATION_CREDENTIALS is not set (expected in ~/.hermes/.env).")
    try:
        import google.auth
        from googleapiclient.discovery import build
        creds, _ = google.auth.default(scopes=SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:  # noqa: BLE001
        fail(f"auth/build failed: {e}")


# ── tab helpers ──────────────────────────────────────────────────────────────

def _tab_meta(svc, sid):
    """Return {title: sheetId} for every tab."""
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


def _q(title):
    return f"'{title}'" if (" " in title or "'" in title) else title


def _money(s):
    """Parse a currency-ish string ('$5.00', '5') to a float, or None."""
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _active_tab():
    if ACTIVE_FILE.exists():
        t = ACTIVE_FILE.read_text().strip()
        if t:
            return t
    return None


def _set_active(title):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(title)


def _resolve_tab(args, svc, sid):
    title = getattr(args, "donation", None) or _active_tab()
    if not title:
        fail("no active donation — start one with `new --charity <name>` (or pass --donation).")
    tabs = _tab_meta(svc, sid)
    if title not in tabs:
        fail(f"donation {title!r} not found. Existing donations: {sorted(tabs)}")
    return title


def _read_items(svc, sid, title):
    """Return [(row_number, item, quantity_str, value_str)] for the tab's item rows."""
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{_q(title)}!A2:D").execute()
    rows = r.get("values", [])
    items = []
    for i, row in enumerate(rows):
        row = (row + ["", "", "", ""])[:4]
        if (row[0] or "").strip():
            items.append((i + 2, row[0].strip(), row[1], row[2]))
    return items


def _read_total(svc, sid, title):
    r = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{_q(title)}!F1").execute()
    vals = r.get("values", [])
    return vals[0][0] if vals and vals[0] else None


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_new(args):
    svc = _service()
    sid = _sheet_id()
    charity = " ".join(w.capitalize() for w in args.charity.split())
    day = args.date or datetime.date.today().isoformat()
    title = f"{day} {charity} Donation"
    tabs = _tab_meta(svc, sid)
    if title in tabs:
        _set_active(title)
        out({"ok": True, "donation": title, "created": False,
             "note": "that donation already exists; it is now the active donation."})
    try:
        resp = svc.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]}).execute()
        new_sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        # header row + running-total formula
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{_q(title)}!A1:F1",
            valueInputOption="USER_ENTERED", body={"values": [HEADER]}).execute()
        # currency format for Value Per (C), Product (D), and the total (F1)
        fmt = {"userEnteredFormat": {"numberFormat": CURRENCY}}
        svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
            {"repeatCell": {"range": {"sheetId": new_sid, "startColumnIndex": 2,
                            "endColumnIndex": 4}, "cell": fmt,
             "fields": "userEnteredFormat.numberFormat"}},
            {"repeatCell": {"range": {"sheetId": new_sid, "startRowIndex": 0,
                            "endRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 6},
             "cell": fmt, "fields": "userEnteredFormat.numberFormat"}},
        ]}).execute()
        _set_active(title)
        out({"ok": True, "donation": title, "created": True, "total": "$0.00"})
    except Exception as e:  # noqa: BLE001
        fail(e)


def _find_item(items, name):
    key = name.strip().lower()
    for row, item, qty, val in items:
        if item.lower() == key:
            return row, item, qty, val
    return None


def cmd_add(args):
    svc = _service()
    sid = _sheet_id()
    title = _resolve_tab(args, svc, sid)
    item = " ".join(w.capitalize() for w in args.item.split())
    try:
        items = _read_items(svc, sid, title)
        existing = _find_item(items, item)
        warning = None
        if existing:  # merge — bump quantity, keep the existing value
            row, _, qty, existing_val = existing
            new_qty = int(float(qty or 0)) + int(args.quantity)
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"{_q(title)}!B{row}",
                valueInputOption="USER_ENTERED", body={"values": [[new_qty]]}).execute()
            action, at_row, new_val = "merged", row, existing_val
            prev = _money(existing_val)
            if prev is not None and abs(prev - float(args.value)) > 0.005:
                warning = (f"{item} already on this donation at ${prev:.2f} each; "
                           f"kept that value and merged the quantity. You said "
                           f"${float(args.value):.2f} — the row's value was NOT changed.")
        else:  # new row, with the =B*C product formula
            # Append after the LAST occupied row, not after the count of
            # occupied rows: one blanked-out entry would otherwise make the
            # next add land on top of a row that still holds an item.
            row = max((r for r, *_ in items), default=1) + 1
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"{_q(title)}!A{row}:D{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[item, int(args.quantity), float(args.value),
                                  f"=B{row}*C{row}"]]}).execute()
            action, at_row, new_qty = "added", row, int(args.quantity)
            new_val = args.value
        result = {"ok": True, "donation": title, "action": action, "item": item,
                  "quantity": new_qty, "value_per": new_val,
                  "total": _read_total(svc, sid, title)}
        if warning:
            result["warning"] = warning
        out(result)
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_more(args):
    svc = _service()
    sid = _sheet_id()
    title = _resolve_tab(args, svc, sid)
    item = " ".join(w.capitalize() for w in args.item.split())
    try:
        existing = _find_item(_read_items(svc, sid, title), item)
        if not existing:
            fail(f"{item!r} isn't on the {title!r} donation yet; use `add` with a value to add it first.")
        row, _, qty, _val = existing
        new_qty = int(float(qty or 0)) + int(args.count)
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{_q(title)}!B{row}",
            valueInputOption="USER_ENTERED", body={"values": [[new_qty]]}).execute()
        out({"ok": True, "donation": title, "action": "incremented", "item": item,
             "quantity": new_qty, "total": _read_total(svc, sid, title)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_total(args):
    svc = _service()
    sid = _sheet_id()
    title = _resolve_tab(args, svc, sid)
    try:
        out({"ok": True, "donation": title, "total": _read_total(svc, sid, title)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_show(args):
    svc = _service()
    sid = _sheet_id()
    title = _resolve_tab(args, svc, sid)
    try:
        items = [{"item": it, "quantity": q, "value_per": v}
                 for _r, it, q, v in _read_items(svc, sid, title)]
        out({"ok": True, "donation": title, "items": items,
             "total": _read_total(svc, sid, title)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_use(args):
    svc = _service()
    sid = _sheet_id()
    tabs = _tab_meta(svc, sid)
    if args.donation not in tabs:
        fail(f"donation {args.donation!r} not found. Existing: {sorted(tabs)}")
    _set_active(args.donation)
    out({"ok": True, "active_donation": args.donation})


def main():
    p = argparse.ArgumentParser(prog="donations", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("new", help="create today's donation tab")
    g.add_argument("--charity", required=True)
    g.add_argument("--date", help="YYYY-MM-DD (default: today)")
    g.set_defaults(func=cmd_new)

    g = sub.add_parser("add", help="add an item (merges into an existing item row)")
    g.add_argument("--item", required=True)
    g.add_argument("--quantity", required=True, type=int)
    g.add_argument("--value", required=True, type=float, help="value per item")
    g.add_argument("--donation")
    g.set_defaults(func=cmd_add)

    g = sub.add_parser("more", help="add more of an existing item")
    g.add_argument("--item", required=True)
    g.add_argument("--count", type=int, default=1)
    g.add_argument("--donation")
    g.set_defaults(func=cmd_more)

    g = sub.add_parser("total", help="the running total (cell F1)")
    g.add_argument("--donation")
    g.set_defaults(func=cmd_total)

    g = sub.add_parser("show", help="list the current tab's items")
    g.add_argument("--donation")
    g.set_defaults(func=cmd_show)

    g = sub.add_parser("use", help="switch the active donation tab")
    g.add_argument("--donation", required=True)
    g.set_defaults(func=cmd_use)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
