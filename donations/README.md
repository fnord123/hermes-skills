# donations

Log itemized charitable donations for the Hermes agent. The user says what they
donated ("three pairs of pants at $5 each"); the agent calls one small CLI and
the item lands on a per-charity donation with a running total — no spreadsheet
thinking required of the model.

Backed by a Google Sheet, but that's an implementation detail: the model-facing
surface (`SKILL.md` + the CLI's verbs/output) speaks only in donations. This
README is the human/developer side — setup, the backend, and rationale.

## What this is for

Recording donated goods by voice or chat:
- *"Start a new Goodwill donation."*
- *"Add three pairs of pants at $5 each."*
- *"Add another pair of pants."*
- *"What's the total?"*

## What this is NOT for

- **Cash donations** — the sheet's separate cash tab is a different shape; the
  skill only handles itemized goods (quantity × per-item value).
- **General spreadsheet access** — this is a donation-domain skill, not a Sheets
  client. The low-level Sheets CLI it grew out of is kept as internal
  infrastructure and is intentionally NOT exposed to the model.

## How it works

```
donations.py <verb>
   │  (re-execs into the Hermes venv for google-api-python-client)
   ▼
Google Sheets API v4  — service-account auth (ADC via GOOGLE_APPLICATION_CREDENTIALS)
   ▼
"<YYYY-MM-DD> <Charity> Donation" tab in the configured spreadsheet
   │  header + per-item =B*C product formula + =sum(D2:D) total + currency format
   ▼
one JSON object on stdout (donation / item / quantity / value_per / total)
```

Each donation is one tab laid out like:

| | Quantity | Value Per | Product | Total: | =sum(D2:D) |
|---|---|---|---|---|---|
| Pants | 3 | $20.00 | =B2*C2 | | |

The script owns all of that — creating the tab, the product formula, the
running-total formula, and currency formatting — so the agent never sees a cell
or a range. The **active donation** (the one `add`/`total`/etc. target) is
tracked in `~/.config/donations/active.txt`, written by `new`/`use`.

### Identity & attribution (why a service account)

Runtime auth is a **Google Cloud service account**, not user OAuth — OAuth
3-legged flows carry the "agentic use → account suspension" risk; a service
account is a non-human identity metered against a GCP project with no user to
suspend. It acts as *itself* (no domain-wide delegation); the sheet is reached
by **sharing it** to the service account's address as Editor. Edits appear in the
sheet's revision history attributed to the service account — the intended split
that keeps the owner's personal Google account insulated. (Full provisioning
history for this install lives in the project handoff, kept outside the repo.)

## Setup

### 1. Provision the service account (one-time)

Create (or reuse) a GCP project, enable the **Sheets API**, create a service
account, and mint a JSON key. Install the key on the host at
`~/.hermes/creds/hermes-sheets.json` (chmod 600) and point ADC at it in
`~/.hermes/.env`:

```
GOOGLE_APPLICATION_CREDENTIALS=/home/<user>/.hermes/creds/hermes-sheets.json
```

The Hermes venv (`~/.hermes/hermes-agent/venv/bin/python`) already has
`google-api-python-client` + `google-auth`; the script re-execs into it, so no
system `pip install` is needed.

### 2. Share the spreadsheet

Share the "Charitable Donations" spreadsheet with the service account's address
(`…@<project>.iam.gserviceaccount.com`) as **Editor**. Sharing to a service
account triggers a "no Google account associated" prompt — proceed; the notify
email bounces harmlessly and the share still applies.

### 3. Configure the sheet id

```bash
mkdir -p ~/.config/donations
echo 'DONATIONS_SHEET_ID=<spreadsheet-id-from-its-URL>' > ~/.config/donations/config.env
chmod 600 ~/.config/donations/config.env
```

(See `examples/config.env.example`.)

### 4. Wire the skill into Hermes

```bash
ln -s ~/hermes-skills/donations ~/.hermes/skills/donations
hermes skills list        # confirm it's discovered
```

### 5. Test

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
$PY ~/.hermes/skills/donations/examples/donations.py new --charity "Test" --date 2099-01-01
$PY .../donations.py add --item pants --quantity 3 --value 5
$PY .../donations.py total       # expect {"ok": true, ... "total": "$15.00"}
# then delete the test tab in the sheet
```

## The verbs

`new` · `add` · `more` · `total` · `show` · `use` — see [`SKILL.md`](./SKILL.md)
for the model-facing contract and the word→call mapping. Each prints one JSON
object; failures are `{"ok": false, "error": "…"}` with exit 1.

## Design notes

- **Domain skill, not primitives.** The user's workflow is donation logging, so
  the skill exposes donation verbs and hides the Sheets mechanics entirely,
  rather than handing the model a generic read/write-cells tool.
- **Merge, don't duplicate.** Adding an item already on the donation increments
  its quantity. If the stated per-item value differs from the recorded one, the
  script keeps the original value and returns a `warning` — it never silently
  changes a price.
- **New donations only.** `new` creates a fresh tab; it never edits the sheet's
  existing structure. To reach a different spreadsheet (e.g. a new tax year),
  point `DONATIONS_SHEET_ID` at it — no code change.

## Files

| Path | What |
|---|---|
| `SKILL.md` | Model-facing contract (donation vocabulary only). |
| `examples/donations.py` | The CLI. |
| `examples/config.env.example` | Template for `~/.config/donations/config.env`. |
| `~/.config/donations/config.env` | `DONATIONS_SHEET_ID` (not in the repo). |
| `~/.config/donations/active.txt` | The active-donation pointer (runtime state). |
| `~/.hermes/creds/hermes-sheets.json`, `~/.hermes/.env` | Service-account key + ADC path (not in the repo). |

## License

MIT.
