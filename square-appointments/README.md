# square-appointments

Manage your Square appointments at a small, hand-configured set of merchants,
through Hermes chat (e.g. Discord). Read-only by default; the two mutating
operations (book, cancel) require `--confirm` plus a date/time invariant.

## What this is, and what it isn't

This skill lets you ask Hermes things like:
- *"What appointments do I have at sugarmama?"*
- *"Find a slot at hairdresser around the 20th."*
- *"Cancel my dentist appointment on Tuesday."*
- *"Move my sugarmama appointment to next Thursday at 3."*

It does **not**:
- Discover new Square merchants for you ("find me any salon in Portland"). You
  pre-configure each merchant by hand.
- Hold a Square login. Square has no buyer-side OAuth and no unified buyer
  dashboard; the architecture leans on email instead.
- Pay anything. Out of scope here; possibly a future tie-in with Stripe Link.

## How it works under the hood (and why)

Square's buyer experience is email-anchored: every booking confirmation comes
with a "manage this booking" URL containing a bearer token. That URL is how
buyers cancel and reschedule. So this skill leans on email as the source of
truth and Playwright as the executor:

| Operation | Data path |
|---|---|
| List existing appts at a merchant | AgentMail REST → filter recent threads by sender/subject → parse start time + manage-link from `extracted_text` |
| Find an open slot near a date | Playwright navigates the merchant's public `book.squareup.com/.../services/<id>` URL → scrapes the day picker → returns up to 5 candidates |
| Book a slot | Playwright replays the booking flow to that slot → verifies the appointment summary against `--confirm-date` / `--confirm-time` → fills your contact details → submits → verifies the submit actually landed before reporting `booked` |
| Cancel | Playwright loads the bearer-token manage URL → verifies the displayed date + start time match `--confirm-date` / `--confirm-time` → clicks Cancel → confirms |
| Move | No dedicated script. Book the new slot first, then cancel the old one — that order never leaves you with no appointment if step 2 fails |

### Architectures we rejected, and why
- **Square Bookings REST API directly.** Square's OAuth is seller-side only —
  no buyer-side OAuth. To use the API on a buyer's behalf, the *merchant*
  would have to authorise our personal Square Developer app. Per-merchant
  cooperation is not realistic.
- **Square's official MCP server.** Same OAuth model, different interface
  shape. The MCP shape (3 meta-tools: `get_service_info`, `get_type_info`,
  `make_api_request`) is also a poor fit for small/local LLMs — every
  operation is a 3-round-trip discovery dance.
- **Buyer dashboard scrape at `app.squareup.com`.** Doesn't exist. Square's
  own docs are explicit: there is no unified buyer view across merchants;
  per-seller customer profiles only show that one seller's bookings.

## Setup

### One-time prerequisites
- AgentMail inbox: this skill assumes you've already configured `agentmail-lite`
  and have a working `AGENTMAIL_API_KEY` in `~/.hermes/config.yaml`.
- A Gmail (or other email provider) filter that forwards Square confirmation
  emails to your AgentMail inbox. Square sends from
  `noreply@messaging.squareup.com` and similar `*.squareup.com` addresses; a
  filter that matches any of those is the right shape.
- Python 3.10+ with `pip`. (Hermes' default.)
- Playwright + Chromium, installed in a venv inside `scripts/`:
  ```
  cd ~/.hermes/skills/square-appointments/scripts
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/playwright install chromium
  ```

### Skill config
```
cd ~/.hermes/skills/square-appointments/scripts
cp ../templates/.env.example .env
$EDITOR .env                       # paste your AGENTMAIL_API_KEY (same as Hermes uses)
mkdir -p ~/.config/square-appointments
cp ../templates/merchants.example.json ~/.config/square-appointments/merchants.json
$EDITOR ~/.config/square-appointments/merchants.json
```

### Per-merchant config

Each merchant gets one entry in `merchants.json` keyed by a short alias the
user will say to Hermes ("sugarmama", "hairdresser", "dentist"…):

```json
{
  "sugarmama": {
    "name": "The Sugar Mama",
    "booking_url": "https://book.squareup.com/appointments/<uuid>/location/<id>/services",
    "default_service_id": "<service-id from drilling into the service URL>",
    "sender_match": ["messaging.squareup.com"],
    "subject_match": ["Sugar Mama"]
  }
}
```

- **`name`**: human-readable. Shown back to the user; also used as a fallback
  subject-match if `subject_match` isn't set.
- **`booking_url`**: copy from the merchant's "Book Online" link.
- **`default_service_id`**: open the booking URL in a browser, click into the
  service you typically book, copy the trailing path segment from the resulting
  URL (e.g. `…/services/NASUF2RAB6R4VUENNHZR6BC3`). If you book several
  different services with this merchant, configure the most common one here;
  you can override per-call later.
- **`sender_match`**: list of substrings any of which must appear in the
  email's `from` field for it to count as a confirmation from this merchant.
  Default `["messaging.squareup.com"]` will catch most Square mail.
- **`subject_match`**: substrings any of which must appear in the subject.
  Defaults to `[name]` if absent.

### Verify
```
.venv/bin/python list-merchants.py
.venv/bin/python square-list.py --merchant sugarmama
```

You should see your upcoming bookings at that merchant.

## Operational notes & caveats

- **Bearer tokens in `booking_handle`.** The manage-booking URL in your
  confirmation email contains a token that gives whoever holds it the
  ability to cancel/reschedule. This is by Square's design (no login
  required for the buyer). The scripts treat handles as opaque and pass
  them around carefully; the agent never sees raw handles itself.
