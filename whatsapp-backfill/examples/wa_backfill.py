#!/usr/bin/env python3
"""wa_backfill.py — import a WhatsApp "Export chat" .txt into Hindsight memory.

Parses a WhatsApp chat export, groups messages into coherent conversation
blocks, and retains each block into the same Hindsight bank the Hermes agent
already uses — so the agent can later answer questions about the conversation
(via its normal memory recall). Retains are submitted asynchronously in
batches, because Hindsight extracts facts with an LLM in the background.

WhatsApp has no message-history API; the supported source of existing history
is the app's own "Export chat" (Chat → Export chat → Without media). This tool
imports that .txt.

Commands (each prints ONE JSON object on stdout; exit 1 on error):
  preview --file <chat.txt> [--chat "<name>"]        parse only: stats + samples
  import  --file <chat.txt> [--chat "<name>"] [--bank <id>]
          [--block-messages N] [--block-gap-hours H] [--include-system]
                                                     parse + retain into Hindsight

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

HINDSIGHT_CONFIG = Path.home() / ".hermes" / "hindsight" / "config.json"

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


def parse_export(path):
    """Return a list of message dicts: {dt: datetime|None, sender, text, system}."""
    text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
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


def group_blocks(messages, include_system, block_messages, gap_hours):
    """Group consecutive messages into coherent conversation blocks. A block
    breaks on size (block_messages) or a long time gap (gap_hours)."""
    blocks, cur, last_dt = [], [], None
    for msg in messages:
        if not include_system and _is_system(msg):
            continue
        if _media_only(msg["text"]):
            continue
        if cur:
            gap = None
            if last_dt and msg["dt"]:
                gap = (msg["dt"] - last_dt).total_seconds() / 3600.0
            if len(cur) >= block_messages or (gap is not None and gap >= gap_hours):
                blocks.append(cur)
                cur = []
        cur.append(msg)
        last_dt = msg["dt"] or last_dt
    if cur:
        blocks.append(cur)
    return blocks


def render_block(chat, block):
    """Render one block into a self-describing transcript for fact extraction."""
    dts = [m["dt"] for m in block if m["dt"]]
    senders = sorted({m["sender"] for m in block if m["sender"]})
    if dts:
        d0, d1 = min(dts), max(dts)
        span = d0.strftime("%Y-%m-%d %H:%M") if d0.date() == d1.date() else \
            f"{d0.strftime('%Y-%m-%d %H:%M')} → {d1.strftime('%Y-%m-%d %H:%M')}"
    else:
        span = "unknown date"
    lines = [f"WhatsApp chat: {chat}", f"When: {span}",
             f"Participants: {', '.join(senders) or 'unknown'}", ""]
    for m in block:
        t = m["dt"].strftime("%H:%M") if m["dt"] else "--:--"
        who = m["sender"] or "system"
        lines.append(f"[{t}] {who}: {m['text'].strip()}")
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


def cmd_preview(args):
    messages = parse_export(args.file)
    if not messages:
        fail("no messages parsed — is this a WhatsApp 'Export chat' .txt?")
    chat = args.chat or Path(args.file).stem.replace("WhatsApp Chat with ", "").strip()
    blocks = group_blocks(messages, args.include_system, args.block_messages, args.block_gap_hours)
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
    messages = parse_export(args.file)
    if not messages:
        fail("no messages parsed — is this a WhatsApp 'Export chat' .txt?")
    chat = args.chat or Path(args.file).stem.replace("WhatsApp Chat with ", "").strip()
    blocks = group_blocks(messages, args.include_system, args.block_messages, args.block_gap_hours)
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
            return ops, submitted

    try:
        ops, submitted = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        fail(f"retain failed: {e}")

    out({"ok": True, "chat": chat, "bank": bank, "messages_parsed": len(messages),
         "blocks_submitted": submitted, "batches": len(ops),
         "operation_ids": ops[:20],
         "note": "Retains are processing in the background on the Hindsight "
                 "server; facts become recallable once the worker finishes "
                 "(can take a while). Ask the agent afterward to query them."})


def main():
    p = argparse.ArgumentParser(prog="wa_backfill", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(g):
        g.add_argument("--file", required=True, help="WhatsApp Export chat .txt")
        g.add_argument("--chat", help="chat name (default: derived from filename)")
        g.add_argument("--block-messages", dest="block_messages", type=int, default=30,
                       help="max messages per conversation block (default 30)")
        g.add_argument("--block-gap-hours", dest="block_gap_hours", type=float, default=6.0,
                       help="a gap this many hours starts a new block (default 6)")
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
    g.set_defaults(func=cmd_import)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
