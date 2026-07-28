#!/usr/bin/env python3
"""store_api.py - authenticated client for the REAL Bambu US store
(us-store-api.bambulab.com), driven by the Bearer store token. Pure JSON API,
no browser. Contracts verified against live DevTools captures (2026-06-20).

Auth: header `authorization: Bearer TC <storeJWT>` + `x-bbl-account-identity: PERSONAL`.
Token from $BBL_STORE_TOKEN or ~/.hermes/cache/bambu-store/store_token.txt.
The token is a live account secret - never printed.

Output contract (via the vendored scripts/skill_json.py): every command prints
exactly one JSON object - {"ok": true, ...} and exit 0 on success, or
{"ok": false, "error": "..."} and sys.exit(1) on any failure. `add` and `set`
change the user's real cart and refuse to run without --confirm.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_json import ArgumentParser, fail, guard, ok  # noqa: E402

API = "https://us-store-api.bambulab.com"
GID = "178181257136532676"          # authenticated store-gid (from live capture)
SEARCH_GID = "178182358546347218"   # search uses a different store-gid than cart/checkout
TOKEN_FILE = "~/.hermes/cache/bambu-store/store_token.txt"

CONFIRM_HINT = ("Re-run with --confirm ONLY after the user has explicitly approved "
                "this exact change.")


class StoreError(Exception):
    """A failure talking to the store, already phrased for the user."""


def load_token():
    t = os.environ.get("BBL_STORE_TOKEN")
    if t and t.strip():
        return t.strip()
    p = os.path.expanduser(TOKEN_FILE)
    if os.path.exists(p):
        t = open(p).read().strip()
        if t:
            return t
    raise StoreError("the store sign-in has not been set up yet; the account token "
                     "needs to be saved before cart or order commands can run")


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


def _describe_http(code, payload):
    """Turn a store HTTP failure into one sentence in the user's domain."""
    if code in (401, 403):
        return ("the store sign-in has expired; it needs to be refreshed before "
                "cart or order commands will work")
    msg = ""
    if isinstance(payload, dict):
        msg = str(payload.get("message") or "").strip()
    if msg:
        return "the store refused the request: %s" % msg
    return "the store returned an unexpected error (HTTP %s)" % code


def call(path, method="GET", body=None, gid=None):
    """One store API call. Raises StoreError on any failure - never returns a
    silent empty result, so an expired sign-in cannot look like 'no results'."""
    data = json.dumps(body).encode() if body is not None else None
    headers = _headers()
    if gid:
        headers["x-bbl-store-gid"] = gid
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:                                      # noqa: BLE001
            payload = None
        raise StoreError(_describe_http(e.code, payload))
    except urllib.error.URLError as e:
        raise StoreError("could not reach the Bambu store (%s)" % (e.reason,))
    except OSError as e:
        raise StoreError("could not reach the Bambu store (%s)" % (e,))
    try:
        payload = json.loads(raw)
    except ValueError:
        raise StoreError("the store sent a response this tool could not read")
    if isinstance(payload, dict) and payload.get("code") not in (None, 1, 0):
        raise StoreError(_describe_http(200, payload))
    return payload


# ---- search / resolve (bridge a text query -> the ids an add needs) ----
def search(query, size=10):
    payload = call("/mall-goods/product/globalSearchV2", "POST",
                   {"content": query, "current": 1, "size": size}, gid=SEARCH_GID)
    return (((payload.get("data") or {}).get("page") or {}).get("records")) or []


def resolve(query):
    """Map a text query to what an add needs. Returns None only when the store
    genuinely has no match - any store/auth/network fault raises instead."""
    recs = search(query, 5)
    if not recs:
        return None
    p = recs[0]
    sku = p.get("highlightProductSkuId")
    colors = [{"color": (c.get("colorName") or c.get("name")),
               "in_stock": not (c.get("outOfStockMsg") or "").strip()}
              for c in (p.get("colorList") or [])]
    return {
        "name": p.get("name"),
        "productId": str(p.get("id")) if p.get("id") is not None else None,
        "productSkuId": str(sku) if sku is not None else None,
        "in_stock": not (p.get("outOfStockMsg") or "").strip(),
        "from_price": p.get("lowerPrice"),
        "needs_color": sku is None,
        "colors": colors,
    }


