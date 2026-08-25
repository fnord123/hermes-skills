#!/usr/bin/env python3
"""The webaccess HTTP API — one facade over the handler core.

`search`, `fetch` and `do` arrive as JSON POSTs and leave as the same JSON
objects the old CLI printed. The verb handlers live in handlers.py and are
the ONE implementation; the MCP facade reaches them through this API, and
the CLI shim does the same — no second codepath anywhere.

Stdlib only (http.server, no web framework): the service's whole job is
three long-running POSTs and a health endpoint, and every fetch that ever
happens logs one line here, which is what makes the container the single
audit point the architecture calls for.

    POST /search  {"query", "scope"?, "max_results"?, "timeout"?, "no_metrics"?}
    POST /fetch   {"url", "max_chars"?, "timeout"?, "no_browser"?, "trace"?,
                   "min_chars"?, "no_metrics"?}
    POST /do      {"task", "start_url"?, "max_steps"?, "confirm"?, "cookies"?,
                   "no_browserbase"?}
    GET  /health  liveness plus the state each verb depends on

Bind: LAN-only by deployment choice (compose publishes the container port on
the docker host's LAN interface, not 0.0.0.0), so the network boundary is the
capability boundary — anything on the LAN can call all three verbs, and that
is accepted for search/fetch (read-only) and, for now, for do.
"""

import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handlers                                              # noqa: E402

PORT = int(os.environ.get("WEBACCESS_PORT") or 8910)
# A verb request carries a URL, a query, or a task — never a document. 1 MiB
# is four orders of magnitude past anything real; more is an accident.
MAX_BODY = 1024 * 1024


def _verb_search(p):
    return handlers.run_search(
        query=p.get("query") or "",
        scope=p.get("scope") or handlers.DEFAULT_SCOPE,
        max_results=int(p.get("max_results") or handlers.DEFAULT_MAX),
        timeout=int(p.get("timeout") or 30),
        no_metrics=bool(p.get("no_metrics")))


def _verb_fetch(p):
    return handlers.cmd_fetch(
        url=p.get("url") or "",
        max_chars=int(p.get("max_chars") or handlers.DEFAULT_MAX_CHARS),
        timeout=int(p.get("timeout") or 45),
        no_browser=bool(p.get("no_browser")),
        trace=p.get("trace"),
        min_chars=int(p.get("min_chars") or handlers.MIN_CHARS_DEFAULT),
        no_metrics=bool(p.get("no_metrics")))


def _verb_do(p):
    return handlers.run_do(
        task=p.get("task") or "",
        start_url=p.get("start_url") or "https://www.bing.com/",
        max_steps=int(p.get("max_steps") or 25),
        confirm=bool(p.get("confirm")),
        cookies=p.get("cookies"),
        no_browserbase=bool(p.get("no_browserbase")))


VERBS = {"/search": _verb_search, "/fetch": _verb_fetch, "/do": _verb_do}
# One audit line per request: verb, what it was asked for, outcome, cost.
# The container's stdout IS the audit trail.
_AUDIT = {"/search": lambda p: "query=%s" % (p.get("query") or "")[:120],
          "/fetch": lambda p: "url=%s" % (p.get("url") or "")[:200],
          "/do": lambda p: "task=%s confirm=%s" % ((p.get("task") or "")[:120],
                                                   bool(p.get("confirm")))}


class _ApiHandler(BaseHTTPRequestHandler):
    server_version = "webaccess/1.0"
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                          # noqa: N802
        if self.path == "/health":
            self._send(200, handlers.health())
        else:
            self._send(404, {"ok": False,
                             "error": "unknown path — POST /search /fetch /do, or GET /health"})

    def do_POST(self):                                         # noqa: N802
        verb = VERBS.get(self.path)
        if verb is None:
            self._send(404, {"ok": False,
                             "error": "unknown path — POST /search /fetch /do"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": "request body too large (max %d bytes)" % MAX_BODY})
            return
        try:
            payload = json.loads(self.rfile.read(length or 0).decode("utf-8", "replace") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as exc:
            self._send(400, {"ok": False, "error": "bad request body: %s" % exc})
            return

        t0 = time.time()
        try:
            result = verb(payload)
        except Exception:                                     # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            result = {"ok": False, "error": "the handler crashed; see the service log"}
        ms = int((time.time() - t0) * 1000)
        print("webaccess %-7s ok=%-5s ms=%-6d %s" %
              (self.path, bool(result.get("ok")), ms, _AUDIT[self.path](payload)), flush=True)
        self._send(200, result)

    def log_message(self, format, *args):                      # noqa: A002
        pass    # the audit line above is the log; the default per-request noise is not


def make_server(port=PORT, host="0.0.0.0"):
    srv = ThreadingHTTPServer((host, port), _ApiHandler)
    srv.daemon_threads = True
    return srv


def main():
    srv = make_server()
    print("webaccess HTTP API listening on 0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
