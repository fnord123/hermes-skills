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
import json
import os
import sys
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
    with rxfetch.host_gate(rxfetch._host_of(SEARXNG_URL)):
        with urllib.request.urlopen(url, timeout=timeout) as fh:
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
    if not SEARXNG_URL:
        return out({"ok": False, "error": "SEARXNG_URL is not set for this profile. Search is "
                                          "not available; do not fall back to another engine."})
    try:
        data, widened = run_search(args.query, args.scope, args.timeout)
    except Exception as exc:                                   # noqa: BLE001
        return out({"ok": False, "query": args.query,
                    "error": "search backend unreachable: %s: %s" % (type(exc).__name__, exc)})

    results = []
    for r in (data.get("results") or [])[:args.max]:
        results.append({"title": (r.get("title") or "").strip(),
                        "url": r.get("url") or "",
                        "snippet": (r.get("content") or "").strip()[:400],
                        "engine": r.get("engine") or ""})
    # An empty result set is a FACT, not an error: say so plainly rather than leaving the
    # caller to infer that the backend broke and try to work around it.
    return out({"ok": True, "query": args.query, "scope": args.scope, "widened": widened,
                "count": len(results), "results": results,
                "note": ("no results — try different terms" if not results else
                         "read a page with `fetch` before drawing a conclusion from it")})


def cmd_fetch(args):
    rxfetch.configure(min_chars=args.min_chars)
    r = rxfetch.fetch(args.url, timeout=args.timeout, allow_browser=not args.no_browser)
    text = (r.text or "")
    truncated = len(text) > args.max_chars
    body = {"ok": r.ok, "url": args.url, "outcome": r.outcome, "detail": r.detail,
            "via": r.via, "chars": len(text), "truncated": truncated,
            "text": text[:args.max_chars]}
    if r.outcome == "unreadable" and args.no_browser:
        body["next"] = ("the server answered but withheld the document, and the browser tier "
                        "was skipped by --no-browser. Re-run this exact command without that "
                        "flag to render the page in a real browser.")
    elif r.outcome == "unreadable":
        body["next"] = ("every tier including a real browser was tried and none returned the "
                        "document. The content is behind a login or genuinely absent. Report "
                        "that you could not read it, and name the URL.")
    elif r.outcome == "unreachable":
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
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("fetch", help="read one page's text, rate-limited and retried")
    p.add_argument("--url", required=True)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, dest="max_chars")
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--no-browser", action="store_true", dest="no_browser",
                   help="skip the browser tier. Escalation is automatic and fires only when a "
                        "server answered without giving us the document; pass this when you "
                        "would rather have the failure than spend the seconds")
    p.add_argument("--min-chars", type=int, default=200, dest="min_chars",
                   help="a response shorter than this is treated as an interstitial. The "
                        "default suits documents; lower it only if you know the page is "
                        "genuinely short, and never to make a bot wall look like success")
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
