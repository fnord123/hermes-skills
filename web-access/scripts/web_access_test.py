#!/usr/bin/env python3
"""Regression tests for the fetcher. Offline: no network, no browser, stdlib only.

Every case here is a bug that actually happened, most of them on 2026-07-31. They are pure
logic on purpose - the behaviours worth protecting (is this a document or a bot wall? is this
one website or two? which tier may run?) are all decidable without touching the network, and a
test that needs thorne.com to be up is a test that stops running the day thorne.com changes.

    python3 web_access_test.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxfetch                                                  # noqa: E402

PASS = FAIL = 0


def chk(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % desc)
    else:
        FAIL += 1
        print(" FAIL  %s %s" % (desc, detail))


def section(name):
    print("\n%s" % name)


# ── the interstitial floor ────────────────────────────────────────────────────────────────────
# Lowering MIN_DOCUMENT_CHARS to 1 so short pages would read made a 141-character Thorne
# JavaScript shell report ok=True. The two errors are not symmetric: a short real page called
# unreadable escalates to the browser and is recovered, while a shell called ok becomes
# conclusions written from an empty page.
section("a JavaScript shell is not a document")
rxfetch.configure(min_chars=200)
SHELL = ("<html><head><title>Thorne</title></head><body><div id=root></div>"
         "<script src=/app.js></script></body></html>")
chk("a 141-char shell is unusable", rxfetch.looks_unusable(SHELL[:141]))
chk("empty text is unusable", rxfetch.looks_unusable(""))
chk("a real document is usable", not rxfetch.looks_unusable("word " * 300))
rxfetch.configure(min_chars=1)
chk("--min-chars is honoured when lowered deliberately",
    not rxfetch.looks_unusable("short but real"))
rxfetch.configure(min_chars=200)
chk("and restored afterwards", rxfetch.looks_unusable("short but real"))

# Moved here from the rx-review pipeline's suite when the fetcher moved into this skill: these
# test the fetcher, so they belong with it. The pipeline could no longer run them anyway - it
# binds to this file, and CI has no skills directory.
chk("a short bot wall is rejected",
    rxfetch.looks_unusable("Checking your browser before accessing pubmed..."))
chk("a long document is NOT rejected for merely mentioning JavaScript",
    not rxfetch.looks_unusable("This site requires JavaScript. "
                               + "polycythemia hematocrit criteria " * 1200),
    "judging on the marker alone once rejected a 35KB Bookshelf chapter")
chk("a genuine shell IS rejected despite being long-ish",
    rxfetch.looks_unusable("This site needs JavaScript to work properly. "
                           + "clipboard search history " * 400))


# ── one website, one throttle timer ───────────────────────────────────────────────────────────
# www.thorne.com and thorne.com were throttled independently: one server, one client, double the
# intended request rate for anyone mixing the forms - which search results routinely do.
section("host identity: one website, one timer")
h = rxfetch._host_of
chk("www is stripped", h("https://www.thorne.com/a") == "thorne.com",
    "got %r" % h("https://www.thorne.com/a"))
chk("bare host matches it", h("https://thorne.com/b") == "thorne.com")
chk("scheme is irrelevant", h("http://thorne.com/c") == h("https://thorne.com/d"))
chk("port is part of the host", h("https://thorne.com:8443/e") == "thorne.com:8443")
chk("subdomains stay distinct", h("https://api.thorne.com/f") != h("https://cdn.thorne.com/g"))
chk("a real subdomain is not mistaken for www",
    h("https://wwwx.thorne.com/h") == "wwwx.thorne.com")
chk("userinfo cannot forge a host", h("https://evil.com@thorne.com/i") == "thorne.com",
    "got %r" % h("https://evil.com@thorne.com/i"))
chk("case is normalised", h("https://WWW.Thorne.COM/j") == "thorne.com")
chk("a malformed url still yields something", h("not a url") == "?")


# ── the outcome taxonomy ──────────────────────────────────────────────────────────────────────
# A caller that cannot tell "the server refused us" from "we never reached it" writes "the
# source does not support this claim" when the truth is "we were throttled". One citation audit
# judged claims against the text "Checking your browser before accessing pubmed".
section("unreachable and unreadable are different facts")
unreadable = rxfetch.Result("", "unreadable", "interstitial or empty (141 chars)")
unreachable = rxfetch.Result("", "unreachable", "HTTP 404")
okres = rxfetch.Result("text " * 100, "ok", "fetched", via="http")
chk("neither failure is ok", not unreadable.ok and not unreachable.ok)
chk("a fetch that worked is ok", okres.ok)
chk("they are not the same outcome", unreadable.outcome != unreachable.outcome)
chk("a result carries its tier", okres.via == "http")
chk("a failure names no tier", unreadable.via == "")


# ── which tier may run ────────────────────────────────────────────────────────────────────────
# The browser is orders of magnitude more expensive than every tier above it. It may run only
# for `unreadable` - a server that answered and withheld the document, the one failure a render
# can fix. A 404 is an ANSWER and a timeout is silence; neither is worth a browser process.
section("the browser tier is a last resort, not a retry")
# Exercise the REAL fetch() with the network and the browser stubbed out. An earlier draft of
# this file re-implemented the guard as a local helper and asserted against that, which tests a
# copy of the logic and passes happily while the real thing rots.


def run_fetch(http_outcome, allow_browser, browser_ok=True):
    """fetch() with both outer tiers replaced. Returns (result, browser_call_count)."""
    calls = {"n": 0}
    real_attempt, real_browser, real_cache = (
        rxfetch._one_attempt, rxfetch._browser_attempt, rxfetch.hermes_cache_text)

    def fake_attempt(url, timeout, via="http"):
        if http_outcome == "ok":
            return rxfetch.Result("document " * 100, "ok", "stub", via=via), False
        return rxfetch.Result("", http_outcome, "stub"), False

    def fake_browser(url, timeout):
        calls["n"] += 1
        if browser_ok:
            return rxfetch.Result("rendered " * 100, "ok", "stub", via="browser")
        return rxfetch.Result("", "unreadable", "stub: still a shell")

    rxfetch._one_attempt, rxfetch._browser_attempt = fake_attempt, fake_browser
    rxfetch.hermes_cache_text = lambda url: ""
    try:
        with tempfile.TemporaryDirectory() as td2:
            rxfetch.configure(sources_dir=td2)
            return rxfetch.fetch("https://tier.example/page", allow_browser=allow_browser), calls["n"]
    finally:
        rxfetch._one_attempt, rxfetch._browser_attempt = real_attempt, real_browser
        rxfetch.hermes_cache_text = real_cache


r, n = run_fetch("unreadable", True)
chk("unreadable + opted in renders", r.ok and r.via == "browser" and n == 1,
    "(via=%s calls=%d)" % (r.via, n))
r, n = run_fetch("unreadable", False)
chk("unreadable, not opted in: no render", not r.ok and n == 0, "(calls=%d)" % n)
r, n = run_fetch("unreachable", True)
chk("a 404 spends no render", not r.ok and n == 0, "(calls=%d)" % n)
r, n = run_fetch("ok", True)
chk("a success spends no render", r.ok and r.via == "http" and n == 0,
    "(via=%s calls=%d)" % (r.via, n))
r, n = run_fetch("unreadable", True, browser_ok=False)
chk("a render that still fails keeps the server's diagnosis",
    not r.ok and "browser tier" in r.detail, "(detail=%r)" % r.detail[:60])


# ── the cache never stores a bot wall ─────────────────────────────────────────────────────────
# Caching an interstitial made one blocked fetch permanent: the read path trusted any non-empty
# file, so every later sweep replayed the bot wall as though it were the page.
section("only usable text is ever cached")
with tempfile.TemporaryDirectory() as td:
    rxfetch.configure(sources_dir=td)
    p = rxfetch.cache_path("https://example.com/x")
    chk("cache paths land in the configured dir", os.path.dirname(p) == td)
    chk("two urls do not collide",
        rxfetch.cache_path("https://example.com/a") != rxfetch.cache_path("https://example.com/b"))
    chk("the same url is stable",
        rxfetch.cache_path("https://example.com/a") == rxfetch.cache_path("https://example.com/a"))
    open(p, "w").write(SHELL[:141])
    chk("a cached shell would be rejected on read",
        rxfetch.looks_unusable(open(p).read()))

# An unidentifiable URL must never be answered with some other document from the same host.
# Returning the largest cached page for a host once had 53 of 69 PMC citations audited against
# the wrong article, and it recurred with a nine-character Bookshelf id.
chk("no host-only cache fallback",
    rxfetch.hermes_cache_text("https://example.com/no-identifier-here") == "")


# ── throttling ────────────────────────────────────────────────────────────────────────────────
section("the throttle is per host and shared by every route")
with tempfile.TemporaryDirectory() as td:
    rxfetch.configure(lock_dir=td)
    import time
    a = time.time()
    for _ in range(2):
        with rxfetch.host_gate("gate.example"):
            pass
    same = time.time() - a
    chk("a second request to one host waits", same >= rxfetch.DEFAULT_HOST_INTERVAL * 0.8,
        "(%.2fs)" % same)

    a = time.time()
    for hh in ("p.example", "q.example", "r.example"):
        with rxfetch.host_gate(hh):
            pass
    diff = time.time() - a
    chk("different hosts do not wait on each other", diff < rxfetch.DEFAULT_HOST_INTERVAL,
        "(%.2fs)" % diff)
    chk("NCBI gets its own stricter interval",
        rxfetch._interval_for("eutils.ncbi.nlm.nih.gov") <= rxfetch.DEFAULT_HOST_INTERVAL)


# ── NCBI URLs prefer the API ──────────────────────────────────────────────────────────────────
section("NCBI URLs route to the API, not the bot-walled page")
api = rxfetch._ncbi_url("https://pubmed.ncbi.nlm.nih.gov/12345678/")
chk("a pubmed url yields an api url", bool(api) and "ncbi" in (api or ""))
chk("an ordinary url does not", not rxfetch._ncbi_url("https://example.com/article"))


print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
