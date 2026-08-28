#!/usr/bin/env python3
"""Negative-test battery for lint_skills.py — the linter's own test suite.

WHY THIS EXISTS. lint_skills.py is the only mechanical enforcement of CONVENTIONS.md, and
enforcement is the whole point: a convention nobody checks is a convention that decays.
But a linter that only passes on clean inputs has not been proven to work — its rules are
a promise, and a promise with no test is a claim of one (the same argument run_tests.py
makes for skill scripts). Built 2026-08-27 as an ad-hoc audit harness (55 cases) and
committed the same week the robustness fixes landed, so a regression in those fixes shows
up here instead of as the next silent drift.

WHAT COUNTS AS A CASE. One compliant baseline skill; each case mutates exactly one aspect
and asserts which rules must FIRE and which must NOT fire. "Must not fire" matters as much
as "must fire": this linter's own history is a catalogue of false positives — the exit-code
rule firing on the house sys.exit(main()) pattern in 39 of the repo's scripts, CRLF line
endings producing a wall of false criticals, a UTF-8 BOM voiding the whole frontmatter.

GATE TESTS. Three whole-repo behaviours a per-skill case cannot express (the exit code is
the gate, not the finding list): --severity must not bypass a critical, and one unreadable
SKILL.md must not mask the rest of the repo.

HERMETIC. The suite copies the linter into a tempdir and builds fixtures next to it — the
linter resolves its repo ROOT from its own file location, so a copied linter lints a
lab, never the real repo. The suite is NOT named <skill>/scripts/*_test.py, which tools/
run_tests.py discovers; it runs as its own step in .github/workflows/lint.yml.

    python3 tools/test_lint_skills.py     # run the whole battery
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINTER = os.path.join(HERE, "lint_skills.py")

try:
    import yaml  # noqa: F401  (the linter parses frontmatter with PyYAML)
except ImportError:
    # Repo convention (run_tests.py): an unavailable dependency is a loud SKIP, never a pass.
    print("SKIP  tools/test_lint_skills.py (pyyaml not available; pip install pyyaml)")
    sys.exit(0)

# ── fixtures ─────────────────────────────────────────────────────────────────
BASE_SKILLMD = '''---
name: __NAME__
description: >
  Does one thing well. PREFER THIS SKILL when the user asks for that one thing.
  Activate on any of: "do the thing", "thing status".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Thing, Demo]
---

# Thing

## When to use

When the user asks for the thing.

## When NOT to use

When they ask for something else.

## Tools

| Tool | Purpose |
|------|---------|
| `do-thing` | Runs the thing and reports the result. |

## Output

`{"ok": true, "result": "…"}`

## Error handling

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
'''

BASE_README = "# Thing\n\nWhy it exists.\n"

BASE_SCRIPT = '''#!/usr/bin/env python3
import json, sys

def main():
    print(json.dumps({"ok": True}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''

# G1's script: the "pristine is clean" anchor. Carries a literal sys.exit(1) in the
# guard path, so it stays clean under every version of the exit-code rule — the control
# must not move when the rule itself is retuned.
CLEAN_SCRIPT = ('#!/usr/bin/env python3\nimport json, sys\n'
                'def main():\n'
                '    try:\n'
                '        return json.dumps({"ok": True})\n'
                '    except Exception as e:\n'
                '        print(json.dumps({"ok": False, "error": str(e)}))\n'
                '        sys.exit(1)\n'
                'if __name__ == "__main__":\n'
                '    print(main())\n')


def make_skill(lab, name, skillmd=BASE_SKILLMD, readme=BASE_README, script=None,
               extra_dirs=(), extra_files=None):
    # type: (str, str, str, str | None, str | None, tuple, dict | None) -> None
    d = os.path.join(lab, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, "SKILL.md"), "w").write(skillmd.replace("__NAME__", name))
    if readme is not None:
        open(os.path.join(d, "README.md"), "w").write(readme)
    if script is not None:
        os.makedirs(os.path.join(d, "scripts"))
        open(os.path.join(d, "scripts", "tool.py"), "w").write(script)
    for x in extra_dirs:
        os.makedirs(os.path.join(d, x))
    for rel, content in (extra_files or {}).items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(content)


def run_lint(lab, skill=None):
    """Run the lab's copy of the linter; return (findings|None, stderr_or_err)."""
    cmd = [sys.executable, os.path.join(lab, "tools", "lint_skills.py"), "--json"]
    if skill:
        cmd += ["--skill", skill]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode not in (0, 1) and not r.stdout:
        return None, r.stderr
    try:
        return (json.loads(r.stdout) if r.stdout.strip() else []), None
    except json.JSONDecodeError:
        return None, "unparseable stdout: %s" % r.stdout[:200]


