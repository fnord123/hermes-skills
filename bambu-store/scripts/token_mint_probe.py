#!/usr/bin/env python3
"""token_mint_probe.py - discover how the store frontend MINTS the Bearer store
JWT, so we can refresh it headlessly. Our login cookies only authenticate
bambulab.com; the store JWT is minted on first store visit (likely the HOME
page, which crashes the FF driver). So we STREAM every request/response to a log
file immediately - if the driver crashes mid-load, the mint call is still on disk.

Look in the log for: a response body containing a JWT (eyJ...), or any
login/sso/auth/token/sign endpoint, and what request carried the bambulab.com
token to mint the store JWT.

Usage: .venv/bin/python bin/token_mint_probe.py [home|pdp]   (default home)
Output log: /tmp/mint_net.log   (token values truncated)
"""
import os, sys, json
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
target = sys.argv[1] if len(sys.argv) > 1 else "home"
URL = "https://us.store.bambulab.com/" if target == "home" else "https://us.store.bambulab.com/products/asa-filament"
LOG = "/tmp/mint_net.log"

INTEREST = ("login", "sso", "auth", "/token", "sign", "session", "exchange", "refresh", "oauth", "/user/")

def trunc(s, n=24):
    return (s[:n] + "...(%d)" % len(s)) if s and len(s) > n else s

def main():
    logf = open(LOG, "w", buffering=1)  # line-buffered: survives a crash
    logf.write("# token mint probe url=%s\n" % URL)
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=COOKIES)
        page = ctx.new_page()

        def on_request(req):
            try:
                u = req.url
                if "bambulab.com" not in u:
                    return
                low = u.lower()
                interesting = any(k in low for k in INTEREST)
                # log auth header presence + any token-ish in body
                ah = (req.headers or {}).get("authorization", "")
                line = {"ev": "req", "m": req.method, "url": u[:120],
                        "has_auth": bool(ah), "auth": trunc(ah, 18)}
                if interesting:
                    try:
                        line["body"] = trunc(req.post_data or "", 200)
                    except Exception:
                        pass
                    logf.write(json.dumps(line) + "\n")
            except Exception:
                pass

        def on_response(resp):
            try:
                u = resp.url
                if "bambulab.com" not in u:
                    return
                low = u.lower()
                if not any(k in low for k in INTEREST):
                    return
                body = ""
                try:
                    if "json" in (resp.headers.get("content-type", "")):
                        body = resp.text()
                except Exception:
                    pass
                has_jwt = "eyJ" in body
                rec = {"ev": "resp", "status": resp.status, "url": u[:120],
                       "has_jwt": has_jwt}
                if has_jwt:
                    # capture WHICH field holds the jwt, value truncated
                    i = body.find("eyJ")
                    rec["jwt_ctx"] = body[max(0, i - 40):i + 20].replace("\n", " ")
                else:
                    rec["body"] = trunc(body, 160)
                logf.write(json.dumps(rec) + "\n")
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            for _ in range(20):
                page.wait_for_timeout(1500)
            logf.write("# completed without crash\n")
        except Exception as e:
            logf.write("# python-level error: %s\n" % str(e)[:120])
    logf.write("# done\n"); logf.close()
    print("log written to", LOG)

if __name__ == "__main__":
    main()
