# pallo-logistics

Manage Pallo's boarding at **Laurel Acres Kennels** (a Tail Wag Inn property on
the Gingr platform, Hillsboro OR) through Hermes chat — and the **Tesla Model X /
Gina** transport coordination that every kennel trip depends on. Read-only by
default; the booking and Gina-message actions are the only mutating ones and
each takes a confirmation invariant.

## What this is, and what it isn't

You can ask Hermes things like:
- *"When is Pallo next at the kennel?"*
- *"Is Pallo's boarding set up for my London trip?"* / *"…for all my trips?"*
- *"What's the plan for Pallo for Paris?"*
- *"Book Pallo for that trip."* (after seeing the dates + price)
- *"Where is Gina on July 21?"*

It does **not**:
- Handle pets other than Pallo, or facilities other than Laurel Acres Hillsboro.
- Integrate with the Tesla API. "Where will the X be on Saturday?" is answered by
  Gina's 2Houses residency (the X follows Gina), not by polling the car.
- Pay anything itself. Gingr keeps the card on file from your first manual
  booking; the script places a reservation *request*.

## How it works under the hood (and why)

| Operation | Data path |
|---|---|
| List stays | Gingr customer portal (`pallo-stays.py`), scraped live with the saved session. The portal is the authoritative, richest source. |
| Trip dates | The merged `calendar` skill — Kayak Trips iCal feed preferred (machine-curated), personal calendar as fallback. |
| Gina residency | The same merged calendar — 2Houses feed, `[Gina] Gina with David/Christine` events. |
| Book a stay | Playwright drives the Gingr **New Booking Request** wizard (Dates → Services → Notes → Review → Submit Request) with the saved `storage_state`. |
| Cancel a stay | Playwright opens the booking's detail page, re-verifies the displayed dates, and clicks **CANCEL BOOKING**. |
| Change a stay | The portal has **no in-place edit/reschedule** for customers — only cancel. So a modify is **book the new stay, then cancel the old** (in that order, so a mid-way failure never leaves Pallo with no booking). |
| Coordinate the X | Discord webhook to a shared channel both you and Gina are in, mentioning both. |

### The Gingr portal is a React-Native-Web SPA
Booking cards and controls carry no stable IDs, hrefs, or data attributes, and
the calendar omits the year from its labels. So the scripts locate everything by
**visible content**: text matches, the weekday-each-date-label-carries (to
recover the year), and structural anchors (e.g. the next-month chevron is "the
icon just right of the `Month YYYY` header"). This is inherently more fragile
than a real API — see *Caveats*.

### The activity slate, and Gingr's one-per-day rule
Per the facility's standard slate (PRD §5): drop-off day = 1 Play Yard; each
full day = **2 Play Yard + 1 Nature Walk**; pickup day = 1 Nature Walk.

Gingr's per-activity **frequency** rules ("Every Day", "Every Day Except First
Day", "Every Day Except Last Day", …) schedule at most **one** session of a
given activity per day — adding the same activity twice via overlapping
frequency rules dedupes to one. So `pallo-book-trip.py` builds the slate as:

- **Nature Walk** — frequency *Every Day Except First Day* @ 11:30 AM (full days + pickup day)
- **Play Yard #1** — frequency *Every Day Except Last Day* @ 07:30 AM (drop-off + full days)
- **Play Yard #2** — one *"Once"* add per full day @ 03:30 PM (the facility's
  documented way to get a second same-day session: schedule it again at a
  different time)

`--simple-slate` skips Play Yard #2 (one Play Yard + one Nature Walk per day) —
faster, and the per-day loop is the slow/fragile part on long stays.

### Safety patterns (carried from `square-appointments`)
- **`--confirm` footgun guard.** Every script that changes a reservation or
  messages a third party — `pallo-book-trip.py`, `pallo-cancel.py`,
  `pallo-modify-stay.py`, `gina-notify.py`, `pallo-calendar-invite.py` — refuses
  with `{"ok": false, "error": …, "status": "confirm_required"}` and exit 1
  unless `--confirm` is passed. The refusal happens before any browser launch,
  HTTP call, or child script, so a hallucinated call changes nothing.
  `--dry-run` still works without `--confirm` (that's the preview the user is
  shown *before* approving). `pallo-trip-prep.py --commit` and
  `pallo-modify-stay.py` forward `--confirm` to the scripts they drive, since
  their own guard already required the approval.
