#!/usr/bin/env bash
# morning-briefing.sh — Compile and post the daily morning briefing.
#
# Called once a day from user cron, typically early morning local time.
# Fetches calendar, weather, news, and jackpots; composes the briefing;
# posts to Discord via webhook; archives a copy under archive/.
#
# Timezone for date formatting + the cron schedule's wall-clock anchor
# defaults to America/New_York; override by setting BRIEFING_TZ in
# .env (e.g. BRIEFING_TZ=America/Los_Angeles).
#
# Usage:
#   morning-briefing.sh             # normal run: fetch, compose, archive, post
#   morning-briefing.sh --dry-run   # fetch & compose, print to stdout, do
#                                   #   NOT archive and do NOT post
#
# Requires: curl, jq, python3
# Env vars (in ~/daily-briefing/.env):
#   GCAL_ICAL_KEY        — Google Calendar secret iCal URL
#   BRAVE_API_KEY        — Brave Search News API key
#   BRIEFING_WEBHOOK_URL — Discord webhook URL for #daily-briefings

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        --help|-h)
            awk 'NR==1{next} /^#/{sub(/^#[[:space:]]?/,""); print; next} /^[^#[:space:]]/{exit}' "$0"
            exit 0 ;;
        *) echo "ERROR: Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
LOG_FILE="/var/tmp/daily-briefing.log"
ARCHIVE_DIR="${SCRIPT_DIR}/archive"
AGENT_BROWSER="${HOME}/.npm-global/bin/agent-browser"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] BRIEFING $*" | tee -a "$LOG_FILE"; }

# Fetch jackpots via agent-browser (vercel-labs/agent-browser). The lottery
# pages are JS-rendered SPAs that don't expose values to plain curl, so we
# drive a headless Chrome to take an accessibility-tree snapshot and parse
# StaticText nodes from it. Returns "unavailable" on any failure — never
# blocks the briefing.
fetch_jackpots() {
    if ! command -v "$AGENT_BROWSER" &>/dev/null && [[ ! -x "$AGENT_BROWSER" ]]; then
        echo "unavailable"
        return
    fi
    local snap_file
    snap_file=$(mktemp)
    "$AGENT_BROWSER" open https://www.lotteryvalley.com/ >>"$LOG_FILE" 2>&1 || true
    "$AGENT_BROWSER" wait 3000 >>"$LOG_FILE" 2>&1 || true
    "$AGENT_BROWSER" snapshot >"$snap_file" 2>>"$LOG_FILE" || true
    "$AGENT_BROWSER" close >>"$LOG_FILE" 2>&1 || true

    SNAP_FILE="$snap_file" python3 <<'PYEOF' 2>/dev/null || echo "unavailable"
import os, re
with open(os.environ['SNAP_FILE']) as f:
    text = f.read()
# lotteryvalley.com renders each game as a link whose accessible name
# includes "CURRENT JACKPOT $XXXM". Match that pattern; first hit per
# game wins.
pat = re.compile(r'link "(Powerball|Mega Millions)\b[^"]*?CURRENT JACKPOT \$([0-9.]+)\s*M')
games = {}
for m in pat.finditer(text):
    name, val = m.group(1), float(m.group(2))
    if name not in games and val > 0:
        games[name] = val
if len(games) != 2:
    print("unavailable")
    raise SystemExit
def fmt(v):
    return f"${v:g}M"  # 180.0 -> $180M, 28.17 -> $28.17M
mm, pb = games['Mega Millions'], games['Powerball']
if mm >= pb:
    print(f"Mega Millions {fmt(mm)} | Powerball {fmt(pb)}")
else:
    print(f"Powerball {fmt(pb)} | Mega Millions {fmt(mm)}")
PYEOF
    rm -f "$snap_file"
}

# Load env vars
if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
fi

# Timezone for date formatting in the briefing. Override via .env.
BRIEFING_TZ="${BRIEFING_TZ:-America/New_York}"

mkdir -p "$ARCHIVE_DIR"

log "=== START ==="

# ── Step 1: Calendar ──────────────────────────────────────────────────────────
log "STEP calendar"
CALENDAR=$(bash "$SCRIPT_DIR/fetch-calendar.sh" 2>>"$LOG_FILE") \
    || { log "ERROR calendar failed"; CALENDAR="Calendar fetch failed"; }
