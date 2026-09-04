#!/usr/bin/env python3
"""Extract the card inventory from rx.py and fanout.py, as markdown.

WHY GENERATED. A hand-written table of 21 card types is wrong within a week — this pipeline
gained three new card types in a single day. The inventory below is read out of the source, so
it cannot drift silently; ARCHITECTURE.md embeds it between markers and a test fails the build
when the file on disk no longer matches what the code would produce.

Only the mechanical facts are extracted: title, which profile runs it, what gates it, how long
it may run. WHY a card exists, and why the stages are ordered as they are, is prose and lives in
ARCHITECTURE.md around this table — a generator cannot know it and should not pretend to.

    python3 cardmap.py            # print the table
    python3 cardmap.py --check    # exit 1 if ARCHITECTURE.md is stale
"""

import argparse
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ARCHITECTURE.md lives at the skill root, one level above scripts/.
DOC = os.path.join(os.path.dirname(HERE), "ARCHITECTURE.md")
BEGIN = "<!-- BEGIN GENERATED CARD MAP -->"
END = "<!-- END GENERATED CARD MAP -->"

# rx.py:     create(args, title, body, minutes, priority, parents=(), key=None, assignee=...)
# fanout.py: create(args, title, assignee, body, parents=(), runtime="45m", priority=0)
SIGS = {
    "rx.py": ["args", "title", "body", "minutes", "priority"],
    "fanout.py": ["args", "title", "assignee", "body", "parents"],
}
DEFAULT_ASSIGNEE = {"rx.py": "rx-intake", "fanout.py": "(varies)"}


CONSTS = {}


def _load_consts(tree):
    """Module-level `NAME = "literal"` so the table shows 45m, not {PART_RUNTIME}."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    CONSTS[t.id] = str(node.value.value)


def _lit(node):
    """A readable rendering of an argument that may be a literal, a name, or a format call."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return CONSTS.get(node.id, "{%s}" % node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(_lit(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return "{...}"
    if isinstance(node, ast.BinOp):                    # "Transcribe labs: %s" % name
        return _lit(node.left)
    if isinstance(node, ast.Attribute):
        return "{%s}" % node.attr
    if isinstance(node, ast.Call):
        return _lit(node.func) if not node.args else _lit(node.args[0])
    if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.List)):
        return "(computed)"
    return "(computed)"


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return _lit(k.value)
    return None


def cards():
    rows = []
    for fname, positional in SIGS.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        _load_consts(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "create"):
                continue
            pos = {positional[i]: a for i, a in enumerate(node.args) if i < len(positional)}
            title = _lit(pos["title"]) if "title" in pos else "?"
            if not title or title == "?":
                continue
            assignee = (_lit(pos["assignee"]) if "assignee" in pos
                        else _kw(node, "assignee") or DEFAULT_ASSIGNEE[fname])
            parents = _kw(node, "parents")
            if parents is None and "parents" in pos:
                parents = _lit(pos["parents"])
            runtime = _kw(node, "runtime")
            if runtime is None and "minutes" in pos:
                runtime = _lit(pos["minutes"]) + "m"
            rows.append({
                "title": title.strip(),
                "assignee": (assignee or "").strip(),
                "parents": (parents or "—").strip() or "—",
                "runtime": (runtime or "—").strip(),
                # FILE only, no line number. Including the line made the map "stale" on any
                # edit above a create() call - churn that has nothing to do with cards, which
                # trains you to regenerate reflexively and kills the alarm's meaning. The check
                # should fire when a card is added, removed or renamed, and not otherwise.
                "source": fname,
            })
    rows.sort(key=lambda r: (r["source"], r["title"]))
    return rows


def table():
    out = [BEGIN,
           "",
           "| card | runs as | waits on | runtime | defined in |",
           "|---|---|---|---|---|"]
    for r in cards():
        out.append("| `%s` | %s | %s | %s | `%s` |"
                   % (r["title"], r["assignee"], r["parents"], r["runtime"], r["source"]))
    out += ["", "_%d card types. Generated by `cardmap.py`; do not edit by hand._" % len(cards()),
            END]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if ARCHITECTURE.md's generated block is out of date")
    ap.add_argument("--write", action="store_true", help="update ARCHITECTURE.md in place")
    args = ap.parse_args()

    fresh = table()
    if not (args.check or args.write):
        print(fresh)
        return 0

    if not os.path.exists(DOC):
        print("ARCHITECTURE.md not found at %s" % DOC, file=sys.stderr)
        return 1
    doc = open(DOC, encoding="utf-8").read()
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), doc, re.S)
    if not m:
        print("ARCHITECTURE.md has no generated block (%s ... %s)" % (BEGIN, END), file=sys.stderr)
        return 1

    if m.group(0) == fresh:
        if args.check:
            print("ARCHITECTURE.md card map is current (%d cards)." % len(cards()))
        return 0
    if args.check:
        print("ARCHITECTURE.md card map is STALE — a card was added, removed or renamed.\n"
              "Regenerate with: python3 cardmap.py --write", file=sys.stderr)
        return 1
    open(DOC, "w", encoding="utf-8").write(doc[:m.start()] + fresh + doc[m.end():])
    print("Updated the card map in ARCHITECTURE.md (%d cards)." % len(cards()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
