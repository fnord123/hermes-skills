---
name: hermes-skills-repo
description: >
  The hermes-skills house-repo rubric and mechanics: CONVENTIONS.md,
  README.md, tools/lint_skills.py, the eight-section house format, and the
  linter rule map with its known false-positive classes. The skill covers
  the lint-rule change procedure and the host-neutrality transform for
  doctrine docs. The skill documents house commit and push mechanics,
  distributing a house skill to profiles, and the skill-maintenance
  pipeline cards. Use when authoring, extending, or reviewing a skill in
  the house repo, deciding how to distribute one, or operating the
  skill-maintenance board. PREFER THIS SKILL over generic
  skill-authoring guidance whenever work touches a skill folder inside
  the house repo. This covers authoring a new skill, adding a verb, a
  compliance review, interpreting lint findings, changing a lint rule, or
  committing house-repo work. The house rules differ from the
  hermes-agent in-repo standard, and the linter enforces them. Activate on any of:
  "author a skill in the house repo", "review a house-repo skill",
  "CONVENTIONS.md compliance", "interpret lint_skills.py findings",
  "change a lint rule", "adopt a skill into the house repo", "pass the
  house linter", "commit house-repo work", "distribute a house skill to
  profiles", "operate the skill-maintenance board", "host-neutral doctrine
  extraction".
version: 0.9.3
license: MIT
metadata:
  hermes:
    tags: [Skills, Authoring, HouseRepo, Review, Conventions]
    requires_toolsets: [terminal, web]
---

# hermes-skills house repo

Author, extend, and review skills in `{{REPO_DIR}}` — the owner's personal skill
library. This is a DIFFERENT rule set from the hermes-agent in-repo standard
(`hermes-agent-skill-authoring`): here the description is long-form with a
**PREFER** clause and an **"Activate on any of"** trigger list, the section flow
is an eight-section house order, and `tools/lint_skills.py` is the enforcement
mechanism. Do not port rules between the two repos.

This file is the skill's **canonical home**. The pipeline roles
(author, audit, STE100, scripter, verifier, commit, fleet) grade against it
and install it from this repo. They do not carry hand-made copies.

## Host neutrality (standing policy — the owner, 2026-09-03, stated twice)

`{{REPO_DIR}}` is meant to be REUSABLE BY OTHERS. Never commit
host-bound values: assignee/profile names, machine absolute paths, model
ids, board/task/commit ids from run history, personal names, fleet census
counts. What goes in the repo is DOCTRINE — work orders, rubrics, graph
and mechanics, pitfalls (distilled, no run ids), card bodies as
`{{token}}` templates + a `PROFILE.example` naming the tokens. What stays
in the profile is the INSTANCE — filled values, execution logs, decision
ledgers, censuses. "Put this in {{REPO_DIR}}" on host-bound docs is a
TRANSFORM (de-host), not a move. Verify before committing: leak grep = 0
across the new tree, only declared tokens present, full-repo linter
adding no findings, explicit staging, push verified `0 0` +
`git ls-tree origin/main`. Worked example: `skill-kanban/` (commit
`144f3cd`, 2026-09-03). Full workflow + token list:
`references/host-neutral-doctrine-extraction-2026-09.md`.

## When to use

- Adding a new skill, or a verb/capability, to a skill in `{{REPO_DIR}}`
- Committing or pushing finished house-repo work (see "Committing and pushing")
- Reviewing a house-repo skill for CONVENTIONS.md compliance
- Deciding how to distribute a house skill to other profiles (hub/tap vs external_dirs)
- Operating the skill-maintenance kanban board (card graph, parentage rule, loop caps)
- Interpreting `lint_skills.py` findings (which are real, which false-positive)
- **Scope trap (incident, 2026-09-01):** "the hermes skills repo" ≠ `{{REPO_DIR}}` only.
  The fleet repo `{{HERMES_HOME}}` (`{{FLEET_REPO}}`) ALSO tracks `skills/` (profile-shared
  skills) + `profiles/*/config.yaml` + `managed/` — it is the other skills scope on
  this host. When asked to review/summarize "uncommitted changes to the skills repo(s)",
  prove BOTH: `git status` in `{{REPO_DIR}}` AND in `{{HERMES_HOME}}`. A clean house repo
  with a dirty fleet tree (or vice versa) is not an answer — name which repo carries
  the changes. A dirty fleet tree is NORMAL (living config repo, many profiles write
  into it); a dirty house repo is the unusual one worth investigating.

## When NOT to use

- Skills in the hermes-agent repo — that is the `hermes-agent-skill-authoring`
  standard, a different rule set.