CALENDAR=$(printf '%s' "$CALENDAR" | sed '/[^[:space:]]/,$!d')
log "calendar lines=$(echo "$CALENDAR" | wc -l)"

# ── Step 2: Weather ───────────────────────────────────────────────────────────
log "STEP weather"
WEATHER_RAW=$(bash "$SCRIPT_DIR/fetch-weather.sh" 2>>"$LOG_FILE") \
    || { log "ERROR weather failed"; WEATHER_RAW=""; }
WEATHER_TODAY=$(echo "$WEATHER_RAW" | grep "^Today:" | sed 's/^Today: //' || echo "unavailable")
log "weather=${WEATHER_TODAY}"

# ── Step 3: News ──────────────────────────────────────────────────────────────
log "STEP news"
NEWS=$(bash "$SCRIPT_DIR/fetch-news.sh" 2>>"$LOG_FILE") || { log "WARN news failed"; NEWS=""; }
log "news lines=$(echo "$NEWS" | grep -c '^- ' || echo 0)"

# ── Step 4: Jackpots ──────────────────────────────────────────────────────────
log "STEP jackpots"
JACKPOTS=$(fetch_jackpots) || JACKPOTS="unavailable"
log "jackpots=${JACKPOTS}"

# ── Step 5: Tickers ───────────────────────────────────────────────────────────
log "STEP tickers"
TICKERS=$(bash "$SCRIPT_DIR/fetch-tickers.sh" 2>>"$LOG_FILE") \
    || { log "WARN tickers failed"; TICKERS=""; }
log "tickers lines=$(echo "$TICKERS" | wc -l)"

# ── Step 6: Compose briefing ──────────────────────────────────────────────────
TODAY=$(TZ="$BRIEFING_TZ" date "+%A, %B %-d, %Y")
ARCHIVE_DATE=$(TZ="$BRIEFING_TZ" date "+%Y-%m-%d")

_cal=$(printf '%s' "$CALENDAR" | sed 's/^- /· /')
_news=$(printf '%s' "$NEWS" | sed 's/^- /· /')

BRIEFING="__Today__
· ${TODAY}
· Weather: ${WEATHER_TODAY}
· Jackpots: ${JACKPOTS}
· Morning status: No immediate concerns

__Calendar__
${_cal}"

if [[ -n "$TICKERS" ]]; then
    BRIEFING="${BRIEFING}

${TICKERS}"
fi

if [[ -n "$NEWS" ]]; then
    BRIEFING="${BRIEFING}

__Alerts & News__
${_news}"
fi
unset _cal _news

log "briefing_chars=${#BRIEFING}"

# ── Step 7: Dry-run early exit ────────────────────────────────────────────────
if (( DRY_RUN )); then
    log "dry-run: skipping archive and webhook post"
    log "=== END (dry-run) ==="
    printf '%s\n' "$BRIEFING"
    exit 0
fi

# ── Step 8: Archive ───────────────────────────────────────────────────────────
ARCHIVE_FILE="${ARCHIVE_DIR}/daily-briefing-${ARCHIVE_DATE}.md"
printf '%s\n' "$BRIEFING" > "$ARCHIVE_FILE"
log "archived ${ARCHIVE_FILE}"

# ── Step 9: Post to Discord (stub in channel, full briefing in thread) ───────
# Three-step flow when BRIEFING_BOT_TOKEN is set:
#   1. Post a one-line stub to the channel (?wait=true to get message_id back).
#   2. Create a date-named public thread anchored to the stub via the bot.
#   3. Post the full briefing into the thread via webhook ?thread_id=<id>.
# Public (not private) threads — private threads require a per-day member-add
# whose "X added Y to the thread" system message kicks Hermes into an
# empty-response retry loop in this channel.
# The bot needs CREATE_PUBLIC_THREADS on the parent channel.
#
# Without BRIEFING_BOT_TOKEN we can't create threads, so we fall back to
# posting the full briefing straight to the channel.
log "STEP post"
if [[ -z "${BRIEFING_WEBHOOK_URL:-}" ]]; then
    log "ERROR BRIEFING_WEBHOOK_URL not set; skipping post"
    exit 1
fi

