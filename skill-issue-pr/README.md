# skill-issue-pr

Propose a skill folder on disk for review: one issue in a code repo
states the change, a branch carries the skill's files, and a pull
request links back to the issue. The model-facing surface
(`SKILL.md` + the CLI's verbs and output) speaks only in issues, pull
requests, and branches; this README is the human/developer side —
setup, the backend, and the rationale.

## What this is for

- *"Open an issue and a pull request for my donations skill."*
- *"Propose this skill in the acme/skills repo."*
- *"Push the fix to the skill's pull request."*

## What this is NOT for

- **Arbitrary changes** — the unit moved is a skill folder (its
  `SKILL.md`, its `scripts/`, its other tracked files); a plain feature
  or fix goes through a normal pull request.
- **Review and merge** — the skill opens and updates; the verdict stays
  with the reviewer.

## How it works

```
skill_issue_pr.py <verb>
   │  (the CLI client for the repo's API, in whatever client
   │   library the install is set up with)
   ▼
the configured code repo (owner/name, from the config file
or --repo)
   │  issue created (body = the skill's own summary)
   │  branch created (sr/<skill-name>-<date>)
   │  the skill's files committed to the branch
   │  pull request opened (head = the branch, body links to the issue)
   ▼
one JSON object on stdout (issue, issue_url, pr, pr_url, branch)
```

## Setup

The script reads one config file (it owns it; never hand-edit it
around a running pipeline):

| Key | Meaning |
|---|---|
| `REPO` | `owner/name` — the repo the skills go to |
| `BOT_LOGIN` | the bot account's login (required only when `--as-bot` is used) |

`propose` and `update` take `--repo owner/name` to override `REPO` for
one call. Sign-in is delegated to whatever account the machine's repo
client is already signed in as (the person's); a bot account is an
opt-in second sign-in, enabled with `--as-bot` per call.

## Identity

The request asks for **attribution to the person or the agent's bot
account, the user's choice**. The contract therefore carries:

- no flag → the person (the account the machine is already signed in as)
- `--as-bot` → the bot account (requires the bot sign-in to be set up;
  the error says so plainly when it is not)

The skill documents the flag in domain words and says nothing about
how a sign-in works — that is this README's job.

## Design notes

- **Issue first, pull request second.** The issue is the work order the
  reviewer reads first; the pull request's body links to it, so the
  whole trail (what was proposed, what changed in it) reads top to
  bottom in one place.
- **The tool is the only writer.** The agent never assembles repo
  commands by hand; one call per intent keeps the record consistent
  (issue ↔ branch ↔ pull request always agree).
- **`update` is the idempotent path.** A second `propose` for the same
  folder refuses (it would open a duplicate pair); the existing pull
  request's number goes to `update`, which just adds a commit.
- **The branch name is stable per folder + day**
  (`sr/<skill-name>-YYYY-MM-DD`) so a same-day retry reuses the branch
  instead of stacking branches.