def rules(findings):
    return {f["rule"] for f in findings} if findings is not None else None


def set_baseline(lab, data):
    """Write (or remove, when data is None) the lab's tools/trigger_baseline.json.

    Each trigger-baseline case sets its own state so the cases are order-independent
    and can't leak a baseline into each other. The path mirrors lint_skills.py's
    trigger_baseline_path() (linter's grandparent dir + tools/ = the lab root)."""
    p = os.path.join(lab, "tools", "trigger_baseline.json")
    if data is None:
        if os.path.exists(p):
            os.remove(p)
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


# (name, build_fn, expected_rules_that_must_FIRE, rules_that_must_NOT_fire)
CASES = []


def case(name, build, must_fire=(), must_not=()):
    CASES.append((name, build, must_fire, must_not))


def _lab():
    global LAB
    return LAB


# ── A. frontmatter ───────────────────────────────────────────────────────────
case("A1 name-mismatch", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("name: __NAME__", "name: other")),
     {"frontmatter/name"})

case("A2 no PREFER", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("PREFER THIS SKILL when", "use this skill when")),
     {"routing/prefer"})

case("A3 lowercase 'prefer'", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("PREFER THIS SKILL", "prefer this skill")),
     {"routing/prefer"})   # case-sensitive check SHOULD catch this

case("A4 no trigger list", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace('Activate on any of: "do the thing", "thing status".', "")),
     {"routing/triggers"})

case("A5 trigger list paraphrase", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("Activate on any of:", "Activate on:")),
     {"routing/triggers"})  # exact phrase mandated by CONVENTIONS

case("A6 version 1.0.0", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("version: 0.1.0", "version: 1.0.0")),
     {"frontmatter/version"})

case("A7 wrong license", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("license: MIT", "license: GPL-3.0")),
     {"frontmatter/license"})

case("A8 no tags", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("    tags: [Thing, Demo]", "")),
     {"frontmatter/tags"})

case("A9 lowercase tag", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("tags: [Thing, Demo]", "tags: [thing, Demo]")),
     {"frontmatter/tags"})

case("A10 no metadata at all", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("metadata:\n  hermes:\n    tags: [Thing, Demo]", "")),
     {"frontmatter/tags"})

# ── B. body content ──────────────────────────────────────────────────────────
case("B1 NEVER-read section", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("# Thing", "# Thing\n\n## Files this skill must NEVER read\n\n- /etc/passwd")),
     {"body/never-read-section"})

case("B2 'never read' lowercase mid-sentence", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## When to use", "## When to use\n\nThis tool must never read the cache directory.")),
     {"body/never-read-section"})

case("B3 missing error sentence", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.",
                                  "Handle errors carefully.")),
     {"body/error-sentence"})

case("B4 error sentence typo (dropped 'yourself')", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("resolve errors yourself.", "resolve errors.")),
     {"body/error-sentence"})

case("B5 missing 'When to use'", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## When to use\n\nWhen the user asks for the thing.\n\n", "")),
     {"body/section-flow"})

case("B6 missing 'When NOT to use'", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## When NOT to use\n\nWhen they ask for something else.\n\n", "")),
     {"body/section-flow"})

case("B7 lowercase section heading", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## When to use", "## when to use").replace("## When NOT to use", "## when not to use")),
     set(), must_not={"body/section-flow"})  # re.I tolerates case

case("B8 no tools table", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## Tools\n\n| Tool | Purpose |\n|------|---------|\n| `do-thing` | Runs the thing and reports the result. |\n\n",
                                  "## Tools\n\nRun the tool directly.\n\n")),
     {"body/tools-table"})

case("B9 Purpose leads with article", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("| Runs the thing and reports the result. |",
                                  "| The thing, run and reported. |")),
     {"body/explicit-verb"})

