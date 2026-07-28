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

All scripts live in `${HERMES_SKILL_DIR}/scripts/`. Invoke each as
`python3 <path> <args>`; every script re-execs into its own `.venv` and prints
small JSON the agent relays to the user. Scope: **Pallo only, Laurel Acres
(Hillsboro) only.**

## When to use
- Pallo's kennel reservations: listing, planning, booking, modifying, cancelling.
- "Is boarding set up for <trip>?" readiness checks (one trip or all upcoming).
- "Where is Gina on <date>?" and the Model-X handoff coordination tied to a stay.

## When NOT to use
- Pets other than Pallo, or facilities other than Laurel Acres (Hillsboro).
- Tesla or vehicle-status questions. Where the Model X will be is answered by
  Gina's residency (`gina-where.py`), not by the car.
- General calendar or trip questions with no kennel angle — use the `calendar`
  skill.
- Reaching Laurel Acres or Gingr any other way. The scripts below are the only
  path to that portal; a browser tool, a web search, or a URL will not reach it.

## The tool surface

Every script that changes a reservation or messages someone takes `--confirm`.
Run it with `--dry-run` first, show the user what will change, and add
`--confirm` only after they say yes in that same turn. Without `--confirm` these
scripts refuse and change nothing.

| Script | Purpose | Mutating? |
|---|---|---|
| `pallo-stays.py [--include-past] [--include-canceled] [--all]` | Lists Pallo's reservations from the portal. Default = upcoming + in-progress. | No |
| `pallo-trip-plan.py --trip-name <name>` *or* `--trip-start <ISO> --trip-end <ISO>` | Builds the boarding window + activity slate + proposed Gina messages for a trip. **Adds one day of buffer each end** (drop-off = day before trip, pickup = day after). Returns an opaque `plan_json`. | No |
| `pallo-trip-plan.py --drop-date <ISO> --pickup-date <ISO> [--drop-time <t>] [--pickup-time <t>]` | Builds the same plan from the given dates used **directly as boarding dates** — no buffer added. Use this when the user specifies explicit drop-off and pickup dates (e.g. "drop Pallo on Sep 30, pick up Oct 4") rather than travel dates. Pass `--drop-time`/`--pickup-time` when the user gives clock times; they're carried in the `plan_json` so the booking uses them. | No |
| `pallo-trip-status.py [--trip-name <name>] [--trip-start/--trip-end] [--horizon-days N]` | Reports whether a trip's boarding is set up. No trip identifier = sweep ALL upcoming trips. | No |
| `gina-where.py --date <ISO> [--who Gina\|Sky]` | Gets Gina's residency on a date, from the merged 2Houses feed. | No |
| `pallo-book-trip.py --plan '<plan_json>' --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--drop-time <t>] [--pickup-time <t>] [--simple-slate] --dry-run` *then* the same call with `--confirm` | Makes the boarding reservation + activity slate, then sends the plan's Gina messages. **Requires `--confirm` to book for real; with `--dry-run` it only previews.** Drop/pickup times come from the plan; `--drop-time`/`--pickup-time` here override them. **SLOW: drives the whole booking wizard — 1–4 minutes. ALWAYS call the terminal tool with `timeout=600` (or run it in the background); the default 60–120s timeout WILL kill it mid-booking. `--simple-slate` is much faster (~1 min) and reliable; the full slate is slower.** | **Yes — real, paid** |
| `pallo-cancel.py --stay-id <id> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> --dry-run` *then* the same call with `--confirm` | Cancels a stay, located by its dates and re-verified on its detail page before anything is clicked. **Requires `--confirm` to cancel for real.** | **Yes** |
| `pallo-modify-stay.py --stay-id <id> --new-drop-date <ISO> --new-pickup-date <ISO> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--simple-slate] --dry-run` *then* the same call with `--confirm` | Changes a stay's dates or activities by booking the new stay and then cancelling the old one (the portal has no in-place edit). The `--confirm-*` dates must repeat the `--new-*` dates exactly. **Requires `--confirm` to run for real.** | **Yes** |
| `pallo-trip-prep.py --trip-name <name> [--commit --confirm-drop-date <ISO> --confirm-pickup-date <ISO>]` | Preps a whole trip in one call: readiness → plan → (with `--commit`) book + notify Gina. No `--commit` = read-only preview. | **Yes (with `--commit`)** |
| `gina-notify.py --topic <t> --body <b> [--handoff-date <ISO>] [--trip-name <n>] --dry-run` *then* the same call with `--confirm` | Posts a Model-X coordination message to the shared Discord channel (mentions Gina + the user). **Requires `--confirm` to post for real.** Normally fired automatically by a booking; direct call is an escape hatch. | **Yes — sends a message** |
| `gina-pending.py [--resolve <id>]` | Lists outstanding Gina-coordination asks; `--resolve` clears one. | Read / small write |
| `pallo-calendar-invite.py --plan '<plan_json>' [--events pickup,dropoff] [--to <emails>] --dry-run` *then* the same call with `--confirm` | Emails Google-Calendar invites (iMIP `.ics`) for the drop-off and/or pickup to you + Gina via AgentMail — they land in Google Calendar with RSVP. **Requires `--confirm` to send for real;** `--dry-run` prints the `.ics`. Fired automatically after a booking; call directly to (re)send for an existing stay. | **Yes — sends email invites** |
| `gingr-login.py [--show-head]` | Captures / refreshes the saved portal session. Run only when a script reports `session_expired` or `not_logged_in`. | No (writes session file) |

