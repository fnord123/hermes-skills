# sr-commit

**skillpipe — Commit role** for the [skill review
pipeline](https://github.com/fnord123/hermes-skills/tree/main/skill-kanban).

The only role that lands work: merges an approved pull request (after the
pre-flight) and closes the tracking issue. Does not push feature
branches — it merges the existing one and fast-forwards `main` locally.
