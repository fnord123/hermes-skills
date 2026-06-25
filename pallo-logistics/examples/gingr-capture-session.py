#!/usr/bin/env python3
"""gingr-capture-session.py — capture a Gingr portal session by logging in BY HAND.

Use this when `gingr-login.py` returns `login_failed`. The portal's React-Native-Web
login form blocks headless/automated submission, so we open a REAL visible browser,
you log in yourself (handling any captcha / 2FA), and this writes the full Playwright
storage_state — cookies AND localStorage (the SPA's `apiKey` / `user.token` live in
localStorage, so a plain cookie copy is NOT enough) — to:
    ~/.config/pallo-logistics/gingr-storage-state.json   (perms 600)

The read/book/cancel scripts reuse that state, so they never need credentials.

Requires a DISPLAY (it opens a window). Run it either:
  • on a machine with a desktop, or
  • over SSH X-forwarding (`ssh -X`), or
  • on your laptop, then copy the JSON to the path above on the Hermes host.

Usage:
  python3 gingr-capture-session.py            # opens a window; log in, then press Enter
  python3 gingr-capture-session.py --headless # debugging only; will NOT solve the block
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

LOGIN_URL = "https://tailwaginn.portal.gingrapp.com/public/login"
HOME_URL = "https://tailwaginn.portal.gingrapp.com/secure/home"

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

from playwright.sync_api import sync_playwright  # noqa: E402


def _logged_in(page) -> bool:
    """True once the SPA is authenticated: off the public login page AND the
    localStorage auth token is present (what the booking scripts actually need)."""
    if "public/login" in page.url:
        return False
    try:
        return bool(page.evaluate("() => window.localStorage.getItem('user.token')"))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", action="store_true",
                    help="Run headless (debugging only; the portal blocks headless login).")
    args = ap.parse_args()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=args.headless, args=["--no-sandbox"])
        except Exception as e:
            print(json.dumps({
                "status": "error",
                "reason": f"could not launch a browser: {e}. A visible browser needs a "
                          f"display — run with `ssh -X`, on a desktop, or capture on your "
                          f"laptop and copy the JSON to {STATE_FILE}.",
            }, indent=2))
            return 2

        ctx = browser.new_context(
            viewport={"width": 1280, "height": 1000},
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            browser.close()
            print(json.dumps({"status": "error", "reason": f"goto login failed: {e}"}, indent=2))
            return 1

        print("\n" + "=" * 70, file=sys.stderr)
        print("A browser window is open on the Laurel Acres / Tail Wag Inn login.", file=sys.stderr)
        print("Log in by hand. When you reach your account home, return here and", file=sys.stderr)
        print("press Enter to save the session.", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        # Give the SPA a moment, then verify we're actually authenticated.
        page.wait_for_timeout(1500)
        if not _logged_in(page):
            try:
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
            except Exception:
                pass

        if not _logged_in(page):
            browser.close()
            print(json.dumps({
                "status": "not_logged_in",
                "reason": "Still not authenticated (no user.token in localStorage / on the "
                          "login page). Finish logging in fully, then re-run.",
                "final_url": page.url,
            }, indent=2))
            return 1

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        os.chmod(STATE_FILE, 0o600)
        # quick integrity check on what we saved
        data = json.loads(STATE_FILE.read_text())
        cookie_names = sorted({c.get("name") for c in data.get("cookies", [])})
        ls_keys = []
        for o in data.get("origins", []):
            if "gingrapp.com" in o.get("origin", ""):
                ls_keys = [i.get("name") for i in o.get("localStorage", [])]
        browser.close()
        print(json.dumps({
            "status": "ok",
            "state_file": str(STATE_FILE),
            "final_url": page.url,
            "cookies_saved": cookie_names,
            "has_session_cookie": "gingr_ci_session" in cookie_names,
            "has_user_token": "user.token" in ls_keys,
            "note": "Session saved. The read/book scripts will use it. Verify with "
                    "`python3 pallo-stays.py`.",
        }, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
