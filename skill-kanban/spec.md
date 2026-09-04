# Skill Maintenance Kanban — Card Spec (host-neutral)

For a house skill repo with `CONVENTIONS.md` as the authoritative rubric and
`tools/lint_skills.py` as the mechanical gate. Instance values are
`{{placeholders}}` (see `PROFILE.example`); per-run values are
`__PIPELINE_INPUT__` / `__CHANGESSET_MANIFEST__` (substituted by the
creating card). Measured facts (profile counts, tap census, run timings) are
stated as "re-derive, do not trust" — the originating fleet's run history is
not part of this spec.

## 0. The graph

```
 (owner asks)
      |
      v
 +----------------------------+
 | 1. Skill.md Create/Update  |  (Author)
 +----------------------------+
      | pass                            ^
      v                                 | fail (issues): new Author card,
 +----------------------------+         | loop max 2 rounds
 | 2. Skill.md Audit          |---------+
 +----------------------------+
      | pass
      v
 +----------------------------+
 | 2b. STE100 Writing Audit   |
 +----------------------------+
      | pass
      | (script-less skill: jump straight to 5)
      v
 +----------------------------+
 | 3. Skill Scripter          |
 +----------------------------+
      | (always)
      v
 +----------------------------+
 | 4. Skill Script Verifier   |
 +----------------------------+
      | pass                    ^
      v                         | fail (script defects): new Scripter card,
 +----------------------------+ | loop max 2 rounds
 | 5. Skill.md Commit         |---+
 +----------------------------+
      | update: 6        | create: done (no installed copies exist)
      v
 +----------------------------+
 | 6. Fleet Update Check      |
 +----------------------------+

 Special edge: 4 --(contract infeasible, one-shot)--> 1
               (a second infeasibility on the same skill = PARK)
 Special edge: 2b --(gating finding, max 2)--> 1
               (title marker [STE100 round N/2] - a SEPARATE counter from
               loop 2's [round N/2]; the retry re-enters at 2 (Audit) for
               a fresh house check before the draft comes back to 2b)
```

The `board.md` flow diagram is a second copy of this graph. They drift
independently — add/remove a card and update BOTH, edge by edge.

Rules that bind every card:

- **R1 - Evidence or no verdict.** Every card ends in a verdict backed by
  output: command + result line, file:line, or JSON. "Looks fine" is not a
  verdict.
- **R2 - Bounded loops.** Author ↔ Audit: max 2 retries. Scripter ↔
  Verifier: max 2 retries. STE100 → Author: max 2 (separate counter).
  Verifier → Author (contract infeasible): one-shot. Any exhaustion →
  **PARK**: board stops, the owner gets a findings table (rule, file:line,
  evidence, true/FP classification) and decides. No third spin, no silent
  retry.
- **R3 - One pipeline in flight per repo.** The worktree is a shared
  resource. Before any card touches the repo: `git status --short` + `git
  rev-list --left-right --count main...origin/main`. If another pipeline
  (or the owner) left uncommitted work, capture it (`git diff >
  /tmp/state-<ts>.patch`) and build on it. Never clobber, never sweep it
  into the commit.
- **R4 - Explicit staging only.** `git add <files>` by list, never `-A`.
  After staging, `git status --short` must show exactly the approved
  changeset.
- **R5 - No destructive git ops on uncommitted work without a captured
  diff first** (recovery source).
- **R6 - A child's self-report is not a fact.** The card that receives
  another card's output re-verifies the seams it relies on (contract
  fields, file existence, lint output) before acting on them.
- **R7 - Never `--no-verify`, never `--force`, never force-push** without
  the owner's explicit per-instance OK.
- **R8 - Walk git, never disk.** Any scan of `scripts/` iterates
  `git ls-files`, not the filesystem (an untracked venv under `scripts/`
  invents thousands of phantom files).

## 1. Card: Skill.md Creation or Update (Author)

**Input.** The owner's request (create or update) — substituted into the
body's PIPELINE INPUT (mode, target skill, the request text, constraints).
For updates, additionally: path of the existing SKILL.md, and — if this
card is a loop-back from Audit or STE100 — the issues/change list to fix
(fix exactly those; no scope expansion; for STE100, keep the protected
surface byte-identical and echo the title marker verbatim in the summary).

**Work.**
1. Read `CONVENTIONS.md` (uppercase — the lowercase `conventions.md` and
   `readme.md` are decoys, not the docs) and `README.md`. This card does
   not work from memory of the rubric.
