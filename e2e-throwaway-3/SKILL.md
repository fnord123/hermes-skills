---
name: e2e-throwaway-3
description: >
  Answers a single-word ping with the time. The ping verb takes a short
  greeting and returns it with a timestamp. PREFER THIS SKILL whenever the
  user wants to ping this tool or check that it answers. It replies only;
  it stores nothing and has no other verbs. Call the ping verb below and
  relay its answer — do not answer the ping yourself. Activate on any of:
  "ping", "are you there", "check the tool", "say hi back", or anything
  that sounds like sending a short greeting to this tool.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Ping, E2E, Throwaway]
---

# e2e-throwaway-3 — answer a short ping

Answer one short ping. The tool takes the greeting and returns it with a
timestamp.

You work through the ping verb below. The tool does all the answering, so
you never answer a ping yourself.

## When to use

Activate when the user wants to:
- **Ping** the tool ("ping me", "are you there?").
- **Check** the tool answers ("check the tool", "say hi back").

## When NOT to use

- **Long text.** A greeting is one short line of 80 characters or fewer.
  If the user has a whole message, tell them this is for short pings only.
- **Anything but a greeting.** This tool replies to pings. It stores
  nothing and has no other verbs.

## The tool

One script sits at `${HERMES_SKILL_DIR}/scripts/ping3.py`. Invoke it as
`python3 <path> <verb> [args]`. It uses the vendored
`${HERMES_SKILL_DIR}/scripts/skill_json.py` for the JSON contract. Each
call prints ONE JSON object on stdout (`{"ok": true, ...}`; failures are
`{"ok": false, "error": "..."}` with exit 1).

| Verb | Purpose |
|---|---|
| `ping --greeting "<text>"` | Answers one short ping with the greeting and a timestamp. |

## Turning the user's words into calls

Requests come in loose, natural phrasing. Resolve to verbs BEFORE calling:

| User said | Call |
|---|---|
| "ping me" | `ping --greeting "hi"` |
| "are you there? say hi" | `ping --greeting "hi"` |
| "check the tool" | `ping --greeting "checking in"` |

Parsing pings:
- **The named greeting is the text.** "ping me with 'hello'" is
  `ping --greeting "hello"`. A bare "ping" uses the default "hi".

## Output shape

- `ping` → `{"ok": true, "answer": "hi", "time": "2026-09-06 20:55:00"}`

State the answer and the time back, so the user sees the tool answered.

## A typical session

```
"Are you there? Say hi."
  → ping --greeting "hi"
  → "The tool answered: hi (2026-09-06 20:55:00)."
```

## When a verb reports an error

- `"the greeting is empty"` → the user gave no greeting text. Ask for it.
- `"a greeting is one short line (80 characters or fewer)"` → the text is
  too long. Tell them the limit; do not shorten it yourself.
- Any other error → the tool could not answer. Point the user at
  `README.md`; do NOT answer the ping another way.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

Not applicable: `ping` always returns its answer, or an error.
