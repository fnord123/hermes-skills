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

# Fixture fetches (tier.example, wall.example, …) go through rxfetch.fetch, which emits a metrics
# event. Off by default here so the suite does not pollute the real fetch/search dashboard; the one
# test that verifies emission re-enables it locally against a temp events file.
os.environ["RX_METRICS"] = "0"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxfetch                                                  # noqa: E402
# Unit tests exercise the ladder LOGIC with the render tier (_firecrawl_attempt) stubbed; they must
# never actually docker-run bladebro (tier 5) or spend browserbase (tier 6). Disable those rungs.
rxfetch.BLADEBRO_SSH = []
rxfetch.ALLOW_BROWSERBASE = False

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
chk("a short page of clean PROSE is usable, not rejected as a shell",
    not rxfetch.looks_unusable("This domain is for use in documentation examples without needing "
                               "permission. You may use it in literature without prior coordination."))
chk("a short NON-prose blob (stripped shell/title) is still unusable",
    rxfetch.looks_unusable("Thorne Loading"))
rxfetch.configure(min_chars=1)
chk("--min-chars is honoured when lowered deliberately",
    not rxfetch.looks_unusable("short but real"))
rxfetch.configure(min_chars=200)
chk("and restored afterwards", rxfetch.looks_unusable("short but real"))

# ── ONE cache ─────────────────────────────────────────────────────────────────────────────────
# Every caller shares the skill's one page cache; a caller cannot be handed a different cache by
# the environment. Per-corpus overrides re-fetched hundreds of already-cached pages per audit.
chk("the default cache is the one shared sources dir",
    rxfetch.SOURCES == os.path.expanduser("~/.hermes/cache/web-access/sources"))
chk("no environment variable re-points the cache",
    'environ.get("ANALYSIS_SOURCES_DIR"' not in open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "rxfetch.py")).read())

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


# ── search scope ──────────────────────────────────────────────────────────────────────────────
# SearXNG dispatches by CATEGORY. A search with no category hits `general` only - bing and
# duckduckgo - so pubmed, openalex, crossref, semantic scholar and arxiv were installed,
# enabled, and never queried. Cards were told to prefer PubMed while the backend could not
# reach it.
section("search scope maps to the right engines")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("wa", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "web_access.py"))
_wa = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_wa)
chk("literature merges the science engines",
    _wa.SCOPES["literature"].get("categories") == "science",
    "(%r)" % _wa.SCOPES.get("literature"))
chk("literature does NOT widen", "widen" not in _wa.SCOPES["literature"],
    "pubmed, openalex and arxiv index different corpora, so merging them is additive")
for _s in ("products", "web"):
    chk("%s asks one good engine first" % _s,
        _wa.SCOPES[_s].get("engines") == _wa.PRIMARY_WEB_ENGINE, "(%r)" % _wa.SCOPES[_s])
    chk("%s widens to the whole category when that is empty" % _s,
        _wa.SCOPES[_s]["widen"].get("categories") == "general")
chk("omitting --scope still works", _wa.DEFAULT_SCOPE in _wa.SCOPES)
# The widen path cannot be reached from a live query - the preferred engine answers even
# nonsense - so it is exercised here with the network stubbed out. Otherwise its first real
# run would be in production, on the day the engine is suspended or out of quota.
_calls = []


def _fake_ask(query, selector, timeout):
    _calls.append(selector)
    if "engines" in selector:                 # the preferred engine: pretend it has nothing
        return {"results": []}
    return {"results": [{"title": "t", "url": "u", "content": "c", "engine": "bing"}]}


