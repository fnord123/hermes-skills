# The `skills` board — skill-maintenance pipeline (operating reference)

Host-neutral doctrine; instance values are `{{placeholders}}` — see
`PROFILE.example` for the six tokens used in this file and in `cards/`.
The full card spec (inputs, work, verdicts, R1–R8, loop budgets, board
mechanics) lives in `spec.md`.

## 1. Goal of the board

A skill change lands in the house repo only after it has survived a
multi-card pipeline in which **no card is trusted on its own self-report**:

- **Author** drafts, **Audit** lints and fact-checks, **STE100** audits the
  writing standard, **Scripter** writes the scripts, **Verifier** runs them
  (reading code is not a test), **Commit** is the only card allowed to
  touch git, **Fleet-Update-Check** verifies the fleet sees the change.
- Every handoff re-verifies the seams (R6): the child re-shas every file the
  parent's payload names before acting on it. A broken seam is a FAIL, not
  a "probably fine."
- Failures **loop instead of vanishing**: a FAIL spawns a fix card with the
  findings in its body, and each loop has a hard cap (2 rounds). Exhausting
  a cap **PARKs** the board (blocked + findings table) — a first-class state
  a human unblocks with direction. No third spin, no silent retry.
- The repo is never left dirty: repo state is checked before any card
  touches it (R3), the Commit card stages by explicit list (R4), and every
  PARK first undoes its own footprint.
- The writing standard is enforced by a dedicated stage (STE100), not by
  asking the card that wrote the prose to grade it.

## 2. Overview of the flow

```
(owner asks)
      |
      v
+----------------------------+
| 1. Author (create/update) |
+----------------------------+
      | pass                            ^
      v                                 | fail (max 2): [round N/2]
+----------------------------+          |
| 2. Audit (lint + CONVE)    |----------+
+----------------------------+
      | pass
      v
+----------------------------+
| 2b. STE100 (writing)       |
+----------------------------+
      | pass                                                        |
      v                     (script-less skill: jump straight to 5) | fail (max 2): [STE100 round N/2]
+----------------------------+                                      | (retry re-enters at 2)
| 3. Scripter                |<-------------------------------------+
+----------------------------+
      | always
      v
+----------------------------+
| 4. Verifier (runs scripts) |
+----------------------------+
      | pass                    ^
      v                         | fail-defect (max 2): [round N/2]
+----------------------------+  |
| 5. Commit (git only here)  |--+
+----------------------------+
      | pass
      v
+----------------------------+
| 6. Fleet-Update-Check      |
+----------------------------+
      | done
```

Edges not drawn (one-shot, not a loop): Verifier → Author on
**contract infeasible** — the declared output shape cannot be produced by
any correct implementation. That renegotiation is one-shot; a second
infeasibility on the same skill PARKs.

**Graph invariants** (binding, verified against the live board):

- **Parentage rule:** no pipeline card is ever created without `parents`;
  every card creates its successor BEFORE completing itself. A card with
  no parent is immediately `ready` and runs against nothing — the
  structural parentage failure. (The kickoff's first card is the one
  exception: it has no parent by design and carries its input in its body.)
- **No verdict channel:** a child promotes on parent `done` regardless of
  what the parent's summary says. PASS and FAIL differ only in WHICH
  successor gets created — a FAIL still creates its (retry) successor and
  only then completes.
- **The successor's body is the only channel the successor sees.** A
  placeholder body that reads like a work order IS a work order (that is
  how a throwaway fixture once got committed and pushed). See
  `known-pitfalls.md`.
- **Payload lives in two places on purpose:** the successor's `--body`
  (standing work order) and the parent's `kanban_complete --summary`
  (evidence snapshot the child re-verifies).