2. Survey peers: for a create, read 2-3 skills in the same domain and
   prefer **extending an existing skill over creating a sibling**. A
   near-duplicate request is a red flag to stop and ask the owner.
3. `git status --short` in `{{REPO_DIR}}` (R3). For updates, read the
   existing skill dir (SKILL.md + scripts + README).
4. Draft the SKILL.md to a scratch location in the worktree (never the
   live path, until Commit). House style: long-form `description` with a
   **PREFER** clause and an **"Activate on any of"** trigger list; the
   eight-section house order; scripts documented by the profile-path
   token, never machine-local absolute paths; no backend terms in prose
   (the leak scan strips code spans, not prose).
5. **Update only - trigger diff:** count the quoted trigger phrases in the
   description before and after (programmatically). Any delta must be
   intentional and carried in the card output.
6. Extract the **script contract** the SKILL.md declares: every verb, its
   flags, its output JSON shape, its error behavior. For updates, mark
   each verb `unchanged | changed | new` against the current scripts.

**Output (the card payload).** Proposed SKILL.md path + sha256; mode
(create/update); trigger diff (before count → after count, deltas named);
script contract table; list of files expected to change (SKILL.md, which
scripts, README verb list if verbs change); live anchor for updates.

**Verdict.** No self-approval — hand to **Skill.md Audit** with the full
payload.

## 2. Card: Skill.md Audit

**Input.** Author payload: proposed SKILL.md path + sha256, mode, trigger
diff, script contract. For updates: the diff vs the existing SKILL.md. For
a loop-back: the issue list.

**Work.**
1. **Baseline (updates only):** lint the repo at its current state
   (`python3 tools/lint_skills.py --skill <skill> --json >
   /tmp/lint_baseline_<skill>.json`), so "new finding" is measurable.
2. **Stage the proposed SKILL.md in place, lint, then UNDO the staging.**
   Update mode: back up the live file (verify the backup sha256 == the
   live anchor), swap the draft in, lint, restore, re-sha the restored
   file against the anchor. Create mode: temp dir at the repo root, lint,
   move the dir to /tmp (never delete — the worker sandbox blocks
   bulk `rm`). The linter only lints repo-ROOT skill dirs; a
   dot-prefixed scratch dir is a silent no-op for `--skill` (see
   `known-pitfalls.md`).
3. **Classify every finding true vs false-positive** against source. The
   linter has documented FP classes (premise-false entry points, the
   one-directional `ok` token test, `--skill` no-op on non-skill dirs).
   Cite file:line + the source fact for each classification. **Only true
   positives fail the card.** Never "fix" a script to satisfy a broken
   regex.
4. **Verify factual claims** in the SKILL.md. At this stage only claims
   about *existing* scripts are checkable — verify each (output shapes,
   flags, behavior) against current source. Claims about *new* scripts
   become "contract" entries: they are the spec the Scripter builds to,
   and the Verifier re-checks them against reality. The linter checks
   mechanics, not truth — this step is the truth check.
