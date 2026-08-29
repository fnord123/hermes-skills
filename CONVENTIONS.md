# Authoring a skill — house conventions

These are the conventions every skill in this repo follows. They exist because
the target is a **local small model** (see [README](./README.md) for the *why*);
the rules all trace back to "give the model fewer ways to go wrong."

A good template to copy from is [`donations`](./donations/) — it is the one skill
verified to satisfy every rule below: long-form description with a PREFER clause and an
`Activate on any of:` list, the exact eight-section flow, verb-leading Purposes, a leak-free
domain surface, the mandated error sentence verbatim, and all rationale in its README.

(This used to name `calendar`. An audit found `calendar` carried the "Files this skill must
NEVER read" section this document forbids, omitted the mandated error sentence, and emitted
no `ok` field while exiting 2 instead of 1 — so anything copied from it inherited four
violations. Check with `python3 tools/lint_skills.py --skill <name>` rather than trusting
any exemplar, including this one.)

## Layout

```
<skill-name>/          # folder name == skill name, lowercase
  SKILL.md             # the model-facing contract (see below)
  scripts/             # code the skill INVOKES — the runtime, not demos
    <tool>.py          # invoked as `python3 <path> …`, one JSON object per call
  README.md            # optional: human docs — setup, rationale, "why this exists"
```

### Tier 1 — the four directory names are a Hermes requirement

Exactly four subdirectories exist as far as Hermes is concerned:

```
references/   templates/   scripts/   assets/
```

This is enforced in code, not style advice:

- `tools/skill_manager_tool.py` — `ALLOWED_SUBDIRS = {"references", "templates",
  "scripts", "assets"}` gates `write_file`/`remove_file`. **The agent cannot
  author a file anywhere else in a skill.**
- `agent/skill_commands.py` — when a skill has no explicit `linked_files`,
  Hermes discovers its supporting files by scanning *those four directories* and
  announcing "[This skill has supporting files:]" to the model. `skill_view`
  buckets results the same way, with everything else dumped into `other`.

So a file outside those four is invisible to supporting-file discovery and
unwritable by the agent. **Do not invent directory names** (`bin/`, `data/`,
`lib/`) — the cost is silent, not a warning.

**Do not create `examples/`.** It is a half-supported bucket: the hub fetcher
accepts it (`tools/skills_hub.py` — `_ALLOWED_SUPPORT_DIRS` includes
`"examples"`, so an install *will* copy files from it), but it is absent from
`ALLOWED_SUBDIRS` and from the supporting-file discovery scan. So content there
installs and then sits invisible to the model, and the agent cannot write to it
— the worst of both. Put example outputs in `assets/` if you ever have any.

Separately, `hermes skills install` copies "SKILL.md plus the exact local files
it references" and does **not** copy unreferenced files — so anything a skill
needs must be referenced from `SKILL.md` or `README.md` to survive an install.

### Tier 2 — our house rule for choosing among the four

Hermes does not care which of the four a file lands in; nothing treats them
differently. That freedom is what produces bikeshedding, so pick by **who
consumes the file**:

| Dir | Consumer | Contents |
|---|---|---|
| `scripts/` | the machine, at runtime | executable code, `requirements.txt`, and data the code loads by default (`SCRIPT_DIR / "topics.json"`) |
| `templates/` | the human, at setup | anything copied or instantiated elsewhere: every `*.env.example`, sample config JSON, deployable YAML, `SOUL.md` |
| `references/` | the model or human, on demand | architecture notes, debugging guides, API dumps |
| `assets/` | the skill | static resources that are not code and not loaded as config (seed data, images) |

The rule that settles the recurring argument: **a template lives in
`templates/`; the instance created from it lives wherever the code reads it.**
So `templates/config.env.example` is copied to `scripts/config.env` (or to
`~/.config/<skill>/`), and the two do not need to sit side by side.

## Frontmatter

```yaml
---
name: <skill-name>            # matches the folder, lowercase
description: >                # long, multi-line — NOT a ≤60-char one-liner
  <what it does>. PREFER THIS SKILL when <routing>. <one-line boundary vs other
  skills, if relevant>. Activate on any of: "<trigger>", "<trigger>", …
version: 0.1.0                # 0.x.0
license: MIT
metadata:
  hermes:
    tags: [Capitalized, Keywords]
---
```

The `description` does the routing: an explicit **PREFER** clause and an
**"Activate on any of:"** trigger-phrase list. It is model context — keep it in
the domain (below).

## SKILL.md section flow

