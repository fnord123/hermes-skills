---
name: google-docs
description: >
  Used to read contents & comments, and write google docs. Create a new
  document, add or insert text, find-and-replace, format text
  (bold/italic/underline or headings), remove text, or list every comment on
  a document with the exact text each one is anchored to. Works through a
  pre-configured service account; new documents land in the agent's shared
  Drive folder, and existing documents are reachable once shared with the
  agent. PREFER THIS SKILL for anything about a Google Doc / document's
  contents or its comments. It is a different, self-contained setup from
  `google-workspace` (which is OAuth-based) — reach for this one for Docs.
  Finds documents by name or content, so the user never needs a document ID
  or URL. Activate on any of: "google doc", "doc", "document", "write a doc",
  "create a document", "add to the doc", "insert into the document", "edit
  the doc", "find and replace in the doc", "make this a heading", "bold this
  in the doc", "read the doc", "what does the document say", "what comments
  are on the doc", "what did people comment on", "find my doc", "search my
  docs", "which docs do I have", "it's in my docs".
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
- **Read the comments** on a document — what people wrote and the text each
  comment is anchored to.
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

One script at `${HERMES_SKILL_DIR}/scripts/docs.py`, invoked as
`python3 <path> <verb> [args]`. Each call prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with exit 1).

Editing verbs take a `<doc_id>` — the document's ID (the long string in its URL,
`https://docs.google.com/document/d/<doc_id>/edit`). `create` returns that id
and url; keep them to edit the same document afterward.

| Verb | Purpose |
|---|---|
| `find [query] [--title-only] [--anywhere] [--limit N]` | **Finds documents without an id.** No query lists everything in the folder, newest first. A query matches the title *and* the body text. `--anywhere` looks beyond the folder at everything shared with the agent. Returns `document_id`, `title`, `url`, `modified` for each. |
| `create --title "<t>" [--text "<initial>"]` | Creates a new document in the shared folder. Returns its `document_id` and `url`. |
| `read <doc_id>` | Gets a document's title and full plain text. |
| `read-comments <doc_id>` | Lists every comment on the document, each with its `quoted_anchor` — the exact highlighted text the comment was attached to — plus author, resolved state, and replies. Pagination is handled internally. |
| `append <doc_id> --text "<t>"` | Adds text as a new paragraph at the end. |
| `insert <doc_id> --text "<t>" --after "<anchor>"` | Inserts text right after the first occurrence of the anchor text. |
| `insert <doc_id> --text "<t>" --at-start` | Inserts text at the very beginning. |
| `replace <doc_id> --find "<s>" --with "<s>"` | Replaces every occurrence of one string with another. |
| `style <doc_id> --find "<text>" [--bold] [--italic] [--underline] [--heading N]` | Formats every occurrence of the text. `--heading` 1–6 makes its paragraph a heading; 0 returns it to normal. |
| `delete <doc_id> --find "<text>" --confirm` | **Destructive.** Removes every occurrence of the text. Needs `--confirm`. |
| `insert-image <doc_id> (--url "<public_url>" \| --file "<local_path>") (--replace "<placeholder>" \| --after "<anchor>" \| --at-start) [--width N] [--height N]` | Inserts an image, either from a public HTTPS URL or a **local file** (PNG/JPEG/GIF). Placement is one of: `--replace` (swap a placeholder like `[IMAGE:x]` for the image), `--after` (right after some text), `--at-start`, or nothing (end of document). |
| `resize-image <doc_id> (--url "<public_url>" \| --file "<local_path>") (--nth N \| --after "<anchor>") [--width N] [--height N]` | Resizes an existing image. Pass its source again (`--url` or `--file`) — the resize re-inserts it. |
| `delete-image <doc_id> (--nth N \| --after "<anchor>") --confirm` | **Destructive.** Removes an image. Needs `--confirm`. |

Add `--match-case` to `insert --after`, `replace`, `style`, `delete`, or the
image verbs when the match must respect capitalization; by default matching
ignores case.

**Images:** give the image with EITHER `--url` (a public HTTPS image URL) OR
`--file` (a path to a local PNG/JPEG/GIF, which is uploaded for you) — not both.
`--width`/`--height` are in points; **give just `--width` and the height scales
to keep the image's aspect ratio** (a full text-column width is ~468). Address an
existing image by `--nth N` (1-based, in reading order) or `--after "<nearby
text>"`.

## Turning the user's words into calls

Resolve loose phrasing to a verb BEFORE calling. Editing verbs need the
`document_id` of the document in play — the one from the last `create`, or one
the user names.

| User said | Call |
|---|---|
| "start a doc called Trip Plan" | `create --title "Trip Plan"` |
| "make a doc titled Notes that says 'Hello team'" | `create --title "Notes" --text "Hello team"` |
| "what does the doc say / read it back" | `read <doc_id>` |
| "what comments are on the doc / what did people comment on" | `read-comments <doc_id>` |
| "add a line: 'Bring sunscreen'" | `append <doc_id> --text "Bring sunscreen"` |
| "put a title line at the top: 'Agenda'" | `insert <doc_id> --text "Agenda\n" --at-start` |
| "after 'Day 1' add 'Fly to Rome'" | `insert <doc_id> --text " Fly to Rome" --after "Day 1"` |
| "change every 'Rome' to 'Milan'" | `replace <doc_id> --find "Rome" --with "Milan"` |
| "make 'Agenda' a heading" | `style <doc_id> --find "Agenda" --heading 1` |
| "bold the word 'urgent'" | `style <doc_id> --find "urgent" --bold` |
| "remove the line 'draft — do not send'" | `delete <doc_id> --find "draft — do not send" --confirm` (confirm first) |
| "put this banner where it says [IMAGE:Gents]" | `insert-image <doc_id> --url "https://…/banner.jpg" --replace "[IMAGE:Gents]" --width 468` |
| "add the logo after the title" | `insert-image <doc_id> --url "https://…/logo.png" --after "Trip Plan"` |
| "insert this image file I have at ~/pics/map.png" | `insert-image <doc_id> --file "~/pics/map.png" --after "Directions"` |
| "make the first image smaller / 300pt wide" | `resize-image <doc_id> --url "https://…/banner.jpg" --nth 1 --width 300` |
| "remove the second image" | `delete-image <doc_id> --nth 2 --confirm` (confirm first) |

