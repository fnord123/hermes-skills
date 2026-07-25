#!/usr/bin/env python3
"""store_api.py - authenticated client for the REAL Bambu US store
(us-store-api.bambulab.com), driven by the Bearer store token. Pure JSON API,
no browser. Contracts verified against live DevTools captures (2026-06-20).

Auth: header `authorization: Bearer TC <storeJWT>` + `x-bbl-account-identity: PERSONAL`.
Token from $BBL_STORE_TOKEN or ~/.hermes/cache/bambu-store/store_token.txt
(fallback /tmp/bbl_store_token.txt). Token is a live secret - never printed.

CLI:
  store_api.py whoami            # account identity (read)
  store_api.py cart              # show cart (read)
"""
import os, sys, json, urllib.request, urllib.error

API = "https://us-store-api.bambulab.com"
GID = "178181257136532676"          # authenticated store-gid (from live capture)

def load_token():
    t = os.environ.get("BBL_STORE_TOKEN")
    if t:
        return t.strip()
    for p in ("~/.hermes/cache/bambu-store/store_token.txt", "/tmp/bbl_store_token.txt"):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return open(p).read().strip()
    raise SystemExit("no store token (set BBL_STORE_TOKEN or write the token file)")

def _headers():
    return {
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "bbl-locale": "en-US",
        "bbl-trace-id": "hermes-store",
        "x-bbl-store-gid": GID,
        "x-bbl-store-region": "US",
        "x-bbl-account-access": "DEFAULT_C",
        "x-bbl-account-identity": "PERSONAL",   # required for cart writes
        "x-bbl-time-zone": "America/Los_Angeles",
        "authorization": "Bearer " + load_token(),
        "origin": "https://us.store.bambulab.com",
        "referer": "https://us.store.bambulab.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36",
    }

def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None

# ---- search / resolve (bridge a text query -> the ids cart_add needs) ----
SEARCH_GID = "178182358546347218"  # search uses a different store-gid than cart/checkout

def search(query, size=10):
    H = dict(_headers()); H["x-bbl-store-gid"] = SEARCH_GID
    body = {"content": query, "current": 1, "size": size}
    import urllib.request as u
    req = u.Request(API + "/mall-goods/product/globalSearchV2",
                    data=json.dumps(body).encode(), headers=H, method="POST")
    try:
        with u.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
    except Exception:
        return []
    return (((d.get("data") or {}).get("page") or {}).get("records")) or []

def resolve(query):
    """Map a text query to what cart_add needs. Returns:
      {name, productId, productSkuId, seoCode, inStock, fromPrice, needsColor, colors[]}
    If highlightProductSkuId is null (generic query) -> needsColor=True + color list."""
    recs = search(query, 5)
    if not recs:
        return None
    p = recs[0]
    sku = p.get("highlightProductSkuId")
    colors = [{"name": (c.get("colorName") or c.get("name")),
               "propertyValueId": c.get("propertyValueId"),
               "skuId": c.get("discountSkuId") or c.get("skuId"),
               "inStock": not (c.get("outOfStockMsg") or "").strip()}
              for c in (p.get("colorList") or [])]
    return {
        "name": p.get("name"), "seoCode": p.get("seoCode"),
        "productId": str(p.get("id")) if p.get("id") is not None else None,
        "productSkuId": str(sku) if sku is not None else None,
        "inStock": not (p.get("outOfStockMsg") or "").strip(),
        "fromPrice": p.get("lowerPrice"), "needsColor": sku is None,
        "colors": colors,
    }

# ---- reads ----
def user_info():
    return call("/mall-userms/user/ms/user/info")[1]

def cart_size():
    return ((call("/mall-order/v1/cart/size")[1] or {}).get("data") or {}).get("cartSize")

def cart_query():
    d = (call("/mall-order/v1/cart/query")[1] or {}).get("data") or {}
    lines = []
    for g in d.get("groups") or []:
        for it in g.get("items") or []:
            lines.append({
                "cartItemId": it.get("cartItemId"), "productId": it.get("productId"),
                "productSkuId": it.get("productSkuId"), "name": it.get("productName"),
                "variant": it.get("propertySimpleDesc"), "qty": it.get("quantity"),
                "price": it.get("price"), "discountedPrice": it.get("discountedPrice"),
                "total": it.get("total"), "selected": it.get("selected"),
                "removable": it.get("removable"), "outOfStock": it.get("outOfStock"),
            })
    return {"size": d.get("cartSize"), "total": d.get("totalPrice"),
            "withoutDiscount": d.get("totalWithoutDiscount"), "lines": lines}

# ---- writes (verified contract) ----
def cart_add(sku_id, product_id, quantity=1, sub_items=None):
    """Add a SKU to cart. Verified body shape from a live browser capture."""
    body = {"addSku": [{"quantity": quantity, "productSkuId": str(sku_id),
                        "productId": str(product_id), "subItems": sub_items or []}],
            "giftList": [], "pageType": 0}
    return call("/mall-order/v1/cart/add", "POST", body)

