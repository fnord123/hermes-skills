# daily-briefing — setup

A morning briefing pipeline that runs once a day under your crontab, gathers calendar / weather / news / lottery / market data, composes a fixed-format message, and posts it to a Discord channel via webhook. The accompanying [`SKILL.md`](./SKILL.md) is the **runtime companion** — it lets the Hermes agent edit the pipeline's policy files in plain English ("add Reuters to my trusted sources", "stop tracking GLW", etc.). The agent doesn't generate briefings; cron does.

This README is for the human standing the pipeline up. The skill never reads it.

## Heads up: this is "as-is" and takes some assembly

This skill bundles a working pipeline but doesn't pretend setup is push-button. You'll need accounts and API keys with three providers (Brave, Twelve Data, Discord), a Google Calendar iCal URL, an NWS gridpoint lookup, a Python venv for `yfinance`, a per-channel prompt edit in your Hermes config, and a cron job. Plan for 30–60 minutes the first time; less if you're already comfortable with all of those moving parts. There's no installer.

### Why the pipeline lives outside the agent

Originally I tried to have the Hermes agent itself produce the briefing — fetch calendar, look up weather, run news searches, compose, post. With **small local models** (Qwen-27B-class on consumer hardware) this never worked reliably:

- The agent would forget steps, skip the dedup logic, produce inconsistent formatting day-to-day.
- Small models have a strong pull toward generic `terminal` invocations and improvise hallucinated CLI commands when typed tools don't exactly fit.
- Even when it ran end-to-end, it was slow (multiple LLM calls × per-day cost) and brittle (one bad tool selection wrecked the whole briefing).

What I ended up with — and what this skill ships — is **scripts do the boring deterministic work, the agent only does what scripts can't**:

- **Shell + jq + a few Python helpers** are far more robust and far cheaper than asking an LLM to do them. The whole pipeline runs in under a second total wall-clock once it's warm.
- **Cron-driven pipelines are easy to debug**: one log per fetcher, plain exit codes, no token budgets, no context-window failure modes.
- **The agent's job is the human-facing edge of the loop**: managing the JSON config files when the user wants to tweak what's in the briefing ("add NVDA to my watchlist") and answering follow-up questions about the day's posted briefing using normal conversation context. Those are things small models do well — short, single-purpose, deterministic-ish edits and Q&A.

If you're running on a frontier model (Claude / GPT-4 / Gemini), you could likely have the agent generate the whole briefing dynamically. If you're running on small local models like the rest of this repo targets, **bake the pipeline into shell scripts** and let the agent do follow-up — that's what works.

## Overview

```
                 ┌─── user cron (06:00 daily) ────┐
                 │                                │
                 ▼                                │
       morning-briefing.sh                        │
                 │                                │
   ┌────┬────┬───┴────┬────┬───────┐              │
   ▼    ▼    ▼        ▼    ▼       ▼              │
 cal   wx   news    jackpots tickers              │
 (iCal)(NWS)(Brave) (browser)(TwelveData/yf)      │
   │    │    │        │      │                    │
   └────┴────┴────────┴──────┘                    │
                 │                                │
                 ▼                                │
         compose BRIEFING                         │
                 │                                │
        ┌────────┴───────┐                        │
        ▼                ▼                        │
   archive/         Discord webhook  ─────────────┘
   YYYY-MM-DD.md   ──►  #daily-briefings
```

Hermes sees the post in the channel (via `DISCORD_ALLOW_BOTS=all` plus a per-channel prompt instructing it to ingest silently). The day's content is then in conversation context for any follow-up turns in that channel — and if you have a memory subsystem attached to Hermes (e.g. Hindsight, or whatever Hermes plugin you use), it retains the briefing's facts the same way it retains any other ingested message. Net result: the agent has automatic context for the day's news / calendar / markets without being involved in producing the briefing itself.

Most "today's briefing didn't show up" failures are shell-debuggable from `/var/tmp/daily-briefing*.log` — not a context-window problem inside the agent.

See [`examples/overview.md`](./examples/overview.md) for the full architecture deep-dive (debugging tips, log file locations, memory-system integration, etc.).

## Prerequisites