## Safety — read-only by default

Read-only scripts are safe any time. Every mutating action —
`pallo-book-trip.py`, `pallo-cancel.py`, `pallo-modify-stay.py`,
`pallo-trip-prep.py --commit`, `gina-notify.py`, and
`pallo-calendar-invite.py` — needs an **explicit "yes" from the user in the same
turn**, then `--confirm` (or `--commit`) on the call. First echo what will change
(dates, activity slate, estimated price, or which stay gets cancelled), get the
clean yes, THEN run the real action with `--confirm`.

Every mutating booking/cancel script also takes `--confirm-drop-date` /
`--confirm-pickup-date` (or `--stay-id` + confirms) — type the exact dates the
user agreed to. They refuse (`confirm_mismatch`) on a mismatch;
`pallo-book-trip.py` also refuses (`conflict`) if Pallo already has an
overlapping reservation, and refuses to book at all if any existing reservation
on the portal is unreadable.

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
this automatically via three frequency rules — the second daily Play Yard is
just the Play Yard activity added again at a DIFFERENT time (07:30 AM + 03:30 PM),
which is how Gingr allows two same-day sessions. `--simple-slate` drops the
second daily Play Yard (1 Play Yard/day; faster, reasonable for long stays).

The result reports:
- `activity_rules`: how many frequency rules attached per activity — the real
  slate signal. Play Yard should be **2** (both times) unless `--simple-slate`;
  Nature Walk **1**.
- `estimate_days`: the Review estimate's per-activity count. Note this is UNIQUE
  DAYS, not sessions, so it shows e.g. Play Yard "5" for a 5-day stay even with
  both rules attached — that's expected, not a shortfall.
- `slate_warning`: only appears if a rule failed to attach (e.g. Play Yard shows
  1 rule instead of 2). **Relay it to the user** and offer to re-run; a clean
  booking has no `slate_warning`.

## Gina coordination

When the user's request involves a stay whose drop-off or pickup falls on a day
Gina is at her mom's, the Model X needs a handoff. `pallo-trip-plan.py` proposes
those messages; `pallo-book-trip.py` sends them after a successful booking.

**When you see a message FROM Gina in the coordination channel, call
`gina-pending.py` FIRST** to get the outstanding asks her reply is answering;
after you've acted on her answer, call `gina-pending.py --resolve <id>`.

## Calendar invites (drop-off + pickup)

After a real booking, `pallo-book-trip.py` automatically emails Google-Calendar
invites for BOTH handoffs (drop-off and pickup) to you and Gina via AgentMail —
they arrive as normal calendar invites and land on both Google Calendars with
RSVP + reminders (1 day and 2 hours before). The booking result includes a
`calendar_invites` field (`ok` / `partial` / an error status); a send failure
never undoes the booking. Pass `--no-calendar` to skip.

To (re)send for an EXISTING stay, or send just one handoff, call
`pallo-calendar-invite.py` directly (e.g. `--events pickup --confirm`, after the
user asks for it). Re-sending uses a
stable per-stay UID, so it UPDATES the same calendar event rather than
duplicating it. Attendee emails come from `USER_EMAIL` / `GINA_EMAIL` in
secrets.env (override with `--to`); the AgentMail key/inbox are reused from
Hermes' config. If a stay's time changes, bump `--sequence` so the update
supersedes the prior invite.

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

## Drop-off / pickup times

