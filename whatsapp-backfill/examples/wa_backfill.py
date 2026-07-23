#!/usr/bin/env python3
"""wa_backfill.py — import a WhatsApp "Export chat" into Hindsight memory.

Parses a WhatsApp chat export, groups messages into coherent conversation
windows, and retains each into a Hindsight bank — so the agent can later answer
questions about the conversation (via its normal memory recall). Retains are
submitted asynchronously, because Hindsight extracts facts with an LLM in the
background.

WhatsApp has no message-history API; the supported source of existing history
is the app's own "Export chat" (Chat → Export chat → Without media). This tool
takes that export's `.zip` directly (extracts the `_chat.txt` inside) or a
plain `.txt`.

Commands (each prints ONE JSON object on stdout; exit 1 on error):
  preview --file <export.zip|.txt> [--chat "<name>"]   parse only: stats + sample
  import  --file <export.zip|.txt> [--bank <id>] [--block-days N]
          [--since D --until D] [--alias "Old=New"] [--wait]
                                                       parse + retain into Hindsight
  status  --bank <id> [--operation-id <id> …] [--wait]  monitor progress + counts

Auth/target: reads api_url + bank_id (+ apiKey) from
~/.hermes/hindsight/config.json — the same server/bank the agent recalls from.
"""

import os
import sys
from pathlib import Path

# Run under the Hermes venv (has hindsight_client_api).
_VENV = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
if _VENV.exists() and sys.executable != str(_VENV):
    os.execv(str(_VENV), [str(_VENV), *sys.argv])

import argparse
import json
import re
from datetime import datetime

# Path to the Hindsight provider config; overridable via env for testing.
HINDSIGHT_CONFIG = Path(os.environ.get(
    "WA_BACKFILL_HINDSIGHT_CONFIG",
    str(Path.home() / ".hermes" / "hindsight" / "config.json")))

# LTR/RTL marks WhatsApp sprinkles into exports; strip them before matching.
_MARKS = "‎‏‪‬ "

# iOS:     [2026-03-04, 9:15:23 AM] Dan: hello   /  [3/4/26, 21:15] Dan: hi
_IOS = re.compile(r"^\[(?P<dt>[^\]]+)\]\s?(?P<rest>.*)$", re.S)
# Android: 3/4/26, 9:15 AM - Dan: hello  /  04/03/2026, 21:15 - Dan: hi
_ANDROID = re.compile(
    r"^(?P<dt>\d{1,2}/\d{1,2}/\d{2,4},\s\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:\s?[APap]\.?\s?[Mm]\.?)?)\s-\s(?P<rest>.*)$", re.S)

_SYSTEM_HINTS = (
    "end-to-end encrypted", "created group", "added ", "removed ", "left",
    "changed the subject", "changed this group", "changed their phone number",
    "changed to ", "you deleted", "this message was deleted", "joined using",
    "changed the group description", "pinned a message", "turned on",
    "security code changed", "missed voice call", "missed video call",
)
_MEDIA_HINTS = ("<media omitted>", "image omitted", "video omitted",
                "audio omitted", "sticker omitted", "gif omitted",
                "document omitted", "‎image omitted", "(file attached)")


def out(d, code=0):
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(code)


def fail(msg):
    out({"ok": False, "error": str(msg)}, 1)


def _strip_marks(s):
    for m in _MARKS:
        s = s.replace(m, "")
    return s


