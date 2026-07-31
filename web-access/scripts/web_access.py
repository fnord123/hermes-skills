#!/usr/bin/env python3
"""Search the web and read a page, through tooling we control.

Two verbs, one JSON object on stdout each time:

    web_access.py search --query "..." [--max 10] [--json]
    web_access.py fetch  --url "https://..." [--max-chars 20000]

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
reporting which one did in `via`: a cached file, NCBI's API, plain HTTP, another worker's
extract, and finally — only with --browser — a real rendered browser page. The last tier reads
JavaScript sites that return an empty shell to plain HTTP, at a cost of seconds rather than
milliseconds, which is why it is opt-in rather than automatic.
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
#   short real page called unreadable  -> the caller escalates to browse-task and gets it
#   JS shell called ok                 -> the caller writes conclusions from an empty page
#
# The escalation makes the first harmless, so the conservative default is the correct one.
# --min-chars lowers it deliberately for a caller that knows it wants short pages.

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
DEFAULT_MAX = 10
# Enough to answer from, small enough that a worker's context survives several of them.
DEFAULT_MAX_CHARS = 20000


def out(obj):
    """Print the one JSON object and exit: 0 on success, 1 on failure."""
    print(json.dumps(obj, indent=2))
    if not obj.get("ok"):
        sys.exit(1)
    sys.exit(0)


def cmd_search(args):
    if not SEARXNG_URL:
        return out({"ok": False, "error": "SEARXNG_URL is not set for this profile. Search is "
                                          "not available; do not fall back to another engine."})
    url = "%s/search?%s" % (SEARXNG_URL, urllib.parse.urlencode(
        {"q": args.query, "format": "json"}))
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as fh:
            data = json.loads(fh.read().decode("utf-8", "replace"))
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
    return out({"ok": True, "query": args.query, "count": len(results),
                "results": results,
                "note": ("no results — try different terms" if not results else
                         "read a page with `fetch`; if that returns unreadable, the page needs "
                         "the browse-task skill")})


def cmd_fetch(args):
    rxfetch.configure(min_chars=args.min_chars)
    r = rxfetch.fetch(args.url, timeout=args.timeout, allow_browser=args.browser)
    text = (r.text or "")
    truncated = len(text) > args.max_chars
    body = {"ok": r.ok, "url": args.url, "outcome": r.outcome, "detail": r.detail,
            "via": r.via, "chars": len(text), "truncated": truncated,
            "text": text[:args.max_chars]}
    if r.outcome == "unreadable" and not args.browser:
        body["next"] = ("the server answered but did not give us the document (JavaScript "
                        "shell, bot wall, or login). Retrying will not help, and neither will "
                        "another URL from the same site — re-run this exact command with "
                        "--browser, which renders the page in a real browser.")
    elif r.outcome == "unreadable":
        body["next"] = ("a real browser rendered this page and it still had no document. "
                        "The content is behind a login or genuinely absent. Report that you "
                        "could not read it; do not guess at what it said.")
    elif r.outcome == "unreachable":
        body["next"] = ("no usable response. This is a fact about our reach, NOT evidence "
                        "about the page's content — never report it as though the page said "
                        "nothing.")
    return out(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="search the web via the self-hosted engine")
    p.add_argument("--query", required=True)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("fetch", help="read one page's text, rate-limited and retried")
    p.add_argument("--url", required=True)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, dest="max_chars")
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--browser", action="store_true",
                   help="if every cheap tier fails, render the page in a real browser. Costs "
                        "seconds and a browser process, so it is off by default — use it when "
                        "a plain fetch came back unreadable, not pre-emptively")
    p.add_argument("--min-chars", type=int, default=200, dest="min_chars",
                   help="a response shorter than this is treated as an interstitial. The "
                        "default suits documents; lower it only if you know the page is "
                        "genuinely short, and never to make a bot wall look like success")
    p.set_defaults(fn=cmd_fetch)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