Notes:
- When the text should start on its own line, include a `\n` in `--text` (as in
  the title-at-top example).
- If the user asks to edit a document but no `document_id` is in play, ask which
  document (or offer to `create` one). Don't guess an id.

## Output shape

- `create` → `{"ok": true, "document_id": "1AbC...", "title": "Trip Plan", "url": "https://docs.google.com/document/d/1AbC.../edit"}`
- `read` → `{"ok": true, "document_id": "1AbC...", "title": "Trip Plan", "text": "Day 1\nFly to Rome\n..."}`
- `read-comments` → `{"ok": true, "document_id": "1AbC...", "count": 2, "comments": [{"id": "...", "content": "Can we ship this Friday?", "author": "Jane", "quoted_anchor": "launch on Monday", "anchor_segment": "kix.abc123", "resolved": false, "created": "2026-08-24T23:00:00Z", "replies": []}]}`
- `append` → `{"ok": true, "document_id": "1AbC...", "action": "appended", "characters": 16}`
- `insert` → `{"ok": true, "document_id": "1AbC...", "action": "inserted", "at_index": 42, "characters": 12}`
- `replace` → `{"ok": true, "document_id": "1AbC...", "action": "replaced", "occurrences": 3}`
- `style` → `{"ok": true, "document_id": "1AbC...", "action": "styled", "occurrences": 1}`
- `delete` → `{"ok": true, "document_id": "1AbC...", "action": "deleted", "occurrences": 1}`
- `insert-image` → `{"ok": true, "document_id": "1AbC...", "action": "image_inserted"}`
- `resize-image` → `{"ok": true, "document_id": "1AbC...", "action": "image_resized"}`
- `delete-image` → `{"ok": true, "document_id": "1AbC...", "action": "image_deleted"}`

After `create`, give the user the `url` so they can open the document. After an
edit, confirm what changed (e.g. "Replaced 3 occurrences of 'Rome' with
'Milan'.") so a mis-heard word is caught immediately.

When a `replace` or `delete` returns `"occurrences": 0` with a `note`, relay the
note — the text wasn't in the document, so nothing changed.

## The user rarely knows a document id

Assume they don't. When they refer to a document by what it *is* rather than by id or URL —
"my regimen doc", "the trip plan", "search my docs, it's in there", "the one I made yesterday"
— start with `find`, then use the id it returns.

    # "the regimen is in my docs somewhere"
    python3 ${HERMES_SKILL_DIR}/scripts/docs.py find regimen
    # -> {"count": 1, "documents": [{"document_id": "1B6l…", "title": "David's Supplement & Medication Regimen", …}]}
    python3 ${HERMES_SKILL_DIR}/scripts/docs.py read 1B6l…

Pick a search word from what the user said — a distinctive noun beats their full phrasing.
`find` matches body text too, so a doc whose title never says "regimen" is still found.

- **Exactly one match** → use it.
- **Several matches** → show the titles and ask which one. Do not guess.
- **No matches** → try a different word, or `--anywhere` to look outside the folder, before
  telling the user it isn't there. Bare `find` lists everything, which is the fastest way to
  see what exists.

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

### "Build a doc with banner images for each section."
Write the text first with a placeholder where each image goes, then swap each
placeholder for its image — one `insert-image --replace` per banner:
```
create --title "Theme Nights" --text "Thursday — GENTS & MAIDS\n[IMAGE:Gents]\nFriday — DESERT OF DESIRE\n[IMAGE:Desert]"
insert-image <id> --url "https://…/gents.jpg"  --replace "[IMAGE:Gents]"  --width 468
insert-image <id> --url "https://…/desert.jpg" --replace "[IMAGE:Desert]" --width 468
```

## When a verb reports an error

- `"couldn't find '<x>' in the document…"` (from `insert`/`style`/image verbs) →
  the anchor or placeholder text isn't in the document. Read it back with `read`
  to see the actual text, then retry with text that appears.
- An image error mentioning the URL / `"Invalid image"` / fetch failure → the
  `--url` isn't a public, directly-reachable image (PNG/JPEG/GIF). Ask the user
  for a public image URL, or — if the image is a file on disk — insert it with
  `--file "<path>"` instead (that path uploads the file for you).
- `"isn't a supported image type"` / `"no such image file"` (from `--file`) → the
  path is wrong or the file isn't a PNG/JPEG/GIF. Confirm the path with the user.
- `"the document has N images; say which…"` (image verbs) → be specific with
  `--nth N` or `--after "<nearby text>"`.
- A `"...not found"` or permission error on an existing `<doc_id>` → the document
  isn't shared with the agent. Tell the user it needs to be shared (or dropped in
  the shared folder); don't try to reach it another way.
- `"...has not been used in project…"` / `"Access Not Configured"` → the Docs API
  isn't enabled yet for the project. Point the user to `README.md`.
- Any credential/config error (`GOOGLE_APPLICATION_CREDENTIALS…`, `auth/build
  failed`, `GOOGLE_DOCS_FOLDER_ID not set`) → the skill isn't configured. Point
  the user to `README.md`.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`read` with an empty `text` means the document genuinely has no text yet — say so
plainly ("that document is empty"); don't re-check or speculate.
