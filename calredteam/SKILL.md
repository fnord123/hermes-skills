---
name: calredteam
description: >
  Answers questions about the shared team calendar - what is on it for a
  day or a range, and finding a team event by topic or person. PREFER THIS
  SKILL when the user asks about the shared team calendar (not the personal
  calendar). Activate on any of: "team calendar", "team schedule",
  "team event", "team meeting".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Calendar, Team, Productivity]
    requires_toolsets: [terminal]
---

# calredteam - shared team calendar

Answer questions about the shared team calendar: what is on it for a day or
a range, and finding a team event by topic or person. Work entirely through
the one script below; it does the reading and you relay what it returns.

## When to use

Activate when the user wants to:
- Know what is on the shared team calendar for a day or a range.
- Find a team event by topic or person.

## When NOT to use

- **The personal calendar.** This skill answers only about the shared team
  calendar. If the user asks about their own calendar, say this skill
  answers the team calendar only.
- **Changing events.** This skill is read-only - it lists and finds team
  events. It cannot create, edit, or remove anything.

## The tools

One script at `${HERMES_SKILL_DIR}/scripts/calrt.py`, invoked as
`python3 <path> --start <ISO> --end <ISO>`. Each call prints ONE JSON object
on stdout (`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}`
with exit 1).

| Script | Purpose |
|---|---|
| `calrt.py --start <ISO> --end <ISO>` | Lists the team events between two ISO dates (a single day passes the same date twice). |

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to a range BEFORE calling:

| User said | Call |
|---|---|
| "what's on the team calendar today?" | `python3 calrt.py --start <today> --end <today>` |
| "what's on the team calendar this week?" | `python3 calrt.py --start <Monday> --end <Sunday>` |
| "any team events on Friday?" | `python3 calrt.py --start <Friday> --end <Friday>` |
| "is there a team event about <topic>?" / "when is <person> on the team calendar?" | Query the range they mean, then scan the returned `events` for the topic or person. |

Parsing notes:
- **Dates are ISO (`YYYY-MM-DD`).** "Today" is the date of the user's
  message; a range question expands to its first and last day.
- **Topic or person is not a flag.** The script takes a range only; find
  the event by scanning the returned `events` list for the topic or the
  person's name.

## Output shape

Success: `{"ok": true, "count": <int>, "events": [...]}` — `count` is the
number of team events in the range, `events` is the list of those events.
Failure: `{"ok": false, "error": "..."}` with exit 1.

Relay the count and the events in the user's own words (times, titles,
people) rather than pasting the raw JSON.

## Common flows

### "What is on the team calendar today?"
```
python3 calrt.py --start 2026-08-31 --end 2026-08-31
```
count 2 → "Two on the team calendar today: …".

### "Anything on the team calendar next week?"
```
python3 calrt.py --start 2026-09-07 --end 2026-09-13
```
Relay the events, or say there are none (see Empty results).

### "Is there a team event about the launch?"
Query the range the user means (e.g. this week), then scan the returned
`events` for "launch" and relay the match — or say none in that range.

## When a script reports an error

A failed call prints `{"ok": false, "error": "..."}` and exits 1. Relay the
error to the user as-is; do not work around it by reaching for the calendar
another way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`"count": 0` with an empty `events` list means the range has no team events.
Say so plainly ("nothing on the team calendar for that range"); do not
re-check or speculate.
