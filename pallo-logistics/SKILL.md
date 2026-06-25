---
name: pallo-logistics
description: >
  Pallo's boarding at Laurel Acres Kennels plus the Model X / Gina transport
  coordination that every kennel trip depends on. Use for ANY request about
  Pallo's kennel stays, boarding, drop-off / pickup, daily kennel activities
  (Play Yard, Nature Walk), or whether a trip's boarding is set up — e.g.
  "when is Pallo at the kennel", "is Pallo's boarding booked for my London
  trip", "book Pallo for my Paris trip", "cancel Pallo's July stay". Also use
  for "where is Gina on <date>" and the Model-X handoff messages that go with a
  booking. Trip dates come from the merged calendar (Kayak feed); the agent can
  pass a trip NAME and let the scripts resolve the dates.
  YOU MUST use this skill's scripts for ALL Pallo boarding tasks. Do NOT open a
  browser, do NOT web-search "Laurel Acres" or "Gingr", and do NOT try to
  navigate to any booking website — those paths will fail. The ONLY correct
  approach is to run the scripts listed below and relay their output.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Pets, Boarding, Gingr, Booking, Calendar, Travel]
    category: pets
---

# pallo-logistics — Pallo's boarding + Model-X coordination

All scripts live in `~/.hermes/skills/pallo-logistics/examples/`. Invoke each as
`python3 <path> <args>`; every script re-execs into its own `.venv` and prints
small JSON the agent relays to the user. Scope: **Pallo only, Laurel Acres
(Hillsboro) only.**

## When to use
- Pallo's kennel reservations: listing, planning, booking, modifying, cancelling.
- "Is boarding set up for <trip>?" readiness checks (one trip or all upcoming).
- "Where is Gina on <date>?" and the Model-X handoff coordination tied to a stay.

## NEVER do these — use the scripts instead
- **Do NOT** open a browser or use browser tools for Gingr / Laurel Acres.
- **Do NOT** web-search for "Laurel Acres", "Gingr", or kennel booking sites.
- **Do NOT** navigate to any URL yourself — the scripts handle all portal interaction.
- **Do NOT** try to use `browser_vision` or screenshot the booking portal.
The 27B model cannot process images and these paths will fail with errors.

## The tool surface

| Script | Purpose | Mutating? |
|---|---|---|
| `pallo-stays.py [--include-past] [--include-canceled] [--all]` | List Pallo's reservations from the portal. Default = upcoming + in-progress. | No |
| `pallo-trip-plan.py --trip-name <name>` *or* `--trip-start <ISO> --trip-end <ISO>` | Build the boarding window + activity slate + proposed Gina messages for a trip. **Adds one day of buffer each end** (drop-off = day before trip, pickup = day after). Returns an opaque `plan_json`. | No |
| `pallo-trip-plan.py --drop-date <ISO> --pickup-date <ISO>` | Same as above but uses the given dates **directly as boarding dates** — no buffer added. Use this when the user specifies explicit drop-off and pickup dates (e.g. "drop Pallo on Sep 30, pick up Oct 4") rather than travel dates. | No |
| `pallo-trip-status.py [--trip-name <name>] [--trip-start/--trip-end] [--horizon-days N]` | Is a trip's boarding set up? No trip identifier = sweep ALL upcoming trips. | No |
| `gina-where.py --date <ISO> [--who Gina\|Sky]` | Where is Gina (residency) on a date, from the merged 2Houses feed. | No |
| `pallo-book-trip.py --plan '<plan_json>' --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--dry-run] [--simple-slate]` | Make the boarding reservation + activity slate, then send the plan's Gina messages. | **Yes — real, paid** |
| `pallo-cancel.py --stay-id <id> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--dry-run]` | Cancel a stay (located by its dates; re-verifies the displayed dates before cancelling). | **Yes** |
| `pallo-modify-stay.py --stay-id <id> --new-drop-date <ISO> --new-pickup-date <ISO> [--simple-slate] [--dry-run]` | Change a stay's dates/activities. The portal has no in-place edit, so this books the new stay, then cancels the old. | **Yes** |
| `pallo-trip-prep.py --trip-name <name> [--commit --confirm-drop-date <ISO> --confirm-pickup-date <ISO>]` | One-call trip prep: readiness → plan → (with `--commit`) book + notify Gina. No `--commit` = read-only preview. | **Yes (with `--commit`)** |
| `gina-notify.py --topic <t> --body <b> [--handoff-date <ISO>] [--trip-name <n>] [--dry-run]` | Post a Model-X coordination message to the shared Discord channel (mentions Gina + the user). Normally fired automatically by a booking; direct call is an escape hatch. | **Yes — sends a message** |
| `gina-pending.py [--resolve <id>]` | List outstanding Gina-coordination asks; `--resolve` clears one. | Read / small write |
| `gingr-login.py [--show-head]` | Capture / refresh the saved portal session. Run only when a script reports `session_expired` or `not_logged_in`. | No (writes session file) |

## Safety — read-only by default