case("B10 Purpose 'Returns'", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("| Runs the thing and reports the result. |",
                                  "| Returns the result of the thing. |")),
     {"body/explicit-verb"})

case("B11 Purpose valid verb", lambda: make_skill(LAB, "x", script=BASE_SCRIPT),
     set(), must_not={"body/explicit-verb"})

case("B12 rationale heading", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## When to use", "## Why this exists\n\n## When to use")),
     {"body/model-context"})

case("B13 failure prime 'models tend to' (CONVENTIONS' own example)", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("When the user asks for the thing.",
                                  "Models tend to call do-thing twice; avoid that.")),
     {"body/model-context"})  # contract after 2026-08-27: plural form must be caught

case("B14 failure prime 'tends to'", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("When the user asks for the thing.",
                                  "The model tends to call do-thing twice.")),
     {"body/model-context"})

case("B15 domain leak in prose", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("Does one thing well.", "Sits on a spreadsheet backend.")),
     {"body/domain-leak"})

case("B16 domain leak inside code fence", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## Output", "## Output\n\n```\nspreadsheet\n```")),
     set(), must_not={"body/domain-leak"})

case("B17 domain leak in URL", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## Output", "## Output\n\nSee https://example.com/spreadsheet/docs")),
     set(), must_not={"body/domain-leak"})

case("B18 domain leak in inline literal", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("## Output", "## Output\n\nUse `spreadsheet` mode.")),
     set(), must_not={"body/domain-leak"})

# ── C. layout ────────────────────────────────────────────────────────────────
case("C1 stray directory", lambda: make_skill(LAB, "x", script=BASE_SCRIPT, extra_dirs=("notes",)),
     {"layout/dirs"})

case("C2 examples/ directory", lambda: make_skill(LAB, "x", script=BASE_SCRIPT, extra_dirs=("examples",)),
     {"layout/dirs"})

case("C3 dot directory ignored", lambda: make_skill(LAB, "x", script=BASE_SCRIPT, extra_dirs=(".hidden",)),
     set(), must_not={"layout/dirs"})

case("C4 no README", lambda: make_skill(LAB, "x", script=BASE_SCRIPT, readme=None),
     {"layout/readme"})

# ── D. scripts ───────────────────────────────────────────────────────────────
# Contract after fix #6 (2026-08-27): the house pattern sys.exit(main()) / sys.exit(code)
# propagates a computed status and must NOT fire; BASE_SCRIPT also carries the separate,
# pre-existing top-level-guard finding, which IS asserted here to keep the case strict.
case("D1 sys.exit(main()) house pattern is clean", lambda: make_skill(LAB, "x", script=BASE_SCRIPT),
     {"scripts/top-level-guard"}, must_not={"scripts/exit-code"})

case("D2 sys.exit(1) literal", lambda: make_skill(LAB, "x", script=BASE_SCRIPT.replace("sys.exit(main())",
     'sys.exit(1) if False else sys.exit(main())')),
     set(), must_not={"scripts/exit-code"})

case("D3 never exits non-zero", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json\nprint(json.dumps({"ok": False, "error": "boom"}))\n'),
     {"scripts/exit-code"})

case("D4 no ok field", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps({"result": "x"}))\nsys.exit(1)\n'),
     {"scripts/json-contract"})

case("D5 no top-level guard", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json, sys\ndef main():\n    print(json.dumps({"ok": True}))\n    return 0\nif __name__ == "__main__":\n    sys.exit(main())\n'),
     {"scripts/top-level-guard"})

case("D6 guarded main", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json, sys\ndef main():\n    try:\n        raise ValueError\n    except Exception:\n        print(json.dumps({"ok": False, "error": "x"}))\n        return 1\n    print(json.dumps({"ok": True}))\n    return 0\nif __name__ == "__main__":\n    sys.exit(main())\n'),
     set(), must_not={"scripts/top-level-guard"})

case("D7 silent except", lambda: make_skill(LAB, "x", script=BASE_SCRIPT.replace("def main():",
     'def main():\n    try:\n        open("/nonexistent/xyz")\n    except Exception:\n        pass')),
     {"scripts/silent-except"})

case("D8 undeclared import, no requirements", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport requests\nimport json, sys\nprint(json.dumps({"ok": True}))\nsys.exit(1) if False else None\n'),
     {"scripts/requirements"})

