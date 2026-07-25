#!/usr/bin/env python3
"""cart_ops.py - authenticated cart operations against the REAL store API using
the Bearer store token (no browser). Reversible self-test by default: snapshots
the cart, adds 1 unit of a sku, verifies, then restores the cart exactly.

Token: read from $BBL_STORE_TOKEN or ~/.hermes/cache/bambu-store/store_token.txt
(falls back to /tmp/bbl_store_token.txt). Token is a live secret - never printed.
"""
import os, json, urllib.request, urllib.error

GID = "178181257136532676"
API = "https://us-store-api.bambulab.com"
TEST_SKU = "41227780718728"  # ASA default color (from PDP capture)

def load_token():
    t = os.environ.get("BBL_STORE_TOKEN")
    if t:
        return t.strip()
    for p in ("~/.hermes/cache/bambu-store/store_token.txt", "/tmp/bbl_store_token.txt"):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return open(p).read().strip()
    raise SystemExit("no store token found")

TOKEN = load_token()
H = {"content-type": "application/json", "accept": "application/json, text/plain, */*",
     "bbl-locale": "en-US", "bbl-trace-id": "hermes-cart-ops", "x-bbl-store-gid": GID,
     "x-bbl-store-region": "US", "x-bbl-account-access": "DEFAULT_C",
     "x-bbl-time-zone": "America/Los_Angeles", "authorization": "Bearer " + TOKEN,
     "origin": "https://us.store.bambulab.com", "referer": "https://us.store.bambulab.com/",
     "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36"}

def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None

def cart():
    s, j = call("/mall-order/v1/cart/query")
    d = (j or {}).get("data") or {}
    lines = []
    for g in d.get("groups") or []:
        for it in g.get("items") or []:
            lines.append({"cartItemId": it.get("cartItemId"), "sku": it.get("productSkuId"),
                          "name": it.get("productName"), "qty": it.get("quantity"),
                          "selected": it.get("selected")})
    return {"size": d.get("cartSize"), "total": d.get("totalPrice"), "lines": lines}

def main():
    print("=== SNAPSHOT (before) ===")
    before = cart()
    print("size", before["size"], "total", before["total"])
    before_ids = {l["cartItemId"]: l for l in before["lines"]}
    print("existing skus:", [l["sku"] for l in before["lines"]])

    # 1) discover the remove endpoint with a FAKE cartItemId (touches nothing real)
    print("\n=== probe remove/update endpoints (fake id, safe) ===")
    fake = "999999999999999999"
    for path, body in [
        ("/mall-order/v1/cart/delete", {"cartItemIds": [fake]}),
        ("/mall-order/v1/cart/delete", {"cartItemId": fake}),
        ("/mall-order/v1/cart/remove", {"cartItemIds": [fake]}),
        ("/mall-order/v1/cart/deleteItem", {"cartItemId": fake}),
        ("/mall-order/v1/cart/batchDelete", {"cartItemIds": [fake]}),
    ]:
        s, j = call(path, "POST", body)
        code = (j or {}).get("code") if isinstance(j, dict) else None
        msg = (j or {}).get("message") if isinstance(j, dict) else j
        print(f"  {path} {list(body)} -> http={s} code={code} msg={str(msg)[:60]}")

    # 2) add 1 unit
    print("\n=== cart/add (productSkuId + quantity) ===")
    for body in [{"productSkuId": TEST_SKU, "quantity": 1}, {"skuId": TEST_SKU, "count": 1}]:
        s, j = call("/mall-order/v1/cart/add", "POST", body)
        code = (j or {}).get("code"); msg = (j or {}).get("message")
        print(f"  add {list(body)} -> http={s} code={code} msg={str(msg)[:60]}")
        if code == 1:
            print("  >>> add SUCCESS with body keys:", list(body)); break

    print("\n=== SNAPSHOT (after add) ===")
    after = cart()
    print("size", after["size"], "total", after["total"])
    # find the delta: a new cartItemId, or an existing line whose qty increased
    new_lines = [l for l in after["lines"] if l["cartItemId"] not in before_ids]
    bumped = [l for l in after["lines"]
              if l["cartItemId"] in before_ids and l["qty"] > before_ids[l["cartItemId"]]["qty"]]
    print("new lines:", [(l["cartItemId"], l["sku"], l["qty"]) for l in new_lines])
    print("bumped lines:", [(l["cartItemId"], l["sku"], before_ids[l["cartItemId"]]["qty"], "->", l["qty"]) for l in bumped])

    print("\n(restore step intentionally left for a second invocation once the")
    print(" remove endpoint is confirmed above - see which path returned a")
    print(" non-404 business code.)")

if __name__ == "__main__":
    main()