5. **CONVENTIONS.md compliance beyond the linter:** eight-section flow,
   PREFER clause intact, the verbatim error sentence ("Always ask the user
   for guidance when there is an error; do not proactively try to resolve
   errors yourself."), no "NEVER read" section, scripts invoked as
   `python3 <path>` never `./<path>`, profile-path token only, frontmatter
   name == folder name, routing window — first about 65 description chars
   state the capability in domain words, not "Used to ...".
6. **Trigger re-check (updates):** recount the quoted phrases yourself and
   compare with the Author's diff and the committed baseline
   (`tools/trigger_baseline.json`). A silent drop is a true positive even
   if the linter did not fire.
7. **Worktree witness:** after un-staging, `git status --short` is EMPTY
   and (update) the live file hash equals the live anchor.

**Verdict.**
- **PASS** → evidence table (rule | result | evidence), then hand to
  **STE100 Writing Audit** with the full payload (every approved draft
  passes the writing-standard audit before Scripter/Commit; the STE100
  card owns the script-less-vs-script routing and gets it from your
  script-contract note).
- **FAIL** → new **Skill.md Creation/Update** card: proposed text + issue
  list (each issue: rule, file:line, evidence, classification, required
  fix). Loop max 2 (R2); then PARK.

## 2b. Card: STE100 Writing Audit

**Input.** Audit's payload: draft path + sha256, mode, the script-contract
table (the spec the Scripter builds to; its marks also carry the script
note — has scripts / script-less — used for routing), trigger diff, the
Author's files-expected list, ste100_round (0 = first pass; on a loop-back
the Author's title carries `[STE100 round N/2]`), the parent card id (the
full Audit PASS evidence table lives in the parent's `runs[0].summary`).
Standalone variant: one SKILL.md path, no pipeline context (the card has
no parent).

**Work.**
1. Read the force-loaded `{{STD_SKILL}}` skill fresh (skill_view +
   `references/writing-rules.md`); do not work from memory of the
   standard.
2. Scope: STRICT on the description, the verb/flag documentation, and
   error strings; STE-flavored on explanatory prose. PROTECTED — never a
   finding: the PREFER clause, the eight-section order, the quoted
   trigger phrases, profile-path-token paths, code spans/fences. A
   house-format violation is the Audit card's finding, not this
   card's.
3. Find violations mechanically, not by feel; classify GATING (sentence
   over ~25 words, passive voice, phrasal verb, hedging, one word with
   more than one meaning, off-standard jargon/abbreviation, redundancy)
   vs ADVISORY (lexical choice — never fails the card). A finding
   without a concrete, paste-able proposed rewrite is a report, not an
   audit. Target <= 15 rows; group uniform fixes. Each rewrite must itself
   pass the standard AND keep the protected surface byte-identical.

**Output.** A concise, actionable change list — rule | quote | proposed
rewrite | GATING/ADVISORY — written so an Author card can execute it
verbatim, plus the protected-surface note.

**Verdict.**
- **PASS (zero GATING)** → successor BEFORE completing: skill has scripts
  → **Skill Scripter** with the full payload; script-less → **Skill.md
  Commit** (changeset = SKILL.md + state files only; render the manifest
  from the files-expected list).
- **FAIL (>= 1 GATING)** → retry **Skill.md Creation/Update** card with
  the change list, title marker `[STE100 round N/2]` (separate counter
  from loop 2). The retry re-enters at **2 Skill.md Audit** and the draft
  comes back to 2b. Loop max 2 (R2); then PARK with the change list as
  the findings table.
- **Standalone (no parent)** → create NO successor, ever: the change list
  is the deliverable, for the human to hand to a create/update card. Do
  NOT block.

## 3. Card: Skill Scripter

**Input.** Approved SKILL.md + script contract (verbs, flags, output
shapes, `unchanged/changed/new` marks) + current scripts (update).
Loop-back: Verifier findings + prior proposed scripts.

**Work.**
1. Implement the contract. House script dialect: stdout is **exactly one
   JSON object** per call; failures are `{"ok": false, "error": "..."}` +
   exit 1 (never 0, never 2, never a traceback on stdout); `@guard` on
   `main` so no failure path escapes without emitting the contract; every
   output path through the vendored JSON helper (`ok()`/`fail()`,
   `NoReturn`-annotated; vendor by BYTE COPY of the repo's
   `tools/skill_json.py`, never edit the copy); informational outcomes the
   agent relays are `ok: true` with the outcome in `status`.
2. Model-facing surface stays domain-leak-free: verbs, flags, JSON fields,
   error strings in user vocabulary. Backend terms allowed only inside
   code spans / inline code / URLs.
3. Touch ONLY the scripts the contract marks `changed`/`new` (create mode:
   all of them). Do not touch SKILL.md, README, or any other skill.
4. Produce the **test matrix** — the Verifier's work order. One row per
   verb per path: a throwaway fixture (exact path under a /tmp dir) with
   at least a boundary case, a mid-range case, an out-of-range case; the
   exact command line (environment inline); expected stdout at FIELD level
   and expected exit code. Include one error-path row per declared error
   behavior.
5. Sanity before handoff: `python3 -m py_compile` each script (the
   Verifier is the one who RUNS).
6. Pre-handoff witnesses: the repo's vendor check (the staged vendored
   copy is gitignored, so the repo-wide check must still pass) + `git
   status --short` EMPTY.

**Output.** Proposed scripts (path | sha256 | line count each) + test
matrix.

**Verdict.** No self-approval — **always** spawn **Skill Script
Verifier** with SKILL.md + scripts + test matrix. (The Scripter is never
its own verifier.) Loop-back cap 2 (R2) → PARK with the findings table.

## 4. Card: Skill Script Verifier

**Input.** SKILL.md, proposed scripts, test matrix; update: baseline lint
+ `git status` snapshot from Audit.