# Discord rejects messages > 2000 chars; we'd need to chunk if exceeded.
# Briefings are usually 800-1500 chars, so this is a guard rather than a
# realistic concern.
if (( ${#BRIEFING} > 1900 )); then
    log "WARN briefing length ${#BRIEFING} > 1900; truncating"
    BRIEFING="${BRIEFING:0:1900}"
fi

RESPONSE_FILE=$(mktemp)
trap 'rm -f "$RESPONSE_FILE"' EXIT

if [[ -n "${BRIEFING_BOT_TOKEN:-}" ]]; then
    STUB_CONTENT="**Daily Briefing — ${TODAY}**"
    STUB_PAYLOAD=$(jq -n --arg content "$STUB_CONTENT" \
        '{content: $content, username: "Daily Briefing"}')
    HTTP_CODE=$(curl -sS -m 15 \
        -X POST \
        -H "Content-Type: application/json" \
        -o "$RESPONSE_FILE" \
        -w "%{http_code}" \
        -d "$STUB_PAYLOAD" \
        "${BRIEFING_WEBHOOK_URL}?wait=true" 2>>"$LOG_FILE" || echo "000")
    if [[ ! "$HTTP_CODE" =~ ^2 ]]; then
        log "ERROR stub post failed http=${HTTP_CODE}"
        exit 1
    fi
    log "stub posted http=${HTTP_CODE}"

    MESSAGE_ID=$(jq -r '.id // empty' "$RESPONSE_FILE" 2>>"$LOG_FILE")
    CHANNEL_ID=$(jq -r '.channel_id // empty' "$RESPONSE_FILE" 2>>"$LOG_FILE")
    if [[ -z "$MESSAGE_ID" || -z "$CHANNEL_ID" ]]; then
        log "ERROR couldn't extract message_id/channel_id from stub response"
        exit 1
    fi

    THREAD_PAYLOAD=$(jq -n --arg name "$ARCHIVE_DATE" \
        '{name: $name, auto_archive_duration: 1440}')
    THREAD_HTTP=$(curl -sS -m 10 \
        -X POST \
        -H "Authorization: Bot $BRIEFING_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -o "$RESPONSE_FILE" \
        -w "%{http_code}" \
        -d "$THREAD_PAYLOAD" \
        "https://discord.com/api/v10/channels/${CHANNEL_ID}/messages/${MESSAGE_ID}/threads" \
        2>>"$LOG_FILE" || echo "000")
    if [[ ! "$THREAD_HTTP" =~ ^2 ]]; then
        log "ERROR thread creation failed http=${THREAD_HTTP}"
        exit 1
    fi
    THREAD_ID=$(jq -r '.id // empty' "$RESPONSE_FILE" 2>>"$LOG_FILE")
    if [[ -z "$THREAD_ID" ]]; then
        log "ERROR thread response missing id"
        exit 1
    fi
    log "thread created '${ARCHIVE_DATE}' id=${THREAD_ID}"

    BRIEFING_PAYLOAD=$(jq -n --arg content "$BRIEFING" \
        '{content: $content, username: "Daily Briefing"}')
    HTTP_CODE=$(curl -sS -m 15 \
        -X POST \
        -H "Content-Type: application/json" \
        -o /dev/null \
        -w "%{http_code}" \
        -d "$BRIEFING_PAYLOAD" \
        "${BRIEFING_WEBHOOK_URL}?wait=true&thread_id=${THREAD_ID}" 2>>"$LOG_FILE" || echo "000")
    if [[ ! "$HTTP_CODE" =~ ^2 ]]; then
        log "ERROR briefing post to thread failed http=${HTTP_CODE}"
        exit 1
    fi
    log "briefing posted to thread http=${HTTP_CODE}"
else
    log "WARN BRIEFING_BOT_TOKEN not set; posting full briefing to channel without thread"
    PAYLOAD=$(jq -n --arg content "$BRIEFING" \
        '{content: $content, username: "Daily Briefing"}')
    HTTP_CODE=$(curl -sS -m 15 \
        -X POST \
        -H "Content-Type: application/json" \
        -o /dev/null \
        -w "%{http_code}" \
        -d "$PAYLOAD" \
        "$BRIEFING_WEBHOOK_URL" 2>>"$LOG_FILE" || echo "000")
    if [[ "$HTTP_CODE" =~ ^2 ]]; then
        log "posted http=${HTTP_CODE}"
    else
        log "ERROR webhook post failed http=${HTTP_CODE}"
        exit 1
    fi
fi

log "=== END ==="
