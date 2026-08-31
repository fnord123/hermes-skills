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