**Work.**
1. Seam check: sha256sum every file the payload names and compare with the
   anchors. A broken seam is FAIL (defect: "handoff seam broken") — do
   not run against an unidentified script.
2. Build the throwaway fixture set exactly per the test matrix (one-line
   commands; no multi-line heredocs — the scanner flags them).
3. **Run every row of the test matrix** — happy path *and* error path.
   For each: capture stdout, stderr, exit code; assert (a) stdout is
   EXACTLY one line and parses as ONE JSON object, (b) the `ok` field
   matches, (c) the exit code matches, (d) every contract-named field is
   present with the expected value (field-level, not "looks like a list").
   "Tests" on this board mean runs; reading code is not a test.
4. **Docs-to-code agreement:** re-verify every script claim in the draft
   SKILL.md (the Audit's "contract" entries are now checkable) against
   the OBSERVED runs. A claim the runs contradict is a finding.
5. **Lint:** the scratch skill dir is a dot-dir — stage the WHOLE skill
   (SKILL.md + README + scripts/) at the repo root, lint, then move it
   out (and verify the undo). Diff the staged finding set against the
   baseline; classify every NEW finding true vs false-positive against
   linter source (cite file:line + the source fact). New true findings
   fail the card.
6. **Clean up and VERIFY the cleanup:** move fixtures to the /tmp archive
   dir (never bulk delete); `git status --short` EMPTY; draft sha256
   unchanged.

**Verdict.**
- **PASS** → spawn **Skill.md Commit** with: SKILL.md, scripts,
  test-matrix results (verb | path | command | observed JSON | exit code),
  lint delta, changeset manifest (see Card 5).
- **FAIL (script defects)** → new **Skill Scripter** card: findings (verb,
  command, expected vs observed verbatim, file:line, required fix) + prior
  scripts. Loop max 2.
- **FAIL (contract infeasible)** — a declared output shape/behavior cannot
  be produced by any correct implementation: do **not** loop the Scripter
  (that spins on a broken contract). Spawn **Skill.md Creation/Update**
  with the infeasibility finding (what was declared, what the runs show,
  suggested contract fix). One-shot; a second infeasibility on the same
  skill PARKs.

## 5. Card: Skill.md Commit

**Input.** Approved SKILL.md + scripts, Verifier results, changeset
manifest. The handoff INTO this card is the canonical body VERBATIM with
`__PIPELINE_INPUT__` and `__CHANGESSET_MANIFEST__` substituted — and an
assert that no unsubstituted token remains (the body is the ONLY channel
the worker sees; see `known-pitfalls.md`).

**Changeset definition (what may be in this commit):**
- `SKILL.md` of the skill
- its `scripts/` (only contract-touched files)
- the skill `README.md` verb list **iff** verbs changed
- `tools/trigger_baseline.json` **iff** a trigger was intentionally removed
  (regenerated via `python3 tools/lint_skills.py --update-triggers` — the
  diff IS the review point)
- `TODO.md` / `CONVENTIONS.md` number syncs **iff** the changeset moves
  counts a doc states (every number written must be grep-verified against
  the real file first — a count written from memory is a false fact
  shipped into the tree)

**Work.**
1. **PRE-FLIGHT (repo state first — R3):** `git fetch origin`; `git
   status --short` EMPTY (any untracked or modified file is a pre-existing
   conflict — do NOT clean it, do NOT commit around it); `git rev-list
   --left-right --count main...origin/main` = "0 0"; `git branch
   --show-current` = main. Any failure: record verbatim, go to VERDICT
   (PARK).
2. **SEAM CHECK (R6):** sha256sum every manifest file at its scratch path
   and compare with the anchors. A file not in the manifest (e.g.
   pycache) must NOT be copied or staged. Any mismatch = PARK.
3. **COPY:** mkdir the skill dir at the repo root if absent; copy the
   manifest files (never bulk-delete anything); re-sha all copied files —
   byte-for-byte against the anchors; copied count == manifest count.
4. **PRE-COMMIT CHECKS:** the repo's vendor check (all vendored copies
   match); full-repo lint diffed per-rule against the baseline — ANY new
   critical = PARK; new major/minor ON THE SKILL classified true/FP (a
   true finding = PARK; an FP is cited); findings in OTHER skills are
   recorded and moved past (the changeset touched only this skill).
5. **STAGE by explicit file list** — never `-A`, never globs. Post-stage:
   `git diff --cached --name-status` shows EXACTLY the manifest, every row
   A or M. Anything else staged is a defect: unstage and re-check.