case("D9 declared import with requirements.txt", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport requests\nimport json, sys\nprint(json.dumps({"ok": True}))\nsys.exit(1) if False else None\n',
     extra_files={"scripts/requirements.txt": "requests==2.32.0\n"}),
     set(), must_not={"scripts/requirements"})

case("D10 DIST mapping (googleapiclient)", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport googleapiclient\nimport json, sys\nprint(json.dumps({"ok": True}))\nsys.exit(1) if False else None\n',
     extra_files={"scripts/requirements.txt": "google-api-python-client==2.100.0\n"}),
     set(), must_not={"scripts/requirements"})

# The stub skill_json.py must itself satisfy the entry-point checks (it is linted as a
# script, not a library) or it adds json-contract/exit-code noise. With a clean stub and
# a correctly guarded, dotted entry point, fix #5 makes the whole vendored block pass.
case("D11 vendored skill_json + ok() + @skill_json.guard", lambda: make_skill(LAB, "x", extra_files={
     "scripts/skill_json.py": "import json, sys\ndef ok(*a, **k):\n    pass\ndef fail(*a, **k):\n    print(json.dumps({\"ok\": False}))\n    sys.exit(1)\ndef guard(f):\n    return f\n",
     "scripts/tool.py": '#!/usr/bin/env python3\nimport skill_json\nfrom skill_json import ok, fail\n@skill_json.guard\ndef main():\n    ok()\nif __name__ == "__main__":\n    main()\n'}),
     set(), must_not={"scripts/json-contract", "scripts/exit-code", "scripts/top-level-guard"})

case("D12 vendored but no @guard", lambda: make_skill(LAB, "x", extra_files={
     "scripts/skill_json.py": "def ok(*a, **k):\n    pass\ndef fail(*a, **k):\n    pass\ndef guard(f):\n    return f\n",
     "scripts/tool.py": '#!/usr/bin/env python3\nfrom skill_json import ok, fail\ndef main():\n    ok()\nif __name__ == "__main__":\n    main()\n'}),
     {"scripts/top-level-guard"})

case("D13 './script.py' invocation in docs", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("Runs the thing", "Run ./scripts/tool.py to run the thing")),
     {"scripts/invocation"})

case("D14 python3 invocation in docs", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("Runs the thing", "Run python3 scripts/tool.py to run the thing")),
     set(), must_not={"scripts/invocation"})

# Added with fix #6 (2026-08-27): the loosened rule must not swallow its own purpose.
case("D15 only sys.exit(0) still fires", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps({"ok": True}))\nsys.exit(0)\n'),
     {"scripts/exit-code"})

case("D16 raise SystemExit(<var>) is clean", lambda: make_skill(LAB, "x", script=
     '#!/usr/bin/env python3\nimport json\nrc = 0\nprint(json.dumps({"ok": True}))\nraise SystemExit(rc)\n'),
     set(), must_not={"scripts/exit-code"})

# ── --confirm on destructive subcommands (fix #9, 2026-08-27) ─────────────
# README motivation #2: a hallucinated `delete` call with no footgun brake. The check
# anchors on the subparser NAME (a file-level scan false-positives on benign "clear"/
# "remove" uses) and requires --confirm declared in that subparser's own block.
CONFIRM_SCRIPT = (
    '#!/usr/bin/env python3\nimport json, sys, argparse\n'
    'def main():\n'
    '    ap = argparse.ArgumentParser()\n'
    '    sp = ap.add_subparsers()\n'
    '    sp.add_parser("{sub}"){extra}\n'
    '    ap.parse_args()\n'
    '    print(json.dumps({{"ok": True}}))\n'
    '    return 0\n'
    'if __name__ == "__main__":\n    sys.exit(main())\n'
)
case("D17 destructive subcommand without --confirm",
     lambda: make_skill(LAB, "x", script=CONFIRM_SCRIPT.format(sub="delete", extra="")),
     {"scripts/confirm"})

case("D18 destructive subcommand WITH --confirm is clean",
     lambda: make_skill(LAB, "x", script=CONFIRM_SCRIPT.format(
         sub="delete", extra='\n    _d.add_argument("--confirm")'
         ).replace('sp.add_parser("delete")', '    _d = sp.add_parser("delete")')),
     set(), must_not={"scripts/confirm"})

