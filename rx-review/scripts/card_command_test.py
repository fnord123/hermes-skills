#!/usr/bin/env python3
"""Every command a card tells a worker to run must be one the terminal hook permits.

WHY THIS EXISTS. The card templates and hooks/terminal-pipeline-only.sh are two files that are
each correct on their own and can still disagree. On 2026-07-31 two templates printed commands
wrapped over two lines with a backslash, and the hook refused multi-line commands - so a worker
copying the instruction verbatim would have been blocked mid-run, on a card that had already
done its expensive work. Nothing in either file was wrong by itself. The bug lived in the gap,
and it was found by reading code, which is not a method that scales.

This closes the gap from the pipeline's side: it pulls the commands out of the card text the
workers actually receive and asserts the guard admits each one. The hook's own suite
(hooks/test-terminal-pipeline-only.sh) covers the other direction - what it must refuse.

Strings are read with ast, so escape sequences are decoded exactly as the worker sees them
rather than as they appear in the source. That matters: `\\\\\\n` in a source file is a real
backslash-newline in the card, which is the whole bug this test was written for.

    python3 card_command_test.py
"""

import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "terminal-pipeline-only.sh")
BOARD_DB = os.path.expanduser("~/.hermes/kanban/boards/rx-review/kanban.db")

# Card text is generated from these. Any file that writes instructions for a worker belongs here.
SOURCES = ["rx.py", "fanout.py", "lenses.py"]

# A command line as it appears in card text: an indented `python3 ...`, continuing over any
# backslash-wrapped lines.
CMD = re.compile(r"^[ \t]*(python3?[ \t]+[^\n]*(?:\\\n[^\n]*)*)", re.M)

# Card text is a template; a worker receives it filled in. Substitute something plausible for
# every {placeholder} so the guard sees the shape of a real command.
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
# `<like this>` is a placeholder too. A worker replaces it before sending, so the test must as
# well - otherwise every such command trips the shell-redirect guard and the result is noise.
# (The card text no longer uses this form, precisely because a literal copy WOULD be refused,
# with a message about shell operators that says nothing about the real mistake.)
ANGLE = re.compile(r"<[a-z][a-z0-9 _-]*>", re.I)
FILLERS = {
    "pdf": "/home/dputzolu/hermes-skills/rx-review/scripts/inputs/labs/report.pdf",
    "pages": "8", "first": "1", "last": "4", "slug": "report", "tag": "p1-4",
    "inputs": "/home/dputzolu/hermes-skills/rx-review/scripts/inputs",
    "reports": "/home/dputzolu/.hermes/reports/rx-review/current",
    "tilde": "~/hermes-skills/rx-review/scripts/inputs",
}


def commands():
    """Every distinct command found in a string literal in the card-generating sources."""
    seen = {}
    for name in SOURCES:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for m in CMD.finditer(node.value):
                cmd = m.group(1).strip()
                if "python3" not in cmd:
                    continue
                filled = PLACEHOLDER.sub(lambda mm: FILLERS.get(mm.group(1), "X"), cmd)
                filled = ANGLE.sub("their exact words", filled)
                # A trailing continuation with nothing after it is an artefact of slicing a
                # template, not something a worker would ever send.
                filled = filled.rstrip("\\").rstrip()
                seen.setdefault(filled, (name, node.lineno))
    return seen


def hook_allows(cmd):
    """(allowed, reason). Runs the real hook, as the tool executor would."""
    payload = json.dumps({"tool_name": "terminal", "tool_input": {"command": cmd}})
    env = dict(os.environ, HERMES_KANBAN_DB=BOARD_DB)
    r = subprocess.run(["bash", HOOK], input=payload, capture_output=True, text=True,
                       env=env, timeout=30)
    out = (r.stdout or "").strip()
    if not out:
        return True, ""
    try:
        return False, (json.loads(out).get("reason") or "")[:150]
    except ValueError:
        return False, out[:150]


def main():
    if not os.path.exists(HOOK):
        print("hook not found at %s — nothing to check against" % HOOK)
        return 1

    cmds = commands()
    if not cmds:
        print("FAIL: found no commands in %s — the extractor is broken, not the templates"
              % ", ".join(SOURCES))
        return 1

    print("every command a card instructs must pass the terminal hook\n")
    bad = []
    for cmd, (src, line) in sorted(cmds.items()):
        allowed, why = hook_allows(cmd)
        shown = cmd if len(cmd) <= 76 else cmd[:73] + "..."
        if allowed:
            print("  ok   %s" % shown)
        else:
            bad.append((cmd, src, line, why))
            print(" FAIL  %s" % shown)
            print("         %s:%d — %s" % (src, line, why))

    print("\n%d command(s) checked, %d refused" % (len(cmds), len(bad)))
    if bad:
        print("\nA card instructs something its own guard forbids. Fix whichever is wrong:")
        print("  - the template, if the command shape is avoidable (prefer ONE line)")
        print("  - the allowlist, if the command is legitimate and newly needed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