6. **COMMIT, house style** (`git log --oneline -5` is the reference):
   `<skill>: <imperative summary>`; body order: constraint/design → verb
   signature + output shape → SKILL.md/README changes → red-team/
   provenance note if a retry round. **Version rule:** bump `version`
   only when the trigger surface or verbs changed; a pure script change
   ships without a bump.
7. **PUSH:** `git push origin main`. The pre-push hook (lint, linter
   battery, vendor check, skill tests) is an **INDEPENDENT WITNESS**:
   record its verbatim output; its pass is part of your evidence, its fail
   blocks the push — you stop, nothing more. Never `--no-verify` (R7).
8. **VERIFY THE PUSH** (a push is not verified by the absence of an
   error): `git status -sb` shows `## main...origin/main` with no
   ahead/behind; `git rev-list --count origin/main..HEAD` = 0; record the
   commit sha.
9. **ARCHIVE THE SCRATCH:** move the scratch skill dir to a timestamped
   subdirectory under the /tmp archive dir (MOVE, never bulk delete);
   verify the scratch path is gone and git status is empty.
10. **SUCCESSOR:** create the Fleet-Update-Check card (before completing).

**Verdict.**
- **COMMITTED** (push verified `0 0`) → the Fleet-Update-Check card is the
  successor (body = that canonical VERBATIM, filled, report-only drift).
  For a CREATE the census is the baseline: record which profiles inherit
  via external_dirs and confirm no tap copies exist yet.
- **PARK** (any pre-flight, seam, lint, stage, or push failure): UNDO
  your footprint FIRST (repo-root skill dir moved back to /tmp so the
  worktree is EXACTLY as you found it — verified), then `kanban_comment`
  the findings table (step | command | verbatim output | why it violates
  the card | options for the owner), then `kanban_block
  kind="needs_input"` with a SHORT reason (the CLI mis-parses long
  reasons). NO successor. The owner unblocks with direction; a re-run
  comes as a NEW card.

## 6. Card: Fleet Update Check

**Why.** A push is not a deployment. The fleet consumes `{{REPO_DIR}}` two
different ways, and they do not update by the same mechanism:

- **Channel A — `skills.external_dirs`** includes the repo (globally and/or
  per profile). Live inheritance: a committed change is visible in those
  profiles' new sessions with **zero action and no drift surface** — as
  long as nobody edits the shared checkout.
- **Channel B — hub/tap installs.** Per-profile copies under
  `<profile>/skills/`, tracked in `<profile>/skills/.hub/lock.json`
  (`content_hash`, `identifier`, `install_path`). A profile-local copy
  **shadows** the Channel A copy of the same name, and it only moves via
  `hermes skills update` — which **skips** any copy whose on-disk hash
  drifted from the lock (local-edit protection). That skip is silent
  unless someone checks. (Consumer census: re-derive per run — never
  trust a stored count.)

**Input.** Skill name, commit SHA, mode. (Creates: census only — record
the tap/external_dirs state so the first update has a baseline.)

**Work.**
1. **REPO STATE:** in `{{REPO_DIR}}` confirm HEAD == origin/main and the
   input commit is on origin main; `git status --short` empty. Record the
   commit's name-status: the census must cover EXACTLY the skills it
   touched (a commit that touched skill X can only make a tap copy of X
   stale).
