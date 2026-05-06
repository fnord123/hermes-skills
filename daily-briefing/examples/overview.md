# Morning Briefing — Architecture Overview

## What this is

A morning briefing pipeline that runs once a day under the user's
crontab, gathers calendar/weather/news/lottery data using plain shell
and Python, composes a fixed-format message, and delivers it to the
Discord `#daily-briefings` channel via webhook.

All files live in `~/daily-briefing/`.

## Why this is split between scripts and the agent

The deliberate design choice is **scripts do the boring deterministic
work; the agent doesn't need to be involved at all in generation**.
Reasons:

- Calendars, weather, news APIs are well-behaved data sources. Shell +
  jq + a little Python are far more robust and far cheaper than asking
  an LLM to do them.
- Cron-driven pipelines are easy to debug — one log file per script,
  plain exit codes, no token budgets, no context window failure modes.
- The Discord webhook delivers as a separate "Daily Briefing" identity.
  Hermes still **sees** the briefing land in `#daily-briefings` (per
  `DISCORD_ALLOW_BOTS=all`) and a per-channel prompt instructs it to
  absorb the content silently for context. If a memory subsystem is
  attached to Hermes, it retains the briefing's facts as a normal
  side-effect of that ingestion (same path any other message takes).
  So the agent gets context awareness without any code in this
  pipeline pushing it.

This means most "the briefing didn't show up today" failures will be
shell-debuggable from `/var/tmp/daily-briefing*.log`, not a context-
window problem inside the agent.

## High-level flow

```
                ┌──── user cron (06:00 PT, daily) ────┐
                │                                     │
                ▼                                     │
       morning-briefing.sh                            │
                │                                     │
   ┌────────────┼─────────────┬──────────────┐        │
   ▼            ▼             ▼              ▼        │
 fetch-      fetch-         fetch-        fetch_      │
 calendar    weather        news          jackpots    │
   │ (iCal)    │ (NWS)        │ (Brave)     │ (curl)  │
   ▼           ▼              ▼              ▼        │
 today's     Today line    news-dedup.py   "Mega      │
 events                    1 story/topic    Millions  │
                           trusted-first    $X | …"   │
   │           │              │              │        │
   └───────────┴──────────────┴──────────────┘        │
                │                                     │
                ▼                                     │
        compose BRIEFING                              │
                │                                     │
        ┌───────┴───────┐                             │
        ▼               ▼                             │
   archive/         Discord webhook                   │
   daily-briefing-  ──────►  #daily-briefings  ◄──────┘
   YYYY-MM-DD.md

                            (Hermes sees the post,
                             channel_prompts says
                             "ingest silently",
                             attached memory — if
                             any — retains via the
                             normal ingestion path.)
```

## Components

### `morning-briefing.sh` — Orchestrator

The entry point. Loads `~/daily-briefing/.env`, runs the four fetchers
in sequence, composes the message, archives a copy, and posts it to
Discord. Logs everything to `/var/tmp/daily-briefing.log` with
timestamped `BRIEFING` lines.

**Notable behavior:**

- `set -euo pipefail` — any unhandled error aborts.
- Each fetcher is allowed to fail without aborting the briefing; the
  failed line becomes `unavailable` or a stub in the output. A missing
  weather line shouldn't kill the whole message.
- Briefing is archived to `archive/daily-briefing-YYYY-MM-DD.md` before
  the webhook post — so even if Discord delivery fails, the day's
  briefing is preserved on disk.
- Webhook content is capped at 1900 chars (Discord's hard limit is
  2000). Real briefings are usually 800–1500 chars; this is a guard,
  not a real concern.
- Posts as `username: "Daily Briefing"` so it's visually distinct from
  the Hermes bot in the channel.

### `fetch-calendar.sh` — Calendar

Pulls the user's iCal feed at `$GCAL_ICAL_KEY`, parses it with
**stdlib-only Python** (no `icalendar` package), and emits today's
events in the briefing format.

**What it gets right (worth not breaking):**

