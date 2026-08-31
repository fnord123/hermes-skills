#!/usr/bin/env bash
# sync-from-skill.sh — refresh this build context's service code from the
# canonical skill at the repo root (web-access/).
#
# The skill is the single source of truth for handler code (scripts/*,
# service.yaml, assets/fara-local-patches.diff). This directory only carries
# the DEPLOYMENT layer (Dockerfile, docker-compose.yml, README, manifest).
#
# Usage:  webstack/webaccess/sync-from-skill.sh          (in-place)
#         webstack/webaccess/sync-from-skill.sh --check  (report drift only)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SKILL="$ROOT/web-access"

[ -d "$SKILL" ] || { echo "ERROR: skill not found at $SKILL (run from a full repo clone)" >&2; exit 1; }

mode=copy
[ "${1:-}" = "--check" ] && mode=check

drift=0
# File list = everything the Dockerfile COPYs from the context, minus the
# deploy-only files. Kept explicit: a file added to the Dockerfile must be
# added here, and the drift check below catches exactly that mistake.
files=(
  "scripts/requirements.txt"
  "scripts/app.py"
  "scripts/mcp_server.py"
  "scripts/service.py"
  "scripts/handlers.py"
  "scripts/rxfetch.py"
  "scripts/browse_task.py"
  "scripts/run_service.py"
  "service.yaml"
  "assets/fara-local-patches.diff"
)

for f in "${files[@]}"; do
  src="$SKILL/$f"
  dst="$HERE/$f"
  if [ ! -f "$src" ]; then
    echo "MISSING in skill: $f" >&2; drift=1; continue
  fi
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
    if [ "$mode" = check ]; then
      echo "DRIFT: $f"
      drift=1
    else
      mkdir -p "$(dirname "$dst")"
      cp "$src" "$dst"
      echo "synced: $f"
    fi
  elif [ "$mode" = check ]; then
    : # in sync
  fi
done

if [ "$mode" = check ]; then
  if [ "$drift" = 1 ]; then
    echo "context is out of date with the skill — re-run without --check" >&2
    exit 1
  fi
  echo "build context in sync with the skill"
  exit 0
fi
echo "build context refreshed from $SKILL"
