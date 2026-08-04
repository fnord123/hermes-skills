#!/usr/bin/env python3
"""One fetcher for every caller: rate-limited, retried, and honest about failure.

Lives in the web-access skill rather than in any one pipeline, because the hardening below is
not domain knowledge - it is what any agent hitting the open web needs, and a second copy is
how it gets lost (see the incident in the next paragraph, which is exactly that).

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
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# The text cache is per-corpus: two pipelines auditing different documents should not share
# one pile of extracted sources. Override with configure() or ANALYSIS_SOURCES_DIR.
SOURCES = os.path.expanduser(
    os.environ.get("ANALYSIS_SOURCES_DIR") or "~/.hermes/cache/web-access/sources")

# The locks are NOT per-corpus. A rate limit counts the client, not the pipeline, so every
# consumer on this machine has to queue behind the same per-host gate — otherwise two pipelines
# each politely spacing their own requests still hand NCBI twice its limit between them.
LOCKDIR = os.path.expanduser(os.environ.get("ANALYSIS_FETCH_LOCKDIR") or "~/.hermes/.fetchlocks")


def configure(sources_dir=None, lock_dir=None, min_chars=None):
    """Point the text cache (and, rarely, the locks) somewhere else.

    Callers that keep their own corpus — analysis-engine gives each Pipeline a sources_dir —
    set this once at startup. Leave lock_dir alone unless you genuinely want a separate rate
    limiter, which you almost never do.
    """
    global MIN_DOCUMENT_CHARS, SOURCES, LOCKDIR
    if sources_dir:
        SOURCES = os.path.expanduser(sources_dir)
    if min_chars is not None:
        MIN_DOCUMENT_CHARS = int(min_chars)
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

# Statuses where the server ANSWERED and withheld the document, rather than failing to answer.
# That is the definition of `unreadable`, and it is the one failure a rendered browser can fix,
# so these escalate instead of dead-ending.
#
# 403 is how a bot wall says no: Home Depot's Akamai edge returns it to a plain client and the
# page loads perfectly in a browser. 401 is the same shape for a login wall — worth the render,
# because a browser carrying a session cookie may well be admitted. 429 stays in
# TRANSIENT_STATUS above, so it is still retried with backoff first; only once the retries are
# spent does it land here, and by then a browser is the last thing left to try.
#
# A 404 keeps its old meaning: it is an ANSWER — the page is not there, and no amount of
# rendering invents it. Read timeouts escalate too, but they are handled at the transport level
# below rather than here, since they carry no status code.
WITHHELD_STATUS = {401, 403, 429}

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
    """What a fetch produced, why, and WHICH TIER produced it.

    `via` is not bookkeeping. A caller that cannot tell cheap HTTP from a rendered browser
    page cannot make two decisions it needs to make: whether the cost it just incurred was
    expected, and whether the text is trustworthy for its purpose. The citation audit locates
    verbatim quotes, so it may reasonably refuse anything a browser rendered; a product lookup
    may not care. Neither can choose if the tier is invisible. string."""

    __slots__ = ("text", "outcome", "detail", "via")

    def __init__(self, text="", outcome="unreachable", detail="", via=""):
        self.text, self.outcome, self.detail, self.via = text, outcome, detail, via

    @property
    def ok(self):
        return self.outcome == "ok"

    def __repr__(self):
        return "Result(%s, %d chars, %r)" % (self.outcome, len(self.text), self.detail[:60])


# Above this, a page is a document whatever boilerplate it also carries. Measured on the two
# cases that forced this distinction, both of which announce "needs JavaScript to work":
# pubmed's article shell is 11,159 characters of pure chrome, while an NCBI Bookshelf chapter
# is 35,095 characters of real text. Judging on the marker alone rejected the chapter; judging
# on size alone accepted the shell. Both signals are needed.
SUBSTANTIAL_CHARS = 20_000


# A floor tuned for LONG-FORM sources - papers, drug labels, monographs - where anything under
# 200 characters is a shell rather than the document. That is a domain assumption, not a fact
# about the web: example.com is a legitimate 136-character page, and this module rejected it as
# an interstitial. Callers that read short pages should lower it via configure(min_chars=...).
MIN_DOCUMENT_CHARS = int(os.environ.get("ANALYSIS_MIN_DOCUMENT_CHARS") or 200)


def looks_unusable(text):
    """True when what came back is an interstitial rather than the document."""
    if not text or len(text.strip()) < MIN_DOCUMENT_CHARS:
        return True
    if len(text) >= SUBSTANTIAL_CHARS:
        return False                     # too much text to be an interstitial
    head = text[:4000]
    if BOT_WALL_STRONG_RE.search(head):
        return True
    return len(text) < MIN_USABLE_CHARS and bool(BOT_WALL_WEAK_RE.search(head))


def _host_of(url):
    """The throttle identity of a URL: one WEBSITE, one timer.

    A leading `www.` is dropped because www.example.com and example.com are one server being
    asked to serve one client, and giving them separate timers hands out double the intended
    request rate to whoever happens to mix the two forms - which search results routinely do.
    Subdomains are NOT collapsed: api.example.com really is a different host.
    """
    try:
        host = url.split("/")[2].lower()
    except Exception:                                          # noqa: BLE001
        return "?"
    host = host.split("@")[-1]                                 # strip any userinfo
    return host[4:] if host.startswith("www.") else host


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
    # NCBI Bookshelf (StatPearls) is deliberately NOT routed here: efetch db=books answers with
    # a 193-byte id list, not the chapter, while a plain GET of the page returns ~94KB of real
    # text. Routing it would swap working content for an empty request.
    else:
        m = re.search(r"(PMC\d{4,})", url, re.I)
        if not m or "ncbi.nlm.nih.gov" not in url.lower():
            return None
        query = "db=pmc&id=%s&retmode=xml" % m.group(1).upper()
    key = os.environ.get("NCBI_API_KEY")
    if key:
        query += "&api_key=" + key
    return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + query


def _one_attempt(url, timeout, via='http'):
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
        outcome = "unreadable" if e.code in WITHHELD_STATUS else "unreachable"
        return Result("", outcome, "HTTP %s" % e.code), retryable
    except Exception as e:                                     # noqa: BLE001
        # A silent drop is a bot wall too. Best Buy accepts the connection and never answers a
        # plain client, and the same page loads in a browser — so a timeout is a statement about
        # THIS client, not about the host being gone. A local render is cheap (a process and a
        # few seconds, no third party, no metered account), so it is worth spending on the
        # chance the site simply refuses non-browsers. Name resolution and connection-refused
        # stay `unreachable`: there is no server there for a browser to reach either.
        transport = type(e).__name__
        timed_out = isinstance(e, (TimeoutError, socket.timeout)) or "timeout" in transport.lower()
        return Result("", "unreadable" if timed_out else "unreachable", transport), True

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
    return Result(text, "ok", "fetched %d chars" % len(text), via=via), False


# The browser driver, which owns the browser and remembers which sites need which mode. It now
# sits beside this file rather than in a separate skill, but it is STILL reached as a path and a
# subprocess rather than an import. Its dependencies (playwright, a fara-cli venv, xvfb) are not
# this module's: a box with no browser installed must fail this ONE tier, not fail to import
# rxfetch and take every cheap tier down with it. rx-review's CI has no browser and imports
# rxfetch through verify.py, so an import-time dependency here stops the pipeline's tests dead.
BROWSE_TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browse_task.py")
# A render is not a request; it is a page load pulling scripts, fonts and images from the same
# host. Give it room, but bound it - a hung browser must not hold the host lock forever.
BROWSER_TIMEOUT_FLOOR = 180


def _browser_attempt(url, timeout):
    """Render the page in a real browser and return its text, throttled like any other fetch.

    This runs INSIDE host_gate, which is the entire reason the browser tier lives here rather
    than being called directly by each caller. A politeness interval that one client honours and
    another ignores is not a rate limit; before this, browse-task drove a browser at whatever
    rate an agent asked for while rxfetch carefully spaced its own requests at the same host.
    One gate, all clients, or the limit is decorative.
    """
    if not os.path.exists(BROWSE_TASK):
        return Result("", "unreadable", "browser tier unavailable (browser driver not installed)")
    # --no-browserbase is NOT a policy choice here, it is the ladder's shape. browserbase is the
    # last resort, after the agent, because it is the only rung that leaves the machine and bills
    # a metered account. This tier is a plain local render — cheaper than the agent, which is
    # cheaper than paying someone. Without this flag a site whose policy says browserbase (e.g.
    # costco.com) would jump straight from `http` to the paid remote browser, skipping both free
    # local rungs.
    cmd = [sys.executable, BROWSE_TASK, "--dump-text", "--no-browserbase", "--start-url", url]
    host = _host_of(url)
    # browse_task.py takes this host's gate when it is run on its own. Here it is a CHILD of a
    # gate we already hold, and flock is per-process: without this hand-off it would block on
    # its own parent until the timeout, every single time. Name the host rather than passing a
    # bare flag so a stale value cannot disable throttling for some other site.
    env = dict(os.environ, RXFETCH_GATE_HELD=host)
    try:
        with host_gate(host):
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                  timeout=max(timeout, BROWSER_TIMEOUT_FLOOR))
    except subprocess.TimeoutExpired:
        return Result("", "unreadable", "browser timed out")
    except Exception as exc:                                   # noqa: BLE001
        return Result("", "unreadable", "browser failed: %s" % type(exc).__name__)

    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        # stderr, not stdout: a browser that dies prints its reason on stderr, and reporting
        # "no output" when the real message was "no display available" wastes the next hour.
        return Result("", "unreadable",
                      "browser output unparseable: %s" % (proc.stderr or "")[-160:].strip())
    if not data.get("ok"):
        return Result("", "unreadable", "browser: %s" % str(data.get("error", ""))[:160])

    text = data.get("text") or ""
    if looks_unusable(text):
        return Result("", "unreadable", "browser rendered %d chars, still a shell" % len(text))
    return Result(text, "ok", "browser rendered %d chars (%s)" % (len(text), data.get("mode", "?")),
                  via="browser")


def cache_path(url):
    return os.path.join(SOURCES, hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def _write_cache(path, text):
    """Publish cached text atomically: write a temp file, then rename over the target.

    A plain open(path, "w") truncates first, so a reader arriving mid-write sees a partial
    document. Most partials are caught by looks_unusable, but not the dangerous ones: anything
    at or above SUBSTANTIAL_CHARS is declared a document without further inspection, so a large
    page torn at 30KB reads as complete. In this pipeline that is a citation judged against half
    a source.

    os.replace is atomic on POSIX, so a reader sees either the whole old file or the whole new
    one and never a seam. That is cheaper than a lock, which every reader would have to take,
    and readers here are the common case. The temp name carries the pid so two writers racing on
    the same URL cannot corrupt each other's scratch file.
    """
    os.makedirs(SOURCES, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:                                          # noqa: BLE001
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def fetch(url, timeout=45, use_cache=True, allow_browser=False):
    """Usable text for a URL, or a Result saying why not.

    Tiers, cheapest first, stopping at the first that yields usable text:

        cache        a file we already wrote               free, no request
        ncbi-api     NCBI URLs ONLY - NCBI's own API       one request, no bot wall
        http         the page itself, retried              one request
        browser      a real browser renders it             seconds, a whole process

    `ncbi-api` is conditional rather than a step every fetch walks: _ncbi_url() yields a target
    only for a PubMed article or a PMC id on an NCBI host, so for every other URL `http` is the
    first request made. Only the tiers that make a request take the host gate; reading the cache
    is not traffic.

    The order is the point. Anything above the browser costs a request or nothing at all, so
    trying them first is nearly free; the browser is the only tier that can read a JavaScript
    page, and the most expensive by a wide margin. It is therefore OPT-IN (`allow_browser`) and
    reached only after a cheaper tier returned `unreadable` — meaning the server answered and
    withheld the document, the one failure a browser can actually fix. There is no point
    rendering a page that never responded.

    Only usable text is ever cached — writing an interstitial is what made one blocked fetch
    permanent, because the read path trusted any non-empty file and every later sweep round
    replayed it.
    """
    path = cache_path(url)
    if use_cache and os.path.exists(path) and os.path.getsize(path) > 0:
        cached = open(path, encoding="utf-8", errors="ignore").read()
        if not looks_unusable(cached):
            return Result(cached, "ok", "cached", via="cache")

    targets = []
    api = _ncbi_url(url)
    if api:
        targets.append((api, "ncbi-api"))
    targets.append((url, "http"))

    last = Result("", "unreachable", "no attempt made")
    for target, via in targets:
        delay = 1.0
        for attempt in range(ATTEMPTS):
            res, retryable = _one_attempt(target, timeout, via=via)
            if res.ok:
                _write_cache(path, res.text)
                return res
            last = res
            if not retryable or attempt == ATTEMPTS - 1:
                break
            time.sleep(delay)
            delay *= 2

    # Last, and only when asked: drive a real browser. This tier is ORDERS of magnitude more
    # expensive than the ones above - a browser process, a page render, seconds instead of
    # milliseconds - so it runs only after every cheaper tier has already failed, and only for
    # a caller that opted in. It is also the only tier that can read a JavaScript-rendered page,
    # which is why `unreadable` was previously a dead end that sent the work back to the user.
    if allow_browser and last.outcome == "unreadable":
        res = _browser_attempt(url, timeout)
        if res.ok:
            _write_cache(path, res.text)
            return res
        # Keep the cheaper tier's diagnosis: it says what the SERVER did, which is more useful
        # than "the browser also could not read it".
        last.detail += "; browser tier: %s" % res.detail
    return last


def fetch_text(url, timeout=45):
    """Back-compatible shim: the text, or "" when it could not be obtained."""
    return fetch(url, timeout=timeout).text


if __name__ == "__main__":
    failed = False
    for u in sys.argv[1:]:
        r = fetch(u, allow_browser="--browser" in sys.argv)
        failed = failed or not r.ok
        print(json.dumps({"ok": r.ok, "url": u, "outcome": r.outcome, "via": r.via,
                          "chars": len(r.text), "detail": r.detail}))
    if failed:
        sys.exit(1)