- **Body cap:** the worker context builder caps the stored body at 8 KB
  (`_CTX_MAX_BODY_BYTES = 8*1024` in `kanban_db.py`, applied in
  `_build_worker_context` — tail-truncated at context-build time; the DB
  row keeps the full length, so the DB shows the over-cap body even though
  the worker saw a cut one). Keep assembled bodies under 8 KB; if a
  successor's body is large or token-bearing, the parent instructs the
  worker to READ the sibling canonical with its file tools instead of
  inlining it.
- **Workers hand off with `kanban_*` tools, not the CLI.** The worker's
  sandbox exposes `kanban_show/list/complete/block/comment/create/link/
  unblock` + `terminal` (for git/lint). The `hermes kanban ... create` CLI
  form is the **kickoff** command (card 1, created from chat); every later
  edge is a worker `kanban_create` with a `parents` list.

## 3. The cards

Per-card details: input, work (summary), verdict/handoff. Canonical bodies:
`cards/` (templated; the profile-local instance substitutes the tokens).
Params (runtime, model) are **creator-side values** — the kickoff command
and each parent's `kanban_create` args consume them. The card's WORKER
never sees them; the canonical body is the worker's source of truth for the
successor's exact args.

Model tier (per card class): Author + Scripter = `--model {{MID_MODEL}}`
(grunt work); all others = no `--model` (profile default, the judgment
tier). Every card: `--workspace dir:{{REPO_DIR}}` +
`--skill {{HOUSE_SKILL}}`; the STE100 card additionally
`--skill {{STD_SKILL}}` (the only card class that force-loads the
writing-standard skill).

### 3.1 Author — Skill.md Creation or Update
- **Job:** produce a proposed SKILL.md (scratch location, never in place) +
  the handoff payload. Never self-approves, never writes final scripts,
  never commits.
