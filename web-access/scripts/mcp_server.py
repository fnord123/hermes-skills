#!/usr/bin/env python3
"""The webaccess MCP facade — typed tools over the ONE handler core.

Hermes profiles reach the three verbs as typed MCP tools from their
config.yaml (HTTP transport, url: http://docker.putzolu.com:8911/mcp). This file
serves the /mcp endpoint: it speaks MCP over Streamable HTTP (one session,
list tools, post calls) and delegates every call to the HTTP verb handlers
in app.py, in the SAME process. There is no second implementation: the
facade and the HTTP API are two faces of handlers.py.

Stdlib only, same reasoning as app.py. No web framework in the image.

A `do` call runs up to the do-run budget (handlers.DO_TIMEOUT, 21 minutes for
the 100-round default at the measured ~8.3s/round). tools/call answers over an
SSE stream with periodic pings, so client idle-read timeouts cannot sever a
healthy long call (see _stream_reply). Profiles registering this server should
still give it a matching per-call timeout (timeout: 1320 in the mcp_servers
entry), and treat `do` as a long-poll verb.
"""

import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app                                                 # noqa: E402

MCP_PORT = int(os.environ.get("WEBACCESS_MCP_PORT") or 8911)
# SSE keepalive cadence for the streamed `do` response (see _stream_reply):
# must stay well under client read-timeouts between bytes (300 s observed on
# the Hermes streamable-HTTP path — that cap killed run A and forked run B
# as an auto-retry on 2026-09-27).
STREAM_PING_SECS = float(os.environ.get("WEBACCESS_MCP_PING_SECS") or 20)
PROTOCOL_VERSION = "2025-03-26"
MAX_BODY = 1024 * 1024

# The typed schemas ARE the contract that replaces SKILL.md discipline: the
# model reads these descriptions, not a prose file.
TOOLS = [
    {
        "name": "search",
        "description": ("Search the web and return titles, URLs and snippets — never page "
                        "content. Use scope 'literature' for research databases (PubMed, "
                        "Semantic Scholar, OpenAlex, Crossref, arXiv); 'products' for "
                        "manufacturer/retailer pages; default 'web' for everything else. "
                        "Read a found page with the fetch-content tool before drawing a conclusion "
                        "from it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "scope": {"type": "string", "enum": ["web", "products", "literature"],
                          "description": "Where to search. Default web."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                                "description": "Maximum results to return. Default 10."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch-content",
        "description": ("Read one or more URLs and return each document verbatim — text, "
                        "with PDFs handled. Escalation is this tool's job: it climbs cache, "
                        "NCBI API, plain HTTP, a self-hosted render, and a stealth browser, "
                        "cheapest first. Pass ONE url or a LIST (urls, up to 20; batch runs "
                        "parallel across hosts, polite-serial per host). The response is "
                        "always {ok, results: [{url, ...}]} — one element per URL, in order. "
                        "Each result's outcome is 'ok', 'unreadable' (the server answered but "
                        "withheld the document — report that it could not be read; never state "
                        "what an unread page 'says'), or 'unreachable' (no usable response). "
                        "A result may carry age_hours: how old the served copy is (0 after the "
                        "origin re-certified it); absence means fetched live just now. Never "
                        "report an unread page as empty. For byte-exact content or a hash, "
                        "use fetch-bytes instead — this tool extracts text and truncates. "
                        "This is the fleet's sanctioned web-retrieval lane: never shell-fetch "
                        "a page with curl, wget, or urllib. max_chars truncates from the top "
                        "— there is no range or offset fetch; when the runtime saves an "
                        "oversized result whole to a local file, page that file instead of "
                        "re-requesting the URL."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "One URL to read."},
                "urls": {"type": "array", "items": {"type": "string"},
                         "description": "A batch (up to 20). Pass url OR urls."},
                "max_chars": {"type": "integer", "minimum": 1,
                              "description": "Truncate each text to this many characters. "
                                             "Default 20000."},
                "no_browser": {"type": "boolean",
                               "description": "Skip the browser render rungs; fail rather "
                                              "than spend the seconds. Default false."},
            },
        },
    },
    {
        "name": "fetch-bytes",
        "description": ("Get one or more URLs' EXACT ORIGIN BYTES — for hashing, integrity "
                        "checks, or verbatim transport. The default answer is METADATA ONLY: "
                        "sha256 (over the raw bytes, before any decode), size, content_type, "
                        "served_via, and age_hours when cached — zero file content enters "
                        "your context. raw=true adds the payload (utf-8 for text types, "
                        "base64 otherwise) up to max_bytes; over-cap returns metadata and "
                        "says so — it never truncates a hashable body silently. served_via: "
                        "'direct' = what a plain HTTP client receives; 'rendered' = the wire "
                        "bytes a local browser was served (network-layer capture, not the "
                        "DOM) — anti-bot sites can answer different clients differently, so "
                        "the field keeps a hash honest about which view it hashes. Never "
                        "uses the paid remote browser. Response shape is always "
                        "{ok, results: [...]}, same batching rules as fetch-content. "
                        "Same sanctioned lane: never shell-fetch bytes directly. "
                        "Re-requesting a URL replays its cache entry — a second request "
                        "is not independent verification of the first."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "One URL."},
                "urls": {"type": "array", "items": {"type": "string"},
                         "description": "A batch (up to 20). Pass url OR urls."},
                "raw": {"type": "boolean",
                        "description": "Include the payload, not just its metadata. "
                                       "Default false."},
                "max_bytes": {"type": "integer", "minimum": 1,
                              "description": "Payload cap when raw=true. Default 2000000."},
                "no_render": {"type": "boolean",
                              "description": "Direct HTTP only; never capture via a local "
                                             "browser render. Default false."},
            },
        },
    },
    {
        "name": "do",
        "description": ("Carry out a multi-step task on a website in a real browser and "
                        "return an answer (not a document): apply the site's filters, page "
                        "through listings, follow a flow across screens. This is the only "
                        "verb that can ACT on a site. Without confirm the run is strictly "
                        "read-only by instruction; confirm=true means the user approved "
                        "that exact action — use it only after that approval, never on a "
                        "guess. A run can take up to ~20 minutes at the 100-round "
                        "default; budget your call timeout accordingly."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task, in plain language."},
                "start_url": {"type": "string",
                              "description": "The site to start on. Name it whenever the "
                                             "task is about a particular site."},
                "max_steps": {"type": "integer", "minimum": 1,
                              "description": "Step budget. Default 100; a full run may "
                                             "take up to ~50 minutes."},
                "confirm": {"type": "boolean",
                            "description": "true only after the user approved this exact "
                                           "action. Default false (strictly read-only "
                                           "instruction is prepended)."},
                "cookies": {"type": "string",
                            "description": "Optional path (on the service) to a JSON "
                                           "cookie file to pre-seed into the browser."},
            },
            "required": ["task"],
        },
    },
]


