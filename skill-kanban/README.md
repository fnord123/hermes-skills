# skill-kanban — the skill-maintenance pipeline

A request to create or change a skill in a house skill repo does not get
hand-edited into the tree. It goes through a tracked, multi-role review that
lands **only through a pull request**, so every change has a pull request
that reads top to bottom as the whole record: the author's proposal, then
the auditor's comment, then the writing auditor's, then the verifier's, then
the merge.

This is a Hermes skill (`SKILL.md` + `scripts/`). It is **host-neutral**:
the repo carries the doctrine — the script, the role playbooks, this doc —
with the instance values (repo path, GitHub slug, board, role profiles)
pulled out into a filled instance file that lives host-side and is never
committed. `templates/PROFILE.example` names every key.

## How it works

One GitHub **issue** per skill request is the state + work order. Its label
is the current stage; a fenced state block at the bottom of its body carries
the round counters and the pull-request / branch / worktree pointers. One
long-lived GitHub **pull request** per pipeline is the artifact trail —
every role verdict is posted to it as a comment by the script. A **kanban
card** is dumb dispatch: it wakes one role profile and tells it to read its
playbook and the issue.

`scripts/skillpipe.py` is the **single writer** of all of it: the labels,
the state block, the comments, and the dispatch cards. The roles do their
stage work and make exactly one script call to hand off. Because the script
— not the models — owns the transitions, the whole pipeline is a testable
state machine: `scripts/skillpipe_test.py` exercises every edge (happy
path, every fail loop, every cap → park, resume, label/state desync) and is
run by CI and the pre-push hook.

## The graph

```
intake ──author-ready-1──▶ author ──▶ audit ──▶ ste100 ──▶ scripter ──▶ verifier ──▶ commit-ready
        (N preserved)      (N)        (M)         (K)        (K)          │
                                  ▲            │          │          │     └▶ parked-commit (pre-flight)
        audit FAIL (N→N+1) ─────────┘ FAIL(N)───┘ FAIL(K)───┘ FAIL(K→K+1)
        caps: author/audit 5 · ste100 3 · scripter/verifier 3 · commit (parked-commit)
        script-less skills: audit/ste100 route straight to commit-ready
```

The label's number is how many times that role has been assigned the skill.
author and audit share a counter (a review round, N preserved across the
handoff); scripter and verifier share one (K); ste100 keeps its own (M). A
cap hit parks the issue with a per-cap label; the owner resumes it to any
ready stage (or abandons it). The full transition table, with the exact
label each edge produces, is in `references/spec.md` and — authoritatively —
in `skillpipe.py:decide()`.

## Layout

- `SKILL.md` — the operator skill: what to do when the user asks to create
  or review skills, and the intake routing (one call per skill).
- `scripts/skillpipe.py` — the state machine and single writer.
- `scripts/skillpipe_test.py` — the white-box tests for the transition
  table (run by CI + pre-push).
- `references/` — the six role playbooks (`<role>-role.md`) that each
  dispatch card points its worker to, plus `spec.md` (the graph, the state
  machine, the labels, the worktree strategy) and `known-pitfalls.md`.
- `templates/PROFILE.example` — the instance-file key reference.

## Invariants

- **The repo never gets direct edits.** The main checkout stays on `main`,
  clean; every pipeline works in its own worktree on `sr/<skill>` and lands
  only via a squash merge. `intake` and `merge` both refuse to proceed if
  the target skill dir is dirty on `main`.
- **The script is the only writer.** Roles never touch labels, issue
  bodies, or kanban cards by hand — that is what keeps the record
  consistent and the state machine enforceable in code.
- **One pipeline per skill in flight** (different skills run in parallel).
- **A park is a first-class state**, not a failure: the board stops,
  nothing is committed, the owner gets the evidence and a `resume` or
  `abandon` decision.
