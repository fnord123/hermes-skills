---
name: whatsapp-backfill
description: >
  Import a WhatsApp chat export into the agent's long-term memory so you can ask
  about those conversations later ("what did Dan say about the regatta?"). Takes
  a WhatsApp "Export chat" .txt file, groups the messages into conversation
  blocks, and stores them in the same Hindsight memory bank the agent recalls
  from. PREFER THIS SKILL whenever the user wants to load, import, ingest, or
  remember a WhatsApp conversation/history/export. It handles existing history
  only (WhatsApp has no live-history API — the user exports the chat from the
  app). Activate on any of: "import my WhatsApp", "WhatsApp export", "load this
  chat into memory", "remember this WhatsApp conversation", "backfill WhatsApp",
  "add my WhatsApp history", "ingest WhatsApp chat".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [WhatsApp, Memory, Hindsight, Import, Backfill, Productivity]
---

# whatsapp-backfill — import a WhatsApp export into memory

Load an exported WhatsApp chat into the agent's Hindsight memory so its contents
become recallable later. The user exports a chat from WhatsApp (**Chat → Export
chat → Without media**), which produces a `.txt` file; this skill parses it,
groups messages into conversation blocks, and retains them into the memory bank
the agent already uses. Afterward the user can just ask the agent about the
conversation.

## When to use

- The user wants an existing WhatsApp conversation remembered/queryable: "import
  this WhatsApp export", "remember this chat", "load my WhatsApp history".

## When NOT to use

- **Live/ongoing capture.** This imports an exported file; it does not stream new
  messages. There's no WhatsApp history API, so a file export is the only source.
- **Non-WhatsApp text.** For arbitrary notes/files, use the agent's normal memory
  directly; this skill is specifically for WhatsApp `Export chat` `.txt` files.

## The tool

One script at `~/.hermes/skills/whatsapp-backfill/examples/wa_backfill.py`,
invoked as `python3 <path> <command> [args]`. Each call prints ONE JSON object
(`{"ok": true, ...}`; failures are `{"ok": false, "error": "..."}` with exit 1).

| Command | Purpose |
|---|---|
| `preview --file <chat.txt> [--chat "<name>"]` | Parse the export and report stats (messages, blocks, date range, what was skipped) plus a sample block. No memory is written. |
| `import --file <chat.txt> [--chat "<name>"] [--bank <id>]` | Parse and store the conversation into Hindsight memory. Returns how many blocks were submitted and the operation ids. |
| `status --bank <id> [--operation-id <id> …] [--wait]` | Report how many documents/facts are in the bank, and the status of specific import operations. Use this to monitor an import — no external tooling needed. |

Grouping (both preview and import):
- `--block-days N` — **preferred.** Put one document per N-day window (e.g. 7)
  and let Hindsight chunk and date-stamp it itself. Disables the caps below.
- `--block-messages N` (default 10) / `--block-gap-hours H` (default 6) —
  legacy small-block mode; ignored when `--block-days` is set.

Other options: `--chat "<name>"` (label; defaults to the filename), `--since` /
`--until YYYY-MM-DD` (limit the date range), `--alias "Old=New"` (rename a
sender; repeatable), `--include-system` (keep join/left/encryption notices;
default skips them).

Monitoring: add `--wait` to `import` to block until extraction finishes and
report the documents/facts landed; or submit without `--wait` and call `status
--operation-id <id> --wait` later. Progress prints to stderr; the final JSON is
on stdout.

## How to run it

1. **Always `preview` first** and show the user the stats — message count, date
   range, and how many blocks will be stored. This catches a wrong or malformed
   file before anything is written.
2. **Confirm with the user**, then run `import` (prefer `--block-days 7`). The
   chat's contents go into the agent's memory.
3. **Monitor the import** with `--wait` on `import`, or a follow-up `status
   --operation-id <id> --wait`. Report the documents/facts landed and whether
   every operation completed. Extraction runs in the background and can take a
   while; once it finishes the user can ask about the conversation normally.

## Turning the user's words into calls

| User said | Call |
|---|---|
| "import my WhatsApp export at ~/Downloads/chat.txt" | `preview --file ~/Downloads/chat.txt` → confirm → `import --file ~/Downloads/chat.txt` |
| "load this WhatsApp chat with the sailing group" | `preview --file <path> --chat "Sailing Group"` → confirm → `import …` |
| "remember my chat with Mom, keep the system messages" | `import --file <path> --chat "Mom" --include-system` |

## Output shape

- `preview` → `{"ok": true, "chat": "...", "messages_parsed": 812, "system_or_media_skipped": 47, "blocks": 34, "date_range": ["2026-01-02T…", "2026-06-30T…"], "unparsed_timestamps": 0, "sample_block": "..."}`
- `import` → `{"ok": true, "chat": "...", "bank": "David", "messages_parsed": 812, "blocks_submitted": 34, "batches": 4, "operation_ids": [...], "note": "..."}`. With `--wait`, it also includes `status_counts`, `all_completed`, and `bank_summary` (`{"documents": N, "facts": N}`).
- `status` → `{"ok": true, "bank": "...", "bank_summary": {"documents": 1, "facts": 27}, "operation_status": {...}, "status_counts": {"completed": 1}, "all_completed": true}`.

After `preview`, relay the message count, date range, and block count so the user
can confirm it's the right export. After `import`, tell them it's stored; if you
used `--wait` (or a follow-up `status`), relay the documents/facts landed and
whether every operation completed. Otherwise relay the `note` and offer to
monitor with `status`.

## When a command reports an error

- `"no messages parsed…"` → the file isn't a WhatsApp `Export chat` `.txt` (wrong
  file, or an unusual locale format). Ask the user to re-export via **Chat →
  Export chat → Without media**.
- `"Hindsight config not found…"` / `"no api_url…"` → the memory provider isn't
  set up. Tell the user to run `hermes memory setup` and pick Hindsight.
- `"retain failed…"` → the memory server rejected or timed out on the request.
  Report it; do not retry in a loop.

Always ask the user for guidance when there is an error; do not proactively try
to resolve errors yourself.

## Empty results

`preview` with `messages_parsed: 0` means nothing was recognized as WhatsApp
messages — say so plainly and ask for a proper `Export chat` `.txt`.
