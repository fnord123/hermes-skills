---
name: square-appointments
description: 'Appointments for the USER at their own pre-configured local businesses — hair salon,
  barbershop, dentist, trainer, anywhere they hold a Square service-business account. Finds open slots,
  books, reschedules and cancels. PREFER THIS SKILL for any appointment task about the user''s own
  businesses, even when the business name is misspelled or approximate. Use `pallo-logistics` instead
  for anything involving the dog or a kennel stay — this skill books people, that one books the dog.
  Use `calendar` instead to read what is already scheduled without changing anything. Activate on
  any of: "book a haircut", "schedule an appointment", "when is my next appointment", "cancel my appointment",
  "reschedule my", "any openings at", "what time can I get in", "move my appointment", "book me at
  <business>", "do they have anything Thursday".'
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags:
    - Appointments
    - Booking
    - Square
    - Calendar
    - Scheduling
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
- **Phonetic / sound-alike homonyms**: say each candidate alias out loud
  and ask "does this sound like what the user said, even if it's spelled
  very differently?" For instance, "Dhoraso Brothers" sounds like
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

## The six tools

All scripts live at `${HERMES_SKILL_DIR}/scripts/`. Invoke
each via `python3 <path> <args>`. Each returns small structured output (JSON
lines or plain text) the agent can relay to the user directly.

| Script | Purpose | Mutating? |
|---|---|---|
| `list-merchants.py` | Show the configured merchant aliases. | No |
| `customer-info.py show` | Confirm the user's contact info (phone/name/email) is configured for booking. Redacted output safe for chat. | No |
| `square-list.py --merchant <alias> [--days-back <N>] [--days-ahead <N>]` | List the user's appointments at one merchant, parsed from their confirmation emails. Defaults to upcoming only (60 days ahead, 0 back). Pass `--days-back 90` (or higher) when the user asks about *past* appointments. | No |
| `square-find-slot.py --merchant <alias> --around <date-or-relative>` | Check for collision with existing booking; if none, return up to 5 available slots near the target date. **⚠ Slow — loads Square's booking widget in a real browser. Always call the terminal with `timeout=600`; the default 60s is too short and will kill it before it finishes.** | No |
| `square-book.py --merchant <alias> --slot-handle '<json>' --confirm-date <ISO> --confirm-time '<HH:MM AM/PM>' --confirm [--note <text>]` | Book the slot that find-slot emitted. Pass through `slot_handle` opaquely. `--confirm-date`, `--confirm-time` and `--confirm` are safety invariants. See "Booking safety" below. | Yes |
| `square-cancel.py --merchant <alias> --booking-handle '<URL>' --confirm-date <ISO> --confirm-time '<HH:MM AM/PM>' --confirm` | Cancel a specific existing booking. `--confirm-date`, `--confirm-time` and `--confirm` are all required. See "Cancellation safety" below. | Yes |

## The `--confirm` flag (required for both mutating tools)

