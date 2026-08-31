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
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}

# Subparser names whose operations are destructive (CONVENTIONS.md: "Any destructive
# operation stays behind a --confirm flag and refuses to run without it"). The stems are
# the convention's own vocabulary; a leading-stem match tolerates the house compound form
# (delete-image, clear-history). Matched against the subparser NAME — not anywhere in the
# file — because a word scan false-positives on benign uses ("clear" = browser-context
# reset in a probe script, "remove" = deleting the tool's own temp files), which is
# exactly how a rule learns to be ignored.
DESTRUCTIVE_STEM = re.compile(r"^(delete|remove|trash|clear|reset|purge|revoke)[\w-]*$")

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
    # The product name IS the domain here: the skill's own name is AgentMail, its
    # trigger phrases mention agentmail.to, and the model must be able to say the
    # product by name.
    "agentmail-lite": {"agentmail"},
}

# Files under scripts/ that are not entry points and so carry no JSON contract.
def _is_library(path):
    b = os.path.basename(path)
    return (b.endswith("_lib.py") or b.startswith("lib") or b.endswith("_test.py")
            or b.startswith("test_") or b == "__init__.py" or b == "conftest.py")


# ── entry-point derivation (entry-point declaration spec, 2026-08-28) ─────────
# A script carries the JSON contract only when the AGENT CAN RUN IT: when SKILL.md
# references it in code. "Referenced in code" = an inline code span or a fenced code
# block whose text contains a token whose LAST path component equals the file's name.
# Prose mentions never count (no backticks, no fence): "the dedup lives in
# news-dedup.py" is a sentence, not a command. Last-component matching is what makes
# all three documented path forms (bare `name.py`, `${HERMES_SKILL_DIR}/scripts/name.py`,
# `~/…/scripts/name.py`) work with no standardization.
#
# Two hard-won constraints, both hit live:
#  1. Pair backtick spans PER LINE. One stray/unbalanced backtick on an earlier line
#     throws off parity in a whole-doc scan and silently drops every later span
#     (verified 2026-08-28: square-appointments L84's `customer-info.py show` was
#     invisible, so a documented entry point was misclassified non-entry).
#  2. The inventory is git-tracked files PLUS the repo's own untracked-but-not-ignored
#     files — never a disk walk. pallo/square carry ~1,400 untracked .venv files under
#     scripts/; os.walk invents ~2,800 phantom scripts. The disk-fallback branch
#     (git ls-files fails: hermetic test lab, fresh clone before .git) is safe because
#     the .venv directory is excluded explicitly. Untracked-not-ignored files stay in
#     the inventory on purpose: they will be committed, so the contract applies to them
#     now, not at the commit where the rule suddenly fires on a "new" file.
#
# One bounded hop: a .sh that IS a direct entry point and delegates to exactly one
# tracked .py as its SOLE substantive (non-comment) command makes that .py an entry
# point too — a documented wrapper must not hide the code that actually runs. The two
# qualifiers are what keep it mechanical (the repo's only .sh census, every file read in
# full, 2026-08-28): fetch-tickers.sh's `exec "$PY" …/fetch-tickers.py` IS delegation;
# fetch-news.sh curls + jqs for real work and only THEN pipes to news-dedup.py —
# orchestration, NO hop; morning-briefing.sh fans out to four helpers — fan-out, no
# hop. (Repo-wide the hop is currently dormant: no skill has a .sh — it is retained for
# the day one does, with a battery case proving each shape.)

