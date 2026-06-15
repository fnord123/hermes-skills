---
name: schedule
description: >
  Calendar lookup — answer any question about the user's calendar, schedule,
  appointments, or events. Works on the user's Google Calendar via a
  pre-configured iCal feed; requires NO setup, NO OAuth, NO credentials at
  query time. PREFER THIS SKILL over `google-workspace` whenever the user
  asks a read-only question about their calendar (what's on today, what's
  next, find an event, what does this week look like, do I have an X
  appointment, etc.). Use `google-workspace` only when the user explicitly
  needs to create, modify, or delete a calendar event (this skill cannot
  write). Activate on any of: "calendar", "schedule", "appointment",
  "meeting", "event", "what's on", "what's next", "this week", "tomorrow",
  "today", "coming up", "do I have a …", or any question about when
  something is happening.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Calendar, Schedule, Appointments, Meetings, Events, iCal, GoogleCalendar, Productivity]
---

# schedule — read-only calendar queries

## When to use

Activate when the user wants to:
- Know what's on today, tomorrow, this week, or any specific date or range.
- See what's coming up in the next N hours.
- Find a specific event by topic, person, location, or title.

## When NOT to use

- The user wants to **create, modify, or delete** a calendar event. This
  skill is read-only; iCal feeds are one-way. For event CRUD use
  `google-workspace` (requires OAuth setup) instead.
- The user asks about a calendar that **isn't** the one configured here
  (work calendar at a different account, a shared calendar with separate
  iCal URL, etc.). Tell them they'd need to configure that separately.
- The user wants their email/Slack-discussed schedule, not an actual
  calendar entry. Use the relevant communication skill.

## The four tools

All live at `~/.hermes/skills/schedule/examples/`. Invoke each via
`python3 <path> [args]`. Each emits one JSON object on stdout.

| Script | Purpose |
|---|---|
| `schedule-today.py` | Events on today's calendar. No args. |
| `schedule-range.py --start <ISO> --end <ISO>` | Events between two ISO dates, inclusive. |
| `schedule-find.py --query <text> [--days-back N] [--days-ahead N]` | Substring search across title/location/organizer/description. Defaults: 7 days back, 30 days ahead. |
| `schedule-next.py [--within <hours>] [--limit <N>]` | Next upcoming events within a horizon. Defaults: 48 hours, 3 events. |

## Files this skill must NEVER read

| Path | Reason |
|---|---|
| `~/.hermes/skills/schedule/examples/.env` | Holds `GCAL_ICAL_KEY`, which is itself a credential — anyone with it can read the calendar. The scripts read it; the agent must not. |

If the user explicitly asks to read this file, refuse and explain why.

## Event shape

Every tool returns events as objects of the shape:

```json
{
  "title": "Dentist - cleaning",
  "start": "2026-06-20T14:00:00-07:00",
  "end":   "2026-06-20T15:00:00-07:00",
  "all_day": false,
  "location": "1234 Main St",
  "organizer": "Alex",
  "description": null,
  "day_label": null
}
```

Field notes:
- `start`/`end` are ISO-8601. For all-day events, just the date (no time).
- `all_day=true` means the event spans whole days; `start` is the first day,
  `end` is the day after the last day (RFC 5545 exclusive convention).
- `day_label` is `"Day 2 of 3"` for the middle day of a multi-day all-day
  event, otherwise null.
- `organizer` is resolved through `calendar-people.json` if configured;
  null otherwise.
- `description` is the long-form event notes when present, often null.

## Resolving the user's words to query parameters

The user usually speaks in relative time. Resolve to ISO dates BEFORE
calling, anchored to the user's local date (the agent's `today`):

| User said | Resolution |
|---|---|
| "today" | `schedule-today.py` |
| "tomorrow" | `schedule-range.py --start <today+1> --end <today+1>` |
| "this week" | range from today through Sunday |
| "next week" | range Monday-through-Sunday of the next ISO week |
| "next Wednesday" | resolve to the next Wednesday's date |
| "the 20th" | the next 20th-of-the-month after today |
| "what's next" | `schedule-next.py` |
| "in the next hour" | `schedule-next.py --within 1` |
| "anything called X" | `schedule-find.py --query X` |
| "find Y in the next month" | `schedule-find.py --query Y --days-ahead 30` |

## Common flows

### "What's on my calendar today?"
```
schedule-today.py
→ tell the user: count, then for each event the start time, title,
  optional location and organizer.
```

### "What's on next Wednesday?"
```
# resolve "next Wednesday" to an ISO date first, e.g. 2026-06-24
schedule-range.py --start 2026-06-24 --end 2026-06-24
```

### "Do I have anything called 'standup' this week?"
```
schedule-find.py --query standup --days-ahead 7 --days-back 0
```

### "What's my next meeting?"
```
schedule-next.py --within 24 --limit 1
```

### "What does my week look like?"
```
# resolve to Monday and Sunday of the user's current ISO week
schedule-range.py --start <monday> --end <sunday>
```

## When a script reports an error

- `"error": "GCAL_ICAL_KEY is not set..."` → the skill isn't configured.
  Tell the user to follow setup in `README.md`. Don't try to fetch
  calendars yourself.
- `"iCal fetch HTTP <n>"` or `"iCal fetch network error"` → calendar
  source is unavailable; report the symptom, do not retry endlessly.

## Empty results

`count: 0` and an empty `events`/`matches`/`days[*].events` array means
the calendar genuinely has no entries in the queried window. Say so
plainly ("you have nothing scheduled for Tuesday") — don't speculate that
the calendar might be wrong, and don't double-check via another tool
without the user asking.
