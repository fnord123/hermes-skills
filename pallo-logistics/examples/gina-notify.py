#!/usr/bin/env python3
"""gina-notify.py — post a Gina-coordination message to the shared Discord channel.

Stdlib-only (webhook POST via urllib). Mentions BOTH Gina and the user via
`<@id>` so the conversation is auditable and the user stays looped in — never
a DM to Gina alone. Also records the ask in the pending-coordination ledger so
the agent can later thread Gina's reply back to it.

Normally called internally by the booking flow (pallo-book-trip.py) after a
stay is booked. The agent only invokes it directly as an escape hatch for a
one-off ping.

Usage:
  python3 gina-notify.py --topic "Pallo dropoff" --body "Need the X Sun afternoon" \\
      [--trip-name Paris] [--handoff-date 2026-07-21] [--dry-run]

Output: JSON. --dry-run returns the formatted message + would-be ledger entry
without sending. A real send returns {status: "sent", discord_message_id,
ledger_entry} or {status: "send_failed", ...}.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import coord_lib  # noqa: E402


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24] or "msg"


def _build_content(cfg: dict, topic: str, body: str, handoff_date: str | None) -> str:
    lines = [
        f"<@{cfg['gina_user_id']}> <@{cfg['self_user_id']}> — heads up: {body}",
        "",
        f"Subject: {topic}",
    ]
    if handoff_date:
        lines.append(f"Date: {handoff_date}")
    lines.append("Reply if any issues; otherwise we'll assume this works.")
    return "\n".join(lines)


def _post(webhook_url: str, content: str, user_ids: list[str]) -> dict:
    url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    payload = json.dumps({
        "content": content,
        "allowed_mentions": {"users": user_ids},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "pallo-logistics/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8") or "{}"
        return {"ok": True, "message": json.loads(raw)}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"network error: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--body", required=True)
    ap.add_argument("--trip-name", default=None)
    ap.add_argument("--handoff-date", default=None, help="ISO date of the handoff this is about.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Format the message and ledger entry without sending.")
    args = ap.parse_args()

    cfg = coord_lib.discord_config()
    if "error" in cfg:
        print(json.dumps({"status": "not_configured", "reason": cfg["error"]}, indent=2))
        return 2

    content = _build_content(cfg, args.topic, args.body, args.handoff_date)
    now = datetime.now().astimezone()
    base = args.handoff_date or now.date().isoformat()
    entry = {
        "id": f"coord-{base}-{_slug(args.topic)}",
        "sent_at": now.isoformat(timespec="seconds"),
        "trip_name": args.trip_name,
        "handoff_date": args.handoff_date,
        "ask_summary": args.body,
        "discord_message_id": None,
    }

    if args.dry_run:
        print(json.dumps({
            "status": "dry_run",
            "content": content,
            "ledger_entry": entry,
        }, indent=2))
        return 0

    res = _post(cfg["webhook_url"], content, [cfg["gina_user_id"], cfg["self_user_id"]])
    if not res["ok"]:
        print(json.dumps({"status": "send_failed", "reason": res["error"], "content": content}, indent=2))
        return 1

    entry["discord_message_id"] = str(res["message"].get("id")) if res.get("message") else None
    coord_lib.add_pending(entry)
    print(json.dumps({
        "status": "sent",
        "discord_message_id": entry["discord_message_id"],
        "ledger_entry": entry,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
