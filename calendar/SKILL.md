---
name: calendar
description: >
  Calendar lookup — answer any question about the user's calendar, schedule,
  appointments, or events. Works on the user's Google Calendar via a
  pre-configured iCal feed; requires NO setup, NO sign-in, NO credentials at
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
    tags: [Calendar, Schedule, Appointments, Meetings, Events, ICal, GoogleCalendar, Productivity]
---

# calendar — read-only calendar queries

## When to use

Activate when the user wants to:
- Know what's on today, tomorrow, this week, or any specific date or range.
- See what's coming up in the next N hours.
- Find a specific event by topic, person, location, or title.

## When NOT to use

- The user wants to **create, modify, or delete** a calendar event. This
  skill is read-only; iCal feeds are one-way. For event CRUD use
  `google-workspace` (needs its own sign-in setup) instead.
- The user asks about a calendar that **isn't** the one configured here
  (work calendar at a different account, a shared calendar with separate
  iCal URL, etc.). Tell them they'd need to configure that separately.
- The user wants their email/Slack-discussed schedule, not an actual
  calendar entry. Use the relevant communication skill.

## The four tools

All live at `${HERMES_SKILL_DIR}/scripts/`. Invoke each via
`python3 <path> [args]`. Each emits one JSON object on stdout: `{"ok": true,
…}` when the lookup succeeded, `{"ok": false, "error": "…"}` when it did not.
Check `ok` first.

| Script | Purpose |
|---|---|
| `calendar-today.py` | Lists today's events. No args. |
| `calendar-range.py --start <ISO> --end <ISO>` | Lists events between two ISO dates, inclusive. Spans up to 400 days. |
| `calendar-find.py --query <text> [--days-back N] [--days-ahead N]` | Searches title, location, organizer and description for a substring. Defaults: 7 days back, 30 days ahead. |
| `calendar-next.py [--within <hours>] [--limit <N>]` | Lists the next upcoming events within a horizon. Defaults: 48 hours, 3 events. |

## Merged sources

The calendar surface merges up to three feeds into one sorted stream. Each
event carries a `source` field and (for the non-personal feeds) a title
prefix so plain-text search reaches across all of them:

| `source` | Title prefix | What it is |
|---|---|---|
| `personal` | *(none)* | The user's own Google Calendar. |
| `2houses` | `[Gina] ` | Co-parenting / shared-custody schedule (2Houses). Use these to answer "where is Gina / Sky on date X" — events read "Gina with David" (at the user's house) or "Gina with Christine" (at her mother's). |
| `kayak` | `[Trip] ` | The user's travel itinerary (Kayak Trips). Each trip has an all-day umbrella event named "<Place> Trip" spanning its dates, plus timed flight / hotel sub-events. |

The `2houses` and `kayak` feeds appear only when configured; with just
`GCAL_ICAL_KEY` set, the surface is the personal calendar alone.

Every tool's output also includes a `feed_errors` array. It is normally
empty; if one feed failed to fetch, the others still return and the failure
is listed there. Mention it only if it explains a gap the user noticed.

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
  "day_label": null,
  "source": "personal"
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
- `source` is `personal`, `2houses`, or `kayak` (see Merged sources above).

## Resolving the user's words to query parameters

The user usually speaks in relative time. Resolve to ISO dates BEFORE
calling, anchored to the user's local date (the agent's `today`):

| User said | Resolution |
|---|---|
| "today" | `calendar-today.py` |
| "tomorrow" | `calendar-range.py --start <today+1> --end <today+1>` |
| "this week" | range from today through Sunday |
| "next week" | range Monday-through-Sunday of the next ISO week |
| "next Wednesday" | resolve to the next Wednesday's date |
| "the 20th" | the next 20th-of-the-month after today |
| "what's next" | `calendar-next.py` |
| "in the next hour" | `calendar-next.py --within 1` |
| "anything called X" | `calendar-find.py --query X` |
| "find Y in the next month" | `calendar-find.py --query Y --days-ahead 30` |

## Common flows

### "What's on my calendar today?"
```
calendar-today.py
→ tell the user: count, then for each event the start time, title,
  optional location and organizer.
```

### "What's on next Wednesday?"
```
# resolve "next Wednesday" to an ISO date first, e.g. 2026-06-24
calendar-range.py --start 2026-06-24 --end 2026-06-24
```

### "Do I have anything called 'standup' this week?"
```
calendar-find.py --query standup --days-ahead 7 --days-back 0
```

### "What's my next meeting?"
```
calendar-next.py --within 24 --limit 1
```

### "What does my week look like?"
```
# resolve to Monday and Sunday of the user's current ISO week
calendar-range.py --start <monday> --end <sunday>
```

## When a script reports an error

A script that fails prints `{"ok": false, "error": "…"}`.

- `"No calendar feeds configured..."` → the skill isn't
  configured. Tell the user to follow setup in `README.md`. Don't try to
  fetch calendars yourself.
- `"iCal fetch HTTP <n>"` or `"iCal fetch network error"` → calendar
  source is unavailable; report the symptom, do not retry endlessly.
- `"the requested range covers ... days; ask for a window of 400 days or
  fewer"` → narrow `--start`/`--end` to the span the user actually asked
  about and call again.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`count: 0` and an empty `events`/`matches`/`days[*].events` array means
the calendar genuinely has no entries in the queried window. Say so
plainly ("you have nothing scheduled for Tuesday") — don't speculate that
the calendar might be wrong, and don't double-check via another tool
without the user asking.
