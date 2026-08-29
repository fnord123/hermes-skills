#!/usr/bin/env python3
"""square-list.py — list the user's upcoming appointments at one configured
Square merchant, parsed from Square confirmation emails in the user's
AgentMail inbox.

Read-only. Source of truth: AgentMail (which receives forwarded Square mail).

Usage:
  python3 square-list.py --merchant <alias>
  python3 square-list.py --merchant <alias> --horizon-days 90
  python3 square-list.py --merchant <alias> --probe   # dump raw email text for tuning

The --probe mode is for first-time setup or when parsing breaks: it emits the
matched thread headers plus the first ~2KB of one matched message's
extracted_text so you can refine sender_match / subject_match in
merchants.json and (if needed) the parser heuristics in this script.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_json import ok, fail, guard  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MERCHANTS = Path.home() / ".config" / "square-appointments" / "merchants.json"
AGENTMAIL_BASE = "https://api.agentmail.to"
HTTP_TIMEOUT_SECONDS = 30
MAX_PAGES = 20


class AgentMailError(Exception):
    """An AgentMail API call failed. Carries the user-facing message."""


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def env_value(env: dict[str, str], key: str) -> str | None:
    v = env.get(key) or os.environ.get(key)
    return v.strip() if v and v.strip() else None


def http_get_json(url: str, key: str) -> Any:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise AgentMailError(f"AgentMail HTTP {e.code} at {url}\n{body}")
    except urllib.error.URLError as e:
        raise AgentMailError(f"AgentMail network error: {e}")


def _coerce_list(payload: Any, key_hint: str) -> list[dict]:
    """AgentMail responses sometimes arrive as a list, sometimes wrapped.
    Pull a list out regardless of which envelope shape we got."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in (key_hint, "data", "items", "results"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def discover_inbox(key: str) -> str:
    data = http_get_json(f"{AGENTMAIL_BASE}/v0/inboxes", key)
    inboxes = _coerce_list(data, "inboxes")
    if not inboxes:
        raise AgentMailError("AgentMail returned no inboxes for this API key.")
    if len(inboxes) > 1:
        ids = ", ".join((i.get("inbox_id") or i.get("id") or i.get("email_address") or "?") for i in inboxes[:5])
        raise AgentMailError(
            f"AgentMail returned {len(inboxes)} inboxes; set AGENTMAIL_INBOX_ID in .env to pick one. Examples: {ids}"
        )
    ix = inboxes[0]
    return ix.get("inbox_id") or ix.get("id") or ix.get("email_address") or ix.get("email") or ""


def list_recent_threads(inbox_id: str, key: str, lookback_days: int) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    threads: list[dict] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        params = {"limit": "50", "after": after}
        if page_token:
            params["page_token"] = page_token
        url = f"{AGENTMAIL_BASE}/v0/inboxes/{urllib.parse.quote(inbox_id, safe='@')}/threads?" + urllib.parse.urlencode(params)
        data = http_get_json(url, key)
        threads.extend(_coerce_list(data, "threads"))
        page_token = None
        if isinstance(data, dict):
            page_token = data.get("next_page_token") or data.get("page_token") or None
        if not page_token:
            break
    return threads


def _str_field(thread: dict, *keys: str) -> str:
    for k in keys:
        v = thread.get(k)
        if v:
            return str(v)
    return ""


def matches_merchant(thread: dict, cfg: dict) -> bool:
    senders = [s for s in (cfg.get("sender_match") or ["messaging.squareup.com"]) if s]
    subjects = [s for s in (cfg.get("subject_match") or [cfg.get("name", "")]) if s]
    # Sender-side blob: thread metadata may surface various from-style fields,
    # and Gmail forwarding can shuffle them. Cast a wide net by also dumping
    # the whole thread metadata blob as a substring haystack for sender match.
    sender_blob = " ".join([
        _str_field(thread, "from", "sender", "last_sender", "from_address"),
        json.dumps(thread, default=str),
    ]).lower()
    if senders and not any(s.lower() in sender_blob for s in senders):
        return False
    subj = _str_field(thread, "subject", "last_subject").lower()
    if subjects and not any(s.lower() in subj for s in subjects):
        return False
    return True


def fetch_thread(inbox_id: str, thread_id: str, key: str) -> dict:
    url = f"{AGENTMAIL_BASE}/v0/inboxes/{urllib.parse.quote(inbox_id, safe='@')}/threads/{urllib.parse.quote(thread_id)}"
    data = http_get_json(url, key)
    return data if isinstance(data, dict) else {}


