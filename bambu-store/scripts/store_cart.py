#!/usr/bin/env python3
"""store_cart.py - operate the authenticated STORE session from inside the
logged-in browser origin (in-page fetch), so the browser attaches the correct
session cookie automatically (the store auths by COOKIE, sends no auth header).

Uses the REAL store-gid 28252390332552050 (captured from a live store XHR), not
the 178182358546347218 the read-CLI uses for search.

Default action is READ-ONLY: confirm auth (user/info) + dump cart (cart/query).
Pass --add SKUID[:COUNT] to add to cart (WRITE - hits the logged-in account's
real cart). Read-only unless --add is given.

Usage:
  .venv/bin/python bin/store_cart.py                 # auth check + cart read
  .venv/bin/python bin/store_cart.py --add 41227780718728:1
"""
import os, sys, json
from camoufox.sync_api import Camoufox

COOKIES = os.path.expanduser("~/.hermes/cache/bambu-store/bambu_cookies.json")
PDP = "https://us.store.bambulab.com/products/asa-filament"
STORE_GID = "28252390332552050"  # real, from a live store XHR

add_arg = None
if "--add" in sys.argv:
    add_arg = sys.argv[sys.argv.index("--add") + 1]

# JS run inside the authenticated store page. Returns {userType, cart, add?}.
JS = """
async (cfg) => {
  const base = 'https://us-store-api.bambulab.com';
  const H = () => ({
    'content-type': 'application/json',
    'accept': 'application/json, text/plain, */*',
    'bbl-locale': 'en-US',
    'bbl-trace-id': crypto.randomUUID(),
    'x-bbl-store-gid': cfg.gid,
    'x-bbl-store-region': 'US',
    'x-bbl-account-access': 'DEFAULT_C',
    'x-bbl-time-zone': 'America/Los_Angeles',
  });
  const get = async (p) => {
    const r = await fetch(base + p, {headers: H(), credentials: 'include'});
    return {status: r.status, body: await r.json().catch(() => null)};
  };
  const post = async (p, b) => {
    const r = await fetch(base + p, {method: 'POST', headers: H(),
      credentials: 'include', body: JSON.stringify(b)});
    return {status: r.status, body: await r.json().catch(() => null)};
  };
  const out = {};
  out.userinfo = await get('/mall-userms/user/ms/user/info');
  out.cart = await get('/mall-order/v1/cart/query');
  if (cfg.add) {
    const [sku, cnt] = cfg.add.split(':');
    out.add_sku = sku; out.add_count = Number(cnt || 1);
    out.add = await post('/mall-order/v1/cart/add', {skuId: sku, count: out.add_count});
    out.cart_after = await get('/mall-order/v1/cart/query');
  }
  return out;
}
"""

def main():
    out = {"add_requested": add_arg}
    with Camoufox(headless=True, geoip=True, humanize=True) as browser:
        ctx = browser.new_context(storage_state=COOKIES)
        page = ctx.new_page()
        page.goto(PDP, wait_until="domcontentloaded", timeout=70000)
        for _ in range(25):
            page.wait_for_timeout(2000)
            t = (page.title() or "").lower()
            if "moment" not in t and "verify" not in t and "checking" not in t:
                break
        page.wait_for_timeout(4000)
        out["result"] = page.evaluate(JS, {"gid": STORE_GID, "add": add_arg})
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
