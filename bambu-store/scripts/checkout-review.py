#!/usr/bin/env python3
"""checkout-review.py - log in (once, persistent), reach checkout, return total. No payment."""
import os, sys, json, argparse
from pathlib import Path
HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parent / ".venv" / "bin" / "python"
if VENV_PY.exists() and sys.executable != str(VENV_PY):
    os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
sys.path.insert(0, str(HERE))
import bambu_lib as B
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cart-url", required=True)
    ap.add_argument("--shotdir", default="/tmp/bambu-checkout")
    a = ap.parse_args()
    os.makedirs(a.shotdir, exist_ok=True)
    email = B.envv("BAMBU_EMAIL"); key = B.get_agentmail_key()
    out = {"key_loaded": bool(key)}
    with Stealth().use_sync(sync_playwright()) as p:
        ctx = B.launch(p)
        page = ctx.new_page()
        page.goto(a.cart_url, wait_until="domcontentloaded", timeout=60000)
        B.wait_cloudflare(page)
        if B.needs_login(page):
            li = B.login(page, email, key); out["login"] = li
            if not li.get("ok"):
                print(json.dumps(out, indent=2)); ctx.close(); return
        else:
            out["login"] = {"ok": True, "stage": "already_logged_in"}
        try: page.wait_for_url("**/checkouts/**", timeout=30000)
        except Exception: pass
        B.wait_cloudflare(page)
        page.wait_for_timeout(3000)
        out["url"] = page.url; out["title"] = page.title()
        out["totals"] = B.parse_totals(page)
        try: page.screenshot(path=str(Path(a.shotdir) / "review.png"))
        except Exception: pass
        ctx.close()
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()