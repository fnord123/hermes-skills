#!/usr/bin/env python3
"""gingr-login.py — capture (or refresh) a saved Gingr portal session.

One-time setup, re-runnable whenever the saved session expires. Reads the
portal credentials from ~/.config/pallo-logistics/secrets.env, logs into the
Laurel Acres / Tail Wag Inn customer portal headlessly, and writes a Playwright
storage_state to ~/.config/pallo-logistics/gingr-storage-state.json (perms 600).
The read/book/cancel scripts reuse that state so they never need credentials.

Usage:
  python3 gingr-login.py [--show-head]   # --show-head dumps post-login page text

Output: JSON status. Never prints the password.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / ".config" / "pallo-logistics"
SECRETS = CONFIG_DIR / "secrets.env"
STATE_FILE = CONFIG_DIR / "gingr-storage-state.json"

LOGIN_URL = "https://tailwaginn.portal.gingrapp.com/public/login"

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

from playwright.sync_api import sync_playwright  # noqa: E402


def _load_env(path: Path) -> dict[str, str]:
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


def login(login_id: str, password: str, show_head: bool) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1100},
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            browser.close()
            return {"status": "error", "reason": f"goto login failed: {e}"}
        page.wait_for_timeout(3500)

        # The fields are React-Native-Web TextInputs (no name/id; match on
        # placeholder). page.fill() sets the DOM value but RNW's controlled state
        # does NOT register it, so enter REAL keystrokes: focus via JS (a Playwright
        # click is intercepted by an overlay div) then keyboard.type.
        def _type_into(placeholder: str, value: str) -> bool:
            focused = page.evaluate(
                """(ph) => { const i=[...document.querySelectorAll('input')]
                    .find(x=>x.placeholder===ph); if(!i) return false; i.focus(); return true; }""",
                placeholder)
            if not focused:
                return False
            page.keyboard.type(value, delay=25)
            return True

        try:
            if not _type_into("Enter email or mobile phone", login_id) or \
               not _type_into("Enter your password", password):
                browser.close()
                return {"status": "error", "reason": "could not find the login fields"}
        except Exception as e:
            browser.close()
            return {"status": "error", "reason": f"could not type into login fields: {e}"}

        # Submit. The LOGIN control is a DIV (RNW pressable), not a <button> or a
        # real <form>, so Enter and button:has-text selectors don't submit. Press
        # Enter, then a real mouse click at the LOGIN button's coordinates.
        submitted_via = None
        try:
            page.keyboard.press("Enter")
            submitted_via = "enter"
            page.wait_for_timeout(2500)
        except Exception:
            pass

        if "public/login" in page.url:
            box = page.evaluate(r"""() => {
                const c=[...document.querySelectorAll('div,span,a')]
                    .filter(e => /^log\s*in$/i.test((e.innerText||'').trim()));
                if(!c.length) return null;
                c.sort((a,b)=>b.getBoundingClientRect().width-a.getBoundingClientRect().width);
                const r=c[0].getBoundingClientRect();
                return {x:r.x+r.width/2, y:r.y+r.height/2};
            }""")
            if box:
                try:
                    page.mouse.click(box["x"], box["y"])
                    submitted_via = "click-login"
                    page.wait_for_timeout(4500)
                except Exception:
                    pass

        page.wait_for_timeout(2500)
        final_url = page.url
        try:
            body = page.locator("body").inner_text()
        except Exception:
            body = ""

        # Failure signals: still on the login page, or an explicit error.
        login_failed = (
            "public/login" in final_url
            or "incorrect" in body.lower()
            or "invalid" in body.lower()
        )
        if login_failed:
            browser.close()
            return {
                "status": "login_failed",
                "submitted_via": submitted_via,
                "final_url": final_url,
                "hint": "Headless login to this Gingr portal does not complete: the "
                        "LOGIN press fires no auth request (the form is a React-Native-Web "
                        "app that appears to block headless/automated submission). The "
                        "EXISTING saved session usually still works for reads and bookings "
                        "until it truly expires — try the task anyway. To refresh the "
                        "session, a non-headless login may be required; also verify "
                        "GINGR_LOGIN / GINGR_PASSWORD in secrets.env are current.",
                "body_head": body[:600],
            }

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        os.chmod(STATE_FILE, 0o600)
        result = {
            "status": "ok",
            "submitted_via": submitted_via,
            "final_url": final_url,
            "state_file": str(STATE_FILE),
        }
        if show_head:
            result["body_head"] = body[:1200]
        browser.close()
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show-head", action="store_true",
                    help="Include first ~1200 chars of the post-login page text.")
    args = ap.parse_args()

    env = _load_env(SECRETS)
    login_id = env.get("GINGR_LOGIN", "")
    password = env.get("GINGR_PASSWORD", "")
    if not login_id or login_id.startswith("<") or not password or password.startswith("<"):
        print(json.dumps({
            "status": "not_configured",
            "reason": "Set GINGR_LOGIN and GINGR_PASSWORD in ~/.config/pallo-logistics/secrets.env.",
        }, indent=2))
        return 2

    result = login(login_id, password, args.show_head)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