- **Token expiry.** Square's manage-booking tokens appear to remain valid
  until the appointment ends. If a script reports `token_expired`, you'll
  have to go through the email confirmation manually.
- **`--confirm-date` / `--confirm-time` exact match.** Book and cancel both
  refuse to proceed if the script's own read of the page disagrees with the
  agent's asserted date and time. This protects against the model acting on
  the wrong appointment when there's ambiguity. The date check verifies the
  year whenever Square renders one next to the date, and reports
  `date_year_verified: false` when it doesn't.
- **Move atomicity.** A move is book-new-then-cancel-old, in that order, run
  as two explicit steps. If the booking step returns anything other than
  `booked`, the original appointment is left alone.
- **UI churn.** Playwright selectors against `book.squareup.com` are
  inherently fragile — Square can redesign their flow without notice.
  If a script starts failing with selector errors, that's the cause;
  inspect the page and update the selectors in the script.
- **Find-slot can't always scrape time slots.** Square's date picker
  currently keeps its week-advance controls (`prior-week-button`,
  `next-week-button`) CSS-hidden in production. The script reaches the
  availability page and can click dates within the default visible window
  (this week + a few days ahead), but can't advance further from headless.
  When that happens, `square-find-slot.py` returns
  `status="no_collision_use_url"` and surfaces the booking URL so the user
  can pick a slot in their own browser. Collision detection (the
  "do I already have one?" half) is unaffected and works fully.
- **ToS gray zone.** Automating one's own buyer interactions on Square is
  in a gray zone with Square's site ToS. Personal-use automation against
  your own confirmation emails and the publicly-accessible buyer flows is
  in practice tolerated; bulk operation across many merchants is not.
  Stay personal-scale.

## Why the design philosophy is what it is

The hermes-skills repo design philosophy (per the top-level README) is that
local models "mis-select among large tool sets" and "hallucinate dangerous
calls," so skills should be "smaller, more prescriptive, and free of obvious
footguns." That informs three load-bearing choices here:

1. **Six named scripts, not a meta-API.** The agent's tool surface is small
   and direct. No JSON-schema discovery dance like the Square MCP server.
2. **Opaque handles.** `booking_handle` and `slot_handle` contain values the
   model has no business reasoning about. The contract is "carry them
   verbatim."
3. **`--confirm-date` + `--confirm-time` invariants.** If the model is
   confused about which appointment, the asserted date/time won't match the
   script's own read of the page, and it refuses. Cheap correctness check at
   the boundary. The date is required as well as the time because times
   repeat daily — two 1:15 PM appointments on different days are
   indistinguishable by time alone.
4. **`--confirm` on both mutating scripts.** Booking and canceling refuse to
   run without it, before opening a browser at all. `--dry-run` is the
   escape hatch for "show me what would happen" and needs no `--confirm`.
5. **Post-action verification.** `square-book.py` will not report `booked`
   unless the checkout step is actually gone or a confirmation marker is on
   the page; `square-cancel.py` only reports `canceled` on Square's specific
   post-cancel wording, not on the word "canceled" alone (which also appears
   in every merchant's cancellation *policy* text). Anything else comes back
   as `submit_failed` or `uncertain`, which the SKILL tells the agent to
   relay as "not confirmed."
6. **Collision checks fail closed.** If `square-find-slot.py` cannot read the
   user's existing appointments, it returns an error instead of an empty
   list — an empty list reads as "no conflict" and is how you end up with two
   appointments in the same week.

### Why SKILL.md pushes so hard on fuzzy merchant matching

The local model routing this skill tends to give up on a business name it
doesn't recognise and fall through to a web search, which is always the wrong
move here: the user is naming one of a handful of merchants they configured
themselves. Sound-alike misspellings ("Dhoraso Brothers" for "deRosso
Brothers") were the specific failure. SKILL.md therefore states the positive
rule — read `list-merchants.py`, match on spelling *and* sound, be aggressive
about claiming a match — without describing the failure itself, since naming a
model's failure mode in its own context tends to reproduce it.

## Files

```
square-appointments/
├── SKILL.md                       # agent-facing model context
├── README.md                      # this file
├── templates/
│   ├── .env.example
│   └── merchants.example.json
└── scripts/
    ├── requirements.txt           # playwright, playwright-stealth
    ├── list-merchants.py
    ├── customer-info.py
    ├── square-list.py             # CUJ #1 — list appointments
    ├── square-find-slot.py        # CUJ #2 — find an open slot
    ├── square-book.py             # CUJ #3 — book a slot
    └── square-cancel.py           # CUJ #4 — cancel an appointment
```

There is no move/reschedule script. A move is done as book-the-new-slot
then cancel-the-old-one, in that order, so a failure never leaves the user
with no appointment.
