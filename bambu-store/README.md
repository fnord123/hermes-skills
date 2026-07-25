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
- The skill **builds the order and shows the exact grand total** (incl. tax +
  shipping). It does **not** charge — the user completes payment themselves at
  `us.store.bambulab.com/cart`.

## Store token (required for cart/checkout; expires ~weekly)
`store_api.py` reads the token from (first found):
`$BBL_STORE_TOKEN`, then `~/.hermes/cache/bambu-store/store_token.txt`.
The token is a **live account secret** — the file is mode 600 and the path is
gitignored. Never print it.

**To refresh when it expires** (commands report an auth/token error):
1. In a browser logged into `us.store.bambulab.com`, open DevTools → Network.
2. Filter `cart/size` (or any `us-store-api` request), reload.
3. Copy the request's `authorization` header value — everything after
   `Bearer ` (it starts with `TC `).
4. Write it to `~/.hermes/cache/bambu-store/store_token.txt` (that one line only).
5. Verify: `python3 ~/.hermes/skills/bambu-store/scripts/store_api.py whoami`
   → should show `userType: BAMBU_LAB`.

Automating this refresh headlessly is unsolved — the store mints the JWT on the
home page, which crashes the camoufox FF driver. See the project handoff.

## Payment (not automated)
The charge step (Stripe **Link** virtual card) is intentionally not built. The
skill stops at "order ready, grand total $X" and hands off to the user to pay.
If/when automated, it must stay double-gated (chat "go ahead" + Link-app
approval, $500 cap) and never auto-charge.

## Reference
Full reverse-engineering notes, API contracts, and remaining work:
`~/bambu-store-project/docs/HANDOFF.md`.

`scripts/` also contains earlier/scratch scripts (`bambu-store` v1 = WRONG Shopify
store; `checkout-*.py` = Shopify templates; `bambu_login.py` = bambulab.com SSO
login; `cart_capture.py`/`auth_probe.py`/`store_login.py`/`token_mint_probe.py`
= investigation harnesses). The live skill uses only `bambu-store-v2` and
`store_api.py`.