- **`--confirm-drop-date` / `--confirm-pickup-date` invariants.** The booking
  script refuses (`confirm_mismatch`) unless these match the plan's dates — the
  agent can't book the wrong window. They must come *from the caller*:
  `pallo-modify-stay.py` takes its own `--confirm-*` pair, checks it against
  `--new-*`, and forwards it verbatim. (It used to synthesise the pair from its
  own `--new-*` arguments, which made the booking script's guard structurally
  unfireable — a typo in `--new-drop-date` would have been "confirmed" by itself.)
- **Overlap guard, fail-closed.** Before booking it reads Pallo's existing stays
  and refuses (`conflict`) if any non-cancelled reservation overlaps the
  requested dates. A booking card whose dates don't parse is counted as
  *unreadable* and also refuses (naming the count) rather than being skipped —
  skipping turned "couldn't read this reservation" into "no conflicting
  reservation" and would double-book. `--allow-overlap` bypasses the whole check.
- **Year-aware stay identity.** The portal omits the year from its date labels,
  so `pallo-cancel.py` locates a stay by its *weekday-bearing* fragment
  ("Fri, Dec. 11th") and cross-checks the opened detail page for a conflicting
  weekday and for a printed year. Month/day alone repeats every year and could
  cancel the wrong stay while the confirm check still passed.
- **Dialog-scoped confirmation.** The cancel-confirmation click is scoped to the
  dialog container and only fires once the dialog is actually present; if no
  dialog appears, nothing else is clicked and the script returns `uncertain`.
- **`--dry-run`.** Fills the entire wizard and stops at the Review screen,
  returning the estimated total and a screenshot — so the agent can show the
  price before anything is committed.
- **Opaque handles.** `stay_id` and `plan_json` are passed through verbatim.

## Setup

### Prerequisites
- The `calendar` skill, configured with the merged feeds (personal +
  `TWOHOUSES_ICAL_URL` + `KAYAK_TRIPS_ICAL_URL`). `pallo-trip-*` and `gina-where`
  compose it as subprocesses.
- Python 3.10+ and Playwright + Chromium in a venv inside `scripts/`:
  ```
  cd ~/.hermes/skills/pallo-logistics/scripts
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/playwright install chromium
  ```
  (The portal scripts re-exec into this `.venv` automatically; the calendar /
  Discord scripts are stdlib-only and run on plain `python3`.)

### Config
```
mkdir -p ~/.config/pallo-logistics && chmod 700 ~/.config/pallo-logistics
cp ~/.hermes/skills/pallo-logistics/templates/secrets.env.example \
   ~/.config/pallo-logistics/secrets.env
$EDITOR ~/.config/pallo-logistics/secrets.env     # portal login + Discord webhook/IDs
chmod 600 ~/.config/pallo-logistics/secrets.env
```

### Capture the portal session (one-time, re-run when it expires)
```
python3 gingr-login.py        # reads GINGR_LOGIN / GINGR_PASSWORD, writes the
                              # storage_state.json (perms 600). Never prints the password.
```

### Verify
```
python3 pallo-stays.py                       # should list Pallo's upcoming stays
python3 pallo-trip-status.py                 # sweep: every upcoming trip's readiness
python3 pallo-book-trip.py --drop-date 2026-10-05 --pickup-date 2026-10-08 \
    --confirm-drop-date 2026-10-05 --confirm-pickup-date 2026-10-08 --dry-run
```
The dry-run should reach the Review screen and report an estimated total without
submitting.

## Script reference