- **Linux/macOS** with cron (or a systemd timer)
- **`bash`, `curl`, `jq`, `python3`** (3.10+ recommended for `zoneinfo`)
- **A Discord server** where you can create a webhook
- **A Hermes Agent** install with the Discord platform enabled, listening to the channel

API keys you'll need (all free tiers are sufficient):

| Service | Used for | Free-tier limits | Sign-up |
|---|---|---|---|
| **Brave Search** | News headlines per topic | 2,000 queries/month | https://brave.com/search/api/ |
| **Twelve Data** | US ticker prices in `__Markets__` block | 800 calls/day, 8/min | https://twelvedata.com/pricing |
| **Google Calendar** | Today's calendar events | n/a | calendar.google.com → Settings → "Secret address in iCal format" |

`yfinance` (used for non-US tickers) is keyless — it scrapes Yahoo Finance.

## Setup

### 1. Install the skill

```bash
hermes skills install fnord123/hermes-skills/daily-briefing
```

This places `SKILL.md` and `examples/` under `~/.hermes/skills/daily-briefing/`. The skill is now active in Hermes; the rest of these steps stand up the cron pipeline that the skill talks about.

### 2. Copy the pipeline scripts to your home dir

```bash
mkdir -p ~/daily-briefing
cp -r ~/.hermes/skills/daily-briefing/examples/. ~/daily-briefing/
chmod +x ~/daily-briefing/*.sh ~/daily-briefing/*.py
mv ~/daily-briefing/.env.example ~/daily-briefing/.env
```

(The skill assumes the pipeline lives at `~/daily-briefing/`. If you put it somewhere else, update the path references in `SKILL.md` accordingly — but the path convention is what makes natural-language edits like "add NVDA to my watchlist" land in the right place.)

### 3. Get API keys and fill in `.env`

Edit `~/daily-briefing/.env` (created in step 2). Required:

```
GCAL_ICAL_KEY=https://calendar.google.com/calendar/ical/<your-secret>/basic.ics
BRAVE_API_KEY=<your Brave key>
TWELVE_DATA_API_KEY=<your Twelve Data key>
NWS_GRIDPOINT=OKX/33,42       # NYC; see step 4 to find yours
BRIEFING_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
```

**Discord webhook**: in your server, Channel → Settings → Integrations → Webhooks → New Webhook → name it "Daily Briefing" → copy the webhook URL. Anyone with the URL can post to that channel as the webhook user, so treat it as a credential.

### 4. Find your NWS gridpoint

`fetch-weather.sh` uses the US National Weather Service forecast API, which is free and keyless but requires a "gridpoint" identifier. To find yours:

```bash
curl -s 'https://api.weather.gov/points/<lat>,<lon>' | jq -r .properties.forecastGridData
```

Replace `<lat>,<lon>` with your coordinates (e.g. `40.7128,-74.0060` for New York, NY → returns `OKX/33,42`). The response includes a URL ending with `/gridpoints/<OFFICE>/<X>,<Y>` — that's your `NWS_GRIDPOINT` value. Set it in `.env`.

If you're outside the US, swap `fetch-weather.sh` for an open-meteo or openweathermap variant that emits the same one-line `Today: ... high NF low NF, rain N%` format.

### 5. Set up the Python venv (for `yfinance`)

Only needed if `tickers.json` will include non-US tickers (anything with a dot suffix like `SU.PA`, `7203.T`).

```bash
cd ~/daily-briefing
python3 -m venv venv
./venv/bin/pip install --quiet yfinance
```

`fetch-tickers.sh` automatically uses `./venv/bin/python3` if it exists; otherwise it falls back to system python and just leaves international tickers as DATA UNAVAILABLE.

### 6. Configure Hermes for ingestion

In your Hermes `config.yaml`:

1. Make sure the Discord platform is enabled (likely already is).
2. Set or confirm `DISCORD_ALLOW_BOTS=all` so Hermes sees webhook posts (those count as bot messages).
3. Add a channel-prompt for your briefings channel that tells the agent how to handle the daily post. Example:

   ```yaml
   discord:
     channel_prompts:
       "<channel-id-of-briefings>": |
         Daily briefings post here every morning. Ingest the content
         silently for context — do NOT reply with a text message.
         If you have a memory subsystem attached, it will retain the
         facts as a side effect of ingestion.
   ```