- Personal skills directly under `{{HERMES_HOME}}/skills` — plain
  `skill_manage` there. Exception: the fleet repo `{{HERMES_HOME}}`'s OWN
  tracked `skills/` tree (see the scope trap under "When to use") —
  reviewing or rolling back changes THERE is this skill's job, minus the
  linter (which lives in `{{REPO_DIR}}` and does not cover `{{HERMES_HOME}}`).

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/trace-handoff.py`, invoked as
`python3 ${HERMES_SKILL_DIR}/scripts/trace-handoff.py <profile>`. Each call
prints ONE JSON object on stdout: `{"ok": true, ...}` on success,
`{"ok": false, "error": "..."}` with exit 1 on failure. The script opens the profile's
`state.db` read-only (`mode=ro`) and never writes to it.

| Verb | Purpose |
|---|---|
| `trace-handoff <profile>` | Prints the profile's newest tool-calling session's `kanban_*` calls in row order (args: title / assignee / parents / task_id). Includes each create's result: the new card's id and born status. Tallies every other tool the session used — the durable proof of handoff order and bank consultation, from the worker's OWN record. |

Output shape: `{"ok": true, "profile": "<p>", "session_id": "<s>",
"kanban_calls": [{"order": N, "call": "<name>", "args": {...}, "result":
{...}}, ...], "other_tools": {"terminal": N, "...": N}}`. The profile DB
resolves from the `HERMES_HOME` env var (default `~/.hermes`): the script
opens `{{HERMES_HOME}}/profiles/<profile>/state.db` — never a hardcoded
home. Errors are uniform (house pattern). Every failure path — no
`state.db` for the profile, no session with tool calls, a missing
argument — prints `{"ok": false, "error": "..."}` with exit 1. A missing
argument puts the usage text inside `error`:
`{"ok": false, "error": "usage: trace-handoff <profile>"}`.

## The rubric — read it every session, don't trust memory

1. `{{REPO_DIR}}/CONVENTIONS.md` — authoritative. **Uppercase**: the
   tracked doc is exactly `CONVENTIONS.md` — confirm with
   `git ls-files 'CONVENTIONS.md'`, never with a remembered listing; a
   lowercase look-alike in any listing is not the doc.
2. `{{REPO_DIR}}/README.md` — repo-level rules.
3. `tools/lint_skills.py` — mechanical enforcement; run it, but know its limits
   (below). Full rule map: `references/linter-rules.md`.
4. Exemplar: `donations/` is the one skill verified against every rule.
   `calendar/` is a documented BAD exemplar — CONVENTIONS.md lists its four
   violations; copy nothing from it.

## Adding or extending a skill

1. Read CONVENTIONS.md, then the skill's SKILL.md + README.md + script.
2. `git status` in the repo first — prior sessions often leave uncommitted work;
   build on it, don't clobber it.
3. Implement. Scripts print ONE JSON object per call; failures are
   `{"ok": false, "error": "..."}` with exit 1. House pattern (Phase 2,
   2026-08-29 — all pallo/square scripts): `@guard` on `main`, every output
   path through the vendored `skill_json.py` (`ok()`/`fail()`,
   `NoReturn`-annotated; vendor via `tools/vendor.py sync` — five lockstep
   copies), informational outcomes the agent relays are `ok: true` with the
   outcome in `status`; real failures are `ok: false` + exit 1.
4. Keep the model-facing surface domain-leak-free: verbs, flags, JSON fields,
   error strings in user vocabulary. Backend terms (`"service account"`, `"oauth"`)
   are fine inside code blocks/inline code/URLs — the leak scan strips those —
   but they leak when written in prose.
5. Use `${HERMES_SKILL_DIR}/scripts/...` in SKILL.md — Hermes substitutes the
   token at load time. A hardcoded `/home/...` path is a broken recipe on any
   other install; grep for it before calling a review done.
6. If you touch the description, diff the trigger list before and after (Pitfall 1).
7. Live-test any new verb end-to-end on a throwaway document, including the
   error path. Then clean up and verify the cleanup (below).

## Reviewing a skill (compliance pass)

1. Baseline pre-existing findings: `git stash`, lint, `git stash pop`, then lint
   the working tree. Findings present in both are not yours to fix without a
   judgment call; new ones are.
2. The linter checks mechanics, not truth. Verify every factual claim in
   SKILL.md (output shapes, flags, behavior) against the script's source — the
   linter cannot catch a wrong claim. For CLI/flag/behavior claims in a DIFF,
   verify against the live CLI or the hermes-agent source, never by reading
   the diff alone: a diff's prose is another agent's self-report, and a
   WRONG factual claim can survive review (incident 2026-09-01: an uncommitted
   tool-control skill edit asserted "There is NO `hermes -p <profile>` flag" +
   promoted `HERMES_PROFILE` as the profile switch; `hermes_cli/main.py
   _apply_profile_override()` proves `-p`/`--profile` is consumed pre-import,
   and a live `hermes -p <other> config get` of a profile-specific key proved
   it routes. Negative claims — "NO such flag", "ignored", "never fires" —
   demand a LIVE DISCRIMINATOR TEST: pick a key that differs across profiles
   and read it via the claimed-ignored switch vs the claimed-valid one; a
   test that returns the same value either way proves nothing.)
3. Check the description's first ~65 chars: they are the routing window. Lead
   with what the skill does, in domain words — not "Used to ...".
4. Confirm the mandated error sentence is verbatim and closes the error section:
   "Always ask the user for guidance when there is an error; do not proactively
   try to resolve errors yourself."
5. Report a rule-by-rule table with evidence (file lines, linter output, code
   checks) — not vibes. "Looks fine" is not a review outcome.

## Interpreting lint findings

- **0 critical is the ship line** — CI fails only on critical.
- **Never quote a remembered census — measure.** `python3 tools/lint_skills.py
  --json` on the current checkout is the baseline; TODO.md's snapshot is the
  standing ledger — re-census before quoting any count. A dated "current
  state" snapshot goes stale the day it is written: `git log` is the
  changelog, and the rules in this section stand on their own without one.
- **`scripts/exit-code`**: the house pattern `sys.exit(main())` / `sys.exit(<code>)`
  is accepted (it propagates a computed status). It still fires on the known
  **entry-point-vs-library class** (non-entry files the agent never runs). The
  discriminator is "does the skill document the script as a command the agent
  runs", NOT "does it have `def main`" (most of the class has one). The three
  contract rules (`json-contract`, `top-level-guard`, `exit-code`) are all
  **critical** — severity lives in `tools/lint_skills.py` itself; battery G6
  pins it (a demotion back to major breaks the suite). Entry-point derivation
  spec: `references/entry-point-declaration-spec-2026-08.md`; file-level
  breakdown: `references/linter-rules.md`.
- **`scripts/json-contract` is a one-directional token test** (probed 2026-08-28):
  it fires only when a quoted `"ok"` token is absent from source (sound), and is
  satisfied by ANY occurrence — including a comment-only `"ok"` and `"ok"` as a
  dict *value* (six currently-passing pallo/square scripts carry a non-`ok`
  success field while passing). It does not verify what the agent parses from
  stdout; behavioral closure needs a smoke run, which is gated on entry-point
  declaration.
- **Entry-point declaration (spec: `references/entry-point-declaration-spec-2026-08.md`):**
  scripts are declared in SKILL.md code (body mechanism — code span or fence,
  never a manifest as source of truth). `undocumented-shebang` is **minor**
  (warning-layer, never a gate). `derive_entrypoints()` + `_skill_inventory()`
  in `tools/lint_skills.py` decide which files the three contract rules gate.
  An entry-point script carries the full house pattern: vendored
  `skill_json.py` (`tools/vendor.py sync` keeps the copies in lockstep),
  `@guard` on `main`, `ok()`/`fail()` (`NoReturn`-annotated) on every output
  path. Clean output on every declared entry point keeps the contract rules
  at zero findings and their critical gate free of premise-false hits.
  Behavioral convention: informational outcomes the agent relays
  (`ambiguous_trip`, `no_trip_found`, `pallo-trip-status` sweep results) are
  `ok: true` with the outcome in `status`; real failures are `ok: false` exit 1.
  The `.sh`→`.py` hop rule is **dormant repo-wide** (no skill has a `.sh`);
  battery I7/I8/I9 pin its three shapes (thin wrapper hops; work-then-pipe and
  fan-out do NOT). Known limits: the `ok` token test is still one-directional
  (behavioral `--smoke` closure is future work, gated on entry-point
  declaration — which now exists).
- **`frontmatter/requires-toolsets` — clause-scoped negation filter (2026-08-28,
  `e1b58e5`)**: a tool mention inside a same-clause prohibition ("Do not substitute
  `curl`", "**No** `curl`...") is not a use — matches governed by a negation cue
  (do not / don't / dont / never / must not / avoid / forbid / prohibit / no, window
  80, no clause break (`. ! ? ,` newline `; | )`) between cue and token) are dropped.
  It exists because `agentmail-lite` fired on the sentences that *forbid* curl; its
  sanctioned path is `mcp_agentmail_*` MCP tools — it uses no terminal. **0 findings
  repo-wide as of the 2026-08-28 census**, and the rule is **CRITICAL as of `a5173d2`**
  (promotion rubric = "Changing a lint rule" step 4: re-census the rule's current
  findings; zero findings ⇒ promotion gates future regressions only). The zero is a
  measured census, not a promise — re-census before acting on it.
- **`body/domain-leak` has NO negation filter and scans the WHOLE file incl. frontmatter**
  (verified 2026-08-28 at `1a21e43` against the scan source): the clause-scoped
  `_in_negation` filter exists only in `frontmatter/requires-toolsets`; the leak scan
  greps lowercased prose over the full document, so "requires NO `OAuth`" (calendar) is
  the same FP shape `agentmail`'s "Do not substitute `curl`" was — reword it or port the
  filter; the scan will never self-suppress a prohibition. And the `name:`/
  `description:`/`tags:` lines ARE scanned: a skill whose domain IS a named backend
  fires on its OWN name (`agentmail-lite`'s only finding was SKILL.md:2, its own `name:`
  line). Fix class for that: a per-skill `LEAK_ALLOW` entry (precedent:
  `bambu-store: {"myshopify"}`) — never reword the product out of its own description.
  **Standing rule (the owner, 2026-08-31): never add a
  `LEAK_ALLOW` (or any linter allow-list) entry without explicit direction to
  do so** — fix the prose instead; allow-list only when the owner says so. The
  sanctioned class is narrow: the skill's own product name IS the domain
  (`agentmail-lite`; `bambu-store`/`myshopify` setup-doc precedent).
- **`body/tools-table`** requires a table with a Purpose column (the check is a
  whole-body `^\|.*\bPurpose\b` regex; a *files*-table with a Purpose column
  satisfies it, e.g. daily-briefing). The rule exempts a skill with no tracked
  scripts, no derived entry-point references, and no shell-recipe command lines
  — `tool_table_exempt()`, the computed-premise pattern; its full semantics live
  under "Changing a lint rule" operation class (e). The bare-word exclusion is
  load-bearing: a `curl` in prose is a mention, not a call, while real recipe
  command lines keep the mandate. Measure the live census with
  `python3 tools/lint_skills.py --json`. In this repo the rule's only finding
  is pet-care-tracker: its remaining defect is the `scripts/pet_care.py` it
  never had (TODO.md §5). That skill hands the model raw
  `curl`/`jq`/bearer-token.
- **The linter tests itself**: `tools/test_lint_skills.py` is the battery —
  skill cases plus gate tests (G1–G8), run by CI and pre-push. Re-count the
  cases with `grep -c 'case(' tools/test_lint_skills.py`, never from memory.
  After changing lint rules, run it — a case exists for every
  audited false positive, and a new rule without a case is a promise, not a mechanism.
  Adding a rule or a filter means adding its cases in the same commit: B14a/B14b
  (model-context must-not-fire controls), E3/E4 (negation-filter pair), I1-I10
  (entry-point derivation: premise-false silence, prose-is-not-a-command, gate fires
  on a documented span, 3a both directions, hop yes/no on three wrapper shapes, L84
  stray-backtick regression), G6 (contract-rule severity pin), T1–T3
  (tools-table exemption: no-scripts exempt / command-line fires /
  bare-mention quiet) all landed with their rules.
- `scripts/silent-except` on deliberate best-effort paths (log writes, trashing
  an upload artifact) is by design when the README documents the swallow.
- Full map: `references/linter-rules.md`.

## Changing a lint rule (only when the owner approves it)

The linter is shared machinery; a rule change moves findings for every skill at
once. **First name the operation class** — each has a different correct
before/after shape: **(a) additive rule** (new check), **(b) loosening fix**
(accept more), **(c) suppression/filter** (drop a match class), **(d) severity
promotion** (flip a finding's severity), **(e) scope change** (the rule now
applies to a *different set of files*). (a)–(c) are steps 3–5, (d) is step 6,
(e) is below. Then follow the procedure that held up in the 2026-08-27/28
audit rounds:

1. **Capture the before-state as JSON**: `python3 tools/lint_skills.py --json >
   /tmp/lint_before.json` — never "remember the counts".
2. **Classify every finding in the rule's current output** (parse the JSON, group by
   skill) and read the actual source of each flagged file BEFORE writing the patch.
   The count you're given may be stale, and "the rule fires N times" is usually
   several distinct classes wearing one label (one rule had 39 findings that
   resolved into 24 house-pattern + 15 library modules + 1 never-linted — only one
   class was in scope). Fixing all of them at once is scope creep; fixing the
   in-scope class and *documenting* the rest is the right shape.
3. **Dry-run a NEW pattern against the whole repo before you commit to it.** When the
   change adds a rule that greps for something (a flag, a verb, a phrasing), run the
   candidate pattern over every `scripts/*.py` first and read each hit. A literal
   word-scan almost always over-fires on benign uses — the 2026-08-27 `--confirm`
   check matched 12 "destructive" verbs that were actually browser-context cleanup
   (`clear`) and tempfile removal (`remove`); anchoring on **argparse subparser
   names** (the real user-facing operation) dropped it to the 3 that mattered. If the
   dry-run isn't clean, refine the matcher before it becomes a rule — a rule that
   false-positives on the current repo teaches authors to ignore it on day one.
4. **A new rule on a compliant repo is expected to move the baseline by zero.** For an
   *additive* rule (unlike a loosening fix), the correct before/after per-rule diff is
   **byte-identical** — the rule fires only on a regression that doesn't exist yet. If
   it fires on the current repo, either the repo is non-compliant (a real finding) or
   the matcher is too loose (a defect in your rule). Distinguish those before
   committing: the 7/9/10 round landed byte-identical at 64 major because all three
   new rules had a clean negative control. New rules land as **major** (reported in
   CI + pre-push, not build-gating); promoting to **critical** is a separate gate
   decision the owner owns. **Before answering a promotion question, re-classify the
   rule's *current* findings** (step 2 applied to that rule's live output): if any
   belong to a premise-false class, promotion gates the build on findings the rule
   was never meant to catch — scope/fix those first, then promote. A rule with zero
   current findings is promotable today and would gate future regressions only.
5. **Loosened rules need a must-still-fire case.** If the change makes the rule accept
   more, pin the sharp edge with a new battery case that the old rule also fired on
   (e.g. "only `sys.exit(0)` must still fire") — otherwise the fix can silently swallow
   the rule's purpose and nothing notices. The same two-direction shape applies to ANY
   suppression/filter change: a negative control that must NOT fire (E3 — a prohibited
   tool mention) AND a sharp edge that MUST still fire (E4 — a real use sitting next to
   a prohibition). The 2a negation filter (2026-08-28, `e1b58e5`) shipped exactly this
   pair.
6. **Run the battery** (`python3 tools/test_lint_skills.py`), then the **per-rule diff**
   of before/after JSON: `Counter(rule)` each side, show the delta table. Exactly the
   expected rule moves, everything else byte-identical. Any other rule moving is a
   bug in your patch, not a baseline. For a *severity* change specifically:
   (a) the repo run must be byte-identical (the severity flip changes nothing if the
   rule has 0 findings — verify that), (b) prove the teeth are real — a lab fixture
   exercising the promoted rule must report it at critical AND flip the exit code to 1
   (the 2026-08-28 promotion commit did exactly this for all four promoted rules;
   G7 did it for the 2026-08-31 batch), (c) update the severity-bearing docs in
   the same commit (CONVENTIONS.md's critical-definition paragraph, TODO.md's
   snapshot) — a doc that still says "critical means the two failures a user
   actually notices" after five new critical categories is a false fact in the
   tree. **Precondition for ANY promotion candidate (added 2026-08-31): re-probe
   the rule's documented false-positive shapes LIVE before flipping** — the note
   in the rule map may be stale in either direction (an FP may have been fixed, or
   may still fire); the decision must rest on a fresh hermetic-lab probe of each
   known-FP shape, and a still-live FP is a hard veto even for a pre-approved
   candidate (that is how `body/model-context` lost its 2026-08-31 promotion:
   both shapes re-probed, both fired). And an early-returning rule (e.g.
   `readability` — the linter stops checking that skill after recording the
   finding) needs its OWN lab skill in the gate test: one skill cannot exercise
   it and anything else.
**(e) Scope change.** The rule now fires on a different set of files. Its
   before/after is neither byte-identical (like an additive rule) nor a severity
   no-op (like a promotion) — it is a *targeted removal* of a premise-false class.
   Verify three things:
   - **Enumerate the removed findings by file and prove each is the intended
     class**, not a regression. "28 findings vanished" is not a verdict; "these 28
     are the probes + service files + `triplib.py` the spec named" is. The survivors
     must all be true positives (the rule keeps its teeth); a rule that hits 0
     (e.g. `exit-code` at Phase 1) becomes promotion-safe.
   - **Audit the sibling rules on BOTH sides of the gate you moved, not just the
     one you intended.** A gate change re-scopes every check that shares the loop.
     Phase 1 moved the `_is_library()` gate, which silently changed what
     `silent-except` and `--confirm` see; the safe move was to enumerate every file
     that newly enters/exits scope and check each for a finding in a *neighbouring*
     rule (a new `--confirm` hit would be a new CRITICAL = exit 1). A scope change
     that only audits the intended rule ships a silent re-scoping of the others.
   - **A moved mechanism with zero in-repo firing cases is dormant — the repo census
     CANNOT prove it.** If what you ported (e.g. the `.sh`→`.py` hop) has no live
     consumer, the before/after repo run is byte-identical whether the logic is right
     or broken. Validate it in a hermetic lab against its *known real-world shapes*
     (the four `.sh` shapes: thin-wrapper hops; work-then-pipe, fan-out, heredoc do
     not) — and each battery case must **discriminate**: it fires under the correct
     logic AND would NOT under the naive/old one (I10 asserts per-line pairing finds
     the span while a whole-doc scan loses it). A regression case that both the buggy
     and the fixed code pass documents intent but guards nothing.
   - **Prefer a computed premise over an exception list** (the owner, 2026-08-29:
     exceptions are "brittle and not scalable"). The exemption is a predicate on
     the rule's own premise, not a per-skill allowlist. Second instance
     (tools-table, `8fa941a`): `tool_table_exempt()` exempts a skill iff it has NO
     tracked scripts AND NO derived entry-point references AND NO shell-recipe
     command lines — the bare-word exclusion is load-bearing (a `curl` in prose is
     a mention, not a call; a prohibition sentence must stay quiet while real
     recipes keep firing). Dry-run first: exactly 4 exemptions across 16 skills,
     pet-care-tracker's live curl/jq recipes kept firing. Battery T1–T3 pins all
     three directions.

7. **Update `references/linter-rules.md`** (this skill) in the same commit — a rule-map
   entry that says "see false positive" for a fixed false positive is the single most
   common way this knowledge rots.

## Cleanup of live-test artifacts

- Trash test docs with `drive.files().update(fileId=..., body={"trashed": True},
  supportsAllDrives=True)` — the pattern docs.py itself uses. The
  `files().trash()` method was absent from the discovery doc served in this
  environment; don't retry it.
- **Verify the deletion**: `files().get` each id directly (expect 404) plus a
  sweep (`list` by name / folder query). A delete command that hangs and returns
  no output is not a deletion — the call may have silently succeeded, and the
  witnesses (404, empty sweep) are the only evidence that counts.

## Committing and pushing (only when asked)

1. `git fetch origin`, then `git rev-list --left-right --count main...origin/main`
   and `git log 759778a..HEAD` style checks: establish what is AHEAD of the
   last known state BEFORE planning commits — prior sessions may have already
   committed (or pushed) what you assumed was still uncommitted. A "2 logical
   commits" plan can collapse to 1 once HEAD is checked.
1a. **"Is it committed and pushed?" first asks "is it even committable?"**
   `git status` shows NOTHING about ignored paths — before answering any
   commit-state question about a specific path, run `git ls-files -- <paths>`
   (empty = NOT tracked) and `git check-ignore -v <paths>` (names the blocking
   rule). Measured 2026-09-03: the fleet repo's `.gitignore` is
   deny-by-default + allowlist (root `/*` denied, then `!` re-allows);
   the profiles block re-allows exactly `config.yaml` + `SOUL.md` per
   profile, so everything else under a profile (`skills/`,
   `future-work/`) stays ignored — the entire skill-kanban doc tree (this
   skill's SKILL.md + references, the `future-work` card spec + canonical
   bodies) is UNCOMMITTABLE without an explicit carve-out, so. READ THE
   ACTUAL `.gitignore` before citing a rule from a single `check-ignore`
   line: the 2026-09-03 first answer inferred the whole file from one
   matched line and got the mechanism wrong (the owner caught it) — the
   verdict was right, the reasoning was not. "yes, committed and pushed" is a false-positive
   there; the honest answer is "not committable as-is" + the options
   (carve-out, relocate, or local-only + /tmp backup). And when the fleet
   tree is dirty from OTHER profiles' curator churn (the normal state), that
   is not your changeset — never sweep it into a commit (R4).
2. The repo has a **pre-push hook that runs what CI runs**
   (`pre-push: lint, lint self-test, vendor and tests ok` — lint, the linter's
   own battery, vendor check, skill-script tests). Treat its pass as an
   independent verification witness; its fail blocks the push. **The hook is not
   a static file** — it is generated by the tracked `tools/install-git-hooks.sh`
   (run once per clone; `.git/hooks/` is not version-controlled). When CI steps
   change (new check in `lint.yml`), update the installer's heredoc and re-run
   `bash tools/install-git-hooks.sh`, or the local gate and CI silently diverge.
   And stage by explicit file list, never `-A`: after `git add <files>`,
   `git status --short` must show exactly the approved changeset staged, with only
   deliberately-excluded lines left unstaged — a stray one-line edit you did not
   write is never swept into your commit.
3. Commit style (house convention from existing history):
   `<skill>: <imperative summary>` — e.g. `google-docs: add comment verb
   (quoted section + best-effort anchor)` — followed by body paragraphs in the
   order: what the constraint/design is, the verb signature + output shape,
   SKILL.md/README changes. Match the tone of `git log --oneline -5`. A commit
   spanning several skills takes the `skills:` prefix (precedent `e1b58e5`);
   linter/gate-only commits take `lint:` / `gate:` (precedent `8fa941a`,
   `7767b16`). Version-bump rule (settled 2026-08-29): bump `version` when the
   trigger surface or verbs change (every version bump so far was a verb/trigger
   change); a pure pitfall-note commit ships WITHOUT a bump (google-docs
   `e5cac4f` stayed at 0.4.0). When
   the commit also syncs a state file (TODO.md, the rule map), grep-verify every
   number written into it — versions, counts, deltas — against the real file
   first; a version written from memory is a false fact shipped into the tree.
4. `git push origin main`, then verify: `git status -sb` must show
   `## main...origin/main` with no ahead/behind.
5. One push can also be a NO-OP check: if the user asks "push the repo" and
   local == origin, say so with the `fetch` + `rev-list` evidence instead of
   manufacturing a commit.
5a. **Pipeline runs push their own commits — a local-vs-remote diff that shows
   nothing about the reviewed skill is CORRECT, not a loss.** The Commit card
   pushes from the worker's session, so pipeline work lands on `origin/main`
   the moment the run ends; `git diff origin/main..HEAD` then shows only
   unpushed local work (e.g. the doctrine commit). When the user asks where a
   reviewed skill's changes went, or the diff appears to be "missing" them:
   `git fetch origin && git log --oneline origin/main -5`, then `git show
   <pipeline-sha>` — THAT is the change (2026-09-04: the pre-ipo update
   `f8cee42` was pushed by the pipeline; the user's diff showed only the
   doctrine rename `2d79c63` — nothing was lost). Mirror hazard: the remote
   can MOVE between your turns — another profile's push or a pipeline push can
   land between checks (the same day, an rx-review commit `a67c45b` appeared
   above `f8cee42` after the run, outside my session context entirely). Always
   fetch + read the remote log before declaring what is local vs pushed, and
   name remote commits you did not make when you notice them.
6. **Subset commit in a dirty tree:** stage EXACTLY the approved file list,
   confirm `git diff --cached --stat` shows only that set, and after the
   commit re-run `git status --short` and REPORT what is still dirty. A
   subset commit narrows the tree, and the user will check the rest —
   2026-09-01: committed one config line out of a 10-path dirty tree, and
   the next user message was "I thought several skill files still had
   uncommitted updates?" The post-commit status listing preempts it.
7. **Rolling back an uncommitted file (deliberate discard to HEAD):**
   capture-first (Pitfall 12) applies to intentional discards too, with a
   stronger verify: (a) `git diff -- <file> > /tmp/<name>-dropped-<date>.patch`
   and confirm the hunk count matches the reviewed diff — the patch is the
   recovery witness AND carries any good content mixed into the discarded
   changeset (2026-09-01 tool-control rollback: 4 hunks, three factually
   sound; the whole-file revert was the owner's call, but salvageable parts
   deserve a recorded home, so say where the patch lives); (b)
   `git checkout -- <file>`; (c) verify at BLOB level, not just status
   level: sha256 of the disk file == sha256 of `git show HEAD:<file>`,
   plus empty `git diff --stat -- <file>` and empty
   `git status --short -- <file>`. "Status is clean" only proves no delta
   against the index; the sha pair proves the committed content is on disk.

## Settled stance: skill pattern vs MCP server (2026-08-27)

For `{{REPO_DIR}}` capabilities consumed by Hermes profiles: keep the
**skill** (script + SKILL.md) over wrapping it in an MCP server, because
(a) the SKILL.md description IS the routing surface — the trigger list carries the
routing; MCP tool descriptions are a fraction of that and the when-to-use /
pitfalls sections have nowhere to live without a companion skill (at which
point the skill is doing the job anyway); (b) the script has no process to
crash — MCP adds a daemon, token-refresh-in-a-daemon (SA tokens: 1h), health
checks, and a VM→host→Google network hop; (c) profiles inherit the shared
skill free via `skills.external_dirs` — zero wiring. Revisit only if a
non-Hermes consumer appears or real CLI-quoting friction shows up in
transcripts — and the honest shape then is HYBRID: keep the script as the
engine, wrap it in a thin MCP server, keep a slim companion skill for
routing. Full analysis + the evidence that the Google-hosted Docs MCP is a
dead end (2 tools only, anonymous discovery breaks `hermes mcp login`) is in
the profile skill `google-workspace-mcp`.

**The hybrid shape already materialized in web-access (census 2026-08-31):**
`1f4ada4`+ did exactly the predicted hybrid for web-access — the fetch stack is
now the **webaccess service** on the docker host (one handler core, two
facades: HTTP `:8910` and MCP `:8911/mcp`); the service code + `service.yaml`
+ config template live in `{{REPO_DIR}}/web-access/` (the repo IS the
deployment source, since `dab6de4`/`f371fcf`); the skill's `web_access.py`
is a byte-for-byte CLI shim over the HTTP facade; SKILL.md routes to the MCP
tools first ("MCP tools (preferred)… Use these.", CLI shim for cron/terminal).
Black-box status: the MCP tool descriptions name **no** backends ("cache,
NCBI API, plain HTTP, a self-hosted render, and a stealth browser"); the
**Black box LANDED (2026-08-31 — the owner: never expose internals to agents, ever).**
The lesson is per-RESPONSE, not per-FIELD: the first pass removed only `via`
from the `fetch` response and still leaked the same internals through `attempts`
(its `layer` names every rung: `cache`/`http`/`firecrawl`/`bladebro`/
`browserbase`) and `detail` ("`firecrawl` rendered 5000 chars"). The whole
returned JSON is the boundary — audit EVERY field + the tool description, not
just the obvious one. Final state: `cmd_fetch` (`handlers.py`, shared by both
facades) is the single sanitizing boundary — the model gets
`ok, url, outcome, chars, truncated, text` + a `next` what-to-do hint on
failure, and nothing rung-shaped; `rxfetch` keeps its full internal trail
(`Result.via/.detail/.attempts`) for the container's audit log, and `cmd_fetch`
still reads the raw `r.detail` internally to pick the 404/DNS/unreachable
hint. SKILL.md, the MCP tool description, and the shim docstring are
de-branded; the `web-access: {firecrawl}` LEAK_ALLOW entry is dropped (see the
standing rule above). Verified: a stubbed-fetch probe enumerating the returned
keys finds no vendor name; 139/139 web-access unit tests; 93/93 linter
battery; repo census unchanged (46). Consumer census + re-census keys:
`references/webaccess-deployment-2026-08.md`.
MCP+skill vs skill-only profiles, plus the separate third path — the builtin
`web: search_backend/extract_backend` toolset pointed at the same `searxng`/
`firecrawl`) is measured in `references/webaccess-deployment-2026-08.md`,
including the grep keys to re-census.

## Promoting a profile-local sibling skill into a shared skill

Another profile's bot may keep a profile-local skill that complements a
house-repo skill (a peer profile's `google-docs-raw-api` beside `google-docs`).
It is invisible to `git` in `{{REPO_DIR}}` — before concluding "nobody
changed the skill," sweep `find {{HERMES_HOME}}/profiles -type d -name "<skill>"`
plus recently-modified SKILL.md files. When asked whether to fold one in:

1. **Reconstruct its history from the profile's curator state; don't trust
   the file.** A profile's `skills/` dir is not a git repo; it has
   `.curator_ledger.jsonl` (one entry per curator write, sha256 before/after)
   and `.curator_backups/blobs/<sha>` (content keyed by that hash). The
   creation blob can be empty — diff later blobs in ledger order. The ledger
   is the audit trail of what the bot changed, and when.
2. **Fact-check its claims against the shared script's source.** It is
   another agent's self-report: grep every function it claims to import
   (`d._docs()`, `d._batch()`, …) in the real script and verify each API
   claim before promoting anything from it.
3. **Promote only what is verified, transferable, and non-redundant.**
   Incident-derived rules with a real repro promote; framing/positioning
   prose gets re-expressed in CONVENTIONS style, not copied (frontmatter,
   `${HERMES_SKILL_DIR}`, no hardcoded paths).
4. **Merge through the normal path**: verb-table row + word→call row +
   output-shape row, triggers added with a before/after count (Pitfall 1),
   version bump, README verb list, lint, live-test both paths, commit + push.
5. **Do not unilaterally slim the twin.** The end state is the twin
   becoming a pointer to the shared skill, but that edits ANOTHER profile's
   curator-managed skill — draft the pointer version and get the owner's
   (bot's or the owner's) sign-off first.
6. **Track the coupling.** If the twin imports the shared script's *private*
   (`_`-prefixed) functions, a future internal refactor breaks it silently;
   note it in the commit and keep it in mind before touching internals.

The first promotion (2026-08-27, `google-docs-raw-api` → `google-docs`:
rename verb + concurrent-editor race rules) is recorded in
`references/sibling-promotion-2026-08.md`.

## Distributing a house skill to other profiles (hub/tap vs external_dirs)

the owner's standing question (asked 2026-08-31): can Hermes be pointed at a URL or
GitHub repo and pull a skill down? Yes — `hermes skills install <URL-to-SKILL.md>`,
`hermes skills tap add owner/repo` + `install owner/repo/path/skill`, or registries via
`search`/`browse`. Mechanics verified in source (`tools/skills_hub.py`,
`hermes_cli/skills_hub.py` in the local install) — condensed in
`references/skills-hub-tap-distribution-2026-08.md`. The three facts that matter for
fleet distribution:

1. **Updates are real and safe-by-default.** Per-profile `skills/.hub/lock.json`
   records `identifier` + `content_hash`; `hermes skills check` re-fetches from the
   RECORD source (provenance-pinned — a removed tap yields `unavailable`, never a
   same-named skill from another registry) and compares hashes. `update` replaces only
   stale skills and **skips any whose disk content drifted from the recorded hash**
   unless `--force`.
2. **The profile does NOT know its copy is managed.** No read-only marker is injected
   at load; nothing stops a bot from editing its installed copy. Drift is detected
   AFTER the fact — and a drifted copy **silently stops receiving updates** until
   reconciled or `--force`d. That is exactly the drifted-build-context failure mode wearing
   a hub costume.
3. **Mitigations if we tap-distribute the house repo** (cheap → strong): (a) a
   read-only convention line in the skill's SKILL.md (the only thing that reaches the
   model); (b) a per-profile cron running `hermes skills check` and reporting
   `update_available`/skipped-local-edits; (c) triage by churn — tap-distribute stable
   docs-like skills, keep frequently-edited ones (e.g. google-docs, which already has a
   profile-local twin) as `skills.external_dirs` by design. Taps and external_dirs are
   not exclusive: existing profiles keep `external_dirs`; tap is for new consumers.

**Fleet census — measure, never quote.** Which profiles hold tap-installed
copies is a per-install fact: enumerate each profile's `skills/.hub/lock.json`
(the home-based `default` profile's lock is
`{{HERMES_HOME}}/skills/.hub/lock.json`; the tap identifier form is
`skills-sh/{{REPO}}/<skill>`). Which profiles inherit via `external_dirs`:
`grep -l 'external_dirs' {{HERMES_HOME}}/config.yaml
{{HERMES_HOME}}/profiles/*/config.yaml` — most configs, plus the global one,
declare `external_dirs: {{REPO_DIR}}` (live inheritance, no drift surface).
Decisions (the owner, 2026-08-31): stale tap installs stay untouched (no
catch-up sweep), the all-skills sweep is DEFERRED, and drift handling is
report-only — no standing reconcile/`--force` authority. A tap copy shadows
the `external_dirs` copy of the same name — the profile-local copy wins, so
a tap copy is the one that must be updated.

## Skill-maintenance kanban (the owner's board, 2026-08-31)

The seven-card pipeline — Author → Audit → STE100 → Scripter →
Verifier → Commit → Propagation-Check (STE100 = the ASD-STE100
writing-standard audit, inserted 2026-09-03; a failed draft loops back
to Author via [STE100 round N/3], cap 3; step 6 is
Propagation-Check; the separate periodic fleet-wide census job (designed, not yet built)) —
runs on the dedicated
`skills` board (created 2026-08-31;
DB `{{HERMES_HOME}}/kanban/boards/skills/kanban.db`; first card run verified the same
day). Every card: `--workspace dir:{{REPO_DIR}}` +
`--skill hermes-skills-repo` + per-class `--max-runtime`. **Doctrine
layer (host-neutral, versioned in the house repo — commit `144f3cd`,
2026-09-03): `{{REPO_DIR}}/skill-foundry/`** — `board.md` (operating
reference: goal → flow → per-card → loop caps → substrate →
operations), `spec.md` (graph, per-card work orders, R1–R8, kickoff
mechanics), `cards/` (the seven bodies as {{placeholder}} templates),
`known-pitfalls.md` (distilled failure classes), `PROFILE.example`
(the six install tokens). The files below are this fleet's INSTANCE —
filled values; a handoff's "read the sibling canonical" points at the
instance, and a doctrine change is made in the repo copy first, then
re-substituted into the instance. Full card spec + this fleet's dated
execution log (instance content — not carried in this repo):
`{{HERMES_HOME}}/profiles/{{OPERATOR}}/future-work/
skill-kanban-card-spec-2026-08-31.md`; the instance operating reference
(filled values, decision ledger, re-census keys) lives beside it under
the same `future-work/` directory. Canonical card bodies (written
per card, reused verbatim): `{{HERMES_HOME}}/profiles/{{OPERATOR}}/future-work/
skill-kanban-cards/<card>.md` (Author: `author.md`; Audit:
`audit-canonical.md`; STE100 Writing Audit:
`ste100-audit-canonical.md`; Scripter:
`scripter-canonical.md`; Verifier:
`verifier-canonical.md`; Commit: `commit-canonical.md`; Propagation-Check:
`propagation-check-canonical.md`). Assembly discipline (learned the hard way,
step 4): substitute the `__PIPELINE_INPUT__` / `__CHANGESSET_MANIFEST__`
tokens and ASSERT none remain — but note `__pycache__` in prose also
contains `__`, so assert on the full token, not a bare `__`. A token that
appears in MORE THAN ONE place in a body is a latent double-substitution
bug: replace-all hits both and the second one mangles live instructions
(observed step 4: the Commit body's own successor section carried
`__PIPELINE_INPUT__`, the assembled body went over the 8 KB worker-context
cap, and the Fleet handoff came out garbled). Keep the assembled body
under 8 KB — the cap is verified in source, not folklore: the worker
context builder caps the STORED card body (what `kanban_create` wrote) at
`_CTX_MAX_BODY_BYTES = 8 * 1024` (`kanban_db.py:482`, applied in
`_build_worker_context` as `s[:limit] + "… [truncated, N chars omitted]"`
— tail-truncated at context-build time; the stored row keeps the full
length, so the DB shows the over-cap body even though the worker saw a
cut one). **The cap does NOT apply to the on-disk work-order file the
body points at**: the house idiom is that the stored body carries the
payload + `"Your work order: read <canonical path> and follow it, with the
payload above as your PIPELINE INPUT"`, and the worker reads the canonical
with its file tools — so an on-disk canonical over 8 KB is fine, and only
a large INLINE embed (the `VERBATIM` handoff bodies: Fleet, Commit) ever
puts a stored body at risk. Proven 2026-09-04: audit-canonical.md
(8,837 B) and ste100-audit-canonical.md (8,737 B) sit over 8 KB on disk
while every stored body in the same run was under (Author 7,127 B, Audit
4,025 B, STE100 2,724 B, Commit 1,843 B) — do NOT report an on-disk
work-order file as an over-cap seam (that misread shipped in the same
day's fix list); when debugging "worker never saw the tail", measure the
STORED body (`hermes kanban --board <b> show <id>` Body section), not the
canonical file. The 2026-09-02
board-DB audit found stored bodies of 8,595–11,108 bytes across proven
runs (Audit, Verifier, Scripter, Commit all have examples — board
reference). Those workers ran fine because the work steps sit at the
head, but the tail (RULES IN FORCE / late handoff detail) is the part
that gets cut, so an over-cap body carries a latent seam. If a
successor's body is large or token-bearing, the parent card should
instruct the worker to READ the sibling canonical file with its file
tools instead of carrying an inline template.

- **Force-loaded skills are referenced, not restated (the owner, 2026-09-02).**
  `--skill` is repeatable on `kanban
  create` (action=append; verified in `create --help`). The card body
  carries ONE line referencing the force-loaded skill and NEVER restates
  its rules — the force-loaded SKILL.md is already in the worker's
  context, so restating adds bytes toward the 8 KB cap and a second
  source of truth. Every card class (including retry Authors) is
  `--skill hermes-skills-repo` ONLY. Exactly one exception exists: the
  STE100 Writing Audit card (graph slot 2b, between Audit and Scripter)
  also force-loads `asd-ste100`. Any card body or kickoff command
  force-loading `asd-ste100` on another card class is stale. Its FAIL
  loop is `[STE100 round N/3]` back to Author. The cap is 3, separate
  from the Audit loop's counter. The loop-caps bullet is the single
  source for the caps in force.

- **Card-class change protocol (proven 2026-09-03, the STE100 removal).** A
  force-load / instruction change to a card class lives in PARALLEL sources of
  truth: the canonical body in `future-work/skill-kanban-cards/<card>.md` (work
  steps AND the `skills` list in the handoff branch — a retry card's `skills`
  array silently re-loads a removed skill), the card spec (per-card section, the
  §10.3 "Every successor" line, AND the kickoff/handoff table rows — three
  separate hunks), this skill's force-load bullet, and the board reference's
  dated sections. Before editing: grep the token (e.g. `asd-ste100`) across all
  four locations and count occurrences. After: re-grep to zero (deliberate
  supersession notes excepted). The 2026-09-03 pass needed exactly this — the
  final re-grep caught a spec hunk that had silently failed to land (patch
  tool escape-drift rejection) and a list item with a stray leading space; the
  the per-patch diffs alone would have shipped a stale §10.3 line. Supersede, never
    rewrite history: mark the dated decision section SUPERSEDED and append a new
    dated section, so the decision ledger stays intact. A LOOP-CAP change is a
    sibling change class (exercised 2026-09-04, STE100 cap 2→3) with its own site
    list: identify the target loop by marker — the STE100 marker is `[STE100
    round N/x]`, distinct from the Audit and Verifier loops' bare `[round N/2]`
    — then patch, in all THREE layers (repo SoT `{{REPO_DIR}}/skill-foundry/`,
    instance mirror `future-work/skill-kanban-cards/`, fleet SKILL.md): (1) the
    retry marker, (2) the PARK threshold (`ste100_round >= N`), (3) the cap
    labels (cap N / max N / ≤N), (4) the retry-title `N = ste100_round + 1`.
    Verify: grep the OLD value across all three layers → zero (deliberate
    supersession notes excepted) AND confirm the OTHER two loops' bare
    `[round N/2]` / `max 2` are intact (change only the target loop) — and a filtered `git diff` (every changed line is cap-related) proves nothing else moved. An
    in-flight run is unaffected — its stored bodies were fixed at creation and
    its round counter rides in per-card payloads, so a cap change takes effect
    from the next kickoff — but any owner commit the change produces must be
    PUSHED before firing a pipeline (re-)run, or the Commit's pre-flight 1c
    fails on the unpushed local commit.
diagram lives in TWO places** (the spec §0 graph box and the board
reference's flow section) — adding or removing a card must update BOTH,
and every edge (especially retry loops) must be checked against the
spec's branch tables one-for-one: the 2026-09-03 first draft of the
doc's flow diagram was missing the STE100→Author retry line and drew
the Verifier's fail edge FROM Commit (it points at Scripter). The
spec's graph was right, the doc's was wrong — the two diagrams drift
independently.
- **Operating reference docs: goal → flow → per-card sections (the owner,
  2026-09-03 rewrite).** A board/pipeline reference doc that grows as a
  dated running log (decision sections stacked on superseded ones,
  step-run narratives) is UNREADABLE and gets rewritten. The working
  shape: (1) goal of the board, (2) overview of the flow — one diagram,
  every retry loop drawn as a line, exception edges named in text below
  it, (3) one section per card, then loops/caps table, substrate facts,
  decisions (dated table with live/deferred/superseded status), re-census
  keys, and a COMPRESSED history appendix. Before rewriting: back the old
  file up to `/tmp/<name>.pre-rewrite-<date>` with its sha256 recorded,
  and reference the backup from the new doc's header + appendix (history
  preserved, not rewritten). During the rewrite, verify every
  infrastructure claim against LIVE state, not the old doc — the
  2026-09-03 rewrite caught the "weekly watchdog cron" the old doc listed
  as part of the design but which never existed (`cronjob list` = 0
  jobs); carry it forward as "designed, not created," never as fact.
- **The parentage rule (reordered 2026-09-04 per the owner; doctrine `7df63f2`):
no pipeline card is ever created without `--parent`, and every card
COMPLETES ITSELF FIRST, then creates its successor. No card may exist —
`todo` or anywhere — before its prerequisite is `done`; because the parent
is `done` at create time, the successor is born `ready`, never parked in
`todo`. Both orderings were live-verified: the original create-before-
complete (2026-08-31) and the complete-first reorder (GATE-v3, 2026-09-04:
a non-goal worker completed its own card, then in the same turn created the
successor and show-checked it; the child was `ready` in the board DB). Also production-verified across the full seven-card chain (2026-09-04 pre-ipo-investment-analysis run): five consecutive complete-first handoffs — Author→Audit, Audit→STE100, STE100→retry-Author, retry-Author→Audit, Audit→STE100 — each successor born `ready`, zero cards ever in `todo`, including the FAIL-branch loopback (STE100 FAIL → retry Author → Audit → STE100 round 1/2).**
- **Manual reclaim re-runs the card** (observed 2026-09-01): a reclaim at
  14:19:27 re-queued the task and the dispatcher spawned a fresh run at
  14:19:30 — 3s later. Reclaim is not a stop signal; the worker comes back
  with the same card body.
- **Stopping a pipeline = archive EVERY card in the chain AND kill the
  worker; kill alone is NOT a stop (observed 2026-09-04).** A killed worker
  on a still-`running` card is re-claimed and RE-SPAWNED by the dispatcher
  (same body, fresh PID) within seconds. Sequence: check the board FIRST
  (the chain may have advanced past the card you think is running — the user
  asked "did you stop the run" after the chain had already moved on), kill
  the worker(s), `archive` every card of the run (done cards included — they
  are part of the discarded run), then WAIT a few seconds and re-check `ps`
  for a respawn before reporting stopped; discard `.kanban-scratch/` + the
  assembled body. End-state to report: board empty (all archived), 0 worker
  processes, repo clean.
- **Archiving a task clears the parentage gate** (observed 2026-09-01, a child card): a child whose parent was `archived` (never `done`) promoted and
  claimed ~1s after creation. The "no card runs against nothing" invariant is
  bypassable by archive-as-cancel; the child's own body must therefore carry
  the full handoff (it does, by design) or the run has no input. And neither
  `block` nor `archive` signals an already-spawned worker — an in-flight run
  continues its work order and its `kanban_complete` is later rejected with
  "already terminal"; the durable record for that run is a final handoff
  `kanban_comment`.

- `create --parent <id>` is a hard dependency gate: the child is born `todo` and
  auto-promotes to `ready` only when ALL parents are `done` (observed live) — and
  the mirror case the complete-first handoff relies on: a card created AFTER all
  its parents are already `done` is born `ready` DIRECTLY (substrate rule in
  `create_task`, `kanban_db.py:3169`), so it never appears in `todo` at all. A
  card with no parent is immediately `ready` and runs against nothing — that is
  the structural parentage failure; post-hoc `link` is how it happens.
- **The substrate has NO verdict channel**: a child promotes on parent `done`
  regardless of what the parent's summary says. So a FAIL verdict still creates
  its successor (retry card, issues in the successor's body) — and under the
  complete-first order (2026-09-04) the FAIL card completes FIRST, then creates
  the retry. PASS and FAIL differ only in WHICH successor gets created.
- Handoff = the parent's `complete --summary` (+ `--metadata` JSON), auto-injected
  into every child's worker context as a "Parent task results" snapshot. Payload
  lives in two places on purpose: successor's `--body` (standing spec) and the
  parent's `--summary` (evidence snapshot the child re-verifies). Under the
  complete-first order the successor id cannot ride in `complete --summary` (the
  complete call precedes the create) and `kanban_complete` must NOT pass
  `created_cards` (the id doesn't exist yet) — the id is recorded as a
  `kanban_comment` on the parent so it survives in the record.
- **Worker handoffs use the `kanban_*` TOOLS, not the CLI (verified 2026-08-31,
  step 2 - this corrected the spec).** A kanban worker's sandbox exposes the
  `kanban_*` toolset (`kanban_show/list/complete/block/comment/create/link/
  unblock`) as its board interface - it also has `terminal` (for git/lint), but
  the handoff is via the tools, NOT `hermes kanban` in a shell. The worker knows
  its own id from the `HERMES_KANBAN_TASK` env var. Creating a successor =
  `kanban_create(title=, body=, parents=[<own-id>], workspace_kind="dir",
  workspace_path=..., skills=[...], max_runtime_seconds=, model=)`; finishing =
  `kanban_complete(summary=, metadata={...})`; PARK = `kanban_block(reason=,
  kind="needs_input")`. So the `hermes kanban ... create --parent` CLI form in
  this skill is the **kickoff** command (card 1, created by me from chat);
  every later edge is a worker `kanban_create` with a `parents` list. Card
  bodies must tell the worker to use its `kanban_*` tools, not "run `hermes
  `kanban`".
- **A non-goal worker SURVIVES its own `kanban_complete`** (GATE-v3,
  live-verified 2026-09-04 on a scratch board with the exact production
  worker class — `-p sr-*`, no `--goal`): one turn can run
  `kanban_complete` → `kanban_create` → `kanban_show`; the session does
  not end on completion, and the successor appears `ready` in the board DB.
  This refutes the earlier belief "workers are one-shot, so the successor
  must be created BEFORE completing — late-creation is impossible without
  substrate changes"; that belief produced the pre-created-successor design
  the owner rejected on 2026-09-04. Caveats: (a) keep complete→create→show in
  ONE turn — in `--goal` mode the judge rejects a premature complete, and
  the goal loop reaps the worker the moment the task is `done` between
  turns; (b) if the worker dies between complete and create, the pipeline
  stops at a `done` head with no successor — visible and recoverable; that
  is the accepted price of the complete-first rule.
- **A worker WILL edit its own loaded skill mid-run if it learns something
  durable** (S5, observed 2026-09-01 across three fleet-check runs — the
  hermes-skills-repo skill's SKILL.md + a reference were rewritten during
  runs, mtime-witnessed, content accurate but not owner-directed). The card
  body's ROLE prohibition is the ONLY control (the worker loads the skill
  before it reads the card, so the skill cannot forbid what the card is
  about to do). Every pipeline canonical carries the explicit line now;
  when you spot the mtime jump, treat the edits as DRAFTS: verify each
  claim against source, then owner-sign them (keep or reissue) — never let
  worker-signed skill edits stand silently, and never revert good content
  just because the author was a worker.
- **The successor's body is the ONLY channel the successor sees - a
  placeholder that reads like a work order IS a work order** (incident,
  2026-09-01, step 3: the Verifier's paraphrased step-4 note was read as a
  real Commit work order; the placeholder committed AND pushed a throwaway
  fixture to origin/main - `e776308`, reverted as `be58dcd`). Binding fix:
  every placeholder successor body carries the no-op INSTRUCTION BLOCK
  VERBATIM ("STEP 4 PLACEHOLDER. ... Do NOT commit, push, or edit any repo
  file. Do NOT create any successor. ... complete with summary 'HANDOFF-STOP: ...'"),
  and a handoff whose body is missing it is INVALID. The step-2 Audit
  placeholder survived only because its negative was unambiguous.
- **Model tier per card class (the owner, 2026-08-31):** Author + Scripter =
  `--model {{MID_MODEL}}` (mid tier - draft/script grunt work); Audit +
  Verifier + Commit + Propagation-Check = no `--model` (profile default
  `{{HIGH_MODEL}}` - the judgment calls: catching the bug, ruling
  infeasibility, the one card with push). Both ids live on the model gateway with the profile's key; the mid pin rides the same fallback as the default. Gateway key architecture + kanban model/provider pinning mechanics (source-verified 2026-09-03; Option-B EXECUTED 2026-09-04 — dedicated profile `{{REVIEW_KEY}}`, key-identity-verified; safe-rotation order): `references/litellm-keys-and-kanban-pinning-2026-09.md`.
- Loop caps (≤2 Author↔Audit, ≤2 Scripter↔Verifier, one-shot contract-infeasible
  back to Author, ≤3 STE100→Author via `[STE100 round N/3]` — separate counter) are
  encoded in the retry card's title + body. Cap exhausted → create NO successor;
  `block --kind needs_input` + findings-table comment = PARK (first-class state;
  the owner unblocks with direction). **The carriers differ per loop** (2026-09-03,
  the owner asked): Author↔Audit and Scripter↔Verifier are self-contained (each
  increment re-reads the parent's title in "Parent task results" / its own title);
  STE100 rides a PAYLOAD field (`ste100_round`, copied from the Author's title
  into the Audit payload). The cap-PARK branch (a FAIL on the LAST allowed round
  -> block + findings table, NO successor) needs a red-team seed before it is
  trusted: full runs that PASS on the last allowed round do not prove the PARK
  path fires. Do not trust an unproven branch. (DISTINCT branch: the commit
  card's pre-flight PARK — dirty-tree block, see the concurrent-workstream
  bullet below.) Also: per-card "params" (runtime cap,
  model) are CREATOR-SIDE values — the kickoff command and the parent's
  `kanban_create` args consume them; the card's WORKER never sees them. The
  canonical body is the worker's source of truth for the successor's exact args.
- **A resident orchestrator card is the wrong shape** (the owner proposed it; rejected
  on substrate grounds): kanban workers are one-shot, so a permanently-running
  orchestrator holds a claim forever and is an SPOF. Orchestration decomposes:
  kickoff (in-chat pre-flight + card 1), routing (per-card branch table embedded
  in each decision card's handoff step), watchdog (weekly cron — stale pipelines,
  `todo` >24h, parked cards; a cron ping, not a task).
- Audit's PASS branch has a shortcut: a script-less skill goes straight to Commit
  (no no-op Scripter round-trip).
- **STE100 GATING scope = this round's work product ONLY.** GATING
  covers the prose this round's Author wrote; a pre-pipeline skill's
  inherited prose debt (long sentences, semicolon lines) is recorded
  ADVISORY for the owner, never gated — so a wording pass cannot balloon
  into a whole-file rewrite, and a full-file conformance pass, if the
  owner wants it, is a separate run. The per-round GATING window is NOT
  exhaustive over the whole file — the second round of the bounded loop
  is the net, not redundancy. FAIL rows carry BEFORE/AFTER rewrites
  pre-written, so the retry Author is mechanical.
- **Kickoff check for family-hub targets** (skills whose PREFER clauses
  and cross-references mesh with siblings — the investment skills are one
  such mesh): add a named cross-reference consistency check to the Author
  card's PIPELINE INPUT — verify every outbound routing sentence against
  the referenced skill's ACTUAL description (read, not grep); the Audit
  re-verifies by independently reading the sibling descriptions.
- **Commit card** (`commit-canonical.md`):
  manifest-anchored seam check (every file `file | scratch path | sha256
  anchor`), pre-flight FIRST (`git status --short` EMPTY, `main...origin/main`
  = `0 0`, branch `main`), stage by explicit list only, pre-push hook as
  independent witness (record verbatim), undo-on-failure PARK branch (restore
  worktree to pre-run state, comment findings table, `kanban_block
  needs_input`, NO successor). Red-team recipe (both executed): seed
  `.kanban-scratch/` with the verified deliverable, then (A) plant a STALE
  anchor in the manifest -> must PARK at the seam step, no commit/push,
  git clean; (B) plant an untracked probe file at repo root with CORRECT
  anchors -> must PARK at pre-flight 1b BEFORE any copy, and must NOT
  delete the probe (the card says never clean a pre-existing conflict).
- **Propagation-Check** (report-only census): the standing decision is NO
  update reconcile / NO edits — the canonical carries an explicit "do not
  edit any skill file at all, including the one you load to do this job"
  line, because a fleet-check worker edited the very skill it had loaded
  mid-run (accurate content, not owner-directed). Standing rule: if a
  worker learns something durable it records it in its completion summary
  + a `kanban_comment`; the OWNER maintains the skills. Census trap: the
  headline count and the table must agree — when they disagree, the table
  + a fresh independent re-parse win.

- **Commit pre-flight PARK from a concurrent workstream (live-proven 2026-09-04,
  stock run).** The house repo is shared: a concurrent rx-review workstream had
  an in-flight edit (rx-review/SKILL.md) while the stock pipeline ran, and the
  Commit card correctly PARKed at pre-flight 1b — findings table, zero
  footprint, did NOT clean or commit around the dirt (per canonical). RED-B had
  planted the probe; this was a legitimate concurrent edit — same branch, real
  trigger. Proven response protocol: (1) let the other workstream commit its
  own work — the owner never discards or completes another workstream's
  in-flight edit; (2) re-verify the run's anchors AFTER the external commit
  lands (valid if the external commit doesn't touch the target skill — verify,
  don't assume); (3) if the owner has their OWN unpushed commits (e.g. a
  mid-run doctrine change), PUSH them before firing the re-run — pre-flight 1c
  (`main...origin/main` = `0 0`) fails on an unpushed local commit; (4) re-run
  as a NEW card mirroring the manifest + a "re-run context" note in the body
  (what blocked, what resolved, what is still at risk); (5) annotate the old
  (5) annotate the old blocked card SUPERSEDED via comment — never delete (the record stays). (6) WAITING without ping-pong (2026-09-04 stock run, re-run 2): arm a background watcher (notify=true) polling `git status --short | grep <the-specific-dirty-path>` every ~2 min, with an in-script timeout (3h) and an exit on the path going clean — watch the PATH, not the whole tree and not the card. When it fires, re-run the FULL pre-flight battery before spawning, not just the one file: empty `git status --short`, `git fetch` + `main...origin/main` = `0 0`, `git log -N -- <target-skill>/` proving the external commit did not touch the skill, and draft + live anchor sha256s still equal the manifest (the external commit moved HEAD under you — verify, don't assume). Successive re-runs: number them in the card title ('re-run 2; tree now clean') and give each a FRESH RE-RUN CONTEXT snapshot (timestamp + all four pre-flight facts) plus an explicit 'if pre-flight 1b fails on NEW dirt, PARK per canonical' line, so the worker inherits verified state, not a stale note.

## Operating the pipeline cards (proven 2026-08-31, first live run)

- **Kickoff routing (correction, the owner, 2026-09-03):** a request to "run the
  skill kanban on <skill>" — including "review the skill for quality" — means:
  put the user's instructions VERBATIM as PIPELINE INPUT into an **Author
  (create/update) card** and let the regular pipeline follow (Audit → STE100 →
  Commit → Fleet-Check). It does NOT mean a standalone report-only review card —
  that shape was explicitly rejected (the STOP of 2026-09-03): the deliverable
  is the updated skill through the full graph, not a review document. Resolve
  fuzzy target names against `git ls-files` first and state the mapping in the
  card's scope note ("pre-ipo-tracker" → `pre-ipo-investment-analysis`).
- **Card recipe** (Author, verified): `hermes kanban --board skills create
  "<title>" --body "$(cat <card-body.md>)" --assignee {{ASSIGNEE}} --workspace
  dir:{{REPO_DIR}} --skill hermes-skills-repo --max-runtime 30m
  --created-by {{CREATED_BY}} --json`. `--assignee {{ASSIGNEE}}` is the Author role
  profile (one profile per pipeline role — author/audit/ste100/
  scripter/verifier/commit/fleet; each has its own long-term-memory
  bank so no role can read another's memory — see the Role Isolation
  section of the card spec). All role profiles share the dedicated
  `{{REVIEW_KEY}}` gateway key (their `.env` defines `LITELLM_API_KEY` with
  it), so attribution goes to `{{REVIEW_KEY}}`, not the chat profile; the
  key is verified BY IDENTITY per the recipe in
  `references/litellm-keys-and-kanban-pinning-2026-09.md`, never by a plain
  KEYOK); `--created-by` stays `{{CREATED_BY}}` (who kicked off). The `--json` echo confirms
  `workspace_kind: dir`, resolved `workspace_path`, and `skills` before
  anything else. (Boards: `hermes kanban boards create skills` — `boards add`
  does not exist.)
  - **Role isolation (2026-09-04) — one profile + one long-term-memory bank + one
  `MEMORY.md` per role, and a SINGLE `{{ASSIGNEE}}` token.** The backchannel
  risk: if roles share a bank or `MEMORY.md`, an adversarial role
  (Audit/Verifier/STE100) can recall what the role it is checking reasoned
  — yes-men. Sever BOTH channels (bank AND file memory) by giving each role
  its own profile + dedicated bank: build = `hermes profile create` +
  `setup-memory-bank.sh` + a model tier + the symlinked `hermes-skills-repo`
  skill (the ste100 role also gets `asd-ste100`). All role profiles share the
  dedicated `{{REVIEW_KEY}}` key (attribution off the chat key; NOT per-role
  keys). Tiers: Author/Scripter/STE100 = mid (`{{MID_MODEL}}`);
  Audit/Verifier/Commit/Fleet = high (profile default) — STE100 was moved
  high→mid on the owner's call. the owner's standing call (2026-09-04): ONE
  `{{ASSIGNEE}}` token, NEVER seven per-role tokens — the per-role split is
  the token's VALUE, a `role=profile` map (single-runner install = one
  profile name; role-isolated = `author=…, audit=…, …`), so the card
  templates gain no new tokens. Verify a role before trusting it: bank
  round-trip (a retained marked fact lands in ITS bank, other banks' counts
  flat) + the key identity poison test (see the gateway-key ref) + one GATE card on
  a scratch board that spawns as `-p <role>` and writes to its own bank. Production confirmation the same day: the workers actively USE their own bank — STE100 wrote a durable writing-class observation to ITS bank as its final action, so the per-role bank is live and consulted, not merely configured.
  - **Monitoring**: the gateway dispatcher claims `ready` cards automatically;
  poll with short foreground commands (`for i in 1..4; do sleep 90; hermes
  kanban --board skills list | grep <id>; done`). Keep each polling command
  under ~4 min — a longer loop hits the terminal tool's 420s timeout (the loop
  is lost, the card keeps running). `list` shows glyphs: `●` = running,
  `✓` = done. `kanban log <id> | tail` shows live worker reasoning when a
  card runs past the expected half of its cap. An Author card completed in
  22m of a 30m cap. For cards expected to run long (STE100 dense-prose
  scans ~12m, retry Authors ~21m), the pattern proven across the
  2026-09-04 relaunch is a BACKGROUND watcher: a short script that
  loops `hermes kanban --board skills list | grep <id>` every ~60s
  (grep by id, NEVER `tail` — a long chain falls off a tail window),
  exits on a terminal status printing the board tail + `show | tail`
  summary, launched with `terminal background=true, notify=true` —
  the notify fires once with the terminal state and the card keeps
  running the whole time; a 40–55m in-script timeout is the backstop.
  Verify the watcher script ITSELF before arming (three failure modes
  observed 2026-09-04, stock run): (a) a mid-write truncation arrived
  as a 5-line fragment that exited instantly and lost the completion
  notify with no visible failure — check `bash -n` + line count + tail
  before launch; (b) the card ID was copied from a stale snapshot
  instead of the latest `list` — a wrong ID loops against nothing
  (or matches the wrong card) for the whole run; (c) killing the
  watcher: `pkill -f <script>` also matches its OWN command line and
  SIGTERMs the calling shell command (observed: exit -15, output
  lost) — use `ps aux | grep '[<pattern-first-char>]…' | awk
  '{print $2}' | xargs -r kill` instead (the bracket stops the grep
  self-match).
- **Where the completion payload lives**: the `show --json` keys are
  `task / runs / events / comments / children / parents / latest_summary` —
  the completion summary is in `runs[0].summary` (and `latest_summary`); the
  task's `result`/`summary` fields can be `None`.
- **Worker sandbox**: the security scanner BLOCKS `rm -rf` and bulk `rm` for
  kanban workers (single-query mode) and flags heredocs. Card bodies that
  stage a temp dir must say **"move the scratch to /tmp, never rm"** — the
  worker self-corrects to `mv <stage> /tmp/` and the repo stays clean. (My
  own terminal as the curator profile can still `mv` cleanup.)
- **R6 verification of a card's summary** (do this before reporting the card
  as passed — the summary is a self-report): (1) output artifact exists at
  the claimed path; (2) worktree clean: `git status --short` empty +
  `git rev-list --left-right --count main...origin/main` = `0 0`; (3) the
  skill dir is untouched (`git diff --stat HEAD -- <skill>/` empty); (4)
  scratch path is git-excluded (`git check-ignore -v <scratch>`); (5)
  frontmatter block byte-identical for a no-trigger-change edit; (6) trigger
  count re-done independently on both files; (7) baseline lint re-run; (8)
  staged-draft lint reproduced independently (below). After the card has
  served its test purpose: `hermes kanban --board skills archive <id>`.
- **Staged-draft lint recipe**: `mkdir tmp-lint-<skill>/{,scripts}`; copy
  draft SKILL.md + the skill's README.md + `git ls-files '<skill>/scripts/*.py'`
  into it; `python3 tools/lint_skills.py --skill tmp-lint-<skill>`; then
  `mv tmp-lint-<skill> /tmp/`. Expected artifact: exactly ONE critical,
  `frontmatter/name` ("name 'calendar' does not match the folder") — the
  linter compares the frontmatter `name:` to the STAGING folder name
  (`lint_skills.py` `frontmatter/name` rule); the draft must keep its real
  name, so that single critical is the known staging artifact, and zero
  other findings is the pass line. Without the README staged, an extra
  `layout/readme` major appears.
- **Staged-draft lint, UPDATE mode (name == live folder) - in-place swap, not a
  temp folder (verified 2026-08-31, step 2).** The linter only lints repo-ROOT
  skill dirs (it scans `os.listdir(ROOT)`) and `--skill <name>` no-ops silently
  on a non-skill dir - so a create-mode temp folder works, but an UPDATE-mode
  draft (whose `name:` equals the live skill folder) can only be linted by
  swapping it into place: `cp <live> /tmp/<skill>-SKILL.md.backup-<ts>` (sha the
  backup) - `cp <draft> <live>` - `python3 tools/lint_skills.py --skill <skill>
  --json` - `cp <backup> <live>` (re-sha the restored file and confirm it equals
  the live-anchor recorded BEFORE staging). The pass line is "zero NEW findings
  vs the baseline lint run on the untouched live file" - not "zero findings"
  (a compliant live skill is the baseline). This is what the Audit card's
  update-mode staging does.
  **Dot-dir trap (step 3):** `.kanban-scratch/` is a dot-prefixed dir, and the
  linter's root scan skips dot-dirs (`os.listdir(ROOT)` filter,
  `lint_skills.py:792`) - so `--skill <name>` on a scratch skill silently
  returns "0 skills / 0 findings". A Verifier linting a scratch skill must
  stage the WHOLE skill (SKILL.md + README + scripts/) at the repo root
  (temp folder), lint, then `mv` out; the known artifact is exactly ONE
  critical `frontmatter/name` (staging folder != frontmatter name). Zero
  findings from a dot-dir `--skill` run means "nothing linted", not clean.
- **Red-team FAIL-loop test (how step 2 proved the retry path fires).** To
  verify the loop without a real defect, author a throwaway skill (e.g.
  `calredteam`) that deliberately violates N rules, lint it standalone to fix
  the GROUND TRUTH (exactly which N fire, true vs FP) BEFORE feeding it to the
  pipeline, then run Author - Audit and watch: Audit must FAIL with the right
  findings, spawn the round-1 retry Author, and the round-1 Audit must PASS
  with zero findings. The retry Author ran on the mid tier and recovered - the
  tier swap did not break the loop. Keep the fixture in `.kanban-scratch/`
  (git-excluded); the throwaway skill is never committed.
- **Durable task id is the DB, not a `list`/poll snapshot (step-2
  observation).** During a handoff (worker creates a successor and completes),
  a `list` snapshot can show a short-lived claim id for a card whose durable id
  differs; `show <that-id>` then returns "unknown task". Do not chase it -
  query `{{HERMES_HOME}}/kanban/boards/<board>/kanban.db` `tasks` (id, title, status,
  model_override) + `task_runs` + `task_links` for the canonical id. The DB is
  the witness. For the WORKER's own record (prove what a role did, in what
  order — e.g. verify the complete→create→show handoff order, or whether the
  role consulted its long-term-memory bank): `python3 ${HERMES_SKILL_DIR}/scripts/trace-handoff.py <profile>` —
  prints the last session's kanban_* calls + create results in order,
  plus a tally of every other tool, from
  `{{HERMES_HOME}}/profiles/<profile>/state.db` (read-only, no sqlite3 CLI
  needed). Note the shape: the `messages` table has `tool_calls` (JSON
  args) and `tool_name` — there is NO `tool_args` column (that guess
  cost two query rewrites on 2026-09-04).

## Error handling

A house-repo step that errors stops the work. That covers a lint critical,
a failing pre-push hook, a parked pipeline card, and a linter/CI
disagreement. Capture the exact failure — verbatim output, finding list,
exit code — and surface it. Never bypass a gate without the owner's
direction — no allow-list entries, no severity edits, no skipped hooks.
See the standing rules in "Interpreting lint findings" and "Changing a lint rule".

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Pitfalls

1. **Trigger-list regression — the #1 self-inflicted failure.** Rewriting the
   description is how triggers get lost. Since the 2026-08-27 fixes the linter
   catches *lost* triggers via `routing/triggers-baseline` (a committed
   `tools/trigger_baseline.json`; only removals fire) — but that only fires on a
   re-lint *after* the edit. First line of defense is still manual: count the
   quoted phrases in the "Activate on any of" list programmatically before AND
   after any description edit, and after an *intentional* removal regenerate the
   baseline with `python3 tools/lint_skills.py --update-triggers` (commit that
   diff — it IS the review point). Any delta must be intentional and reported.
   (The baseline is a committed artifact, NOT a git-HEAD diff: a CI checkout's
   worktree equals HEAD, so a HEAD diff is always empty there.)
2. **Case decoys** — lowercase `conventions.md`/`readme.md` are not the docs
   (see rubric).
3. **Inherited uncommitted state** — check `git status` before the first edit.
4. **Linter false positives** — confirm against source/live behavior, report as
   such with evidence; never "fix" the script to satisfy a broken regex.
5. **Cross-repo rule bleed** — the hermes-agent in-repo standard (≤60-char
   description, author credit, platforms gating, docs generator) contradicts
   this repo's CONVENTIONS.md. One session per repo; don't carry the checklist
   over. Note: even the profile's own skill_manage create guard enforces the
   60-char description budget, so house-repo-style long descriptions live only
   in the repo, never in profile skills.
6. **googleapiclient's HttpError is NOT `urllib.error.HTTPError`.** A verb
   that drives `googleapiclient` (docs.py's Drive calls) raises
   `googleapiclient.errors.HttpError` — catching `urllib.error.HTTPError`
   never fires and the raw repr leaks into the JSON error. Catch
   `googleapiclient.errors.HttpError`, read `e.status_code` (NOT `e.code` —
   that attribute doesn't exist in the installed client) and `e.content`
   (bytes). Found live: a `rename` 404 test returned the raw `<HttpError ...>`
   string until fixed.
7. **Pyright "not a known attribute of None" after `fail()`.** `fail()` ends
   in `sys.exit`, which Pyright doesn't model — annotate `fail()` and `out()`
   `-> NoReturn` (import from `typing`) and the whole fail-then-continue
   pattern type-checks.
8. **`--skill <name>` on a non-skill directory silently no-ops.** Repo mode only
   lints directories containing a `SKILL.md` (a correct design — non-skill
   install kits like `archivist/` are out of scope), so `--skill archivist`
   reports "1 skills, 0 findings" and `archivist/scripts/rank.py` has never been
   mechanically checked. "0 findings" from a `--skill` run means "clean OR not
   a skill" — check the directory actually contains a `SKILL.md` before
   treating the result as a verdict. (Same root cause: `--skill` with no
   matching `SKILL.md` should error; trivial CLI gap, unfixed as of
   2026-08-27.)
9. **Untracked `.venv/` under `scripts/` — walk git, never disk.**
   `pallo-logistics/scripts/` and `square-appointments/scripts/` each carry a
   ~1,400-file `.venv` on disk (gitignored via per-dir `.gitignore`). Any
   mechanism that walks `scripts/` — an entry-point declaration scan, an
   orphan/reachability check, a future smoke runner — MUST iterate
   `git ls-files` output, never the disk: a disk walk invents ~2,800 phantom
   "scripts", and naive mention-scans count pip's RECORD files as references
   to hundreds of basenames (skill-lint's transitive method reaches 1,370/1,392
   of pallo's files via RECORD files alone).
10. **Backtick pairing across lines silently drops code spans.** When scanning a
    SKILL.md for code references, pair inline-backtick spans **per line**, never
    with one whole-doc `re.finditer(r"`[^`]+`")`: a single stray/unbalanced backtick
    on any earlier line throws off parity and makes every later span invisible.
    Verified 2026-08-28 — square-appointments L84's `` `customer-info.py show` `` was
    dropped by a whole-doc scan (file was tracked AND documented, yet reclassified
    non-entry), fixed by per-line pairing. Any future SKILL.md scanner (entry-point
    derivation, orphan check, link checker) inherits this trap.
11. **Suppression filters hide true positives — audit every suppression, not the
    findings.** The 2a negation filter's first draft (raw 40-char window) fixed the 2
    intended FPs but also silenced 3 genuine uses whose prohibition sat in an
    *adjacent clause* ("No sign-in needed. | `python3`...", "do not rewrite it from
    memory. — `web_search`..."). Two rules made the fix safe: (a) scope the filter to
    the *clause* — a negation governs a mention only if no sentence/clause break
    (`. ! ? , ; | )`, newline) sits between cue and token; (b) run a **suppression
    audit** that imports the linter's actual function (`from lint_skills import
    _in_negation`) — never a hand-rolled copy of the logic (the first audit had its own
    copy and reported clean while the linter was still wrong) — over EVERY pattern
    match in the repo, verifying each suppression is a true FP. The audit must root at
    the absolute repo path: a relative `glob.glob('/*/SKILL.md')` from the working
 shell returned 0 silently, which reads as "nothing suppressed".
 12. **A failed compound git command can silently revert uncommitted work — and a
 captured diff is the recovery source.** When a multi-step command (heredoc +
 `git apply` + …) fails partway, a `git checkout -- <file>` inside it may still
 have run, wiping uncommitted edits — and the failure mode is a *corrupt partial
 patch* on the next apply, not an obvious error. Two defenses: (a) before ANY
 destructive git op on uncommitted work, capture the live state
 (`git diff > /tmp/state-<ts>.patch`, and `--stat` it to confirm the hunk count
 matches what you expect is dirty); `git apply` that file back and the work is
 byte-recoverable — this is the stash-baseline step of the review workflow
 generalized to *accident* recovery, not just before/after measurement.
 (b) When one file carries TWO logical changesets and you must commit them
 separately, prefer `git add -p` or re-applying each hunk with the `patch`
 tool on the live file — hand-constructing partial `.patch` files by slicing a
 captured diff on `@@` boundaries is fragile (a slice cut between context lines
 yields a "corrupt patch" on apply and you end up re-deriving the state by
 other means). If you must slice, validate with `git apply --check` BEFORE
 touching the worktree, and never chain the apply with a checkout in the same
 command.
13. **`hermes kanban block <id> --kind X "reason..."` mis-parses long reasons.**
 A multi-clause reason with special chars after `--kind` dumps top-level hermes
 usage and blocks nothing (observed 2026-09-01: the FIRST plain-form `block
 <id> "reason"` had already succeeded, masking that the `--kind` re-block
 never landed). Use the plain form `hermes kanban --board <b> block <id>
 "short reason"`; keep reasons short; verify the block with `list`/`show`.
14. **`git ls-files <dir> -- '*.ext'` returns the WHOLE repo, not the dir.**
 A pathspec given as a leading argument plus a GLOB after `--` selects
 files repo-wide whose basename matches the glob: `git ls-files
 calendar/scripts -- '*.py'` returned every tracked .py in the repo (a
 staging dir built from it filled up with web-access, square, pallo,
 whatsapp, and tools scripts). The correct scoped form is the single-quoted
 glob that carries the path: `git ls-files 'calendar/scripts/*.py'`. Same
 trap for any per-skill file enumeration — and per pitfall 9 it must be
 `git ls-files` (never a disk walk) anyway.
15. **The patch tool rejects "escape-drift" when quotes are backslash-escaped.**
 If old_string/new_string carry a literal backslash+quote but the file
 holds a plain quote, the patch is refused with an escape-drift error
 (fired twice during the 2026-09-03 board-doc edits). Pass quotes exactly
 as the file has them (plain), never hand-escaped — the refusal is a
 serialization artifact, not a content mismatch; re-issue with raw quotes.
16. **Hand-aligned ASCII diagrams fight the patch tool — draw them with a
 column script.** For precise graph edits (arrowheads, lane pipes, lane
 labels), the fuzzy matcher repeatedly fails on whitespace (1-space vs
 2-space indent, trailing spaces) and each retry risks a wrong hunk.
 Working method (2026-09-03 flow-diagram redraw): a small Python script
 that (a) locates the block by exact line anchors, (b) builds each line
 by placing cells at explicit column indices, (c) ASSERTS the critical
 characters (every arrowhead's column, every lane's pipe column, no label
 crossing a lane) BEFORE writing, (d) then re-reads the section from
 disk; cosmetic label spacing becomes plain patches afterwards. Verify
 the finished diagram against the spec's branch table, edge by edge.
17. **A timeout re-queues the card, and a re-run re-executes the whole
 work order (→ duplicate successor).** When a run hits
 `max_runtime_seconds`, the substrate marks it `timed_out` and puts the
 card BACK in the queue; the next spawn re-reads the work order from the
 top. Observed 2026-09-04 (STE100 card): run #37 timed out at
 1805s; run #38 re-executed. Under the complete-first order (2026-09-04,
 `7df63f2`) the duplication window is now narrow: a run completed its
 card AND created the successor, then timed out in the show-check tail —
 the re-run must not create a second. Fix (every successor-creating card,
 doctrine + instance): the HANDOFF carries a RE-QUEUE GUARD (defensive) —
 before creating, query the board for an existing successor whose
 `parents` list contains the card's own id and whose title matches its
 branch; if one exists, create NOTHING, verify its payload, and post the
 existing id as a `kanban_comment` citing it. Companion fix that day: a
 mid-tier prose-scan card (STE100, 398-line dense SKILL.md) was under its
 1800s cap — raise the cap (→2700s) rather than let it time out after
 doing the work.
