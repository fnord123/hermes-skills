#!/usr/bin/env python3
"""Keep vendored copies identical to their single source, and fail the build when they are not.

Hermes needs each skill to be self-contained: `hermes skills install` copies "SKILL.md plus the
exact local files it references", so a symlink arrives broken and a git submodule arrives
empty. Real files have to sit under each skill's scripts/. But four copies of the same module
is precisely how this repo ended up with four implementations of "which lab markers are out of
range" that returned four different answers - and the one wired to the confirmation gate was
the wrong one.

So: one editable source, generated copies, and a check that fails CI the moment they diverge.
This is not hypothetical. tools/skill_json.py was vendored into two skills earlier today and
one copy had already drifted before the day was out.

    python3 tools/vendor.py check     # exit 1 on any divergence  (CI runs this)
    python3 tools/vendor.py sync      # rewrite every copy from its source
    python3 tools/vendor.py status    # what is vendored where

Consumers are discovered by filename rather than listed, so a copy someone adds by hand is
governed automatically instead of silently escaping the check.
"""

import argparse
import difflib
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules with exactly one editable copy. Everything else named the same under a skill's
# scripts/ is a generated copy of it.
SOURCES = {
    "skill_json.py": "tools/skill_json.py",
    "pipeline.py": "analysis-engine/pipeline.py",
}

HEADER = (
    "# ---------------------------------------------------------------------------\n"
    "# GENERATED COPY - do not edit here.\n"
    "# Source: {src}\n"
    "# Edit the source, then run: python3 tools/vendor.py sync\n"
    "# CI fails if this file diverges (tools/vendor.py check).\n"
    "# ---------------------------------------------------------------------------\n"
)
HEADER_MARK = "# GENERATED COPY - do not edit here."


def _strip_header(text):
    """Content without the provenance header, so copies compare on substance."""
    if HEADER_MARK not in text:
        return text
    lines = text.split("\n")
    out, in_header = [], False
    for i, l in enumerate(lines):
        if l.startswith("# ---") and i + 1 < len(lines) and HEADER_MARK in lines[i + 1]:
            in_header = True
            continue
        if in_header:
            if l.startswith("# ---"):
                in_header = False
            continue
        out.append(l)
    return "\n".join(out)


def consumers(name):
    """Every vendored copy of `name`, excluding the source itself."""
    src = os.path.join(ROOT, SOURCES[name])
    found = []
    for p in glob.glob(os.path.join(ROOT, "*", "scripts", "**", name), recursive=True):
        if os.path.abspath(p) != os.path.abspath(src) and ".venv" not in p:
            found.append(p)
    return sorted(found)


def _rel(p):
    return os.path.relpath(p, ROOT)


def cmd_status(args):
    for name, rel_src in SOURCES.items():
        cs = consumers(name)
        print("  %s  <-  %s" % (name, rel_src))
        if not cs:
            print("      (no copies)")
        for c in cs:
            print("      %s" % _rel(c))
    return 0


def cmd_check(args):
    bad = 0
    for name, rel_src in SOURCES.items():
        src_text = open(os.path.join(ROOT, rel_src), encoding="utf-8").read()
        for c in consumers(name):
            copy_text = open(c, encoding="utf-8").read()
            if _strip_header(copy_text).rstrip() != src_text.rstrip():
                bad += 1
                print("DRIFT: %s differs from %s" % (_rel(c), rel_src))
                if args.diff:
                    d = difflib.unified_diff(
                        src_text.splitlines(), _strip_header(copy_text).splitlines(),
                        fromfile=rel_src, tofile=_rel(c), lineterm="", n=1)
                    for line in list(d)[:40]:
                        print("    " + line)
            elif args.verbose:
                print("ok:    %s" % _rel(c))
    if bad:
        print("\n%d vendored copy(ies) diverged. Run: python3 tools/vendor.py sync" % bad)
        return 1
    print("all vendored copies match their source")
    return 0


def cmd_sync(args):
    n = 0
    for name, rel_src in SOURCES.items():
        src_text = open(os.path.join(ROOT, rel_src), encoding="utf-8").read()
        for c in consumers(name):
            new = HEADER.format(src=rel_src) + src_text
            old = open(c, encoding="utf-8").read()
            if old == new:
                continue
            if args.dry_run:
                print("would update %s" % _rel(c))
            else:
                open(c, "w", encoding="utf-8").write(new)
                print("updated %s" % _rel(c))
            n += 1
    print("%d copy(ies) %s" % (n, "would change" if args.dry_run else "updated"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("check", cmd_check), ("sync", cmd_sync)):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--verbose", "-v", action="store_true")
        p.add_argument("--diff", action="store_true", help="show what diverged")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