case("D19 compound destructive name (delete-image) without --confirm",
     lambda: make_skill(LAB, "x", script=CONFIRM_SCRIPT.format(sub="delete-image", extra="")),
     {"scripts/confirm"})

case("D20 non-destructive subcommand needs no --confirm",
     lambda: make_skill(LAB, "x", script=CONFIRM_SCRIPT.format(sub="read", extra="")),
     set(), must_not={"scripts/confirm"})

# A --confirm declared once elsewhere does NOT guard a destructive subparser that lacks
# it — the scoped-block requirement is what makes the check precise rather than "does the
# file contain the string".
case("D21 --confirm on a DIFFERENT subparser does not guard delete",
     lambda: make_skill(LAB, "x", script=
         '#!/usr/bin/env python3\nimport json, sys, argparse\n'
         'def main():\n'
         '    ap = argparse.ArgumentParser()\n'
         '    sp = ap.add_subparsers()\n'
         '    _r = sp.add_parser("read")\n'
         '    _r.add_argument("--confirm")\n'
         '    sp.add_parser("delete")\n'
         '    ap.parse_args()\n'
         '    print(json.dumps({"ok": True}))\n'
         '    return 0\n'
         'if __name__ == "__main__":\n    sys.exit(main())\n'),
     {"scripts/confirm"})

# ── E. toolsets ──────────────────────────────────────────────────────────────
case("E1 uses web_search, undeclared", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("When the user asks for the thing.",
                                  "Use web_search first when the user asks for the thing.")),
     {"frontmatter/requires-toolsets"})

case("E2 declares requires_toolsets", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("    tags: [Thing, Demo]",
                                  "    tags: [Thing, Demo]\n    requires_toolsets: [web]")),
     set(), must_not={"frontmatter/requires-toolsets"})

# ── F. parser robustness ─────────────────────────────────────────────────────
case("F1 no frontmatter at all", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd="# Thing\n\nNo frontmatter here.\n"),
     {"frontmatter/name", "routing/prefer", "routing/triggers"})

# Contract after fix #7 (2026-08-27): broken YAML is ONE finding (frontmatter/yaml) and
# the per-field checks are skipped — before the fix this same input produced a cascade
# (name + prefer + triggers + …) that buried the real problem. Pin both: the yaml finding
# MUST fire and the old cascade findings MUST NOT (that is the sharp edge).
case("F2 broken YAML is one finding, not a cascade", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd="---\nname: [unclosed\ndescription: >\n  dangling\n---\n\n# Thing\n"),
     {"frontmatter/yaml"},
     must_not={"frontmatter/name", "routing/prefer", "routing/triggers",
               "frontmatter/version", "frontmatter/license", "frontmatter/tags"})

# Contract after fix #3 (2026-08-27): CRLF must parse as valid frontmatter and stay clean.
# Before the fix this produced a wall of false criticals.
case("F3 CRLF line endings", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd=BASE_SKILLMD.replace("\n", "\r\n")),
     set(), must_not={"frontmatter/name", "routing/prefer", "routing/triggers"})

case("F4 UTF-8 BOM", lambda: make_skill(LAB, "x", script=BASE_SCRIPT,
     skillmd="\ufeff" + BASE_SKILLMD),
     set(), must_not={"frontmatter/name"})

def _build_f5():
    make_skill(LAB, "x", script=BASE_SCRIPT)
    with open(os.path.join(LAB, "x", "SKILL.md"), "wb") as f:
        f.write(b"---\nname: x\n---\n\xff\xfe broken\n")
# Contract after fix #1 (2026-08-27): invalid UTF-8 is a finding, not a crash. A crash
# still fails the suite — the harness reports it — so this case guards the guard.
case("F5 invalid UTF-8 bytes", _build_f5, {"readability"})

case("F6 empty SKILL.md", lambda: make_skill(LAB, "x", script=BASE_SCRIPT, skillmd=""),
     {"frontmatter/name", "routing/prefer", "routing/triggers"})

