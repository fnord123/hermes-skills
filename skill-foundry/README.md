# skill-foundry — the skill-maintenance pipeline

## What it is

This is how skills in the house repo get created and edited: not by hand, but
through a tracked, multi-role review where all steps (new issues, PRs, comments)
are viewable in github with attribution to what person or agent took them.

Main flows are "make a new skill that does X" and "improve this skill or skills."
Both utilize the same pipeline of specialist roles. The author role writes it, a
reviewer role checks it against the house rubric, a writing-auditor role checks the
prose, and (for skills with code) a scripter role implements and a verifier role tests
the scripts, before a commit role lands it. All of the steps above are done in
github for full traceability and attribution.

Previously skills were vibe-written using Claude (typically Opus) with the entirety of the creation, 
edits, etc., being mostly ephemeral in agent context.  While this produced decent skills, it
also was leaning heavily on the quality of a black box commercial model.  This rewrite is intended
to allow leveraging "free" (except for electricity and hardware) models that can be run locally.
To enable that, a much more robust approach was used based on adversarial review with separate
memory/context for different roles.  Github issues/PRs/comments was adopted as the core mechanism
for tracking the interactions between each role both to make it structured as well as to provide
more visibility into what the interactions are, which should make it easier to improve those 
interactions and the relevant agents.

This is a Hermes skill (`SKILL.md` + `scripts/`) and it is **host-neutral**:
the repo carries the doctrine — the script, the role playbooks, this doc —
while the instance values (repo path, GitHub slug, board, role profiles) live
in a filled instance file that is never committed. `templates/PROFILE.example`
names every key.

## How it works

The pipeline is a **bounded state machine**, carried by three artifacts:

- **One GitHub issue per skill request** — the state and the work order. Its
  label is the current stage; a fenced state block in the body carries the
  round counters and the PR/branch/worktree pointers.
- **One long-lived GitHub pull request per skill** — the artifact trail. The
  author opens it on round one; every rework adds commits to the same branch;
  every agent/role's verdict is posted to it as a comment by the script attributed
  to the agent.
- **One kanban card per handoff** — dumb dispatch. It wakes one role profile
  and tells it to read its playbook and the issue. Use of Hermes kanban cards
  was chosen because it provides an easy way to track progress of the agent within
  its task.

Seven roles each do their stage and make exactly one script call to hand off.
`scripts/skillpipe.py` is the **single writer** of everything in github — the labels,
the state block, the comments, the dispatch cards. Because the script, not the
models, owns every transition, the pipeline is a testable state machine:
`scripts/skillpipe_test.py` exercises every edge and runs in CI and the
pre-push hook.

| Role         | Job                                                            |
|--------------|----------------------------------------------------------------|
| **author**   | writes or reworks the skill on the branch; opens the pull request |
| **audit**    | checks the proposal against the house rubric; classifies findings true vs false |
| **ste100**   | the controlled-language writing audit (sentence length, voice, tense) |
| **scripter** | implements the script contract `SKILL.md` declares (scripted skills only) |
| **verifier** | runs the test matrix against the scripts (scripted skills only) |
| **commit**   | lands the green pull request on `main` — the one place it happens |
| **fleet**    | confirms the merge propagated to the fleet (a merge is not a deploy) |

A skill's journey, at a glance:

```
author ─▶ audit ─▶ ste100 ─▶ (scripter ─▶ verifier) ─▶ commit ─▶ fleet
          review    writing     scripted skills only
```

Each stage can send the work **back** to an earlier stage with a concrete fix
list — a *rework round*. Rework is **bounded**: a stage that keeps failing
parks the issue with a per-cap label instead of spinning forever, and the
owner resumes or abandons it. Scriptless skills skip the code stages and go
straight to commit.

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
author and audit share a counter (a review round, N, preserved across the
handoff); scripter and verifier share one (K); ste100 keeps its own (M). A cap
hit parks the issue with a per-cap label; the owner resumes it to any ready
stage (or abandons it). The full transition table, with the exact label each
edge produces, is in `references/spec.md` and — authoritatively — in
`skillpipe.py:decide()`.

## Layout

- `SKILL.md` — the operator skill: what to do when the user asks to create or
  review skills, and the intake routing (one call per skill).
- `scripts/skillpipe.py` — the state machine and single writer.
- `scripts/skillpipe_test.py` — the white-box tests for the transition table
  (run by CI + pre-push).
- `references/` — the seven role playbooks (`<role>-role.md`) that each
  dispatch card points its worker to, plus `spec.md` (the graph, the state
  machine, the labels, the worktree strategy) and `known-pitfalls.md`.
- `templates/PROFILE.example` — the instance-file key reference.

## Invariants

- **The repo never gets direct edits.** The main checkout stays on `main`,
  clean; every pipeline works in its own worktree on `sr/<skill>` and lands
  only via a squash merge. `intake` and `merge` both refuse to proceed if the
  target skill dir is dirty on `main`.
- **The script is the only writer.** Roles never touch labels, issue bodies,
  or kanban cards by hand — that is what keeps the record consistent and the
  state machine enforceable in code.
- **One pipeline per skill in flight** (different skills run in parallel).
- **A park is a first-class state**, not a failure: the board stops, nothing
  is committed, the owner gets the evidence and a `resume` or `abandon`
  decision.
