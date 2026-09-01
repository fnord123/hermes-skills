#!/usr/bin/env bash
# deploy.sh — push this repo's webstack/ + web-access/ to the docker host and
# rebuild webaccess. Run on the dev box (.228), from anywhere in the repo.
#
# Assumes the one-time cutover has already created ~/webstack-repo on the
# docker host (see the cutover run-sheet). Steady state after that:
#
#   webstack/deploy.sh            # sync + build + up + verify
#   webstack/deploy.sh --no-build # push + context sync only
#
# Ships a working-tree tarball over scp (the docker host has no rsync and no
# git credentials). Uncommitted changes in webstack/ or web-access/ are
# included on purpose (WIP-friendly) — but the secret carriers are excluded
# explicitly: .env files, scripts/config.env, caches. The .env files on the
# host are never touched by a deploy.
set -euo pipefail

HOST="${DEPLOY_HOST:-docker}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

cd "$REPO"

STAMP="$(date +%F-%H%M)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Working-tree snapshot with explicit secret excludes. Anything new you add
# to the context that carries a secret MUST be added to this exclude list.
tar -cf "$tmp/ws.tar" \
    --exclude='.git' \
    --exclude='*.env' \
    --exclude='config.env' \
    --exclude='webstack/searxng/config' \
    --exclude='fara' \
    --exclude='docker-client' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    webstack web-access

UNTRACKED=$(git status --short -- webstack web-access | awk '$1=="??" {print $2}')
if [ -n "$UNTRACKED" ]; then
  echo "NOTE: untracked (new) files will be pushed:" >&2
  echo "$UNTRACKED" | sed 's/^/       /' >&2
fi
UNCOMMITTED=$(git status --short -- webstack web-access | awk '$1!="??" {print $2}')
if [ -n "$UNCOMMITTED" ]; then
  echo "NOTE: uncommitted changes will be pushed (commit first if this should be canonical):" >&2
  echo "$UNCOMMITTED" | sed 's/^/       /' >&2
fi

scp -q "$tmp/ws.tar" "$HOST:~/ws-$STAMP.tar"

echo ">> $STAMP: deploying to $HOST"
ssh "$HOST" "set -euo pipefail
  mkdir -p ~/webstack-repo
  tar -xf ~/ws-$STAMP.tar -C ~/webstack-repo
  rm -f ~/ws-$STAMP.tar
  cd ~/webstack-repo/webstack
  webaccess/sync-from-skill.sh
  [ -f webaccess/.env ] || { echo 'ERROR: webaccess/.env missing on host — restore from the backup before deploying' >&2; exit 1; }
  [ -f .env ]           || echo 'WARN: top-level .env (firecrawl vars) missing — firecrawl tiers will use compose defaults'
  # --- secret guards: a deploy must not silently run searxng keyless or unsigned ---
  # (awk field compare, not a KEY=value regex — keeps the check out of the redactor's
  #  pattern space and exact: the line must be KEY=<non-empty>)
  env_has() { awk -F= -v k="$1" '$1==k && length($2)>0 { found=1 } END { exit found?0:1 }' "$2"; }
  env_has SEARXNG_SECRET .env || { echo 'ERROR: SEARXNG_SECRET missing/empty in root .env — searxng would fall back to the secret baked in settings.yml (or auto-gen on a fresh host): sessions and signed URLs break' >&2; exit 1; }
  if [ -f searxng/.env ]; then
    env_has SEARXNG_BRAVE_API_KEY searxng/.env || { echo 'ERROR: SEARXNG_BRAVE_API_KEY missing/empty in searxng/.env — the brave api engine will be skipped at next apply' >&2; exit 1; }
  else
    echo 'WARN: searxng/.env missing — keyed engines will be skipped (keyless engines unaffected)'
  fi
  # --- keep searxng's managed engine block in sync with the (clean) repo tree ---
  # The tarball ships api-engines.yml with placeholder keys; without this, the
  # host's live managed block would go stale against repo changes. The apply
  # script expands real keys from searxng/.env and re-verifies the engine answers.
  searxng/apply-api-engines.sh
"

if [ "$BUILD" = 1 ]; then
  echo ">> building webaccess (Fara + docker CLI fetched at pinned pins)"
  ssh "$HOST" "cd ~/webstack-repo/webstack && docker compose build webaccess"
fi

echo ">> starting / restarting"
ssh "$HOST" "cd ~/webstack-repo/webstack && docker compose up -d"
echo ">> verifying"
ssh "$HOST" 'cd ~/webstack-repo/webstack
  sleep 8
  docker compose ps --format "  {{.Name}}\t{{.Status}}"
  echo -n "  health: "; curl -sf http://127.0.0.1:8910/health || { echo FAIL; exit 1; }
'
echo ">> done ($HOST) — black-box probes (expected key sets):"
echo "   search result: [\"snippet\", \"title\", \"url\"]"
echo "   fetch:         [\"chars\", \"ok\", \"outcome\", \"text\", \"truncated\", \"url\"]"