Read-only scripts are safe any time. Every mutating action — `pallo-book-trip.py`,
`pallo-cancel.py`, `pallo-modify-stay.py`, `pallo-trip-prep.py --commit`, and
`gina-notify.py` — requires an **explicit "yes" from the user in the same turn**
when run for real (without `--dry-run` / without `--commit`). First echo what will
change (dates, activity slate, estimated price, or which stay gets cancelled), get
the clean yes, THEN run the real action.

Every mutating booking/cancel script takes `--confirm-drop-date` /
`--confirm-pickup-date` (or `--stay-id` + confirms) — pass the exact dates the
user agreed to. They refuse (`confirm_mismatch`) on a mismatch; `pallo-book-trip.py`
also refuses (`conflict`) if Pallo already has an overlapping reservation.

Always preview first:
- `pallo-book-trip.py --dry-run` fills the whole request and stops at the Review
  screen (estimated total + screenshot) without submitting.
- `pallo-cancel.py --dry-run` opens the booking and verifies its dates without
  cancelling.
- `pallo-trip-prep.py` (no `--commit`) returns the plan-of-plans (window + slate +
  the Gina messages it would send).

## Opaque handles

`stay_id` (from `pallo-stays.py`) and `plan_json` (from `pallo-trip-plan.py`)
are black boxes. Pass them through verbatim; never construct, edit, or decode
them.

## Daily activity slate

Every booking gets, per §5: drop-off day = 1 Play Yard; each full day = 2 Play
Yard + 1 Nature Walk; pickup day = 1 Nature Walk. `pallo-book-trip.py` applies
this automatically. `--simple-slate` drops the second daily Play Yard (faster;
reasonable for long stays).

## Gina coordination

When the user's request involves a stay whose drop-off or pickup falls on a day
Gina is at her mom's, the Model X needs a handoff. `pallo-trip-plan.py` proposes
those messages; `pallo-book-trip.py` sends them after a successful booking.

**When you see a message FROM Gina in the coordination channel, call
`gina-pending.py` FIRST** to get the outstanding asks her reply is answering;
after you've acted on her answer, call `gina-pending.py --resolve <id>`.

## Trip dates vs. boarding dates

**`--trip-start` / `--trip-end`** are TRAVEL dates. The script automatically
adds a buffer: drop-off = day before trip starts, pickup = day after trip ends.
Use these when the user says "I'm flying to London July 12–14" or gives you
trip/travel dates.

**`--drop-date` / `--pickup-date`** are the BOARDING dates themselves — no
buffer is added. Use these when the user says things like "drop Pallo off Sep 30,
pick up Oct 4" or gives you specific kennel times ("Sep 30 3pm to Oct 4 11am").
If in doubt about which kind of dates the user means, ask — getting this wrong
adds an unwanted extra day on each end.

## Session expiry

If any script returns `session_expired` or `not_logged_in`, run
`gingr-login.py` (reads credentials from the secrets file). If it returns
`login_failed`, tell the user to check the portal credentials — do not retry in
a loop.

## Files this skill must NEVER read

| Path | Reason |
|---|---|
| `~/.config/pallo-logistics/secrets.env` | Portal password + Discord webhook. Scripts read it; the agent must not. |
| `~/.config/pallo-logistics/gingr-storage-state.json` | Saved login session (auth cookies). |
| `~/.config/pallo-logistics/pending-coordination.json` | Read it via `gina-pending.py`, not directly. |

## Common flows

- **"When is Pallo next at the kennel?"** → `pallo-stays.py`.
- **"Is Pallo's boarding set for my London trip?"** → `pallo-trip-status.py --trip-name London`.
- **"Is boarding arranged for all my trips?"** → `pallo-trip-status.py` (no args, sweep), then offer to book each gap one at a time.
- **"What's the plan for Pallo for Paris?"** → `pallo-trip-plan.py --trip-name Paris`; relay the window + slate + any Gina messages.
- **"Yes, book it."** → after the user confirms the dates/price you showed (from a `--dry-run`): `pallo-book-trip.py --plan '<plan_json>' --confirm-drop-date <ISO> --confirm-pickup-date <ISO>`. Relay the confirmation and whether the Gina messages went out.
- **"Set everything up for my Paris trip."** → `pallo-trip-prep.py --trip-name Paris` (preview); show the window + slate + Gina messages; on "yes": `pallo-trip-prep.py --trip-name Paris --commit --confirm-drop-date <ISO> --confirm-pickup-date <ISO>`.
- **"Cancel Pallo's July stay."** → `pallo-stays.py` to get the `stay_id`; confirm which one with the user; `pallo-cancel.py --stay-id <id> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> --dry-run`; on "yes" re-run without `--dry-run`.
- **"Move Pallo's stay to the 22nd"** / **"give him the simple activity slate"** → `pallo-stays.py` for the `stay_id`; `pallo-modify-stay.py --stay-id <id> --new-drop-date <ISO> --new-pickup-date <ISO> [--simple-slate] --dry-run`; on "yes" re-run without `--dry-run`. (For an activity-only change, pass the SAME dates.) It books the new stay then cancels the old; if it returns `modified_old_not_cancelled`, tell the user to cancel the old stay manually.
- **"Where's Gina on the 21st?"** → `gina-where.py --date 2026-07-21`.