def _parse_dt(raw):
    """Parse a WhatsApp date+time token to a datetime, or None if unparseable."""
    raw = raw.replace(",", " ").replace(" ", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = raw.replace("a.m.", "AM").replace("p.m.", "PM").replace("am", "AM").replace("pm", "PM")
    fmts = (
        "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M",
        "%m/%d/%y %I:%M:%S %p", "%m/%d/%y %H:%M:%S", "%m/%d/%y %I:%M %p", "%m/%d/%y %H:%M",
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M",
        "%d/%m/%y %I:%M %p", "%d/%m/%y %H:%M", "%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M",
    )
    for f in fmts:
        try:
            return datetime.strptime(raw, f)
        except ValueError:
            continue
    try:
        from dateutil import parser as _p  # optional
        return _p.parse(raw)
    except Exception:
        return None


def _split_sender(rest):
    """Return (sender, text) or (None, rest) if it's a system line."""
    idx = rest.find(": ")
    if idx == -1 or idx > 60 or "\n" in rest[:idx]:
        return None, rest
    return rest[:idx].strip(), rest[idx + 2:]


def _read_export(path):
    """Read a WhatsApp export into text. Accepts the exported `.zip` directly
    (extracts the `_chat.txt` inside — no `unzip` needed) or a plain `.txt`."""
    import zipfile
    p = Path(path).expanduser()
    if not p.exists():
        fail(f"file not found: {p}")
    if p.suffix.lower() == ".zip" or zipfile.is_zipfile(str(p)):
        try:
            with zipfile.ZipFile(str(p)) as z:
                names = ([n for n in z.namelist() if n.endswith("_chat.txt")]
                         or [n for n in z.namelist() if n.lower().endswith(".txt")])
                if not names:
                    fail("no chat .txt inside the zip — is it a WhatsApp export?")
                return z.read(names[0]).decode("utf-8", errors="replace")
        except zipfile.BadZipFile:
            fail(f"{p.name} is not a readable zip.")
    return p.read_text(encoding="utf-8", errors="replace")


def _derive_chat(path):
    """Turn an export filename into a chat label, stripping WhatsApp's export
    prefix (e.g. `WhatsApp Chat with Mom.zip` / `WhatsApp_Chat__Mom` -> `Mom`)."""
    import re
    stem = Path(path).stem
    # strip anything up to and including the "WhatsApp Chat [with|-]" marker
    # (also drops upload/hash prefixes like "d207f316-WhatsApp_Chat__…").
    stem = re.sub(r"(?i)^.*?whatsapp[ _]*chat[ _]*(?:with|-)?[ _]*", "", stem)
    return stem.replace("_", " ").strip() or "WhatsApp chat"


def parse_export(path):
    """Return a list of message dicts: {dt: datetime|None, sender, text, system}."""
    text = _read_export(path)
    messages = []
    for raw_line in text.splitlines():
        line = _strip_marks(raw_line)
        m = _IOS.match(line) or _ANDROID.match(line)
        if m:
            dt = _parse_dt(m.group("dt"))
            sender, body = _split_sender(m.group("rest"))
            messages.append({"dt": dt, "sender": sender,
                             "text": body, "system": sender is None})
        elif messages:
            # continuation of the previous message
            messages[-1]["text"] += "\n" + line
    return messages


def _media_only(text):
    """True for a message whose entire content is a media placeholder (no
    caption) — these carry no recallable information, so they're dropped."""
    t = text.strip().lower()
    if not t:
        return False
    exact = {"<media omitted>", "image omitted", "video omitted", "audio omitted",
             "sticker omitted", "gif omitted", "document omitted",
             "contact card omitted", "this message was deleted",
             "you deleted this message", "null", "‎"}
    if t in exact:
        return True
    if t.endswith("(file attached)") and "\n" not in text.strip():
        return True
    return False


def _is_system(msg):
    if msg["system"]:
        return True
    low = msg["text"].strip().lower()
    return any(h in low for h in _SYSTEM_HINTS)


def group_blocks(messages, include_system, block_messages, gap_hours, block_days=None):
    """Group consecutive messages into conversation blocks (one document each).

    Two modes:
    - Window mode (block_days set): break ONLY when a block spans more than
      block_days from its first message. block_messages / gap_hours / the
      per-day break are all ignored. This hands Hindsight one large,
      coherent transcript per window and lets it do its own chunking and
      fact extraction (its recommended mode — more context = better facts).
    - Legacy mode (block_days None): small SINGLE-DAY blocks, broken on a
      calendar-day change, a size cap (block_messages), or an idle gap
      (gap_hours)."""
    blocks, cur, last_dt, block_day, block_start_dt = [], [], None, None, None
    for msg in messages:
        if not include_system and _is_system(msg):
            continue
        if _media_only(msg["text"]):
            continue
        if cur:
            if block_days is not None:
                span_days = None
                if block_start_dt and msg["dt"]:
                    span_days = (msg["dt"] - block_start_dt).total_seconds() / 86400.0
                should_break = span_days is not None and span_days >= block_days
            else:
                gap = None
                if last_dt and msg["dt"]:
                    gap = (msg["dt"] - last_dt).total_seconds() / 3600.0
                day_changed = bool(msg["dt"] and block_day and msg["dt"].date() != block_day)
                should_break = (len(cur) >= block_messages or day_changed
                                or (gap is not None and gap >= gap_hours))
            if should_break:
                blocks.append(cur)
                cur = []
        if not cur and msg["dt"]:
            block_day = msg["dt"].date()
            block_start_dt = msg["dt"]
        cur.append(msg)
        last_dt = msg["dt"] or last_dt
    if cur:
        blocks.append(cur)
    return blocks


def _apply_aliases(messages, aliases):
    """Rename senders per 'Old Name=New Name' entries — for contact renames or a
    number switch that splits one person across two chats."""
    amap = {}
    for a in (aliases or []):
        if "=" in a:
            old, new = a.split("=", 1)
            amap[old.strip()] = new.strip()
    if amap:
        for m in messages:
            if m.get("sender") in amap:
                m["sender"] = amap[m["sender"]]
    return messages


def _filter_range(messages, since, until):
    """Keep only messages whose date is within [since, until] (YYYY-MM-DD)."""
    if not since and not until:
        return messages
    import datetime
    s = datetime.date.fromisoformat(since) if since else None
    u = datetime.date.fromisoformat(until) if until else None
    kept = []
    for m in messages:
        d = m["dt"].date() if m["dt"] else None
        if d is None:
            continue
        if s and d < s:
            continue
        if u and d > u:
            continue
        kept.append(m)
    return kept


def render_block(chat, block):
    """Render one block into a self-describing transcript for fact extraction.

    Dates are emphasized aggressively so the extractor stamps each fact with
    the right day (a multi-day block otherwise lets undated facts fall back to
    the block's first day):
    - a dated section header (`===== YYYY-MM-DD (Weekday) =====`) opens each
      calendar day,
    - every message carries a full `[YYYY-MM-DD HH:MM]` stamp,
    - messages are separated by a blank line and flattened to one line each, so
      a message boundary is unambiguous vs. a within-message line break."""
    dts = [m["dt"] for m in block if m["dt"]]
    senders = sorted({m["sender"] for m in block if m["sender"]})
    if dts:
        d0, d1 = min(dts), max(dts)
        span = d0.strftime("%Y-%m-%d %H:%M") if d0.date() == d1.date() else \
            f"{d0.strftime('%Y-%m-%d %H:%M')} → {d1.strftime('%Y-%m-%d %H:%M')}"
    else:
        span = "unknown date"
    lines = [f"WhatsApp chat: {chat}",
             f"Participants: {', '.join(senders) or 'unknown'}",
             f"Date range: {span}"]
    cur_day = None
    for m in block:
        if m["dt"] and m["dt"].date() != cur_day:
            cur_day = m["dt"].date()
            lines += ["", f"===== {m['dt'].strftime('%Y-%m-%d (%A)')} ====="]
        t = m["dt"].strftime("%Y-%m-%d %H:%M") if m["dt"] else "unknown time"
        who = m["sender"] or "system"
        text = " ".join(m["text"].split())  # flatten internal line breaks
        lines += ["", f"[{t}] {who}: {text}"]
    first_dt = dts[0].isoformat() if dts else None
    return "\n".join(lines), first_dt, senders, span


def _load_hindsight_config():
    if not HINDSIGHT_CONFIG.exists():
        fail(f"Hindsight config not found at {HINDSIGHT_CONFIG}. Set up the "
             "memory provider first (`hermes memory setup` → hindsight).")
    cfg = json.loads(HINDSIGHT_CONFIG.read_text())
    api_url = cfg.get("api_url")
    if not api_url:
        fail("no api_url in ~/.hermes/hindsight/config.json.")
    return api_url, cfg.get("bank_id") or "hermes", (cfg.get("apiKey") or "").strip()


def _progress(msg):
    """Emit a live progress line to stderr (stdout stays reserved for the
    final JSON result)."""
    import sys
    print(msg, file=sys.stderr, flush=True)


async def _call(method, *pos, auth=None):
    """Call a generated-client coroutine, passing authorization only if the
    endpoint accepts it and a key is configured."""
    import inspect
    kw = {}
    try:
        if auth and "authorization" in inspect.signature(method).parameters:
            kw["authorization"] = auth
    except (ValueError, TypeError):
        pass
    return await method(*pos, **kw)


def _d2(x):
    return x if isinstance(x, dict) else x.to_dict()


async def _poll_operations(api, bank, op_ids, interval, timeout, auth=None):
    """Poll each retain operation until all are terminal or `timeout` seconds
    pass. Emits progress to stderr; returns {operation_id: final_status}."""
    import asyncio
    from hindsight_client_api.api.operations_api import OperationsApi
    ops = OperationsApi(api)
    terminal = {"completed", "failed", "error"}
    status = {op: "pending" for op in op_ids}
    waited = 0
    while True:
        for op in op_ids:
            if status[op] in terminal:
                continue
            try:
                s = _d2(await _call(ops.get_operation_status, bank, op, auth=auth))
                status[op] = s.get("status") or status[op]
            except Exception as e:  # noqa: BLE001
                status[op] = f"poll-error: {str(e)[:60]}"
        done = sum(1 for v in status.values() if v in terminal or v.startswith("poll-error"))
        _progress(f"[{waited}s] operations {done}/{len(op_ids)} finished "
                  f"— {', '.join(f'{v}:{n}' for v, n in _counts(status.values()).items())}")
        if done == len(op_ids) or waited >= timeout:
            break
        await asyncio.sleep(interval)
        waited += interval
    return status


def _counts(values):
    c = {}
    for v in values:
        c[v] = c.get(v, 0) + 1
    return c


async def _bank_summary(api, bank, auth=None):
    """Return {documents, facts} landed in the bank (facts = sum of each
    document's memory_unit_count)."""
    from hindsight_client_api.api.documents_api import DocumentsApi
    docs = DocumentsApi(api)
    try:
        dl = _d2(await _call(docs.list_documents, bank, auth=auth))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
    items = dl.get("items") or []
    facts = 0
    for doc in items:
        try:
            dd = _d2(await _call(docs.get_document, bank, _d2(doc)["id"], auth=auth))
            facts += dd.get("memory_unit_count") or 0
        except Exception:  # noqa: BLE001
            pass
    return {"documents": dl.get("total", len(items)), "facts": facts}


def cmd_preview(args):
    messages = _apply_aliases(
        _filter_range(parse_export(args.file), args.since, args.until), args.alias)
    if not messages:
        fail("no messages parsed in range — check the file / --since / --until.")
    chat = args.chat or _derive_chat(args.file)
    blocks = group_blocks(messages, args.include_system, args.block_messages,
                          args.block_gap_hours, args.block_days)
    dts = [m["dt"] for m in messages if m["dt"]]
    sample = render_block(chat, blocks[0])[0] if blocks else ""
    out({"ok": True, "chat": chat, "messages_parsed": len(messages),
         "system_or_media_skipped": sum(1 for m in messages if _is_system(m) or _media_only(m["text"])),
         "blocks": len(blocks),
         "date_range": [min(dts).isoformat(), max(dts).isoformat()] if dts else None,
         "unparsed_timestamps": sum(1 for m in messages if m["dt"] is None and not m["system"]),
         "sample_block": sample[:1200]})


def cmd_import(args):
    import asyncio
    messages = _apply_aliases(
        _filter_range(parse_export(args.file), args.since, args.until), args.alias)
    if not messages:
        fail("no messages parsed in range — check the file / --since / --until.")
    chat = args.chat or _derive_chat(args.file)
    blocks = group_blocks(messages, args.include_system, args.block_messages,
                          args.block_gap_hours, args.block_days)
    if not blocks:
        fail("nothing to import after filtering system/media messages.")
    api_url, cfg_bank, api_key = _load_hindsight_config()
    bank = args.bank or cfg_bank

    import hindsight_client_api
    from hindsight_client_api.api.memory_api import MemoryApi
    from hindsight_client_api.models.retain_request import RetainRequest
    from hindsight_client_api.models.memory_item import MemoryItem
    from hindsight_client_api.models.timestamp import Timestamp

    def item(block):
        content, first_dt, senders, span = render_block(chat, block)
        meta = {"source": "whatsapp", "chat": str(chat), "when": str(span),
                "participants": ", ".join(senders)[:250]}
        ts = None
        if first_dt:
            try:
                ts = Timestamp(actual_instance=first_dt)
            except Exception:
                ts = None
        return MemoryItem(content=content, timestamp=ts, context="conversation", metadata=meta)

    items = [item(b) for b in blocks]

    async def run():
        cfg = hindsight_client_api.Configuration(host=api_url)
        async with hindsight_client_api.ApiClient(cfg) as api:
            mem = MemoryApi(api)
            auth = api_key or None
            ops, submitted = [], 0
            for i in range(0, len(items), args.batch_size):
                batch = items[i:i + args.batch_size]
                try:
                    req = RetainRequest(items=batch, var_async=True)
                except Exception:
                    req = RetainRequest.from_dict(
                        {"items": [b.to_dict() for b in batch], "async": True})
                resp = await mem.retain_memories(bank, req, authorization=auth)
                submitted += len(batch)
                op = getattr(resp, "operation_id", None)
                if op:
                    ops.append(op)
            statuses = summary = None
            if args.wait and ops:
                _progress(f"submitted {submitted} block(s) in {len(ops)} "
                          f"operation(s); waiting for extraction to finish…")
                statuses = await _poll_operations(
                    api, bank, ops, args.poll_interval, args.wait_timeout, auth)
                summary = await _bank_summary(api, bank, auth)
            return ops, submitted, statuses, summary

    try:
        ops, submitted, statuses, summary = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        fail(f"retain failed: {e}")

    result = {"ok": True, "chat": chat, "bank": bank,
              "messages_parsed": len(messages), "blocks_submitted": submitted,
              "batches": len(ops), "operation_ids": ops[:50]}
    if statuses is not None:
        incomplete = [o for o, s in statuses.items() if s != "completed"]
        result["status_counts"] = _counts(statuses.values())
        result["all_completed"] = not incomplete
        if incomplete:
            result["incomplete_operations"] = incomplete
        result["bank_summary"] = summary
        result["note"] = ("Import finished. bank_summary shows documents/facts "
                          "landed. If any operation did not complete, re-run the "
                          "import for that date range or ask the user.")
    else:
        result["note"] = ("Retains are processing in the background. Monitor with "
                          "`status --bank <bank> --operation-id <id> --wait`, or "
                          "re-run import with --wait to block until finished.")
    out(result)


def cmd_status(args):
    """Report progress of retain operations and how many documents/facts are in
    the bank — the skill's own monitor, so no external polling is needed."""
    import asyncio
    import hindsight_client_api
    from hindsight_client_api.api.operations_api import OperationsApi
    api_url, cfg_bank, api_key = _load_hindsight_config()
    bank = args.bank or cfg_bank

    async def run():
        cfg = hindsight_client_api.Configuration(host=api_url)
        async with hindsight_client_api.ApiClient(cfg) as api:
            auth = api_key or None
            statuses = None
            if args.operation_id:
                if args.wait:
                    statuses = await _poll_operations(
                        api, bank, args.operation_id,
                        args.poll_interval, args.wait_timeout, auth)
                else:
                    ops = OperationsApi(api)
                    statuses = {}
                    for op in args.operation_id:
                        try:
                            s = _d2(await _call(ops.get_operation_status, bank, op, auth=auth))
                            statuses[op] = s.get("status")
                        except Exception as e:  # noqa: BLE001
                            statuses[op] = f"error: {str(e)[:60]}"
            summary = await _bank_summary(api, bank, auth)
            return statuses, summary

    try:
        statuses, summary = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        fail(f"status check failed: {e}")

    res = {"ok": True, "bank": bank, "bank_summary": summary}
    if statuses is not None:
        res["operation_status"] = statuses
        res["status_counts"] = _counts(statuses.values())
        res["all_completed"] = all(s == "completed" for s in statuses.values())
    out(res)


def _add_wait_args(g):
    g.add_argument("--wait", action="store_true",
                   help="block and poll until the retain operations finish, then "
                        "report documents/facts landed")
    g.add_argument("--poll-interval", dest="poll_interval", type=int, default=20,
                   help="seconds between status polls when --wait (default 20)")
    g.add_argument("--wait-timeout", dest="wait_timeout", type=int, default=3600,
                   help="max seconds to wait before returning (default 3600)")


def main():
    p = argparse.ArgumentParser(prog="wa_backfill", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(g):
        g.add_argument("--file", required=True,
                       help="WhatsApp export .zip (the skill extracts the "
                            "chat inside) or a plain _chat.txt")
        g.add_argument("--chat", help="chat name (default: derived from filename)")
        g.add_argument("--block-days", dest="block_days", type=float, default=None,
                       help="window mode: one block per this many days (e.g. 7). "
                            "Disables --block-messages/--block-gap-hours/day splits "
                            "and lets Hindsight chunk each window itself (recommended)")
        g.add_argument("--block-messages", dest="block_messages", type=int, default=10,
                       help="legacy mode: max messages per block (default 10; also "
                            "split at day boundaries). Ignored when --block-days is set")
        g.add_argument("--block-gap-hours", dest="block_gap_hours", type=float, default=6.0,
                       help="legacy mode: a gap this many hours starts a new block "
                            "(default 6). Ignored when --block-days is set")
        g.add_argument("--since", help="only messages on/after this date (YYYY-MM-DD)")
        g.add_argument("--until", help="only messages on/before this date (YYYY-MM-DD)")
        g.add_argument("--alias", action="append", default=[],
                       help="rename a sender: \"Old Name=New Name\" (repeatable)")
        g.add_argument("--include-system", dest="include_system", action="store_true",
                       help="keep system/notice lines (default: skip)")

    g = sub.add_parser("preview", help="parse only; print stats and a sample block")
    common(g)
    g.set_defaults(func=cmd_preview)

    g = sub.add_parser("import", help="parse and retain into Hindsight")
    common(g)
    g.add_argument("--bank", help="Hindsight bank id (default: from config)")
    g.add_argument("--batch-size", dest="batch_size", type=int, default=10,
                   help="blocks per retain request (default 10)")
    _add_wait_args(g)
    g.set_defaults(func=cmd_import)

    g = sub.add_parser("status",
                       help="report retain-operation progress and bank fact counts")
    g.add_argument("--bank", help="Hindsight bank id (default: from config)")
    g.add_argument("--operation-id", dest="operation_id", action="append", default=[],
                   help="operation id to check, from an earlier import (repeatable)")
    _add_wait_args(g)
    g.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