# ---- reads ----
def user_info():
    return call("/mall-userms/user/ms/user/info")


def cart_query():
    d = (call("/mall-order/v1/cart/query").get("data")) or {}
    lines = []
    for g in d.get("groups") or []:
        for it in g.get("items") or []:
            lines.append({
                "line_id": it.get("cartItemId"),
                "name": it.get("productName"),
                "color": it.get("propertySimpleDesc"),
                "quantity": it.get("quantity"),
                "price": it.get("discountedPrice"),
                "line_total": it.get("total"),
                "included_in_total": it.get("selected"),
                "in_stock": not it.get("outOfStock"),
            })
    return {"item_count": d.get("cartSize"), "total": d.get("totalPrice"),
            "total_before_discount": d.get("totalWithoutDiscount"), "lines": lines}


# ---- writes (verified contract) ----
def cart_add(sku_id, product_id, quantity):
    body = {"addSku": [{"quantity": quantity, "productSkuId": str(sku_id),
                        "productId": str(product_id), "subItems": []}],
            "giftList": [], "pageType": 0}
    return call("/mall-order/v1/cart/add", "POST", body)


def cart_modify(line_id, quantity):
    body = {"modifyItems": [{"cartItemId": str(line_id), "quantity": quantity}]}
    return call("/mall-order/v1/cart/modify", "POST", body)


# ---- checkout (pre-payment; computes the reviewable total, NO charge) ----
def checkout_preview():
    """Build the order for the SELECTED cart items at the default address.
    'temporary' order = preview; does NOT charge. Verified contract."""
    addrs = (call("/mall-userms/user/shippingAddress/queryList?").get("data")) or []
    loc = (([x for x in addrs if x.get("isDefault") == 1] or addrs) or [None])[0]
    if not loc:
        raise StoreError("the account has no shipping address saved, so a total "
                         "cannot be calculated")
    token = ((call("/mall-order/v1/checkout/token/create", "POST", {}).get("data")) or {}).get("token")
    body = {"token": token, "isTaxRelatedValid": True, "isOrderValid": True,
            "location": loc, "promotionCodeList": [], "usedPoints": None,
            "insuranceSelected": False}
    d = (call("/mall-order/v1/checkout/temporary/one/page/order/create", "POST", body).get("data")) or {}
    if not d:
        raise StoreError("the store did not return an order total; the cart may be empty")
    return d


# ---- quantity phrasing ----
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT_RE = re.compile(
    r"^\s*(\d{1,3}|" + "|".join(_WORD_NUM) + r")\s*"
    r"(?:x|rolls?|spools?|units?|pcs?|pieces?)?\s*(?:of)?\s+(?=\S)",
    re.I)


def split_count(text):
    """Pull a leading count out of an item phrase: '2 rolls of PLA Basic' ->
    (2, 'PLA Basic'). Returns (None, text) when the phrase carries no count."""
    m = _COUNT_RE.match(text or "")
    if not m:
        return None, (text or "").strip()
    tok = m.group(1).lower()
    n = _WORD_NUM.get(tok)
    if n is None:
        n = int(tok)
    return n, text[m.end():].strip()


# ---- commands ----
def cmd_whoami(args):
    d = (user_info().get("data")) or {}
    ok(account=d.get("userName"), account_type=d.get("userType"))


def cmd_cart(args):
    ok(**cart_query())


def cmd_search(args):
    results = []
    for p in search(args.item, 8):
        oos = (p.get("outOfStockMsg") or "").strip()
        results.append({
            "name": p.get("name"),
            "from_price": p.get("lowerPrice"),
            "in_stock": not oos,
            "needs_color": p.get("highlightProductSkuId") is None,
        })
    ok(query=args.item, count=len(results), results=results)


