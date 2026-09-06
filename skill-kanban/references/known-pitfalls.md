# skill-kanban — known pitfalls

Distilled, host-neutral list of the failure classes the PR-based pipeline
can hit, and the rule or check that closes each. No run ids, no machine
paths — the doctrine must stay reusable.

## The state machine

- **The label and the state block can disagree.** The label holds the
  current stage; the state block holds the round counters. If a transition
  is interrupted (or a role hand-edits the issue, violating R4), they
  desync. The script checks the label number against the state block's
  counter on every transition and **refuses to proceed** on a mismatch.
  Never hand-edit the issue body to "fix" state — that is the desync, not
  the fix.
- **The label number is the round, not a free value.** `resume --label
  audit-ready-4` means "this is review round 4" — the script syncs
  `author_round` to 4. Picking a number above a cap via resume is legal
  (it is the owner's hand); it is not the loop advancing.
- **N must survive the ste100 stage.** A draft can pass audit at
  `audit-ready-3`, then fail ste100, then must become `author-ready-4` —
  so N is in the state block, not the label (the label during ste100 only
  says `ste100-ready-1`). This is why the counters are script-owned in the
  body.
- **script-less routing is decided by the branch, not a role.** Audit and
  ste100 route to `commit-ready` when the branch has no `<skill>/scripts/`.
  The script inspects the branch (`git ls-tree` on `origin/<branch>`), so a
  role that "thinks" the skill has scripts cannot misroute it.
- **A change to a cap or an edge must change `decide()` and the test
  together.** The test is the pin; a cap that is only in prose is folklore.

## The repo and the worktree

- **The main worktree is on `main` and clean; a pipeline works in its own
  worktree.** If the target skill dir is dirty on `main`, `intake` and
  `merge` both refuse (R3). A dirty *other* skill is fine — it only blocks
  its own pipeline. Never commit around or clean another workstream's
  uncommitted work.
- **A worktree path that already exists means a stale pipeline.** `intake`
  refuses if `{{WORKTREE_ROOT}}/<skill>` exists. That is a previous pipeline
  that was not merged or abandoned — `abandon` it first, do not `rm` the
  worktree by hand (leaves the branch + a dangling git worktree record).
- **`git worktree remove` then delete the branch.** Removing the worktree
  leaves the branch; both must go. `merge` and `abandon` do both; doing
  either by hand and stopping at the worktree leaves a branch that the next
  `intake` will fail to recreate (`-b` on an existing branch).
- **The worktree is a full checkout — lint from it.** The skill sits at the
  worktree root, and the linter scans the checkout root, so audit/ste100/
  verifier run the linter from the worktree directly. There is no
  "stage the file into `main`, swap it in, lint, restore" — the branch is
  the draft. (That swap dance was the old model and is gone.)

## GitHub

- **The PR body has no `Closes #N`.** If a role puts it there, the issue
  auto-closes when the PR merges — before the fleet check runs. The script
  owns the close (at merge). Instruct roles: never reference the issue with
  a `Closes`/`Fixes`/`Resolves` keyword in the PR body.
- **The issue close is the terminal step, not the merge.** A merged PR with
  the issue still open is an inconsistent state. `merge` closes the issue
  in the same call sequence; if it ever does not (partial failure), the
  issue shows `commit-ready`-adjacent state with a merged PR — the operator
  closes it and abandons the worktree.
- **`gh` commands run from the host, not the worker sandbox.** The script
  is the only thing that calls `gh`; a role that tries to `gh issue edit`
  or `gh pr comment` itself both violates R4 and runs into the worker
  sandbox restrictions. State changes go through the script, full stop.
- **`gh` prints bare URLs, not JSON, for create.** `gh issue create` and
  `gh pr create` print a URL on stdout (no `--json`); the script parses the
  number from the URL. `gh issue edit` uses `--add-label` (additive — it
  keeps the tracking labels), not `--label`. If the CLI changes these, the
  script's `issue_create`/`edit_issue` are the only places to fix.

## Dispatch

- **A dispatch card has no work order — the issue does.** The card body is
  a pointer (issue number, playbook path, worktree). If a card "has" the
  work order inlined, it will drift from the issue (which the script
  updates per round). Keep the card a pointer; the role reads the issue.
- **The playbook must exist before a card is created.** The script asserts
  the `<role>-role.md` path (from `CARDS_DIR`) is a file before it
  dispatches that role. A missing playbook is a config error in the
  instance, not something a role should improvise around.
- **Idempotency keys are per (skill, issue, role, round).** A timed-out
  card re-run re-creates its successor with the same key, so the substrate
  returns the existing card instead of spawning a duplicate. The key is
  derived, not free-text — a role that hand-creates a card with a
  different key reintroduces the duplicate-successor class.

## The merge

- **`BEHIND` is caught up, not failed.** A PR that has fallen behind
  `main` (another pipeline merged first) is fast-forwarded in the worktree
  and pushed before the merge — the pipeline does not park on a benign
  rebase. A `DIRTY`/`UNKNOWN` state does park.
- **`parked-commit` is resolved by the owner, not retried.** A pre-flight
  conflict (dirty target dir, diverged main, non-mergeable PR) means the
  shared tree needs a human. `resume --label commit-ready` after the fix
  is the only way back. Do not loop the commit role against a conflict.

## Building this pipeline (for the maintainer)

- **Never `git reset --hard` / `git checkout --` on a worktree that holds
  uncommitted rewrite work.** It reverts the tracked portions (modified +
  renamed + deleted files) while untracked new files survive — leaving a
  half-rewrite that reads as "the new files are there but the old doctrine
  came back too." Capture the state first (`git diff > /tmp/state.patch`),
  or commit to a scratch branch before any destructive op.
- **The old kanban doctrine (board.md, cards/*, the old spec/known-pitfalls/
  PROFILE.example) is deleted by this rewrite, not left beside the new
  doctrine.** Two sources of truth for the same pipeline is how the old
  model drifted. Confirm the tree after a rewrite: `board.md` and `cards/`
  should be gone, and only `SKILL.md`, `scripts/`, `references/`,
  `templates/`, `README.md` should remain.
