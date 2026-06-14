---
name: square-appointments
description: >
  List, find, cancel, and move appointments at Square-using merchants the user
  has previously configured by alias. Activate when the user asks about
  appointments, scheduling, or names a merchant alias known to be configured
  here (e.g. "do I have an appointment at sugarmama next week", "find me a
  slot at hairdresser around the 20th", "cancel my appointment", "move my
  haircut to Tuesday"). The user's confirmation emails (forwarded to the
  AgentMail inbox) are the source of truth for existing bookings.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Appointments, Booking, Square, Calendar, Scheduling]
---

# Square Appointments — read & manage bookings at configured merchants

## When to use

Activate when the user mentions:
- An appointment, booking, scheduling, reschedule, or cancellation.
- A merchant alias known to be configured (run `list-merchants.py` if you
  don't recognise the name).

## When NOT to use

- The user is asking about appointments at a non-Square merchant (different
  flow entirely — say so).
- The user wants to *create a brand-new booking from scratch* at a merchant
  not configured here. This skill operates on the configured merchant set;
  ask the user to add the merchant first (point them to README.md).

## The four tools

All scripts live at `~/.hermes/skills/square-appointments/examples/`. Invoke
each via `python3 <path> <args>`. Each returns small structured output (JSON
lines or plain text) the agent can relay to the user directly.

| Script | Purpose | Mutating? |
|---|---|---|
| `list-merchants.py` | Show the configured merchant aliases. | No |
| `square-list.py --merchant <alias>` | List the user's upcoming appointments at one merchant, parsed from their AgentMail confirmation emails. | No |
| `square-find-slot.py --merchant <alias> --around <date-or-relative>` | Check for collision with existing booking; if none, return up to 5 available slots near the target date. | No |
| `square-cancel.py --merchant <alias> --booking-handle <h> --confirm-time <ISO>` | Cancel a specific existing booking. | Yes |
| `square-move.py --merchant <alias> --booking-handle <h> --new-slot <slot-handle> --confirm-time <ISO>` | Move an existing booking to a new slot (atomic). | Yes |

## Files this skill must NEVER read

| Path | Reason |
|---|---|
| `~/.hermes/skills/square-appointments/examples/.env` | Holds `AGENTMAIL_API_KEY`. The scripts read it; the agent must not. |
| `~/.config/square-appointments/merchants.json` | User-edited merchant config. Read indirectly via `list-merchants.py`. |
| `~/.config/square-appointments/*.log` | Operational logs may contain bearer tokens. |

If the user explicitly asks to read one of these, refuse and explain that
the skill forbids it.

## The opaque-handle contract

`booking_handle` and `slot_handle` are emitted by `square-list.py` and
`square-find-slot.py`. They contain bearer tokens (in the booking case) or
internal selector state (in the slot case).

- Treat them as **opaque**. Pass them through verbatim. Never construct,
  guess, decode, or trim them.
- Never URL-fetch a `booking_handle` yourself, even if it looks like a URL.
  Use the scripts.

## The `--confirm-time` invariant (the safety pattern)

`square-cancel.py` and `square-move.py` both require `--confirm-time` —
the ISO-8601 start time of the booking you intend to act on, exactly as
emitted by `square-list.py`.

The script re-reads the booking from Square and **refuses to mutate if
its read disagrees with `--confirm-time`**. This protects against the
agent acting on the wrong booking when there's ambiguity.

When the user's request is ambiguous about which booking to cancel or
move (e.g. they have multiple), call `square-list.py` first, ask the user
to confirm which one, then proceed.

## Common flows

### "Do I have an appointment at sugarmama next week?"
```
square-list.py --merchant sugarmama
→ filter / inspect results for matches in the next 7 days
→ relay to user
```

### "Find me a slot at sugarmama around the 20th."
```
square-find-slot.py --merchant sugarmama --around 2026-06-20
→ if status="already_have": tell user about the existing one; offer to move it
→ if slots returned: present them; ask user which to take
```

### "Cancel my sugarmama appointment on the 18th at 2pm."
```
square-list.py --merchant sugarmama        # find the booking
→ confirm the start_time with the user if any ambiguity
square-cancel.py --merchant sugarmama \
    --booking-handle <handle from list> \
    --confirm-time 2026-06-18T14:00:00-07:00
```

### "Move my sugarmama appointment to next Thursday at 3pm."
```
square-list.py --merchant sugarmama        # find the booking to move
square-find-slot.py --merchant sugarmama --around 2026-06-25
→ pick the user's preferred slot
square-move.py --merchant sugarmama \
    --booking-handle <handle> \
    --new-slot <slot-handle> \
    --confirm-time <original start time>
```

## Session-expired / token-expired errors

If a script reports `status: "token_expired"` or `status: "manage_link_dead"`,
tell the user: "The manage-booking link from Square expired — please cancel
or reschedule via the email confirmation directly." Do not retry; the bearer
token in the email is the only path and we can't refresh it.
