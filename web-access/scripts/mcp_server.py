#!/usr/bin/env python3
"""The webaccess MCP facade — typed tools over the ONE handler core.

Hermes profiles reach the three verbs as typed MCP tools from their
config.yaml (HTTP transport, url: http://docker.putzolu.com:8911/mcp). This file
serves the /mcp endpoint: it speaks MCP over Streamable HTTP (one session,
list tools, post calls) and delegates every call to the HTTP verb handlers
in app.py, in the SAME process. There is no second implementation: the
facade and the HTTP API are two faces of handlers.py.

Stdlib only, same reasoning as app.py. No web framework in the image.

A `do` call blocks for up to the 30-minute agent budget. That is expected:
the MCP client holds the call open and the handler owns the child process.
Profiles registering this server should give it a matching per-call timeout
(timeout: 1950 in the mcp_servers entry).
"""

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app                                                 # noqa: E402

MCP_PORT = int(os.environ.get("WEBACCESS_MCP_PORT") or 8911)
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
                        "Read a found page with the fetch tool before drawing a conclusion "
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
        "name": "fetch",
        "description": ("Read one URL and return the document verbatim — text, with PDFs "
                        "handled. Escalation is this tool's job: it climbs cache, NCBI API, "
                        "plain HTTP, a self-hosted render, and a stealth browser, cheapest "
                        "first, and reports which layer produced the text in 'via'. The "
                        "outcome is 'ok', 'unreadable' (the server answered but withheld "
                        "the document — report that it could not be read; never state what "
                        "an unread page 'says'), or 'unreachable' (no usable response). "
                        "Never report an unread page as empty."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to read."},
                "max_chars": {"type": "integer", "minimum": 1,
                              "description": "Truncate text to this many characters. "
                                             "Default 20000."},
                "no_browser": {"type": "boolean",
                               "description": "Skip the browser render rungs; fail rather "
                                              "than spend the seconds. Default false."},
            },
            "required": ["url"],
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
                        "guess. A run can take several minutes; the service budgets 30."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task, in plain language."},
                "start_url": {"type": "string",
                              "description": "The site to start on. Name it whenever the "
                                             "task is about a particular site."},
                "max_steps": {"type": "integer", "minimum": 1,
                              "description": "Step budget. Default 25; raise for long "
                                             "flows."},
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
    elif name == "fetch":
        body = app._verb_fetch({"url": args.get("url"),
                                "max_chars": args.get("max_chars"),
                                "no_browser": args.get("no_browser")})
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
        # Synchronous on purpose: the POST waits out the verb (bounded by the
        # verb's own budget — 30 minutes for do) and carries the answer back.
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
