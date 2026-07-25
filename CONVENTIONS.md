# Authoring a skill — house conventions

These are the conventions every skill in this repo follows. They exist because
the target is a **local small model** (see [README](./README.md) for the *why*);
the rules all trace back to "give the model fewer ways to go wrong."

A good template to copy from is [`calendar`](./calendar/).

## Layout

```
<skill-name>/          # folder name == skill name, lowercase
  SKILL.md             # the model-facing contract (see below)
  scripts/             # code the skill INVOKES — the runtime, not demos
    <tool>.py          # invoked as `python3 <path> …`, one JSON object per call
  README.md            # optional: human docs — setup, rationale, "why this exists"
```

Use the directory names Hermes prescribes, and pick by *role*, not by habit:

| Dir | Holds |
|---|---|
| `scripts/` | code the skill calls at runtime — **required to function** |
| `references/` | additional docs the model may read |
| `templates/` | output formats |
| `examples/` | **referenced example outputs only** — never runtime code |
| `assets/` | supplementary files |

This matters beyond tidiness: `hermes skills install` copies "SKILL.md plus the
exact local files it references," and **unreferenced files are not copied**.
Runtime code filed under a name that implies it is optional is a real install
hazard, not just a naming quibble.

File it by **what it is**, not by what it sits next to:

- A YAML/config the user deploys and fills in → `templates/`, even if its header
  says "example" (placeholders make it a template, not a sample output).
- An architecture/debugging write-up → `references/`.
- Data a script loads at runtime (`SCRIPT_DIR / "topics.json"`) → stays in
  `scripts/`. It is part of the runtime, not a demo.
- A `*.example` credential stub → stays beside the real file the user creates
  from it, i.e. in `scripts/`. Splitting the pair is more confusing than useful.

`examples/` should be *empty of anything the skill needs*. If nothing qualifies
as a referenced example output, the skill simply has no `examples/`.

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