- **Work (summary):** read CONVENTIONS.md + README.md fresh (uppercase —
  lowercase decoys exist in some repos); survey peers (PREFER extending an
  existing skill over creating a sibling; a near-duplicate request is a
  stop-and-ask red flag); repo state (R3); draft to scratch; house style
  (PREFER clause, "Activate on any of" trigger list, eight-section order,
  profile-path token for scripts, no backend terms in prose); **trigger
  diff** on updates (count quoted phrases before/after, programmatically —
  the #1 self-inflicted failure is a silent trigger loss); **script
  contract table** (every verb: script, flags, output shape, error
  behavior, marked unchanged/changed/new).
- **Verdict/handoff:** completes with the full payload (draft path +
  sha256, mode, trigger diff, contract table, files-expected list) and
  creates **Audit** as its successor.
- **Loop role:** the retry Author (from Audit FAIL, `[round N/2]`) fixes
  exactly the listed issues, no scope expansion; the retry Author (from
  STE100 FAIL, `[STE100 round N/2]`) fixes the change list, keeps the
  protected surface (PREFER clause, eight-section order, quoted trigger
  phrases) byte-identical, and echoes its title marker verbatim in its
  summary (that is how the round counter propagates).
- **Params:** 30m (1800s), `{{MID_MODEL}}`. Body: `cards/author.md`.

### 3.2 Audit — Skill.md Audit
- **Job:** verify the proposed SKILL.md against the linter and
  CONVENTIONS.md. Never approves a draft it wrote; never writes scripts;
  never commits.
- **Work (summary):** repo state (R3); seam check (R6); baseline lint
  (update mode); **stage the draft in place, lint, UNDO the staging**
  (update mode: back up the live file — sha-verified — swap, lint,
  restore, re-sha; create mode: temp dir at repo root, lint, move to
  /tmp); classify EVERY finding true vs false-positive against linter
  source (cite file:line + the source fact; only true positives fail the
  card; never "fix" the draft to satisfy a broken regex); verify factual
  claims about EXISTING scripts against source (new-script claims are
  contract entries, not yet checkable); CONVENTIONS compliance beyond the
  linter (section flow, PREFER, verbatim error sentence, routing window);
  independent trigger re-check; worktree witness (git clean + live file
  hash restored).
- **Verdict/handoff:** PASS → creates **STE100** with the handoff payload
  (draft path + sha256, mode, trigger diff, script-contract table,
  files-expected list, `ste100_round`, the parent card id — the full PASS
  evidence table lives in `runs[0].summary`, read by card id) + a
  read-the-sibling-canonical work-order line. FAIL → retry Author
  `[round N/2]` with the issues list; cap reached → comment + PARK, no
  successor.
- **Params:** 30m (1800s), profile default. Body: `cards/audit-canonical.md`.

### 3.3 STE100 — Writing Standard Audit (slot 2b)
- **Job:** accept the (Audit-passed) SKILL.md and audit it against the
  ASD-STE100 controlled-language standard — force-loaded on this card
  (`--skill {{STD_SKILL}}`), never restated. Output: a concise, actionable
  **change list** (rule | exact quote | section+line | proposed rewrite |
  GATING/ADVISORY, target ≤ 15 rows, uniform fixes grouped) that a
  create/update card can execute verbatim. Every GATING finding carries a
  paste-able rewrite — a finding without one is a report, not an audit.
- **Scope:** STRICT on the description, verb/flag docs, and error strings;
  STE-flavored on explanatory prose. **PROTECTED — never a finding:** the
  house format (PREFER clause, section order, quoted triggers,
  profile-path tokens, code spans). A house-format violation is the Audit
  card's finding, not this card's.
- **Verdict/handoff:** PASS → **Scripter** (skill has scripts) or
  **Commit** (script-less — the changeset is SKILL.md + state files; the
  manifest is rendered from the files-expected list). FAIL → retry Author
  `[STE100 round N/2]` (separate counter; the retry re-enters at Audit for
  a fresh house check); cap → comment + PARK. **Standalone mode** (input
  says `standalone: true`, no parent): no successor, ever — the change
  list is the deliverable.
- **Params:** 30m (1800s), profile default, `--skill {{HOUSE_SKILL}}
  --skill {{STD_SKILL}}`. Body: `cards/ste100-audit-canonical.md`.

### 3.4 Scripter — Skill Scripter
- **Job:** turn the approved SKILL.md's script contract into working
  Python. Never runs the scripts to a verdict (that is the Verifier),
  never commits, never edits SKILL.md, touches only
  `<scratch>/<skill>/scripts/`.
- **Work (summary):** seam check (R6); read CONVENTIONS.md + the vendored
  JSON helper source fresh; implement the house dialect (stdout = exactly
  one JSON object per call; `{"ok": true,...}` exit 0 / `{"ok": false,
  "error": ...}` exit 1, never a traceback; `@guard` on main; vendor the
  helper as a byte copy, never edit the copy; model-facing surface
  domain-leak-free); build the **test matrix** (per verb, per path:
  throwaway fixture, exact command, field-level expected JSON, expected
  exit code, one error-path row per declared error); sanity =
  `py_compile` only; witnesses (vendor check, git status empty).
- **Verdict/handoff:** completes "SCRIPTS WRITTEN" + per-script
  (path|sha|lines) + test matrix, and creates **Verifier** with the full
  spec.
- **Params:** 45m (2700s), `{{MID_MODEL}}`. Body: `cards/scripter-canonical.md`.

### 3.5 Verifier — Skill Script Verifier
- **Job:** RUN the scripts the Scripter wrote and judge them against the
  contract. "Tests" = runs: reading code is not a test. Never fixes
  scripts (reports findings, Scripter fixes); never commits.
- **Work (summary):** seam check (R6); build the throwaway fixture set
  exactly per the test matrix; for EVERY row run the exact command,
  capture stdout/stderr/exit, and assert (one JSON object, `ok` field,
  exit code, field-level shape); **docs-to-code agreement** (every script
  claim in the draft re-verified against the observed runs — the
  linter cannot see this); lint the staged skill (dot-dir trap — see
  `known-pitfalls.md`; stage at repo root, lint, move out) and classify
  the delta; clean up and VERIFY the cleanup (move fixtures to /tmp;
  git status empty; draft hash unchanged).
- **Verdict/handoff:** PASS → **Commit** with the changeset manifest
  (file | scratch path | sha256) — the handoff is the Commit canonical
  VERBATIM with tokens substituted and an assert-no-unsubstituted-token
  check. FAIL (script defects) → retry Scripter `[round N+1/2]` with the
  findings table. FAIL (contract infeasible) → one-shot Author card with
  the infeasibility finding (second infeasibility = PARK).
- **Params:** 1h (3600s), profile default. Body: `cards/verifier-canonical.md`.

### 3.6 Commit — Skill.md Commit
- **Job:** the ONLY card allowed to touch git. Copy the VERIFIED scratch
  skill into the repo, stage EXACTLY the changeset, commit in house style,
  push, verify. Never re-verifies behavior, never fixes findings (a
  finding = PARK), never amends/force-pushes/`--no-verify`.
- **Work (summary):** pre-flight FIRST (git status EMPTY,
  `main...origin/main` = `0 0`, branch main); seam check (every manifest
  file re-sha'd; a file not in the manifest must not be copied); copy;
  pre-commit checks (vendor check + full-repo lint diffed against the
  baseline — any new critical = PARK; new major/minor on the skill
  classified true/FP); **stage by explicit file list, never `-A`**;
  commit house style (`<skill>: <imperative>`; version bump only when the
  trigger surface or verbs changed); push with the pre-push hook as
  **independent witness** (record verbatim; its fail blocks the push);
  **verify the push** (`git status -sb` no ahead/behind +
  `rev-list --count origin/main..HEAD` = 0 — a push is not verified by the
  absence of an error); archive the scratch (move to /tmp, verify).
- **Verdict/handoff:** COMMITTED → creates **Fleet-Update-Check** (body =
  that canonical VERBATIM, filled, report-only drift). PARK (any failure)
  → UNDO the footprint first (worktree back to pre-run state, verified),
  comment the findings table, `kanban_block kind="needs_input"` (short
  reason), NO successor.
- **Params:** 40m (2400s), profile default. Body: `cards/commit-canonical.md`.

### 3.7 Fleet-Update-Check — the deployment census
- **Job:** verify the fleet sees the pushed change correctly and report
  drift. **Report-only is the standing decision** — no `hermes skills
  update`, no `--force`, no reconcile, no edits to any profile's skill
  dir, config, or env, and no edits to any skill file at all — including
  the house skill this card loads (a fleet-check worker once spent its run
  self-editing its own skill; the card body's ROLE prohibition is the only
  control).
- **Work (summary):** repo state (HEAD == origin/main, the input commit
  exists on origin main); the commit's name-status bounds the census
  (a commit that touched skill X can only make a tap copy of X stale);
  **re-census per run — never trust memory or the last report**: profile
  list, then per profile both channels — Channel A (`external_dirs`
  includes the repo → live inheritance, zero drift surface, evidence =
  the sha) and Channel B (tap install in `skills/.hub/lock.json` →
  `hermes skills check` read-only status), plus profile-local twins
  (flagged, never auto-edited — another profile's curator-managed state);
  drift reported per status (`update_available`, `skipped-local-edits`,
  `unavailable`), pre-existing drift on untouched skills reported
  separately as "pre-existing."
- **Verdict/handoff:** PASS (repo synced AND census/drift consistent) →
  complete, NO successor (end of pipeline). PARK (out of sync,
  unreportable drift, self-contradictory census) → comment + block, no
  successor.
- **Census trap:** the headline count and the table must agree; when they
  disagree, the table + an independent re-parse win.
- **Params:** 30m (1800s), profile default. Body: `cards/fleet-check-canonical.md`.

## 4. Loops and caps

| Loop | Edge | Marker | Cap | On exhaust |
|------|------|--------|-----|------------|
| Author ↔ Audit | Audit FAIL → retry Author | `[round N/2]` | 2 | PARK (blocked + findings table) |
| STE100 → Author | STE100 FAIL → retry Author (re-enters at Audit) | `[STE100 round N/2]` | 2 | PARK (blocked + change list) |
| Scripter ↔ Verifier | Verifier FAIL (defects) → retry Scripter | `[round N/2]` on the successor's title | 2 | PARK (blocked + findings table) |
| Verifier → Author | contract infeasible (one-shot) | — | 1 per contract | second infeasibility = PARK |

The counters are independent — a draft can spin in both Author↔Audit and
STE100↔Author.

**Counter propagation — who sets N, who reads it:**

| Loop | Marker set by (at retry creation) | Current round read by |
|------|-----------------------------------|----------------------|
| Author ↔ Audit | the Audit: N = 1 + whatever the parent Author's title carried (1 if none) | the retry Author (its own title/body); the Audit's increment re-reads the parent's title in "Parent task results" |
| STE100 → Author | the Audit copies the Author's title marker into the payload field `ste100_round: N` (0 = first pass) | the STE100 card reads the payload field |
| Scripter ↔ Verifier | the parent writes it into the successor's title: Scripter → Verifier on the first pass (N = 1), Verifier → retry Scripter at N + 1 | the Verifier, from its OWN title; the retry Scripter gets N + 1 in title + body |

The carrier is self-contained per loop (title/body for the two
self-loops, a payload field for STE100) — no history reads.

**Verification-status note (do not drop):** in the originating fleet,
only the FIRST increment of the Scripter↔Verifier and Author↔Audit loops
is live-proven (both round-1 titles exist in the board DB from a red-team
chain). The round-2 increment and the cap PARK depend on the parent's
TITLE reaching the child's "Parent task results" context — step 2 of the
original rollout proved summary injection, not title injection. Until a
run proves the cap PARK, treat the cap as "designed, not proven."

## 5. Substrate facts (live-verified against the board engine)

These describe the kanban substrate the pipeline runs on; verify each
against your install before relying on it (the re-census keys in §8).

- **Dispatch:** a gateway dispatcher claims `ready` cards automatically;
  workers are one-shot. A **reclaim re-runs the card** (observed: reclaim
  re-queued and re-spawned within 3s) — reclaim is not a stop signal.
- **`create --parent <id>` is a hard dependency gate:** the child is born
  `todo` and auto-promotes only when ALL parents are `done`. A card with
  no parent is immediately `ready` and runs against nothing.
- **Archiving a task clears the parentage gate:** a child whose parent was
  `archived` (never `done`) promoted and claimed ~1s after creation.
  "No card runs against nothing" is bypassable by archive-as-cancel; the
  child's own body must carry the full handoff (it does, by design).
- **Neither `block` nor `archive` signals an already-spawned worker** —
  the in-flight run continues its work order and its `kanban_complete` is
  later rejected ("already terminal"); the durable record for that run is
  a final handoff `kanban_comment`.
- **The DB is the witness, not a `list` snapshot.** During a handoff a
  `list` snapshot can show a short-lived claim id whose durable id differs
  (`show` on it returns "unknown task"). Query
  `~/.hermes/kanban/boards/<board>/kanban.db` (tables `tasks`,
  `task_runs`, `task_links`) for the canonical id.
- **Body cap:** 8 KB stored-body cap at context-build time (see §2).
- **Worker sandbox:** the security scanner BLOCKS `rm -rf` / bulk `rm`
  for kanban workers and flags multi-line heredocs. Card bodies that touch
  scratch say "move to /tmp, never rm" and "one-line commands" — the
  worker self-corrects to `mv <stage> /tmp/`.
- **Completion payload location:** `show --json` keys are `task / runs /
  events / comments / children / parents / latest_summary`; the summary is
  in `runs[0].summary` (and `latest_summary`); the task's
  `result`/`summary` fields can be `None`.
- **`kanban block <id> --kind X "reason"` mis-parses long reasons** — a
  multi-clause reason after `--kind` dumps top-level usage and blocks
  nothing. Use the plain form `block <id> "short reason"` and verify the
  block with `list`/`show`.
- **Boards:** `hermes kanban boards create skills` — `boards add` does not
  exist.

## 6. Operating the pipeline from chat

**Kickoff (card 1, from chat — the only CLI-created card):**

```
hermes kanban --board skills create "<title>" \
  --body "<assembled Author body: canonical + PIPELINE INPUT substituted>" \
  --assignee {{ASSIGNEE}} --workspace dir:{{REPO_DIR}} \
  --skill {{HOUSE_SKILL}} --max-runtime 30m --created-by {{ASSIGNEE}} --json
```

Role-map install: `--assignee` resolves to the `author=` map entry, and
`--created-by` is the OPERATOR profile (the one running this command),
not a map entry. Single-runner install: both are the one profile.

The `--json` echo confirms `workspace_kind: dir`, the resolved
`workspace_path`, and `skills` before anything else.

**Standalone STE100 kickoff** (audit one SKILL.md off-pipeline; no
`--parent`, the card has no parent by design):

```
# assemble: canonical body, PIPELINE INPUT substituted with
# "standalone: true / skill: <name> / draft path: <path> / live anchor: n-a
#  / script-contract table: n-a / files-expected: n-a / ste100_round: 0 /
#  parent card id: n-a" — then ASSERT no un-substituted placeholder remains
hermes kanban --board skills create "STE100 audit: <skill> (standalone)" \
  --body "$BODY" --assignee {{ASSIGNEE}} --workspace dir:{{REPO_DIR}} \
  --skill {{HOUSE_SKILL}} --skill {{STD_SKILL}} --max-runtime 30m --created-by {{ASSIGNEE}} --json
```

**Monitoring:** poll with short foreground commands
(`for i in 1..4; do sleep 90; hermes kanban --board skills list | grep <id>; done`)
— keep each polling command under ~4 min (a longer loop hits the terminal
timeout; the loop is lost, the card keeps running). `list` glyphs: `●` =
running, `✓` = done. `kanban log <id> | tail` shows live worker reasoning
when a card runs past the expected half of its cap.

**R6 verification of a card's summary** (the summary is a self-report —
do this before reporting the card as passed): (1) output artifact exists
at the claimed path; (2) worktree clean (`git status --short` empty +
`rev-list` = `0 0`); (3) the skill dir untouched (`git diff --stat HEAD --
<skill>/` empty); (4) scratch path is git-excluded; (5) frontmatter block
byte-identical for a no-trigger-change edit; (6) trigger count re-done
independently on both files; (7) baseline lint re-run; (8) staged-draft
lint reproduced independently. After a card has served its test purpose:
`hermes kanban --board skills archive <id>`.

**Staged-draft lint recipes** (the linter only lints repo-ROOT skill dirs;
`--skill <name>` on a non-skill dir silently no-ops):
- create mode: temp folder at repo root (SKILL.md + README + scripts),
  lint, move to /tmp. Expected artifact: exactly ONE critical,
  `frontmatter/name` (staging folder ≠ frontmatter name) — zero other
  findings is the pass line; without the README staged, an extra
  `layout/readme` major appears.
- update mode (draft `name:` == live folder): in-place swap — back up the
  live file (sha it), swap the draft in, lint, restore, re-sha the
  restored file against the pre-staging anchor. Pass line: zero NEW
  findings vs the baseline on the untouched live file.
- **Dot-dir trap:** a dot-prefixed scratch dir is skipped by the linter's
  root scan — `--skill` on it returns "0 skills / 0 findings", which means
  "nothing linted", not "clean". Stage the whole skill at the repo root,
  lint, move out.

## 7. Decisions (as patterns, host-neutral)

Dated decisions in a fleet's instance docs become patterns here; the
specifics (who, when, which fleet) stay in the fleet's local ledger.

- **Force-loaded skills are referenced, not restated.** The card body
  carries ONE line naming the force-loaded skill; it never restates the
  skill's rules — the force-loaded SKILL.md is already in the worker's
  context, and restating adds bytes toward the 8 KB cap plus a second
  source of truth.
- **The writing standard lives on its own card.** A standard enforced by
  the card that wrote the prose (or the card that lints house format) is
  a conflict of interest; a dedicated stage (STE100) owns it. A standard's
  scope is STRICT on the machine-facing surface (description, verb/flag
  docs, error strings) and flavored on explanatory prose, with the house
  format PROTECTED so the two rubrics cannot fight.
- **Script-less skills route around the Scripter** — but the routing
  decision has ONE owner (the STE100 card), so the graph never has two
  cards deciding the same edge.
- **Report-only fleet checks.** The census card observes and reports
  drift; it never updates, forces, or reconciles. Reconciliation would
  rmtree a bot's local edits — that is the failure mode the tap
  protection exists to prevent.
- **PARK is a first-class state.** Cap exhausted = blocked + findings
  table + options for the owner. The owner unblocks with direction; a
  re-run comes as a NEW card (never a mutation of the parked one).
- **A resident orchestrator card is the wrong shape.** Workers are
  one-shot; a permanently-running orchestrator holds a claim forever and
  is a SPOF. Orchestration decomposes: kickoff (in-chat pre-flight + card
  1), routing (per-card branch tables), and — if wanted — a watchdog cron
  (stale pipelines, `todo` > 24h, parked cards). A watchdog is a cron
  ping, not a board task; "designed" and "created" are different claims —
  verify with `cronjob list` before writing either into a doc.
- **Supersede, never rewrite history.** A card-class change marks the
  old dated decision SUPERSEDED and appends a new one. A card-class change
  lives in PARALLEL sources (canonical body — work steps AND the `skills`
  list in the handoff branch, which silently re-loads removed skills —
  plus the spec, plus the board doc, plus the house skill): grep the token
  across all of them before, and re-grep to zero after (deliberate
  supersession notes excepted).

## 8. Re-census keys

Re-derive before quoting any dated number:

- **Pipeline in flight:** `hermes kanban --board skills list` (glyphs) or
  the DB `SELECT status, COUNT(*) FROM tasks GROUP BY status;`
- **A card's durable state:** the board DB `tasks` / `task_runs` /
  `task_links` (the DB is the witness, §5).
- **Worker context cap:** grep `_CTX_MAX_BODY_BYTES` in
  `.../kanban_db.py` (and the truncation in `_build_worker_context`).
- **Model availability:** both tiers must resolve on your proxy with the
  profile's key.
- **Fleet census:** `hermes profile list`; per profile, the
  `external_dirs` line in `config.yaml` (and the global config) + the
  `skills/.hub/lock.json` entry for the skill.
- **Linter baseline:** `python3 tools/lint_skills.py --json` (full repo)
  at the commit you are baselining against — never "remember the counts."
- **Watchdog:** `cronjob list` — "designed" is not "created."

## 9. Appendix — provenance (compressed)

This doctrine is distilled from the originating fleet's live pipeline,
which proved the full card set green AND red across a step-by-step rollout
(create-mode green chain; a deliberately defective red-team skill driving
the Author↔Audit loop to a fixed draft; seam-break and dirty-worktree red
cases PARKing at the Commit pre-flight with zero successors and clean git;
a placeholder-body incident that committed and pushed a throwaway fixture
and was reverted, leaving the incident pair in git history as the record
the no-op-placeholder rule exists because). The per-card red-team
defect classes, the exact run ids, and the dated decision ledger (including
the writing-standard force-load that was later moved onto its own card)
live in the originating fleet's local, gitignored docs — intentionally
not here, because a shared repo carries doctrine, not one fleet's run
history.
