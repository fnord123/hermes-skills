---
name: browse-task
description: >
  Carry out a multi-step task on the web in a real browser — open pages,
  navigate menus, apply filters, click through results, and report back what it
  found or did. PREFER THIS SKILL when the user wants something *done on a
  website* that takes several steps: checking live price or availability by
  working through a site's own filters, looking something up that needs
  navigating menus or forms, or operating a web app on their behalf. It is
  read-only by default; a task that must sign in, submit, buy, book, post, or
  send requires explicit user approval first. Do NOT use it for a single web
  search or reading one page — the ordinary web tools handle those better.
  Activate on any of: "browse to", "go to X and …", "on the website …", "check
  the site for …", "look up … on <site>", "find the … and tell me", "work
  through …", "operate …", "fill out …", "navigate …".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Web, Browser, Browsing, Automation, Tasks, Research, Productivity]
---

# browse-task — do a multi-step web task in a browser

## When to use

- The user wants a result that requires **operating a website over several
  steps**: apply filters and read the outcome, page through listings, follow a
  flow across screens, use an interactive web app.
- The answer isn't on one page — it takes navigating menus, forms, or results.

## When NOT to use

- A **single web search** or reading **one page's text** — those are faster and
  cheaper with the ordinary web search / fetch tools.
- A task that would **change something** (sign in, submit a form, buy, book,
  post, send) unless the user has **explicitly approved that exact task** — then
  run it with `--confirm`.
- Anything that isn't on the public web (a desktop app, a local file).

## The tool

One script at `~/.hermes/skills/browse-task/examples/browse_task.py`, invoked as
`python3 <path> [args]`. It prints ONE JSON object on stdout.

| Command | Purpose |
|---|---|
| `browse_task.py --task "<what to find/do>" [--start-url <url>] [--max-steps N]` | Carries out a **read-only** web task (look up / read / compare) and returns the answer. The agent is told not to change anything. |
| `browse_task.py --task "<what to do>" --confirm [...]` | Carries out a task that must **act** on a site (sign in, submit, buy, book, post, send). Use only **after the user approved this exact task**. |

Defaults: opens a search engine first (`--start-url` to start elsewhere), up to
25 steps (`--max-steps` to raise or lower).

## Turning the user's words into calls

| User said | Call |
|---|---|
| "check REI for the price of the X jacket in medium" | `browse_task.py --task "On rei.com, find the price and availability of the <X> jacket in size medium"` |
| "what times is the Ferry Building open on Sunday" | `browse_task.py --task "Find the Ferry Building Marketplace hours for Sunday"` |
| "find the cheapest nonstop SFO→JFK on Aug 1 and tell me" | `browse_task.py --task "Find the cheapest nonstop flight from SFO to JFK on 2026-08-01 and report the airline, time, and price" --max-steps 40` |
| "book that flight" (after you showed it and they approved) | `browse_task.py --task "Book the 9am United nonstop SFO→JFK on 2026-08-01" --confirm` |

## Output shape

- Done → `{"ok": true, "status": "complete", "answer": "…", "steps": 12, "task": "…", "acted": false}`
- The agent needs a decision from the user → `{"ok": true, "status": "needs_input", "question": "…", "task": "…"}` — relay the `question` to the user and, once answered, call again with the answer folded into `--task`.
- Didn't finish → `{"ok": false, "status": "max_rounds" | "timed_out" | "aborted", "error": "… partial finding: …"}`.

Relay the `answer` to the user in plain language. Mention `steps` only if useful.
For `needs_input`, ask the user the `question` rather than guessing.

## Common flows

### Read-only lookup
```
browse_task.py --task "On <site>, find <thing> and report <fields>"
→ relay answer
```

### A task that acts (needs approval)
```
# 1. Describe the exact action and get the user's explicit go-ahead.
# 2. Only then:
browse_task.py --task "<the approved action>" --confirm
```

### It asks a question mid-task
```
{"status": "needs_input", "question": "Which delivery date do you want?"}
→ ask the user, then re-run with the choice included in --task
```

## When the tool reports an error

- `"not configured"` / `"not installed"` → the skill's one-time setup isn't done;
  point the user to `README.md`. Don't try to browse another way.
- `status: max_rounds` / `timed_out` → the task was too broad or the site too
  slow; report the partial finding and suggest narrowing it. Don't silently retry.

Always ask the user for guidance when there is an error; do not proactively try
to resolve errors yourself.

## Empty or inconclusive results

If `answer` is empty or says the information wasn't found, tell the user plainly
that the site didn't yield it — don't invent a value or assume the task
succeeded. If the task needed to act but ran read-only (no `--confirm`), the
answer will describe what action would be required; relay that and ask whether
to proceed.
