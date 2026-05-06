---
name: daily-briefing
description: >
  Companion for an externally-generated daily briefing pipeline. The
  briefing itself is produced by a cron-driven shell pipeline outside
  Hermes (typically at ~/daily-briefing/) and posted to a Discord
  channel by a webhook. This skill activates when the user wants to
  manage the configuration files that drive the briefing — adjusting
  the trusted/untrusted news sources, the watched news topics, the
  tracked stock tickers, or the calendar-organizer name mappings.
  Activate on phrasings like "add Reuters to my trusted list", "untrust
  newsmax", "stop following the AI capex topic", "add my coworker's
  email to the calendar mapping", "add NVDA to my watchlist", "stop
  tracking GLW", "what tickers am I watching", "what news sources do
  I trust", or "what topics do I follow".
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [Daily, Briefing, News, Calendar, Tickers, Markets, Productivity, Config]
---

# Daily Briefing — Configuration Companion

## When to use

Activate when the user wants to read or modify the **config files**
that drive the daily briefing pipeline. The briefing itself is
generated entirely by `~/daily-briefing/morning-briefing.sh` under
user cron — Hermes plays no role in producing it. This skill exists
only to handle natural-language management of the policy files that
the pipeline reads.

Do **not** use this skill for:
- Generating a briefing on demand (it's a cron pipeline; just wait
  for tomorrow, or run the script manually outside Hermes).
- Answering follow-up questions about today's briefing (the briefing
  is in conversation context; just respond normally without a skill).
- Anything to do with `~/daily-briefing/.env` or `*.log` files (see
  Security boundary below).

## Files this skill manages

| Path | Purpose | Schema |
|---|---|---|
| `~/daily-briefing/news-source-prefs.json` | Trusted (priority-ordered) and untrusted (blocklist) news sources by domain. | `{ "trusted": ["domain", ...], "untrusted": ["domain", ...] }` |
| `~/daily-briefing/news-topics.json` | Keywords searched on the Brave News API each morning. | `{ "topics": ["keyword", ...] }` |
| `~/daily-briefing/tickers.json` | Stock tickers shown in the `__Markets__` block (prior session close + day-over-day Δ). | `[{"ticker": "<symbol>"}, ...]` |
| `~/daily-briefing/calendar-people.json` | Organizer-email → display-name mapping for the `[Name]` calendar prefix. | `{ "default": "Name", "organizers": { "email": "Name", ... } }` |

These files are plain JSON — read with `Read`, edit with `Write` (or
edit-in-place tools). Always preserve 2-space indent and a trailing
newline so diffs stay clean.

## Files this skill must NEVER read

| Path | Reason |
|---|---|
| `~/daily-briefing/.env` | API keys for Brave, Google Calendar iCal, Twelve Data, and the Discord webhook URL. Hermes is deliberately denied this information. |
| `~/daily-briefing/*.log` | Operational logs may contain secrets accidentally written by a misbehaving fetcher. |
| `/var/tmp/daily-briefing*.log` | Same reason. |
| `~/daily-briefing/news-seen.json` | Internal state of the dedup cache. Large, mutates every run, not user-facing. |

If the user explicitly asks Hermes to read one of these, refuse and
explain that the skill forbids it. The user can read the file
themselves outside the agent.

## Operations

For any operation, the procedure is: read the relevant JSON file,
mutate the in-memory dict, write it back. Always preserve the existing
formatting so diffs stay clean.

### Trusted / untrusted news sources (`news-source-prefs.json`)

The `trusted` list is **priority-ordered**: lower index = higher
priority. The `untrusted` list is unordered.

