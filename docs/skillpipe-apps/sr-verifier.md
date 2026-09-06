# sr-verifier

**Verifier role** for the [skill review
pipeline](https://github.com/fnord123/hermes-skills/tree/main/skill-kanban).

Runs the test matrix and the style checks against a finished skill
proposal, locally in the pipeline worktree. Never edits the skill; posts
the pass/fail verdict and, on fail, the findings the scripter must
address. Read-only over the repository content — this bot never pushes.