# Manage-booking URLs to look for in the email body.
# - Bare form: `app.squareup.com/appointments/book/reservations/<id>` (current)
#   or `book.squareup.com/...` (older templates).
# - Wrapped form: confirmations route every link through Square's click-tracker
#   at `a.squareupmessaging.com/CL0/<URL-encoded target>/<tracking-suffix>`.
#   The tracking-suffix part is one-shot/expiring; the encoded inner target is
#   the durable URL we want.
MANAGE_LINK_BARE_RE = re.compile(
    r"https://(?:app|book)\.squareup\.com/appointments/[^\s)>'\"<>]+",
    re.IGNORECASE,
)
MANAGE_LINK_WRAPPED_RE = re.compile(
    r"https://a\.squareupmessaging\.com/CL0/(https?:%2F%2F(?:app|book)\.squareup\.com%2Fappointments%2F[^/\s)>'\"<>]+)",
    re.IGNORECASE,
)


def _find_manage_url(text: str) -> str | None:
    m = MANAGE_LINK_BARE_RE.search(text)
    if m:
        return m.group(0)
    m = MANAGE_LINK_WRAPPED_RE.search(text)
    if m:
        return urllib.parse.unquote(m.group(1))
    return None

# Heuristic date+time patterns we look for in Square confirmation bodies.
# We capture the date-phrase and time-phrase separately so we can stitch ISO
# downstream once we know the timezone. Order matters: more specific first.
DATE_TIME_RES = [
    # "Monday, June 17, 2026 at 2:00 PM"
    re.compile(r"(\w+,\s+\w+\s+\d{1,2},\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[APap][Mm])"),
    # "Mon, Jun 17, 2026 · 2:00 PM"   (Square frequently uses · or • separators)
    re.compile(r"(\w{3,9},?\s+\w{3,9}\s+\d{1,2},?\s+\d{4})\s*[·•]\s*(\d{1,2}:\d{2}\s*[APap][Mm])"),
    # "Mon, Jun 17 · 2:00 PM"
    re.compile(r"(\w{3,9},?\s+\w{3,9}\s+\d{1,2})\s*[·•]\s*(\d{1,2}:\d{2}\s*[APap][Mm])"),
    # "June 17 at 2:00 PM"
    re.compile(r"(\w+\s+\d{1,2})\s+at\s+(\d{1,2}:\d{2}\s*[APap][Mm])"),
]

SERVICE_RES = [
    # "Service: 01-BRAZILIAN"
    re.compile(r"(?:Service|service|appointment)\s*[:\-]\s*([^\n\r]{2,80})"),
]


def _strip_forward_headers(text: str) -> str:
    """Strip Gmail-style forwarded-message headers preceding the actual body.

    Without this, the appointment-date regexes happily match the forward's own
    `Date: Wed, Jun 3, 2026 at 12:52 PM` line and return the *forwarding*
    timestamp instead of the appointment time. The body usually starts a blank
    line or two after the last header line (`To: …`).
    """
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        # Last `To:` line marks the bottom of the forward header block.
        if line.startswith("To: "):
            body_start = i + 1
    if not body_start:
        return text
    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1
    return "\n".join(lines[body_start:])


def parse_confirmation(text: str) -> dict:
    """Pull what we can from a Square confirmation email body.
    Defensive: every field is optional. Caller decides what to do with gaps."""
    text = _strip_forward_headers(text)
    out: dict[str, Any] = {
        "start_time_raw": None,
        "start_time_iso": None,
        "service": None,
        "booking_handle": None,
    }
    handle = _find_manage_url(text)
    if handle:
        out["booking_handle"] = handle
    for rx in DATE_TIME_RES:
        m = rx.search(text)
        if m:
            out["start_time_raw"] = f"{m.group(1)} {m.group(2)}".strip()
            iso = _try_parse_iso(m.group(1), m.group(2))
            if iso:
                out["start_time_iso"] = iso
            break
    for rx in SERVICE_RES:
        m = rx.search(text)
        if m:
            out["service"] = m.group(1).strip()
            break
    return out


