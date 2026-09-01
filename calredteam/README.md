# calredteam

Answer questions about the shared team calendar: what is on it for a day or
a range, and finding a team event by topic or person.

## Rationale

The team calendar is the shared one — the calendar the team plans on, as
distinct from any personal calendar. This skill answers questions about that
shared calendar only, through one small script, so a local model has one
prescribed path instead of improvising calendar access.

## Layout

- `scripts/calrt.py` — the only script. Invoked as
  `python3 calrt.py --start <ISO> --end <ISO>`; prints one JSON object:
  `{"ok": true, "count": <int>, "events": [...]}` on success,
  `{"ok": false, "error": "..."}` with exit 1 on failure.
- `SKILL.md` — the model-facing contract.

## Setup

`calrt.py` is self-contained: it reaches the shared team calendar on its own
and needs no per-user setup.
