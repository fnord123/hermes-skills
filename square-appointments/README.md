# square-appointments

Manage your Square appointments at a small, hand-configured set of merchants,
through Hermes chat (e.g. Discord). Read-only by default; the two mutating
operations (cancel, move) require a confirmation-time invariant.

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
| Cancel | Playwright loads the bearer-token manage URL → verifies the displayed start time matches `--confirm-time` → clicks Cancel → confirms |
| Move | Prefer Square's reschedule UI if exposed (single transaction). Fallback: book new slot first, then cancel old — never leave you with no booking if step 2 fails |

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
- **`--confirm-time` exact match.** Cancel and move both refuse to proceed
  if the script's own read of the manage page disagrees with the agent's
  asserted `--confirm-time`. This protects against the model acting on the
  wrong booking when there's ambiguity.
- **Move atomicity.** Where the manage-booking UI exposes a "Reschedule"
  flow, the move script uses it as a single transaction. Where it doesn't,
  the fallback is book-new-then-cancel-old (in that order). The script
  reports the partial state explicitly if step 2 fails, so you and the
  agent can recover.
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

1. **Five named scripts, not a meta-API.** The agent's tool surface is small
   and direct. No JSON-schema discovery dance like the Square MCP server.
2. **Opaque handles.** `booking_handle` and `slot_handle` contain values the
   model has no business reasoning about. The contract is "carry them
   verbatim."
3. **`--confirm-time` invariants.** If the model is confused about which
   booking, the time won't match the manage-page read, and the script
   refuses. Cheap correctness check at the boundary.

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
    ├── square-list.py             # CUJ #1
    ├── square-find-slot.py        # CUJ #2 (planned, post-validate)
    ├── square-cancel.py           # CUJ #3 (planned, post-validate)
    └── square-move.py             # CUJ #4 (planned, post-validate)
```

The two read-only scripts ship first. The mutating scripts land after
end-to-end validation of the read path against real data.