Replace `<channel-id-of-briefings>` with the channel ID where the webhook posts.

### 7. Add the cron job

```bash
crontab -e
```

Add (adjust the time and timezone to your liking):

```
# Daily briefing at 06:00 ET
TZ=America/New_York
0 6 * * * $HOME/daily-briefing/morning-briefing.sh
```

If your platform's cron doesn't honor a `TZ=` line, drop it and translate the time to your system's clock instead (e.g. 06:00 ET → 11:00 UTC most of the year, 10:00 UTC during EDT). Whichever zone the cron *fires* in, the briefing's date formatting and archive filenames are anchored to `BRIEFING_TZ` from `.env` (default `America/New_York`).

### 8. Optional: jackpot fetching

The default `morning-briefing.sh` fetches Powerball + Mega Millions jackpots via [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) (drives a headless Chrome to scrape JS-rendered lottery pages). If you don't care about jackpots, delete the `fetch_jackpots()` function from `morning-briefing.sh` and replace `JACKPOTS=$(fetch_jackpots)` with `JACKPOTS=""` (then drop the line from the briefing template). If you want jackpots, install `agent-browser` per its README:

```bash
npm install -g @vercel-labs/agent-browser
```

The script gracefully degrades to "unavailable" if `agent-browser` isn't found.

### 9. Test

```bash
cd ~/daily-briefing
set -a; source .env; set +a
./morning-briefing.sh --dry-run
```

This fetches everything but skips the Discord post. If the output looks right, your next cron tick will deliver it for real.

## Customization

**Want a different time or timezone?** Adjust the cron schedule (and its `TZ=` line) in step 7, and set `BRIEFING_TZ` in `.env` to your IANA zone (e.g. `BRIEFING_TZ=America/Los_Angeles`, `Europe/London`, `Asia/Tokyo`).

**Want different sections?** The composer in `morning-briefing.sh` is straightforward bash — add or remove sections by editing the BRIEFING string. Each new section just needs a fetcher script that emits markdown.

**Want a different output destination?** Replace `BRIEFING_WEBHOOK_URL` with a Slack / Telegram / email / custom HTTP target. The post step is one `curl` near the bottom of `morning-briefing.sh`.

**Want to fill in tickers/news topics/etc. via Hermes?** The skill is now active. Try:
- "Add NVDA and AAPL to my watchlist."
- "Add Reuters and the Atlantic to my trusted news sources."
- "Follow the AI capex topic in my news."
- "Map alex@example.com to Alex in my calendar people list."

The agent edits the JSON files; the next briefing reflects the change.

## Files in this skill

```
daily-briefing/
├── SKILL.md                         model-facing companion (Hermes uses this)
├── README.md                        this file
└── examples/
    ├── morning-briefing.sh          orchestrator (entry point for cron)
    ├── fetch-calendar.sh            Google Calendar iCal → today's events
    ├── fetch-news.sh                Brave News API → 1 story per topic
    ├── fetch-tickers.sh             Bash wrapper for fetch-tickers.py
    ├── fetch-tickers.py             Twelve Data + yfinance hybrid
    ├── fetch-weather.sh             NWS forecast API → 1-line summary
    ├── news-dedup.py                source-pref ranking + dedup
    ├── source-prefs.py              optional curses TUI for prefs
    ├── news-topics.json             {"topics": [...]}
    ├── news-source-prefs.json       {"trusted": [...], "untrusted": [...]}
    ├── tickers.json                 [{"ticker": "..."}]
    ├── calendar-people.json         {"default": ..., "organizers": {...}}
    ├── .env.example                 template — copy to .env, fill in keys
    └── overview.md                  full architecture / debugging guide
```

## Roadmap

- **Non-US weather** — currently NWS-only. Open-meteo support would make this work globally.
- **Multi-channel routing** — post different sections to different channels.
- **Per-day topic rotations** — e.g. tech news weekdays, sports weekends.

PRs welcome via the parent [hermes-skills](https://github.com/fnord123/hermes-skills) repo.
