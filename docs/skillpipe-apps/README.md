# skillpipe GitHub Apps

The [skill review pipeline](../../skill-kanban/) runs one GitHub App per
review role so every artifact in the trail is attributed to the role that
produced it — the pull request the author opened, the verdict the audit
posted, the merge the commit role performed — instead of all showing the
repo owner.

## The apps

| App | Bot identity | Contents | Issues | Pull requests | Why |
|---|---|---|---|---|---|
| [sr-author](sr-author.md) | `sr-author[bot]` | Read & write | Read & write | Read & write | pushes author commits; opens + updates the PR; relabels the tracking issue on handoff |
| [sr-audit](sr-audit.md) | `sr-audit[bot]` | Read only | Read & write | Read & write | never pushes; reads the PR diff; posts round verdicts; relabels the issue on handoff |
| [sr-ste100](sr-ste100.md) | `sr-ste100[bot]` | Read only | Read & write | Read & write | writing audit — same surface as audit |
| [sr-scripter](sr-scripter.md) | `sr-scripter[bot]` | Read & write | Read & write | Read & write | pushes script commits onto the pipeline branch; updates the open PR; posts findings on infeasibility |
| [sr-verifier](sr-verifier.md) | `sr-verifier[bot]` | Read only | Read & write | Read & write | runs the test matrix + style checks locally in the worktree; never edits the skill; posts the verdict |
| [sr-commit](sr-commit.md) | `sr-commit[bot]` | Read only | Read & write | Read & write | merges the approved PR via the API and closes the tracking issue; local fast-forward of `main` only, no push of its own |

All apps: **Metadata: Read only** (default, required). **No webhooks.**
**No account/org-level permissions** — repository-level only, installed on
`fnord123/hermes-skills`.

## Notes on the minimums

- GitHub App permissions are coarse: **"Pull requests: Read & write" is
  the smallest tier that allows commenting on a pull request** — there is
  no comment-only tier. A read-only role could therefore *technically*
  open a PR from that permission alone; what actually prevents it is
  **lacking Contents: write** (no push → no new branch → nothing to open
  from). The Contents column is the real least-privilege lever.
- The read-only roles keep **Contents: Read only** (not none) so their
  `git fetch` / `git ls-remote` plumbing authenticates cleanly; a token
  with no Contents scope at all 403s on git-over-https.
- **Intake has no app.** Starting a pipeline is a human decision; the
  operator's identity on the issue/worktree origin is deliberate. The
  `sr-fleet` role reports locally (did the profiles pick up the commit)
  and never posts to GitHub, so it has no app either.
- Tokens are minted per role by the `skillpipe-auth` helper (RS256 JWT →
  1-hour installation token, cached ~55 min); the private keys live
  host-side (`~/.hermes/creds/gh-skillpipe-<role>.pem`, mode 600) and are
  referenced by path from each role's `.env`. Nothing secret is in this
  repo.

## Rotating an app

Regenerate the key in the app's General tab, replace the PEM file, and
restart the role's profile — no code change. Delete-and-recreate is
last resort: the `installation_id` changes and the `.env` must be updated.
