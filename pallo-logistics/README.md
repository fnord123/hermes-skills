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
- **`--confirm-drop-date` / `--confirm-pickup-date` invariants.** The booking
  script refuses (`confirm_mismatch`) unless these match the plan's dates — the
  agent can't book the wrong window.
- **Overlap guard.** Before booking it reads Pallo's existing stays and refuses
  (`conflict`) if any non-cancelled reservation overlaps the requested dates.
  `--allow-overlap` bypasses it.
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
cp ~/.hermes/skills/pallo-logistics/scripts/secrets.env.example \
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
| `pallo-book-trip.py` | **Mutating.** Drives the booking wizard. `--plan` or `--drop-date/--pickup-date`; `--confirm-*`; `--drop-time`/`--pickup-time` (default 08:00 AM / 09:00 AM); `--dry-run`; `--simple-slate`; `--allow-overlap`; `--no-gina`. |
| `pallo-cancel.py` | **Mutating.** Cancel a stay by `--stay-id` + `--confirm-*`. `--dry-run` verifies without cancelling. |
| `pallo-modify-stay.py` | **Mutating.** `--stay-id` (old) + `--new-drop-date`/`--new-pickup-date` (+ `--simple-slate`, times). Books new then cancels old. `--dry-run` previews both. |
| `pallo-trip-prep.py` | **Mutating with `--commit`.** `--trip-name` (or `--trip-start/--trip-end`); no `--commit` = preview; `--commit` + `--confirm-*` books + notifies Gina. |
| `gina-where.py` | Residency on a date (`user_home`/`gina_mom`/`traveling_with_user`/`unknown`). |
| `gina-notify.py` | **Mutating.** Posts to the shared Discord channel. `--dry-run` formats without sending. |
| `gina-pending.py` | Read/clear the coordination ledger. Call it first when a Gina reply arrives. |
| `triplib.py` / `coord_lib.py` | Shared helpers (trip/residency resolution; Discord config + ledger). Imported, not run. |

### Mutating-script statuses
- `pallo-book-trip.py`: `dry_run_ok` · `booked` · `booked_with_notification_warnings` · `conflict` · `confirm_mismatch` · `session_expired` · `not_logged_in` · `error`.
- `pallo-cancel.py`: `dry_run_ok` · `cancelled` · `already_canceled` · `not_found` · `confirm_mismatch` · `not_cancellable` · `uncertain` · `session_expired` · `error`.
- `pallo-modify-stay.py`: `dry_run_ok` · `modified` · `modified_old_not_cancelled` · `book_failed` · `error`.
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
- **ToS gray zone.** Personal-scale automation of one's own portal interactions.
  Stay personal-scale.

## Files

```
pallo-logistics/
├── SKILL.md                       # agent-facing model context
├── README.md                      # this file
└── scripts/
    ├── requirements.txt           # playwright
    ├── secrets.env.example        # portal + Discord config template
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
    ├── gina-where.py              # CUJ-5 residency
    ├── gina-notify.py             # Gina coordination send (mutating)
    ├── gina-pending.py            # coordination ledger
    ├── triplib.py                 # trip/residency/slate helpers (shared)
    └── coord_lib.py               # Discord config + ledger (shared)
```

All PRD CUJs (1–8) are now covered. The only step exercised solely by
inference (not against a live mutation) is the cancel-confirmation dialog — see
*Caveats*.
