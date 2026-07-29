#!/usr/bin/env python3
"""One fetcher for the whole pipeline: rate-limited, retried, and honest about failure.

Every script here used to fetch its own way. citations.py had per-host locks, a delay, three
attempts with backoff, and a plausibility check — written after a first pass "ran 8 threads
straight at NCBI and got 41 URLs rate-limited, which the sizer then read as unmeasurably large
and gave a card each". verify.py, which replaced it as the audit that actually runs, had none
of that: a bare parallel urllib GET. So the lesson was learned once and then lost with the
module that learned it, and the citation audit spent a full run judging claims against
"Checking your browser before accessing pubmed.ncbi.nlm.nih.gov".

The thing that makes this worth centralising is that only the fetcher can tell the three cases
apart. A bot wall, an HTTP 429 and a read timeout all reach the caller as "no text", and a
caller that cannot distinguish them writes "the source does not support this claim" when the
truth is "we were throttled". Downstream that becomes a medical finding. So this module
returns WHY it failed, and retries the failures that deserve retrying.

    r = fetch(url)
    r.ok            # usable text was obtained
    r.text          # the text ("" unless ok)
    r.outcome       # "ok" | "unreadable" | "unreachable"
    r.detail        # short human reason, for logs and audit reasons

`unreadable` means we reached the server and it gave us something that is not the document —
an interstitial, a JS shell, a login wall. `unreachable` means we never got a usable response
at all. Neither is evidence about the claim; both are facts about our own reach.

Serialisation is per host and holds across threads AND processes, because the pipeline runs
several cards at once and NCBI counts requests per client, not per process. One in-flight
request per host, no closer together than that host's minimum interval.
"""

import contextlib
import fcntl
import glob
import hashlib
import os
import re
import threading
import time
import urllib.error
import urllib.request

# The text cache is per-corpus: two pipelines auditing different documents should not share
# one pile of extracted sources. Override with configure() or ANALYSIS_SOURCES_DIR.
SOURCES = os.path.expanduser(
    os.environ.get("ANALYSIS_SOURCES_DIR") or "~/.hermes/rx-review/sources")

# The locks are NOT per-corpus. A rate limit counts the client, not the pipeline, so every
# consumer on this machine has to queue behind the same per-host gate — otherwise two pipelines
# each politely spacing their own requests still hand NCBI twice its limit between them.
LOCKDIR = os.path.expanduser(os.environ.get("ANALYSIS_FETCH_LOCKDIR") or "~/.hermes/.fetchlocks")


def configure(sources_dir=None, lock_dir=None):
    """Point the text cache (and, rarely, the locks) somewhere else.

    Callers that keep their own corpus — analysis-engine gives each Pipeline a sources_dir —
    set this once at startup. Leave lock_dir alone unless you genuinely want a separate rate
    limiter, which you almost never do.
    """
    global SOURCES, LOCKDIR
    if sources_dir:
        SOURCES = os.path.expanduser(sources_dir)
    if lock_dir:
        LOCKDIR = os.path.expanduser(lock_dir)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

MIN_USABLE_CHARS = 2000
ATTEMPTS = 3
DEFAULT_HOST_INTERVAL = 0.4

# NCBI publishes 3 requests/second for anonymous clients and 10 with a key. Exceeding it earns
# a 429 that looks exactly like a small page once the HTML is stripped.
NCBI_HOSTS = ("ncbi.nlm.nih.gov", "eutils.ncbi.nlm.nih.gov")
HOST_INTERVALS = {"eutils.ncbi.nlm.nih.gov": 0.35, "ncbi.nlm.nih.gov": 0.35}

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Interstitials that mean "this is not the document", at any length: pubmed's JS shell is
# 6-11KB of clipboard/search-history chrome and carries no abstract at all.
BOT_WALL_STRONG_RE = re.compile(
    r"checking your browser|just a moment|verify you are human|are you a robot"
    r"|attention required|needs javascript to work|site requires javascript"
    r"|javascript is (not available|disabled|required)", re.I)
BOT_WALL_WEAK_RE = re.compile(
    r"enable javascript|captcha|access denied|cloudflare|403 forbidden", re.I)

