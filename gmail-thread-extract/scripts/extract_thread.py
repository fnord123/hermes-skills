#!/usr/bin/env python3
"""Split a saved Gmail thread page into one plain-text file per message.

Each output file holds the message's own words only — quoted reply text and
"On ... wrote:" attributions are stripped — with the sender and the send date
as a header. Files are written in chronological order as
NNN_YYYY-MM-DD_HHMM_sender.txt under <outdir>/messages/.

See the skill's README.md for how a saved Gmail thread page is structured and
why the stripping rules are what they are.
"""

import copy
import os
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from skill_json import ArgumentParser, fail, guard, ok

# Block-level tags: text between them becomes line breaks.
BLOCK_TAGS = {"div", "p", "tr", "blockquote", "li", "table", "h1", "h2",
              "h3", "h4", "h5", "h6", "pre", "section", "article"}

DATE_FMTS = [
    "%a, %b %d, %Y at %I:%M %p",   # Thu, Jun 18, 2026 at 5:05 PM
    "%a, %b %d, %Y at %-I:%M %p",  # 12-hour without leading zero
    "%a, %b %d, %Y at %H:%M",      # 24-hour
]

# Google Groups appends this system footer to every posting. It is boilerplate
# (unsubscribe instructions + a link to the post), not part of the person's
# message.
GFOOTER_MARKER = "You received this message because you are subscribed"

# Matches "On <date> <sender> <addr> wrote:" attribution lines. Dates use
# either "at H:MM AM/PM" (US) or a bare ", H:MM" (international) form.
ATTR_RE = re.compile(
    r"On\s+\w{3},\s+\w{3}\s+\d{1,2},\s+\d{4}\s+"
    r"(?:at\s+)?\d{1,2}:\d{2}(?:\s*[AP]M)?\s+"
    r".{0,120}?\bwrote\s*:\s*$",
    re.IGNORECASE,
)

# Trailing "To view this discussion visit <link>." boilerplate line.
MSGID_FOOTER_RE = re.compile(
    r"To view this discussion visit\s+https://groups\.google\.com/d/msgid/\S+",
    re.IGNORECASE,
)


def parse_date(raw: str):
    """Parse a thread header date like 'Thu, Jun 18, 2026 at 5:05 PM'."""
    s = " ".join(raw.split())
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def render_el(node) -> str:
    """Render a fragment of the message body into readable text.

    Block-level elements become line breaks; inline text keeps its source
    spacing. clean_text() tidies the whitespace afterwards.
    """
    out: list[str] = []

    def rec(n) -> None:
        if isinstance(n, NavigableString):
            out.append(str(n))
            return
        if not isinstance(n, Tag):
            return
        if n.name == "br":
            out.append("\n")
            return
        block = n.name in BLOCK_TAGS
        if block:
            out.append("\n")
        for child in n.children:
            rec(child)
        if block:
            out.append("\n")

    rec(node)
    return "".join(out)


def clean_text(html_text: str) -> str:
    """Normalize extracted text: tidy whitespace, drop stray lines."""
    t = html_text.replace("\xa0", " ").replace("\u200b", "")
    lines = [ln.strip() for ln in t.splitlines()]
    cleaned = []
    prev_blank = True  # treat start as blank to trim leading empties
    for ln in lines:
        blank = ln == ""
        if blank and prev_blank:
            continue
        cleaned.append(ln)
        prev_blank = blank
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    return "\n".join(cleaned)


def _quote_depth(node) -> int:
    """Number of quoted-reply ancestors of a node."""
    depth = 0
    anc = node.parent
    while anc is not None and getattr(anc, "name", None):
        if anc.name == "blockquote" and "gmail_quote" in (anc.get("class") or []):
            depth += 1
        anc = anc.parent
    return depth


def _strip_quoted(body_wrapper) -> Tag:
    """
    Return a copy of the body with all quoted reply material removed.

    In a saved Gmail thread page, quoted reply text is always:
      * wrapped in a <blockquote class="gmail_quote">  (the quoted body), and
      * preceded by an "On <date> <name> wrote:" attribution line, usually
        inside a <div class="gmail_attr"> but occasionally a bare <span>.
    The sender's own NEW words are everything that is NOT inside a
    blockquote.gmail_quote and NOT an attribution line. New words can appear
    before, after, or interleaved with the quote, so the quote subtrees are
    removed in place rather than cutting at a boundary.
    """
    w = copy.deepcopy(body_wrapper)

    # Remove the outermost quoted-reply subtrees (nested quotes ride along
    # inside their parent, so only top-level ones are needed).
    for bq in w.find_all("blockquote", class_="gmail_quote"):
        if bq.find_parent("blockquote", class_="gmail_quote") is None:
            bq.decompose()
    # Remove "On ... wrote:" attribution lines (the div form).
    for attr in w.find_all("div", class_="gmail_attr"):
        attr.decompose()
    # Remove any remaining depth-0 attribution line regardless of its tag
    # (covers the bare-<span> form some clients emit, where the text is split
    # across the span, a mailto link, and more text).
    matchers = [
        el for el in w.find_all(True)
        if _quote_depth(el) == 0 and ATTR_RE.search(el.get_text(" ", strip=True))
    ]
    for el in matchers:
        # Only decompose the innermost matching element (an ancestor's text
        # matches only because it contains this one).
        if any(inner is not el and inner in el.descendants for inner in matchers):
            continue
        el.decompose()
    # Remove the "[Quoted text hidden]" placeholder.
    for node in w.find_all(string=lambda s: s and s.strip() == "[Quoted text hidden]"):
        node.replace_with(NavigableString(" "))
    return w