- **Full RRULE expansion** for `DAILY`, `WEEKLY` (with `BYDAY` / `WKST` /
  `INTERVAL`), `MONTHLY` (`BYMONTHDAY`), and `YEARLY` recurrences.
- **`EXDATE` exclusions** are respected.
- **`RECURRENCE-ID` modified instances** override their corresponding
  occurrence — so if "Pilates every Wed" was rescheduled this week, the
  modified instance shows.
- **Multi-day all-day events** are formatted as `Day N of Total (all
  day)` on each occurrence day.
- **Timezone** anchored to `America/Los_Angeles` regardless of the iCal
  feed's TZID.
- **Person attribution**: `ORGANIZER` email is looked up in
  `calendar-people.json` to produce the `[Name]` prefix. Falls back to
  the file's `default` if no match.
- **`--days=N` flag** for testing — emits multi-day grouped output
  instead of just today's lines.
- **Cancellations dropped** (`STATUS:CANCELLED` events filtered out).

### `fetch-weather.sh` — Weather

Calls NWS API at `https://api.weather.gov/gridpoints/$NWS_GRIDPOINT/forecast`
(set `NWS_GRIDPOINT=OFFICE/X,Y` in `.env` — see the README for how to
look up your local gridpoint). Pairs each daytime period with the next
nighttime period and emits two lines:

```
Today: Partly Sunny high 76F low 52F, rain 11%
Tomorrow: ...
```

The orchestrator only uses the `Today:` line. No API key required (NWS
is free for `User-Agent`-identified clients).

### `fetch-news.sh` — News pipeline (orchestrator side)

Iterates the keywords in `news-topics.json` and calls Brave News
Search (`https://api.search.brave.com/res/v1/news/search`) for each,
with a freshness window mapped from `--days`:

| `--days` | Brave `freshness` |
|---|---|
| ≤ 1 | `pd` (past day) |
| ≤ 7 | `pw` (past week, default) |
| > 7 | `pm` (past month) |

Up to 5 stories per topic are collected as `{url, title, topic}` JSON,
then piped to `news-dedup.py` for selection. Output is one bullet per
topic in the form `- {Title} — {URL}`.

**Requires `$BRAVE_API_KEY`** (loaded from `~/daily-briefing/.env`).

### `news-dedup.py` — Story selection

The brains of the news pipeline. Reads stories from stdin, applies
trusted/untrusted filters, and emits at most one story per topic.

Filtering pipeline:

1. **Already-shown filter.** Drops any URL that appears in
   `news-seen.json`.
2. **Cross-topic dedup within a run.** If the same URL surfaced under
   two topics, only the first topic gets it.
3. **Source policy** (`pick_best_for_topic`):
   - Drop any candidate whose domain is in `untrusted`.
   - If any survivor's domain is in `trusted`, pick the one with the
     lowest index in `trusted` (highest priority).
   - Otherwise pick the first remaining candidate (Brave's relevance
     order).
   - If everything was untrusted → no story for that topic today.

After picking, **all URLs from this run** (not just picked ones) are
appended to `news-seen.json`, which is rolling-trimmed to the last 500
entries. This prevents non-picked stories from resurfacing on the next
run as "new" — a subtle point. If you only logged picked URLs, every
unpicked story would re-enter the candidate pool tomorrow with the same
ranking.

### `source-prefs.py` — Interactive source ordering

A `curses` TUI for editing `news-source-prefs.json`. Items above the
separator are trusted (in priority order); items below are untrusted.
Invoked manually via `python3 source-prefs.py`, or automatically by
`news-dedup.py` when run with `--debug` (so you can re-rank as you see
new domains appear).

## State files

| File | Purpose | Schema |
|---|---|---|
| `news-topics.json` | Keywords searched on Brave each run. | `{ "topics": ["...", ...] }` |
| `news-source-prefs.json` | Source ranking + blocklist. | `{ "trusted": ["domain", ...], "untrusted": [...] }` |
| `news-seen.json` | Rolling window of URLs already considered (last 500). | `["url", ...]` |
| `calendar-people.json` | Organizer email → display name. | `{ "default": "...", "organizers": { "email": "Name" } }` |
| `archive/daily-briefing-YYYY-MM-DD.md` | Each day's composed briefing, written before webhook delivery. | Plain markdown. |