_DICT_LOCK = threading.Lock()
_THREAD_LOCKS = {}


class Result(object):
    """What a fetch produced, and why, so callers never have to guess from an empty string."""

    __slots__ = ("text", "outcome", "detail")

    def __init__(self, text="", outcome="unreachable", detail=""):
        self.text, self.outcome, self.detail = text, outcome, detail

    @property
    def ok(self):
        return self.outcome == "ok"

    def __repr__(self):
        return "Result(%s, %d chars, %r)" % (self.outcome, len(self.text), self.detail[:60])


def looks_unusable(text):
    """True when what came back is an interstitial rather than the document."""
    if not text or len(text.strip()) < 200:
        return True
    head = text[:4000]
    if BOT_WALL_STRONG_RE.search(head):
        return True
    return len(text) < MIN_USABLE_CHARS and bool(BOT_WALL_WEAK_RE.search(head))


def _host_of(url):
    try:
        return url.split("/")[2].lower()
    except Exception:                                          # noqa: BLE001
        return "?"


def _interval_for(host):
    for h, iv in HOST_INTERVALS.items():
        if host == h or host.endswith("." + h):
            return 0.11 if (os.environ.get("NCBI_API_KEY") and "ncbi" in h) else iv
    return DEFAULT_HOST_INTERVAL