_real_ask = _wa._ask
_wa._ask = _fake_ask
try:
    _calls.clear()
    body, widened = _wa.run_search("q", "products")
    chk("an empty primary widens", widened and len(body["results"]) == 1)
    chk("it tried the preferred engine first", _calls[0].get("engines") == _wa.PRIMARY_WEB_ENGINE,
        "(%s)" % _calls[0])
    chk("then the wider category", _calls[1].get("categories") == "general", "(%s)" % _calls[1])

    _calls.clear()
    _wa._ask = lambda q, sel, t: (_calls.append(sel) or
                                  {"results": [{"url": "u", "engine": "brave api"}]})
    body, widened = _wa.run_search("q", "products")
    chk("a primary WITH results does not widen", not widened and len(_calls) == 1,
        "(%d call(s))" % len(_calls))

    _calls.clear()
    _wa._ask = _fake_ask
    body, widened = _wa.run_search("q", "literature")
    chk("literature never widens", not widened and len(_calls) == 1,
        "science engines are complementary, so a merge is correct there")
finally:
    _wa._ask = _real_ask

chk("the vocabulary is the caller's, not the engine's",
    not any(v in _wa.SCOPES for v in ("science", "general")),
    "callers say what they want, not which SearXNG category or engine serves it")


section("a repeated search is served from a 7d cache, and every search emits one event")
import io as _io
import json as _json
import time as _time
import types as _types
import contextlib as _clib

_tmp = tempfile.mkdtemp()
_wa._SEARCH_CACHE = os.path.join(_tmp, "searches")
_saved_events = rxfetch._FETCH_EVENTS_PATH
rxfetch._FETCH_EVENTS_PATH = os.path.join(_tmp, "events.jsonl")
_saved_searxng = _wa.SEARXNG_URL
_wa.SEARXNG_URL = "http://searxng.test"            # run_search is stubbed below, so this URL is
                                                   # never contacted; it only clears cmd_search's
                                                   # `if not SEARXNG_URL` guard so the test is
                                                   # hermetic (CI has no SEARXNG_URL in env)
_wa._push_search_loki = lambda ev: None            # no network sink in tests
os.environ["RX_METRICS"] = "1"                      # this test VERIFIES emission — to the temp file

_ncalls = {"n": 0}


def _one_result(query, scope, timeout=30):
    _ncalls["n"] += 1
    return ({"results": [{"title": "T", "url": "http://x/y", "content": "snip",
                          "engine": "brave api"}]}, False)


def _run_search_cmd(query="magnesium glycinate dose", scope="products", mx=10):
    ns = _types.SimpleNamespace(query=query, scope=scope, max=mx, timeout=30)
    buf = _io.StringIO()
    with _clib.suppress(SystemExit), _clib.redirect_stdout(buf):
        _wa.cmd_search(ns)
    return _json.loads(buf.getvalue())


try:
    _wa.run_search = _one_result
    _r1 = _run_search_cmd()
    chk("the first search asks the backend and is not cached",
        _ncalls["n"] == 1 and _r1["cached"] is False and _r1["count"] == 1)
    _r2 = _run_search_cmd()
    chk("an identical query+scope is served from cache, backend untouched",
        _ncalls["n"] == 1 and _r2["cached"] is True and _r2["results"][0]["url"] == "http://x/y")
    _run_search_cmd(scope="literature")
    chk("a different scope is a different cache entry", _ncalls["n"] == 2,
        "the key is query AND scope")

    _events = [_json.loads(l) for l in open(rxfetch._FETCH_EVENTS_PATH, encoding="utf-8")]
    chk("every search emits exactly one event", len(_events) == 3, "(%d)" % len(_events))
    chk("the first event is a live search, the second a cache hit",
        _events[0]["outcome"] == "searched" and _events[1]["outcome"] == "cache_hit")
    chk("a search event names the query, scope, count and kind",
        _events[0]["kind"] == "search" and _events[0]["scope"] == "products"
        and _events[0]["count"] == 1)

    # TTL: an entry older than _SEARCH_TTL is ignored; a fresh one is served.
    _cp = _wa._search_cache_path("stale q", "web")
    _wa._write_search_cache(_cp, {"ts": _time.time() - _wa._SEARCH_TTL - 10,
                                  "results": [{"url": "u"}]})
    chk("a cache entry past the TTL is not served", _wa._read_search_cache(_cp) is None)
    _wa._write_search_cache(_cp, {"ts": _time.time(), "results": [{"url": "u"}]})
    chk("a fresh cache entry is served", _wa._read_search_cache(_cp) is not None)

    # An empty result set is a fact, not cached: the next identical query re-asks.
    _ncalls["n"] = 0
    _wa.run_search = lambda q, s, t=30: (_ncalls.__setitem__("n", _ncalls["n"] + 1)
                                         or ({"results": []}, False))
    _re = _run_search_cmd(query="zzz no such thing", scope="web")
    chk("an empty result set is ok=True with count 0", _re["ok"] is True and _re["count"] == 0)
    _run_search_cmd(query="zzz no such thing", scope="web")
    chk("an empty search is not cached, so it is re-asked", _ncalls["n"] == 2,
        "a transient empty must not be pinned for the TTL")
