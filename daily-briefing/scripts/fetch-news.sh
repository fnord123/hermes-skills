#!/usr/bin/env bash
# fetch-news.sh — Search for new stories matching watched keywords via Brave News API
# Usage: bash tools/fetch-news.sh [--days=N] [--debug]
# Default lookback: 7 days (maps to Brave freshness=pw). Dedup prevents re-reporting.

set -eo pipefail

LOG_FILE="/var/tmp/daily-briefing-news.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] NEWS $*" >> "$LOG_FILE"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPICS_FILE="$SCRIPT_DIR/news-topics.json"

# Parse arguments
DAYS=7
DEBUG_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --help)
      awk 'NR==1{next} /^#/{sub(/^#[[:space:]]?/,""); print; next} /^[^#[:space:]]/{exit}' "$0"
      exit 0 ;;
    --days=*) DAYS="${arg#--days=}" ;;
    --debug)  DEBUG_FLAG="--debug" ;;
  esac
done

# Map days to Brave freshness value
if [ "$DAYS" -le 1 ]; then
  FRESHNESS="pd"
elif [ "$DAYS" -le 7 ]; then
  FRESHNESS="pw"
else
  FRESHNESS="pm"
fi

# Load .env for API keys if not already set (morning-briefing.sh exports them
# already; this is for direct invocation).
ENV_FILE="${SCRIPT_DIR}/.env"
if [ -f "$ENV_FILE" ] && [ -z "${BRAVE_API_KEY:-}" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

if [ -z "${BRAVE_API_KEY:-}" ]; then
  log "WARN BRAVE_API_KEY not set, skipping"
  echo "Warning: BRAVE_API_KEY not set, skipping news fetch" >&2
  exit 0
fi

if ! command -v jq &>/dev/null; then
  log "WARN jq not found, skipping"
  echo "Warning: jq not found, skipping news fetch" >&2
  exit 0
fi

if [ ! -f "$TOPICS_FILE" ]; then
  log "WARN topics file not found: $TOPICS_FILE"
  echo "Warning: $TOPICS_FILE not found, skipping news fetch" >&2
  exit 0
fi

TOPIC_COUNT=$(jq '.topics | length' "$TOPICS_FILE" 2>/dev/null || echo 0)
log "=== START days=${DAYS} freshness=${FRESHNESS} topics=${TOPIC_COUNT} ==="

# Fetch stories from Brave for each topic, collect as JSON array
TMP_STORIES=$(mktemp)
trap 'rm -f "$TMP_STORIES"' EXIT
echo "[]" > "$TMP_STORIES"

stories_fetched=0
topics_ok=0
topics_failed=0

while IFS= read -r TOPIC; do
  [ -z "$TOPIC" ] && continue

  ENCODED=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$TOPIC")

  RESPONSE=$(curl -sf \
    "https://api.search.brave.com/res/v1/news/search?q=${ENCODED}&freshness=${FRESHNESS}&count=5" \
    -H "X-Subscription-Token: $BRAVE_API_KEY" \
    -H "Accept: application/json" \
    2>/dev/null || true)

  if [ -z "$RESPONSE" ]; then
    log "topic=\"${TOPIC}\" result=empty_response"
    topics_failed=$((topics_failed + 1))
    continue
  fi

  # Extract url+title+topic and merge into stories array
  NEW_STORIES=$(echo "$RESPONSE" | jq --arg topic "$TOPIC" '[.results[]? | {url: .url, title: .title, topic: $topic}]' 2>/dev/null || echo "[]")
  new_count=$(echo "$NEW_STORIES" | jq 'length' 2>/dev/null || echo 0)
  log "topic=\"${TOPIC}\" stories=${new_count}"
  stories_fetched=$((stories_fetched + new_count))
  topics_ok=$((topics_ok + 1))

  jq -s '.[0] + .[1]' "$TMP_STORIES" <(echo "$NEW_STORIES") > "${TMP_STORIES}.new"
  mv "${TMP_STORIES}.new" "$TMP_STORIES"

done < <(jq -r '.topics[]' "$TOPICS_FILE")

log "fetch done: topics_ok=${topics_ok} topics_failed=${topics_failed} total_stories=${stories_fetched}"

# Pipe collected stories to dedup/clustering script
DEDUP_OUTPUT=$(cat "$TMP_STORIES" | python3 "$SCRIPT_DIR/news-dedup.py" \
  --seen-file "$SCRIPT_DIR/news-seen.json" \
  --prefs-file "$SCRIPT_DIR/news-source-prefs.json" \
  ${DEBUG_FLAG})

new_item_count=$(echo "$DEDUP_OUTPUT" | grep -c '^- ' 2>/dev/null || echo "?")
log "result=done new_items=${new_item_count}"
echo "$DEDUP_OUTPUT"
