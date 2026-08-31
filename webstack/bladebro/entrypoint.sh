#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${BLADE_PROFILE_DIR}"

# One persistent browser (Xvfb + Chrome) reused across fetches — avoids the per-call display race.
bladebro daemon >/tmp/blade-daemon.log 2>&1 &
sleep 4

# The container's job: the HTTP fetch service the web-access skill's bladebro tier calls.
# (Was an MCP bridge for the agent — kept commented in case that role is wanted again.)
exec node /home/blade/fetch.js
# exec supergateway --stdio "bladebro mcp" --outputTransport streamableHttp \
#   --streamableHttpPath /mcp --stateful --port "${PORT}" --healthEndpoint /health