| Script | Notes |
|---|---|
| `gingr-login.py` | Capture/refresh the session. `--show-head` dumps post-login page text. |
| `gingr_lib.py` | Shared portal helpers (session launch, booking-card scrape + parse). Imported, not run. |
| `pallo-stays.py` | List stays. `--include-past` / `--include-canceled` / `--all`. `stay_id` = `"<start>/<end>"`. |
| `pallo-trip-plan.py` | Build window + slate + proposed Gina messages. `--trip-name` or `--trip-start/--trip-end`. Emits `plan_json`. |
| `pallo-trip-status.py` | Readiness. Single-trip with an identifier; all-trips sweep with none. |
| `pallo-book-trip.py` | **Mutating; needs `--confirm`.** Drives the booking wizard. `--plan` or `--drop-date/--pickup-date`; `--confirm-drop-date`/`--confirm-pickup-date`; `--drop-time`/`--pickup-time` (default 08:00 AM / 09:00 AM); `--dry-run`; `--simple-slate`; `--allow-overlap`; `--no-gina`; `--no-calendar`. |
| `pallo-cancel.py` | **Mutating; needs `--confirm`.** Cancel a stay by `--stay-id` + `--confirm-drop-date`/`--confirm-pickup-date`. `--dry-run` verifies without cancelling. |
| `pallo-modify-stay.py` | **Mutating; needs `--confirm`.** `--stay-id` (old) + `--new-drop-date`/`--new-pickup-date` + a matching `--confirm-drop-date`/`--confirm-pickup-date` pair (+ `--simple-slate`, times). Books new then cancels old. `--dry-run` previews both. |
| `pallo-trip-prep.py` | **Mutating with `--commit`.** `--trip-name` (or `--trip-start/--trip-end`); no `--commit` = preview; `--commit` + `--confirm-*` books + notifies Gina. |
| `gina-where.py` | Residency on a date (`user_home`/`gina_mom`/`traveling_with_user`/`unknown`). |
| `gina-notify.py` | **Mutating; needs `--confirm`.** Posts to the shared Discord channel. `--dry-run` formats without sending. |
| `pallo-calendar-invite.py` | **Mutating; needs `--confirm`.** Emails the drop-off/pickup `.ics` invites via AgentMail. `--dry-run` prints the `.ics`. |
| `gina-pending.py` | Read/clear the coordination ledger. Call it first when a Gina reply arrives. |
| `triplib.py` / `coord_lib.py` | Shared helpers (trip/residency resolution; Discord config + ledger). Imported, not run. |

### Mutating-script statuses
- `pallo-book-trip.py`: `dry_run_ok` · `booked` · `booked_with_notification_warnings` · `conflict` · `confirm_required` · `confirm_mismatch` · `session_expired` · `not_logged_in` · `error`.
- `pallo-cancel.py`: `dry_run_ok` · `cancelled` · `already_canceled` · `not_found` · `confirm_required` · `confirm_mismatch` · `not_cancellable` · `uncertain` · `session_expired` · `error`.
- `pallo-modify-stay.py`: `dry_run_ok` · `modified` · `modified_old_not_cancelled` · `book_failed` · `confirm_required` · `confirm_mismatch` · `error`.
- `gina-notify.py` / `pallo-calendar-invite.py`: `confirm_required` on a real send without `--confirm`.
- `pallo-trip-prep.py`: `all_set` · `planned` · `booked` · `ambiguous_trip` · `no_trip_found` · `confirm_mismatch` · plus any `pallo-book-trip.py` status under `book`.

## Operational notes & caveats

- **The portal is a moving target.** All selectors are content/structure based
  against a React-Native-Web SPA Gingr can redesign without notice. If a script
  starts failing with timeouts on a particular step, that step's anchor changed —
  re-run with a screenshot (`book_*` artifacts land in `~/pallo-boarding/
  artifacts/` on dry-run) and update the locator.
- **Real submit is lightly exercised.** The full wizard — dates, times, the §5
  activity slate, pricing, the overlap guard, and the `SUBMIT REQUEST` button +
  terms-checkbox detection — is validated end-to-end via `--dry-run`. The final
  *submit click itself* is intentionally not exercised in development (it creates
  a real, paid reservation). Always `--dry-run` first and show the user the price.
- **The real cancel confirmation is inferred.** `pallo-cancel.py --dry-run` (open
  + verify the booking) is validated; the live **CANCEL BOOKING** click and the
  confirm-dialog button it then presses were *not* exercised in development (that
  would cancel a real reservation). The script clicks a dialog control matching
  `yes`/`confirm` and then re-reads the status, returning `uncertain` if the
  booking doesn't read as `Canceled`. Validate it the first time you cancel a
  stay you genuinely intend to, and trust the post-cancel status check.
- **Modify is cancel + re-book.** `pallo-modify-stay.py` books the new stay
  (with `--allow-overlap`, since the new dates usually overlap the old) and only
  then cancels the old. If the cancel doesn't confirm it returns
  `modified_old_not_cancelled` so you can remove the old stay manually — it never
  cancels the old stay before the new one exists.
