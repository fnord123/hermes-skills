---
name: github-issue-pr
description: >
  Create a GitHub issue and then open a pull request against that issue, with
  the issue and the pull request attributed to the app's bot identity instead
  of the logged-in account. PREFER THIS SKILL whenever the user wants work
  tracked on GitHub the way a bot should do it: filing an issue to describe
  the work, then adding a pull request that addresses it, with the bot (not
  the operator) as the author of both. It posts issues, issue comments, and
  pull requests through the app credential; the user still writes and pushes
  the code with their normal git workflow. Do not use it for merging,
  reviewing, or closing pull requests - those are out of scope. Activate on
  any of: "open an issue", "file an issue", "create an issue", "an issue for
  this", "track it in an issue", "add a PR against the issue", "a pull
  request for this issue", "issue and PR", "as the bot", "bot identity",
  "attributed to the bot", "under the app", or anything that sounds like
  filing a GitHub issue and then a pull request that fixes it.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Github, Issue, PullRequest, Bot, Workflow]
---

# github-issue-pr — file an issue, then a pull request against it

Open an issue to describe the work, then open a pull request against that
issue. When the app credential is configured, the tool posts both as the
app's bot identity. The issue and the pull request then carry the bot as
their author, not the logged-in account. You work entirely through the
verbs below; the tool posts everything, so you never build request bodies
yourself.

## When to use

Activate when the user wants to:
- **Open an issue** to describe a piece of work ("open an issue for the
  login bug").
- **Add a comment** to an existing issue ("add a comment to issue 42").
- **Open a pull request against an issue** ("now add a PR against issue 42").
- **List recent issues** to find one the user is referring to ("which issue
  is this?").

## When NOT to use

- **Merging, reviewing, or closing** a pull request. This skill opens them;
  it does not finish them. If the user wants a merge, say that this skill
  does not handle it.
- **Plain git work with no issue involved** (committing, pushing a branch,
  checking status). The user's normal git workflow covers that; this skill
  only posts the issue, the comments, and the pull request.
- **Repositories the app is not installed on.** If the tool reports that it
  cannot access the repository, the app does not have it - tell the user
  and stop.

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/issue_pr.py`, invoked as
`python3 <path> --repo owner/name <verb> [args]`. Every call passes the
repository as `--repo owner/name`. Each call prints ONE JSON object on
stdout (`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}`
with exit 1).

| Verb | Purpose |
|---|---|
| `issue --title <t> --body <b>` | Opens a new issue with the given title and body, and returns its number and URL. |
| `issue --title x --list` | Lists the 20 most recent open issues (newest first) so the user can pick one. |
| `comment --issue <n> --text <t>` | Adds a comment to the given issue. |
| `pr --issue <n> --title <t> [--branch <b>]` | Opens a pull request from the branch against the issue's repository default branch, and returns the pull request's number and URL. Without `--branch`, it uses the repository's single `issue-pr/…` branch; with several, it asks. |

`--body` / `--text` accept long text directly, or `--body-file` / `--file`
for a file path. The pull request body must not contain a line like
`Closes #42`. The tool refuses it. GitHub closes the issue when the
pull request is merged.

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to verbs BEFORE calling:

| User said | Call |
|---|---|
| "open an issue about the flaky test" | `issue --title "Flaky test in …" --body "…"` |
| "which issue did I file for the export bug?" | `issue --title x --list` |
| "add a comment to issue 42 with the repro steps" | `comment --issue 42 --text "…"` |
| "now add a PR against issue 42" | `pr --issue 42 --title "<imperative summary>"` |
| "open a PR from branch fix/export against issue 42" | `pr --issue 42 --title "<imperative summary>" --branch fix/export` |

Parsing notes:
- **The issue body carries the detail.** The title is one line; the user's
  full description (repro steps, expected vs actual) goes in `--body`.
- **A pull request title is an imperative summary** of what the branch
  changes ("Fix the export crash"), not the issue's title.
- **The code path sits between the two calls.** After `issue` returns, the
  user does the normal git work on a branch, or you do it when asked. Push
  the branch first. Never call `pr` before the branch is pushed.

## Output shape

- `issue` → `{"ok": true, "repo": "owner/name", "issue": 42, "url": "https://github.com/owner/name/issues/42", "title": "…", "as_bot": true, "actor": "app-123[bot]"}`
- `issue --list` → `{"ok": true, "repo": "…", "as_bot": true, "issues": [{"issue": 42, "title": "…", "author": "…", "created": "…"}]}`
- `comment` → `{"ok": true, "repo": "…", "issue": 42, "url": "…", "as_bot": true}`
- `pr` → `{"ok": true, "repo": "…", "pr": 43, "url": "https://github.com/owner/name/pull/43", "title": "…", "issue": 42, "head": "fix/export", "base": "main", "as_bot": true, "actor": "app-123[bot]"}`

`as_bot` is `true` when the post went out as the app's bot identity and
`false` when it went out as the logged-in account. `actor` names the
identity that posted.

Always echo the confirmation back so a mis-scoped post is caught
immediately. After `issue`, echo the issue number and URL. After `pr`,
echo the pull request number and URL. e.g. "Opened issue #42
(https://github.com/owner/name/issues/42) as the bot, and pull request
#43 from fix/export against it."

## A typical session

```
"Open an issue for the export crash, then I'll fix it on a branch."
  → issue --title "Export crashes on empty file" --body "<the user's description>"
  → "Opened issue #42 as the bot. Push the branch when it is ready."

(user pushes the branch)

"Now add the PR against it."
  → pr --issue 42 --title "Handle the empty-file export" --branch fix/export
  → "Opened pull request #43 from fix/export as the bot, against issue #42."
```

## When a verb reports an error

- `"no credential configured…"` → the skill is not configured. Point the
  user to the Setup section of `README.md`; do NOT try to reach GitHub
  another way (no raw requests, no other tools).
- `"cryptography is not installed…"` → the skill's dependency is missing.
  Point the user to the Setup section of `README.md`; do NOT try to reach
  GitHub another way (no raw requests, no other tools).
- `"GitHub rejected the request (404)…"` → the app cannot access that
  repository. Tell the user the app is not installed on it, and stop.
- `"branch '…' was not found on the remote"` (from `pr`) → the branch has
  not been pushed yet. Ask the user to push it, then retry `pr`.
- `"several issue-pr branches exist…"` (from `pr`) → the response names
  them. Ask which one, and pass it as `--branch`.
- `"the pull request body must not auto-close…"` → remove the
  `Closes #N` line from the body and retry.
- Any other error → relay it verbatim and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`issue --list` with an empty `issues` array means the repository has no
open issues. Say so plainly: "no open issues in that repository yet".
Do not re-check or speculate.