When the user gives clock times ("3pm", "11am", "Sep 30 3pm to Oct 4 11am"),
pass them as `--drop-time` / `--pickup-time`. The scripts accept loose forms —
`3pm`, `3:00 PM`, `15:00` all work; you do NOT need to reformat them. Without
these flags the booking defaults to **08:00 AM drop / 09:00 AM pickup**, so if
the user stated times and you omit the flags, the reservation gets the wrong
times. Set them on `pallo-trip-plan.py` (they travel in the `plan_json`) or
directly on `pallo-book-trip.py`. A `dates_invalid` status with a time reason
means the clock value couldn't be parsed — re-read what the user said.

Example for "book Pallo Sep 30 3pm to Oct 4 11am":
```
pallo-trip-plan.py --drop-date 2026-09-30 --pickup-date 2026-10-04 \
    --drop-time 3pm --pickup-time 11am
# preview the booking and show the user the estimated total:
pallo-book-trip.py --plan '<plan_json>' \
    --confirm-drop-date 2026-09-30 --confirm-pickup-date 2026-10-04 --dry-run
# then, after the user says yes:
pallo-book-trip.py --plan '<plan_json>' \
    --confirm-drop-date 2026-09-30 --confirm-pickup-date 2026-10-04 --confirm
```

## When a script reports an error

Each script prints one JSON object. `"ok": false`, or a `status` other than
`ok` / `dry_run_ok` / `booked` / `cancelled` / `modified` / `planned` /
`all_set`, means **nothing was changed**. Read the `error` (or `reason`) field
and relay it.

- `confirm_required` — the action needs `--confirm`. Show the user exactly what
  will change, get their yes, then re-run the same call with `--confirm`.
- `confirm_mismatch` — the `--confirm-*` dates don't match the stay or plan.
  Re-read the dates from `pallo-stays.py` / `pallo-trip-plan.py`, check them with
  the user, and re-run.
- `conflict` — Pallo already has an overlapping reservation. Show it to the user
  and ask which stay they want.
- `session_expired` / `not_logged_in` — run `gingr-login.py` once, then re-run
  the original call.
- `not_found`, `not_cancellable`, `uncertain` — the stay wasn't located, has no
  cancel control, or the cancellation didn't verify. Report it and ask the user
  to check the portal.

Run `gingr-login.py` **only** for `session_expired` / `not_logged_in`. A booking
that took a long time is slow, not expired — re-run it with `timeout=600`.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Common flows

- **"When is Pallo next at the kennel?"** → `pallo-stays.py`.
- **"Is Pallo's boarding set for my London trip?"** → `pallo-trip-status.py --trip-name London`.
- **"Is boarding arranged for all my trips?"** → `pallo-trip-status.py` (no args, sweep), then offer to book each gap one at a time.
- **"What's the plan for Pallo for Paris?"** → `pallo-trip-plan.py --trip-name Paris`; relay the window + slate + any Gina messages.
- **"Yes, book it."** → after the user confirms the dates/price you showed (from a `--dry-run`): `pallo-book-trip.py --plan '<plan_json>' --confirm-drop-date <ISO> --confirm-pickup-date <ISO> --confirm`. Relay the confirmation and whether the Gina messages went out.
- **"Set everything up for my Paris trip."** → `pallo-trip-prep.py --trip-name Paris` (preview); show the window + slate + Gina messages; on "yes": `pallo-trip-prep.py --trip-name Paris --commit --confirm-drop-date <ISO> --confirm-pickup-date <ISO>`.
- **"Cancel Pallo's July stay."** → `pallo-stays.py` to get the `stay_id`; confirm which one with the user; `pallo-cancel.py --stay-id <id> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> --dry-run`; on "yes" re-run with `--confirm` instead of `--dry-run`.
- **"Move Pallo's stay to the 22nd"** / **"give him the simple activity slate"** → `pallo-stays.py` for the `stay_id`; `pallo-modify-stay.py --stay-id <id> --new-drop-date <ISO> --new-pickup-date <ISO> --confirm-drop-date <ISO> --confirm-pickup-date <ISO> [--simple-slate] --dry-run` (the `--confirm-*` dates repeat the `--new-*` dates); on "yes" re-run with `--confirm` instead of `--dry-run`. (For an activity-only change, pass the SAME dates.) It books the new stay then cancels the old; if it returns `modified_old_not_cancelled`, tell the user to cancel the old stay manually.
- **"Where's Gina on the 21st?"** → `gina-where.py --date 2026-07-21`.
