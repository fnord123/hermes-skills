#!/usr/bin/env bash
# post-digest.sh — Saturday cron entry point for the Archivist weekly digest.
#
# Reads $HOME/archivist-digest/.env (ARCHIVIST_WEBHOOK_URL,
# ARCHIVIST_VAULT_PATH), runs rank.py against INDEX.md, posts the
# resulting digest to the configured Discord webhook.
#
# Designed to be installed under user crontab:
#   0 9 * * 6  $HOME/archivist-digest/post-digest.sh
set -euo pipefail

PIPELINE_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PIPELINE_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "post-digest.sh: missing $ENV_FILE — see archivist/README.md" >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${ARCHIVIST_WEBHOOK_URL:?ARCHIVIST_WEBHOOK_URL not set in $ENV_FILE}"
: "${ARCHIVIST_VAULT_PATH:?ARCHIVIST_VAULT_PATH not set in $ENV_FILE}"

INDEX="$ARCHIVIST_VAULT_PATH/INDEX.md"
if [[ ! -f "$INDEX" ]]; then
    echo "post-digest.sh: $INDEX not found — vault not initialized?" >&2
    exit 1
fi

DIGEST="$(python3 "$PIPELINE_DIR/rank.py" "$INDEX")"

# Discord webhook payload — content field is the message body.
# jq safely JSON-encodes the digest text (handles quotes, newlines, unicode).
payload="$(jq -nc --arg c "$DIGEST" '{content: $c}')"

curl -fsS -X POST -H "Content-Type: application/json" \
    -d "$payload" \
    "$ARCHIVIST_WEBHOOK_URL"