finally:
    rxfetch._FETCH_EVENTS_PATH = _saved_events
    _wa.SEARXNG_URL = _saved_searxng
    os.environ["RX_METRICS"] = "0"                  # back off for the rest of the suite


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
    real_attempt, real_browser = rxfetch._one_attempt, rxfetch._firecrawl_attempt

    def fake_attempt(url, timeout, via="http"):
        if http_outcome == "ok":
            return rxfetch.Result("document " * 100, "ok", "stub", via=via), False
        return rxfetch.Result("", http_outcome, "stub"), False

    def fake_browser(url, timeout):
        calls["n"] += 1
        if browser_ok:
            return rxfetch.Result("rendered " * 100, "ok", "stub", via="firecrawl")
        return rxfetch.Result("", "unreadable", "stub: still a shell")

    rxfetch._one_attempt, rxfetch._firecrawl_attempt = fake_attempt, fake_browser
    try:
        with tempfile.TemporaryDirectory() as td2:
            rxfetch.configure(sources_dir=td2)
            return rxfetch.fetch("https://tier.example/page", allow_browser=allow_browser), calls["n"]
    finally:
        rxfetch._one_attempt, rxfetch._firecrawl_attempt = real_attempt, real_browser


r, n = run_fetch("unreadable", True)
chk("unreadable + opted in renders", r.ok and r.via == "firecrawl" and n == 1,
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
    not r.ok and "render tier" in r.detail, "(detail=%r)" % r.detail[:60])


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


# ── cached page text expires after SOURCES_TTL ────────────────────────────────────────────────
# The text cache is content-addressed but not immortal: an entry older than the 30d TTL is
# ignored on read (lazy, like the search cache) and overwritten by the next successful fetch.
section("cached page text expires after the 30d TTL")
with tempfile.TemporaryDirectory() as td:
    import time as _t
    rxfetch.configure(sources_dir=td)
    _real_attempt = rxfetch._one_attempt
    _live = {"n": 0}

    def _fresh(url, timeout, via="http"):
        _live["n"] += 1
        return rxfetch.Result("fresh copy " * 100, "ok", "stub", via=via), False

    rxfetch._one_attempt = _fresh
    try:
        _u = "https://ttl.example/page"
        _p = rxfetch.cache_path(_u)
        open(_p, "w").write("old cached copy " * 100)
        _r = rxfetch.fetch(_u)
        chk("a young entry is served from cache, no request",
            _r.via == "cache" and _live["n"] == 0, "(via=%s live=%d)" % (_r.via, _live["n"]))
        _old = _t.time() - rxfetch.SOURCES_TTL - 60
        os.utime(_p, (_old, _old))
        _r = rxfetch.fetch(_u)
        chk("an entry older than the TTL is re-fetched, not served",
            _r.via == "http" and _live["n"] == 1, "(via=%s live=%d)" % (_r.via, _live["n"]))
        chk("...and the re-fetch overwrote the expired entry",
            "fresh copy" in open(_p).read() and _t.time() - os.path.getmtime(_p) < 60)
    finally:
        rxfetch._one_attempt = _real_attempt


