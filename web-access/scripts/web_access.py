#!/usr/bin/env python3
"""web_access.py — CLI shim for the webaccess service.

The verbs now live in a container on the docker host (one handler core, two
facades: HTTP and MCP). This shim keeps the old command line byte-for-byte —
it POSTs the args and prints the same JSON object, exiting 1 when ok=false.
Callers swap transport, not syntax; the JSON contract is unchanged.

    web_access.py search --query "..." [--scope literature|products|web] [--max 10]
    web_access.py fetch  --url "https://..." [--max-chars 20000]
    web_access.py do     --task "..." [--start-url ...] [--max-steps 25] [--confirm]

Service endpoint: WEBACCESS_URL env, else the default LAN address. There is
no local fallback: if the service is down the shim fails cleanly with an
error. The service's backends are not reachable from the LAN, so
a local path would have pointed at closed ports anyway.
"""

import argparse
import json
import os
import sys
import urllib.request

WEBACCESS_URL = (os.environ.get("WEBACCESS_URL") or "http://docker.putzolu.com:8910").rstrip("/")


def _post(path, payload, timeout):
    req = urllib.request.Request(
        WEBACCESS_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser(description="webaccess service CLI shim",
                                 add_help=True)
    ap.add_argument("cmd", choices=["search", "fetch", "do"])
    ap.add_argument("--query")
    ap.add_argument("--scope", default=None)
    ap.add_argument("--max", type=int, default=None, dest="max")
    ap.add_argument("--url")
    ap.add_argument("--max-chars", type=int, default=None, dest="max_chars")
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true", dest="no_browser")
    ap.add_argument("--trace", default=None)
    ap.add_argument("--min-chars", type=int, default=None, dest="min_chars")
    ap.add_argument("--no-metrics", action="store_true", dest="no_metrics")
    ap.add_argument("--task")
    ap.add_argument("--start-url", dest="start_url", default=None)
    ap.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--cookies", default=None)
    ap.add_argument("--no-browserbase", action="store_true", dest="no_browserbase")
    args, argv_rest = ap.parse_known_args()

    if args.cmd == "search":
        if not args.query:
            ap.error("--query is required")
        payload = {"query": args.query}
        if args.scope:
            payload["scope"] = args.scope
        if args.max is not None:
            payload["max_results"] = args.max
        if args.timeout is not None:
            payload["timeout"] = args.timeout
        if args.no_metrics:
            payload["no_metrics"] = True
        path, timeout = "/search", 60
    elif args.cmd == "fetch":
        if not args.url:
            ap.error("--url is required")
        payload = {"url": args.url}
        if args.max_chars is not None:
            payload["max_chars"] = args.max_chars
        if args.timeout is not None:
            payload["timeout"] = args.timeout
        if args.no_browser:
            payload["no_browser"] = True
        if args.trace:
            payload["trace"] = args.trace
        if args.min_chars is not None:
            payload["min_chars"] = args.min_chars
        if args.no_metrics:
            payload["no_metrics"] = True
        path, timeout = "/fetch", 45
    else:  # do
        if not args.task:
            ap.error("--task is required")
        payload = {"task": args.task}
        if args.start_url:
            payload["start_url"] = args.start_url
        if args.max_steps is not None:
            payload["max_steps"] = args.max_steps
        if args.confirm:
            payload["confirm"] = True
        if args.cookies:
            payload["cookies"] = args.cookies
        if args.no_browserbase:
            payload["no_browserbase"] = True
        path, timeout = "/do", 1950

    try:
        body = _post(path, payload, timeout)
    except Exception as exc:
        print(json.dumps({"ok": False,
                          "error": "webaccess service is unreachable at %s: %s: %s. "
                                   "There is no local fallback — backends are not "
                                   "reachable from the LAN. Fix the service and retry."
                                   % (WEBACCESS_URL, type(exc).__name__, exc)}))
        sys.exit(1)

    print(json.dumps(body, indent=2))
    sys.exit(0 if body.get("ok") else 1)


if __name__ == "__main__":
    main()
