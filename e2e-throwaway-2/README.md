# e2e-throwaway-2

Record one short note to a per-note file. This is a throwaway end-to-end
skill — it exists to exercise the review pipeline and is safe to delete
after the run.

## What this is for

- *"Jot down: 'call the plumber'."*

## What this is NOT for

- **Long text** — a note is one short line (500 characters or fewer).
- **Reading or deleting notes** — this skill stores notes only; it has no
  list or delete verb.

## How it works

Each note is one file under the notes directory:

```
$E2E2_NOTES_DIR/                # default ~/.local/share/e2e-throwaway-2/notes/
  note-20260906-143211-call-the-plumber.txt   # stem = note id; body = the note
```

The file name is `note-<timestamp>.txt`: a `YYYYMMDD-HHMMSS` stamp at
record time, plus a slug of the note text, plus a numeric suffix if the
same second and same words collide.

The one script is `scripts/note2.py` (verb table in `SKILL.md`). It obeys
the house JSON contract via the vendored `scripts/skill_json.py`: one JSON
object on stdout, `ok: false` + exit 1 on failure.

## Rationale

- **One note per file** is the whole feature: no index file, no database,
  no parsing — a note is just a file with a timestamp-prefixed name.
- **The env override** (`E2E2_NOTES_DIR`) keeps tests and throwaway runs
  out of the real notes directory.
