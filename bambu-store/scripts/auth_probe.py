#!/usr/bin/env python3
"""auth_probe.py - with the logged-in bambulab.com cookies, navigate the STORE
(us.store.bambulab.com) so the SSO->store-session handshake runs, then capture
the FULL request headers of an authenticated store-API XHR (user/info, cart/*).

Goal: discover the exact auth header the store frontend attaches (the missing
piece behind the cart/add 10012 and the GUEST user/info). Read-only: no cart
writes, just browses the store home + an account read.

Usage: .venv/bin/python bin/auth_probe.py
"""
import os, json
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
# product page, not store home: the home page throws an uncaught JS error that
# crashes the camoufox FF driver (pageError.location undefined). PDP is stable.
URL = "https://us.store.bambulab.com/products/asa-filament"

REDACT = ("token", "authorization", "cookie")  # don't print full secrets

def redact(k, v):
    if any(s in k.lower() for s in REDACT):
        return (v[:14] + "…(%d chars)" % len(v)) if v else v
    return v

def main():
    out = {}
    grabbed = []
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=COOKIES)
        page = ctx.new_page()

        def on_request(req):
            try:
                u = req.url
                if "us-store-api.bambulab.com" not in u:
                    return
                if not any(p in u for p in ("/user/", "/cart/", "/mall-userms")):
                    return
                hdrs = {k: redact(k, v) for k, v in (req.headers or {}).items()}
                grabbed.append({"url": u.split("bambulab.com")[-1][:70],
                                "auth_hdr_names": [k for k in (req.headers or {})
                                                   if any(s in k.lower() for s in ("token", "auth"))],
                                "headers": hdrs})
            except Exception:
                pass
        page.on("request", on_request)

        page.goto(URL, wait_until="domcontentloaded", timeout=70000)
        for _ in range(25):
            page.wait_for_timeout(2000)
            t = (page.title() or "").lower()
            if "moment" not in t and "verify" not in t and "checking" not in t:
                break
        out["title"] = (page.title() or "")[:60]
        page.wait_for_timeout(5000)  # let user/info + boot XHR fire

        # also read user/info from inside the authenticated page context
        out["userinfo_in_page"] = page.evaluate(
            """async () => {
                try {
                  const r = await fetch('https://us-store-api.bambulab.com/mall-userms/user/ms/user/info',
                    {headers:{'accept':'application/json'}, credentials:'include'});
                  const j = await r.json();
                  return {code:j.code, userType:(j.data||{}).userType, name:(j.data||{}).userName};
                } catch(e) { return {err:String(e).slice(0,100)}; }
            }""")
        out["captured_requests"] = grabbed[:8]
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
