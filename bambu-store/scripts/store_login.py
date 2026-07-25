#!/usr/bin/env python3
"""store_login.py - establish the STORE session via SILENT SSO, reusing the
existing bambulab.com login (token cookie already in storage_state). Logging
into bambulab.com does NOT authenticate us.store.bambulab.com; the store needs
its own SSO callback. Because the IdP session already exists, triggering the
store's sign-in SHOULD complete silently (no fresh 2FA).

SAFETY: if the SSO lands on an actual login FORM (input[name=account]), this
STOPS and reports — it does NOT fill credentials or trigger a 2FA code.

Read-only unless it succeeds, in which case it saves the upgraded storage_state
(now carrying the store session) back to the cookies file.

Usage: .venv/bin/python bin/store_login.py
"""
import os, json
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
PDP = "https://us.store.bambulab.com/products/asa-filament"
STORE_GID = "28252390332552050"

USERTYPE_JS = """
async (gid) => {
  try {
    const r = await fetch('https://us-store-api.bambulab.com/mall-userms/user/ms/user/info',
      {credentials:'include', headers:{
        'accept':'application/json, text/plain, */*','bbl-locale':'en-US',
        'bbl-trace-id':crypto.randomUUID(),'x-bbl-store-gid':gid,'x-bbl-store-region':'US',
        'x-bbl-account-access':'DEFAULT_C','x-bbl-time-zone':'America/Los_Angeles'}});
    const j = await r.json(); const d = j.data||{};
    return {userType:d.userType, name:d.userName, identity:d.latestAccountIdentity};
  } catch(e){ return {err:String(e).slice(0,80)}; }
}
"""

def clear_cf(page, secs=50):
    for _ in range(secs // 2):
        page.wait_for_timeout(2000)
        t = (page.title() or "").lower()
        if all(s not in t for s in ("moment", "verify", "checking")):
            return True
    return False

def find_signin(page):
    """Return info about a sign-in entry on the page (href or a clickable handle tag)."""
    return page.evaluate(
        """() => {
            const els=[...document.querySelectorAll('a,button,[role=button]')].filter(e=>e.offsetParent!==null);
            const hit=els.find(e=>/sign in|log in|login|sign-in/i.test((e.innerText||'')+' '+(e.getAttribute('aria-label')||'')+' '+(e.href||'')));
            if(!hit) return null;
            hit.setAttribute('data-signin','1');
            return {tag:hit.tagName, txt:(hit.innerText||'').trim().slice(0,30), href:hit.href||null,
                    aria:(hit.getAttribute('aria-label')||'').slice(0,30)};
        }""")

def main():
    out = {}
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=COOKIES)
        page = ctx.new_page()
        page.goto(PDP, wait_until="domcontentloaded", timeout=70000)
        clear_cf(page)
        page.wait_for_timeout(3000)
        out["before"] = page.evaluate(USERTYPE_JS, STORE_GID)

        si = find_signin(page)
        out["signin_entry"] = si
        if not si:
            out["note"] = "no sign-in entry found on PDP header"
            print(json.dumps(out, indent=2)); return

        # trigger it (href nav if present, else click) and follow SSO redirects
        el = page.query_selector("[data-signin='1']")
        try:
            el.click(timeout=6000)
        except Exception as e:
            out["click_err"] = str(e)[:100]
        # wait for SSO round-trip to settle
        for _ in range(20):
            page.wait_for_timeout(1500)
            u = (page.url or "")
            if "us.store.bambulab.com" in u and "sign-in" not in u:
                break
        out["url_after"] = (page.url or "")[:100]

        # SAFETY: if we're on a login form, stop without filling
        on_form = bool(page.query_selector("input[name=account]") or page.query_selector("input[name=password]"))
        out["on_login_form"] = on_form
        if on_form:
            out["note"] = "SSO landed on a LOGIN FORM (would need credentials/2FA) - STOPPED, no code consumed"
            print(json.dumps(out, indent=2)); return

        page.wait_for_timeout(3000)
        out["after"] = page.evaluate(USERTYPE_JS, STORE_GID)
        authed = (out["after"] or {}).get("userType") not in (None, "GUEST")
        out["store_authenticated"] = authed
        if authed:
            json.dump(ctx.storage_state(), open(COOKIES, "w"))
            out["cookies_upgraded"] = COOKIES
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