# ── H. trigger-list regression baseline (fix #10, 2026-08-27) ──────────────
# Gap 11: a description rewrite dropped 9 of google-docs' triggers (19→10) and the linter
# passed it — presence was checked, completeness was not. The baseline (a committed
# artifact, tools/trigger_baseline.json) makes the drop a finding. Only REMOVALS fire
# (adding triggers widens routing — always safe). Each case sets its own baseline.
def _h1():
    make_skill(LAB, "x", script=BASE_SCRIPT)
    set_baseline(LAB, {"x": ["do the thing", "thing status"]})   # matches the description
case("H1 baseline matches description — clean", _h1,
     set(), must_not={"routing/triggers-baseline"})

def _h2():
    make_skill(LAB, "x", script=BASE_SCRIPT)   # description has 2 triggers
    set_baseline(LAB, {"x": ["do the thing", "thing status", "write to the doc"]})
case("H2 a trigger was dropped (19→10 shape)", _h2,
     {"routing/triggers-baseline"},
     must_not={"routing/triggers"})   # the list is still PRESENT — presence is fine

def _h3():
    # the list itself was deleted — both rules must fire (presence critical + baseline major)
    make_skill(LAB, "x", script=BASE_SCRIPT,
               skillmd=BASE_SKILLMD.replace('Activate on any of: "do the thing", "thing status".', ""))
    set_baseline(LAB, {"x": ["do the thing", "thing status"]})
case("H3 whole trigger list deleted", _h3,
     {"routing/triggers", "routing/triggers-baseline"})

def _h4():
    make_skill(LAB, "x", script=BASE_SCRIPT)
    set_baseline(LAB, {"other-skill": ["do the thing"]})   # x not in the baseline
case("H4 skill not in baseline — no check", _h4,
     set(), must_not={"routing/triggers-baseline"})

def _h5():
    make_skill(LAB, "x", script=BASE_SCRIPT)
    set_baseline(LAB, None)   # no baseline file (the hermetic-lab / fresh-clone posture)
case("H5 no baseline file — check is inert", _h5,
     set(), must_not={"routing/triggers-baseline"})

# ── G. control ───────────────────────────────────────────────────────────────
case("G1 pristine baseline is clean", lambda: make_skill(LAB, "x", script=CLEAN_SCRIPT),
     set(), must_not={"frontmatter/name", "routing/prefer", "routing/triggers",
                      "body/section-flow", "body/tools-table", "body/domain-leak",
                      "scripts/json-contract", "scripts/exit-code",
                      "scripts/top-level-guard", "layout/readme", "layout/dirs"})


