#!/usr/bin/env python3
"""store_sso_test.py - test whether the store stays GUEST only because the
auth cookies (token/refreshToken/...) are host-only on `bambulab.com` and thus
never reach `us-store-api.bambulab.com` / the store's SSO-exchange call.

Experiment (no 2FA, no writes):
  1. Load saved cookies; clone the token-family onto `.bambulab.com` (shared)
     AND onto the store hosts, so any cross-subdomain exchange/refresh call can
     carry them.
  2. Navigate a PDP; capture any sso/login/refresh/exchange/token XHR and the
     exact cookie header the store sends to us-store-api.
  3. Re-check user/info. If it authenticates, save the upgraded storage_state.

Usage: .venv/bin/python bin/store_sso_test.py
"""
import os, json, copy
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
PDP = "https://us.store.bambulab.com/products/asa-filament"
STORE_GID = "28252390332552050"
AUTH_NAMES = {"token", "refreshToken", "expiresIn", "refreshExpiresIn"}

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

def build_state():
    st = json.load(open(COOKIES))
    extra = []
    for c in st["cookies"]:
        if c["name"] in AUTH_NAMES and not c.get("domain", "").startswith("."):
            for dom in (".bambulab.com",):
                n = copy.deepcopy(c); n["domain"] = dom; extra.append(n)
    st["cookies"].extend(extra)
    return st, len(extra)

def main():
    out = {}
    state, n = build_state()
    out["rescoped_cookies_added"] = n
    grabbed = []
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=state)
        page = ctx.new_page()

        def on_req(req):
            try:
                u = req.url
                if "bambulab.com" not in u:
                    return
                if any(k in u.lower() for k in ("sso", "/login", "sign-in", "refresh", "exchange", "oauth", "/token")):
                    grabbed.append({"m": req.method, "url": u[:110]})
                # record exact cookie header sent to the store API user/info
                if "us-store-api.bambulab.com/mall-userms/user/ms/user/info" in u:
                    ck = (req.headers or {}).get("cookie", "")
                    names = [p.split("=")[0].strip() for p in ck.split(";") if p.strip()]
                    grabbed.append({"m": "INFO", "url": "user/info cookie names",
                                    "cookie_names": names})
            except Exception:
                pass
        page.on("request", on_req)

        page.goto(PDP, wait_until="domcontentloaded", timeout=70000)
        for _ in range(25):
            page.wait_for_timeout(2000)
            t = (page.title() or "").lower()
            if all(s not in t for s in ("moment", "verify", "checking")):
                break
        page.wait_for_timeout(6000)  # let any exchange/refresh + user/info run
        out["userinfo"] = page.evaluate(USERTYPE_JS, STORE_GID)
        out["auth_xhr"] = grabbed[:15]
        authed = (out["userinfo"] or {}).get("userType") not in (None, "GUEST")
        out["store_authenticated"] = authed
        if authed:
            json.dump(ctx.storage_state(), open(COOKIES, "w"))
            out["cookies_upgraded"] = COOKIES
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
