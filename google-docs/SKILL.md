---
name: google-docs
description: >
  Create, read, and edit Google Docs documents — write a new document, read one
  back, add or insert text, find-and-replace, format text (bold/italic/underline
  or headings), or remove text. Works through a pre-configured service account;
  new documents land in the agent's shared Drive folder, and existing documents
  are reachable once shared with the agent. PREFER THIS SKILL for anything about
  a Google Doc / document's contents. It is a different, self-contained setup
  from `google-workspace` (which is OAuth-based) — reach for this one for Docs.
  Activate on any of: "google doc", "doc", "document", "write a doc", "create a
  document", "add to the doc", "insert into the document", "edit the doc",
  "find and replace in the doc", "make this a heading", "bold this in the doc",
  "read the doc", "what does the document say".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [GoogleDocs, Documents, Writing, Editing, Productivity]
---

# google-docs — create, read, and edit Google Docs

Work with Google Docs documents through the document verbs below. Each verb does
one thing to a document; you call it and relay the result. The tool handles all
of the document mechanics, so you work in plain document terms — title, text,
headings, find-and-replace — and never track positions or state yourself.

## When to use

Activate when the user wants to:
- **Create** a new document, optionally with starting text.
- **Read** a document's title and text back.
- **Add** text to the end of a document, or **insert** text at a located spot.
- **Replace** text throughout a document (find-and-replace).
- **Format** occurrences of some text (bold, italic, underline, or a heading).
- **Remove** text from a document.

## When NOT to use

- **Spreadsheets** — numbers, tables, cells, tabs, totals. That is a different
  surface; use the sheets/donations tooling, not this.
- **Sending or sharing** a document to someone, or moving it between folders.
  This skill writes document *contents*; it does not manage sharing.
- A document the agent **cannot reach**. This skill only sees documents in the
  agent's shared folder or ones explicitly shared with it. If a `read`/edit
  reports the document isn't found, it hasn't been shared — tell the user.

## The tool

One script at `~/.hermes/skills/google-docs/examples/docs.py`, invoked as
`python3 <path> <verb> [args]`. Each call prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with exit 1).

Editing verbs take a `<doc_id>` — the document's ID (the long string in its URL,
`https://docs.google.com/document/d/<doc_id>/edit`). `create` returns that id
and url; keep them to edit the same document afterward.

| Verb | Purpose |
|---|---|
| `create --title "<t>" [--text "<initial>"]` | Creates a new document in the shared folder. Returns its `document_id` and `url`. |
| `read <doc_id>` | Gets a document's title and full plain text. |
| `append <doc_id> --text "<t>"` | Adds text as a new paragraph at the end. |
| `insert <doc_id> --text "<t>" --after "<anchor>"` | Inserts text right after the first occurrence of the anchor text. |
| `insert <doc_id> --text "<t>" --at-start` | Inserts text at the very beginning. |
| `replace <doc_id> --find "<s>" --with "<s>"` | Replaces every occurrence of one string with another. |
| `style <doc_id> --find "<text>" [--bold] [--italic] [--underline] [--heading N]` | Formats every occurrence of the text. `--heading` 1–6 makes its paragraph a heading; 0 returns it to normal. |
| `delete <doc_id> --find "<text>" --confirm` | **Destructive.** Removes every occurrence of the text. Needs `--confirm`. |

Add `--match-case` to `insert --after`, `replace`, `style`, or `delete` when the
match must respect capitalization; by default matching ignores case.

## Turning the user's words into calls

Resolve loose phrasing to a verb BEFORE calling. Editing verbs need the
`document_id` of the document in play — the one from the last `create`, or one
the user names.

