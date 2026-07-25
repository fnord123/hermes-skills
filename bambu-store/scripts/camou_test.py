import sys, json
from camoufox.sync_api import Camoufox
URL = "https://bambulab.com/en/sign-in"
with Camoufox(headless=True, geoip=True, humanize=True) as browser:
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=70000)
    cleared = False
    for i in range(30):
        page.wait_for_timeout(2000)
        t = (page.title() or "").lower()
        if "just a moment" not in t and "moment" not in t and "verify" not in t:
            cleared = True; break
    print("cleared CF:", cleared, "| title:", (page.title() or "")[:60], "| waited ~%ds" % ((i+1)*2))
    page.wait_for_timeout(3000)
    try: page.screenshot(path="/tmp/camou_signin.png")
    except Exception: pass
    fields = page.evaluate("() => [...document.querySelectorAll('input')].filter(e=>e.offsetParent!==null).map(e=>({type:e.type,name:e.name,ph:e.placeholder}))")
    print("login fields:", json.dumps(fields)[:400])
    btns = page.evaluate("() => [...document.querySelectorAll('button')].filter(e=>e.offsetParent!==null).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,10)")
    print("buttons:", json.dumps(btns)[:300])
    print("url:", (page.url or "")[:80])