## External dependencies

| Source | What it provides | Auth |
|---|---|---|
| Google Calendar iCal | Today's events (incl. recurring) | `$GCAL_ICAL_KEY` |
| NWS API | US local forecast (gridpoint configured in `.env`) | None (`User-Agent` header) |
| Brave News API | Topic-keyword search | `$BRAVE_API_KEY` |
| Lottery Valley | Mega Millions / Powerball jackpots | None — page is a JS-rendered SPA, scraped via headless Chrome (`agent-browser`) |
| Discord webhook | Final delivery to `#daily-briefings` | `$BRIEFING_WEBHOOK_URL` |

System tools required: `bash`, `curl`, `jq`, `python3` (stdlib only),
[`agent-browser`](https://github.com/vercel-labs/agent-browser) (npm-installed
headless-Chrome CLI used only for the JS-rendered jackpot page; install via
`npm install -g agent-browser && agent-browser install`).

## Modes

`morning-briefing.sh` supports `--dry-run` (alias `-n`): runs the full
fetch + compose pipeline, prints the composed briefing to stdout, and
**skips the archive write and the webhook post**. Useful for testing
without polluting the archive directory or pinging Discord.

## Logs & operational details

- Each script writes to `/var/tmp/daily-briefing*.log` with timestamped
  entries.
- Failure mode on each fetcher: log the error, return an empty/error
  stub, let the orchestrator continue. The briefing always sends, even
  if degraded.
- Dedup state (`news-seen.json`) is the only persistent mutable state
  the pipeline writes; everything else is recomputed every run.
- Briefings are archived to `archive/` before the webhook post, so the
  archive is the source of truth even if Discord delivery fails.

## Cron

Intended schedule: **once a day, around the time you want the briefing
to land**. Example user-crontab entry:

```
TZ=America/Los_Angeles
0 6 * * *  $HOME/daily-briefing/morning-briefing.sh
```

Without the `TZ=` line, cron uses the system timezone. The
`morning-briefing.sh` script anchors all date math to the timezone set
inside it (default `America/Los_Angeles`) regardless of when cron
fires, so adjust both the cron schedule *and* the `TZ=` line at the top
of `morning-briefing.sh` together if you want a different timezone.

## How this relates to Hermes

The pipeline is **fully decoupled** from Hermes — no Hermes CLI is
invoked anywhere in the scripts. The integration is one-way and
indirect:

1. Webhook posts the briefing to `#daily-briefings`.
2. Hermes' Discord adapter sees the message (with `DISCORD_ALLOW_BOTS=all`).
3. The `channel_prompts` entry for the briefings channel in
   `~/.hermes/config.yaml` instructs Hermes to absorb the content for
   context but not respond.
4. If a memory subsystem is configured for Hermes (e.g. Hindsight, or
   whatever Hermes memory plugin you use), it retains the briefing's
   facts as a normal side-effect of message ingestion — same path as
   any other ingested content.
5. Same-day follow-ups in the channel work directly from Hermes'
   session context. Cross-day follow-ups work via the configured
   memory subsystem's recall, if any.

There is **no skill** in Hermes that drives generation of the briefing.
The companion skill at `~/.hermes/skills/productivity/daily-briefing/`
exists only to handle natural-language management of the source-prefs
and topics files (e.g., "add Reuters to my trusted list above
Bloomberg") and to enumerate which files in `~/daily-briefing/` Hermes
is allowed to read or write.

## Security boundary

The `~/daily-briefing/.env` file holds API keys for Brave, GCal, and
the Discord webhook. **Hermes does not have these keys.** The
companion skill instructs Hermes never to read this file or any
`*.log` file under `~/daily-briefing/`. This is a policy boundary, not
a UNIX-permission boundary — Hermes runs as the same user. If the LLM
were jailbroken or bad instructions were injected, the only thing
stopping it from leaking the file is the policy in the skill.
