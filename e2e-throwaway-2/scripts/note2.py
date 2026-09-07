#!/usr/bin/env python3
"""e2e-throwaway-2 — record one short note, one note per note file.

Verbs (each prints ONE JSON object on stdout; exit 1 on error):
  add    --text "<note>"    record a short note to its own note file

Notes live in per-note files under $E2E2_NOTES_DIR (default
~/.local/share/e2e-throwaway-2/notes/). A note file is named
"note-YYYYMMDD-HHMMSS-<slug>.txt"; the stem is the note id.
"""
import datetime
import os
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
    d = os.environ.get("E2E2_NOTES_DIR")
    if d:
        return Path(d)
    return Path.home() / ".local" / "share" / "e2e-throwaway-2" / "notes"


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug[:40].rstrip("-") or "note"


def cmd_add(args) -> None:
    text = args.text.strip()
    if not text:
        fail("the note is empty")
    if len(text) > 500:
        fail("a note is one short line (500 characters or fewer)")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = "%s-%s" % (stamp, slugify(text))
    d = notes_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("note-%s.txt" % base)
    counter = 1
    while path.exists():
        counter += 1
        path = d / ("note-%s-%d.txt" % (base, counter))
    path.write_text(text + "\n", encoding="utf-8")
    ok(note=path.stem, file=str(path))


@guard
def main() -> None:
    parser = ArgumentParser(
        prog="note2.py",
        description="Record one short note, one note per file.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_add = subs.add_parser("add", help="record a short note")
    p_add.add_argument(
        "--text", required=True, help="the note text (one short line)")
    p_add.set_defaults(fn=cmd_add)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