def _try_parse_iso(date_phrase: str, time_phrase: str) -> str | None:
    """Best-effort: parse `date_phrase + time_phrase` into ISO-8601 naive.
    Returns None if any of several formats fail. No timezone is applied —
    callers that need tz handling should compose locally."""
    # Many Square emails use year-elided dates ("Mon, Jun 17"). Append current
    # year (with rollover heuristic) so strptime has something to work with.
    candidates = [date_phrase]
    if not re.search(r"\d{4}", date_phrase):
        this_year = datetime.now().year
        candidates = [f"{date_phrase}, {this_year}", f"{date_phrase}, {this_year + 1}"]
    formats_date = [
        "%A, %B %d, %Y",
        "%a, %b %d, %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    formats_time = ["%I:%M %p", "%I:%M%p"]
    norm_time = time_phrase.upper().replace("  ", " ").strip()
    for dc in candidates:
        for df in formats_date:
            for tf in formats_time:
                try:
                    dt = datetime.strptime(f"{dc} {norm_time}", f"{df} {tf}")
                    return dt.isoformat()
                except ValueError:
                    continue
    return None


def _in_window(start_iso: str | None, days_back: int, days_ahead: int) -> bool:
    """Gate an event's start time to [now - days_back, now + days_ahead].
    If we couldn't parse the time, default to including it — the agent can
    decide what to do with an undated booking rather than us silently
    dropping it."""
    if not start_iso:
        return True
    try:
        dt = datetime.fromisoformat(start_iso)
    except ValueError:
        return True
    now = datetime.now()
    return (now - timedelta(days=days_back)) <= dt <= (now + timedelta(days=days_ahead))


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True, help="Merchant alias from merchants.json.")
    ap.add_argument("--days-ahead", "--horizon-days", dest="days_ahead", type=int, default=60,
                    help="Include parsed appts up to N days ahead. Default 60.")
    ap.add_argument("--days-back", dest="days_back", type=int, default=0,
                    help="Also include past bookings up to N days back. Default 0 (upcoming only). "
                         "Pass a positive value (e.g. 180) when the user asks about past appointments.")
    ap.add_argument("--lookback-days", type=int, default=180,
                    help="How far back to search the inbox for confirmation emails. Default 180. "
                         "Distinct from --days-back: this is the email-search window, that is the output-filter window.")
    ap.add_argument("--probe", action="store_true", help="Dump matched-thread headers + raw email text for tuning. Does not parse or filter.")
    args = ap.parse_args()

    env = load_env(SCRIPT_DIR / ".env")
    key = env_value(env, "AGENTMAIL_API_KEY")
    if not key:
        fail("AGENTMAIL_API_KEY missing (.env or environment).")
        return

    merchants_file = Path(env_value(env, "MERCHANTS_FILE") or DEFAULT_MERCHANTS)
    if not merchants_file.exists():
        fail("merchants file not found", path=str(merchants_file))
        return
    merchants = json.loads(merchants_file.read_text())
    cfg = merchants.get(args.merchant)
    if not cfg:
        fail(f"merchant alias '{args.merchant}' not configured",
             configured_aliases=sorted(merchants.keys()))
        return

    try:
        inbox = env_value(env, "AGENTMAIL_INBOX_ID") or discover_inbox(key)
        threads = list_recent_threads(inbox, key, args.lookback_days)
        matching = [t for t in threads if matches_merchant(t, cfg)]

        if args.probe:
            report: dict[str, Any] = {
                "merchant": args.merchant,
                "lookback_days": args.lookback_days,
                "total_threads_scanned": len(threads),
                "matched_thread_count": len(matching),
                "sample_matches": [],
            }
            for t in matching[:3]:
                tid = t.get("thread_id") or t.get("id") or ""
                full = fetch_thread(inbox, tid, key) if tid else {}
                messages = full.get("messages") or []
                sample = {
                    "thread_id": tid,
                    "subject": _str_field(t, "subject", "last_subject"),
                    "from": _str_field(t, "from", "sender", "last_sender", "from_address"),
                    "message_count": len(messages),
                    "latest_text_head": "",
                }
                if messages:
                    txt = messages[-1].get("extracted_text") or messages[-1].get("text") or ""
                    sample["latest_text_head"] = txt[:2000]
                report["sample_matches"].append(sample)
            ok(**report)
            return

        bookings = []
        skipped = []
        for t in matching:
            tid = t.get("thread_id") or t.get("id")
            if not tid:
                continue
            full = fetch_thread(inbox, tid, key)
            messages = full.get("messages") or []
            if not messages:
                continue
            text = messages[-1].get("extracted_text") or messages[-1].get("text") or ""
            parsed = parse_confirmation(text)
            if not parsed["booking_handle"]:
                skipped.append({"thread_id": tid, "reason": "no manage URL found"})
                continue
            if not _in_window(parsed["start_time_iso"], args.days_back, args.days_ahead):
                continue
            bookings.append({
                "merchant_alias": args.merchant,
                "merchant_name": cfg.get("name"),
                "start_time_iso": parsed["start_time_iso"],
                "start_time_raw": parsed["start_time_raw"],
                "service": parsed["service"],
                "booking_handle": parsed["booking_handle"],
                "thread_id": tid,
            })

        # Sort by start_time_iso when present, else leave order as inbox order
        bookings.sort(key=lambda b: (b["start_time_iso"] or "9999"))
        out: dict[str, Any] = {"merchant": args.merchant, "bookings": bookings}
        if skipped:
            out["skipped"] = skipped
        ok(**out)
    except AgentMailError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
