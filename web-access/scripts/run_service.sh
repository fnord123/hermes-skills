#!/usr/bin/env bash
# Service entrypoint: apply the non-secret service config, then run the
# single process that carries both facades.
set -euo pipefail
cd /opt/webaccess
if [ -f /opt/webaccess/service.env ]; then
  # service.env holds the non-secret deploy DEFAULTS baked into the image.
  # The process env (compose environment:/env_file) is authoritative and the
  # runtime sets it BEFORE this runs, so a variable the caller already provided
  # wins over the baked default.
  #
  # Sourcing it plainly with `.` used to export the baked values on top of the
  # caller's env. That silently kept the host-published tier URLs
  # (docker.putzolu.com:8888 / :3002) in effect after the stack moved to an
  # internal Docker network (2026-08-24) — compose could no longer override
  # them. So load each default only when the caller did not already set it.
  set -a
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;   # blank or comment
      *=*) ;;               # a KEY=VALUE assignment — handle below
      *) continue ;;        # not an assignment; ignore
    esac
    key="${line%%=*}"
    case "$key" in
      *[!A-Za-z0-9_]*|"") continue ;;   # not a plain KEY=VALUE line; ignore
    esac
    if [ -n "${!key+x}" ]; then
      continue              # caller provided this; the caller's value wins
    fi
    export "$line"
  done < /opt/webaccess/service.env
  set +a
fi
exec python3 /opt/webaccess/service.py