def extract_new_content(body_wrapper) -> str:
    """Return the message's own words, with all quoted reply text removed."""
    w = _strip_quoted(body_wrapper)
    text = render_el(w)

    # Drop the system footer (everything from the marker on), together with
    # the lone "--" separator line that precedes it.
    idx = text.find(GFOOTER_MARKER)
    if idx != -1:
        text = text[:idx]
        text = text.rstrip()
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip() == "--":
            lines.pop()
        text = "\n".join(lines)

    # A second footer variant: just the trailing "To view this discussion
    # visit ..." line (some posts omit the preamble above). It is always the
    # last line of the body, so only strip it in that position.
    text = text.rstrip()
    lines = text.splitlines()
    if lines and MSGID_FOOTER_RE.search(lines[-1].strip()):
        lines.pop()
        text = "\n".join(lines)
    return text


def find_body_wrapper(msg_table) -> Tag | None:
    """Locate the cell that holds the message body content."""
    w = msg_table.select_one("div[style*='overflow']")
    if w is not None:
        return w
    # Fallback: the last <td> of the last row.
    rows = msg_table.find_all("tr")
    if rows:
        tds = rows[-1].find_all("td")
        if tds:
            return tds[-1]
    return None


def extract_messages(html: str):
    """Return (messages, soup); each message is a dict with sender, date, to, reply_to, body."""
    soup = BeautifulSoup(html, "lxml")
    messages = []
    for table in soup.select("table.message"):
        header_rows = table.find_all("tr")
        if not header_rows:
            continue
        first_cells = header_rows[0].find_all("td")
        if not first_cells:
            continue

        sender_raw = " ".join(first_cells[0].get_text(" ", strip=True).split())
        m = re.match(r"^(.*?)\s*<([^<>]+)>$", sender_raw)
        if m:
            name, email = m.group(1).strip(), m.group(2).strip()
        else:
            name, email = sender_raw, ""

        date_raw = (first_cells[1].get_text(" ", strip=True)
                    if len(first_cells) > 1 else "")
        dt = parse_date(date_raw)

        recipient = table.select_one("font.recipient, .recipient")
        to = reply_to = ""
        if recipient is not None:
            for d in recipient.find_all("div"):
                txt = d.get_text(" ", strip=True)
                if d.get("class") and "replyto" in d["class"]:
                    reply_to = txt.removeprefix("Reply-To:").strip()
                elif txt.lower().startswith("to:"):
                    to = txt.removeprefix("To:").strip()

        body_wrapper = find_body_wrapper(table)
        body = clean_text(extract_new_content(body_wrapper)) if body_wrapper else ""

        messages.append({
            "sender": name, "email": email, "date": dt,
            "date_raw": date_raw, "to": to, "reply_to": reply_to,
            "body": body,
        })
    return messages, soup


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "unknown"


@guard
def main():
    ap = ArgumentParser(description="Split a saved Gmail thread page into one file per message.")
    ap.add_argument("verb", choices=["extract"])
    ap.add_argument("--source", required=True, help="saved Gmail thread page (HTML file)")
    ap.add_argument("--outdir", required=True,
                    help="thread directory; message files land in <outdir>/messages/")
    args = ap.parse_args()

    path = os.path.expanduser(args.source)
    if not os.path.isfile(path):
        fail("the thread file isn't at %s" % path)

    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()

    messages, soup = extract_messages(html)
    if not messages:
        fail("no messages were found — the file doesn't look like a saved Gmail thread page")

    subject = "Gmail thread"
    h = soup.select_one(".maincontent b")
    if h:
        subject = h.get_text(strip=True)

    # Chronological order (stable for identical timestamps).
    messages.sort(key=lambda m: (m["date"] is None, m["date"] or datetime.min))

    out_dir = os.path.join(os.path.expanduser(args.outdir), "messages")
    os.makedirs(out_dir, exist_ok=True)
    # Re-running against the same thread directory replaces the message files.
    for old in os.listdir(out_dir):
        if old.endswith(".txt"):
            os.remove(os.path.join(out_dir, old))

    files, empty = [], []
    for i, msg in enumerate(messages, 1):
        dt = msg["date"]
        stamp = dt.strftime("%Y-%m-%d_%H%M") if dt else msg["date_raw"] or "undated"
        fname = "%03d_%s_%s.txt" % (i, stamp, slugify(msg["sender"]))
        lines = [
            "From: %s <%s>" % (msg["sender"], msg["email"]) if msg["email"]
            else "From: %s" % msg["sender"],
            "Date: %s" % msg["date_raw"],
        ]
        if msg["to"]:
            lines.append("To: %s" % msg["to"])
        if msg["reply_to"]:
            lines.append("Reply-To: %s" % msg["reply_to"])
        lines.append("Subject: %s" % subject)
        lines.append("")
        lines.append(msg["body"] if msg["body"]
                     else "[no words of its own — the body was entirely quoted reply text]")
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        files.append(os.path.join(out_dir, fname))
        if not msg["body"]:
            empty.append(fname)

    d0 = min((m["date"] for m in messages if m["date"]), default=None)
    d1 = max((m["date"] for m in messages if m["date"]), default=None)
    span = ""
    if d0 is not None and d1 is not None:
        span = "%s .. %s" % (d0.strftime("%Y-%m-%d %H:%M"), d1.strftime("%Y-%m-%d %H:%M"))

    ok(subject=subject, count=len(messages), outdir=out_dir, span=span,
       files=files, empty=empty)


if __name__ == "__main__":
    main()
