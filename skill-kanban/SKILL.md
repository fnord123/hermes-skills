---
name: skill-kanban
description: >
  Operate the skill-maintenance pipeline for a house skill repo — the
  machinery that turns "make a new skill" or "improve these skills" into a
  tracked, multi-role review that lands only through a pull request. One
  request per skill becomes one tracking issue and one pull request; a
  small script is the only thing that moves the state label, posts the
  verdict to the pull request, and wakes the next role, so the whole trail
  (what was proposed, what each reviewer said, what got merged) reads top
  to bottom in one pull request. PREFER THIS SKILL whenever the user asks
  to create a skill, change one or more skills, or run the review pipeline
  on the house repo — a single named skill, a list of skills, or "all
  skills". Relay the script's JSON results — do not edit the repo, move
  labels, or create dispatch cards yourself. Activate on any of: "create
  a skill", "make a new skill", "add a skill", "update this skill", "improve
  these skills", "review these skills", "run the skill pipeline", "skill
  pipeline", "how's the skill review going", "parked skill", "resume the
  skill pipeline", "abandon the skill", or anything that reads like
  requesting a house-repo skill through the tracked review process.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Skills, Pipeline, Review, Authoring, HouseRepo, PRWorkflow]
    requires_toolsets: [terminal]
---

# skill-kanban — the skill-maintenance pipeline

A request to create or change a skill does not get hand-edited into the
repo. It goes through a tracked review: one issue per skill carries the
state and the work order, one pull request per skill carries the proposal
and every reviewer's verdict, and a single script is the only thing that
moves the state, posts the verdict, and wakes the next role. The
deliverable for the user is a set of pull requests they can open and read
top to bottom — author's proposal, then the auditor's comment, then the
writing-auditor's, then the verifier's, then the merge.

This skill is the **operator** (the chat profile). It parses the user's
request and calls `skillpipe.py`. It never edits a skill file, moves a
label, comments on a pull request, or creates a dispatch card by hand —
all of that is the script's job, so the record stays consistent. The
roles (author, audit, writing-audit, scripter, verifier, commit, fleet)
run as separate profiles on a dispatch board; each one does its stage
work and makes exactly one script call to hand off.

## When to use

- The user asks to create a new skill in the house repo
- The user asks to update or improve one skill, a list of skills, or all
  of them
- The user asks how a skill review is going, or wants to park, resume, or
  abandon one
- Anything that reads like "run the skill pipeline on …"

## When NOT to use

- Skills in the agent framework's own repo (use its authoring standard)
- Personal skills kept outside the house repo (plain skill management)
- A request that is really "fix one typo" the user will accept
  un-reviewed — confirm first, but the default is to route it through the
  pipeline, because the trigger surface is what the review protects

## The tool

`skillpipe.py` is the state machine and the only writer of pipeline
state. Pass `--instance $SKILLPIPE_INSTANCE` (the filled instance file for
your install — see `PROFILE.example` for the keys it needs). Every call
prints exactly one JSON object; a failure is `{"ok": false, ...}` and a
nonzero exit.

| Verb | Purpose |
|---|---|
| `intake` | Open one tracking issue + a branch + the author dispatch for one skill |
| `intake-all` | Intake every skill in the repo, or a named list |
| `transition` | Hand a finished role off: move the label, post its verdict, wake the next role |
| `merge` | Merge the pull request, close the issue, wake the fleet check |
| `resume` | Move a parked issue to any ready state (the owner's hand) |
| `status` / `list` | Read one pipeline's state, or every open one |
| `comment` | Post a note to a pipeline's pull request |
| `abandon` | Close an issue and remove its branch and worktree |

Reference the script by path, not by a machine-local absolute path:
`python3 ${HERMES_SKILL_DIR}/scripts/skillpipe.py …`.

## Turning the user's words into calls

Count the skills in the request, then call `intake` once per skill — this
is the whole routing rule.

- **"Create skill foo that does bar"** → one skill → one call:
  `python3 ${HERMES_SKILL_DIR}/scripts/skillpipe.py --instance
  $SKILLPIPE_INSTANCE intake --skill foo --request "Create skill foo that
  does bar"`.
- **"Review these five: a, b, c, d, e for quality"** → five skills → use
  the list verb so the script walks them: `python3
  ${HERMES_SKILL_DIR}/scripts/skillpipe.py --instance
  $SKILLPIPE_INSTANCE intake-all --skills a,b,c,d,e --request "Review for
  quality and robustness"`.
- **"Review all skills"** → every skill in the repo → `python3 ${HERMES_SKILL_DIR}/scripts/skillpipe.py
  --instance $SKILLPIPE_INSTANCE intake-all --request "Review every skill
  for quality and robustness"`. The script enumerates the repo; you do not
  guess the list.

Keep the user's request text verbatim in `--request` — it becomes the work
order the author reads. Resolve fuzzy skill names against the repo first
(`git ls-files '*/SKILL.md'` in the instance's repo dir) and say the
mapping in your reply.

After `intake`/`intake-all` succeeds, report to the user: the issue number,
the issue link, and the state (it starts at `author-ready-1`). Do not poll
the board — the pipeline runs on its own. When the user asks "how's it
going", use `list` and read each pipeline's state label.

## Output shape

One JSON object per call, `{"ok": true, ...}` on success. `intake`
returns `issue`, `issue_url`, `mode`, `branch`, `worktree`, `label`, and
`card`. `list` returns `open` plus a `pipelines` array (issue, title,
label, pull request, worktree). Surface `issue_url` to the user as a link.

## A typical session

User: "Review all my skills for quality."

1. `intake-all` (no `--skills`) → the script enumerates the repo, opens an
   issue + branch + author dispatch per skill, and returns the full list.
2. Report the count and the issue links. Tell the user each one is at
   `author-ready-1` and will walk the review on its own.
3. User, a day later: "How's the review going?"
4. `list` → read each label: most at `audit-ready-N`, one at
   `commit-ready`, one `parked-audit-5`. Report the states; offer to
   `resume` the parked one if the user wants.

## When a verb reports an error

A nonzero exit means the request did not land. Read the `error` string,
fix the input (wrong skill name, a dirty working tree, an already-in-flight
pipeline for that skill), and retry once. Do not open the issue by hand,
edit the repo, or create a dispatch card to "help" — the script is the
single writer and bypassing it produces an inconsistent record.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`list` with `open: 0` means no pipeline is in flight — say so plainly and
offer to start one. `status` on a closed issue returns its final state; a
merged pipeline is the normal, successful end.
