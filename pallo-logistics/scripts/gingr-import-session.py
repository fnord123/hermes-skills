#!/usr/bin/env python3
"""gingr-import-session.py — rebuild the saved Gingr portal session from a
browser localStorage dump.

Why this exists: headless login (gingr-login.py) does not complete on this
portal, and cookies do NOT authenticate it either. The portal is a
React-Native-Web SPA whose API calls send an AUTHORIZATION header taken from
localStorage("user.token") — so the only way to (re)establish a session on
this box is to copy localStorage out of a real logged-in browser tab.

Export instructions (Chrome, on the LOGGED-IN portal tab
https://tailwaginn.portal.gingrapp.com):
  1. Press F12 -> Console tab
  2. Run:  copy(JSON.stringify(Object.fromEntries(Object.entries(localStorage))))
  3. The JSON is now on the clipboard; paste it into a file and pass it here.

Usage:
  python3 gingr-import-session.py /path/to/localstorage.json [--no-verify]

Writes ~/.config/pallo-logistics/gingr-storage-state.json (perms 600),
preserving any cookies already in the state file, then verifies the session
by loading the bookings page. Never prints token or cookie values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / ".config" / "pallo-logistics"
STATE_FILE = CONFIG_DIR / "gingr-storage-state.json"

PORTAL = "https://tailwaginn.portal.gingrapp.com"
BOOKINGS_URL = f"{PORTAL}/secure/book/bookings-deposits/bookings"

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])


def _load_localstorage(path: Path) -> dict[str, str]:
    raw = path.read_text().strip()
    data = json.loads(raw)
    if isinstance(data, str):  # double-encoded (pasted with extra quoting)
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object of localStorage key/value pairs")
    return {str(k): str(v) for k, v in data.items()}


def _verify() -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            storage_state=str(STATE_FILE),
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1400},
            locale="en-US",
        )
        page = ctx.new_page()
        page.goto(BOOKINGS_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        final_url = page.url
        browser.close()
    if "public/login" in final_url:
        return {"status": "session_invalid", "final_url": final_url,
                "hint": "The imported localStorage did not authenticate. Make "
                        "sure the export came from a tab that is currently "
                        "logged in to " + PORTAL + " (user.token must be "
                        "present and fresh)."}
    return {"status": "ok", "final_url": final_url}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("localstorage_json", type=Path,
                    help="File containing the JSON localStorage dump from the browser.")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the post-import login check.")
    args = ap.parse_args()

    try:
        ls = _load_localstorage(args.localstorage_json)
    except Exception as e:
        print(json.dumps({"status": "error",
                          "reason": f"could not parse localStorage dump: {e}"}, indent=2))
        return 2

    if "user.token" not in ls:
        print(json.dumps({
            "status": "error",
            "reason": "dump has no 'user.token' key — this is the credential the "
                      "portal actually uses. Export localStorage from the "
                      "LOGGED-IN portal tab (see script docstring).",
            "keys_found": sorted(ls.keys()),
        }, indent=2))
        return 2

    # Preserve existing cookies (harmless, and __stripe_* may matter for payments).
    cookies: list[dict] = []
    if STATE_FILE.exists():
        try:
            cookies = json.load(open(STATE_FILE)).get("cookies", [])
        except Exception:
            cookies = []

    state = {
        "cookies": cookies,
        "origins": [{
            "origin": PORTAL,
            "localStorage": [{"name": k, "value": v} for k, v in ls.items()],
        }],
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    os.chmod(STATE_FILE, 0o600)

    result = {"status": "imported", "state_file": str(STATE_FILE),
              "localstorage_keys": sorted(ls.keys()),
              "cookies_preserved": [c.get("name") for c in cookies]}
    if not args.no_verify:
        result["verify"] = _verify()
        if result["verify"]["status"] == "ok":
            result["status"] = "ok"
        else:
            result["status"] = "imported_but_invalid"
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("ok", "imported") else 1


if __name__ == "__main__":
    sys.exit(main())
