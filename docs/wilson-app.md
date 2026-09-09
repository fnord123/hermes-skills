# wilson

**Maintenance role** for the [rx-review pipeline](../rx-review/README.md). Wilson owns the
pipeline's engineering — the code, stages, docs, config, hooks, and
notifications — end to end. When a stage is wrong or a doc doesn't match the
code, Wilson ships the fix.

This GitHub App is Wilson's own identity so his actions in
[`fnord123/hermes-skills`](https://github.com/fnord123/hermes-skills) are
attributed to `wilson[bot]` — the pull requests he opens, the commits he
pushes, the comments and labels — instead of all showing the repo owner.

## Permissions (pushing role)

| Scope | Access | Why |
|---|---|---|
| **Contents** | Read & write | pushes his commits / new branches — the real least-privilege lever for a role that ships fixes |
| **Issues** | Read & write | opens / relabels / comments on pipeline issues |
| **Pull requests** | Read & write | opens and updates the PR he is fixing |

**Metadata: Read only** (default, required). **No webhooks.** **No
account/org-level permissions** — repository-level only, installed on
`fnord123/hermes-skills` only.

## Auth

- **API actor** (`gh`): a `ghs_…` installation token minted per call by
  `wilson-gh-auth` (RS256 app JWT → 60-min installation token, cached ~55 min).
- **Git commit author**: `wilson[bot] <…@users.noreply.github.com>`, supplied
  to git via env config so the operator's own global `gh`-PAT helper can't
  answer first.
- Both layers act as `wilson[bot]`; neither falls back to the owner identity.
- The private key lives host-side in the wilson profile directory
  (`~/.hermes/profiles/wilson/gh-wilson.pem`, mode 600, next to that profile's
  `.env`) and is referenced by path. `wilson-gh-auth discover` lists the
  installation id from the API, not the URL; `wilson-gh-auth whoami` proves a
  minted token reaches the installed repo. Nothing secret is in this repo.

## Rotating the app

Regenerate the key in the app's General tab, replace the PEM file, and restart
the wilson profile — no code change. Delete-and-recreate is last resort: the
`installation_id` changes and the `.env` must be updated.