2. **FLEET CENSUS (re-census per run — do not trust memory or the last
   report):** `hermes profile list`; for EACH profile both channels —
   Channel A (does the profile's config, or the global config it inherits
   from, declare `external_dirs` including the repo?), Channel B (does the
   profile's lock hold a tap entry for the skill?), and the twin check
   (`find ~/.hermes/profiles -type d -name '<skill>'` outside any hub
   install path — a profile-local twin is FLAGGED, never auto-edited: it
   is another profile's curator-managed state, owner sign-off required).
   Per-profile table: profile | channel | state-before | action |
   evidence line. Channel A profiles: action = "none (live
   inheritance)", evidence = the sha.
3. **DRIFT REPORT (read-only):** for each Channel B consumer, `hermes
   skills check` (READ-ONLY: fetches the record source, compares hashes,
   writes nothing — verify from source if in doubt) and record the status
   (`up_to_date` / `update_available` / `unavailable` /
   `skipped-local-edits`). **STANDING DECISION: report-only** — no
   `hermes skills update`, no `--force`, no reconcile, no edits.
   `skipped-local-edits` (hash drift) is reported, never reconciled
   (reconciliation would rmtree the bot's local changes). `unavailable`
   is reported, never re-pointed (provenance pinning is a feature).
   Pre-existing drift on skills this commit did NOT touch: report
   separately as "pre-existing, not caused by this commit".
4. **FOR A CREATE:** the census baseline — which profiles will see the new
   skill (Channel A: live; Channel B: nothing, a brand-new skill has no
   tap copy anywhere).

**Acceptance (all of these, per R1):** every Channel B lock entry has a
recorded `check` status line; every Channel A consumer is listed (zero
actions, sha cited); every twin is listed with a flagged disposition; zero
`--force` / zero rmtree / zero `hermes skills update` / zero edits to any
profile's skill dir, config, or env.

**Verdict.** PASS (repo synced AND census/drift consistent) → complete,
NO successor (end of pipeline). PARK (repo out of sync, unreportable
drift, self-contradictory census) → comment the findings table + block,
no successor.

## 7. The kickoff skill (the one that starts the board)

Proposed name: `skill-kanban`. Responsibilities:
1. Parse the request → mode (create/update/extend) + target skill.
2. **Pre-flight:** peer survey (duplicate check — this is where
   redundancy gets prevented; Audit is too late for it); pipeline lock
   (is a board already in flight on the repo? then queue or decline, R3);
   read CONVENTIONS.md.
3. Spawn **Skill.md Creation/Update** with the structured brief (request,
   mode, existing SKILL.md path, constraints, prior findings).
4. **Never bypasses the board:** this skill does not commit, edit the
   repo, or lint for skill work itself. A request that is really "fix one
   line in an existing SKILL.md" still runs Card 1 → 2 → (Commit),
   because the description/trigger surface is exactly what the Audit
   protects.

## 8. Board-level loop budgets (recap, binding)

| Loop | Path | Cap | On exhaust |
|------|------|-----|------------|
| Author ↔ Audit | fix issues | 2 | PARK + findings table |
| STE100 → Author | fix writing findings (re-enters at Audit) | 2 | PARK + change list |
| Scripter ↔ Verifier | fix defects | 2 | PARK + findings table |
| Verifier → Author | contract infeasible | 1 per contract | second infeasibility = PARK |

PARK is a first-class state: the board stops, nothing is committed, the
owner gets the evidence and the decision options. A parked board is a
calibration meeting, not a failure.

## 9. Kanban substrate: how parentage is set

Board: a dedicated `skills` board (`hermes kanban boards create skills` —
there is no `boards add`). Every command is `hermes kanban --board skills
...`. The dispatcher runs in the gateway (no separate daemon). Verify the
substrate facts below against your install before relying on them.

### 9.1 What the substrate gives

- `create --parent <id>` (repeatable) / `link <parent> <child>` = **hard
  dependency gate**: the child is created `todo` and auto-promotes to
  `ready` only when **all** parents are `done` (an **archived** parent
  also counts as satisfied — `recompute_ready` treats done-or-archived;
  that means archive-as-cancel BYPASSES the gate, and the child's own
  body must carry the full handoff). Cycles and self-links are rejected
  by the DB layer.
- The parent's `complete --summary` (+ `--metadata` JSON) is **injected
  automatically into the child's worker context** as a "Parent task
  results" snapshot. Parentage and data handoff are ONE mechanism.
  `--summary` is the structured handoff channel; `--result` is the short
  outcome line.
- `create --workspace dir:{{REPO_DIR}}` → all workers share the real repo
  as workdir. No git worktrees for v1; the repo is a single shared
  worktree.
- `create --skill <name>` force-loads a skill into the worker (repeatable)
  — every card is created with `--skill {{HOUSE_SKILL}}`, and the STE100
  card additionally with `--skill {{STD_SKILL}}`, so the house rubric (and
  the writing standard, where needed) reaches each worker without pasting
  the rules into every body.
- `--max-runtime` per card class (author 30m, audit 30m, STE100 30m,
  scripter 45m, verifier 1h, commit 40m, fleet 30m); `--idempotency-key`
  dedups retried card creation.
- **Model per card class:** cards 1 (Author) and 3 (Scripter) are created
  with `--model {{MID_MODEL}}` (drafting and scripting are grunt work).
  All other cards keep the profile default (the judgment tier: Audit,
  Verifier, Commit, Fleet Check). Verify both model ids resolve on your
  proxy with the profile's key before the first run.

### 9.1a What a worker actually has

Workers do NOT drive the board through terminal/CLI. A dispatcher-spawned
worker is gated onto the `kanban_*` toolset (env `HERMES_KANBAN_TASK` set
and matching the task; `HERMES_KANBAN_BOARD` pinned, so a worker
physically cannot see other boards). Exact field names (from the
registered schemas):

- `kanban_create(title, assignee, body, parents, workspace_kind,
  workspace_path, skills, max_runtime_seconds, model, idempotency_key,
  initial_status)` — `parents` is a list of task ids; the child is `todo`
  until every parent is `done`. `body` is capped at 8 KB in the rendered
  worker context — keep bootstraps small; long specs live in files the
  worker reads.
- `kanban_complete(summary, metadata (dict), result, created_cards,
  artifacts)` — single-task at a time by design (bulk close is refused);
  summary/metadata are redacted before storage.
- `kanban_block(reason, kind)` — kind in `dependency | needs_input |
  capability | transient`; `needs_input` parks for a human (the PARK
  path).
- `kanban_comment(text)`, `kanban_show()`, `kanban_list()` are available;
  the worker's own task id comes from `HERMES_KANBAN_TASK` (default
  argument on every tool call).
- `kanban_show` worker context includes **`## Parent task results`**:
  each parent's most-recent completed run's summary + metadata verbatim.
- The kanban lifecycle skill is auto-injected into every worker; card
  bodies still state the house handoff protocol explicitly (the injected
  lifecycle is substrate-generic, not pipeline-specific).

So "handoff step" in the card bodies means TOOL calls, and the CLI flag
spellings in 9.3 are what the KICKOFF (in chat) uses when creating card 1
— workers mirror the same values as tool arguments (`workspace_kind=dir`,
`workspace_path={{REPO_DIR}}`, `skills=["{{HOUSE_SKILL}}"]`,
`max_runtime_seconds`, `model`).

House worker hygiene: the security scanner blocks `rm -rf` and
heredoc-heavy commands for workers; card bodies tell workers to clean
scratch by `mv`-ing it to /tmp, never by bulk delete.

### 9.2 The parentage rule (the whole discipline in one line)

**A pipeline card is never created without `parents`; every card creates
its successor before completing itself.**

Consequences and why:

- A card created with no parent is immediately `ready` and will run
  against nothing. That is the single biggest parentage bug on this
  substrate, and it is structural: the link must exist AT CREATION.
  Post-hoc `link` is the failure mode (create, forget to link, child runs
  early) — the handoff step's ordering makes that impossible.
- The substrate gives NO verdict channel: a child promotes on parent
  `done`, whatever the parent's summary says. So a card with a FAIL
  verdict must not complete-and-stop; it completes AFTER its successor
  exists. The handoff step is therefore ordered: (1) create successor
  with `parents=[<this-id>]`, (2) show-check the successor, (3) complete
  with the payload + evidence.
- Handoff payload lives in TWO places on purpose: the successor's `body`
  (the standing spec: what to do, round number, issue list) and the
  parent's `complete --summary` (the evidence snapshot the child
  re-verifies per R6).