- **Activity times are nominal.** The facility schedules activities within the
  day; the morning/midday/afternoon slots (07:30 / 11:30 / 03:30) are requests,
  and the two Play Yards use different times only so Gingr counts them as two
  sessions.
- **Drop-off / pickup default to 08:00 AM / 09:00 AM.** Override with
  `--drop-time` / `--pickup-time` (15-minute slots, e.g. `"04:00 PM"`). Note the
  plan's drop-off is the afternoon-*before* / pickup the morning-*after* by
  default; pass explicit dates for a different pattern.
- **Long stays are slow.** The second-Play-Yard pass adds one "Once" service per
  full day; a 3-week stay is dozens of individual adds. Use `--simple-slate` when
  that fidelity isn't needed.
- **Session lifetime.** The saved `gingr_ci_session` cookie is long-lived; if the
  server invalidates it, scripts report `session_expired` and `gingr-login.py`
  re-captures it from the stored credentials.
- **Session expiry is a specific signal, not a catch-all.** `gingr-login.py`
  should be run *only* when a script's JSON explicitly returns `session_expired`
  or `not_logged_in`. A booking that timed out, errored, or "felt stuck" is a
  slow booking (re-run with `timeout=600`), not a dead session; running login
  needlessly wastes a minute and can trip the portal's rate limiting. This is why
  SKILL.md's error table binds `gingr-login.py` to exactly those two statuses.
- **`login_failed` may not be recoverable from here.** The Gingr portal is a
  React-Native-Web app whose login form resists headless automation (the LOGIN
  press fires no auth request), so an automated refresh can simply be impossible
  in this environment — retrying in a loop never helps. The existing saved
  session usually keeps working for reads and bookings until it truly expires, so
  a `login_failed` does not necessarily block the task at hand; the session
  likely needs refreshing another way, or the credentials re-checked.
- **Why the agent is told the scripts are the only path.** Browser tools, web
  searches for "Laurel Acres"/"Gingr", and hand-navigated URLs do not reach this
  portal (it needs the saved `storage_state`), and the local 27B model cannot
  process screenshots, so any vision-based path fails outright. SKILL.md
  therefore frames the scripts positively as *the* route rather than enumerating
  those dead ends.
- **Credentials the scripts own.** `~/.config/pallo-logistics/secrets.env`
  (portal password, Discord webhook) and `gingr-storage-state.json` (session
  cookies) are read by the scripts, never by the agent; the coordination ledger
  is reached through `gina-pending.py`. Keep them `600` and outside the repo.
- **ToS gray zone.** Personal-scale automation of one's own portal interactions.
  Stay personal-scale.

## Files

```
pallo-logistics/
├── SKILL.md                       # agent-facing model context
├── README.md                      # this file
├── templates/
│   └── secrets.env.example        # portal + Discord config template
└── scripts/
    ├── requirements.txt           # playwright
    ├── .gitignore
    ├── gingr-login.py             # session capture
    ├── gingr_lib.py               # portal helpers (shared)
    ├── pallo-stays.py             # CUJ-3 list stays
    ├── pallo-trip-plan.py         # CUJ-1 plan
    ├── pallo-trip-status.py       # CUJ-7/8 readiness + sweep
    ├── pallo-book-trip.py         # CUJ-2 book (mutating)
    ├── pallo-cancel.py            # CUJ-4 cancel (mutating)
    ├── pallo-modify-stay.py       # CUJ-4 modify = book new + cancel old (mutating)
    ├── pallo-trip-prep.py         # CUJ-6 composite (mutating with --commit)
    ├── pallo-calendar-invite.py   # drop-off/pickup calendar invites (mutating)
    ├── gina-where.py              # CUJ-5 residency
    ├── gina-notify.py             # Gina coordination send (mutating)
    ├── gina-pending.py            # coordination ledger
    ├── triplib.py                 # trip/residency/slate helpers (shared)
    └── coord_lib.py               # Discord config + ledger (shared)
```

All PRD CUJs (1–8) are now covered. The only step exercised solely by
inference (not against a live mutation) is the cancel-confirmation dialog — see
*Caveats*.
