# github-issue-pr

File a GitHub issue and then open a pull request against that issue. This
skill attributes both to the app's **bot identity**, not to the
logged-in account.

## What this is for

Agents working in a repo want their tracked work to read as the agent's
bot's work. It must not read as the personal account that happens to be
on the box.
The human-in-the-loop shape:

1. *Open an issue* describing the work.
2. Do the code work the normal way (branch, commit, push) - this skill
   does not touch git.
3. *Open the pull request* against that issue.

The three GitHub-facing posts (issue, comment, pull request) all go
through one small CLI. It authenticates as the app, so the `author` field
on the issue and the pull request is the bot (`app-<id>[bot]` / the app's
login). The response JSON carries `"as_bot": true`. The agent can report
that honestly.

## What this is NOT for

- **Merging, reviewing, or closing** pull requests - the skill opens them;
  finishing them is a human (or another tool) job.
- **Git work** - branching, committing, pushing are the user's normal
  workflow; the skill only posts.
- **Repositories the app is not installed on** - it reports the 404 and
  stops.

## How attribution works

GitHub Apps act through short-lived installation tokens (60-minute life).
When the three app variables are set, the script:

1. Signs a 10-minute JSON Web Token (RS256) with the app's private key.
2. Exchanges it for an installation access token (cached under
   `GH_TOKEN_CACHE`, default `~/.cache/issue-pr`, for 55 minutes - one mint
   per hour in practice).
3. Posts every issue/comment/pull request with that token, so GitHub
   records the app as the actor.

**Fail-closed on attribution.** If the app variables are set but the token
mint fails (bad key, revoked installation, no network), the script exits 1
with an error. It never uses an ambient `GH_TOKEN` in that case. A silent
fallback would post the issue under the operator's account and report
`"as_bot": true` - a misattribution, not a retry.

Without the app variables, the script uses the ambient `GH_TOKEN`
(logged-in account) and every response carries `"as_bot": false` so the
agent says so.

The script signs the JWT RS256 with the app's private key. It uses the
`cryptography` package, which `scripts/requirements.txt` declares. A stock
host lacks that package, so the Setup install step matters.

## Setup

1. Create a GitHub App with these repository permissions: **Issues:
   read/write**, **Pull requests: read/write**, **Contents: read**. The
   script reads refs to verify the branch exists and to resolve the
   default branch.
2. Install it on the target repository.
3. Export the three variables (in the agent's profile `.env` or the
   environment the script runs in):

   ```sh
   export GH_APP_ID=123456
   export GH_APP_INSTALLATION_ID=789012
   export GH_APP_KEY_FILE=/path/to/app-private-key.pem   # chmod 600
   ```

   The key file must not be group- or world-readable. The script refuses
   it. A sloppy `chmod` then fails loudly instead of leaking the key.
4. Install the signer dependency:

   ```sh
   python3 -m pip install -r scripts/requirements.txt
   ```

### Verifying the wiring

```sh
# a read that must succeed on the target repo
python3 scripts/issue_pr.py --repo owner/name issue --title x --list
```

A `{"ok": true, "as_bot": true, ...}` response proves the token mint and
the installation's access to the repo.

## The git flow between issue and pull request

The skill posts issue → (user's normal git work: branch, commit, push) →
skill posts the pull request. When the user says "add the PR" without
naming a branch, the tool looks for the repository's `issue-pr/…`
branches. It uses one when only one exists. It names them and asks when
several exist. It reports an error when none exists. So name the branch
`issue-pr/<slug>` when you want auto-detection. An explicit `--branch
<name>` works regardless of prefix.

The pull request body must not contain an auto-close line (`Closes #42`,
`Fixes #42`, `Resolves #42`) - the tool refuses it. GitHub closes the
issue on merge either way, and an explicit close line written by a bot can
fire before the review the owner wanted.

## Why one CLI instead of raw API calls

A small model given raw HTTP access to api.github.com will invent field
names. It will forget the accept header. In the worst case, it posts with
the operator's credential instead of the app's. One CLI with three verbs
and a stable `{"ok": ...}` envelope removes all of that. The model picks a
verb and relays the result. The attribution decision (app vs ambient,
fail-closed) then lives in code, where a test can check it, not in prompts.
