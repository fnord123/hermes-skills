#!/usr/bin/env python3
"""Enforce CONVENTIONS.md mechanically, so the repo cannot drift back.

Every rule here traces to a line in CONVENTIONS.md. The point is not tidiness: each convention
exists because a local small model goes wrong without it, and a convention nobody checks is a
convention that decays. The first audit of this repo found 16 of 16 skills non-conformant and
the file CONVENTIONS.md itself nominates as the template carrying a section the document
forbids - drift nobody noticed because nothing looked.

Usage:
    python3 tools/lint_skills.py                # report everything
    python3 tools/lint_skills.py --skill donations
    python3 tools/lint_skills.py --severity critical
    python3 tools/lint_skills.py --json

Exit 1 if any critical finding is present, so this can gate a commit.
"""

import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}

ERROR_SENTENCE = ("Always ask the user for guidance when there is an error; "
                  "do not proactively try to resolve errors yourself.")

# Backend vocabulary that must not reach the model-facing surface. The rule is domain-in,
# backend-out: the model reasons about whatever words are literally in front of it, and a
# stray backend term drags it off the domain (CONVENTIONS.md, "Leak-free domain abstraction").
BACKEND_TERMS = [
    "spreadsheet", "worksheet", "batchupdate", "mime type",
    "oauth", "service account", "bearer token", "endpoint", "webhook",
    "selector", "dom ", "playwright", "session cookie", "har file",
    "sqlite", "schema", "primary key", "vector", "embedding",
    # Named backends. Generic terms alone miss the most common real leak: the vendor's own
    # product name. The audit found "Gingr", "Hindsight", "AgentMail", "bank" and "retain" in
    # model context and none were catchable, because a rule of generic nouns cannot know what
    # a given skill sits on top of. These are the ones this repo actually uses.
    "gingr", "hindsight", "agentmail", "camoufox", "browserbase", "firecrawl",
    "myshopify", "home assistant", "litellm", "twelve data",
]

# Words a specific skill may legitimately use because they ARE its domain. Keyed by skill.
# "Square" is the merchant platform AND the word a user says; "bank" is a Hindsight collection
# AND an ordinary English word. Judgement, not pattern-matching - so it stays reviewable.
LEAK_ALLOW = {
    "bambu-store": {"myshopify"},          # named in setup docs the human follows
}

# Files under scripts/ that are not entry points and so carry no JSON contract.
def _is_library(path):
    b = os.path.basename(path)
    return (b.endswith("_lib.py") or b.startswith("lib") or b.endswith("_test.py")
            or b.startswith("test_") or b == "__init__.py" or b == "conftest.py")


STDLIB = {
    "os", "sys", "re", "json", "glob", "datetime", "argparse", "pathlib", "subprocess", "time",
    "hashlib", "urllib", "typing", "collections", "itertools", "math", "random", "shutil",
    "tempfile", "textwrap", "csv", "base64", "uuid", "dataclasses", "functools", "logging",
    "sqlite3", "html", "email", "zoneinfo", "io", "enum", "traceback", "string", "unicodedata",
    "statistics", "concurrent", "threading", "socket", "ssl", "calendar", "warnings",
    "contextlib", "copy", "difflib", "struct", "binascii", "getpass", "platform", "signal",
    "atexit", "secrets", "http", "unittest", "asyncio", "inspect", "zipfile", "mimetypes",
    "stat", "curses", "decimal", "fractions", "pprint", "shlex", "enum", "abc", "__future__",
    # Unix-only, but stdlib: their absence on Windows is a portability question, not a missing
    # dependency, and flagging them as third-party sends you to add a nonexistent package.
    "fcntl", "termios", "tty", "pwd", "grp", "resource", "select", "importlib",
}

# The hand-kept set above is a floor, not the truth. It has been wrong twice — `builtins` and
# `types` are both stdlib and both got reported as "undeclared third-party imports", sending you
# to add a package that does not exist. Python knows the real answer, so ask it: this can only
# ever REMOVE false positives, since it adds names rather than dropping any. The literal set
# stays as the fallback for a runtime old enough to lack the attribute (3.9 and earlier).
STDLIB |= set(getattr(sys, "stdlib_module_names", ()))

