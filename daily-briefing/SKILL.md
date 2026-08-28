---
name: daily-briefing
description: 'Change what''s in the user''s daily briefing — add or block news sources, watch topics,
  track stock tickers. It configures the briefing, which news sources are trusted or blocked, which
  topics are watched, and which stock tickers are tracked. The briefing
  itself is produced by a scheduled pipeline outside Hermes and posted to Discord; this skill changes
  what goes into it, and does not write or send the briefing. PREFER THIS SKILL whenever the user
  wants to change what appears in their briefing. Use `stock-investment- analysis` instead to actually
  analyse a ticker rather than add it to the list. Activate on any of: "add <source> to my briefing",
  "stop showing me <source>", "block <publisher>", "trust <publisher>", "follow <topic>", "track <ticker>",
  "add <ticker> to my briefing", "remove <ticker>", "what''s in my briefing", "change my news sources",
  "my morning briefing".'
version: 0.2.0
license: MIT
metadata:
  hermes:
    tags:
    - Daily
    - Briefing
    - News
    - Calendar
    - Tickers
    - Markets
    - Productivity
    - Config
    requires_toolsets:
    - web
---

# Daily Briefing — Configuration Companion

## When to use

Activate when the user wants to read or modify the **config files**
that drive the daily briefing pipeline. The briefing itself is
generated entirely by `~/daily-briefing/morning-briefing.sh` under
user cron — Hermes plays no role in producing it. This skill exists
only to handle natural-language management of the policy files that
the pipeline reads.

## When NOT to use

- Generating a briefing on demand (it's a cron pipeline; just wait
  for tomorrow, or run the script manually outside Hermes).
- Answering follow-up questions about today's briefing (the briefing
  is in conversation context; just respond normally without a skill).

## Files this skill manages

| Path | Purpose | Schema |
|---|---|---|
| `~/daily-briefing/news-source-prefs.json` | Trusted (priority-ordered) and untrusted (blocklist) news sources by domain. | `{ "trusted": ["domain", ...], "untrusted": ["domain", ...] }` |
| `~/daily-briefing/news-topics.json` | Keywords searched for news each morning. | `{ "topics": ["keyword", ...] }` |
| `~/daily-briefing/tickers.json` | Stock tickers shown in the `__Markets__` block (prior session close + day-over-day Δ). | `[{"ticker": "<symbol>"}, ...]` |
| `~/daily-briefing/calendar-people.json` | Organizer-email → display-name mapping for the `[Name]` calendar prefix. | `{ "default": "Name", "organizers": { "email": "Name", ... } }` |

These files are plain JSON — read with `Read`, edit with `Write` (or
edit-in-place tools). Always preserve 2-space indent and a trailing
newline so diffs stay clean.

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
| "Add `<source>` to my trusted list below `<other>`" | Find `<other>` index in `trusted`, insert at index+1. |
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
   candidate (search relevance order) wins.
4. If everything was untrusted for a topic, the topic produces no
   story that day.

So adding to `trusted` raises a source's priority; adding to
`untrusted` blocks it; reordering inside `trusted` shifts which
trusted source wins on overlapping coverage.

### Watched news topics (`news-topics.json`)

The `topics` array is the list of keyword strings the news fetcher
searches for each morning. Order doesn't affect priority — every
topic gets one story slot per day if a survivor exists.