`square-book.py` and `square-cancel.py` refuse to do anything without
`--confirm` and tell you so. Pass it ONLY in the same turn in which the
user has explicitly approved that exact appointment ("yes, book the 1:15
PM slot", "yes, cancel Tuesday's"). Never add `--confirm` to make an
error message go away.

Both tools also accept `--dry-run`, which verifies everything and changes
nothing. `--dry-run` needs no `--confirm`.

## The opaque-handle contract

`booking_handle` and `slot_handle` are emitted by `square-list.py` and
`square-find-slot.py`. They contain bearer tokens (in the booking case) or
internal browser state (in the slot case).

- Treat them as **opaque**. Pass them through verbatim. Never construct,
  guess, decode, or trim them.
- Never URL-fetch a `booking_handle` yourself, even if it looks like a URL.
  Use the scripts.

## The `--confirm-date` + `--confirm-time` invariant (the safety pattern)

`square-book.py` and `square-cancel.py` both require `--confirm-date`
(ISO, e.g. `2026-06-24`) and `--confirm-time` (display time, e.g.
`1:15 PM`) for the appointment you intend to act on, taken from
`square-list.py` or `square-find-slot.py` output.

Pass both, always. The script re-reads the appointment from Square and
**refuses to mutate if its read disagrees**. The date is what tells two
same-time appointments apart — times repeat every day.

`--confirm-time` must include AM or PM. A time without it is refused.
Split `square-list.py`'s `start_time_iso` into the two flags:

| From square-list.py | Becomes |
|---|---|
| `"start_time_iso": "2026-06-18T14:00:00"` | `--confirm-date 2026-06-18 --confirm-time "2:00 PM"` |

`square-find-slot.py` gives them already split, as `date` and `label`
inside each slot.

When the user's request is ambiguous about which appointment to cancel
or rebook (e.g. they have several), call `square-list.py` first, ask the
user to confirm which one, then proceed.

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
**Always run `square-find-slot.py` with `timeout=600` in the terminal call — the browser needs up to a few minutes on slow days.**
Response shapes; relay each to the user differently:

- **`status="already_have"`**: user already has an appointment within
  ±7 days of the target. Tell them when it is, and ask whether they want
  to keep it or replace it, rather than booking a second one.
- **`status="error"`**: the check for existing appointments could not be
  completed, so no slots were searched. Tell the user what the `reason`
  says and stop — booking now could give them two appointments.
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
square-list.py --merchant sugarmama        # find the appointment
→ confirm the exact date and time with the user
square-cancel.py --merchant sugarmama \
    --booking-handle <handle from list> \
    --confirm-date 2026-06-18 \
    --confirm-time "2:00 PM" \
    --confirm
```

### "Move my sugarmama appointment to next Thursday at 3pm."
This is a two-step operation: take the new time first, then release the
old one. Tell the user that's what you're doing.
```
square-list.py --merchant sugarmama        # find the current appointment
square-find-slot.py --merchant sugarmama --around 2026-06-25
→ ask the user which of the returned slots they want
square-book.py --merchant sugarmama \
    --slot-handle '<the JSON from find-slot output>' \
    --confirm-date 2026-06-25 --confirm-time "3:00 PM" --confirm
→ only once that returns status="booked":
square-cancel.py --merchant sugarmama \
    --booking-handle <handle of the OLD appointment> \
    --confirm-date <old date> --confirm-time "<old time>" --confirm
```
If the booking step returns anything other than `booked`, stop and tell
the user — leave the original appointment alone.

### "Yes book the 1:15 PM slot."
After the user explicitly confirms (don't preempt this):
```
square-book.py --merchant derosso \
    --slot-handle '<the JSON from find-slot output>' \
    --confirm-date 2026-06-30 \
    --confirm-time "1:15 PM" \
    --confirm
→ relay back the confirmed date/time, service name, $ due at appointment,
  cancel-by deadline. Remember the booking_handle for a later cancellation.
```
If `customer-info.py show` returns `configured: false`, refuse to book
and tell the user they need to set their contact info first
(`customer-info.py set --field <name> --value <val>` for each of
`phone`, `first_name`, `last_name`, `email`, `phone_country_code`).

## Booking safety (square-book.py)

Booking creates a real appointment on the merchant's calendar — real
money / a real commitment. Four rules:

1. **Never call `square-book.py` without the user's explicit
   confirmation in the same turn** (e.g. "yes, book that one", "go
   ahead and book the 1:15 PM slot"). Showing slots via find-slot is
   read-only; booking is not.
2. **Pass `--confirm`.** Booking without it is refused.
3. **Always pass `--confirm-date` and `--confirm-time`** matching what
   the user said. The script verifies these against the displayed
   appointment summary and refuses if they disagree — that's what keeps
   the model from booking the wrong slot.
4. **Use `--dry-run` if the user asks you to "check what would happen"
   or "show me the form"** but hasn't said "book it." The dry-run goes
   all the way through filling fields but stops short of Submit, and
   needs no `--confirm`.

The script returns one of these statuses:

| Status | What it means | What to tell the user |
|---|---|---|
| `booked` | Real booking landed and was verified on the page. `booking_handle` is the manage URL. | Echo the date, time, service, due-at-appointment amount, cancel-by deadline. Save the booking_handle for a later cancellation. |
| `dry_run_ok` | Fields filled, would submit. Nothing was booked. | "Here's what would be submitted; ready to actually book?" |
| `submit_failed` | The submit did not go through. **Nothing was booked.** | Say plainly that the appointment was NOT booked, and give the `reason`. Ask the user whether to try again. |
| `uncertain` | The submit was sent but could not be verified. The appointment may or may not exist. | Say it is unconfirmed. Run `square-list.py` for that merchant to check before doing anything else. **Never call it booked.** |
| `card_required` | Merchant requires a card on file (Sugar Mama and similar). Script did NOT submit. | Tell the user the merchant needs a card; surface the `checkout_url`, the amount, and the cancellation policy. Ask them to finish in their browser. **Do not pretend you booked.** |
| `confirm_mismatch` | Asserted date/time didn't match the appointment summary. Nothing was booked. | Stop and re-check with the user — something is off. |
| `error` | Something else broke. Nothing was booked. | Surface the `reason`; do NOT silently retry. |

## Cancellation safety (square-cancel.py)

Canceling removes a real appointment and this skill cannot undo it.

1. **Only after the user explicitly says to cancel that specific
   appointment** — get the date and time from `square-list.py` first.
2. **Pass `--confirm`, `--confirm-date` and `--confirm-time`.** All three
   are required; the script refuses without them.
3. **Use `--dry-run`** to check that the manage page really shows that
   appointment before committing.

| Status | What it means | What to tell the user |
|---|---|---|
| `canceled` | The appointment is canceled. | Confirm what was canceled, with its date and time. |
| `dry_run_ok` | The manage page shows this exact appointment. **Nothing was canceled.** | "This is the one — say the word and I'll cancel it." |
| `uncertain` | The clicks went out but the result could not be read. | Say it is unconfirmed, then run `square-list.py` to see whether it is gone. |
| `already_passed` / `outside_window` | Square will not cancel it. | Relay the `detail`; suggest contacting the merchant. |
| `confirm_mismatch` | The manage page shows a different appointment. **Nothing was canceled.** | Stop. Re-check which appointment the user means. |
| `error` | Something else broke. Nothing was canceled. | Surface the `reason`; do NOT silently retry. |

## Error handling

Every script prints one JSON object. A failure carries either
`{"ok": false, "error": "…"}` or a `status` of `error` /
`submit_failed` / `uncertain` / `confirm_mismatch` with a `reason`.

- Relay the `error` or `reason` text to the user as-is. It says what
  happened and, for the mutating tools, whether anything changed.
- Never retry a mutating call after a failure. Never re-run
  `square-book.py` or `square-cancel.py` with different arguments to get
  past an error.
- If a script reports `status: "token_expired"` or
  `status: "manage_link_dead"`, tell the user: "The manage-booking link
  from Square expired — please cancel or reschedule via the email
  confirmation directly."
- If a status says the outcome is unconfirmed, run `square-list.py` for
  that merchant and report what it shows.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