# ── URL normalisation and the negative cache ────────────────────────────────────────────────────
section("variant URLs share one cache key; a dead URL is remembered, not re-fetched by every card")
# The research fan-out invents many URLs for one product (www vs not, thorne.com vs thorneresearch.com,
# /products/ vs /mineral-supplements/) and guesses URLs that 404. Normalisation collapses the cosmetic
# variants; the negative cache stops a known-dead URL being re-fetched by every card that guesses it.
with tempfile.TemporaryDirectory() as td:
    rxfetch.configure(sources_dir=td)
    _saved_metrics = os.environ.get("RX_METRICS")
    os.environ["RX_METRICS"] = "0"           # keep fixture fetches off the real dashboard

    chk("www, a trailing slash and a fragment collapse to one key",
        rxfetch.cache_path("https://WWW.Thorne.com/x/#a") == rxfetch.cache_path("https://thorne.com/x"))
    chk("a different path is still a different key",
        rxfetch.cache_path("https://thorne.com/a") != rxfetch.cache_path("https://thorne.com/b"))
    chk("http and https stay distinct",
        rxfetch.cache_path("http://thorne.com/a") != rxfetch.cache_path("https://thorne.com/a"))

    rxfetch._write_negative("http://x.example/gone", rxfetch.Result("", "unreachable", "HTTP 404"))
    rxfetch._write_negative("http://x.example/busy", rxfetch.Result("", "unreachable", "HTTP 429"))
    rxfetch._write_negative("http://x.example/wall", rxfetch.Result("", "unreadable", "HTTP 403"))
    chk("a 404 is remembered with the durable TTL",
        _json.load(open(rxfetch._neg_path("http://x.example/gone")))["ttl"] == rxfetch.NEG_TTL_PERMANENT)
    chk("a 429 is remembered only briefly (transient TTL)",
        _json.load(open(rxfetch._neg_path("http://x.example/busy")))["ttl"] == rxfetch.NEG_TTL_TRANSIENT)
    chk("an unreadable (browser-fixable) wall is NOT remembered",
        not os.path.exists(rxfetch._neg_path("http://x.example/wall")),
        "a later caller may still opt into the browser tier")

    _sp = rxfetch._neg_path("http://x.example/stale")
    open(_sp, "w").write(_json.dumps({"ts": 0, "outcome": "unreachable", "detail": "HTTP 429",
                                      "ttl": 3600}))
    chk("an expired entry is ignored", rxfetch._read_negative("http://x.example/stale") is None)

    _calls = {"n": 0}
    def _fail_404(target, timeout, via):
        _calls["n"] += 1
        return rxfetch.Result("", "unreachable", "HTTP 404"), False
    _orig_one = rxfetch._one_attempt
    rxfetch._one_attempt = _fail_404
    try:
        _r1 = rxfetch.fetch("http://x.example/miss", allow_browser=False)
        _after_first = _calls["n"]
        _r2 = rxfetch.fetch("http://x.example/miss", allow_browser=False)
        chk("the first fetch of a dead URL reaches the network", _after_first > 0 and not _r1.ok)
        chk("the second is served from the negative cache with no request",
            _calls["n"] == _after_first and _r2.via == "neg-cache")
    finally:
        rxfetch._one_attempt = _orig_one
        if _saved_metrics is None:
            os.environ.pop("RX_METRICS", None)
        else:
            os.environ["RX_METRICS"] = _saved_metrics


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
chk("a modern pubmed url yields an api url", bool(api) and "efetch" in (api or ""))
# The legacy `(www.)ncbi.nlm.nih.gov/pubmed/<id>` form is what models reconstruct from a PMID and
# it dominates real citations; it MUST reach efetch too, or it lands on pubmed's bot wall (9 such
# citations went unjudged through a 4-round audit sweep on 2026-08-12).
legacy = rxfetch._ncbi_url("https://www.ncbi.nlm.nih.gov/pubmed/12345678")
chk("a legacy /pubmed/ url routes to the same api", bool(legacy) and "db=pubmed&id=12345678" in (legacy or ""))
chk("both pubmed shapes yield the same efetch url", api == legacy)
chk("an ordinary url does not", not rxfetch._ncbi_url("https://example.com/article"))
chk("a bookshelf page is deliberately not routed",
    not rxfetch._ncbi_url("https://www.ncbi.nlm.nih.gov/books/NBK1234/"))