- Neither `block` nor `archive` signals an already-spawned worker — the
  in-flight run continues its work order and its `kanban_complete` is
  later rejected ("already terminal"); the durable record for that run is
  a final handoff `kanban_comment`. A manual reclaim RE-QUEUES the task
  and the dispatcher re-spawns it — reclaim is not a stop signal.
- **The durable task id is the DB, not a `list` snapshot.** During a
  handoff a `list` snapshot can show a short-lived claim id whose durable
  id differs (`show` on it returns "unknown task"). Query the board DB
  (`tasks`, `task_runs`, `task_links`) for the canonical id.

### 9.3 Card → command map

KICKOFF column = what is run from chat (CLI). HANDOFF column = what the
worker does IN-CARD via `kanban_*` tools (arg names per 9.1a). Every
successor: assignee `{{ASSIGNEE}}`, workspace_kind dir, workspace_path
`{{REPO_DIR}}`, skills `["{{HOUSE_SKILL}}"]`, model per the card class,
max_runtime_seconds per the card class — except 2b STE100, which adds
`"{{STD_SKILL}}"` (the standard it audits).

| Card | kickoff create | handoff (on done) |
|------|----------------|-------------------|
| 1 Author | `create --body <author body> --assignee {{ASSIGNEE}} --workspace dir:{{REPO_DIR}} --skill {{HOUSE_SKILL}} --max-runtime 30m --model {{MID_MODEL}}` | PASS: kanban_create Audit `parents=[self]`; FAIL (max 2): kanban_create Author `parents=[self]` round N+1 + issues in body |
| 2 Audit | as above, profile-default model; same `--skill` flags as row 1 (30m) | PASS: kanban_create STE100 `parents=[self]` (STE100 owns the script-less→Commit routing); FAIL: loop per row 1; cap: kanban_block kind=needs_input |
| 2b STE100 | as above (30m), `--skill {{HOUSE_SKILL}} --skill {{STD_SKILL}}` | PASS: kanban_create Scripter (skill has scripts) or Commit (script-less) `parents=[self]`; FAIL (max 2): kanban_create Author `[STE100 round N/2]` + change list; standalone (no parent): complete with the change list, NO successor |
| 3 Scripter | as above (45m, `--model {{MID_MODEL}}`) | ALWAYS kanban_create Verifier `parents=[self]` with scripts + test matrix |
| 4 Verifier | (1h) | PASS: kanban_create Commit; FAIL-defect (max 2): kanban_create Scripter round+1; FAIL-contract: kanban_create Author with infeasibility finding (one-shot) |
| 5 Commit | (40m) | update: kanban_create Fleet Check; create: kanban_complete, pipeline done |
| 6 Fleet Check | (30m) | kanban_complete with per-profile table; drifts → report in summary, no `--force` (R7 + decision 8) |

