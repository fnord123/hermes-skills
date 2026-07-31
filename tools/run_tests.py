#!/usr/bin/env python3
"""Run every skill's test suite.

WHY THIS EXISTS. Four files named `*_test.py` sat in this repo and CI ran none of them - it
checked conventions and vendored copies only. Tests that nothing runs are not a safety net; they
are a claim of one. Two of the four had in fact stopped working and nobody knew.

WHAT COUNTS AS A TEST. A file named `<something>_test.py` under a skill's `scripts/`. Files that
drive a real browser or hit a live service and assert nothing are PROBES, not tests - they are
named `*_probe.py` and are never run here. That distinction is the point: a "test" that needs
bambulab.com to be up tells you about the weather, not about your code.

Tests must be self-contained and offline. A test that needs a wheel or a network round trip is a
test that quietly stops running - which is exactly how this repo ended up with four unrun files.
A suite whose imports are unavailable is reported as SKIP, loudly, never as a pass.

    python3 tools/run_tests.py            # all skills
    python3 tools/run_tests.py web-access # one skill
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Vendored dependencies ship their own test suites - pallo-logistics alone carries greenlet's.
# Running those tells us nothing about this repo and takes minutes.
EXCLUDE = (".venv", "site-packages", "node_modules", ".git", "__pycache__")

TIMEOUT = 300


def discover(only=None):
    found = []
    for skill in sorted(os.listdir(ROOT)):
        sdir = os.path.join(ROOT, skill, "scripts")
        if not os.path.isdir(sdir) or (only and skill != only):
            continue
        for dirpath, dirnames, filenames in os.walk(sdir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE]
            if any(x in dirpath for x in EXCLUDE):
                continue
            for fn in sorted(filenames):
                if fn.endswith("_test.py"):
                    found.append((skill, os.path.join(dirpath, fn)))
    return found


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    tests = discover(only)
    if not tests:
        print("no test files found%s" % (" for %s" % only if only else ""))
        return 0

    passed, skipped, failed = [], [], []
    for skill, path in tests:
        rel = os.path.relpath(path, ROOT)
        try:
            r = subprocess.run([sys.executable, path], capture_output=True, text=True,
                               timeout=TIMEOUT, cwd=os.path.dirname(path))
        except subprocess.TimeoutExpired:
            failed.append((rel, "timed out after %ds" % TIMEOUT))
            print("FAIL  %-58s timed out" % rel)
            continue

        out = (r.stdout or "") + (r.stderr or "")
        # A missing dependency is not a failing test - but it is not a passing one either.
        if r.returncode != 0 and "ModuleNotFoundError" in out:
            mod = out.rsplit("ModuleNotFoundError", 1)[-1].strip().splitlines()[0]
            skipped.append((rel, mod))
            print("SKIP  %-58s %s" % (rel, mod[:40]))
        elif r.returncode != 0:
            tail = [l for l in out.strip().splitlines() if l.strip()][-1:] or ["no output"]
            failed.append((rel, tail[0][:200]))
            print("FAIL  %-58s %s" % (rel, tail[0][:60]))
        else:
            passed.append(rel)
            print("ok    %s" % rel)

    print("\n%d passed, %d skipped, %d failed" % (len(passed), len(skipped), len(failed)))
    for rel, why in skipped:
        print("  skipped: %s (%s)" % (rel, why[:80]))
    for rel, why in failed:
        print("  FAILED : %s" % rel)
        print("           %s" % why)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
