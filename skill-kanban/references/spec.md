# skill-kanban spec — the PR-based pipeline (authoritative reference)

This is the reference for the pipeline: the graph, the state machine, the
labels, the state block, the worktree strategy, and the invariants. The
**authoritative** transition table is `scripts/skillpipe.py:decide()` — this
doc describes it, and `scripts/skillpipe_test.py` pins it. If the doc and
the code disagree, the code is right and the doc is a defect to fix.

Instance values are `{{placeholders}}` (see `templates/PROFILE.example`).
The filled instance file is host-bound and never committed.

## 0. The three artifacts

- **Issue (one per skill request):** state + work order. The state label is
  the current stage; the fenced state block at the bottom of the body
  carries the round counters and the PR/branch/worktree pointers.
- **Pull request (one long-lived per pipeline):** the artifact trail. The
  author opens it on the first round; every rework adds commits to the same
  branch/PR; every role verdict (PASS summary, FAIL findings) is posted to
  it as a comment by the script. It reads top to bottom as the whole review.
  The PR body carries **no `Closes #N`** — the issue is closed by the script
  at merge, so close is script-controlled.
- **Kanban card (dumb dispatch):** created by the script, one per
  transition. Its body says: read your playbook, look at issue #N. No
  parentage, no handoff payload, no complete-first ordering — the issue and
  the PR are the handoff, and the script is the only thing that creates a
  card or moves a label.

## 1. The graph

```
intake ─▶ author-ready-1 ─▶ author ─▶ audit ─▶ ste100 ─▶ scripter ─▶ verifier ─▶ commit-ready ─▶ (merge) ─▶ fleet
                          (N pres.)   (N)      (M)       (K)        (K)          │
                                   ▲       │        │         │          │       └▶ parked-commit (pre-flight)
   audit FAIL ──▶ author-ready-(N+1)
   ste100 FAIL ─▶ author-ready-(N+1)          (M>=3 ─▶ parked-ste100-3)
   scripter FAIL (infeasible) ─▶ author-ready-(N+1)  (2nd ─▶ parked-scripter-3)
   verifier FAIL ─▶ scripter-ready-(K+1)      (K>3 ─▶ parked-verifier-3)
   author FAIL (request infeasible) ─▶ parked-author-5
   audit FAIL at N=5 ─▶ parked-audit-5
```

Routing notes:

- **N is preserved** across author→audit and scripter→verifier (the label
  number stays the same on the handoff).
- **author and audit share one counter (N = review round)**; a FAIL by
  either bumps N and re-enters at the author. So the author/audit N can be
  advanced by an audit FAIL *and* by a ste100 FAIL *and* by a
  scripter-infeasible FAIL — the author is the single rework sink.
- **ste100 keeps its own counter (M)**; a ste100 FAIL bounces to the author
  (N+1) and M advances the next time ste100 is invoked. N must survive the
  ste100 stage — that is what the state block is for (the label only holds
  the current stage, not N).
- **scripter and verifier share one counter (K)**; a verifier FAIL bumps K
  and re-enters at the scripter.
- **script-less skills** (the branch has no `<skill>/scripts/`): audit and
  ste100 route straight to `commit-ready` — no scripter/verifier. The
  script decides this by inspecting the branch, not by a role's self-report.
- **commit-ready** merges (no loop); a failed pre-flight is `parked-commit`.
- **fleet** is a report-only card created after the merge; it has no
  successor and simply completes.

## 2. The state machine (decide())

Input: the issue's **current** state label (role + N), the state block,
whether the branch has scripts (only audit and ste100 use it), and the role
verdict (pass/fail). Output: the next label (or `MERGED`). The table below
is `decide()` line for line; `skillpipe_test.py` covers every row.

