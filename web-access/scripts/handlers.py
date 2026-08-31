#!/usr/bin/env python3
"""The webaccess handler core — the ONE implementation of the three verbs.

`search`, `fetch` and `do` are served from here by every facade: the HTTP API
(app.py) and the MCP server (mcp_server.py) both delegate to the functions in
this file. There is no second implementation anywhere; "shim" means thin
facade, not separate codepath.

Runs from the Docker container on the docker host (the service owns its own
environment, so none of the profile .env discovery of the old CLI remains):

    run_search(query, scope=..., max_results=..., timeout=..., no_metrics=...)
    cmd_fetch(url, max_chars=..., timeout=..., no_browser=..., trace=...,
              min_chars=..., no_metrics=...)
    run_do(task, start_url=..., max_steps=..., confirm=..., cookies=...,
           no_browserbase=...)
    health()

Each returns the same JSON-ready dict the old CLI printed, so callers migrate
by swapping transport, not parsing.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxfetch                                                # noqa: E402

# The 200-character floor STAYS. Lowering it to read short pages was tried and immediately
# reported a 141-character Thorne JavaScript shell as ok=True - trading a harmless false
# positive for the false negative that caused the original incident.
MIN_CHARS_DEFAULT = 200

# The one open-web engine worth asking first: a real keyed API, so it answers consistently
# instead of being rate-limited or CAPTCHA-walled like the scraped engines. If it is absent or
# unhealthy the search widens automatically, so nothing depends on it being configured.
PRIMARY_WEB_ENGINE = "brave api"

# What KIND of question is being asked, in the caller's vocabulary rather than the search
# engine's. `literature` MERGES its engines (PubMed, Semantic Scholar, OpenAlex, Crossref,
# arXiv index different corpora, so blending them is additive). The open-web scopes ask ONE
# good engine first and only widen when it returns nothing.
SCOPES = {
    "literature": {"categories": "science"},
    "products": {"engines": PRIMARY_WEB_ENGINE, "widen": {"categories": "general"}},
    "web": {"engines": PRIMARY_WEB_ENGINE, "widen": {"categories": "general"}},
}
DEFAULT_SCOPE = "web"
DEFAULT_MAX = 10
# Enough to answer from, small enough that a worker's context survives several of them.
DEFAULT_MAX_CHARS = 20000

# All service state lives under WEBACCESS_HOME (a named volume in the container:
# /var/lib/webaccess). Locally it falls back to the historical tree so the files can still
# be exercised on the dev machine.
_WEBACCESS_HOME = os.environ.get("WEBACCESS_HOME") or os.path.expanduser("~/.webaccess-home")


def _home(*parts):
    return os.path.join(_WEBACCESS_HOME, *parts)


SEARXNG_URL = (os.environ.get("SEARXNG_URL") or "").rstrip("/")

# ── search cache (7d) ────────────────────────────────────────────────────────
# A search is a lookup, not evidence: the same query recurs constantly, the backend is
# rate-limited, and a result set is cheap to keep. A query+scope is answered from disk for
# 7 days before we ask SearXNG again. Distinct from the fetch text cache (30d TTL,
# rxfetch.SOURCES_TTL) - a ranked result list goes stale faster than a page's text.
_SEARCH_CACHE = os.environ.get("RX_SEARCH_CACHE") or _home("cache", "searches")
_SEARCH_TTL = int(os.environ.get("RX_SEARCH_TTL", "604800"))    # seconds; 7d


def _search_cache_path(query, scope):
    key = hashlib.sha1(("%s\x1f%s" % (scope, query)).encode()).hexdigest()[:16]
    return os.path.join(_SEARCH_CACHE, key + ".json")


def _read_search_cache(path):
    """The cached result set for this query+scope if one exists and is younger than the TTL."""
    try:
        if os.path.exists(path):
            obj = json.load(open(path, encoding="utf-8"))
            if time.time() - obj.get("ts", 0) < _SEARCH_TTL:
                return obj
    except Exception:                                          # noqa: BLE001
        pass
    return None


def _write_search_cache(path, obj):
    """Publish the result set atomically (temp file then rename), never raising."""
    try:
        os.makedirs(_SEARCH_CACHE, exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except Exception:                                          # noqa: BLE001
        pass


# ── search metrics ───────────────────────────────────────────────────────────
# Mirrors rxfetch's fetch events so a search shows up wherever a fetch does. Never raises:
# metrics must not break a search.
def _push_search_loki(ev):
    try:
        labels = {"job": "rx-search", "scope": ev["scope"] or "web", "outcome": ev["outcome"]}
        body = json.dumps({"streams": [{"stream": labels,
                                        "values": [[str(ev["ts"] * 1_000_000), json.dumps(ev)]]}]})
        req = urllib.request.Request(rxfetch._LOKI_URL, data=body.encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:                                          # noqa: BLE001
        pass


def _emit_search_event(query, scope, outcome, count, ms):
    if not rxfetch._metrics_enabled():
        return
    try:
        ev = {"ts": int(time.time() * 1000), "kind": "search",
              "card": os.environ.get("HERMES_KANBAN_TASK", ""),
              "query": (query or "")[:200], "scope": scope or "web",
              "outcome": outcome, "count": count, "ms": ms}
    except Exception:                                          # noqa: BLE001
        return
    try:
        os.makedirs(os.path.dirname(rxfetch._FETCH_EVENTS_PATH), exist_ok=True)
        with open(rxfetch._FETCH_EVENTS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")
    except Exception:                                          # noqa: BLE001
        pass
    try:
        _push_search_loki(ev)
    except Exception:                                          # noqa: BLE001
        pass


def _ask(query, selector, timeout):
    """One search against a given engine/category selector. Returns the parsed body.

    Through the same per-host gate as every fetch: the search engine is a website too, and a
    burst of queries is exactly the shape of traffic that gets a client suspended.
    """
    params = dict({"q": query, "format": "json"}, **selector)
    url = "%s/search?%s" % (SEARXNG_URL, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": rxfetch.UA})
    with rxfetch.host_gate(rxfetch._host_of(SEARXNG_URL)):
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))


def run_search_impl(query, scope, timeout):
    """Search once, widening only if the preferred engine returned nothing.

    Returns (body, widened). An unknown engine name does NOT produce an empty result set -
    SearXNG silently falls back to its default engines - so this widen exists for the case
    that matters: a configured engine that answers nothing because it is suspended,
    rate-limited, or out of quota.
    """
    spec = SCOPES.get(scope) or {}
    primary = {k: v for k, v in spec.items() if k != "widen"}
    data = _ask(query, primary, timeout)
    if (data.get("results") or []) or not spec.get("widen"):
        return data, False
    return _ask(query, spec["widen"], timeout), True


def run_search(query, scope=DEFAULT_SCOPE, max_results=DEFAULT_MAX, timeout=30,
               no_metrics=False):
    """The `search` verb: find pages. Returns the response dict (see the old CLI's shape)."""
    if no_metrics:
        os.environ["RX_METRICS"] = "0"
    if not SEARXNG_URL:
        return {"ok": False,
                "error": "SEARXNG_URL is not set for the service. Search is not available; "
                         "do not fall back to another engine."}
    t0 = time.time()

    # A cache hit answers from disk without touching the backend. The full result set is
    # cached (not the max slice), so a later call with a larger max still hits.
    cpath = _search_cache_path(query, scope)
    hit = _read_search_cache(cpath)
    if hit is not None:
        results = (hit.get("results") or [])[:max_results]
        _emit_search_event(query, scope, "cache_hit", len(results),
                           int((time.time() - t0) * 1000))
        return {"ok": True, "query": query, "scope": scope,
                "widened": hit.get("widened", False), "cached": True,
                "count": len(results), "results": results,
                "note": ("no results — try different terms" if not results else
                         "read a page with `fetch` before drawing a conclusion from it")}

    try:
        data, widened = run_search_impl(query, scope, timeout)
    except Exception as exc:                                   # noqa: BLE001
        _emit_search_event(query, scope, "failed", 0, int((time.time() - t0) * 1000))
        return {"ok": False, "query": query,
                "error": "search backend unreachable: %s: %s" % (type(exc).__name__, exc)}

    everything = [{"title": (r.get("title") or "").strip(),
                   "url": r.get("url") or "",
                   "snippet": (r.get("content") or "").strip()[:400],
                   "engine": r.get("engine") or ""}
                  for r in (data.get("results") or [])]
    # Cache only a non-empty result set: an empty one is either a genuine miss (cheap to
    # re-ask) or a transient backend hiccup, and pinning either for 7 days is the wrong trade.
    if everything:
        _write_search_cache(cpath, {"ts": time.time(), "query": query, "scope": scope,
                                    "widened": widened, "results": everything})
    results = everything[:max_results]
    _emit_search_event(query, scope, "searched", len(results), int((time.time() - t0) * 1000))
    # An empty result set is a FACT, not an error: say so plainly.
    return {"ok": True, "query": query, "scope": scope, "widened": widened,
            "cached": False, "count": len(results), "results": results,
            "note": ("no results — try different terms" if not results else
                     "read a page with `fetch` before drawing a conclusion from it")}


def cmd_fetch(url, max_chars=DEFAULT_MAX_CHARS, timeout=45, no_browser=False,
              trace=None, min_chars=MIN_CHARS_DEFAULT, no_metrics=False):
    """The `fetch` verb: read one page. Returns the response dict (same shape as the old CLI).

    `trace` may name a file or be truthy (a per-request name under WEBACCESS_HOME); the
    service appends the execution trace there and reports the path in the response.
    """
    if no_metrics:
        os.environ["RX_METRICS"] = "0"
    if trace:
        if isinstance(trace, str) and "/" not in str(trace) and "." not in str(trace):
            trace = _home("traces", "fetch-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(os.path.abspath(str(trace))), exist_ok=True)
        os.environ["RXFETCH_TRACE"] = str(trace)
        rxfetch.TRACE = str(trace)
    rxfetch.configure(min_chars=min_chars)
    r = rxfetch.fetch(url, timeout=timeout, allow_browser=not no_browser)
    text = (r.text or "")
    truncated = len(text) > max_chars
    # The surface actually read, not the alias handed in — a caller that files this URL must
    # end up with the one that will still answer when it is followed.
    #
    # BLACK BOX: the model gets the verdict (`outcome`), the document (`text`), and what to do
    # next (`next`) — and nothing else. The fetcher's internal ladder is not part of the
    # contract: `via`, `detail` and the `attempts` trail each name a rung (a render service, a
    # stealth browser, a metered provider), and none of that crosses this boundary. The container
    # still holds the full trail for its audit log; it simply does not return it.
    body = {"ok": r.ok, "url": rxfetch.canonical_url(url), "outcome": r.outcome,
            "chars": len(text), "truncated": truncated,
            "text": text[:max_chars]}
    if trace:
        body["trace"] = str(trace)
    if r.outcome == "unreadable" and no_browser:
        body["next"] = ("the server answered but withheld the document, and the browser tier "
                        "was skipped by no_browser. Re-run this fetch without that flag to "
                        "render the page in a real browser.")
    elif r.outcome == "unreadable":
        body["next"] = ("the cheap tiers and a local browser render were all tried and none "
                        "returned the document. The site may need a session that works the "
                        "page rather than loading it — try the `do` verb with this URL as "
                        "start_url. Otherwise report that you could not read it, and name "
                        "the URL.")
    elif r.outcome == "unreachable":
        detail = r.detail or ""
        if detail.startswith("HTTP 4"):
            body["next"] = ("the site has no page at this path. Search for the page and fetch "
                            "a `url` from the results.")
        elif "URLError" in detail or "gaierror" in detail:
            body["next"] = ("that domain did not resolve. Search for the page and fetch a "
                            "`url` from the results.")
        else:
            body["next"] = ("no usable response. This is a fact about our reach, NOT evidence "
                            "about the page's content — never report it as though the page "
                            "said nothing.")
    return body


BROWSE_TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browse_task.py")
# A standalone `do` is the whole run (the agent is the point, not a last resort): the old CLI
# capped it at 30 minutes. The service adds a margin so the child dies with its own
# diagnostics, not a parent kill mid-stream.
DO_TIMEOUT = int(os.environ.get("WEBACCESS_DO_TIMEOUT") or 1900)


def run_do(task, start_url="https://www.bing.com/", max_steps=25, confirm=False,
           cookies=None, no_browserbase=False):
    """The `do` verb: carry out a multi-step task on a site via the browser agent.

    Runs browse_task.py as a SUBPROCESS rather than in-process: it owns its own argv,
    defaults and config loading, and a 30-minute agent run must not hold a server thread.
    It prints the one JSON object and exits, which is what this returns.
    """
    argv = [sys.executable, BROWSE_TASK, "--task", task, "--start-url", start_url,
            "--max-steps", str(max_steps)]
    if confirm:
        argv.append("--confirm")
    if cookies:
        argv += ["--cookies", cookies]
    if no_browserbase:
        argv.append("--no-browserbase")
    env = dict(os.environ)
    env.pop("RXFETCH_TRACE", None)          # a do run traces itself via BROWSE_LOG
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env,
                           timeout=DO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timed_out",
                "error": "the browser agent run exceeded the %ds service budget and was "
                         "killed; partial findings may exist in the service log" % DO_TIMEOUT}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": "could not run the browser agent: %s" % exc}
    out = (p.stdout or "").strip()
    try:
        # browse_task prints exactly one JSON object on stdout (its log goes to a file).
        start = out.find("{")
        return json.loads(out[start:]) if start != -1 else {"ok": False, "error": "no result"}
    except ValueError:
        tail = (p.stderr or out or "").strip()[-400:]
        return {"ok": False, "error": "browser agent returned no parseable result "
                                      "(exit %s): %s" % (p.returncode, tail)}


def health():
    """Liveness plus the state the verbs depend on, so a failure names its cause."""
    def _live(url, timeout=5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": rxfetch.UA})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return 100 <= getattr(fh, "status", 200) < 500
        except Exception:                                      # noqa: BLE001
            return False

    return {
        "ok": True,
        "service": "webaccess",
        "searxng": {"url": SEARXNG_URL or None, "live": bool(SEARXNG_URL) and _live(SEARXNG_URL)},
        "firecrawl": {"url": rxfetch.FIRECRAWL_URL or None,
                      "live": bool(rxfetch.FIRECRAWL_URL) and _live(rxfetch.FIRECRAWL_URL)},
        "bladebro": {"configured": bool(rxfetch.BLADEBRO_SSH)
                     or shutil_which("docker") is not None},
        "scaffold": os.path.exists(os.path.join(_fara_home(), ".venv", "bin", "fara-cli")),
        "xvfb": shutil_which("xvfb-run") is not None,
    }


def _fara_home():
    return (os.environ.get("FARA_HOME")
            or _read_key(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "config.env"), "FARA_HOME")
            or "")


def shutil_which(name):
    import shutil                                             # noqa: PLC0415
    return shutil.which(name)


def _read_key(envfile, key):
    try:
        for line in open(envfile, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""