def _dispatch(name, args):
    """Route a tools/call to the verb handler and shape the MCP result."""
    if name == "search":
        body = app._verb_search({"query": args.get("query"),
                                 "scope": args.get("scope"),
                                 "max_results": args.get("max_results")})
    elif name == "fetch-content":
        body = app._verb_fetch({"url": args.get("url"),
                                "urls": args.get("urls"),
                                "max_chars": args.get("max_chars"),
                                "no_browser": args.get("no_browser")})
    elif name == "fetch-bytes":
        body = app._verb_fetch_bytes({"url": args.get("url"),
                                      "urls": args.get("urls"),
                                      "raw": args.get("raw"),
                                      "max_bytes": args.get("max_bytes"),
                                      "no_render": args.get("no_render")})
    elif name == "do":
        body = app._verb_do({"task": args.get("task"),
                             "start_url": args.get("start_url"),
                             "max_steps": args.get("max_steps"),
                             "confirm": args.get("confirm"),
                             "cookies": args.get("cookies")})
    else:
        return {"ok": False, "error": "unknown tool: %s" % name}
    return body


# ── minimal streamable-HTTP transport ────────────────────────────────────────
# Hermes' native client opens one session: POST initialize, then tools/list and
# tools/call, each answered on its own POST. The GET endpoint exists because
# the Streamable HTTP spec expects an event stream; it carries keepalive pings
# only, since every reply goes back on the POST that asked.
class _McpState:
    def __init__(self):
        self.lock = threading.Lock()
        self.session = ""


STATE = _McpState()


def _rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle_rpc(msg):
    """One JSON-RPC message in; the reply (or None for a notification)."""
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        with STATE.lock:
            STATE.session = uuid.uuid4().hex
        return _rpc_result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "webaccess", "version": "1.0.0"},
        })
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        # The verb runs to completion here (bounded by handlers.DO_TIMEOUT for
        # do); _stream_reply keeps bytes flowing to the client while it runs.
        body = _dispatch(params.get("name") or "", params.get("arguments") or {})
        text = json.dumps(body, ensure_ascii=False, indent=2)
        return _rpc_result(rid, {"content": [{"type": "text", "text": text}],
                                 "isError": not bool(body.get("ok"))})
    if rid is None:
        return None                          # other notifications
    return _rpc_error(rid, -32601, "method not found: %s" % method)


