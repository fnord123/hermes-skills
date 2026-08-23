#!/usr/bin/env python3
"""Search the web, read a page, and carry out a task on a site — through tooling we control.

Three verbs, one JSON object on stdout each time:

    web_access.py search --query "..." [--max 10] [--scope literature|products|web]
    web_access.py fetch  --url "https://..." [--max-chars 20000]
    web_access.py do     --task "..." [--start-url ...] [--max-steps 25] [--confirm]

WHY THIS EXISTS. Hermes' built-in `web` toolset auto-selects a backend from whatever API keys
happen to be in the environment, ranking a paid provider FIRST - so a stale key silently
outranked the self-hosted stack and an entire research stage failed on a service nobody had
used in weeks (2026-07-31). Wrapping search and fetch in scripts we own removes that: the
backend is named here, in one place, and an agent given only these cannot reach anything else.

`search` talks to the self-hosted SearXNG directly. `fetch` is rxfetch, which is the reason
this skill is worth having rather than a two-line curl: it rate-limits per host ACROSS
processes, retries what deserves retrying, refuses to cache an interstitial, extracts PDFs, and
distinguishes `unreadable` (we reached the server and it gave us a bot wall) from `unreachable`
(we never got a response). A caller that cannot tell those apart writes "the source does not
support this claim" when the truth is "we were throttled" - which is how one citation audit
came to judge claims against "Checking your browser before accessing pubmed".

`fetch` tries the cheapest source that could work and stops at the first that yields text,
reporting which one did in `via`: a cached file, NCBI's API, plain HTTP, and finally a real
rendered browser page. The last tier reads JavaScript sites that return an empty shell to plain
HTTP, at a cost of seconds rather than milliseconds.

THE BROWSER TIER IS NO LONGER A FLAG. It used to be `--browser`, which the caller was expected
to add after seeing `unreadable` come back. That seam is why this skill and browse-task were
merged: two skills each claimed the JavaScript-page case, and the model routing to the other one
skipped this module's cache and its per-host gate entirely. Escalation is a property of the
fetcher now, and it costs nothing extra in the common case — it fires only on `unreadable`,
which means the server answered and withheld the document, the one failure a browser can fix.
--no-browser is kept for a caller that would rather fail than spend the seconds.

`do` is the other half of the merge: a browser session driven by an agent, for a goal that takes
several steps on a site rather than one page read. It is the only verb that can change anything,
and only with --confirm.
"""

import argparse
import glob
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxfetch                                                # noqa: E402

# The 200-character floor STAYS. Lowering it to read short pages was tried and immediately
# reported a 141-character Thorne JavaScript shell as ok=True - trading a harmless false
# positive for the false negative that caused the original incident. The two errors are not
# symmetric:
#
#   short real page called unreadable  -> the browser tier escalates and gets it
#   JS shell called ok                 -> the caller writes conclusions from an empty page
#
# The escalation makes the first harmless, so the conservative default is the correct one.
# --min-chars lowers it deliberately for a caller that knows it wants short pages.

# The one open-web engine worth asking first: a real keyed API, so it answers consistently
# instead of being rate-limited or CAPTCHA-walled like the scraped engines. If it is absent or
# unhealthy the search widens automatically, so nothing depends on it being configured.
PRIMARY_WEB_ENGINE = "brave api"


