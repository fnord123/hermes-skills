---
name: gmail-thread-extract
description: >
  Split a saved Gmail thread page into one plain-text file per message,
  keeping only each message's own words — quoted reply text is removed — with
  the sender and the send date as the file's header. PREFER THIS SKILL
  whenever the user shares a Gmail thread (a saved .html page of a whole
  conversation, or a link they ask to be broken up message by message) and
  wants the individual messages out of it: extracting the thread into separate
  files, pulling one message out of a long thread, or tidying a thread export
  so replies don't drown the original words. Not for single standalone emails,
  inboxes, or sending mail — this only reads a saved thread page. Activate on
  any of: "extract the messages from this thread", "split this thread into
  separate files", "pull out each message", "save this conversation one file
  per message", "get the original messages out of this Gmail thread", "the
  quoted text is hiding the actual replies", or anything that sounds like
  unpacking a Gmail thread into its individual messages.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [Email, Gmail, Extraction, Productivity]
    requires_toolsets: [terminal, file]
---

# gmail-thread-extract — split a Gmail thread into one file per message

Turn a saved Gmail thread page (the whole conversation, as one HTML file) into
a folder of plain-text files — one per message, in chronological order. Each
file holds that message's own words only: the quoted reply text is stripped
out, and the file starts with the sender and the send date.

## When to use

Activate when the user has a saved Gmail thread page and wants to:
- **Extract** every message into its own file ("split this thread into
  separate files").
- **Pull out** the individual messages from a long conversation ("get the
  original messages out of this thread").
- **Tidy** a thread export so the quoted replies don't drown each message's
  own words.

## When NOT to use

- **A single standalone email** — there is no thread to split; just read the
  file.
- **Inboxes, accounts, or sending** — this skill only reads one saved thread
  page; it never touches a live mailbox.
- **Threads saved from a different mail client** — the page layout must be a
  Gmail thread view; anything else comes back with no messages found.

## The tool

One script at `${HERMES_SKILL_DIR}/scripts/extract_thread.py`, invoked as
`python3 <path> extract --source <thread-file> --outdir <thread-directory>`.
Each call prints ONE JSON object on stdout (`{"ok": true, ...}`; failures are
`{"ok": false, "error": "..."}` with exit 1).

| Verb | Purpose |
|---|---|
| `extract --source <thread-file> --outdir <thread-dir>` | Splits the saved thread page into one plain-text file per message under `<thread-dir>/messages/`, named `NNN_YYYY-MM-DD_HHMM_sender.txt` in chronological order. Re-running against the same thread directory replaces the message files. |

## Turning the user's words into calls

| User said | Call |
|---|---|
| "Extract the messages from this thread" (with a saved file) | `extract --source <the saved .html file> --outdir ~/<thread-name>-thread` |
| "Put each message in its own file under ~/foo" | `extract --source <file> --outdir ~/foo` |

Parsing notes:
- **`--source`** is the saved thread page itself — usually the `.html` file the
  user attached or pointed to. Use the path as given.
- **`--outdir`** is the thread's own directory: message files always land in
  `<outdir>/messages/`. If the user named a folder, use it. If not, make one
  after the thread's subject (lowercase, hyphenated), e.g.
  `~/california-billionaires-tax-thread`.
- One call does the whole thread. Do not loop message by message.

## Output shape

```json
{"ok": true, "subject": "Re: California billionaire's tax", "count": 72,
 "outdir": "/home/dputzolu/california-billionaires-tax-thread/messages",
 "span": "2026-06-18 17:05 .. 2026-08-24 00:05",
 "files": [".../001_2026-06-18_1705_omkar-mate.txt", "..."],
 "empty": []}
```

Each message file looks like:

```
From: David Putzolu <dputzolu@gmail.com>
Date: Sat, Aug 22, 2026 at 12:28 PM
To: xginvesting@googlegroups.com
Subject: Re: California billionaire's tax

I didn't get the impression Omkar was suggesting this...
```

Report back the `count` and the `span` ("72 messages, Jun 18 to Aug 24"), and
the folder. **If the `empty` array is non-empty, say so**: those messages were
pure quoted text with no words of their own, so their files carry a
placeholder line instead.

## A typical session

```
"Here's the thread as a saved file — get each message into its own file,
 without the quoted text."
  → extract --source ~/downloads/thread.html --outdir ~/california-billionaires-tax-thread
  → "Done: 72 messages, one file each, in
     ~/california-billionaires-tax-thread/messages — Jun 18 to Aug 24. All had
     their own words."

"Pull the August 16 exchange out of the same thread."
  → (already extracted — read the files whose date is 2026-08-16)
  → "Here are the six messages from August 16."
```

## When a verb reports an error

- `"no messages were found"` → the file isn't a saved Gmail thread page. Ask
  the user to re-save the thread from Gmail (the view showing every message of
  the conversation) and point you at that file.
- `"the thread file isn't at ..."` → the given path doesn't exist. Check the
  path against the file the user actually shared; don't guess.
- Any dependency error (`ModuleNotFoundError: bs4` or `lxml`) → the skill's
  packages aren't installed. Point the user to `README.md`.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`count` of 0 is impossible — the call fails instead — but an all-`empty`
response (every message was pure quoted text) is real on quote-only chains:
say plainly that none of the messages carried its own words.
