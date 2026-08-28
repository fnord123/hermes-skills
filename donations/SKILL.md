---
name: donations
description: >
  Record the user's charitable donations — the itemized goods they give to a
  charity (Goodwill, Vietnam Veterans of America, etc.), each item with a
  quantity and a per-item dollar value, plus a running total for that donation.
  PREFER THIS SKILL whenever the user is logging things they donated: starting a
  donation, adding items with quantities and values, adding more of something
  already listed, or asking the running total. Call the simple donation verbs
  below and relay their results — do not compute totals or track anything
  yourself. Activate on any of: "donation", "donated", "goodwill", "vietnam
  veterans", "charitable", "tax deduction", "drop-off", "start a new … donation",
  "add N … at $X each", "add another …", "what's the total", or anything that
  sounds like itemizing donated goods with quantities and values.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Donations, Charity, TaxDeduction, Productivity]
---

# donations — record itemized charitable donations

Log the goods the user donates to a charity. Each charity donation is its own
record of items — item name, quantity, and value each — with a running total.
You work entirely through the donation verbs below; the tool does all the
bookkeeping, so you never compute totals or track state yourself.

## When to use

Activate when the user wants to:
- **Start** a donation for a charity ("start a new Goodwill donation").
- **Add** an item with a quantity and a per-item value ("three pairs of pants at
  $5 each").
- **Add more** of an item already listed ("add another pair of pants").
- Ask the **running total** ("what's the total so far?").
- **Read back** what's been logged ("what have I added?").

## When NOT to use

- **Cash donations.** This skill tracks only itemized goods (things with a
  quantity and a per-item value). If the user is logging a cash gift, tell them
  cash isn't handled here yet.
- **Anything that isn't logging donated goods.** Out of scope: general
  record-keeping, lists, and math.

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/donations.py`, invoked as
`python3 <path> <verb> [args]`. Each call prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with exit 1).

| Verb | Purpose |
|---|---|
| `new --charity <name> [--date YYYY-MM-DD]` | Start a new donation for a charity (date defaults to today). It becomes the **active** donation. |
| `add --item <name> --quantity <n> --value <per>` | Add an item to the active donation. If that item is already listed, its quantity is **merged** (added on) instead of duplicated — and if the stated value differs from the one already recorded, the response carries a `warning`. |
| `more --item <name> [--count <n>]` | Add more of an item already listed (raise its quantity by `n`, default 1). |
| `total` | Gets the active donation's running total. |
| `show` | List the active donation's items and total (for read-back). |
| `use --donation "<name>"` | Switch which donation is active (rarely needed). |

`add` / `more` / `total` / `show` act on the **active** donation — the one set by
the last `new` (or `use`). You almost never pass `--donation`; the tool
remembers which donation is in progress.

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to verbs BEFORE calling:

| User said | Call |
|---|---|
| "start a new Goodwill donation" / "…dated for today" | `new --charity Goodwill` |
| "start a Vietnam Veterans donation for July 3rd" | `new --charity "Vietnam Veterans of America" --date 2026-07-03` |
| "add three pairs of pants at $5 each" | `add --item pants --quantity 3 --value 5` |
| "add two girls' socks, value $2 each" | `add --item "girls socks" --quantity 2 --value 2` |
| "add another pair of pants" | `more --item pants` |
| "add two more pairs of pants" | `more --item pants --count 2` |
| "what's the current total?" | `total` |
| "what have I added / read it back" | `show` |

Parsing notes:
- **"N pairs of X" / "N X" → `--quantity N`, `--item X`.** A "pair"/"pairs" is
  just the count word — three pairs of pants is `--quantity 3`, not 6.
- **"$X each" / "value $X" → `--value X`.** Pass a plain number (`5`, not `$5`).
- **Value stated → `add`. No value, and the item is already listed → `more`.**
  That is the one distinction to get right.
- Pass the item as the user said it; the tool normalizes and matches it, so
  "pants", "Pants", "PANTS" all land on one item.
- Once the user has started a donation, every later "add / another / total"
  applies to it automatically — do NOT pass `--donation`.

## Output shape

- `new` → `{"ok": true, "donation": "2026-07-05 Goodwill Donation", "created": true, "total": "$0.00"}`
- `add` → `{"ok": true, "donation": "...", "action": "added"|"merged", "item": "Pants", "quantity": 4, "value_per": 5.0, "total": "$20.00"}`
- `more` → `{"ok": true, "action": "incremented", "item": "Pants", "quantity": 5, "total": "$25.00"}`
- `total` → `{"ok": true, "donation": "...", "total": "$24.00"}`
- `show` → `{"ok": true, "donation": "...", "items": [{"item":"Pants","quantity":"4","value_per":"$5.00"}, ...], "total": "$24.00"}`

Always echo the confirmation back (item, new quantity, and running total) so a
mis-heard word is caught immediately — e.g. "Added 3 Pants at $5 — total $15."

**If an `add` response includes a `warning` field, relay it verbatim.** It means
the item was already listed at a *different* per-item value; the tool merged the
quantity but kept the original value (it never silently changes a price). Tell
the user both values and that the value was left unchanged, so they can fix it
deliberately if they meant to.

## A typical session

```
"Start a new Goodwill donation, dated today."
  → new --charity Goodwill
  → "Started a Goodwill donation for today. Total $0.00."

"Add three pairs of pants at $5 each."
  → add --item pants --quantity 3 --value 5
  → "Added 3 Pants at $5 each. Total $15.00."

"Add another pair of pants."
  → more --item pants
  → "Pants now 4. Total $20.00."

"Add two girls' socks at $2 each."
  → add --item "girls socks" --quantity 2 --value 2
  → "Added 2 Girls Socks at $2 each. Total $24.00."

"What's the total?"
  → total
  → "$24.00."
```

## When a verb reports an error

- `"no active donation…"` → the user hasn't started one. Ask which charity, then
  run `new`. Don't guess.
- `"'<item>' isn't on the … donation yet…"` (from `more`) → they said "another
  X" but X isn't listed. Ask for its per-item value and use `add` instead.
- `"donation '<x>' not found"` → that donation doesn't exist; offer to start it
  (`new`) or `use` an existing one.
- Any credential/config error (`GOOGLE_APPLICATION_CREDENTIALS…`,
  `auth/build failed`, `DONATIONS_SHEET_ID not set`) → the skill isn't
  configured. Point the user to `README.md`; do NOT try to reach the data another
  way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`show` with an empty `items` array means nothing has been logged on that
donation yet — say so plainly ("nothing on this Goodwill donation yet"); don't
re-check or speculate.