18. **"Done head, no successor" can be a snapshot inside the complete→create window, not a stall.** Under the complete-first order the parent's `kanban_complete` and `kanban_create` are seconds apart within one turn, so a board `list` taken inside that window shows the parent `done` with no successor yet. Before declaring the pipeline stopped (the accepted price IS a worker death between complete and create), check the parent's session trace (`trace-handoff.py <profile>`) for a `kanban_create` AFTER `kanban_complete` that returned `ok: true` — if the create is present, the successor was born: re-list the board, it appears `ready`. Observed 2026-09-04: a STE100 FAIL run's board snapshot read "done, no retry card", but the trace showed complete→create (the successor, `ready`)→show→comment, and the card was `running` moments later — the "missing" card was the window, not a defect.
19. **A squash-merged branch reports "not fully merged" — verify containment, then `-D`.** The squash commit is a NEW sha, not an ancestor of the branch tip, so `git branch -d` refuses after `gh pr merge --squash --delete-branch` (which deletes the REMOTE branch only). Safe sequence (exercised 2026-09-09, PR #16): `git fetch origin`, then `git diff main <branch> --stat` — the result must show ONLY main-side commits (newer main work the branch predates; an empty diff means the tip is literally on main, the fast-forward case). Branch-side content adding nothing beyond main = nothing lost → `git branch -D <branch>`. Never `-D` without that containment check — it is the witness that the squash carried everything.
20. **A skill-dir rename landing on main is NOT done — the live instance still points at the old path.** `git mv` + squash changes the repo, not the host: dispatch reads `CARDS_DIR` from the profile instance env, which keeps the old path until flipped (exercised 2026-09-09, skill-kanban → skill-foundry). Post-merge sequence: (1) flip `CARDS_DIR` AND the in-file doc pointers in the instance env — the old name hides in comments too; (2) verify at the config+file+test layers: `ls` the new `CARDS_DIR` (all role playbooks + spec present), the new dir's `SKILL.md` frontmatter carries `name: <new>`, and the test battery passes from the shared checkout; (3) grep-sweep the profile for the old name EXCLUDING `sessions/`, `cache/`, `.curator_backups/` — archives legitimately keep old names, do not rewrite history; (4) the old path can linger as an untracked junk dir (a `__pycache__/` left where rename work was tested) — confirm `git ls-files <old-dir>` is EMPTY before removing it; (5) parked work-state docs (a RESUME.md carrying fleet census / run ids) are host-bound under the neutrality rule — archive them to the profile `future-work/`, never merge them into the repo. Verification honesty (the owner's rule): config+file+test layers are "verified"; a live dispatch card is the RUNTIME layer — if the board is clean and nothing is in flight, report "not verified at runtime" and offer the dry card instead of claiming the flip is proven.

## Verification (done = all of these)

- [ ] Lint: 0 critical, no findings NEW relative to the stash baseline
- [ ] Trigger count before/after identical or delta intentional and reported
- [ ] First ~65 description chars state the capability in domain words
- [ ] Every factual claim in SKILL.md verified against the script source
- [ ] No absolute paths in SKILL.md (`${HERMES_SKILL_DIR}` everywhere)
- [ ] New verb live-tested: happy path + error path; test artifacts trashed AND
      deletion verified (404 + empty sweep)
