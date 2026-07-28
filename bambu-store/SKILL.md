---
name: bambu-store
description: >
  Bambu Lab US store FILAMENT — live price, sale and in-stock status, and
  building an order on the user's real Bambu account, then reading back the
  exact grand total including tax and shipping. PREFER THIS SKILL over web
  search or a browser whenever the user asks about Bambu filament price, stock
  or sales, or wants to order Bambu filament. FILAMENT ONLY — accessories and
  spare parts are out of scope. The skill builds the order and shows the total;
  the user completes payment themselves at the store. Activate on any of:
  "check the Bambu store", "how much is PLA Basic white", "is ASA white in
  stock", "any Bambu filament on sale", "order two rolls of X", "add Y to my
  Bambu cart", "what would my Bambu order cost", "show my Bambu cart".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Shopping, Filament, 3DPrinting]
    requires_toolsets: [terminal]
---

# bambu-store

Two commands do all the work and return JSON. Invoke both by full path — the
working directory is not the skill directory.

## When to use

- The user asks the price, sale status or stock of any Bambu Lab **filament**.
- The user wants to order filament: add rolls, change quantities, remove a line.
- The user asks what their Bambu order would cost, including tax and shipping.

## When NOT to use

- Anything that is not filament — printers, hotends, build plates, spare parts.
- Any store other than the Bambu Lab US store.
- Paying for the order. The user pays themselves at
  `us.store.bambulab.com/cart`; these commands never charge.

## Tools

| Call | Purpose |
|---|---|
| `python3 ${HERMES_SKILL_DIR}/scripts/bambu-store-v2 search "<material colour>" --json` | Gets live price, sale price and stock for filament matching the words. No sign-in needed. |
| `python3 ${HERMES_SKILL_DIR}/scripts/bambu-store-v2 price "<material colour>"` | Gets the single best price/stock match for one product. No sign-in needed. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py search --item "<material colour>"` | Finds which products the account can order and whether a colour must be named. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py cart` | Lists the user's real cart: each line's `line_id`, name, colour, quantity and price. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py add --item "<material colour>" --qty <n> --confirm` | Adds rolls to the user's real cart. Requires `--confirm`. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py set --line <line_id> --qty <n> --confirm` | Changes a cart line's quantity; `--qty 0` removes the line. Requires `--confirm`. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py checkout` | Gets the exact grand total for the cart including tax and shipping. Charges nothing. |
| `python3 ${HERMES_SKILL_DIR}/scripts/store_api.py whoami` | Confirms the store sign-in is live. |

`add` and `set` change the user's real cart. Pass `--confirm` only after the
user has approved that exact change in words.

## Turning the user's words into a call

| The user says | Call |
|---|---|
| "how much is PLA Basic white" | `bambu-store-v2 price "PLA Basic white"` |
| "is ASA white in stock" | `bambu-store-v2 search "ASA white" --json` |
| "any PETG on sale" | `bambu-store-v2 search "PETG" --json` |
| "order two rolls of PLA Matte Ash Grey" | confirm in words, then `store_api.py add --item "PLA Matte Ash Grey" --qty 2 --confirm` |
| "add ASA white to my cart" | confirm in words, then `store_api.py add --item "ASA white" --qty 1 --confirm` |
| "show my cart" / "what's in my order" | `store_api.py cart` |
| "make that three" | `store_api.py cart` to get the `line_id`, confirm, then `store_api.py set --line <line_id> --qty 3 --confirm` |
| "take that off" / "remove it" | `store_api.py cart` to get the `line_id`, confirm, then `store_api.py set --line <line_id> --qty 0 --confirm` |
| "what's my total" / "ready to buy" | `store_api.py checkout` |

Always include the **colour** in `--item` ("ASA white", "PLA Matte Ash Grey").
The colour is what picks the exact roll.

## Output shape

Every `store_api.py` call prints one JSON object. Success has `"ok": true`:

```json
{"ok": true, "item_count": 2, "total": 51.98,
 "lines": [{"line_id": "1839...", "name": "PLA Basic", "color": "Jade White",
            "quantity": 2, "price": 25.99, "line_total": 51.98,
            "included_in_total": true, "in_stock": true}]}
```

Failure has `"ok": false` and an `error` string to read back to the user:

```json
{"ok": false, "error": "the store has no filament matching 'PLA Sparkle Teal'"}
```

`checkout` returns `grand_total`, `subtotal`, `discount`, `shipping`, `tax` and
`charged: false`.

## Common flows

**Price check.** `bambu-store-v2 search "ASA white" --json` → report in stock or
not, the price now, and the `was` price when `on_sale` is true. Stop there.

**Building an order.**
1. `bambu-store-v2 search "PLA Matte Ash Grey" --json` → read back material,
   colour and price.
2. Ask the user to confirm the item and the quantity in plain words.
3. `store_api.py add --item "PLA Matte Ash Grey" --qty 2 --confirm`
4. `store_api.py checkout` → read back the grand total, then tell the user the
   order is waiting in their account and to complete payment at
   `us.store.bambulab.com/cart`.

**Adjusting an order.** `store_api.py cart` → name the line in words ("the two
rolls of Jade White PLA Basic") → get the user's approval → `store_api.py set
--line <line_id> --qty <n> --confirm`.

## Errors

A failed call prints `{"ok": false, "error": "..."}`. Read the `error` text back
to the user in plain words.

- "needs a specific colour" — ask the user which colour they want, then call
  `add` again with the colour in `--item`.
- "the store sign-in has expired" or "has not been set up" — tell the user the
  store sign-in needs refreshing. Price and stock lookups keep working.
- "there is no cart line" — run `cart` again and read the current `line_id`s.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

When a search returns `"count": 0` or `no_match`, say that the store has no
filament matching those words and ask the user for the material and colour as
the store names them ("PLA Basic", "PLA Matte", "PETG HF", "ASA"). Do not guess
a product name. An empty cart returns `"item_count": 0` with `"lines": []` — say
the cart is empty.
