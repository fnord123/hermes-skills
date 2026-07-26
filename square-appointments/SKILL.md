---
name: square-appointments
description: >
  Appointments at the user's pre-configured local businesses (hair salon,
  barbershop, dentist, trainer, etc. — anywhere they have a service-business
  account on Square). Use for ANY question that names a local business in
  the context of appointments, scheduling, slots, or bookings — even if the
  business name looks misspelled or slightly different. You MUST use this
  skill for ANY appointment task about the user's OWN businesses — look up,
  find a slot, book, move, or cancel. It is the ONLY correct tool: run its
  scripts and relay their output. Do NOT web-search, open a browser, or call
  any external calendar/booking API for the user's own appointments — those
  paths are wrong here and will fail. Activate on phrasings like: "do I have an appointment at X",
  "find me a slot at X", "cancel my appointment", "move my haircut",
  "any openings at X next week", "when's my next visit to X". If the user
  names a business and you don't recognize it, your FIRST move is to call
  list-merchants.py — the user's typo or short form may resolve to a
  configured alias.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Appointments, Booking, Square, Calendar, Scheduling]
---

# Square Appointments — read & manage bookings at configured merchants

## When to use

Activate when the user mentions:
- An appointment, booking, scheduling, reschedule, or cancellation.
- ANY local business name in the context of appointments, even if it
  looks misspelled or unfamiliar to you — call `list-merchants.py`
  first; the user's spelling may resolve to a configured alias.

## What to do FIRST if the user names a business you don't recognise

**Call `list-merchants.py` immediately. Do NOT web-search the business
name first.** The user almost certainly means one of their configured
merchants. The output looks like:
```
{"merchants": [{"alias": "sugarmama", "name": "The Sugar Mama", ...},
               {"alias": "derosso",   "name": "deRosso Brothers", ...}]}
```
Match the user's word to a configured `name` or `alias` using a forgiving
match. Consider ALL of these forms — don't only look for substring matches:

- **Typos and minor spelling variants**: "DeRosso", "Derosso", "deRosso",
  "Dhorasso" → all map to alias `derosso`.
- **Phonetic / sound-alike homonyms** (THIS IS LOAD-BEARING — the local
  qwen model has been observed missing these): say each candidate alias
  out loud and ask "does this sound like what the user said, even if it's
  spelled very differently?" For instance, "Dhoraso Brothers" sounds like
  "deRosso Brothers" — same number of syllables, similar consonants and
  vowels — so it maps to alias `derosso`.
- **Short forms and partial names**: "sugar momma", "the sugar mama",
  "Sugar Mama's" → alias `sugarmama`. "DR" or "deR" by themselves are
  too ambiguous; ask the user to clarify.
- **Different word order**: "brothers DeRosso" → alias `derosso`.