def cmd_add(args):
    # Changes the user's real cart. Guard first, before any store call.
    if not args.confirm:
        fail("add puts items in the user's real Bambu cart. " + CONFIRM_HINT)
    phrase_qty, item = split_count(args.item)
    if not item:
        fail("no product was named; say the material and colour, e.g. 'PLA Basic Jade White'")
    if phrase_qty is not None and args.qty is not None and phrase_qty != args.qty:
        fail("the request says %d and --qty says %d; ask the user which quantity they want"
             % (phrase_qty, args.qty))
    qty = args.qty if args.qty is not None else (phrase_qty if phrase_qty is not None else 1)
    if qty < 1:
        fail("quantity must be 1 or more; use `set --line <id> --qty 0 --confirm` to remove a line")
    r = resolve(item)
    if not r:
        fail("the store has no filament matching %r" % item)
    if r["needs_color"]:
        colours = ", ".join(c["color"] for c in r["colors"] if c.get("color"))[:300]
        fail("%r matched %s, which needs a specific colour. Ask the user which colour%s"
             % (item, r["name"], (" (available: %s)" % colours) if colours else ""))
    if not r["in_stock"]:
        fail("%s is out of stock" % r["name"])
    cart_add(r["productSkuId"], r["productId"], qty)
    c = cart_query()
    ok(added=r["name"], quantity=qty, item_count=c["item_count"], total=c["total"],
       lines=c["lines"])


def cmd_set(args):
    # Changes the user's real cart; --qty 0 removes the line. Guard first.
    if not args.confirm:
        verb = "removes a line from" if args.qty == 0 else "changes"
        fail("set %s the user's real Bambu cart. " % verb + CONFIRM_HINT)
    if args.qty < 0:
        fail("quantity must be 0 or more; 0 removes the line")
    before = {l["line_id"]: l for l in cart_query()["lines"]}
    if str(args.line) not in {str(k) for k in before}:
        fail("there is no cart line %s; run `cart` to list the current lines" % args.line)
    cart_modify(args.line, args.qty)
    c = cart_query()
    ok(action=("removed" if args.qty == 0 else "quantity_changed"),
       line_id=str(args.line), quantity=args.qty,
       item_count=c["item_count"], total=c["total"], lines=c["lines"])


def cmd_checkout(args):
    d = checkout_preview()
    ok(subtotal=d.get("subTotal"), discount=d.get("discountPrice"),
       shipping=d.get("shipping"), tax=d.get("taxDetail"),
       grand_total=d.get("grandTotal"), amount_due=d.get("needPay"),
       charged=False,
       note="This is a preview only. Nothing has been charged. The user completes "
            "payment at us.store.bambulab.com/cart.")


@guard
def main():
    ap = ArgumentParser(prog="store_api.py", description="Bambu US store account, cart and order total.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    sub.add_parser("cart").set_defaults(fn=cmd_cart)

    p = sub.add_parser("search")
    p.add_argument("--item", required=True, help="material and colour, e.g. 'ASA white'")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("add")
    p.add_argument("--item", required=True, help="material and colour, e.g. 'PLA Matte Ash Grey'")
    p.add_argument("--qty", type=int, help="how many rolls (default 1)")
    p.add_argument("--confirm", action="store_true", help="required; the cart is the user's real cart")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("set")
    p.add_argument("--line", required=True, help="line_id from `cart`")
    p.add_argument("--qty", type=int, required=True, help="new quantity; 0 removes the line")
    p.add_argument("--confirm", action="store_true", help="required; the cart is the user's real cart")
    p.set_defaults(fn=cmd_set)

    sub.add_parser("checkout").set_defaults(fn=cmd_checkout)

    args = ap.parse_args()
    try:
        args.fn(args)
    except StoreError as e:
        fail(e)


if __name__ == "__main__":
    main()