# ── gate tests (whole-repo behaviours) ───────────────────────────────────────
def gate_tests(lab):
    """The exit code IS the gate. Three behaviours a per-skill case cannot express.

    Each builds its own throwaway lab (copied linter + a couple of skills) so the other
    cases' fixtures cannot leak in. Returns a list of (name, ok, detail).
    """
    results = []

    def fresh_lab():
        lab2 = tempfile.mkdtemp(prefix="lintgate-")
        os.makedirs(os.path.join(lab2, "tools"))
        shutil.copy(LINTER, os.path.join(lab2, "tools", "lint_skills.py"))
        return lab2

    # G2: --severity filters what is PRINTED, not what is GATED. A repo with a critical
    # must still exit 1 when linted with --severity major. (Before the fix, exit was
    # computed from the filtered list: a red repo passed CI under --severity major.)
    lab2 = fresh_lab()
    make_skill(lab2, "red", script=BASE_SCRIPT,
               skillmd=BASE_SKILLMD.replace("name: __NAME__", "name: other"))
    make_skill(lab2, "green", script=CLEAN_SCRIPT)
    cmd = [sys.executable, os.path.join(lab2, "tools", "lint_skills.py"), "--severity", "major"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    results.append(("G2 critical still gates under --severity major",
                    r.returncode == 1, "exit %d" % r.returncode))

    # G3: one unreadable SKILL.md must not mask the rest of the repo. Before the fix,
    # invalid UTF-8 crashed the run with empty stdout — CI failed and reported nothing,
    # and every other skill's findings were silently swallowed.
    make_skill(lab2, "broken", script=BASE_SCRIPT)
    with open(os.path.join(lab2, "broken", "SKILL.md"), "wb") as f:
        f.write(b"---\nname: broken\n---\n\xff\xfe not utf-8\n")
    # green carries no findings; give it one known major (missing README) to track.
    os.remove(os.path.join(lab2, "green", "README.md"))
    findings, err = run_lint(lab2)
    got = rules(findings) or set()
    ok = (findings is not None and "readability" in got and "layout/readme" in got)
    results.append(("G3 unreadable file does not mask the repo",
                    ok, err or "got %s" % sorted(got)))

    # G4: --update-triggers must write a baseline that the linter itself accepts (the
    # generator and the check share extract_triggers — a divergence between them is how
    # the check would start flagging triggers it authored, so the round-trip is the test).
    r = subprocess.run([sys.executable, os.path.join(lab2, "tools", "lint_skills.py"),
                        "--update-triggers"], capture_output=True, text=True, timeout=60)
    bpath = os.path.join(lab2, "tools", "trigger_baseline.json")
    gen_ok = (r.returncode == 0 and os.path.exists(bpath))
    if gen_ok:
        base = json.load(open(bpath))
        f2, e2 = run_lint(lab2)
        got2 = rules(f2) or set()
        gen_ok = "routing/triggers-baseline" not in got2
        detail = e2 or ("%d skills baselined; re-lint: %s"
                        % (len(base), sorted(got2) or "clean"))
    else:
        detail = "generator rc=%d %s" % (r.returncode, (r.stderr or r.stdout)[:60])
    results.append(("G4 --update-triggers round-trips (generated baseline lints clean)",
                    gen_ok, detail))

    # G5: the sharp edge of the baseline check — a trigger the skill GAINED (baseline is
    # a strict subset of the description) must NOT fire. Only removals gate; additions
    # widen routing and are always safe. red's description carries two triggers; give it
    # a baseline that is a proper subset of those and assert it stays quiet.
    if os.path.exists(bpath):
        base = json.load(open(bpath))
        base["red"] = ["do the thing"]   # subset of red's actual ["do the thing", "thing status"]
        json.dump(base, open(bpath, "w"))
        f4, _ = run_lint(lab2)
        got4 = {(f["skill"], f["rule"]) for f in f4} if f4 else set()
        add_ok = ("red", "routing/triggers-baseline") not in got4
        fired = sorted(g[0] for g in got4 if g[1] == "routing/triggers-baseline")
        detail = "fired for %s" % fired if fired else "no false fire"
    else:
        add_ok, detail = False, "no baseline to test (see G4)"
    results.append(("G5 gained trigger (baseline ⊂ description) does not fire",
                    add_ok, detail))

    shutil.rmtree(lab2, ignore_errors=True)
    return results


def main():
    global LAB
    LAB = tempfile.mkdtemp(prefix="lintlab-")
    os.makedirs(os.path.join(LAB, "tools"))
    shutil.copy(LINTER, os.path.join(LAB, "tools", "lint_skills.py"))

    try:
        print("case".ljust(52), "result".ljust(8), "detail")
        print("-" * 104)
        n_pass = n_fail = n_crash = 0
        for name, build, must_fire, must_not in CASES:
            try:
                build()
            except Exception as e:
                n_crash += 1
                print(name.ljust(52), "BUILD-ERR", repr(e)[:60])
                continue
            findings, err = run_lint(LAB, "x")
            if findings is None:
                n_crash += 1
                print(name.ljust(52), "CRASH", (err or "no stdout")[:90])
                continue
            got = rules(findings)
            problems = ["MISS %s (expected to fire)" % r for r in must_fire if r not in got]
            problems += ["FALSE-POS %s (should not fire)" % r for r in must_not if r in got]
            status = "PASS" if not problems else "FAIL"
            n_pass += status == "PASS"
            n_fail += status == "FAIL"
            print(name.ljust(52), status.ljust(8),
                  "; ".join(problems)[:80] if problems else "(%d findings total)" % len(findings))
        print("-" * 104)

        for name, ok, detail in gate_tests(LAB):
            status = "PASS" if ok else "FAIL"
            n_pass += ok
            n_fail += not ok
            print(name.ljust(52), status.ljust(8), detail[:80])

        print("-" * 104)
        print("pass=%d  fail=%d  crash=%d" % (n_pass, n_fail, n_crash))
        return 1 if (n_fail or n_crash) else 0
    finally:
        shutil.rmtree(LAB, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
