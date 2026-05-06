#!/usr/bin/env python3
"""fetch-tickers.py — emit a __Markets__ block from tickers.json.

Hybrid source dispatch:
  - Bare tickers (no dot, e.g. NVDA, GLW)        → Twelve Data REST quote
  - Suffixed tickers (e.g. SU.PA, 7203.T, 0700.HK) → yfinance (Yahoo scraper)

Twelve Data covers US markets reliably on the free tier; international
listings are paywalled to the Grow plan ($29/mo). yfinance fills the gap
free, at the cost of being unofficial — Yahoo can change its API and break
the lib until maintainers patch.

Output is the same markdown table either source produces:

    __Markets__ · prior session close · day-over-day Δ

    | Ticker | Close | Day Δ | As of |
    |---|---:|---:|---|
    | NVDA  | 200.28 | ▲ +1.92% | 2026-05-06 |
    | SU.PA | 234.50 | ▲ +0.85% | 2026-05-05 |

Day Δ is yesterday's close vs the prior trading day's close — same metric
both sources expose (Twelve Data: `percent_change`; yfinance: computed
from a 5-day history slice).

The script never throws — every error path returns DATA UNAVAILABLE so
the briefing always posts.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TICKERS_FILE = SCRIPT_DIR / "tickers.json"


def _row_unavailable(ticker: str, msg: str) -> str:
    # Trim message to keep the table cell readable.
    msg = (msg or "").strip().replace("\n", " ")
    if len(msg) > 60:
        msg = msg[:57] + "..."
    return f"| {ticker} | DATA UNAVAILABLE | — | {msg or '—'} |"


def _row_quote(ticker: str, close: float, pct: float, date: str) -> str:
    pct_signed = f"{pct:+.2f}%"
    arrow = "▲" if pct >= 0 else "▼"
    return f"| {ticker} | {close:.2f} | {arrow} {pct_signed} | {date} |"


def fetch_us(symbols: list[str]) -> dict[str, str]:
    """Twelve Data multi-symbol quote. Returns {ticker: markdown_row}."""
    if not symbols:
        return {}

    api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not api_key:
        return {t: _row_unavailable(t, "TWELVE_DATA_API_KEY not set") for t in symbols}

    qs = urllib.parse.urlencode({"symbol": ",".join(symbols), "apikey": api_key})
    url = f"https://api.twelvedata.com/quote?{qs}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return {t: _row_unavailable(t, f"twelvedata: {exc}") for t in symbols}

    # Single-symbol response is flat; normalize to keyed shape.
    if len(symbols) == 1 and isinstance(data, dict) and "status" not in data and symbols[0] not in data:
        data = {symbols[0]: data}

    # Top-level error (bad key, exhausted quota, etc.)
    if isinstance(data, dict) and data.get("status") == "error":
        msg = data.get("message", "twelvedata error")
        return {t: _row_unavailable(t, msg) for t in symbols}

    rows: dict[str, str] = {}
    for t in symbols:
        entry = data.get(t, {}) if isinstance(data, dict) else {}
        if not isinstance(entry, dict):
            rows[t] = _row_unavailable(t, "unexpected response shape")
            continue
        if entry.get("status") == "error":
            rows[t] = _row_unavailable(t, entry.get("message", "twelvedata error"))
            continue
        try:
            close = float(entry["close"])
            pct = float(entry["percent_change"])
            date = (entry.get("datetime") or "")[:10]
        except (KeyError, TypeError, ValueError):
            rows[t] = _row_unavailable(t, "incomplete data")
            continue
        rows[t] = _row_quote(t, close, pct, date)
    return rows


def fetch_intl(symbols: list[str]) -> dict[str, str]:
    """yfinance per-ticker fetch. Returns {ticker: markdown_row}."""
    if not symbols:
        return {}

    try:
        import yfinance as yf
    except ImportError:
        return {t: _row_unavailable(t, "yfinance not installed in this venv") for t in symbols}

    rows: dict[str, str] = {}
    for t in symbols:
        try:
            hist = yf.Ticker(t).history(period="5d", auto_adjust=False)
            if hist is None or len(hist) < 2:
                rows[t] = _row_unavailable(t, "insufficient yahoo history")
                continue
            last = hist.iloc[-1]
            prev = hist.iloc[-2]
            close = float(last["Close"])
            prev_close = float(prev["Close"])
            if prev_close == 0:
                rows[t] = _row_unavailable(t, "zero prev close")
                continue
            pct = (close - prev_close) / prev_close * 100.0
            date = last.name.strftime("%Y-%m-%d")
            rows[t] = _row_quote(t, close, pct, date)
        except Exception as exc:
            rows[t] = _row_unavailable(t, f"yahoo: {exc}")
    return rows


def main() -> int:
    if not TICKERS_FILE.exists():
        return 0
    try:
        config = json.loads(TICKERS_FILE.read_text())
    except Exception:
        return 0
    if not config:
        return 0

    # Suffix-based source dispatch. A dot in the ticker means an exchange
    # suffix (Yahoo convention) — route to yfinance. Bare tickers go to
    # Twelve Data. Class shares like BRK.B that you want via Twelve Data
    # should be entered as BRK-B (Yahoo's hyphen form for share classes).
    us: list[str] = []
    intl: list[str] = []
    order: list[str] = []
    for entry in config:
        ticker = (entry or {}).get("ticker", "").strip()
        if not ticker:
            continue
        order.append(ticker)
        (intl if "." in ticker else us).append(ticker)

    rows: dict[str, str] = {}
    rows.update(fetch_us(us))
    rows.update(fetch_intl(intl))

    print("__Markets__ · prior session close · day-over-day Δ")
    print()
    print("| Ticker | Close | Day Δ | As of |")
    print("|---|---:|---:|---|")
    for t in order:
        if t in rows:
            print(rows[t])
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