| User said | Call |
|---|---|
| "start a doc called Trip Plan" | `create --title "Trip Plan"` |
| "make a doc titled Notes that says 'Hello team'" | `create --title "Notes" --text "Hello team"` |
| "what does the doc say / read it back" | `read <doc_id>` |
| "add a line: 'Bring sunscreen'" | `append <doc_id> --text "Bring sunscreen"` |
| "put a title line at the top: 'Agenda'" | `insert <doc_id> --text "Agenda\n" --at-start` |
| "after 'Day 1' add 'Fly to Rome'" | `insert <doc_id> --text " Fly to Rome" --after "Day 1"` |
| "change every 'Rome' to 'Milan'" | `replace <doc_id> --find "Rome" --with "Milan"` |
| "make 'Agenda' a heading" | `style <doc_id> --find "Agenda" --heading 1` |
| "bold the word 'urgent'" | `style <doc_id> --find "urgent" --bold` |
| "remove the line 'draft — do not send'" | `delete <doc_id> --find "draft — do not send" --confirm` (confirm first) |

Notes:
- When the text should start on its own line, include a `\n` in `--text` (as in
  the title-at-top example).
- If the user asks to edit a document but no `document_id` is in play, ask which
  document (or offer to `create` one). Don't guess an id.

## Output shape

- `create` → `{"ok": true, "document_id": "1AbC...", "title": "Trip Plan", "url": "https://docs.google.com/document/d/1AbC.../edit"}`
- `read` → `{"ok": true, "document_id": "1AbC...", "title": "Trip Plan", "text": "Day 1\nFly to Rome\n..."}`
- `append` → `{"ok": true, "document_id": "1AbC...", "action": "appended", "characters": 16}`
- `insert` → `{"ok": true, "document_id": "1AbC...", "action": "inserted", "at_index": 42, "characters": 12}`
- `replace` → `{"ok": true, "document_id": "1AbC...", "action": "replaced", "occurrences": 3}`
- `style` → `{"ok": true, "document_id": "1AbC...", "action": "styled", "occurrences": 1}`
- `delete` → `{"ok": true, "document_id": "1AbC...", "action": "deleted", "occurrences": 1}`

After `create`, give the user the `url` so they can open the document. After an
edit, confirm what changed (e.g. "Replaced 3 occurrences of 'Rome' with
'Milan'.") so a mis-heard word is caught immediately.

When a `replace` or `delete` returns `"occurrences": 0` with a `note`, relay the
note — the text wasn't in the document, so nothing changed.

## Common flows

### "Write me a doc titled Trip Plan with a first line, then add a day."
```
create --title "Trip Plan" --text "Rome trip\n"
  → "Created Trip Plan: https://docs.google.com/document/d/<id>/edit"
append <id> --text "Day 1: fly to Rome"
  → "Added it."
```

### "Make the title bold and turn 'Rome trip' into a heading."
```
style <id> --find "Rome trip" --heading 1
style <id> --find "Rome trip" --bold
```

### "Change Rome to Milan everywhere and read it back."
```
replace <id> --find "Rome" --with "Milan"
read <id>
```

### "Delete the 'do not send' warning."
```
# confirm the exact text with the user first, then:
delete <id> --find "do not send" --confirm
```

## When a verb reports an error

- `"couldn't find '<x>' in the document…"` (from `insert`/`style`) → the anchor
  text isn't in the document. Read it back with `read` to see the actual text,
  then retry with text that appears.
- A `"...not found"` or permission error on an existing `<doc_id>` → the document
  isn't shared with the agent. Tell the user it needs to be shared (or dropped in
  the shared folder); don't try to reach it another way.
- `"...has not been used in project…"` / `"Access Not Configured"` → the Docs API
  isn't enabled yet for the project. Point the user to `README.md`.
- Any credential/config error (`GOOGLE_APPLICATION_CREDENTIALS…`, `auth/build
  failed`, `GOOGLE_DOCS_FOLDER_ID not set`) → the skill isn't configured. Point
  the user to `README.md`.

Always ask the user for guidance when there is an error; do not proactively try
to resolve errors yourself.

## Empty results

`read` with an empty `text` means the document genuinely has no text yet — say so
plainly ("that document is empty"); don't re-check or speculate.