def _searxng_url():
    """The search endpoint: the environment first, then the Hermes env files.

    Reading the files matters. The environment is populated by whichever Hermes profile
    launched the caller, so a script run from a different profile, a cron job, or a plain shell
    would otherwise report "search is not available" while a perfectly good endpoint sat
    configured on disk. The value is an endpoint, not a credential.
    """
    v = os.environ.get("SEARXNG_URL", "").strip()
    if v:
        return v.rstrip("/")
    home = os.environ.get("HERMES_HOME", "")
    candidates = ([os.path.join(home, ".env")] if home else []) + [
        os.path.expanduser("~/.hermes/.env")]
    candidates += sorted(glob.glob(os.path.expanduser("~/.hermes/profiles/*/.env")))
    for path in candidates:
        try:
            for line in open(path, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line.startswith("SEARXNG_URL="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v.rstrip("/")
        except OSError:
            continue
    return ""


SEARXNG_URL = _searxng_url()

# What KIND of question is being asked, in the caller's vocabulary rather than the search
# engine's. The mapping to SearXNG categories lives here so it can change - add an engine, split
# a category - without touching a single caller.
#
# This exists because the science engines were installed, enabled, and never once queried.
# SearXNG dispatches by CATEGORY, and a search with no category set hits `general` only, which
# is bing and duckduckgo. Meanwhile pubmed, openalex, crossref, semantic scholar and arxiv sat
# in `science` waiting. Cards were told to prefer PubMed and Cochrane while the backend was
# structurally unable to ask them, which is a fair part of why they cited healthline and worse.
# What KIND of question is being asked, in the caller's vocabulary rather than the engine's.
# The mapping lives here so it can change - swap an engine, add a key - without touching a
# caller or a card body.
#
# `literature` MERGES its engines deliberately: PubMed, Semantic Scholar, OpenAlex, Crossref and
# arXiv index different corpora, so blending them is additive.
#
# The open-web scopes do the opposite. The `general` category has 64 engines enabled, including
# regional ones (baidu, sogou, quark, naver, seznam), and merging them dilutes a product lookup
# rather than strengthening it. So they ask ONE good engine first and only widen when it returns
# nothing - which also covers the cases where it is rate-limited, suspended, out of quota, or
# simply not configured on this instance.
SCOPES = {
    "literature": {"categories": "science"},
    "products": {"engines": PRIMARY_WEB_ENGINE, "widen": {"categories": "general"}},
    "web": {"engines": PRIMARY_WEB_ENGINE, "widen": {"categories": "general"}},
}
DEFAULT_SCOPE = "web"
DEFAULT_MAX = 10
# Enough to answer from, small enough that a worker's context survives several of them.
DEFAULT_MAX_CHARS = 20000


# ── search cache (7d) ────────────────────────────────────────────────────────
# A search is a lookup, not evidence: the same query recurs constantly (every review researches
# the same substances), the backend is rate-limited, and a result set is cheap to keep. So a
# query+scope is answered from disk for 7 days before we ask SearXNG again. Distinct from the fetch
# text cache, which is content-addressed and carries its own longer TTL (rxfetch.SOURCES_TTL,
# 30d) — a ranked result list goes stale faster than a page's text. Lives under the same
# web-access cache tree, so `rx.py reset` keeps it by default (only --clear-web-cache drops it).
_SEARCH_CACHE = os.path.expanduser(
    os.environ.get("RX_SEARCH_CACHE", "~/.hermes/cache/web-access/searches"))
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


# ── per-card search metrics ──────────────────────────────────────────────────
# Mirrors rxfetch's fetch events so a search shows up wherever a fetch does: ONE event per
# search — which card asked, the query and scope, and the outcome (cache_hit / searched / failed)
# — appended to the shared events JSONL and pushed best-effort to Loki under job="rx-search".
# Never raises: metrics must not break a search.
def _push_search_loki(ev):
    import urllib.request                                       # noqa: PLC0415
    labels = {"job": "rx-search", "scope": ev["scope"] or "web", "outcome": ev["outcome"]}
    body = json.dumps({"streams": [{"stream": labels,
                                    "values": [[str(ev["ts"] * 1_000_000), json.dumps(ev)]]}]})
    req = urllib.request.Request(rxfetch._LOKI_URL, data=body.encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=1.5).read()


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


def out(obj):
    """Print the one JSON object and exit: 0 on success, 1 on failure."""
    print(json.dumps(obj, indent=2))
    if not obj.get("ok"):
        sys.exit(1)
    sys.exit(0)


def _ask(query, selector, timeout):
    """One search against a given engine/category selector. Returns the parsed body.

    Through the same per-host gate as every fetch: the search engine is a website too, and a
    burst of queries is exactly the shape of traffic that gets a client suspended.
    """
    params = dict({"q": query, "format": "json"}, **selector)
    url = "%s/search?%s" % (SEARXNG_URL, urllib.parse.urlencode(params))
    # The same identity every other layer sends. This one went out as `Python-urllib/3.x`, which
    # is both inconsistent and the sort of thing an engine blocks first.
    req = urllib.request.Request(url, headers={"User-Agent": rxfetch.UA})
    with rxfetch.host_gate(rxfetch._host_of(SEARXNG_URL)):
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))