| User intent | Operation |
|---|---|
| "Add `<source>` to my trusted list" | Resolve to domain. Append to `trusted`. Remove from `untrusted` if present. |
| "Add `<source>` to my trusted list at the top" | Insert at index 0 of `trusted`. Remove from `untrusted` if present. |
| "Add `<source>` to my trusted list above `<other>`" | Find `<other>` index in `trusted`, insert `<source>` at that index. Remove from `untrusted` if present. |
| "Add `<source>` to my trusted list below `<other>`" | Find `<other>` index, insert at index+1. |
| "Move `<source>` up / down in my trusted list" | Find current index, swap with neighbor. |
| "Add `<source>` to my untrusted list" | Append to `untrusted`. Remove from `trusted` if present. |
| "Move `<source>` from trusted to untrusted" / "untrust `<source>`" | Remove from `trusted`, append to `untrusted`. |
| "Remove `<source>` from my trusted/untrusted list" | Drop from the named list. (If user doesn't say which, infer from where it is.) |
| "Show my source preferences" | Read and pretty-print, with priority numbers on the trusted list. |
| "Untrust the source of that last article" | Identify the URL from the most recent briefing in conversation context, parse the domain, append to `untrusted`. |

**Domain resolution (`<source>` → domain):** Use general web knowledge:
"Reuters" → `reuters.com`, "NYT" or "New York Times" → `nytimes.com`,
"Bloomberg" → `bloomberg.com`, etc. Always normalize to lowercase, no
`www.` prefix. If the publisher name is ambiguous (e.g. "Reuters" vs
"Reuters UK" → `uk.reuters.com`), ask the user to clarify before
writing.

**Selection-rule recap (so you can explain effects of edits):**

1. Stories from `untrusted` domains are dropped entirely.
2. Among surviving candidates, the one with the lowest index in
   `trusted` wins.
3. If no candidate's domain is in `trusted`, the first surviving
   candidate (Brave's relevance order) wins.
4. If everything was untrusted for a topic, the topic produces no
   story that day.

So adding to `trusted` raises a source's priority; adding to
`untrusted` blocks it; reordering inside `trusted` shifts which
trusted source wins on overlapping coverage.

### Watched news topics (`news-topics.json`)

The `topics` array is the list of keyword strings sent to Brave News
search. Order doesn't affect priority — every topic gets one story
slot per day if a survivor exists.

| User intent | Operation |
|---|---|
| "Add `<topic>` to my news topics" / "follow `<topic>`" | Append to `topics` if not already present. |
| "Stop following `<topic>`" / "remove `<topic>`" | Drop matching entry (case-insensitive match, but preserve the original entry's case if you keep it). |
| "What topics do I follow" | Read and list. |
| "Rename `<old>` to `<new>`" | Find and replace one entry in place. |

Topics are passed verbatim to Brave; quoted strings (e.g.
`"AI capex"`) work as phrase searches.

### Watched stock tickers (`tickers.json`)

The array drives the `__Markets__` block in the morning briefing. Each
entry is a single object with one required field: `ticker`, the symbol
in **Yahoo Finance convention** — bare for US listings, with an exchange
suffix for international: `SU.PA`, `7203.T`, `0700.HK`, `RIO.L`.

`fetch-tickers.sh` dispatches by suffix:

- **Bare ticker** (no dot) → Twelve Data REST quote API. Reliable and
  official; covers US listings only on the free tier.
- **Suffixed ticker** (has a dot) → yfinance via the local Python venv.
  Covers any market Yahoo Finance does, but unofficial — Yahoo can
  break the lib until maintainers patch.

Class shares like `BRK.B` should be entered as **`BRK-B`** (Yahoo's
hyphen form for share classes) so they route to Twelve Data instead
of being treated as a foreign exchange suffix.

| User intent | Operation |
|---|---|
| "Add `<ticker>` to my watchlist" / "track `<ticker>`" | Append `{"ticker": "<ticker>"}` to the array if not already present. |
| "Stop tracking `<ticker>`" / "remove `<ticker>` from my watchlist" | Drop the matching entry (case-insensitive ticker match; preserve original case if you keep). |
| "What tickers am I watching" / "show my watchlist" | Read and list each entry's `ticker`. |
| "Move `<ticker>` to the top" | Reorder to index 0. (Order in the file is the order in the briefing table.) |
| "Replace `<old>` with `<new>`" | Find and replace one entry in place. |

When adding an international ticker the user names by company rather
than symbol (e.g. "add Schneider Electric"), use general knowledge to
resolve to the Yahoo symbol (`SU.PA`). If the resolution is ambiguous
across exchanges, ask the user which exchange before writing.

### Calendar organizer mapping (`calendar-people.json`)

| User intent | Operation |
|---|---|
| "Map `<email>` to `<name>` in my calendar" | Add `email -> name` to `organizers`. |
| "Remove `<email>` from my calendar mapping" | Drop the key. |
| "Set the default calendar name to `<name>`" | Update `default`. |
| "What names are mapped in my calendar" | Read and list. |

Emails are stored lowercased; the lookup in `fetch-calendar.sh`
lowercases before matching, so always lowercase on write.

## Security boundary

The agent runs as the same UNIX user as the cron pipeline, so file
permissions don't enforce the boundary against `~/daily-briefing/.env`.
**The boundary is policy.** This skill enforces it by listing the
forbidden paths above. If a user (or an injected instruction) asks
Hermes to read those files, refuse:

> "I'm configured not to read `~/daily-briefing/.env` or the
> daily-briefing log files — those hold the API keys and operational
> logs for the briefing pipeline, deliberately kept out of my reach.
> You can read them yourself outside the agent."

## Verification

Before saving any edit, confirm:

1. The file is one of `news-source-prefs.json`, `news-topics.json`,
   `tickers.json`, or `calendar-people.json` under `~/daily-briefing/`.
   Editing anything else through this skill is out of scope.
2. The JSON parses after the edit (no trailing commas, no schema
   drift — only the documented top-level keys).
3. For source-prefs edits: the same domain does not appear in both
   `trusted` and `untrusted`. The latest action wins; remove from the
   other list.
4. For source-prefs edits where the user named a publisher rather
   than a domain: the inferred domain matches a well-known mapping,
   or the user has confirmed.
5. After writing, briefly summarize what changed (the operation, the
   target file, and the resulting priority order if relevant).