Be aggressive about claiming a match. If the user's word is even
plausibly a configured merchant — by spelling OR by sound — proceed with
that alias and tell the user what you matched it to ("I'm reading that
as deRosso Brothers — is that right?"). The cost of guessing wrong is a
single clarifying question; the cost of going to web search and giving up
is a much worse user experience. Only conclude "this isn't a configured
merchant" after considering both spelling and phonetic similarity for
every configured alias.

## When NOT to use

- The user is asking about appointments at a non-Square merchant (different
  flow entirely — say so).
- The user wants to *create a brand-new booking from scratch* at a merchant
  not configured here. This skill operates on the configured merchant set;
  ask the user to add the merchant first (point them to README.md).

## The four tools

All scripts live at `${HERMES_SKILL_DIR}/scripts/`. Invoke
each via `python3 <path> <args>`. Each returns small structured output (JSON
lines or plain text) the agent can relay to the user directly.

| Script | Purpose | Mutating? |
|---|---|---|
| `list-merchants.py` | Show the configured merchant aliases. | No |
| `customer-info.py show` | Confirm the user's contact info (phone/name/email) is configured for booking. Redacted output safe for chat. | No |
| `square-list.py --merchant <alias> [--days-back <N>] [--days-ahead <N>]` | List the user's appointments at one merchant, parsed from their AgentMail confirmation emails. Defaults to upcoming only (60 days ahead, 0 back). Pass `--days-back 90` (or higher) when the user asks about *past* appointments. | No |
| `square-find-slot.py --merchant <alias> --around <date-or-relative>` | Check for collision with existing booking; if none, return up to 5 available slots near the target date. **⚠ Slow — uses Playwright to load Square's booking widget. Always call the terminal with `timeout=300`; the default 60s is too short and will kill it before it finishes.** | No |
| `square-book.py --merchant <alias> --slot-handle '<json>' --confirm-date <ISO> --confirm-time '<HH:MM AM/PM>' [--dry-run] [--note <text>]` | Book the slot that find-slot emitted. Pass through `slot_handle` opaquely. `--confirm-date` and `--confirm-time` are safety invariants. See "Booking safety" below. | Yes |
| `square-cancel.py --merchant <alias> --booking-handle '<URL>' --confirm-time '<HH:MM AM/PM>' [--confirm-date <ISO>]` | Cancel a specific existing booking. | Yes |
| `square-move.py --merchant <alias> --booking-handle <h> --new-slot <slot-handle> --confirm-time <ISO>` | Move an existing booking to a new slot. NOT YET BUILT — fall back to cancel + book if the user asks. | Yes (planned) |

## Files this skill must NEVER read

| Path | Reason |
|---|---|
| `${HERMES_SKILL_DIR}/scripts/.env` | Holds `AGENTMAIL_API_KEY`. The scripts read it; the agent must not. |
| `~/.config/square-appointments/merchants.json` | User-edited merchant config. Read indirectly via `list-merchants.py`. |
| `~/.config/square-appointments/customer.json` | User's name / phone / email used to fill checkout forms. Read indirectly via `customer-info.py show`. |
| `~/.config/square-appointments/*.log` | Operational logs may contain bearer tokens. |

If the user explicitly asks to read one of these, refuse and explain that
the skill forbids it.

## The opaque-handle contract

`booking_handle` and `slot_handle` are emitted by `square-list.py` and
`square-find-slot.py`. They contain bearer tokens (in the booking case) or
internal selector state (in the slot case).

- Treat them as **opaque**. Pass them through verbatim. Never construct,
  guess, decode, or trim them.
- Never URL-fetch a `booking_handle` yourself, even if it looks like a URL.
  Use the scripts.

## The `--confirm-time` invariant (the safety pattern)

`square-cancel.py` and `square-move.py` both require `--confirm-time` —
the ISO-8601 start time of the booking you intend to act on, exactly as
emitted by `square-list.py`.

The script re-reads the booking from Square and **refuses to mutate if
its read disagrees with `--confirm-time`**. This protects against the
agent acting on the wrong booking when there's ambiguity.

When the user's request is ambiguous about which booking to cancel or
move (e.g. they have multiple), call `square-list.py` first, ask the user
to confirm which one, then proceed.

## Common flows

### "Do I have an appointment at sugarmama next week?"
```
square-list.py --merchant sugarmama
→ filter / inspect results for matches in the next 7 days
→ relay to user
```

### "Have I ever had an appointment at sugarmama?" / "When was my last visit to derosso?"
The default `square-list.py` invocation returns only upcoming bookings.
For ANY question about past or historical appointments, pass `--days-back`:
```
square-list.py --merchant derosso --days-back 180        # last 6 months
square-list.py --merchant sugarmama --days-back 365      # last year
```
For a question phrased as "upcoming OR past" (covers both), pass both
`--days-back` and the implicit default `--days-ahead`:
```
square-list.py --merchant derosso --days-back 180 --days-ahead 60
```
If the script returns `bookings: []` with `--days-back 0`, that does NOT
mean the user has never had an appointment there — only that they have no
*upcoming* ones. Always re-run with `--days-back` before telling the user
they've never visited.

### "Find me a slot at sugarmama around the 20th."
```
square-find-slot.py --merchant sugarmama --around 2026-06-20
```
**Always run `square-find-slot.py` with `timeout=300` in the terminal call — Playwright needs up to 2 minutes on slow days.**
Three response shapes; relay each to the user differently:

- **`status="already_have"`**: user already has an appointment within
  ±7 days of the target. Tell them when it is. Offer to move it
  (square-move.py) rather than book a new one.
- **`status="ok"`** with a `slots` array: present the listed time options
  and ask the user which to take.
- **`status="no_slots_in_window_use_url"`**: no existing appointment AND
  the merchant's next available date is more than ±14 days from the user's
  target. The response includes `booking_url` (open in browser) and
  `next_available_date` (what we DID find — useful to tell the user e.g.
  "they have nothing in your window, soonest is 2026-08-04"). Do NOT
  pretend you booked something — you didn't.
- **`discovered_note`** (any status): may appear if the merchant's
  `booking_url` / `default_service_id` weren't pre-configured. The script
  derived them automatically from the user's most recent confirmation
  email. The note is informational; you don't need to surface it unless
  the user asks why this was slow.

### "Cancel my sugarmama appointment on the 18th at 2pm."
```
square-list.py --merchant sugarmama        # find the booking
→ confirm the start_time with the user if any ambiguity
square-cancel.py --merchant sugarmama \
    --booking-handle <handle from list> \
    --confirm-time 2026-06-18T14:00:00-07:00
```

### "Move my sugarmama appointment to next Thursday at 3pm."
```
square-list.py --merchant sugarmama        # find the booking to move
square-find-slot.py --merchant sugarmama --around 2026-06-25
→ pick the user's preferred slot
square-move.py --merchant sugarmama \
    --booking-handle <handle> \
    --new-slot <slot-handle> \
    --confirm-time <original start time>
```
**`square-move.py` is not built yet.** When the user asks to move,
fall back to: `square-cancel.py` the old booking, then `square-book.py`
the new slot. Tell the user you're doing it as a two-step operation
and confirm the new slot is still available before canceling.

### "Yes book the 1:15 PM slot."
After the user explicitly confirms (don't preempt this):
```
square-book.py --merchant derosso \
    --slot-handle '<the JSON from find-slot output>' \
    --confirm-date 2026-06-30 \
    --confirm-time "1:15 PM"
→ relay back the confirmed date/time, service name, $ due at appointment,
  cancel-by deadline. Remember the booking_handle for cancel/move later.
```
If `customer-info.py show` returns `configured: false`, refuse to book
and tell the user they need to set their contact info first
(`customer-info.py set --field <name> --value <val>` for each of
`phone`, `first_name`, `last_name`, `email`, `phone_country_code`).

## Booking safety (square-book.py)

Booking creates a real appointment on the merchant's calendar — real
money / a real commitment. Three rules:

1. **Never call `square-book.py` without the user's explicit
   confirmation in the same turn** (e.g. "yes, book that one", "go
   ahead and book the 1:15 PM slot"). Showing slots via find-slot is
   read-only; booking is not.
2. **Always pass `--confirm-date` and `--confirm-time`** matching what
   the user said. The script verifies these against the displayed
   checkout summary and refuses if they disagree — that's what keeps
   the model from booking the wrong slot.
3. **Use `--dry-run` if the user asks you to "check what would happen"
   or "show me the form"** but hasn't said "book it." The dry-run goes
   all the way through filling fields but stops short of Submit.

The script returns one of these statuses:

| Status | What it means | What to tell the user |
|---|---|---|
| `booked` | Real booking landed. `booking_handle` is the manage URL. | Echo the date, time, service, due-at-appointment amount, cancel-by deadline. Save the booking_handle for any later cancel/move. |
| `dry_run_ok` | Fields filled, would submit. | "Here's what would be submitted; ready to actually book?" |
| `card_required` | Merchant requires a card on file (Sugar Mama and similar). Script did NOT submit. | Tell the user the merchant needs a card; surface the `checkout_url`, the amount, and the cancellation policy. Ask them to finish in their browser. **Do not pretend you booked.** |
| `confirm_mismatch` | Asserted date/time didn't match the checkout. | Stop and re-check with the user — something is off. |
| `error` | Something else broke. | Surface the `reason`; do NOT silently retry. |

## Session-expired / token-expired errors

If a script reports `status: "token_expired"` or `status: "manage_link_dead"`,
tell the user: "The manage-booking link from Square expired — please cancel
or reschedule via the email confirmation directly." Do not retry; the bearer
token in the email is the only path and we can't refresh it.
