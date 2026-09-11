---
name: skill-issue-pr
description: >
  Take a skill folder on disk (its documentation plus any code it carries)
  and propose it for review: one issue in a code repo states the change, a
  branch carries the skill's files, and a pull request links back to the
  issue, with both filed under the person or bot identity the user picks.
  The issue text carries the skill's own summary, so the reviewer opens one
  pull request and sees everything top to bottom. PREFER THIS SKILL whenever
  the user wants to put a local skill folder up for review as an issue plus
  a pull request, or to add a fixed version of a skill to a pull request
  that is already open. Activate on any of: "open an issue for this skill",
  "propose this skill", "send this skill up for review", "file a pull
  request with this skill", "issue and pull request for the skill", "update
  the open pull request with this skill", "push the fix to the skill's pull
  request", or anything that reads like putting a skill folder on disk in
  front of a reviewer as a tracked issue plus pull request.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Skills, Review, PullRequest, Issue, CodeRepo]
    requires_toolsets: [terminal]
---

# skill-issue-pr — propose a local skill as an issue plus a pull request

A skill folder on disk (documentation and any code) goes to a reviewer as
one tracked pair: an **issue** that states the change, and a **pull
request** whose branch carries the skill's files and whose body links to
the issue. One tool call does the whole pair; the tool does all the repo
plumbing, so you never assemble commands by hand.

## When to use

- The user has a skill folder on disk and wants it proposed for review
  ("open an issue and a pull request for this skill").
- The user fixed a skill and wants the fix onto the pull request that is
  already open for it ("update the open pull request with this skill").

## When NOT to use

- **A plain feature or fix with no skill folder** — this skill moves
  skill folders, not arbitrary changes.
- **Merging or closing** — the tool only opens and updates; the decision
  to merge stays with the reviewer.
- **A repo the user has no write access to** — the tool cannot sign in
  on its own; tell the user it is not configured (see `README.md`).

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/skill_issue_pr.py`, invoked as
`python3 <path> <verb> [args]`. Each call prints ONE JSON object on stdout
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with
exit 1).

| Verb | Purpose |
|---|---|
| `propose <skill-folder> [--repo owner/name] [--title T] [--body B] [--draft]` | Opens the issue, creates the branch, commits the skill's files onto it, and opens the pull request against the issue — one call, one pair. |
| `update <pr-number> <skill-folder> [--repo owner/name]` | Commits the skill folder's current files as a new commit on the branch behind the given open pull request. |

Identity: both verbs file under **the person's account by default**. When
a bot account is set up for this install (see `README.md`), pass
`--as-bot` to file under the bot instead. Ask the user which they want
when they have not said and a bot account is configured; never assume.

`--repo` is optional: when omitted, the tool uses the repo the install is
configured for (the config file), or the current directory's repo when
none is configured.

## Turning the user's words into calls

| User said | Call |
|---|---|
| "open an issue and PR for my donations skill" | `propose ~/skills/donations` |
| "propose this skill in the acme/skills repo" | `propose ./my-skill --repo acme/skills` |
| "send it up for review, but keep it a draft" | `propose ./my-skill --draft` |
| "file the pull request under the bot" | `propose ./my-skill --as-bot` |
| "update the open pull request with this skill" (PR known) | `update 42 ~/skills/donations` |
| "push the fix to the skill's pull request" (PR unknown) | ask the user for the pull request number, then `update <n> <folder>` |

Parsing notes:
- **The folder is the argument** — pass the path the user gave; the tool
  reads the skill's own summary line for the issue title when no
  `--title` is given.
- **A pull request number is a number, not a URL.** When the user names
  a pull request by link, take the trailing digits.
- **"Propose" means issue + branch + pull request.** If a pull request
  for the same skill folder is already open, the tool says so — use
  `update` instead of forcing a second pair.

## Output shape

- `propose` → `{"ok": true, "skill": "donations", "issue": 35, "issue_url": "https://…/issues/35", "pr": 36, "pr_url": "https://…/pull/36", "branch": "sr/donations-2026-09-10"}`
- `update` → `{"ok": true, "pr": 36, "pr_url": "https://…/pull/36", "commit": "a1b2c3d"}`

Always echo the confirmation back — issue link, pull request link, and
branch — so the user can open both immediately.

**A `propose` response may include a `warning` field** (a pull request for
that skill folder is already open). Relay it verbatim: the issue and pull
request were NOT created; the tool points at the open pull request, and
the right move is `update` on it.

## A typical session

```
"Open an issue and a pull request for my donations skill."
  → propose ~/skills/donations
  → "Issue #35 and pull request #36 are open (branch sr/donations-2026-09-10).
     Both link to each other — the reviewer reads them top to bottom."

(The user edits the skill, then:)
"Push the fix to the skill's pull request."
  → update 36 ~/skills/donations
  → "Commit a1b2c3d is on the branch behind pull request #36."
```

## When a verb reports an error

- `"no repo …"` → the install has no configured repo and the current
  directory is not one. Ask the user which repo, then retry with
  `--repo owner/name`. Do not guess.
- `"<folder> is not a skill folder…"` → the path is missing or has no
  documentation file. Confirm the path with the user.
- `"a pull request for 'X' is already open…"` (from `propose`) → the pair
  exists. Switch to `update` with that pull request's number.
- Any sign-in error (an account is not signed in, the bot account is not
  set up) → the install is not configured for that identity. Point the
  user to `README.md`; do NOT try to reach the repo another way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

There are no read-only verbs: every call creates or updates something.
When `update` finds the folder already identical to the branch, it
reports `{"ok": true, "status": "unchanged", …}` — say plainly that
nothing changed; do not invent a fix.
