#!/usr/bin/env python3
"""e2e-throwaway — record a single short note, one note per note file.

Verbs (each prints ONE JSON object on stdout; exit 1 on error):
  add     --text "<note>"                   record a short note
  show    --note "<id>"                     read back one note
  list                                            all note ids, newest first
  delete  --note "<id>" --confirm           remove one note (needs --confirm)

Notes live in per-note files under $E2E_NOTES_DIR (default
~/.local/share/e2e-throwaway/notes/). A note id is the file's stem:
"YYYYMMDD-HHMM-<slug>".
"""
import datetime
from pathlib import Path
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from skill_json import ArgumentParser  # noqa: E402
from skill_json import fail  # noqa: E402
from skill_json import guard  # noqa: E402
from skill_json import ok  # noqa: E402


def notes_dir() -> Path:
    import os

    d = os.environ.get("E2E_NOTES_DIR")
    if d:
        return Path(d)
    return Path.home() / ".local" / "share" / "e2e-throwaway" / "notes"


def note_path(note_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", note_id):
        fail("note id must be letters, digits, dot, dash or underscore")
    return notes_dir() / (note_id + ".md")


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug[:40].rstrip("-") or "note"


def cmd_add(args) -> None:
    text = args.text.strip()
    if not text:
        fail("the note is empty")
    if len(text) > 500:
        fail("a note is a single short line (500 characters or fewer)")
    now = datetime.datetime.now()
    note_id = "%s-%s" % (now.strftime("%Y%m%d-%H%M"), slugify(text))
    # Same minute, same words -> a new id, so two identical notes survive.
    path = note_path(note_id)
    counter = 1
    while path.exists():
        counter += 1
        path = note_path("%s-%d" % (note_id, counter))
    d = notes_dir()
    d.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    ok(note=path.stem, created=True)


def cmd_show(args) -> None:
    path = note_path(args.note)
    if not path.exists():
        fail("note '%s' not found - run list to see the notes" % args.note)
    text = path.read_text(encoding="utf-8").strip()
    ok(note=path.stem, text=text)


def cmd_list(_args) -> None:
    d = notes_dir()
    if not d.exists():
        ok(notes=[])
    ids = sorted(
        (p.stem for p in d.iterdir() if p.is_file() and p.suffix == ".md"),
        reverse=True,
    )
    ok(notes=ids)


def cmd_delete(args) -> None:
    path = note_path(args.note)
    if not path.exists():
        fail("note '%s' not found - run list to see the notes" % args.note)
    path.unlink()
    ok(note=path.stem, deleted=True)


@guard
def main() -> None:
    parser = ArgumentParser(
        prog="e2e_note.py",
        description="Record short notes, one per file.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_add = subs.add_parser("add", help="record a short note")
    p_add.add_argument(
        "--text", required=True, help="the note text (one short line)")
    p_add.set_defaults(fn=cmd_add)

    p_show = subs.add_parser("show", help="read back one note")
    p_show.add_argument("--note", required=True, help="the note id (from list)")
    p_show.set_defaults(fn=cmd_show)

    p_list = subs.add_parser("list", help="all note ids, newest first")
    p_list.set_defaults(fn=cmd_list)

    p_del = subs.add_parser("delete", help="remove one note")
    p_del.add_argument("--note", required=True, help="the note id (from list)")
    p_del.add_argument(
        "--confirm", action="store_true",
        help="required: user approved this exact delete")
    p_del.set_defaults(fn=cmd_delete)

    args = parser.parse_args()
    if args.verb == "delete" and not args.confirm:
        fail("delete requires --confirm - ask the user to approve first")
    args.fn(args)


if __name__ == "__main__":
    main()
