# jarvis

**House butler** for the putzolu fleet. Jarvis runs the house: Home
Assistant with confirm-before-destructive, reminders, phones, the dog
(Pallo), and correspondence.

This GitHub App is Jarvis's own identity so his actions in
[`fnord123/homelab`](https://github.com/fnord123/homelab) are attributed
to `putzolu-jarvis[bot]` — the pull requests he opens, the commits he
pushes, the comments — instead of all showing the repo owner.

## Permissions (pushing role)

| Scope | Access | Why |
|---|---|---|
| **Contents** | Read & write | pushes his commits / new branches — the real least-privilege lever for a role that ships changes |
| **Issues** | Read & write | opens / relabels / comments on issues |
| **Pull requests** | Read & write | opens and updates the PRs he ships |

Metadata (Read only) is always on. No webhooks, no account-level
permissions, installed on `fnord123/homelab` only.