# ── The browser tier fails on USE, not on IMPORT ──────────────────────────────────────────────
# The browser driver now lives beside this module, but it is still reached as a path and a
# subprocess. Its dependencies (playwright, the fara venv, xvfb) are not this module's. rx-review
# imports rxfetch through verify.py in a CI container with no browser at all, so a driver that
# was missing at import time would take every cheap tier down with it and stop the pipeline's
# tests dead. Point BROWSE_TASK at nothing and confirm the damage stays inside its own tier.
section("a missing browser driver costs one tier, not the module")
_real_bt = rxfetch.BROWSE_TASK
try:
    rxfetch.BROWSE_TASK = os.path.join(tempfile.gettempdir(), "no-such-browser-driver.py")
    res = rxfetch._browser_attempt("https://tier.example/page", 5)
    chk("a missing driver reports unreadable rather than raising",
        res.outcome == "unreadable" and not res.ok, "(outcome=%s)" % res.outcome)
    chk("and says which tier is unavailable", "not installed" in (res.detail or ""),
        "(detail=%r)" % res.detail)
    r, n = run_fetch("ok", True)
    chk("the cheap tiers still answer with no driver present", r.ok and r.via == "http",
        "(via=%s)" % r.via)
finally:
    rxfetch.BROWSE_TASK = _real_bt

chk("the driver path resolves beside this module",
    os.path.dirname(rxfetch.BROWSE_TASK) == os.path.dirname(os.path.abspath(rxfetch.__file__)))


# ── the cache is published atomically ─────────────────────────────────────────────────────────
# A plain open(path,"w") truncates first, so a concurrent reader sees a partial document. The
# dangerous partials are the big ones: looks_unusable declares anything at or above
# SUBSTANTIAL_CHARS a document without further inspection, so a large page torn mid-write reads
# as complete — a citation judged against half a source. Readers must see all or nothing.
section("cached text is published whole or not at all")
with tempfile.TemporaryDirectory() as td3:
    rxfetch.configure(sources_dir=td3)
    cp = rxfetch.cache_path("https://atomic.example/doc")
    rxfetch._write_cache(cp, "first " * 400)
    chk("a write lands", os.path.exists(cp) and "first" in open(cp).read())
    chk("no scratch file is left behind",
        [f for f in os.listdir(td3) if f.endswith(".tmp")] == [],
        "(%r)" % os.listdir(td3))

    # The property that matters: at no instant does the target hold a partial document. Stat the
    # target from inside the write and confirm it still holds the OLD text, not a truncation.
    # Shadow `open` in rxfetch's own namespace rather than patching it globally: a module
    # attribute is found before the builtin, so only the code under test is affected and a
    # failure here cannot take the rest of the run down with it.
    seen = {}
    _real_open = open

    def spy_open(p, *a, **kw):
        fh = _real_open(p, *a, **kw)
        if str(p).endswith(".tmp"):
            seen["target_midwrite"] = (_real_open(cp).read() if os.path.exists(cp) else None)
        return fh

    rxfetch.open = spy_open
    try:
        rxfetch._write_cache(cp, "second " * 400)
    finally:
        del rxfetch.open
    chk("the target is never truncated mid-write",
        seen.get("target_midwrite", "").startswith("first"),
        "(saw %r)" % (seen.get("target_midwrite") or "")[:20])
    chk("and holds the new text afterwards", open(cp).read().startswith("second"))


# ── a bot wall is an answer, and a browser can fix it ─────────────────────────────────────────
# Home Depot's edge returns 403 to a plain client and serves the page fine in a browser. That
# used to be classified `unreachable`, which is the one outcome the browser tier never fires on,
# so the request dead-ended at the exact point rendering would have worked. 404 must keep the
# old behaviour: the page is not there, and no render invents it.
section("HTTP status maps to the right outcome")
import urllib.error                                              # noqa: E402