CODE_SPAN = re.compile(r"`([^`]+)`")
SCRIPT_TOKEN = re.compile(r"([\w.${}~/~-]+\.(?:py|sh))\b")
# A line in SKILL.md code that CALLS a tool (tools-table exemption, 2026-08-29): a
# known shell command followed by MORE than just the word. The bare-word exclusion is
# load-bearing — an inline span that merely names a tool (agentmail-lite's prohibition
# sentence carries `curl` in code) is a mention, not a call, and must not make a
# table-required skill of it. pet-care-tracker's `curl -fsS -H ... | jq ...` recipes
# DO match — which is right: its real defect is handing the model raw curl + bearer
# token with no table, so it keeps firing.
TOOL_TABLE_EXEMPT_CMDS = frozenset((
    "python3", "python", "bash", "sh", "curl", "jq", "uv", "uvx", "pip",
    "pip3", "node", "npx", "npm", "git", "docker", "ffmpeg", "sqlite3",
    "rg", "grep", "sed", "awk", "tar", "ssh", "scp",
))
CMD_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\b(.*)")
# A line delegating to a tracked .py: some python interpreter (literal, or the
# variable-interpreter form a wrapper uses: `exec "$PY" …/x.py`) + a path whose last
# component ends in .py, as an argument to THAT line. `python3 <<'PYEOF'` heredocs and
# embedded python never match (no .py path argument). {0,2} tolerates the wrapper's
# argument shapes (`exec "$PY" "$(dirname "$0")/x.py" "$@"`).
DELEGATION = re.compile(
    r"(?:^|[;&|]\s*)(?:\S+\s+)?\S*python3?\s+(?:\S+\s+){0,2}\S*\.py\b"
    r"|(?:^|[;&|]\s*)exec\s+(?:\S+\s+){0,2}\S*\.py\b")
# Shell lines that set up rather than do work: variable assignments (plain, or the
# conditional `if [ -x … ]; then PY=…` / `else PY=…` interpreter-selection shape),
# test compounds, and BARE control-flow words. Anything else — including
# `for t in …; do curl …; done` — is work: a loop body that curls is exactly the
# fetch-news.sh "work-then-pipe" shape the hop must NOT grant.
NON_INVOCATION = re.compile(
    r"^\s*(export\s+)?[A-Za-z_][A-Za-z_0-9]*\s*="
    r"|^\s*(?:if|elif)\s.*\bthen\s+[A-Za-z_][A-Za-z_0-9]*\s*="
    r"|^\s*else\s+[A-Za-z_][A-Za-z_0-9]*\s*="
    r"|^\s*\[[^\]]*\]"
    r"|^\s*(then|else|fi|do|done|esac)\b\s*$"
    r"|^\s*(if|elif)\s.*\bthen\s*$")