### 9.4 Loop caps on the board (R2, encoded)

- Retry counter lives in the retry card's **body and title** (`[round
  2/2]`) — self-contained; each card knows its own round without reading
  history. The STE100 loop runs its OWN counter, `[STE100 round N/2]` in
  the retry Author's title + body, carried in the payload field
  `ste100_round` — a draft can spin in both loops; the counters are
  independent.
- Cap reached → create NO successor. Instead: `kanban_block`
  `kind="needs_input"` + `kanban_comment` the findings table + decision
  options. PARK is a first-class board state; the owner unblocks with
  direction.

### 9.5 The orchestrator question (resolved)

A **resident orchestrator card is the wrong shape for this substrate**:
kanban workers are one-shot — a permanently-running orchestrator task
would hold a claim indefinitely, burn turns polling, and be a single
point of failure that kills the pipeline when it dies. The orchestration
role decomposes into three substrate-native pieces:

1. **Kickoff (in chat):** the owner's request → pre-flight (duplicate
   check, pipeline lock: `kanban list` must show no in-flight skills
   pipeline, R3) → create card 1 with `--created-by {{ASSIGNEE}}`.
2. **Routing (in every decision card's handoff step):** the branch table
   (section 9.3) IS the orchestrator logic, embedded where the decision is
   made. No task sees the graph; each card sees its own decision table and
   the edges it can create.
3. **Watchdog (cron, the honest "orchestrator")** — OPTIONAL, and "designed"
   is not "created": a cron lists the `skills` board and flags any
   pipeline whose head card is `done` with no live child (silent stop —
   the one failure the substrate cannot prevent), any `todo` older than
   24h, and any `blocked/needs_input` awaiting the owner. A cron ping, not
   a task. Verify existence with `cronjob list` before writing "the
   watchdog runs" into any doc.

### 9.6 Verification of parentage (every handoff, R1)

After creating a successor: `kanban_show` the child — record its id, that
its parents list contains the creator's id, and its status (`todo`); after
completing: the child must be `ready`. The handoff step in every card body
ends with these show-checks and their output in the completion summary.

### 9.7 The 8 KB body cap and token discipline

The worker context builder caps the stored body at 8 KB at context-build
time (tail-truncated; the DB row keeps the full length, so the DB shows
the over-cap body even though the worker saw a cut one). Consequences:

- Keep every assembled body under 8 KB. Work steps sit at the head (safe);
  the tail (RULES IN FORCE / late handoff detail) is what gets cut.
- A token that appears in MORE THAN ONE place in a body is a latent
  double-substitution bug: replace-all hits both and the second one
  mangles live instructions. Assert on the FULL token after substitution
  (a bare `__` match is wrong — prose like `__pycache__` also contains
  `__`).
- If a successor's body is large or token-bearing, the parent instructs
  the worker to READ the sibling canonical from `{{CARDS_DIR}}` with its
  file tools instead of inlining it.
- Kickoff bodies are assembled the same way as worker bodies (template +
  substitute + assert no placeholder remains). A worker handed an
  unsubstituted placeholder recovers from the parent summary per R6, but
  the kickoff assembly is the one to fix.
