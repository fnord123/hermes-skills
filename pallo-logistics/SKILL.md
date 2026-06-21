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

## The tool surface

| Script | Purpose | Mutating? |
|---|---|---|
| `pallo-stays.py [--include-past] [--include-canceled] [--all]` | List Pallo's reservations from the portal. Default = upcoming + in-progress. | No |
| `pallo-trip-plan.py --trip-name <name>` *or* `--trip-start <ISO> --trip-end <ISO>` | Build the boarding window + activity slate + proposed Gina messages for a trip. Returns an opaque `plan_json`. | No |
| `pallo-trip-status.py [--trip-name <name>] [--trip-start/--trip-end] [--horizon-days N]` | Is a trip's boarding set up? No trip identifier = sweep ALL upcoming trips. | No |
| `gina-where.py --date <ISO> [--who Gina\|Sky]` | Where is Gina (residency) on a date, from the merged 2Houses feed. | No |
| `pallo-book-trip.py --plan '<plan_json>' --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--dry-run] [--simple-slate]` | Make the boarding reservation + activity slate, then send the plan's Gina messages. | **Yes — real, paid** |
| `gina-notify.py --topic <t> --body <b> [--handoff-date <ISO>] [--trip-name <n>] [--dry-run]` | Post a Model-X coordination message to the shared Discord channel (mentions Gina + the user). Normally fired automatically by a booking; direct call is an escape hatch. | **Yes — sends a message** |
| `gina-pending.py [--resolve <id>]` | List outstanding Gina-coordination asks; `--resolve` clears one. | Read / small write |
| `gingr-login.py [--show-head]` | Capture / refresh the saved portal session. Run only when a script reports `session_expired` or `not_logged_in`. | No (writes session file) |

## Safety — read-only by default

Read-only scripts are safe any time. The two mutating booking actions
(`pallo-book-trip.py` without `--dry-run`, `gina-notify.py` without `--dry-run`)
require an **explicit "yes" from the user in the same turn**. Before booking:
echo the dates + activity slate + estimated price back, get the clean yes, THEN
call without `--dry-run`.

`pallo-book-trip.py` takes `--confirm-drop-date` and `--confirm-pickup-date`
invariants — pass the exact dates the user agreed to. The script refuses
(`confirm_mismatch`) if they disagree with the plan, and refuses (`conflict`)
if Pallo already has an overlapping reservation. Use `--dry-run` to fill the
whole request and stop at the Review screen (captures the estimated total + a
screenshot) without submitting — do this to show the user the price first.

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

## Trip dates

Pass a trip NAME (e.g. `--trip-name London`) and let the scripts resolve dates
from the calendar (Kayak feed preferred). If a name is ambiguous or unknown the
script returns `ambiguous_trip` / `no_trip_found` — ask the user to clarify or
give explicit `--trip-start` / `--trip-end`.

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
- **"Where's Gina on the 21st?"** → `gina-where.py --date 2026-07-21`.