def run_search(query, scope, timeout=30):
    """Search once, widening only if the preferred engine returned nothing.

    Returns (body, widened). Split out from cmd_search so the widen decision can be tested
    without a network: the preferred engine answers even nonsense queries, so the fallback is
    unreachable from a live query and would otherwise only ever be exercised in production.

    NOTE: an unknown engine name does NOT produce an empty result set - SearXNG silently falls
    back to its default engines. So a missing or misnamed primary is already handled upstream,
    and this widen exists for the case that matters: a configured engine that answers nothing
    because it is suspended, rate-limited, or out of quota.
    """
    spec = SCOPES.get(scope) or {}
    primary = {k: v for k, v in spec.items() if k != "widen"}
    data = _ask(query, primary, timeout)
    if (data.get("results") or []) or not spec.get("widen"):
        return data, False
    return _ask(query, spec["widen"], timeout), True


def cmd_search(args):
    if getattr(args, "no_metrics", False):
        os.environ["RX_METRICS"] = "0"
    if not SEARXNG_URL:
        return out({"ok": False, "error": "SEARXNG_URL is not set for this profile. Search is "
                                          "not available; do not fall back to another engine."})
    t0 = time.time()

    # A cache hit answers from disk without touching the backend. The full result set is cached
    # (not the --max slice), so a later call with a larger --max still hits.
    cpath = _search_cache_path(args.query, args.scope)
    hit = _read_search_cache(cpath)
    if hit is not None:
        results = (hit.get("results") or [])[:args.max]
        _emit_search_event(args.query, args.scope, "cache_hit", len(results),
                           int((time.time() - t0) * 1000))
        return out({"ok": True, "query": args.query, "scope": args.scope,
                    "widened": hit.get("widened", False), "cached": True,
                    "count": len(results), "results": results,
                    "note": ("no results — try different terms" if not results else
                             "read a page with `fetch` before drawing a conclusion from it")})

    try:
        data, widened = run_search(args.query, args.scope, args.timeout)
    except Exception as exc:                                   # noqa: BLE001
        _emit_search_event(args.query, args.scope, "failed", 0, int((time.time() - t0) * 1000))
        return out({"ok": False, "query": args.query,
                    "error": "search backend unreachable: %s: %s" % (type(exc).__name__, exc)})

    everything = [{"title": (r.get("title") or "").strip(),
                   "url": r.get("url") or "",
                   "snippet": (r.get("content") or "").strip()[:400],
                   "engine": r.get("engine") or ""}
                  for r in (data.get("results") or [])]
    # Cache only a non-empty result set: an empty one is either a genuine miss (cheap to re-ask)
    # or a transient backend hiccup, and pinning either for 7 days is the wrong trade.
    if everything:
        _write_search_cache(cpath, {"ts": time.time(), "query": args.query, "scope": args.scope,
                                    "widened": widened, "results": everything})
    results = everything[:args.max]
    _emit_search_event(args.query, args.scope, "searched", len(results),
                       int((time.time() - t0) * 1000))
    # An empty result set is a FACT, not an error: say so plainly rather than leaving the
    # caller to infer that the backend broke and try to work around it.
    return out({"ok": True, "query": args.query, "scope": args.scope, "widened": widened,
                "cached": False, "count": len(results), "results": results,
                "note": ("no results — try different terms" if not results else
                         "read a page with `fetch` before drawing a conclusion from it")})


def cmd_fetch(args):
    if getattr(args, "no_metrics", False):
        os.environ["RX_METRICS"] = "0"
    if args.trace:
        os.environ["RXFETCH_TRACE"] = args.trace
        rxfetch.TRACE = args.trace
    rxfetch.configure(min_chars=args.min_chars)
    r = rxfetch.fetch(args.url, timeout=args.timeout, allow_browser=not args.no_browser)
    text = (r.text or "")
    truncated = len(text) > args.max_chars
    # The surface actually read, not the alias handed in — a caller that files this URL (a vault
    # entry, a citation) must end up with the one that will still answer when it is followed.
    body = {"ok": r.ok, "url": rxfetch.canonical_url(args.url), "outcome": r.outcome,
            "detail": r.detail,
            "via": r.via, "chars": len(text), "truncated": truncated,
            # Every layer tried, in order. A caller that can only see the verdict cannot tell a
            # layer that failed from one that never ran — which is the doubt this answers.
            "attempts": getattr(r, "attempts", []),
            "text": text[:args.max_chars]}
    if r.outcome == "unreadable" and args.no_browser:
        body["next"] = ("the server answered but withheld the document, and the browser tier "
                        "was skipped by --no-browser. Re-run this exact command without that "
                        "flag to render the page in a real browser.")
    elif r.outcome == "unreadable":
        body["next"] = ("the cheap tiers and a local browser render were all tried and none "
                        "returned the document. The site may need a session that works the "
                        "page rather than loading it — try the `do` verb with this URL as "
                        "--start-url. Otherwise report that you could not read it, and name "
                        "the URL.")
    elif r.outcome == "unreachable":
        detail = r.detail or ""
        if detail.startswith("HTTP 4"):
            # 404/410: the host answered but has no page at this path — almost always a URL that
            # was constructed rather than taken from a search result. Steer back to search.
            body["next"] = ("the site has no page at this path. Search for the page and fetch a "
                            "`url` from the results.")
        elif "URLError" in detail or "gaierror" in detail:
            # The domain did not resolve — usually an invented host. Steer back to search.
            body["next"] = ("that domain did not resolve. Search for the page and fetch a `url` "
                            "from the results.")
        else:
            body["next"] = ("no usable response. This is a fact about our reach, NOT evidence "
                            "about the page's content — never report it as though the page said "
                            "nothing.")
    return out(body)


