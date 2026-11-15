#!/usr/bin/env python3
"""Reconstruct one kanban worker's handoff from its own session record.

Usage:
  python3 trace-handoff.py <profile>

A board `list` snapshot can land inside the complete->create window and
read "done head, no successor" seconds before the successor exists. The
profile's `state.db` is the worker's OWN record. This tool prints the
profile's newest session that made tool calls: every kanban_* call in
row order (args: title / assignee / parents / task_id) paired with its
result (for creates: the new card's id and born status), plus a tally
of every other tool the session used — the durable proof of handoff
order and of whether the role consulted its own memory bank.

Read-only: opens the DB with sqlite mode=ro; never writes to state.db.
The DB resolves from the HERMES_HOME env var (default ~/.hermes):
profiles/<profile>/state.db — never a hardcoded home.

Output: exactly ONE JSON object on stdout. Success:
  {"ok": true, "profile": ..., "session_id": ...,
   "kanban_calls": [{"order": N, "call": name, "args": {...},
                     "result": {...}}, ...],
   "other_tools": {"terminal": N, ...}}
Failure: {"ok": false, "error": "..."} with exit 1.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_json import fail  # noqa: E402
from skill_json import guard  # noqa: E402
from skill_json import ok  # noqa: E402


def _home() -> Path:
    """Resolve the hermes home: HERMES_HOME env var, default ~/.hermes."""
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _as_json_object(value):
    """Parse tool output to a JSON object when possible; else keep text."""
    text = (value or "").strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {"text": text}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _call_args(function):
    """The call's arguments as a dict; unparseable payloads kept as text."""
    raw = function.get("arguments", "")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _entries(payload):
    """The tool_calls column parsed as a list of call entries, or None."""
    try:
        entries = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return entries if isinstance(entries, list) else None


@guard
def main() -> None:
    if len(sys.argv) < 2:
        fail("usage: trace-handoff <profile>")
    profile = sys.argv[1]
    db_path = _home() / "profiles" / profile / "state.db"
    if not db_path.exists():
        fail("no state.db for profile '%s' at %s" % (profile, db_path))
    db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    db.row_factory = sqlite3.Row
    newest = db.execute(
        "SELECT session_id FROM messages "
        "WHERE tool_name IS NOT NULL OR tool_calls IS NOT NULL "
        "GROUP BY session_id ORDER BY MAX(id) DESC LIMIT 1"
    ).fetchone()
    if newest is None:
        fail("no session with tool calls for profile '%s'" % profile)
    session_id = newest["session_id"]
    rows = db.execute(
        "SELECT id, role, tool_name, tool_calls, content FROM messages "
        "WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    kanban_calls = []
    # tool name -> calls of that name still awaiting their result row;
    # tool results arrive in call order, so each queue pairs FIFO.
    awaiting: dict[str, list] = {}
    other_tools: Counter = Counter()
    for row in rows:
        if row["role"] == "tool":
            name = row["tool_name"] or ""
            queue = awaiting.get(name)
            if name.startswith("kanban_") and queue:
                queue.pop(0)["result"] = _as_json_object(row["content"])
            continue
        payload = row["tool_calls"]
        if not payload:
            continue
        entries = _entries(payload)
        if not entries:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            function = entry.get("function", {})
            name = function.get("name") or row["tool_name"] or "?"
            if not name.startswith("kanban_"):
                other_tools[name] += 1
                continue
            call = {
                "order": row["id"],
                "call": name,
                "args": _call_args(function),
                "result": {},
            }
            kanban_calls.append(call)
            awaiting.setdefault(name, []).append(call)

    ok(
        profile=profile,
        session_id=session_id,
        kanban_calls=kanban_calls,
        other_tools=dict(other_tools.most_common()),
    )


if __name__ == "__main__":
    main()
