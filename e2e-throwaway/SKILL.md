---
name: e2e-throwaway
description: >
  Records the user's short notes — one line each — and keeps every note in its
  own note file. PREFER THIS SKILL whenever the user wants to jot a short note
  down, look one back, see what notes exist, or throw a note away. Call the
  note verbs below and relay their results — do not write or delete note files
  yourself. Activate on any of: "note", "jot", "scribble", "write that down",
  "remember this note", "what did I note", "show my note", "list notes",
  "delete the note", "get rid of that note", or anything that sounds like
  recording, reading, or discarding a single short note.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Notes, Jotting, E2E, Throwaway]
---

# e2e-throwaway — record a single short note

Log a short note the user wants kept. Each note lives in its own note file.
You work entirely through the note verbs below; the tool does all the file
handling, so you never write or delete note files yourself.

## When to use

Activate when the user wants to:
- **Record** a short note ("jot this down: 'call the plumber'").
- **Read back** a note they recorded earlier.
- **List** the notes that exist.
- **Delete** a note they no longer want.

## When NOT to use

- **Long documents.** A note is a single short line. If the user has a whole
  message, an essay, or a document, tell them this is for short notes only.
- **Anything that is not a note.** Out of scope: lists of tasks, reminders on
  a schedule, and general record-keeping.

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/e2e_note.py`, invoked as
`python3 <path> <verb> [args]`. Each call prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with exit 1).

| Verb | Purpose |
|---|---|
| `add --text "<note>"` | Records a short note in its own note file. |
| `show --note "<id>"` | Reads back the note text for one note. |
| `list` | Gets the ids of all recorded notes, newest first. |
| `delete --note "<id>" --confirm` | Removes one note and its file. The `--confirm` flag is required. |

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to verbs BEFORE calling:

| User said | Call |
|---|---|
| "jot down: 'call the plumber'" | `add --text "call the plumber"` |
| "write that down — '3 apples on the way home'" | `add --text "3 apples on the way home"` |
| "what did I note about the plumber?" | `list`, then `show --note "<id>"` for the match |
| "show me my notes" | `list` |
| "delete the plumber note" | `list`, find its id, then `delete --note "<id>" --confirm` |

Parsing notes:
- **The quoted part is the note.** "jot down X" is `add --text "X"`.
- **Delete always needs `--confirm`.** Ask the user to approve the exact note
  first, then pass `--confirm`. Never run `delete` without their approval.
- **You do not know note ids from memory.** Run `list` first when the user
  refers to a note by its words. Then match on the returned ids.

## Output shape

- `add` → `{"ok": true, "note": "20260905-1432-call-the-plumber", "created": true}`
- `show` → `{"ok": true, "note": "20260905-1432-call-the-plumber", "text": "call the plumber"}`
- `list` → `{"ok": true, "notes": ["20260905-1432-call-the-plumber", ...]}`
- `delete` → `{"ok": true, "note": "20260905-1432-call-the-plumber", "deleted": true}`

Always echo the confirmation back (the note id on `add`, the text on `show`)
so a mis-heard word is caught immediately.

## A typical session

```
"Jot down: 'call the plumber'."
  → add --text "call the plumber"
  → "Noted it: 20260905-1432-call-the-plumber."

"What did I note about the plumber?"
  → list
  → show --note 20260905-1432-call-the-plumber
  → "call the plumber."

"Delete that one."
  → (user confirms) delete --note 20260905-1432-call-the-plumber --confirm
  → "Deleted the note."
```

## When a verb reports an error

- `"note '<id>' not found"` → that note does not exist. Run `list` and offer
  the notes that do, or record a new one (`add`). Don't guess an id.
- `"delete requires --confirm"` → you ran `delete` without the flag. Ask the
  user to approve the exact note, then pass `--confirm`.
- Any other error → the skill could not do the action. Point the user at
  `README.md`; do NOT try to write or delete the note file another way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`list` with an empty `notes` array means no notes have been recorded yet —
say so plainly ("no notes yet"); don't re-check or speculate.