| current label | verdict | has scripts | next label | counter moved |
|---|---|---|---|---|
| author-ready-N | PASS | – | audit-ready-N | – |
| author-ready-N | FAIL | – | parked-author-5 | – (request infeasible) |
| audit-ready-N | PASS | yes | ste100-ready-(M+1) | M+=1 (park if M would exceed 3) |
| audit-ready-N | PASS | no | commit-ready | – |
| audit-ready-N | FAIL | – | author-ready-(N+1) | N+=1 (park-audit-5 if N>5) |
| ste100-ready-M | PASS | yes | scripter-ready-(K+1) | K+=1 (park-verifier-3 if K>3) |
| ste100-ready-M | PASS | no | commit-ready | – |
| ste100-ready-M | FAIL, M<3 | – | author-ready-(N+1) | N+=1 (park-audit-5 if N>5) |
| ste100-ready-M | FAIL, M=3 | – | parked-ste100-3 | – |
| scripter-ready-K | PASS | – | verifier-ready-K | – |
| scripter-ready-K | FAIL (infeasible) | – | author-ready-(N+1) | N+=1, infeasible+=1 (park-scripter-3 on 2nd) |
| verifier-ready-K | PASS | – | commit-ready | – |
| verifier-ready-K | FAIL | – | scripter-ready-(K+1) | K+=1 (park-verifier-3 if K>3) |
| commit-ready | PASS | – | MERGED | – |
| commit-ready | FAIL | – | parked-commit | – |

**Desync detection:** on entry the script checks the label's number against
the state block's matching counter (author/audit→`author_round`,
ste100→`ste100_round`, scripter/verifier→`scripter_round`). A mismatch is a
hard error — the label and the state block disagree, and no transition
proceeds on an inconsistent state.

## 3. The labels

Exactly **one** state label is present on the issue at a time (the script
asserts this).

State (ready): `author-ready-1..5`, `audit-ready-1..5`,
`ste100-ready-1..3`, `scripter-ready-1..3`, `verifier-ready-1..3`,
`commit-ready`.

Park (cap exhausted / owner decision needed): `parked-author-5`,
`parked-audit-5`, `parked-ste100-3`, `parked-scripter-3`,
`parked-verifier-3`, `parked-commit`. The cap number is in the label so a
parked board shows *why* it stopped.

Tracking (persist for the life of the issue): `pipeline`, `skill-<slug>`.

The script creates any missing labels on first use (idempotent bootstrap)
— you do not create them by hand.

## 4. The state block

A fenced block at the bottom of the issue body, the **only** place the
round counters live (GitHub has no reliable label-change history):

```
<!-- pipeline-state
{
 "author_round": 1,
 "branch": "sr/<skill>",
 "cards": {"author": ["t_..."]},
 "infeasible": 0,
 "mode": "update",
 "pr": "https://github.com/owner/repo/pull/1",
 "scripter_round": 0,
 "skill": "<skill>",
 "ste100_round": 0,
 "worktree": "/abs/path/hermes-skills-worktrees/<skill>"
}
pipeline-state -->
```

Only the script writes it: it parses it, bumps the counter `decide()`
named, and rewrites it atomically on the same issue edit that moves the
label. The round notes section (above the block) is appended per round so
the issue body is itself a running log.

## 5. The worktree strategy

The main checkout (`{{REPO_DIR}}`) stays on `main`, clean, and read-only to
the pipeline. Each in-flight pipeline gets its own `git worktree`:

- `intake` creates `{{WORKTREE_ROOT}}/<skill>` on branch `sr/<skill>`
  (from `origin/main`).
- author and scripter **commit + push** from that worktree (this is how the
  PR moves).
- read-only roles (audit, ste100, verifier) work **in place on the
  checked-out branch** — the branch IS the draft, so there is no
  "stage the file, swap it in, lint, restore" dance. Linting runs from the
  worktree, which is a full checkout with the skill at its root.
- `merge` removes the worktree + branch, then fast-forwards `main`.

One pipeline per skill in flight (intake refuses a second); different
skills run in parallel (separate worktrees).

## 6. The invariants (R-rules, restated for the PR model)