class _McpHandler(BaseHTTPRequestHandler):
    server_version = "webaccess-mcp/1.0"
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _stream_reply(self, msg):
        """Answer tools/call over an SSE stream (spec-blessed for slow tools).

        A worker thread runs the verb; the stream writes an SSE comment ping
        every STREAM_PING_SECS so bytes keep flowing while it runs — that is
        the whole point: clients cut responses that go silent mid-flight, not
        ones that take long. The JSON-RPC reply rides the final `event:
        message` frame, then the chunked body terminates and the connection
        closes.
        """
        extra = {}
        with STATE.lock:
            if STATE.session:
                extra["Mcp-Session-Id"] = STATE.session
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()

        box, done = {}, threading.Event()

        def work():
            try:
                box["reply"] = _handle_rpc(msg)
            except Exception as exc:                       # never strand the stream
                box["reply"] = _rpc_error(msg.get("id"), -32603, str(exc))
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()

        def chunk(data):
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.wfile.write(("%X\r\n" % len(data)).encode("ascii") + data + b"\r\n")
            self.wfile.flush()

        try:
            while not done.wait(STREAM_PING_SECS):
                chunk(b": ping\n\n")
            reply = box.get("reply")
            if reply is not None:      # None = notification: nothing to deliver
                chunk("event: message\ndata: %s\n\n"
                      % json.dumps(reply, ensure_ascii=False))
            chunk(b"")                 # zero-length chunk terminates the body
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                       # client hung up; the verb thread runs on (by design)
        self.close_connection = True

    def do_GET(self):                                           # noqa: N802
        if self.path != "/mcp":
            self._send(404, {"error": "MCP endpoint is /mcp"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": webaccess mcp stream open\n\n")
            self.wfile.flush()
            while True:
                import time
                time.sleep(15)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_DELETE(self):                                        # noqa: N802
        # Session termination per the streamable-HTTP spec: the client sends
        # DELETE /mcp with the session id; 200 if known, 404 if not.
        if self.path != "/mcp":
            self._send(404, {"error": "MCP endpoint is /mcp"})
            return
        with STATE.lock:
            sid = STATE.session
        if self.headers.get("Mcp-Session-Id") and self.headers.get("Mcp-Session-Id") != sid:
            self._send(404, {"error": "unknown session"})
            return
        self._send(200, b"", extra=sid and {"Mcp-Session-Id": sid} or None)

    def do_POST(self):                                          # noqa: N802
        if self.path != "/mcp":
            self._send(404, {"error": "MCP endpoint is /mcp"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": "request body too large"})
            return
        try:
            msg = json.loads(self.rfile.read(length or 0).decode("utf-8", "replace") or "{}")
            if not isinstance(msg, dict):
                raise ValueError("body must be a JSON-RPC object")
        except ValueError:
            self._send(400, {"ok": False, "error": "bad JSON-RPC body"})
            return
        # tools/call answers as an SSE stream (spec-blessed for exactly this):
        # a verb can legitimately run minutes (do: the full agent run; fetch: the
        # escalation ladder), and clients cut POSTs that fall silent between
        # bytes (the streamable-HTTP httpx read timeout is 300s — witnessed
        # 2026-09-27: it killed a healthy 367s do call and its auto-retry
        # forked a second browser run). Pings keep bytes flowing; the JSON-RPC
        # reply rides the final `event: message` frame.
        if msg.get("method") == "tools/call":
            self._stream_reply(msg)
            return
        reply = _handle_rpc(msg)
        extra = {}
        with STATE.lock:
            if STATE.session:
                extra["Mcp-Session-Id"] = STATE.session
        if reply is None:
            self._send(202, b"", extra=extra)
            return
        self._send(200, reply, extra=extra)

    def log_message(self, format, *args):                       # noqa: A002
        pass


def make_server(port=MCP_PORT, host="0.0.0.0"):
    srv = ThreadingHTTPServer((host, port), _McpHandler)
    srv.daemon_threads = True
    return srv


def main():
    srv = make_server()
    print("webaccess MCP facade listening on 0.0.0.0:%d/mcp" % MCP_PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
