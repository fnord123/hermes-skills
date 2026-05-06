#!/usr/bin/env bash
# fetch-tickers.sh — emit a __Markets__ block from tickers.json.
#
# Thin wrapper around fetch-tickers.py. Kept as a .sh entry point so it
# matches the fetch-*.sh pattern in this directory (fetch-news.sh,
# fetch-weather.sh, fetch-calendar.sh) and is a drop-in callable from
# morning-briefing.sh.
#
# Hybrid price-source dispatch happens inside fetch-tickers.py:
#   - Bare tickers (no dot, e.g. NVDA)         → Twelve Data REST quote
#   - Suffixed tickers (e.g. SU.PA, 7203.T)    → yfinance (Yahoo scraper)
#
# yfinance lives in a local venv to satisfy PEP 668 — system pip refuses
# to install into the OS Python on Debian 12+.
#
# Required environment:
#   TWELVE_DATA_API_KEY — set in ~/daily-briefing/.env
# Optional environment:
#   FETCH_TICKERS_PYTHON — override Python interpreter path

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${FETCH_TICKERS_PYTHON:-${SCRIPT_DIR}/venv/bin/python3}"

# Fall back to system python3 if the venv isn't there — fetch-tickers.py
# will report yfinance as unavailable for any non-US tickers, but US
# tickers via Twelve Data still work.
if [[ ! -x "$PY" ]]; then
    PY="$(command -v python3)"
fi

exec "$PY" "${SCRIPT_DIR}/fetch-tickers.py"