# Import name -> distribution name, where they differ.
DIST = {
    "googleapiclient": "google-api-python-client", "google": "google-auth",
    "dateutil": "python-dateutil", "yaml": "PyYAML", "fitz": "PyMuPDF",
    "playwright_stealth": "playwright-stealth", "hindsight_client_api": "hindsight-client-api",
    "bs4": "beautifulsoup4", "PIL": "Pillow",
}


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    raw = m.group(1)
    try:
        import yaml
        return (yaml.safe_load(raw) or {}), text[m.end():]
    except Exception:                                          # noqa: BLE001
        return {"_raw": raw}, text[m.end():]


def lint_skill(name):
    d = os.path.join(ROOT, name)
    sk = os.path.join(d, "SKILL.md")
    out = []

    def add(sev, rule, msg, where=""):
        out.append({"skill": name, "severity": sev, "rule": rule,
                    "where": where or "SKILL.md", "message": msg})

    text = open(sk, encoding="utf-8").read()
    fm, body = frontmatter(text)
    lines = text.splitlines()

    def lineno(pat):
        for i, l in enumerate(lines, 1):
            if re.search(pat, l, re.I):
                return "SKILL.md:%d" % i
        return "SKILL.md"

    # ── frontmatter ────────────────────────────────────────────────────────
    if fm.get("name") != name:
        add("critical", "frontmatter/name",
            "name %r does not match the folder" % fm.get("name"))
    desc = str(fm.get("description") or "")
    if "PREFER" not in desc:
        add("critical", "routing/prefer",
            "description has no PREFER clause - the model cannot tell this skill from its neighbours")
    if "Activate on any of" not in desc:
        add("critical", "routing/triggers",
            "description has no 'Activate on any of:' trigger list")
    if not re.match(r"^0\.\d+\.\d+$", str(fm.get("version") or "")):
        add("minor", "frontmatter/version",
            "version %r is not 0.x.y" % fm.get("version"))
    if str(fm.get("license") or "") != "MIT":
        add("minor", "frontmatter/license", "no 'license: MIT'")
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    tags = hermes.get("tags") or []
    if not tags:
        add("major", "frontmatter/tags", "no metadata.hermes.tags")
    for t in tags:
        if str(t) != str(t).strip() or not str(t)[:1].isupper():
            add("minor", "frontmatter/tags", "tag %r is not Capitalized" % t)

    # ── forbidden / required body content ──────────────────────────────────
    if re.search(r"NEVER read", text, re.I):
        add("critical", "body/never-read-section",
            "has a 'Files this skill must NEVER read' section - CONVENTIONS.md forbids it: "
            "it primes the behaviour it warns against", lineno(r"NEVER read"))
    if ERROR_SENTENCE not in text:
        add("major", "body/error-sentence",
            "error section does not end with the mandatory sentence verbatim")
    for h, sev in (("When to use", "major"), ("When NOT to use", "major")):
        if not re.search(r"^#+\s*" + h + r"\s*$", body, re.I | re.M):
            add(sev, "body/section-flow", "missing '## %s' section" % h)
    if not re.search(r"^\|.*\bPurpose\b", body, re.I | re.M):
        add("major", "body/tools-table", "no tools table with a Purpose column")
    else:
        for i, l in enumerate(lines, 1):
            m = re.match(r"^\|[^|]+\|\s*([A-Za-z]+)\b", l)
            if m and m.group(1) not in ("Purpose", "Tool", "Verb", "Script", "Command"):
                w = m.group(1)
                # a Purpose must LEAD with a verb; "The running total" does not.
                if w in ("The", "A", "An", "This", "It", "Returns"):
                    add("minor", "body/explicit-verb",
                        "tool Purpose does not lead with a verb", "SKILL.md:%d" % i)

    # ── model-context discipline ───────────────────────────────────────────
    for pat, why in (
        (r"^#+.*\b(why|rationale|background|design|architecture)\b", "rationale belongs in README"),
        (r"\b(tends to|models often|the model will try|a common mistake|don't reach for)\b",
         "failure-mode discussion primes the mistake; move to README"),
    ):
        m = re.search(pat, body, re.I | re.M)
        if m:
            add("major", "body/model-context", why,
                lineno(re.escape(m.group(0)[:30])))

    # ── domain leakage ─────────────────────────────────────────────────────
    # Scan PROSE only. A term inside a fenced code block, an inline literal or a URL is not
    # leaking into the model's vocabulary - it is part of a command the model must copy
    # verbatim. Without this the rule fired on "webhook" inside a literal Home Assistant URL
    # and on "formula" meaning show-your-math, which trains authors to ignore it.
    prose = re.sub(r"```.*?```", " ", text, flags=re.S)
    prose = re.sub(r"`[^`\n]*`", " ", prose)
    prose = re.sub(r"https?://\S+", " ", prose)
    prose = prose.lower()
    allow = LEAK_ALLOW.get(name, set())
    leaked = sorted({t.strip() for t in BACKEND_TERMS
                     if t.strip() not in allow
                     and re.search(r"\b" + re.escape(t.strip()) + r"\b", prose)})
    if leaked:
        add("major", "body/domain-leak",
            "backend vocabulary in model context: %s" % ", ".join(leaked),
            lineno(re.escape(leaked[0])))

    # ── layout ─────────────────────────────────────────────────────────────
    subs = {x for x in os.listdir(d)
            if os.path.isdir(os.path.join(d, x)) and not x.startswith(".")}
    for bad in sorted(subs - ALLOWED_SUBDIRS):
        add("major", "layout/dirs",
            "'%s/' is outside the four Hermes directories - invisible to supporting-file "
            "discovery and unwritable by the agent" % bad)
    if not os.path.exists(os.path.join(d, "README.md")):
        add("major", "layout/readme",
            "no README.md - rationale has nowhere to go but model context (%d SKILL.md lines)"
            % len(lines))

    # ── scripts ────────────────────────────────────────────────────────────
    sdir = os.path.join(d, "scripts")
    scripts = []
    if os.path.isdir(sdir):
        for base, _, files in os.walk(sdir):
            if ".venv" in base:
                continue
            scripts += [os.path.join(base, f) for f in files if f.endswith(".py")]

    if re.search(r"(?<!python3 )(?<!python )\B\./\S*scripts/\S+\.py", text):
        add("major", "scripts/invocation",
            "documents './script.py'; the executable bit is lost over an HTTP install - "
            "always document 'python3 <path>'")

    imports = set()
    for f in scripts:
        rel = os.path.relpath(f, d)
        src = open(f, encoding="utf-8", errors="ignore").read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            add("critical", "scripts/syntax", "does not parse: %s" % e, rel)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".")[0])
        if _is_library(f):
            continue

        # A script that vendors skill_json.py and uses its helpers satisfies the whole
        # contract - the markers just live in the imported module. Grepping the entry point's
        # own text and not following the import means penalising the exact vendoring pattern
        # CONVENTIONS.md prescribes, which is worse than missing a violation: it tells authors
        # the right answer is wrong. Verified by hand that `calendar-range.py --bogus` emits
        # {"ok": false, ...} on stdout with exit 1 while being reported as three findings.
        vendored = os.path.exists(os.path.join(sdir, "skill_json.py"))
        uses_helpers = re.search(r"\b(from\s+skill_json\s+import|import\s+skill_json)\b", src)
        if vendored and uses_helpers:
            # Confirm it actually CALLS them rather than merely importing - an unused import
            # would otherwise buy a free pass on all three checks.
            has_emit = re.search(r"\b(ok|fail)\s*\(", src)
            has_guard = re.search(r"@guard\b", src)
            if has_emit and has_guard:
                continue
            if has_emit and not has_guard:
                add("major", "scripts/top-level-guard",
                    "imports skill_json but main() is not decorated with @guard, so an "
                    "unexpected exception still escapes as a traceback with no JSON", rel)
                continue

        if '"ok"' not in src and "'ok'" not in src:
            add("major", "scripts/json-contract",
                "prints no 'ok' field; the model cannot tell success from failure by a "
                "stable rule", rel)
        if not re.search(r"(sys\.)?exit\(\s*1\s*\)|SystemExit\(\s*1\s*\)", src):
            add("major", "scripts/exit-code", "never exits 1 on failure", rel)
        # An unguarded main() can die with a traceback and NO json on stdout.
        if "def main(" in src and not re.search(r"except Exception", src):
            add("major", "scripts/top-level-guard",
                "no top-level exception guard: an unexpected error prints a traceback and "
                "no JSON object at all", rel)
        for m in re.finditer(r"except\s*(Exception)?\s*:\s*\n\s*pass\b", src):
            add("minor", "scripts/silent-except",
                "'except: pass' swallows a real failure silently",
                "%s:%d" % (rel, src[:m.start()].count("\n") + 1))

    third = {i for i in imports if i not in STDLIB and
             not any(os.path.basename(s)[:-3] == i for s in scripts)}
    if third:
        rq = os.path.join(sdir, "requirements.txt")
        declared = set()
        if os.path.exists(rq):
            declared = {re.split(r"[=<>\[;]", l.strip())[0].strip().lower()
                        for l in open(rq) if l.strip() and not l.startswith("#")}
        missing = sorted(i for i in third
                         if DIST.get(i, i).lower() not in declared and i.lower() not in declared)
        if missing:
            add("critical", "scripts/requirements",
                "undeclared third-party imports (%s) - a fresh install fails with "
                "ModuleNotFoundError" % ", ".join(DIST.get(i, i) for i in missing),
                "scripts/requirements.txt")

    # ── toolset declaration ────────────────────────────────────────────────
    needs = set()
    for tool, pat in (("web", r"\bweb_search\b|\bweb_extract\b"),
                      ("browser", r"\bbrowser_\w+\b"),
                      ("terminal", r"\bpython3 \S+\.py\b|\bcurl\b")):
        if re.search(pat, text):
            needs.add(tool)
    if needs and not hermes.get("requires_toolsets"):
        add("major", "frontmatter/requires-toolsets",
            "uses %s but declares no metadata.hermes.requires_toolsets - if the toolset is "
            "absent the skill activates anyway and fails confusingly"
            % ", ".join(sorted(needs)))

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", help="lint one skill")
    ap.add_argument("--severity", choices=["critical", "major", "minor"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    skills = sorted(x for x in os.listdir(ROOT)
                    if os.path.isdir(os.path.join(ROOT, x))
                    and os.path.exists(os.path.join(ROOT, x, "SKILL.md")))
    if args.skill:
        skills = [s for s in skills if s == args.skill]

    findings = []
    for s in skills:
        findings += lint_skill(s)

    order = {"critical": 0, "major": 1, "minor": 2}
    if args.severity:
        findings = [f for f in findings if f["severity"] == args.severity]
    findings.sort(key=lambda f: (order[f["severity"]], f["skill"], f["rule"]))

    if args.json:
        print(json.dumps(findings, indent=1))
    else:
        counts = {k: sum(1 for f in findings if f["severity"] == k) for k in order}
        print("%d skills, %d findings (%d critical, %d major, %d minor)\n"
              % (len(skills), len(findings), counts["critical"], counts["major"],
                 counts["minor"]))
        cur = None
        for f in findings:
            if f["skill"] != cur:
                cur = f["skill"]
                print("  %s" % cur)
            print("    [%-8s] %-28s %s" % (f["severity"], f["rule"], f["message"]))
            print("               %s" % f["where"])
    return 1 if any(f["severity"] == "critical" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
