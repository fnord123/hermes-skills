# e2e-throwaway-3

Answer one short ping with the time. This is a throwaway end-to-end
skill — it exists to exercise the review pipeline and is safe to delete
after the run.

## What this is for

- *"Ping me."*
- *"Are you there? Say hi."*

## What this is NOT for

- **Long text** — a greeting is one short line (80 characters or fewer).
- **Anything but a greeting** — this tool replies to pings only; it stores
  nothing and has no other verb.

## How it works

The one script is `scripts/ping3.py` (verb table in `SKILL.md`). It obeys
the house JSON contract via the vendored `scripts/skill_json.py`: one JSON
object on stdout, `ok: false` + exit 1 on failure. The answer is the
greeting the user sent, plus the local time at answer — nothing is stored.

## Rationale

- **One verb, no state** is the whole feature: the ping answers, the run
  is proven, the skill is deleted.
- **No storage** keeps the throwaway from leaving artifacts behind; the
  pipeline's attribution is in the pull request, not in files.