`When to use` → `When NOT to use` → a **tools table** (one row per verb) →
turning the user's words into calls → output shape → common flows (worked
examples) → error handling → empty results. Match `calendar`'s ordering.

## SKILL.md is model context, not documentation

Everything in SKILL.md (body **and** description) is injected into the model's
context when the skill activates. So:

- Describe the **happy path** — what to DO.
- Push rationale, "why this exists", comparisons to other skills, and any
  discussion of failure modes to the **README**. Listing a failure mode in
  SKILL.md ("models tend to call `create_inbox`…") primes exactly that mistake.
- **Never name a tool the model can't see.** If an allowlist removed a tool,
  don't mention it — you'd re-introduce it. Anti-patterns are only OK for tools
  the model *does* have (e.g. "don't run `x-mcp` from `terminal`").

## Leak-free domain abstraction

Hiding complexity in the script is necessary but not sufficient — the **exposed
surface** must also speak the user's domain, never the backend's:

- Name verbs, flags, JSON output fields, and error strings in domain vocabulary.
  (donations exposes `donation`, `item`, `total` — never `tab`, `cell`, `row`,
  `formula`, or "spreadsheet".)
- Omit framing the model's input doesn't carry — e.g. don't mention
  "voice/dictation" when Hermes receives plain text.
- Prefer a **high-level domain skill** wrapping a generic capability over
  exposing the primitive (donations, not raw Sheets access) — and don't register
  the primitive for the model at all.

The model reasons about whatever words are literally in front of it; a stray
backend term drags it off the domain.

## Be explicit — the model won't impute

- **Lead every tool Purpose with an explicit verb** ("Gets the running total",
  not "The running total").
- Give a **word → call** table so the model doesn't improvise the mapping.

## Errors

- End the error-handling section with: *"Always ask the user for guidance when
  there is an error; do not proactively try to resolve errors yourself."*
- **Do not include a "Files this skill must NEVER read" section.** It's
  provocative and there are countless off-limits files anyway; naming two adds no
  protection and primes the behavior it warns against.

## Footgun guards

Any **destructive** operation stays behind a `--confirm` flag and refuses to run
without it — and only after the user has explicitly approved that exact action.
Local models hallucinate dangerous calls; this is the standard guard.

## Scripts

- Live under `scripts/`, invoked as `python3 <path> …`.
- Print **one JSON object** on stdout: success `{"ok": true, …}`, failure
  `{"ok": false, "error": "…"}` with **exit 1**.
- `hermes skills install` over HTTP drops the executable bit, so always document
  invocation as `python3 <path>`, never `./<path>`.
- Read credentials/config from files the scripts own; keep keys out of the repo.
- **Entry points are the scripts SKILL.md references in code** (a code span or a
  fenced block — prose never counts). Only entry points carry the JSON contract:
  the linter derives this set instead of assuming everything under `scripts/` is
  runnable, so helpers, probes and service internals are checked for hygiene
  (silent excepts, destructive subcommands) without being held to a contract the
  agent can never trigger. A documented `.sh` wrapper whose sole command delegates
  to a `.py` counts as that `.py`'s declaration (one hop, no further).


## Checking your work

```
python3 tools/lint_skills.py                 # whole repo
python3 tools/lint_skills.py --skill donations
python3 tools/lint_skills.py --severity critical
```

`critical` gates the build. It means a broken install (undeclared third-party
imports, a script that does not parse), broken routing (no PREFER clause, no
trigger list, or a trigger dropped from the description against the committed
baseline), a silent capability gap (a skill that uses a toolset without
declaring it, a destructive subcommand with no `--confirm` guard, a directory
outside the four Hermes ones, a "NEVER read" section, or broken frontmatter),
or a broken script contract (a documented entry point whose output carries no
`"ok"` field, whose `main` has no top-level exception guard, or which can
never exit non-zero on failure — all three block, because the model then
cannot tell success from failure by a stable rule).
CI runs this on every push and fails the build on any critical finding.

`major` is the conformance layer: domain vocabulary in model context, missing
tools tables, failure-prime prose, and so on. It is tracked in TODO.md and fixed
in order, but it does not block a merge.

`tools/skill_json.py` implements the JSON contract in the Scripts section above. Vendor it
into a skill as `scripts/skill_json.py` and use `ok()` / `fail()` / `@guard` rather than
hand-rolling the envelope — `@guard` in particular turns an uncaught exception into a proper
`{"ok": false, ...}` instead of a traceback on stderr with nothing on stdout.
