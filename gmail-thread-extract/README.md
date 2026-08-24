# gmail-thread-extract

Split a saved Gmail thread page into one plain-text file per message, keeping
only each message's own words.

## Why this exists

A saved Gmail thread page is one HTML file holding an entire conversation —
often dozens or hundreds of messages. Each message's own words are buried
under (or interleaved with) the quoted text of every message it replies to,
plus the "On … wrote:" attribution lines and, for Google Groups postings, the
system footer. Reading one, or reasoning over the whole thread, is miserable
against that wall of nesting.

This skill gives the model one verb — `extract` — that does the whole job. The
model never parses the HTML itself; it hands over the file path and a folder,
and gets back a chronological folder of clean plain-text files. That is the
whole point: the layout is subtle enough that a small model doing it ad hoc
gets it wrong (see below), so the subtlety is paid for once in the script.

## How a saved Gmail thread page is structured

Each message is a `<table class="message">` with three header cells:

1. sender — `Name <address>`,
2. send date — `Thu, Jun 18, 2026 at 5:05 PM`,
3. recipient(s) — `To: …` and optionally `Reply-To: …`.

The body sits in a `div[style*='overflow']` deeper in the table. Quoted reply
material is what the extraction removes, and it has a strict shape:

- **The quoted body** is wrapped in `<blockquote class="gmail_quote">`, nested
  to arbitrary depth (a reply to a reply quotes the whole chain).
- **The attribution line** — `On <date> <name> <addr> wrote:` — precedes each
  quoted body. In most messages it is a `<div class="gmail_attr">`, but some
  clients emit it as a bare `<span>` with the address split across a mailto
  link, so it cannot be found by class alone.
- **`[Quoted text hidden]`** — Gmail's placeholder where it elided old quoted
  text; pure noise to drop.

The message's own NEW words are everything that is *not* inside a
`blockquote.gmail_quote` and *not* an attribution line.

## The three traps a naive extraction falls into

These were all real, on a single 72-message thread, and each one silently
corrupts the output rather than erroring:

1. **Cutting at the first quote drops real text.** New words can appear
   *before, after, or interleaved with* the quote. A message whose reply
   follows the quoted text would come back empty if you cut at the first
   `blockquote`. So the script removes every quote subtree *in place* and keeps
   the rest, instead of slicing at a boundary.
2. **The attribution isn't always a `gmail_attr` div.** When it's a bare
   `<span>`, a class-based removal misses it and the "On … wrote:" line leaks
   into the output as if it were the person's own words. The script matches the
   attribution textually at quote-depth 0, regardless of its tag.
3. **The Google Groups footer is boilerplate, not content.** Groups appends
   "You received this message… / To unsubscribe… / To view this discussion
   visit …" (and a lone `--` separator) to every posting. It has two variants
   (with and without the "You received" preamble). Both are stripped, but only
   the trailing `--` when it precedes the footer — a genuine user signature
   like "Sent from my Palm Pilot" must survive.

## Output

`extract --source <file> --outdir <dir>` writes `<dir>/messages/
NNN_YYYY-MM-DD_HHMM_sender.txt`, one file per message in chronological order.
Each file is the header (`From`, `Date`, `To`, `Reply-To`, `Subject`) followed
by that message's own words. Re-running against the same `<dir>` replaces the
`messages/` files, so a second call against the same thread is idempotent.

The JSON `count` is the number of messages, `span` the first-to-last send
time, and `empty` lists the files whose body was entirely quoted text (they
carry a placeholder line instead of words).

## Setup

```
python3 -m pip install -r scripts/requirements.txt
```

`beautifulsoup4` and `lxml` are the only third-party dependencies. The script
reads the thread file you point it at and writes to the folder you name; it
holds no credentials and touches no mailbox.
