#!/usr/bin/env python3
"""bambu_login.py - log into the REAL Bambu account (bambulab.com SSO) using
camoufox (defeats the Cloudflare challenge that plain playwright-stealth could
not), email+password, and AgentMail-read 2FA. Saves the authenticated session
to a cookies file for reuse by cart/checkout steps.

THIS IS THE AUTH BREAKTHROUGH. Plain playwright-stealth gets 403 on
bambulab.com/sign-in; camoufox clears it in ~2s.

Usage:
  .venv/bin/python bin/bambu_login.py            # do a fresh login, save cookies
  .venv/bin/python bin/bambu_login.py --check    # report whether saved cookies look present

CAUTION: bambulab.com rate-limits verification codes after ~8 rapid attempts
(symptom: no new code email -> code_read=false). Space attempts out.
"""
import os, sys, json, re, urllib.parse, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bambu_lib as B

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
SIGNIN = "https://bambulab.com/en/sign-in"

def clear_cf(page, secs=50):
    for _ in range(secs // 2):
        page.wait_for_timeout(2000)
        t = (page.title() or "").lower()
        if "just a moment" not in t and "moment" not in t and "verify" not in t and "checking" not in t:
            return True
    return False

def newest_bambu_code(key, since, timeout=150):
    """Read the NEWEST 'Your Bambu Lab verification code' email after `since`.
    Uses bambu_lib.extract_code (strips HTML/tracking; the code is buried among
    tracking IDs in the email)."""
    after = (since - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = B.AGENTMAIL_BASE
    ids = [(i.get("inbox_id") or i.get("id") or i.get("email_address"))
           for i in B._clist(B.am_get(base + "/v0/inboxes", key), "inboxes")]
    end = time.time() + timeout
    while time.time() < end:
        best = None
        for iid in ids:
            q = urllib.parse.urlencode({"limit": "20", "after": after})
            try:
                th = B._clist(B.am_get(base + "/v0/inboxes/" + urllib.parse.quote(iid, safe="@") + "/threads?" + q, key), "threads")
            except Exception:
                th = []
            for tn in th:
                if "bambu lab verification" not in str(tn.get("subject", "")).lower():
                    continue
                ts = str(tn.get("updated_at") or tn.get("timestamp") or tn.get("created_at") or "")
                tid = tn.get("thread_id") or tn.get("id")
                if best is None or ts > best[0]:
                    best = (ts, tid, iid)
        if best:
            full = B.am_get(base + "/v0/inboxes/" + urllib.parse.quote(best[2], safe="@") + "/threads/" + urllib.parse.quote(best[1]), key)
            body = ""
            for m in (full.get("messages") or []):
                body = m.get("text") or m.get("extracted_text") or body
                if body:
                    break
            if not body:
                body = json.dumps(full)
            code = B.extract_code(body)
            if code:
                return {"code": code, "ts": best[0]}
        time.sleep(5)
    return None

def main():
    if "--check" in sys.argv:
        ok = os.path.exists(COOKIES)
        n = len(json.load(open(COOKIES)).get("cookies", [])) if ok else 0
        print(json.dumps({"cookies_file": COOKIES, "exists": ok, "cookie_count": n}))
        return
    from camoufox.sync_api import Camoufox
    email = B.envv("BAMBU_EMAIL"); pw = B.envv("BAMBU_PASSWORD"); key = B.get_agentmail_key()
    out = {"creds_present": bool(email and pw and key)}
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(SIGNIN, wait_until="domcontentloaded", timeout=70000)
        out["cf_cleared"] = clear_cf(page)
        page.wait_for_timeout(2000)
        if not page.query_selector("input[name=account]"):
            out["state"] = "no_login_form (already logged in or page changed)"
        else:
            page.fill("input[name=account]", email)
            page.fill("input[name=password]", pw)
            cb = page.query_selector("input[name=agree]")
            if cb:
                try: cb.check()
                except Exception:
                    try: cb.click()
                    except Exception: pass
            t0 = datetime.now(timezone.utc)
            page.click("button:has-text('Log In')")
            page.wait_for_timeout(6000)
            # 2FA: single input[name=code] + "Confirm" button
            if page.query_selector("input[name=code]"):
                out["twofa"] = "required"
                res = newest_bambu_code(key, t0, timeout=150) if key else None
                out["code_read"] = bool(res)
                if res:
                    f = page.query_selector("input[name=code]"); f.click(); page.wait_for_timeout(300)
                    for ch in res["code"]:
                        page.keyboard.type(ch); page.wait_for_timeout(80)
                    page.wait_for_timeout(700)
                    page.click("button:has-text('Confirm')")
                    page.wait_for_timeout(9000)
                else:
                    out["note"] = "no fresh code (rate-limited? wait and retry)"
            else:
                out["twofa"] = "none"
            out["logged_in"] = "/sign-in" not in (page.url or "")
            out["url_final"] = (page.url or "")[:90]
            if out.get("logged_in"):
                try:
                    json.dump(ctx.storage_state(), open(COOKIES, "w"))
                    out["cookies_saved"] = COOKIES
                except Exception as e:
                    out["cookies_err"] = str(e)[:80]
            else:
                # capture any error text (e.g. "Incorrect code")
                out["page_msg"] = page.evaluate("() => { const t=document.body.innerText||''; return t.split(String.fromCharCode(10)).filter(x=>/invalid|incorrect|expired|error|wrong/i.test(x)).slice(0,3); }")
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()