def _skill_inventory(d, skill_name):
    """Basename set of the skill's scripts (.py/.sh) — git-tracked plus untracked,
    never a disk walk. Returns None when git is unavailable (test lab / fresh clone),
    in which case the caller uses the disk fallback (which excludes .venv)."""
    try:
        r = subprocess.run(["git", "-C", ROOT, "ls-files", skill_name + "/scripts/"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    names = {os.path.basename(f) for f in r.stdout.splitlines()
             if f.endswith((".py", ".sh"))}
    try:
        u = subprocess.run(["git", "-C", ROOT, "ls-files", "--others", "--exclude-standard",
                            skill_name + "/scripts/"],
                           capture_output=True, text=True, timeout=30)
        if u.returncode == 0:
            names |= {os.path.basename(f) for f in u.stdout.splitlines()
                      if f.endswith((".py", ".sh"))}
    except (OSError, subprocess.SubprocessError):
        pass
    return names


def _disk_fallback(sdir):
    names = set()
    if not os.path.isdir(sdir):
        return names
    for base, dirs, files in os.walk(sdir):
        dirs[:] = [x for x in dirs if x != ".venv"]
        names |= {f for f in files if f.endswith((".py", ".sh"))}
    return names


def _code_text(text):
    """Code-span and fenced-block contents, per line (see the two constraints above)."""
    parts = []
    in_fence = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            parts.append(ln)
        else:
            parts.extend(CODE_SPAN.findall(ln))
    return parts


def derive_entrypoints(d, skill_name, text, inv=None):
    """Return the set of script basenames the agent can invoke: direct references in
    SKILL.md code plus the one-hop delegation set (spec, 2026-08-28). `inv` is the
    skill's script inventory (basenames); when omitted it is computed here — the
    caller normally computes it once and shares it with 3a to avoid a second
    git round-trip per skill."""
    sdir = os.path.join(d, "scripts")
    if inv is None:
        inv = _skill_inventory(d, skill_name)
        if inv is None:
            inv = _disk_fallback(sdir)
    direct = set()
    for span in _code_text(text):
        for m in SCRIPT_TOKEN.finditer(span):
            comp = m.group(1).rsplit("/", 1)[-1]
            if comp in inv:
                direct.add(comp)
    hop = set()
    for sh in direct:
        if not sh.endswith(".sh"):
            continue
        try:
            src = open(os.path.join(sdir, sh), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # Sole-substantive-command test: the wrapper's invoking lines (everything
        # except comments and setup: assignments, [ -x ] tests, fi/done scaffolding)
        # must be EXACTLY ONE, and it must delegate to exactly one .py. Any other
        # invoking line (curl in a loop body, a fan-out to siblings) or a second
        # target = orchestration, not delegation.
        invoking = [l for l in src.splitlines()
                    if l.strip() and not l.strip().startswith("#")
                    and not NON_INVOCATION.match(l)]
        targets = set()
        if len(invoking) == 1 and DELEGATION.search(invoking[0]):
            targets = {m.group(0).rsplit("/", 1)[-1]
                       for m in re.finditer(r"\S*\.py\b", invoking[0])}
        if len(targets) == 1:
            t = next(iter(targets))
            if t in inv and t.endswith(".py"):
                hop.add(t)
    return direct | hop


def tool_table_exempt(text, entrypoints, inv):
    """True when a missing tools table is EXCUSED: the skill has no scripts and
    SKILL.md code makes no tool calls (no script references, no command lines).

    The table exists to document the tools a skill invokes. A pure-reasoning skill
    (the investment analysts: web_search/web_extract in prose, no scripts, no
    commands) has nothing to tabulate, and an unconditional mandate fires on the
    absence of a table that cannot exist. A skill that DOES call tools in code
    keeps the mandate — pet-care-tracker's curl/jq recipes match and it stays
    flagged (its real defect is handing the model raw curl + bearer token with no
    table at all).
    """
    if inv:
        return False
    if entrypoints:
        return False
    for frag in _code_text(text):
        for ln in frag.splitlines():
            s = ln.strip()
            if not s:
                continue
            m = CMD_LINE.match(s)
            if m and m.group(1).lower() in TOOL_TABLE_EXEMPT_CMDS and m.group(2).strip():
                return False
            if SCRIPT_TOKEN.search(s):
                return False
    return True


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
    # Tolerate a UTF-8 BOM and CRLF endings: the old ^---\n … \n---\n was LF-only and
    # BOM-less, so a perfectly good file saved with either silently failed the match and
    # fell back to {} - which then produced a wall of FALSE criticals (name mismatch,
    # missing routing fields) on content that was actually fine.
    #
    # Returns (mapping | None, body). None means "a frontmatter block is PRESENT but is
    # not valid YAML (or not a mapping)". The caller collapses that into a single
    # frontmatter/yaml finding instead of running the per-field checks: on broken YAML
    # every field is unreadable, so "name does not match" + "no PREFER" + "no triggers"
    # + … (~12 findings) buries the one true problem. Absent frontmatter (no block at
    # all) still returns {} - there the per-field findings are accurate, the fields
    # really are missing.
    m = re.match(r"^\ufeff?---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return {}, text
    raw = m.group(1)
    try:
        import yaml
        data = yaml.safe_load(raw)
    except Exception:                                          # noqa: BLE001
        return None, text[m.end():]
    if not isinstance(data, dict):
        return None, text[m.end():]
    return data, text[m.end():]


def extract_triggers(desc):
    """Return the quoted trigger phrases from the 'Activate on any of:' list, or [].

    The single source of truth for "what is a trigger" — both the baseline check in
    lint_skill() and the --update-triggers generator call this, so the two can never
    disagree about what counts (a divergence here is how the check would start flagging
    triggers it itself authored)."""
    i = desc.find("Activate on any of")
    if i < 0:
        return []
    return re.findall(r'"([^"]+)"', desc[i + len("Activate on any of"):])


def trigger_baseline_path():
    return os.path.join(ROOT, "tools", "trigger_baseline.json")


def load_baseline():
    """Read tools/trigger_baseline.json, or {} when absent/malformed.

    An empty baseline means "no regression check" — which is exactly the right posture
    for the hermetic test lab (the linter is copied there alone, no baseline follows it)
    and for a fresh clone before the file is committed. Never raises."""
    try:
        with open(trigger_baseline_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_skill_triggers(name):
    """Current trigger list for a skill, or None when it can't be read (unreadable file,
    broken YAML, no description). --update-triggers uses this to regenerate the baseline
    and to warn about skills it could not include."""
    try:
        text = open(os.path.join(ROOT, name, "SKILL.md"), encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return None
    fm, _ = frontmatter(text)
    if not fm:
        return None
    return extract_triggers(str(fm.get("description") or ""))


# Negation cue for the requires-toolsets tool scan: a tool named inside a
# prohibition ("Do not substitute curl", "Never make outbound requests via curl")
# is not used — the skill says the opposite. The linter scans whole raw SKILL.md
# text, so without this it flags the very sentence that bans the tool.
TOOLSET_NEGATION = ("do not", "don't", "dont", "never", "must not", "avoid",
                    "forbid", "prohibit", "no")


def _in_negation(text, pos, window=80):
    """True when a negation cue governs a tool mention — i.e. it sits in the
    same CLAUSE, before the token, within `window` chars.

    A raw proximity window is not enough: "No sign-in needed. | `python3
    script.py`" or "do not rewrite it from memory. - `web_search` …" put a
    negation within 40 chars of a *genuine* use in the next clause/cell. A
    negation only governs what follows before a clause break (`. ! ? , ; | )`
    or a newline), so the check starts at the last break before the token.
    This keeps "Do not substitute `curl`" suppressed while "No sign-in needed.
    | `python3 …`" still counts as a terminal use."""
    lo = max(0, pos - window)
    seg = text[lo:pos]
    breaks = [m.end() for m in re.finditer(r"[.!?,\n;|)]", seg)]
    clause = seg[breaks[-1]:] if breaks else seg
    return any(re.search(r"\b" + re.escape(n) + r"\b", clause, re.I)
               for n in TOOLSET_NEGATION)


def lint_skill(name, baseline=None):
    d = os.path.join(ROOT, name)
    sk = os.path.join(d, "SKILL.md")
    out = []

    def add(sev, rule, msg, where=""):
        out.append({"skill": name, "severity": sev, "rule": rule,
                    "where": where or "SKILL.md", "message": msg})

    try:
        text = open(sk, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        # One unreadable file must not mask the rest of the repo: the old behaviour was a
        # UnicodeDecodeError crash with empty output, which in CI fails the build and
        # reports nothing. Record a finding and continue instead.
        add("critical", "readability",
            "SKILL.md could not be read (%s); its rules were not checked - repair the "
            "file before this skill can be trusted" % e.__class__.__name__, "SKILL.md")
        return out
    fm, body = frontmatter(text)
    lines = text.splitlines()

    def lineno(pat):
        for i, l in enumerate(lines, 1):
            if re.search(pat, l, re.I):
                return "SKILL.md:%d" % i
        return "SKILL.md"

    # (bound before the if/else so the toolset + trigger-baseline checks below see them either way)
    hermes = {}
    triggers = []
    # What the agent can actually invoke (derived from SKILL.md code) vs what it cannot.
    # Computed here (not in the scripts section) because the body tools-table exemption
    # needs it; the scripts section reuses these instead of recomputing.
    sdir = os.path.join(d, "scripts")
    inv = _skill_inventory(d, name)
    if inv is None:
        inv = _disk_fallback(sdir)
    entrypoints = derive_entrypoints(d, name, text, inv)
    # ── frontmatter ────────────────────────────────────────────────────────
    # Broken YAML is ONE finding, not a cascade. Before this, a single malformed line
    # made every field unreadable and produced name + prefer + triggers + version +
    # license + tags + toolsets — the real problem ("the YAML does not parse") was
    # buried in the wreckage, and an author who fixed `name` saw the rest and gave up.
    if fm is None:
        add("critical", "frontmatter/yaml",
            "frontmatter is not valid YAML — none of the routing fields could be read; "
            "fix the YAML before any of this skill's other rules can be checked")
    else:
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
        # The trigger list, parsed — the SAME extract_triggers() that --update-triggers
        # uses to write the baseline, so the check and the generator cannot disagree
        # about what counts as a trigger.
        triggers = extract_triggers(desc)
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
            t = str(t)
            if t != t.strip():
                add("minor", "frontmatter/tags", "tag %r is not Capitalized" % t)
            # Capitalized = starts with a letter at upper case. A tag that starts
            # with a digit (3D Printing) keeps the first LETTER capitalized instead:
            # the old t[:1].isupper() check rejected "3D Printing" outright, which
            # sent the author to "3d printing" or to fight the rule.
            first_alpha = next((c for c in t if c.isalpha()), None)
            if first_alpha and not first_alpha.isupper():
                add("minor", "frontmatter/tags", "tag %r is not Capitalized" % t)

    # ── forbidden / required body content ──────────────────────────────────
    if re.search(r"NEVER read", text, re.I):
        add("critical", "body/never-read-section",
            "has a 'Files this skill must NEVER read' section - CONVENTIONS.md forbids it: "
            "it primes the behaviour it warns against", lineno(r"NEVER read"))
    if ERROR_SENTENCE not in text:
        add("critical", "body/error-sentence",
            "error section does not end with the mandatory sentence verbatim")
    for h in ("When to use", "When NOT to use"):
        if not re.search(r"^#+\s*" + h + r"\s*$", body, re.I | re.M):
            add("critical", "body/section-flow", "missing '## %s' section" % h)
    if not re.search(r"^\|.*\bPurpose\b", body, re.I | re.M):
        # A skill that invokes no tools — no scripts, no command lines in its code
        # (tool_table_exempt) — has nothing to tabulate; the mandate is for skills
        # that DO call things. The investment analysts are the exemption class;
        # pet-care-tracker keeps firing (its curl recipes are tool calls in code).
        if not tool_table_exempt(text, entrypoints, inv):
            add("major", "body/tools-table", "no tools table with a Purpose column")
    else:
        # The check is for Purpose tables only (its message says so). The old scan
        # looked at the SECOND cell of ANY table, so square-appointments' status
        # tables (headers "Status | What it means | …") fired six premise-false
        # findings on rows whose second cell is a status name, not a purpose.
        # Track the header of the current table; a table that carries no Purpose
        # column is not a purpose table, and its rows are out of scope.
        in_table = False
        tbl_has_purpose = False
        for i, l in enumerate(lines, 1):
            if not l.lstrip().startswith("|"):
                in_table = False
                tbl_has_purpose = False
                continue
            if not in_table:
                in_table = True
                tbl_has_purpose = bool(re.search(r"\bPurpose\b", l, re.I))
            m = re.match(r"^\|[^|]+\|\s*([A-Za-z]+)\b", l)
            if m and m.group(1) not in ("Purpose", "Tool", "Verb", "Script", "Command"):
                w = m.group(1)
                # a Purpose must LEAD with a verb; "The running total" does not.
                if tbl_has_purpose and w in ("The", "A", "An", "This", "It", "Returns"):
                    add("minor", "body/explicit-verb",
                        "tool Purpose does not lead with a verb", "SKILL.md:%d" % i)

    # ── model-context discipline ───────────────────────────────────────────
    for pat, why in (
        (r"^#+.*\b(why|rationale|background|design|architecture)\b", "rationale belongs in README"),
        # tend[s]? - the plural is CONVENTIONS.md's own canonical example ("small models
        # tend to call a verb that does not exist"); the old pattern missed it, so the
        # rule did not catch the sentence the rule was written from.
        (r"\b(tend[s]? to|models often|the model will try|a common mistake|don't reach for)\b",
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
        add("critical", "layout/dirs",
            "'%s/' is outside the four Hermes directories - invisible to supporting-file "
            "discovery and unwritable by the agent" % bad)
    if not os.path.exists(os.path.join(d, "README.md")):
        add("major", "layout/readme",
            "no README.md - rationale has nowhere to go but model context (%d SKILL.md lines)"
            % len(lines))

    # ── scripts ────────────────────────────────────────────────────────────
    # (sdir / entrypoints / inv are computed up top with the frontmatter block,
    # before the body tools-table exemption; reused here.)
    scripts = []
    if os.path.isdir(sdir):
        for base, _, files in os.walk(sdir):
            if ".venv" in base:
                continue
            scripts += [os.path.join(base, f) for f in files if f.endswith(".py")]

    # Drives the contract gate below and the undocumented-shebang warning.
    # 3a (entry-point declaration spec, approved 2026-08-28): a shebang says "run me",
    # but if SKILL.md code never references the file, the agent has no reason to — the
    # executable bit is a promise the docs don't back. Weak signal by design: minor,
    # never a gate. The disk walk is READ-ONLY and .venv-excluded; scope still comes
    # from the git inventory (a file git doesn't know and doesn't list untracked is a
    # phantom — ignored venv debris — and is skipped).
    if os.path.isdir(sdir):
        for base, _, files in os.walk(sdir):
            if ".venv" in base:
                continue
            for f in sorted(files):
                if not f.endswith((".py", ".sh")):
                    continue
                b = f
                p = os.path.join(base, f)
                if b not in inv:
                    continue
                try:
                    first = open(p, errors="replace").readline()
                except OSError:
                    continue
                if not first.startswith("#!"):
                    continue
                if b in entrypoints or _is_library(b) or b == "skill_json.py":
                    continue
                add("minor", "scripts/undocumented-shebang",
                    "carries a shebang but is not an entry point in SKILL.md code - either "
                    "document it (code span or fence) or drop the shebang / rename to a "
                    "library name", os.path.relpath(p, d))

    if re.search(r"(?<!python3 )(?<!python )\B\./\S*scripts/\S+\.py", text):
        add("critical", "scripts/invocation",
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
        # File hygiene applies to EVERY script — entry point or not: a silent except in
        # a helper is a real defect even if the agent never runs it directly, and a
        # destructive subparser with no brake is a footgun in whatever calls it.
        for m in re.finditer(r"except\s*(Exception)?\s*:\s*\n\s*pass\b", src):
            add("minor", "scripts/silent-except",
                "'except: pass' swallows a real failure silently",
                "%s:%d" % (rel, src[:m.start()].count("\n") + 1))

        # ── --confirm on destructive subcommands ───────────────────────────
        # README motivation #2 (local models hallucinate dangerous calls) is answered by
        # exactly one convention — "destructive ops stay behind --confirm" — and it was the
        # only convention with zero enforcement. Check the subparser NAMES against the
        # destructive stems, and require --confirm declared within that subparser's block.
        # A bare file-level "does --confirm appear" would pass any script that has the flag
        # once and guard nothing; a name scan of the whole file false-positives on benign
        # uses. Subparser-name + scoped-block is the precise, false-positive-free form.
        for m in re.finditer(r'add_parser\(\s*["\']([\w-]+)["\']', src):
            sub = m.group(1)
            if not DESTRUCTIVE_STEM.match(sub):
                continue
            nxt = re.search(r'add_parser\(\s*["\']', src[m.end():])
            block = src[m.start():m.end() + (nxt.start() if nxt else 2000)]
            if not re.search(r'--confirm\b', block):
                line = src[:m.start()].count("\n") + 1
                add("critical", "scripts/confirm",
                    "destructive subcommand %r has no --confirm guard — a hallucinated "
                    "call runs it with no footgun brake" % sub,
                    "%s:%d" % (rel, line))

        # The JSON contract is for what the agent INVOKES — the derived entry points.
        # The old gate was _is_library() alone: a name heuristic that let triplib.py
        # through (nobody runs it) while the real defect — "any script under scripts/
        # might be run, so it all carries the contract" — produced 28 premise-false
        # findings on probes, service files and imported helpers (entry-point
        # declaration spec, 2026-08-28). Non-entry scripts are still hygiene-checked
        # above; they just carry no contract.
        if os.path.basename(f) not in entrypoints or _is_library(f):
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
            # The guard can be applied bare (@guard) or dotted (@skill_json.guard); the
            # old @guard\b pattern only saw the former, so the idiomatic dotted form got
            # a false top-level-guard major on every vendored script that used it.
            has_guard = re.search(r"@[\w.]*\bguard\b", src)
            if has_emit and has_guard:
                continue
            if has_emit and not has_guard:
                add("critical", "scripts/top-level-guard",
                    "imports skill_json but main() is not decorated with @guard, so an "
                    "unexpected exception still escapes as a traceback with no JSON", rel)
                continue

        if '"ok"' not in src and "'ok'" not in src:
            add("critical", "scripts/json-contract",
                "prints no 'ok' field; the model cannot tell success from failure by a "
                "stable rule", rel)
        # A literal `sys.exit(1)` is only ONE shape of "exits non-zero on failure". The
        # house pattern (CONVENTIONS.md; donations, google-docs, web-access, ...) is
        # `def out(d, code=0): ... sys.exit(code)` or `sys.exit(main())` — a computed
        # status propagated, never the literal 1. Requiring the literal made the rule fire
        # on 39 of the repo's own scripts (the named "exemplar" included), which trains
        # authors to ignore the linter. Accept a propagated exit code — an identifier or a
        # main() call, never a literal digit — so a script whose only exit is
        # `sys.exit(0)` still fires: it genuinely cannot signal failure. Verified against
        # all 39 findings on 2026-08-27: 24 house patterns cleared, 15 no-exit scripts
        # (mostly imported modules) keep firing — that is a separate, pre-existing class.
        if not re.search(
                r"(?:sys\.)?exit\(\s*1\s*\)|SystemExit\(\s*1\s*\)"
                r"|sys\.exit\(\s*(?:main\(\)|[A-Za-z_]\w*)\s*\)"
                r"|raise\s+SystemExit\(\s*[A-Za-z_]\w*\s*\)", src):
            add("critical", "scripts/exit-code", "never exits non-zero on failure", rel)
        # An unguarded main() can die with a traceback and NO json on stdout.
        if "def main(" in src and not re.search(r"except Exception", src):
            add("critical", "scripts/top-level-guard",
                "no top-level exception guard: an unexpected error prints a traceback and "
                "no JSON object at all", rel)

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
    # Skipped when the frontmatter is broken: the declaration side is unreadable, and
    # "declares no requires_toolsets" against unparseable YAML is a lie.
    if fm is not None:
        needs = set()
        for tool, pat in (("web", r"\bweb_search\b|\bweb_extract\b"),
                          ("browser", r"\bbrowser_\w+\b"),
                          ("terminal", r"\bpython3 \S+\.py\b|\bcurl\b")):
            # a mention inside a prohibition clause is a non-use (see _in_negation)
            if any(not _in_negation(text, m.start())
                   for m in re.finditer(pat, text)):
                needs.add(tool)
        if needs and not hermes.get("requires_toolsets"):
            add("critical", "frontmatter/requires-toolsets",
                "uses %s but declares no metadata.hermes.requires_toolsets - if the toolset is "
                "absent the skill activates anyway and fails confusingly"
                % ", ".join(sorted(needs)))

    # ── trigger-list regression (baseline) ─────────────────────────────────
    # Gap 11: the 2026-08 incident where a description rewrite dropped 9 of google-docs'
    # triggers (19→10, the whole write side) and the linter passed it. A presence check
    # can only see "no list"; it cannot see "the list shrank". This compares against a
    # committed baseline (tools/trigger_baseline.json). Adding triggers is always safe
    # (it widens routing), so only REMOVALS fire. Removal is critical: a trigger gone
    # from the description silently narrows routing — the skill stops matching
    # conversations it used to — which is the "broken routing" class critical exists
    # for. (It was major while majors were advisory and CI gated on critical only;
    # promoting it here makes a dropped trigger block a merge, because an accidental
    # drop would otherwise ship unnoticed.)
    #
    # The baseline is a committed artifact, NOT a diff against git HEAD, for two reasons:
    # (a) CI's checkout is clean (HEAD == worktree) so the diff is always empty there;
    # (b) the commit that *introduced* the regression is exactly the point where worktree
    # and HEAD agree. Updating the baseline is a deliberate act — `--update-triggers` —
    # whose diff the author reviews, so an intentional drop is seen there and an
    # accidental one still fires in CI.
    if fm is not None and baseline is not None and name in baseline:
        missing = sorted(set(baseline[name]) - set(triggers))
        if missing:
            add("critical", "routing/triggers-baseline",
                "trigger(s) in the baseline but gone from the description: %s — if this "
                "was deliberate, review the diff and run --update-triggers"
                % ", ".join(missing))

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", help="lint one skill")
    ap.add_argument("--severity", choices=["critical", "major", "minor"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update-triggers", action="store_true",
                    help="regenerate tools/trigger_baseline.json from the current "
                         "descriptions and exit (does not lint). Run this ONLY when a "
                         "trigger change was deliberate; the diff it produces is the "
                         "record of what you chose to drop.")
    args = ap.parse_args()

    skills = sorted(x for x in os.listdir(ROOT)
                    if os.path.isdir(os.path.join(ROOT, x))
                    and os.path.exists(os.path.join(ROOT, x, "SKILL.md")))
    if args.skill:
        skills = [s for s in skills if s == args.skill]

    if args.update_triggers:
        # Start from the existing baseline so a skill whose description is currently
        # unreadable (broken YAML) keeps its old entry: the check is inert while the YAML
        # is broken (fm is None), and when the author fixes the YAML the old entry
        # reminds them to re-run this. A from-scratch dict would silently drop it.
        base = dict(load_baseline())
        skipped = []
        for s in skills:
            tr = read_skill_triggers(s)
            if tr is None:
                skipped.append(s)          # broken YAML / unreadable — keep its old entry
                continue
            base[s] = sorted(tr)
        # Prune skills that no longer exist — but only on a FULL-repo run. With --skill
        # the `skills` list is filtered to one, so pruning here would wipe every other
        # skill's baseline. A deleted skill left in the baseline would rot: the check
        # would keep demanding a trigger no skill has, and the finding would look like a
        # linter bug rather than a stale baseline.
        if not args.skill:
            stale = sorted(set(base) - set(skills))
            for s in stale:
                del base[s]
            if stale:
                print("dropped baseline entry for deleted skill(s): %s" % ", ".join(stale))
        path = trigger_baseline_path()
        with open(path, "w") as f:
            json.dump(base, f, indent=1, sort_keys=True)
            f.write("\n")
        print("wrote %d skills to %s" % (len(base), path))
        if skipped:
            print("warning: could not read triggers for %s — their baseline was left "
                  "untouched" % ", ".join(skipped))
        return 0

    baseline = load_baseline()
    findings = []
    for s in skills:
        findings += lint_skill(s, baseline=baseline)

    # The gate must reflect what EXISTS, not what the operator chose to print: filtering
    # by --severity before computing the exit code let a skill with a critical finding
    # pass CI when linted with --severity major (exit 0 on a red repo).
    criticals = [f for f in findings if f["severity"] == "critical"]

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
    return 1 if criticals else 0


if __name__ == "__main__":
    sys.exit(main())
