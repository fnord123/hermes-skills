# calendar

Read-only calendar queries for the Hermes agent. Wraps the user's Google
Calendar (or any iCal-compatible feed) behind four small CLI scripts that
return clean JSON, so the agent doesn't have to parse iCal's gritty parts
(timezones, RRULE expansion, EXDATEs, RECURRENCE-ID exceptions) at runtime.

## What this is for

Designed for natural-language schedule questions in chat:
- *"What's on my calendar today?"*
- *"Do I have a haircut next week?"*
- *"What's my next meeting?"*
- *"What does Thursday look like?"*

The agent invokes one of four scripts with simple parameters and gets back
a structured list of events. iCal corner cases are handled inside the
scripts; the agent only ever sees clean Event objects.

## What this is NOT for

- **Event CRUD** (creating, editing, deleting events). iCal feeds are
  read-only by design. For event modification, use the `google-workspace`
  builtin skill (Google Calendar API + OAuth) instead.
- **Multiple calendars in one query.** Configure one iCal URL per
  installation. If you need multi-calendar coverage, run multiple
  copies under different aliases — open an issue if you actually want this.
- **CalDAV.** This skill speaks iCal-over-HTTP (the "secret address" Google
  exposes). For CalDAV servers, use `khal`/`vdirsyncer` directly or a
  CalDAV-specific skill.

## How it works

```
GCAL_ICAL_KEY (env)
   │ urllib.request
   ▼
~5MB .ics body
   │ ical_lib.py — unfold, parse VEVENTs, expand RRULE/EXDATE/RECURRENCE-ID
   ▼
List of Event dataclasses, filtered to the script's date window
   │ json.dumps
   ▼
stdout (small structured payload the agent reads directly)
```

The parser (`scripts/ical_lib.py`) is **stdlib-only** — no
`icalendar`/`recurring-ical-events` pip dependencies. This is a deliberate
choice: it sidesteps EXTERNALLY-MANAGED Python issues that pip-install
skills hit, and the user already has a battle-tested parser of this shape
in `~/daily-briefing/fetch-calendar.sh`. The library carves that parser
out as a reusable module.

### Why a custom parser instead of `icalendar` + `recurring-ical-events`?

Trade-off considered:

**Pros of pip libraries:** Fewer custom lines; well-tested edge cases
(BYSETPOS, BYHOUR, daylight-saving rules); RFC 5545 compliance from the
library author rather than us.

**Cons (why we declined):**
1. Pip installs require `--user --break-system-packages` or a venv on
   modern Debian/Ubuntu/Fedora (PEP 668). One more setup step.
2. The user's existing `~/daily-briefing/` ships zero-pip iCal parsing
   that has run daily for months without issue. Reusing it costs nothing.
3. Google Calendar's iCal export uses a narrow subset of RRULE; the
   missing features (BYSETPOS, BYWEEKNO) don't matter for personal
   calendars.

If your feed surfaces a corner case the parser misses, the right fix is
to extend `ical_lib.py` rather than swap in `icalendar` — keeping the
zero-dep posture is more valuable than full RFC coverage for a
read-mostly skill.

### What the parser handles

- RFC 5545 line unfolding (continuation lines with leading whitespace).
- DTSTART / DTEND with `VALUE=DATE` (all-day) and `TZID=…` (zoned datetimes).
- Multi-day all-day events: emits one Event per included day with a
  `"Day N of M"` label.
- RRULE: `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY` with `INTERVAL`, `BYDAY`,
  `BYMONTHDAY`, `COUNT`, `UNTIL`, `WKST`.
- EXDATE exclusions.
- RECURRENCE-ID exception instances (the modified instance replaces the
  synthesised one).
- `STATUS:CANCELLED` filtering (cancelled events are dropped).

### What it doesn't handle

- BYSETPOS, BYMONTH, BYWEEKNO, BYHOUR, BYMINUTE, BYSECOND, BYYEARDAY.
- VTODO, VJOURNAL, VFREEBUSY (only VEVENT).
- VALARM (no reminders surfaced).
- Attachments / complex properties beyond SUMMARY, LOCATION, ORGANIZER,
  DESCRIPTION.

## Setup

### 1. Install the skill