@contextlib.contextmanager
def host_gate(host):
    """One in-flight request per host, spaced by that host's interval, across processes.

    A threading.Lock alone is not enough: build, sweep and the sizer can run in separate
    processes at the same time, and a rate limit is enforced against the client, not the
    process. The timestamp is written even when the request raises — a 429 consumed our quota
    just as surely as a 200, and retrying immediately is what earns the next one.
    """
    interval = _interval_for(host)
    with _DICT_LOCK:
        tlock = _THREAD_LOCKS.setdefault(host, threading.Lock())
    os.makedirs(LOCKDIR, exist_ok=True)
    path = os.path.join(LOCKDIR, re.sub(r"[^a-z0-9.-]+", "_", host)[:80] + ".lock")
    with tlock:
        fh = open(path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            try:
                last = float((fh.read() or "0").strip() or 0)
            except ValueError:
                last = 0.0
            wait = interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                fh.seek(0)
                fh.truncate()
                fh.write("%.3f" % time.time())
                fh.flush()
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()


def _html_to_text(html):
    """Block tags become newlines BEFORE tags are stripped, so headings stay separable."""
    html = re.sub(r"(?is)<(script|style|head|nav|footer)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<(h[1-6]|p|div|br|li|tr|section|article)\b[^>]*>", "\n", html)
    html = re.sub(r"(?i)</(h[1-6]|p|div|li|tr|section|article)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text)


def _pdf_to_text(raw, scratch):
    try:
        import fitz
        open(scratch, "wb").write(raw)
        doc = fitz.open(scratch)
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        return text
    except Exception:                                          # noqa: BLE001
        return ""
    finally:
        try:
            os.remove(scratch)
        except OSError:
            pass


def _ncbi_url(url):
    """The E-utilities URL for a PubMed/PMC page, or None — the HTML pages never yield text."""
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url, re.I)
    if m:
        query = "db=pubmed&id=%s&rettype=abstract&retmode=text" % m.group(1)
    else:
        m = re.search(r"(PMC\d{4,})", url, re.I)
        if not m or "ncbi.nlm.nih.gov" not in url.lower():
            return None
        query = "db=pmc&id=%s&retmode=xml" % m.group(1).upper()
    key = os.environ.get("NCBI_API_KEY")
    if key:
        query += "&api_key=" + key
    return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + query


def _one_attempt(url, timeout):
    """A single gated request. Returns (Result, retryable)."""
    host = _host_of(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9"})
    try:
        with host_gate(host):
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(8_000_000)
                ctype = (r.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        retryable = e.code in TRANSIENT_STATUS
        return Result("", "unreachable", "HTTP %s" % e.code), retryable
    except Exception as e:                                     # noqa: BLE001
        return Result("", "unreachable", type(e).__name__), True

    if "pdf" in ctype or url.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
        # Per-URL scratch name, created before it is written to. A fixed name would be
        # clobbered by the next PDF in the thread pool, and the makedirs has to come first or
        # the very first PDF of a run writes into a directory that does not exist yet.
        os.makedirs(SOURCES, exist_ok=True)
        scratch = os.path.join(SOURCES, "_scratch-%s.pdf"
                               % hashlib.sha1(url.encode()).hexdigest()[:12])
        text = _pdf_to_text(raw, scratch)
    else:
        text = _html_to_text(raw.decode("utf-8", "ignore"))

    if looks_unusable(text):
        # 200 with an interstitial. Worth one more try: a throttle often answers this way, and
        # a throttled failure is not a document.
        return Result("", "unreadable", "interstitial or empty (%d chars)" % len(text)), True
    return Result(text, "ok", "fetched %d chars" % len(text)), False


def hermes_cache_text(url):
    """Text a Hermes worker already extracted for this URL, if any.

    Matched by host then confirmed by identifier, because the cache filename hash is not
    reproducible from here. Matching on host alone once returned the largest cached file for
    pmc.ncbi.nlm.nih.gov — one paper standing in for forty — and 53 of 69 PMC citations were
    checked against the wrong article. Returning nothing beats returning the wrong source.
    """
    host = _host_of(url)
    ident = None
    for pat in (r"(PMC\d{4,})", r"[?&]setid=([0-9a-f-]{8,})", r"/(\d{6,})/?$",
                r"/([A-Za-z0-9._-]{8,})\.pdf$", r"/([A-Za-z0-9-]{10,})/?$"):
        m = re.search(pat, url, re.I)
        if m:
            ident = m.group(1).lower()
            break
    host_key = host.replace("www.", "")
    best = ""
    for p in (glob.glob(os.path.expanduser("~/.hermes/profiles/*/cache/web/*.md")) +
              glob.glob(os.path.expanduser("~/.hermes/cache/web/*.md"))):
        base = os.path.basename(p).lower()
        if host_key[:18] not in base and host not in base:
            continue
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:                                      # noqa: BLE001
            continue
        low = t.lower()
        if url.lower() in low:
            return t
        if ident and ident in low:
            return t
        if ident is None and len(t) > len(best):
            best = t
    return best


def cache_path(url):
    return os.path.join(SOURCES, hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def fetch(url, timeout=45, use_cache=True):
    """Usable text for a URL, or a Result saying why not.

    Order: cached real text, then NCBI's API for NCBI URLs, then the page itself with retries,
    then whatever a Hermes worker already extracted. Only usable text is ever cached — writing
    an interstitial is what made one blocked fetch permanent, because the read path trusted any
    non-empty file and every later sweep round replayed it.
    """
    path = cache_path(url)
    if use_cache and os.path.exists(path) and os.path.getsize(path) > 0:
        cached = open(path, encoding="utf-8", errors="ignore").read()
        if not looks_unusable(cached):
            return Result(cached, "ok", "cached")

    targets = []
    api = _ncbi_url(url)
    if api:
        targets.append(api)
    targets.append(url)

    last = Result("", "unreachable", "no attempt made")
    for target in targets:
        delay = 1.0
        for attempt in range(ATTEMPTS):
            res, retryable = _one_attempt(target, timeout)
            if res.ok:
                os.makedirs(SOURCES, exist_ok=True)
                open(path, "w", encoding="utf-8").write(res.text)
                return res
            last = res
            if not retryable or attempt == ATTEMPTS - 1:
                break
            time.sleep(delay)
            delay *= 2

    cached = hermes_cache_text(url)
    if cached and not looks_unusable(cached):
        os.makedirs(SOURCES, exist_ok=True)
        open(path, "w", encoding="utf-8").write(cached)
        return Result(cached, "ok", "hermes web cache")
    return last


def fetch_text(url, timeout=45):
    """Back-compatible shim: the text, or "" when it could not be obtained."""
    return fetch(url, timeout=timeout).text


if __name__ == "__main__":
    import json
    import sys
    for u in sys.argv[1:]:
        r = fetch(u)
        print(json.dumps({"url": u, "outcome": r.outcome,
                          "chars": len(r.text), "detail": r.detail}))
