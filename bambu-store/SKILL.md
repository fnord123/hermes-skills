---
name: bambu-store
description: >
  Bambu Lab US store FILAMENT — live price, sale, and in-stock status, and
  building an order (add/adjust a cart on the user's real Bambu account, then
  show the exact grand total incl. tax + shipping). PREFER THIS SKILL over web
  search or the browser whenever the user asks about Bambu filament
  price/stock/sale or wants to order Bambu filament. Activate on phrasings
  like: "check the Bambu store", "how much is PLA Basic white", "is ASA white
  in stock", "any Bambu filament on sale", "order two rolls of X", "add Y to my
  Bambu cart", "what would my Bambu order cost". FILAMENT ONLY — accessories /
  spare parts are out of scope. The skill builds the order and shows the total;
  the USER completes payment themselves at the store (the skill never charges).
---

# bambu-store

Order Bambu Lab **filament** from Discord against the real US store
(`us.store.bambulab.com`). Deterministic CLIs do the work and return text/JSON;
you parse intent, confirm items in words, and build the cart. **Never** scrape
the store, web-search it, compute prices, or build URLs yourself.

Two CLIs, invoked by full path (cwd is not the skill dir):

- **Reads (no login needed):** `python3 ~/.hermes/skills/bambu-store/scripts/bambu-store-v2`
- **Account / cart / total (logged-in):** `python3 ~/.hermes/skills/bambu-store/scripts/store_api.py`

```
# price / stock / sale (read-only, always available)
python3 ~/.hermes/skills/bambu-store/scripts/bambu-store-v2 search "ASA white"
python3 ~/.hermes/skills/bambu-store/scripts/bambu-store-v2 price  "PLA Basic Jade White"

# build the order on the user's account
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py search   "<material color>"   # find + ids
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py add      "<material color>" <qty>
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py cart                          # show cart lines
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py set      <cartItemId> <qty>    # change qty; 0 removes
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py checkout                       # grand total + tax + shipping
python3 ~/.hermes/skills/bambu-store/scripts/store_api.py whoami                         # confirm the session is live
```

## How to handle requests

**Price / stock / sale (default, read-only).** Use `bambu-store-v2 search`/`price`.
Report per item: in stock?, unit price, and the `was` price if on sale. Then
STOP — don't touch the cart unless the user asks to order.

**Order me X / add X.** Include the **color in the query** ("ASA white", "PLA
Matte Ash Grey") — the search resolves color→variant. Confirm the item in words
(material + color + price) and the quantity, then `store_api.py add "<query>" <qty>`.
If `add` reports it needs a color, ask for the specific color. Out-of-stock
items can't be ordered.

**Show / adjust the order.** `store_api.py cart` lists each line with its
`cartItemId`, variant, qty, and price. To change a quantity or remove a line,
`store_api.py set <cartItemId> <qty>` (0 removes). Confirm removals in words.

**What's my total / ready to buy.** `store_api.py checkout` returns the real
grand total including tax and shipping (no charge). Read the total back, then
tell the user the order is ready in their account and to **complete payment at
`us.store.bambulab.com/cart`** (the skill does not pay).

## Notes
- Confirm items and quantities in plain words (color + material), never raw ids.
- The cart is the user's **real** account cart — adding/removing changes it.
- If a logged-in command reports an auth/token error, the store session token
  has expired; tell the user it needs refreshing (see README). Reads
  (`bambu-store-v2`) keep working regardless.
- FILAMENT ONLY. If asked for parts/accessories, say it's out of scope.