- **R1 — Evidence or no verdict.** Every role ends in a verdict backed by
  output (lint JSON, a run's stdout + exit code, file:line). "Looks fine"
  is not a verdict.
- **R2 — Bounded loops.** The caps in §2 are enforced in `decide()`. Any
  exhaustion parks; there is no third silent spin.
- **R3 — The main worktree is clean and shared.** `intake` and `merge` both
  refuse to proceed if the **target skill dir** is dirty on `main`
  (a dirty *other* skill is allowed — it only blocks its own pipeline).
  Never clobber, never sweep another workstream's work into a commit.
- **R4 — Only the script writes state.** Roles never edit labels, issue
  bodies, or kanban cards by hand. Bypassing the script produces an
  inconsistent record and a desync the next transition will reject.
- **R5 — One pipeline per skill in flight.**
- **R6 — A child's self-report is not a fact.** Each role re-verifies the
  seams it relies on (the PR diff is the proposal, the linter/its runs are
  the evidence) before it acts.
- **R7 — No `--force`, no `--no-verify`, no force-push.** The merge is a
  plain squash merge; the pre-push hook (lint, linter battery, vendor
  check, tests) is an independent witness and its failure blocks.

## 7. Merge and the pre-flight

`merge` checks, before touching anything: the target skill dir is clean on
`main`; the main checkout is on `main` and in sync with the remote; the PR
is open and mergeable (a `BEHIND` PR is caught up to `main` and pushed
before the merge). Any failure → `parked-commit`, with the reason posted to
the PR and the commit card blocking `needs_input`. The owner fixes the
shared tree, then `resume --label commit-ready` re-dispatches the commit
role, which re-runs `merge`. On success the script merges (squash), posts
the result, closes the issue, removes the worktree + branch, fast-forwards
`main`, and creates the fleet card.

## 8. Park and resume

A park is a first-class state, not a failure: the board stops, nothing is
committed, and the owner gets the evidence (the findings comment on the PR,
the park reason) plus a decision.

- **`resume --issue N --label <any-ready-label> [--note ...]`** — the
  owner's hand. Sends a parked issue back to any stage; the label you pick
  IS the round (the script syncs the matching counter to it), and your note
  is posted to the issue as the work order. This is how you "add a comment
  and send it back to whatever stage I want."
- **`abandon --issue N --yes`** — closes the issue and removes the
  worktree + branch.

## 9. Dispatch mechanics (what the script does on a transition)

1. Relabel the issue (add the target label, remove the current one) — one
   atomic issue edit that also rewrites the state block and appends the
   round note.
2. Post the verdict comment to the PR (or the issue, if no PR yet).
3. If the target is a ready label: create the next role's card
   (`--assignee` = the role's profile from the instance, workspace = the
   worktree, house skill force-loaded, mid model for author/scripter). The
   card id is recorded in the state block. If the target is a park label:
   create no card (a parked board has no next step; `parked-commit` is the
   exception — the current commit card blocks itself).

The dispatch card body is small (well under the 8 KB cap) and carries: the
issue number, the state label, the playbook path, the worktree, and the
instance + script paths. It carries **no** work-order content — the issue
and the PR are the work order.

## 10. Verification of a role's handoff (R6, for the operator)

Before reporting a stage as done, confirm the artifact the role claims:
the PR diff exists and matches the summary; the lint JSON / run output is
cited; the state block's counter advanced by exactly the `decide()` amount;
the next card was created (its id is in the state block) and is `ready`/
`running`. The script's JSON return is the receipt — read it, do not assume.

## 11. The code style standard (the verifier's `style-check`)

The house writes skill scripts in **bash or python — full stop**. A
different language is allowed only with a very good reason, documented in
the pull request body (the verifier then records the exception as
reviewed-and-accepted instead of FAILing it). Within those two languages
the standard is the **Google Shell Style Guide** and the **Google Python
Style Guide**, mechanized by the `style-check` verb into a subset that
runs deterministically:

| check | tool | rule |
|---|---|---|
| language | ext/shebang scan | `.py`/`.sh`/`.bash` only; a known code extension in a different language, or a shebang that is neither python nor bash, is a finding |
| bash shebang | first line | `#!/usr/bin/env bash` |
| python lines | `pycodestyle --max-line-length=80` | 80 columns, whitespace, naming |
| python hygiene | `pyflakes` | undefined names, unused imports/variables |
| import order | `isort --profile=google --check-only` | stdlib / third-party / local, sorted |
| shell style | `shellcheck -f gcc` | the Google Shell guide's core |

**Diff-scoped, on purpose.** The check runs only over the files the branch
changed versus `origin/main`, and a *modified* file is judged against the
findings its `origin/main` blob already carried. New files get a zero
baseline. The existing fleet carries thousands of pre-existing PEP8
findings (the 7,251-finding baseline was measured, not assumed), so a
repo-wide gate would make every pipeline park; "no *new* debt in the
files you touched" is the enforceable form of "the standard applies."

**The toolkit** is a pinned, host-side install (`STYLE_TOOLKIT` in the
instance file): a venv with `pycodestyle` + `pyflakes` + `isort` and a
`shellcheck` binary. The verb refuses to run if it is missing — the
standard is enforced or the verifier reports the tooling gap; it never
silently skips. The verb is read-only and emits the house JSON contract:
`{"ok", "clean", "standard", "checked", "findings"}` — every `findings[]`
entry is a FAIL finding the verifier files verbatim.