| User intent | Operation |
|---|---|
| "Add `<topic>` to my news topics" / "follow `<topic>`" | Append to `topics` if not already present. |
| "Stop following `<topic>`" / "remove `<topic>`" | Drop matching entry (case-insensitive match, but preserve the original entry's case if you keep it). |
| "What topics do I follow" | Read and list. |
| "Rename `<old>` to `<new>`" | Find and replace one entry in place. |

Topics are passed verbatim to the news search; quoted strings (e.g.
`"AI capex"`) work as phrase searches.

### Watched stock tickers (`tickers.json`)

The array drives the `__Markets__` block in the morning briefing. Each
entry is a single object with one required field: `ticker`, the symbol
in **Yahoo Finance convention** — bare for US listings, with an exchange
suffix for international: `SU.PA`, `7203.T`, `0700.HK`, `RIO.L`.

The pipeline's ticker fetcher dispatches by suffix:

- **Bare ticker** (no dot) → quote API. Reliable and
  official; covers US listings only on the free tier.
- **Suffixed ticker** (has a dot) → yfinance via the local Python venv.
  Covers any market Yahoo Finance does, but unofficial — Yahoo can
  break the lib until maintainers patch.

Class shares like `BRK.B` should be entered as **`BRK-B`** (Yahoo's
hyphen form for share classes) so they route to the quote API instead
of being treated as a foreign exchange suffix.

| User intent | Operation |
|---|---|
| "Add `<ticker>` to my watchlist" / "track `<ticker>`" | **First `web_search` to resolve the ticker → company name** (see "Verify ticker ↔ company" below). Then append `{"ticker": "<ticker>"}` to the array if not already present. |
| "Stop tracking `<ticker>`" / "remove `<ticker>` from my watchlist" | Drop the matching entry (case-insensitive ticker match; preserve original case if you keep). No lookup needed. |
| "What tickers am I watching" / "show my watchlist" | Read and list each entry's `ticker`. No lookup needed. |
| "Move `<ticker>` to the top" | Reorder to index 0. (Order in the file is the order in the briefing table.) |
| "Replace `<old>` with `<new>`" | Same lookup rule as Add — `web_search` for `<new>` before writing. |

#### Verify ticker ↔ company

**Always `web_search` to ground the ticker ↔ company mapping before
writing the file or naming the company in your response.** This applies
in both directions:

- User gives a **ticker** (e.g. "Add GEV"): web_search "GEV stock
  ticker" → confirm GE Vernova → write tickers.json with `GEV` →
  respond `Added GEV (GE Vernova) to your watchlist.`
- User gives a **company name** (e.g. "Add GE Vernova" / "Add Schneider
  Electric"): web_search "GE Vernova stock ticker" → confirm `GEV`
  (or `SU.PA`) → write tickers.json → respond
  `Added GEV (GE Vernova) to your watchlist.`

The lookup is mandatory even for tickers you think you know. Your
training data may not match current ticker reality, and confidently-
asserted-but-wrong company names are the canonical failure mode here.
Canonical confusion sources:

- **GE family:** `GE` (General Electric, post-spinoff parent),
  `GEV` (GE Vernova, energy spin), `GEHC` (GE Healthcare, separate
  spin). All three trade independently — none inherits the others'
  symbol.
- **Class shares:** `GOOG` vs `GOOGL`, `BRK-A` vs `BRK-B` — different
  voting/economic rights, both real tickers.
- **Multi-exchange listings:** `SU.PA` (Schneider Electric, Euronext
  Paris primary) vs `SBGSF` (US OTC ADR). If the user names the
  company without specifying exchange, ask which they want before
  writing.

**On user correction, web_search before re-writing.** If the user
says "you got that wrong, I meant X," do a web_search to confirm
X's actual ticker before changing tickers.json. Don't pile a second
wrong assertion on the first.

#### Confirmation format for ticker changes

After adding or replacing, respond with the format `Added <TICKER>
(<Company Name>) to your watchlist.` — both the symbol and the
verified company name. After removing, ticker-only is fine
(`Removed GEV from your watchlist.`) since the company isn't in
the file.

### Calendar organizer mapping (`calendar-people.json`)

| User intent | Operation |
|---|---|
| "Map `<email>` to `<name>` in my calendar" | Add `email -> name` to `organizers`. |
| "Remove `<email>` from my calendar mapping" | Drop the key. |
| "Set the default calendar name to `<name>`" | Update `default`. |
| "What names are mapped in my calendar" | Read and list. |

Emails are stored lowercased; the calendar fetcher
lowercases before matching, so always lowercase on write.

## Verification

Before saving any edit, confirm:

1. The file is one of `news-source-prefs.json`, `news-topics.json`,
   `tickers.json`, or `calendar-people.json` under `~/daily-briefing/`.
   Editing anything else through this skill is out of scope.
2. The JSON parses after the edit (no trailing commas, no top-level
   key drift — only the documented top-level keys).
3. For source-prefs edits: the same domain does not appear in both
   `trusted` and `untrusted`. The latest action wins; remove from the
   other list.
4. For source-prefs edits where the user named a publisher rather
   than a domain: the inferred domain matches a well-known mapping,
   or the user has confirmed.
5. After writing, briefly summarize what changed (the operation, the
   target file, and the resulting priority order if relevant).

## When an operation reports an error

- The config file is missing → the briefing pipeline isn't set up on this
  machine. Tell the user which file is absent and point them at this
  skill's `README.md` for pipeline setup.
- The file doesn't parse as JSON → report that it is malformed and name the
  file. Leave it as it is; do not rewrite it from memory.
- A `web_search` for a ticker returns nothing conclusive → say the symbol
  couldn't be confirmed and ask the user which company they mean. Don't write
  an unverified ticker.
- The user names a publisher that maps to more than one domain → ask which one
  before writing.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

An empty `trusted`, `untrusted`, `topics`, `tickers` or `organizers` list means
nothing is configured yet — say so plainly ("you aren't tracking any tickers
yet") and offer to add the first entry.