def run_status(code):
    """One _one_attempt against a stubbed server returning `code`. Returns the Result."""
    real = rxfetch.urllib.request.urlopen

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "stub", {}, None)

    rxfetch.urllib.request.urlopen = boom
    try:
        res, retryable = rxfetch._one_attempt("https://wall.example/page", 5)
        return res, retryable
    finally:
        rxfetch.urllib.request.urlopen = real


for code, want in ((403, "unreadable"), (401, "unreadable"), (429, "unreadable"),
                   (404, "unreachable"), (500, "unreachable")):
    r, _ = run_status(code)
    chk("HTTP %d is %s" % (code, want), r.outcome == want, "(got %s)" % r.outcome)

r, retryable = run_status(429)
chk("429 is still retried before it escalates", retryable)
r, retryable = run_status(403)
chk("403 is not retried — a wall does not soften", not retryable)

# End to end: the tier now fires on a 403 and does not on a 404.
_real_attempt = rxfetch._one_attempt
for code, want_render in ((403, 1), (404, 0)):
    rxfetch._one_attempt = (lambda c: lambda url, timeout, via="http": (
        rxfetch.Result("", "unreadable" if c in rxfetch.WITHHELD_STATUS else "unreachable",
                       "HTTP %d" % c), False))(code)
    calls = {"n": 0}

    def fake_browser(url, timeout, _c=calls):
        _c["n"] += 1
        return rxfetch.Result("rendered " * 100, "ok", "stub", via="browser")

    _real_browser = rxfetch._firecrawl_attempt
    rxfetch._firecrawl_attempt = fake_browser
    try:
        with tempfile.TemporaryDirectory() as td4:
            rxfetch.configure(sources_dir=td4)
            rxfetch.fetch("https://wall.example/p", allow_browser=True)
    finally:
        rxfetch._firecrawl_attempt = _real_browser
    chk("HTTP %d spends %d render(s)" % (code, want_render), calls["n"] == want_render,
        "(spent %d)" % calls["n"])
rxfetch._one_attempt = _real_attempt


# ── a silent drop is a bot wall too ───────────────────────────────────────────────────────────
# Best Buy accepts the connection and never answers a plain client, while the same page loads in
# a browser. So a read timeout is a fact about THIS client, not about the host being gone, and a
# local render is cheap enough to be worth spending on the chance the site just refuses
# non-browsers. Name resolution and connection-refused stay unreachable: no server, nothing to
# render.
section("a read timeout escalates; a dead host does not")


def run_transport(exc):
    real = rxfetch.urllib.request.urlopen

    def boom(req, timeout=None):
        raise exc

    rxfetch.urllib.request.urlopen = boom
    try:
        return rxfetch._one_attempt("https://drop.example/page", 5)[0]
    finally:
        rxfetch.urllib.request.urlopen = real


chk("a read timeout is unreadable", run_transport(TimeoutError("timed out")).outcome == "unreadable",
    "(got %s)" % run_transport(TimeoutError("timed out")).outcome)
chk("socket.timeout is unreadable",
    run_transport(__import__("socket").timeout("timed out")).outcome == "unreadable")
chk("a refused connection stays unreachable",
    run_transport(ConnectionRefusedError("refused")).outcome == "unreachable")
chk("a DNS failure stays unreachable",
    run_transport(OSError("Name or service not known")).outcome == "unreachable")

# ── browserbase is the LAST rung, after the agent ─────────────────────────────────────────────
# The local render and the agent both use the free local browser; browserbase leaves the machine
# and bills a metered account. So the plain-render tier must never escalate onto it — otherwise a
# site whose policy says browserbase (costco.com) jumps from `http` straight to the paid remote
# browser and skips both free rungs.
section("the plain render tier never reaches the paid rung")
_seen = {}
_real_run = rxfetch.subprocess.run


