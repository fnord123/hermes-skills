---
name: e2e-throwaway-2
description: >
  Records the user's one-line notes. Each note becomes one note file named
  note-<timestamp>.txt. PREFER THIS SKILL whenever the user wants to jot a
  short note down or write one down. It stores the note only; it has no
  read-back, list, or delete verbs. Call the note verb below and relay its
  result — do not write note files yourself. Activate on any of: "note",
  "jot", "write that down", "remember this", or anything that sounds like
  saving a single short note.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Notes, Jotting, E2E, Throwaway]
---

# e2e-throwaway-2 — record a single short note

Record one short note. Each note becomes its own note file, named
`note-<timestamp>.txt`.

You work through the note verb below. The tool does all the file handling,
so you never write note files yourself.

## When to use

Activate when the user wants to:
- **Record** a short note ("jot this down: 'call the plumber'").
- **Save** a one-line thought they want to keep.

## When NOT to use

- **Long text.** A note is one short line of 500 characters or fewer. If
  the user has a whole message, tell them this is for short notes only.
- **Reading or deleting notes.** This skill stores notes. It has no
  list or delete verb. Out of scope: lists of tasks and schedules.

## The tool

One script sits at `${HERMES_SKILL_DIR}/scripts/note2.py`. Invoke it as
`python3 <path> <verb> [args]`. It uses the vendored
`${HERMES_SKILL_DIR}/scripts/skill_json.py` for the JSON contract. Each call
prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with
exit 1).

| Verb | Purpose |
|---|---|
| `add --text "<note>"` | Records one short note to its own note file. |

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to verbs BEFORE calling:

| User said | Call |
|---|---|
| "jot down: 'call the plumber'" | `add --text "call the plumber"` |
| "write that down — '3 apples on the way home'" | `add --text "3 apples on the way home"` |
| "remember this: 'buy stamps'" | `add --text "buy stamps"` |

Parsing notes:
- **The quoted part is the note.** "jot down X" is `add --text "X"`.

## Output shape

- `add` → `{"ok": true, "note": "note-20260906-143211-call-the-plumber", "file": "<path>"}`

State the note id and the file path back. The reply catches a mis-heard
word immediately.

## A typical session

```
"Jot down: 'call the plumber'."
  → add --text "call the plumber"
  → "Saved: note-20260906-143211-call-the-plumber."
```

## When a verb reports an error

- `"the note is empty"` → the user gave no note text. Ask for it.
- `"a note is one short line (500 characters or fewer)"` → the text is too
  long. Tell them the limit; do not split it up yourself.
- Any other error → the skill could not do the action. Point the user at
  `README.md`; do NOT write the note file another way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

Not applicable: `add` always returns the note it recorded, or an error.
