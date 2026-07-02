#!/usr/bin/env python3
"""pallo-calendar-invite.py — email Google-Calendar invites for Pallo's handoffs.

Builds an iMIP invite (an .ics VEVENT with METHOD:REQUEST) for the drop-off
and/or pickup of a Laurel Acres stay and sends it via AgentMail as a
`text/calendar; method=REQUEST` attachment. Gmail auto-adds these to the
recipients' Google Calendars with RSVP. Attendees are you + Gina.

Dates/times come from a pallo-trip-plan.py `plan_json` (preferred) or explicit
flags. A STABLE UID per stay+kind means re-sending UPDATES the same calendar
event instead of duplicating it (bump --sequence when you change a time).

Usage:
  # from a plan blob (what pallo-book-trip.py passes):
  python3 pallo-calendar-invite.py --plan '<plan_json>' --events pickup,dropoff

  # explicit:
  python3 pallo-calendar-invite.py \
      --drop-date 2026-09-30 --drop-time "3pm" \
      --pickup-date 2026-10-05 --pickup-time "11am" --events pickup

  # preview the .ics without sending:
  python3 pallo-calendar-invite.py --plan '<plan_json>' --dry-run

Config (in ~/.config/pallo-logistics/secrets.env; the agent must NEVER read it):
  USER_EMAIL=you@example.com          # invitee 1
  GINA_EMAIL=gina@example.com         # invitee 2 (omit / use --to to skip)
The AgentMail API key is read from ~/.hermes/config.yaml
(mcp_servers.agentmail.env.AGENTMAIL_API_KEY); the sending inbox is discovered
via the AgentMail API (or set AGENTMAIL_INBOX_ID in secrets.env).

Output: JSON {status: ok|dry_run_ok|error, events:[...], ...}.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / ".config" / "pallo-logistics"
SECRETS = CONFIG_DIR / "secrets.env"
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"

AGENTMAIL_BASE = "https://api.agentmail.to/v0"
TZ_NAME = "America/Los_Angeles"          # Laurel Acres — Hillsboro, OR (Pacific)
LOCATION = "Laurel Acres Kennels, Hillsboro, OR"
EVENT_MINUTES = 30

_VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if _VENV_PY.exists() and sys.executable != str(_VENV_PY):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def out(d: dict, code: int = 0) -> int:
    print(json.dumps(d, indent=2))
    return code


def _load_env(path: Path) -> dict:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _api_key(env: dict) -> str:
    # Prefer the same key Hermes already uses for AgentMail.
    try:
        import yaml
        cfg = yaml.safe_load(HERMES_CONFIG.read_text())
        k = cfg["mcp_servers"]["agentmail"]["env"]["AGENTMAIL_API_KEY"]
        if k:
            return k
    except Exception:
        pass
    return env.get("AGENTMAIL_API_KEY", "")


def _http(method: str, path: str, key: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        AGENTMAIL_BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AgentMail HTTP {e.code}: {e.read().decode()[:300]}")


def _discover_inbox(key: str, env: dict) -> str:
    if env.get("AGENTMAIL_INBOX_ID"):
        return env["AGENTMAIL_INBOX_ID"]
    _, d = _http("GET", "/inboxes", key)
    boxes = d.get("inboxes", d) if isinstance(d, dict) else d
    boxes = boxes if isinstance(boxes, list) else [boxes]
    if not boxes:
        raise RuntimeError("AgentMail returned no inboxes for this key.")
    b = boxes[0]
    return b.get("inbox_id") or b.get("id") or b.get("email")


# ── time parsing ─────────────────────────────────────────────────────────────

def _parse_clock(s: str) -> tuple[int, int]:
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])?", s or "")
    if not m:
        raise ValueError(f"bad time {s!r}")
    hh = int(m.group(1)); mm = int(m.group(2) or 0); ap = (m.group(3) or "").lower()
    if ap == "pm" and hh != 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    return hh, mm


def _local_dt(d: date, clock: str) -> datetime:
    hh, mm = _parse_clock(clock)
    naive = datetime(d.year, d.month, d.day, hh, mm)
    if ZoneInfo is not None:
        return naive.replace(tzinfo=ZoneInfo(TZ_NAME))
    # Fallback: assume PDT (UTC-7). Sep/Oct handoffs are DST.
    return naive.replace(tzinfo=timezone(timedelta(hours=-7)))


def _ics_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


# ── ICS build ────────────────────────────────────────────────────────────────

def build_ics(kind: str, start_local: datetime, attendees: list[str], organizer: str,
              sequence: int, stamp: datetime) -> tuple[str, str, str]:
    """Return (ics_text, summary, uid) for one handoff VEVENT (METHOD:REQUEST)."""
    end_local = start_local + timedelta(minutes=EVENT_MINUTES)
    verb = "Drop off" if kind == "dropoff" else "Pick up"
    summary = f"{verb} Pallo — Laurel Acres"
    day = start_local.date().isoformat()
    uid = f"pallo-{kind}-{day}@laurel-acres.pallo-logistics"
    desc = (f"{verb} Pallo at Laurel Acres Kennels (Hillsboro). "
            f"Auto-scheduled by Hermes from the boarding reservation.")
    lines = [
        "BEGIN:VCALENDAR", "PRODID:-//pallo-logistics//EN", "VERSION:2.0",
        "CALSCALE:GREGORIAN", "METHOD:REQUEST", "BEGIN:VEVENT",
        f"UID:{uid}", f"SEQUENCE:{sequence}", f"DTSTAMP:{_ics_utc(stamp)}",
        f"DTSTART:{_ics_utc(start_local)}", f"DTEND:{_ics_utc(end_local)}",
        f"SUMMARY:{_esc(summary)}", f"LOCATION:{_esc(LOCATION)}",
        f"DESCRIPTION:{_esc(desc)}",
        f"ORGANIZER;CN=Hermes (Pallo):mailto:{organizer}",
        "STATUS:CONFIRMED", "TRANSP:OPAQUE",
    ]
    for a in attendees:
        lines.append(
            "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;"
            f"RSVP=TRUE;CN={a}:mailto:{a}")
    lines += [
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary)}",
        "TRIGGER:-P1D", "END:VALARM",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary)}",
        "TRIGGER:-PT2H", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n", summary, uid


def send_invite(key: str, inbox: str, organizer: str, attendees: list[str],
                ics: str, summary: str, start_local: datetime) -> None:
    when = start_local.strftime("%a %b %-d, %-I:%M %p")
    body = (f"{summary}\n\nWhen: {when} (Pacific)\nWhere: {LOCATION}\n\n"
            f"This calendar invite was scheduled automatically by Hermes from "
            f"Pallo's boarding reservation. Accept it to add the handoff to your "
            f"Google Calendar.")
    payload = {
        "to": attendees,
        "subject": summary + f" — {start_local.strftime('%b %-d')}",
        "text": body,
        "attachments": [{
            "filename": "invite.ics",
            "content_type": "text/calendar; method=REQUEST; charset=UTF-8",
            "content": base64.b64encode(ics.encode("utf-8")).decode(),
            "content_disposition": "attachment",
        }],
    }
    _http("POST", f"/inboxes/{urllib.parse.quote(inbox, safe='@')}/messages/send",
          key, payload)


def _resolve_handoffs(args) -> dict:
    """Return {'dropoff': (date, time), 'pickup': (date, time)} from plan or flags."""
    out_h: dict = {}
    if args.plan:
        plan = json.loads(args.plan)
        d, p = plan.get("drop_off"), plan.get("pick_up")
        if d:
            out_h["dropoff"] = (date.fromisoformat(d), plan.get("drop_time", "08:00 AM"))
        if p:
            out_h["pickup"] = (date.fromisoformat(p), plan.get("pickup_time", "09:00 AM"))
    if args.drop_date:
        out_h["dropoff"] = (date.fromisoformat(args.drop_date), args.drop_time or "08:00 AM")
    if args.pickup_date:
        out_h["pickup"] = (date.fromisoformat(args.pickup_date), args.pickup_time or "09:00 AM")
    return out_h


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=None, help="plan_json from pallo-trip-plan.py")
    ap.add_argument("--drop-date"); ap.add_argument("--drop-time")
    ap.add_argument("--pickup-date"); ap.add_argument("--pickup-time")
    ap.add_argument("--events", default="pickup,dropoff",
                    help="which handoffs to invite: 'pickup', 'dropoff', or both (default).")
    ap.add_argument("--to", default=None,
                    help="comma-separated attendee emails; overrides USER_EMAIL/GINA_EMAIL.")
    ap.add_argument("--sequence", type=int, default=0,
                    help="iCal SEQUENCE; bump when re-sending a changed time.")
    ap.add_argument("--dry-run", action="store_true", help="print the .ics; send nothing.")
    args = ap.parse_args()

    env = _load_env(SECRETS)
    if args.to:
        attendees = [e.strip() for e in args.to.split(",") if e.strip()]
    else:
        attendees = [e for e in (env.get("USER_EMAIL"), env.get("GINA_EMAIL")) if e]
    if not attendees:
        return out({"status": "error", "reason":
                    "no attendees — set USER_EMAIL / GINA_EMAIL in secrets.env or pass --to."}, 2)

    want = {e.strip().lower() for e in args.events.split(",") if e.strip()}
    handoffs = {k: v for k, v in _resolve_handoffs(args).items() if k in want}
    if not handoffs:
        return out({"status": "error", "reason":
                    "no handoff dates — pass --plan or --drop-date/--pickup-date and --events."}, 2)

    stamp = datetime.now(timezone.utc)
    organizer = "invite@agentmail.to"  # replaced with the discovered inbox below
    key = ""
    inbox = ""
    if not args.dry_run:
        key = _api_key(env)
        if not key:
            return out({"status": "error", "reason": "AGENTMAIL_API_KEY not found."}, 2)
        try:
            inbox = _discover_inbox(key, env)
        except Exception as e:
            return out({"status": "error", "reason": str(e)}, 1)
        organizer = inbox

    results = []
    for kind in ("dropoff", "pickup"):
        if kind not in handoffs:
            continue
        d, clock = handoffs[kind]
        start_local = _local_dt(d, clock)
        ics, summary, uid = build_ics(kind, start_local, attendees, organizer,
                                      args.sequence, stamp)
        rec = {"kind": kind, "when": start_local.isoformat(), "summary": summary,
               "uid": uid, "attendees": attendees}
        if args.dry_run:
            rec["ics"] = ics
        else:
            try:
                send_invite(key, inbox, organizer, attendees, ics, summary, start_local)
                rec["sent"] = True
            except Exception as e:
                rec["sent"] = False; rec["error"] = str(e)
        results.append(rec)

    status = "dry_run_ok" if args.dry_run else (
        "ok" if all(r.get("sent") for r in results) else "partial")
    return out({"status": status, "from": organizer or "(dry-run)",
                "attendees": attendees, "events": results},
               0 if status in ("ok", "dry_run_ok") else 1)


if __name__ == "__main__":
    sys.exit(main())
