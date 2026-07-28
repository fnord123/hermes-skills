# bambu-store skill

Order Bambu Lab **filament** from Discord against the **real** US store
(`us.store.bambulab.com`, JSON API `us-store-api.bambulab.com`). Reads are
public; cart/checkout use the logged-in account.

## Architecture
- **`scripts/bambu-store-v2`** — token-free reads (search/price/stock/sale) via the
  store search API. Always works.
- **`scripts/store_api.py`** — authenticated client (account, cart CRUD, checkout
  preview, search→add resolver). Pure JSON API, no browser. Auth =
  `authorization: Bearer TC <storeJWT>` + `x-bbl-account-identity: PERSONAL`.
  Emits one JSON object per call via the vendored `scripts/skill_json.py`.
- The skill **builds the order and shows the exact grand total** (incl. tax +
  shipping). It does **not** charge — the user completes payment themselves at
  `us.store.bambulab.com/cart`.

## Why the model is told to use only these two commands
The store's own pages are Cloudflare-gated and its prices are computed
server-side per account (points, promotions, regional tax). A model that
scrapes the storefront, web-searches for a price, does its own arithmetic on a
subtotal, or hand-builds a cart/checkout URL will produce a number that looks
right and is wrong — and the user then pays that number. Every price and total
in this skill therefore comes back from the store's own API, unmodified. That
is also why SKILL.md never exposes a URL-building step.

## Footgun guards
`store_api.py add` and `store_api.py set` write to the user's real account cart,
and `set --qty 0` is a deletion. Both refuse to run without `--confirm`, and the
refusal happens before any network call, so a hallucinated invocation cannot
mutate the cart. `--confirm` is only ever passed after the user has approved
that exact change in words.

`store_api.py` also refuses to infer a quantity silently: "2 rolls of PLA Basic"
is parsed into item `PLA Basic` at quantity 2 rather than being searched
literally, and a leading count that disagrees with an explicit `--qty` is an
error rather than a guess.

## Store token (required for cart/checkout; expires ~weekly)
`store_api.py` reads the token from (first found):
`$BBL_STORE_TOKEN`, then `~/.hermes/cache/bambu-store/store_token.txt`.
The token is a **live account secret** — the file is mode 600 and the path is
gitignored. Never print it. There is deliberately no `/tmp` fallback: a
world-writable path is plantable by any local user, and a planted token would
send the account's cart operations to an attacker-chosen session.

**To refresh when it expires** (commands report a sign-in error):
1. In a browser logged into `us.store.bambulab.com`, open DevTools → Network.
2. Filter `cart/size` (or any `us-store-api` request), reload.
3. Copy the request's `authorization` header value — everything after
   `Bearer ` (it starts with `TC `).
4. Write it to `~/.hermes/cache/bambu-store/store_token.txt` (that one line only).
5. Verify: `python3 ~/.hermes/skills/bambu-store/scripts/store_api.py whoami`
   → should show `account_type: BAMBU_LAB`.

When the token expires, cart/checkout commands report a sign-in error and stop.
Reads (`bambu-store-v2`) keep working regardless. Note that an expired token is
now reported as an error rather than being swallowed into an empty search
result — previously an expired session, a DNS failure and a genuinely absent
product were indistinguishable, all surfacing as "no match".

Automating this refresh headlessly is unsolved — the store mints the JWT on the
home page, which crashes the camoufox FF driver. See the project handoff.

## Payment (not automated)
The charge step is intentionally **not built** and no payment code ships with
this skill. The skill stops at "order ready, grand total $X" and hands off to
the user to pay.

Earlier Shopify-era payment prototypes (`checkout-pay.py`, `checkout-review.py`)
were removed: they drove a Stripe **Link** virtual card against
`bambulab-us.myshopify.com`, which is not the store this skill uses, and Hermes
announces everything under `scripts/` to the model — so a hallucinated call
could have raised a real spend request. They are recoverable from git history if
the payment path is ever revisited. Any future version must stay double-gated
(chat "go ahead" + Link-app approval, $500 cap), must never default its spend
`--context` to a string that asserts approval it cannot verify, and must never
continue toward the pay step after a failed navigation or card-fill.

Two write-capable discovery harnesses were also removed for the same reason:
`cart_ops.py` (added to the real cart and explicitly did not restore it) and
`store_cart.py` (in-page fetch cart writes, hand-parsed `--add SKU:COUNT`, no
guard).

## Other scripts
`scripts/` also holds a handful of browser-based login/capture probes left over
from the reverse-engineering phase. They are deliberately **not** listed by name
here or in SKILL.md: `hermes skills install` copies "SKILL.md plus the exact
local files it references", so naming a scratch script in either document is
what ships it to the model's reach. The live skill uses only `bambu-store-v2`
and `store_api.py`, and both are stdlib-only.
`scripts/requirements.txt` declares the third-party packages
(playwright, playwright-stealth, camoufox) the remaining probes import.

## Reference
Full reverse-engineering notes, API contracts, and remaining work:
`~/bambu-store-project/docs/HANDOFF.md`.