def cmd_do(args):
    """Hand a multi-step task to the browser agent, in this process.

    Dispatch is by argv rather than a function call because browse_task owns its own argument
    parsing, defaults and config loading, and duplicating that here is how the two would drift
    back apart. It prints the one JSON object and exits, which is this module's contract too.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import browse_task                                          # noqa: PLC0415

    argv = ["browse_task", "--task", args.task, "--start-url", args.start_url,
            "--max-steps", str(args.max_steps)]
    if args.confirm:
        argv.append("--confirm")
    if args.cookies:
        argv += ["--cookies", args.cookies]
    if args.no_browserbase:
        argv.append("--no-browserbase")
    sys.argv = argv
    return browse_task.main()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="search the web via the self-hosted engine")
    p.add_argument("--query", required=True)
    p.add_argument("--scope", choices=sorted(SCOPES), default=DEFAULT_SCOPE,
                   help="what kind of question this is. `literature` searches the research "
                        "databases (papers, trials, reviews). `products` searches the open web "
                        "for manufacturer and retailer pages. `web` uses the default mix.")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--no-metrics", action="store_true", dest="no_metrics",
                   help="do not record this search in the metrics log/dashboard (for tests and "
                        "one-off checks, so fixture queries do not skew the stats)")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("fetch", help="read one page's text, rate-limited and retried")
    p.add_argument("--url", required=True)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, dest="max_chars")
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--no-browser", action="store_true", dest="no_browser",
                   help="skip the browser tier. Escalation is automatic and fires only when a "
                        "server answered without giving us the document; pass this when you "
                        "would rather have the failure than spend the seconds")
    p.add_argument("--trace", default=None,
                   help="append a detailed execution trace to this file: the curl-equivalent of "
                        "each request, response status and headers, the browser argv and exit "
                        "code, and the agent's prompt and reply")
    p.add_argument("--min-chars", type=int, default=200, dest="min_chars",
                   help="a response shorter than this is treated as an interstitial. The "
                        "default suits documents; lower it only if you know the page is "
                        "genuinely short, and never to make a bot wall look like success")
    p.add_argument("--no-metrics", action="store_true", dest="no_metrics",
                   help="do not record this fetch in the metrics log/dashboard (for tests and "
                        "one-off checks, so fixture fetches do not skew the stats)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("do", help="carry out a multi-step task on a site")
    p.add_argument("--task", required=True,
                   help="the goal, in plain English, including what to report back")
    p.add_argument("--start-url", dest="start_url", default="https://www.bing.com/",
                   help="page to open first. Name the site's own URL whenever the task is "
                        "about a particular site")
    p.add_argument("--max-steps", dest="max_steps", type=int, default=25,
                   help="cap on browser actions before giving up (default 25)")
    p.add_argument("--confirm", action="store_true",
                   help="allow the agent to ACT (sign in, submit, buy, book, post, send). "
                        "Required for any state-changing task, and only after the user "
                        "approved this exact task")
    p.add_argument("--cookies", default=None,
                   help="path to a JSON list of cookies to pre-seed (a delivery location, a "
                        "logged-in session) so the agent need not click through that setup")
    p.add_argument("--no-browserbase", dest="no_browserbase", action="store_true",
                   help="stay on the free local browser modes. browserbase is a paid remote "
                        "service; this keeps a run from escalating onto it. Config equivalent: "
                        "BROWSE_NO_BROWSERBASE=true")
    p.set_defaults(fn=cmd_do)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