def cart_modify(items):
    """Set quantities for existing cart lines. items=[(cartItemId, quantity), ...].
    quantity 0 removes the line. Verified body shape from a live browser capture."""
    body = {"modifyItems": [{"cartItemId": str(c), "quantity": q} for c, q in items]}
    return call("/mall-order/v1/cart/modify", "POST", body)

# ---- checkout (pre-payment; computes the reviewable total, NO charge) ----
def checkout_token():
    d = (call("/mall-order/v1/checkout/token/create", "POST", {})[1] or {}).get("data") or {}
    return d.get("token")

def addresses():
    return (call("/mall-userms/user/shippingAddress/queryList?")[1] or {}).get("data") or []

def default_address():
    a = addresses()
    return (([x for x in a if x.get("isDefault") == 1] or a) or [None])[0]

def checkout_preview(location=None):
    """Build the order for the SELECTED cart items at `location` (default address
    if None). Returns the order summary with grandTotal/tax/shipping/orderCode.
    'temporary' order = preview; does NOT charge. Verified contract."""
    loc = location or default_address()
    token = checkout_token()
    body = {"token": token, "isTaxRelatedValid": True, "isOrderValid": True,
            "location": loc, "promotionCodeList": [], "usedPoints": None,
            "insuranceSelected": False}
    st, j = call("/mall-order/v1/checkout/temporary/one/page/order/create", "POST", body)
    return (j or {}).get("data") or {}

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "cart"
    if cmd == "whoami":
        d = (user_info() or {}).get("data") or {}
        print(json.dumps({"userType": d.get("userType"), "userName": d.get("userName"),
                          "userId": d.get("userId")}, indent=2))
    elif cmd == "cart":
        c = cart_query()
        print("cart: %s items, $%s (pre-discount $%s)" % (c["size"], c["total"], c["withoutDiscount"]))
        for l in c["lines"]:
            print("  [%s] %s %s  x%s  $%s%s" % (
                l["cartItemId"], l["name"], (l["variant"] or ""), l["qty"], l["discountedPrice"],
                "" if l["selected"] else "  (unselected)"))
    elif cmd == "search":
        q = " ".join(sys.argv[2:])
        for p in search(q, 8):
            sku = p.get("highlightProductSkuId")
            lower = p.get("lowerPrice"); oos = (p.get("outOfStockMsg") or "").strip()
            print("  %s — from $%s — %s%s" % (
                p.get("name"), lower, ("in stock" if not oos else "OUT: " + oos),
                ("  sku=%s pid=%s" % (sku, p.get("id")) if sku else "  (specify a color)")))
    elif cmd == "add":
        # add <query...> [qty]  — last arg may be an integer quantity
        args = sys.argv[2:]
        qty = 1
        if len(args) >= 2 and args[-1].isdigit():
            qty = int(args[-1]); args = args[:-1]
        q = " ".join(args)
        r = resolve(q)
        if not r:
            print("no match for %r" % q); return
        if r["needsColor"]:
            print("'%s' matched %s but needs a specific color — include the color in the request "
                  "(e.g. 'add PLA Matte Ash Grey')." % (q, r["name"])); return
        if not r["inStock"]:
            print("%s is out of stock." % r["name"]); return
        st, j = cart_add(r["productSkuId"], r["productId"], qty)
        if (j or {}).get("code") != 1:
            print("add failed: %s" % (j or {}).get("message")); return
        c = cart_query()
        print("added %dx %s. cart now %s items, $%s." % (qty, r["name"], c["size"], c["total"]))
    elif cmd == "set":
        # set <cartItemId> <qty>   (qty 0 removes)
        cid, q = sys.argv[2], int(sys.argv[3])
        st, j = cart_modify([(cid, q)])
        print("modify -> code %s" % (j or {}).get("code"))
    elif cmd == "checkout":
        d = checkout_preview()
        if not d:
            print("checkout preview failed"); return
        def g(k):
            return d.get(k)
        print("ORDER PREVIEW (selected cart items, no charge):")
        print("  subtotal     $%s" % g("subTotal"))
        print("  discount    -$%s" % g("discountPrice"))
        print("  shipping     $%s" % g("shipping"))
        td = g("taxDetail")
        print("  tax          %s" % (td if not isinstance(td, (dict, list)) else json.dumps(td)[:80]))
        print("  GRAND TOTAL  $%s   (needPay=%s)" % (g("grandTotal"), g("needPay")))
        sm = g("selectableShippingMethods")
        if sm:
            print("  shipping methods: %d available" % (len(sm) if isinstance(sm, list) else 1))
    else:
        print("usage: store_api.py [whoami|cart|checkout]")

if __name__ == "__main__":
    main()