This skill is intended to live in
[`fnord123/hermes-skills`](https://github.com/fnord123/hermes-skills) and
install into Hermes via:

```bash
hermes skills install <repo URL pointing at calendar/>
```

The scripts and library are stdlib-only — no `pip install` step required.

### 2. Configure the iCal feed

```bash
cd ~/.hermes/skills/calendar/scripts
cp .env.example .env
$EDITOR .env
```

Get the iCal URL from Google Calendar:
1. Open calendar.google.com.
2. Hover the calendar in the left sidebar → click the kebab → Settings
   and sharing.
3. Scroll to **Integrate calendar** → copy the **Secret address in iCal
   format**. (Use the secret one, not the public one, so all events
   including private/free-busy details are visible.)
4. Paste into `GCAL_ICAL_KEY=...` in `.env`.

Treat the URL like a password — anyone with it can read your calendar.

If you already use `~/daily-briefing/`, this is the same value as that
skill's `GCAL_ICAL_KEY`.

### 3. (Optional) Timezone

By default the scripts pick up `SCHEDULE_TZ` from `.env`, then
`BRIEFING_TZ` (so daily-briefing users inherit), then
`America/New_York`. Set `SCHEDULE_TZ` only if you want this skill
anchored to a different zone than daily-briefing.

### 4. (Optional) Organizer names

Set `CALENDAR_PEOPLE_JSON=/path/to/calendar-people.json` if you want
events enriched with a display-name field for the organizer (mirrors the
file daily-briefing uses):

```json
{
  "default": "Me",
  "organizers": {
    "alice@example.com": "Alice",
    "bob@example.com": "Bob"
  }
}
```

### 5. Test it

```bash
python3 calendar-today.py | python3 -m json.tool
```

You should see today's events as structured JSON. If you get `"error":
"iCal fetch HTTP 401"` or similar, double-check the URL.

## The four scripts

### `calendar-today.py`
No args. Returns today's events in the configured timezone.

```bash
python3 calendar-today.py
```

### `calendar-range.py --start <ISO> --end <ISO>`
List events in an inclusive date range. ISO dates only — no
natural-language phrases. The agent resolves "tomorrow" / "next week" to
ISO before calling.

```bash
python3 calendar-range.py --start 2026-06-15 --end 2026-06-21
```

Returns one entry per day in the window (empty `events` array for empty
days), so the agent can see structure even where there's nothing scheduled.

### `calendar-find.py --query <text> [--days-back N] [--days-ahead N]`
Case-insensitive substring search across title, location, organizer, and
description. Default window is 7 days back through 30 days ahead.

```bash
python3 calendar-find.py --query dentist
python3 calendar-find.py --query "standup" --days-ahead 7 --days-back 0
```

### `calendar-next.py [--within <hours>] [--limit <N>]`
Returns the next upcoming events within a time horizon. Defaults to 48
hours and 3 events.

```bash
python3 calendar-next.py
python3 calendar-next.py --within 2 --limit 1   # next event in next 2h
python3 calendar-next.py --within 168           # next event in next week
```

All-day events count as "upcoming" until the day ends and "in progress"
during their day.

## Output shape (every script)

Every script emits a single JSON object with a top-level
`events` / `matches` / `days[*].events` array of Event objects. Each
Event has the shape documented in [`SKILL.md`](SKILL.md).

The agent should relay these to the user as natural language, not as raw
JSON. Example response style:

> You have 3 things today:
> - **9:00 AM** Standup
> - **11:30 AM** Dentist – cleaning @ 1234 Main St
> - **2:00 PM** Review with Alice

## Security & operational notes

- **`GCAL_ICAL_KEY` is a credential.** Anyone who has the URL can read
  your calendar in perpetuity (until you regenerate it). Keep `.env` out
  of git (the included `.gitignore` covers this) and don't share the URL
  in chat.
- **No write access.** iCal feeds are read-only. There's no way for a
  bug in this skill to corrupt your calendar.
- **Bounded fetch.** Each script makes one HTTPS GET to the iCal URL with
  a 30-second timeout. There's no caching layer; if you find yourself
  hammering Google's iCal endpoint, add caching at the system-cron layer
  or move to `google-workspace`.
- **Timezone is a tripwire.** If the agent reports an event at the wrong
  hour, the most likely cause is `SCHEDULE_TZ` (or `BRIEFING_TZ`) being
  unset or wrong. Default fallback is `America/New_York`.

## Files

```
calendar/
├── SKILL.md                       # agent-facing model context
├── README.md                      # this file
└── scripts/
    ├── .env.example
    ├── .gitignore
    ├── ical_lib.py                # shared iCal parser (stdlib-only)
    ├── calendar-today.py
    ├── calendar-range.py
    ├── calendar-find.py
    └── calendar-next.py
```

## License

MIT.
