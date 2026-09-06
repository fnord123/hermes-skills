# e2e-throwaway

Record a single short note to a per-note file. This is a throwaway end-to-end
skill — it exists to exercise the review pipeline and is safe to delete after
the run.

## What this is for

- *"Jot down: 'call the plumber'."*
- *"What did I note about the plumber?"*
- *"Delete that note."*

## What this is NOT for

- **Long documents** — a note is a single short line (500 characters or
  fewer).
- **General record-keeping** — no tasks, no schedules, no multi-note editing.

## How it works

Each note is one file under the notes directory:

```
$E2E_NOTES_DIR/                    # default ~/.local/share/e2e-throwaway/notes/
  20260905-1432-call-the-plumber.md   # stem = note id; body = the note text
```

The note id is `YYYYMMDD-HHMM-<slug>` at record time, plus a numeric suffix if
the same minute and same words collide. `list` returns ids newest first (the
timestamp prefix makes the sort the sort order).

The one script is `scripts/e2e_note.py` (verb table in `SKILL.md`). It obeys
the house JSON contract via the vendored `scripts/skill_json.py`: one JSON
object on stdout, `ok: false` + exit 1 on failure.

`delete` is destructive, so it sits behind `--confirm` (house footgun guard);
the model is instructed to get the user's approval for the exact note first.

## Rationale

- **One note per file** is the whole feature: no index file, no database, no
  parsing — a note is just a file with a timestamp-prefixed name, and "what
  notes exist" is a directory listing.
- **The env override** (`E2E_NOTES_DIR`) keeps tests and throwaway runs out of
  the real notes directory.