def _spy_run(cmd, **kw):
    _seen["cmd"] = list(cmd)
    raise RuntimeError("stop here — we only need the argv")


rxfetch.subprocess.run = _spy_run
try:
    rxfetch._browser_attempt("https://www.costco.com/p/x", 5)
except Exception:                                                # noqa: BLE001
    pass
finally:
    rxfetch.subprocess.run = _real_run
chk("the browser tier forces --no-browserbase", "--no-browserbase" in _seen.get("cmd", []),
    "(argv=%s)" % _seen.get("cmd"))
chk("and still asks for verbatim text, not an answer", "--dump-text" in _seen.get("cmd", []))

# ── rung names, and the trail that proves a layer ran ─────────────────────────────────────────
# `via` and the attempts trail both used to prefix blindly, turning the agent rung into
# `browser:agent:headful` — not a rung anyone can look up. And the trail itself was documented
# but never surfaced: a caller could not tell a layer that failed from one that never ran.
section("every layer is named and accounted for")
chk("a bare local mode gets the browser: prefix", rxfetch._rung_name("headless") == "browser:headless")
chk("browserbase keeps its own name", rxfetch._rung_name("browserbase") == "browserbase")
chk("the agent rung is not double-prefixed", rxfetch._rung_name("agent:headful") == "agent:headful",
    "(got %s)" % rxfetch._rung_name("agent:headful"))

_real_attempt, _real_browser = rxfetch._one_attempt, rxfetch._browser_attempt
rxfetch._one_attempt = lambda url, timeout, via="http": (
    rxfetch.Result("", "unreachable", "stub 404"), False)
rxfetch._browser_attempt = lambda url, timeout: rxfetch.Result("", "unreadable", "stub")
try:
    with tempfile.TemporaryDirectory() as td5:
        rxfetch.configure(sources_dir=td5)
        r = rxfetch.fetch("https://trail.example/doc", allow_browser=True)
finally:
    rxfetch._one_attempt, rxfetch._browser_attempt = _real_attempt, _real_browser
layers = [a["layer"] for a in r.attempts]
chk("the trail records the cache miss", "cache" in layers, "(%s)" % layers)
chk("and that ncbi-api was skipped", "ncbi-api" in layers)
chk("and the http attempt", "http" in layers)
chk("and says why the browser did not run",
    any(a["layer"] == "browser" and "skipped" in str(a["result"]) for a in r.attempts),
    "(%s)" % r.attempts)

section("a not-found fetch steers back to search, not another guessed URL")
# The failure log showed every real fetch miss was a CONSTRUCTED url — a 404 on a real host or a
# URLError on an invented domain. The `next` hint now distinguishes those and points to search, so
# a worker recovers with a real result instead of guessing another path.
import io as _iof
import json as _jsonf
import types as _typesf
import contextlib as _clibf
_saved_fetch = _wa.rxfetch.fetch


def _fetch_next(detail):
    _wa.rxfetch.fetch = lambda url, **k: _wa.rxfetch.Result("", "unreachable", detail)
    ns = _typesf.SimpleNamespace(url="https://x/y", max_chars=1000, timeout=5,
                                 no_browser=True, trace=None, min_chars=200)
    buf = _iof.StringIO()
    with _clibf.suppress(SystemExit), _clibf.redirect_stdout(buf):
        _wa.cmd_fetch(ns)
    return (_jsonf.loads(buf.getvalue()).get("next") or "")


try:
    _n404 = _fetch_next("HTTP 404")
    chk("a 404 (real host, missing path) points to search", "Search" in _n404 and "path" in _n404,
        "(%s)" % _n404)
    _ndns = _fetch_next("URLError")
    chk("a DNS failure (invented domain) points to search", "resolve" in _ndns and "Search" in _ndns,
        "(%s)" % _ndns)
    _nto = _fetch_next("TimeoutError")
    chk("a generic unreachable keeps the reach caveat", "reach" in _nto, "(%s)" % _nto)
finally:
    _wa.rxfetch.fetch = _saved_fetch

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
