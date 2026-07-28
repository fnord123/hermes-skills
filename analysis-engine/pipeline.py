#!/usr/bin/env python3
"""Evidence pipeline: locate every citation, judge it in context, and report at phase level.

One engine, many domains. A skill supplies a Pipeline() describing its board, its report
filenames and its worker profiles; the engine supplies everything that is not domain-specific -
fetching sources, locating a quoted sentence inside them, extracting the enclosing section,
sizing the judging work into cards that fit, sweeping for stragglers, and tallying the result.

This is extracted from the rx-review pipeline after it ran end to end, so every constant here
was paid for:

  * Card bodies stay under 8KB. build_worker_context() caps task.body at
    _CTX_MAX_BODY_BYTES and appends a truncation marker rather than failing, so a 36KB body
    silently delivers its first two items. Items go to a file; the card names the file.
  * Cards are sized to a 20-minute ceiling from MEASURED rates, and the runtime cap sits
    above that target rather than at it - a cap set at the expected duration turns ordinary
    variance into a timeout.
  * Sources are matched by document identifier, never by host. Matching pmc.ncbi.nlm.nih.gov
    on host alone returned the largest cached article for every citation and checked 53 of 69
    against the wrong paper.
  * A measurement that looks like success is not trusted blindly: a rate-limited page returns
    HTTP 200 with a few hundred bytes, which reads as "small page" and packs 15 heavy articles
    into one card.
  * Guards fail closed. An unreadable input is an error, never "nothing found".

Nothing in here is model-facing. The model sees the card bodies a domain config supplies, in
domain vocabulary; it never sees a board name, a profile, or this file's existence.
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERMES = os.path.expanduser("~/.local/bin/hermes")

# ── sizing ──────────────────────────────────────────────────────────────────
# Measured over completed judging cards: median 0.78 min/item, p90 1.80. Ten items at the p90
# is ~18 minutes against a 30-minute cap. An earlier version used 10 min/item taken from the
# one card that FAILED - sizing a fleet from its slowest member.
CARD_RUNTIME_MINUTES = 30
EST_MINUTES_PER_ITEM = 1.8
MAX_ITEMS_PER_CARD = 10
CARD_BUDGET_CHARS = 36_000
KANBAN_BODY_CAP = 8 * 1024

# A section longer than this is centred on the match rather than passed whole.
MAX_SECTION_CHARS = 5000
CONTEXT_IF_NO_SECTION = 3000
CHARS_PER_TOKEN = 4

# A page under this is a bot wall or an interstitial, not a short article.
MIN_PLAUSIBLE_CHARS = 2000
MAX_MEASURE_WORKERS = 4
PER_HOST_DELAY_SECONDS = 0.4

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

HEADING_PATTERNS = [
    r"^\s*(#{1,6})\s+(.{3,80})\s*$",                            # markdown
    r"^\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z ,/()-]{5,70})\s*$",   # 5.9 Laboratory Abnormalities
    r"^\s*([A-Z][A-Z &/-]{5,60})\s*$",                          # ALL CAPS
]

# Site chrome matches the ALL-CAPS heading pattern perfectly, and a junk heading is worse than
# none: the heading is what tells the judge what SCOPE the quote sits in.
JUNK_HEADING = re.compile(
    r"^(join now|permalink|menu|search|skip to|sign in|log in|subscribe|share|print|download|"
    r"cookie|privacy|terms|follow us|newsletter|your rss feed|rss|advertisement|"
    r"related articles|references|table of contents|back to top|home)\b", re.I)

_HOST_LOCKS = {}


class Pipeline:
    """Everything the engine needs to know about one domain.

    Construct this in a skill's own script. It is never a CLI flag and never appears in a card
    body - the model is given verbs in its own domain, not configuration.
    """

    def __init__(self, name, board, home, reports, profiles, outputs,
                 subject_label="claim", discord_channel=None):
        self.name = name                    # "stock-analysis"
        self.board = board                  # kanban board slug
        self.home = os.path.expanduser(home)          # working state
        self.reports = os.path.expanduser(reports)    # where reports and outputs live
        self.profiles = profiles            # {"research": "...", "audit": "...", "clerk": "..."}
        # Output filenames the pipeline itself produces. Excluded from citation harvesting so
        # the audit never audits its own conclusions.
        self.outputs = set(outputs)
        self.subject_label = subject_label
        self._channel = discord_channel
        self.sources_dir = os.path.join(self.home, "sources")
        self.locations = os.path.join(self.home, "locations.json")
        self.phase_file = os.path.join(self.home, ".phase.json")
        os.makedirs(self.home, exist_ok=True)

    # ── notification ────────────────────────────────────────────────────────

    def channel(self):
        if self._channel:
            return self._channel
        try:
            cfg = open(os.path.expanduser("~/.hermes/config.yaml"), encoding="utf-8").read()
            m = re.search(r"^discord:.*?^\s*free_response_channels:\s*'?\"?([0-9,]+)",
                          cfg, re.S | re.M)
            if m:
                self._channel = m.group(1).split(",")[0].strip()
        except Exception:                                      # noqa: BLE001
            pass
        return self._channel

    def announce(self, message):
        """Phase-level message. Per-card notifications turn a run into narration of its own
        bookkeeping; phases are the unit a person cares about."""
        chan = self.channel()
        if not chan or not message.strip():
            return False
        return subprocess.run([HERMES, "send", "-t", "discord:%s" % chan, "-q", message],
                              capture_output=True, text=True).returncode == 0

    def phase_start(self, key, message):
        """Announce once. Idempotent: intake-style steps re-run, and 'did this pass create
        work' is not a valid guard because idempotent creates return existing ids."""
        st = self._phase_state()
        if st.get(key):
            return
        st[key] = time.time()
        self._phase_state(st)
        self.announce(message)

    def phase_end(self, key, headline, extra=()):
        started = self._phase_state().get(key) or 0
        lines = [headline] + list(extra)
        bits = []
        if started:
            m, s = divmod(int(time.time() - started), 60)
            bits.append("%dm %02ds wall" % (m, s))
            tk = self.phase_tokens(started)
            if tk:
                bits.append("%s in / %s out tokens"
                            % ("{:,}".format(tk["input"]), "{:,}".format(tk["output"])))
                bits.append("%d worker session(s)" % tk["sessions"])
        if bits:
            lines.append(" · ".join(bits))
        st = self._phase_state()
        st.pop(key, None)
        self._phase_state(st)
        self.announce("\n".join(lines))

    def _phase_state(self, write=None):
        if write is not None:
            try:
                json.dump(write, open(self.phase_file, "w"))
            except Exception:                                  # noqa: BLE001
                pass
            return write
        try:
            return json.load(open(self.phase_file)) if os.path.exists(self.phase_file) else {}
        except Exception:                                      # noqa: BLE001
            return {}

    def phase_tokens(self, since_ts):
        """Tokens for this pipeline's worker profiles, from Hermes' own storage.

        Each profile is a full HERMES_HOME with its OWN state.db; the top-level one has
        profile_name NULL on every row and knows nothing about workers. Nothing else is polled,
        and {} is returned rather than a guess when there is no data.
        """
        import sqlite3
        tin = tout = n = 0
        for prof in set(self.profiles.values()):
            db = os.path.expanduser("~/.hermes/profiles/%s/state.db" % prof)
            if not os.path.exists(db):
                continue
            try:
                c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
                r = c.execute("SELECT COALESCE(SUM(input_tokens),0), "
                              "COALESCE(SUM(output_tokens),0), COUNT(*) "
                              "FROM sessions WHERE started_at >= ?", (since_ts,)).fetchone()
                c.close()
            except Exception:                                  # noqa: BLE001
                continue
            if r:
                tin += r[0] or 0
                tout += r[1] or 0
                n += r[2] or 0
        return {"input": tin, "output": tout, "sessions": n} if n else {}

    # ── cards ───────────────────────────────────────────────────────────────

    def create(self, title, profile_key, body, parents=(), runtime_min=None, priority=0,
               dry_run=False):
        """Create a card. Body must fit the worker-context cap; refuse rather than be clipped."""
        if len(body.encode()) > KANBAN_BODY_CAP:
            raise SystemExit(
                "card body is %d bytes, over the %d-byte cap - it would be silently truncated "
                "in worker_context. Move the bulk into a file the card names."
                % (len(body.encode()), KANBAN_BODY_CAP))
        cmd = [HERMES, "kanban", "--board", self.board, "create", title,
               "--assignee", self.profiles[profile_key],
               "--max-runtime", "%dm" % (runtime_min or CARD_RUNTIME_MINUTES),
               # A relative write must land where the next card reads. The default scratch
               # workspace is deleted on completion and no consumer looks there.
               "--workspace", "dir:" + self.reports,
               "--priority", str(priority),
               "--idempotency-key", re.sub(r"[^a-z0-9]+", "-", title.lower())[:60],
               "--body", body]
        for p in parents:
            cmd += ["--parent", p]
        if dry_run:
            print("  would create: %s" % title)
            return "DRY"
        out = subprocess.run(cmd, capture_output=True, text=True)
        m = re.search(r"\bt_[0-9a-f]{6,}\b", out.stdout)
        if not m:
            raise SystemExit("card create failed: %s"
                             % (out.stderr or out.stdout).strip()[:200])
        return m.group(0)

    def open_cards(self, title_prefix):
        """Cards matching a title prefix that have NOT started.

        Only todo/ready. Linking a parent onto a running card does nothing - kanban does not
        un-start it - which is how a reconciler once ran three hours ahead of its evidence.
        """
        import sqlite3
        db = os.path.expanduser("~/.hermes/kanban/boards/%s/kanban.db" % self.board)
        if not os.path.exists(db):
            return []
        con = sqlite3.connect(db)
        return [r[0] for r in con.execute(
            "SELECT id FROM tasks WHERE status IN ('todo','ready') AND title LIKE ?",
            (title_prefix + "%",))]

    def link(self, parent, child):
        subprocess.run([HERMES, "kanban", "--board", self.board, "link", parent, child],
                       capture_output=True, text=True)

    # ── sources ─────────────────────────────────────────────────────────────

    def _cache_path(self, url):
        return os.path.join(self.sources_dir,
                            hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")

    def hermes_cache_text(self, url):
        """Text for this URL from Hermes' own web cache, matched by DOCUMENT, not host.

        A plain GET is far weaker than the web_extract backend: many sources answer a bot wall.
        Those pages were already fetched by a worker and the full text sits in
        <profile>/cache/web/. But matching on host alone returns the largest cached file for
        that host - one paper standing in for forty. Returning nothing beats returning the
        wrong source.
        """
        try:
            host = url.split("/")[2].lower()
        except Exception:                                      # noqa: BLE001
            return ""
        ident = None
        for pat in (r"(PMC\d{4,})", r"[?&]setid=([0-9a-f-]{8,})", r"/(\d{6,})/?$",
                    r"/([A-Za-z0-9._-]{8,})\.pdf$", r"/([A-Za-z0-9-]{10,})/?$"):
            m = re.search(pat, url, re.I)
            if m:
                ident = m.group(1).lower()
                break
        host_key = host.replace("www.", "")
        best = ""
        for p in (glob.glob(os.path.expanduser("~/.hermes/profiles/*/cache/web/*.md"))
                  + glob.glob(os.path.expanduser("~/.hermes/cache/web/*.md"))):
            base = os.path.basename(p).lower()
            if host_key[:18] not in base and host not in base:
                continue
            try:
                t = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:                                  # noqa: BLE001
                continue
            low = t.lower()
            if url.lower() in low or (ident and ident in low):
                return t
            if ident is None and len(t) > len(best):
                best = t
        return best

    def fetch_text(self, url, timeout=25):
        """Plain text for a URL, structure preserved, cached on disk.

        Block-level tags become newlines BEFORE tags are stripped, or every heading runs into
        the following paragraph and the section index finds nothing. PDFs go through PyMuPDF,
        which keeps line breaks and numbered sections that flattened markdown destroys.
        """
        path = self._cache_path(url)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return open(path, encoding="utf-8", errors="ignore").read()

        import threading
        import urllib.request
        host = url.split("/")[2] if "//" in url else url
        lock = _HOST_LOCKS.setdefault(host, threading.Lock())
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/pdf",
            "Accept-Language": "en-US,en;q=0.9"})
        raw, ctype = None, ""
        for attempt in range(3):
            try:
                with lock:
                    time.sleep(PER_HOST_DELAY_SECONDS)
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        raw = r.read(8_000_000)
                        ctype = (r.headers.get("Content-Type") or "").lower()
                break
            except Exception:                                  # noqa: BLE001
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raw = None
        text = ""
        if raw is not None:
            if "pdf" in ctype or url.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
                try:
                    import fitz
                    tmp = path + ".pdf"
                    os.makedirs(self.sources_dir, exist_ok=True)
                    open(tmp, "wb").write(raw)
                    doc = fitz.open(tmp)
                    text = "\n".join(p.get_text() for p in doc)
                    doc.close()
                    os.remove(tmp)
                except Exception:                              # noqa: BLE001
                    text = ""
            else:
                html = raw.decode("utf-8", "ignore")
                html = re.sub(r"(?is)<(script|style|head|nav|footer)\b.*?</\1>", " ", html)
                html = re.sub(r"(?i)<(h[1-6]|p|div|br|li|tr|section|article)\b[^>]*>", "\n", html)
                html = re.sub(r"(?i)</(h[1-6]|p|div|li|tr|section|article)>", "\n", html)
                text = re.sub(r"(?s)<[^>]+>", " ", html)
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)

        # A bot wall answers 200 with almost nothing. Prefer Hermes' own extraction over that.
        if len(text) < MIN_PLAUSIBLE_CHARS:
            cached = self.hermes_cache_text(url)
            if len(cached) > len(text):
                text = cached
        os.makedirs(self.sources_dir, exist_ok=True)
        open(path, "w", encoding="utf-8").write(text)
        return text

    # ── structure and location ──────────────────────────────────────────────

    @staticmethod
    def section_index(text):
        marks = []
        for pat in HEADING_PATTERNS:
            for m in re.finditer(pat, text, re.M):
                label = re.sub(r"^#+\s*", "", " ".join(x for x in m.groups() if x)).strip()
                if label:
                    marks.append((m.start(), label))
        marks.sort()
        out = []
        for i, (off, lab) in enumerate(marks):
            if out and off - out[-1][0] < 3:
                continue
            if JUNK_HEADING.match(lab.strip()):
                continue
            nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            if nxt - off < 200:          # a heading followed by nothing is a link
                continue
            out.append((off, lab))
        return out

    @staticmethod
    def enclosing_section(text, pos, marks):
        prev = [m for m in marks if m[0] <= pos]
        nxt = [m for m in marks if m[0] > pos]
        if not prev:
            lo = max(0, pos - CONTEXT_IF_NO_SECTION // 2)
            return "(no section heading found)", text[lo:lo + CONTEXT_IF_NO_SECTION]
        start, heading = prev[-1]
        end = nxt[0][0] if nxt else len(text)
        body = text[start:end]
        if len(body) > MAX_SECTION_CHARS:
            rel = pos - start
            lo = max(0, rel - MAX_SECTION_CHARS // 2)
            body = "[section truncated around the quote]\n" + body[lo:lo + MAX_SECTION_CHARS]
        return heading, body

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", s or "").strip().lower()

    @classmethod
    def find_quote(cls, text, quote):
        """(kind, offset): exact -> fuzzy shingle -> absent.

        `absent` is not an accusation. A report may cite a source for something it paraphrased;
        the judge decides. It is recorded so a human can see the difference.
        """
        if not text or not quote:
            return "absent", None
        hay, needle = cls._norm(text), cls._norm(quote)
        i = hay.find(needle)
        if i >= 0:
            return "exact", cls._raw_offset(text, needle)
        words = needle.split()
        for size in (10, 8, 6):
            for k in range(0, max(1, len(words) - size + 1)):
                sh = " ".join(words[k:k + size])
                if len(sh) >= 30 and sh in hay:
                    return "fuzzy", cls._raw_offset(text, sh)
        return "absent", None

    @staticmethod
    def _raw_offset(text, needle):
        head = needle[:40]
        i = text.lower().find(head)
        if i >= 0:
            return i
        m = re.search(re.sub(r"\s+", r"\\s+", re.escape(head)), text, re.I)
        return m.start() if m else 0

    # ── reports ─────────────────────────────────────────────────────────────

    def endnotes(self):
        """[(report, number, [quotes], url)] across the domain's research reports.

        Every quoted run is kept, not just the longest: an endnote usually carries the article
        TITLE in quotes as well as the claim it is cited for.
        """
        out = []
        for path in sorted(glob.glob(os.path.join(self.reports, "*.md"))):
            name = os.path.basename(path)
            if name in self.outputs or name.startswith(("AUDIT-", "CONTEXT-", "CITATION-")):
                continue
            for line in open(path, encoding="utf-8", errors="ignore"):
                t = line.strip()
                m = re.match(r"^\[\^?(\d+)\]:?\s+(.*)$", t)
                if not m:
                    continue
                u = re.search(r"https?://[^\s)\"'>]+", t)
                if not u:
                    continue
                quotes = re.findall(r'"([^"]{20,})"', t)
                out.append((name, int(m.group(1)), quotes, u.group(0).rstrip(".,;)")))
        return out

    def claim_for(self, report, number):
        """The sentence(s) in `report` that cite [number].

        Without this a judge can only decide whether the quote exists. The question that
        matters is what the report DID with it, and that lives in the body text.
        """
        path = os.path.join(self.reports, report)
        if not os.path.exists(path):
            return ""
        text = open(path, encoding="utf-8", errors="ignore").read()
        text = re.split(r"\n#+\s*(?:References|Endnotes|Sources)\b", text)[0]
        hits = []
        for para in re.split(r"\n\s*\n", text):
            if "[%d]" % number not in para and "[^%d]" % number not in para:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", para):
                if "[%d]" % number in sent or "[^%d]" % number in sent:
                    hits.append(" ".join(sent.split()))
        return " ... ".join(hits[:3])[:900]
