#!/usr/bin/env python3
"""The webaccess service entrypoint: both facades in one process.

The HTTP API (:8910) and the MCP facade (:8911) serve the same handlers
in one PID, so the service has one lifecycle, one env, and one place to
look when it is down. Both are ThreadingHTTPServer: a `do` call blocks its
thread for up to the 30-minute agent budget, and the rest of the service
keeps answering.

State re-pointing: rxfetch's historical defaults are home-tree paths on the
dev machine. In the container every file the service writes lives under
WEBACCESS_HOME (a named volume) — the page cache, the per-host gates, the
learned browser policy, the search cache, traces. The import-time env vars
(rxfetch binds several at import, so they must be set BEFORE handlers is
imported) are defaulted here at the very top; configure() does the remaining
in-process re-pointing at boot.
"""

import os
import sys

_WEBACCESS_HOME = (os.environ.get("WEBACCESS_HOME")
                   or os.path.expanduser("~/.webaccess-home"))
os.makedirs(os.path.join(_WEBACCESS_HOME, "logs"), exist_ok=True)
# Import-time bindings in rxfetch — must exist before `import handlers`:
os.environ.setdefault("ANALYSIS_FETCH_LOCKDIR",
                      os.path.join(_WEBACCESS_HOME, "fetchlocks"))
os.environ.setdefault("RX_FETCH_EVENTS",
                      os.path.join(_WEBACCESS_HOME, "logs", "fetch-events.jsonl"))
# Call-time reads, set early for consistency (read by browse_task children):
os.environ.setdefault("BROWSE_LEARNED_POLICY",
                      os.path.join(_WEBACCESS_HOME, "learned.json"))
os.environ.setdefault("BROWSE_TASK_LOG",
                      os.path.join(_WEBACCESS_HOME, "logs", "browse-task.log"))
# The service owns its environment: the dev machine's profile .env
# discovery does not exist here. browserbase stays OFF unless the env
# says otherwise — the metered rung is not the default.
os.environ.setdefault("WEB_ALLOW_BROWSERBASE", "0")
os.environ.setdefault("BROWSE_NO_BROWSERBASE", "true")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threading                                           # noqa: E402
import handlers                                            # noqa: E402
import app                                                 # noqa: E402
import mcp_server                                          # noqa: E402


def _repoint_state():
    home = handlers._WEBACCESS_HOME
    os.makedirs(home, exist_ok=True)
    handlers.rxfetch.configure(
        sources_dir=os.path.join(home, "cache", "sources"),
        lock_dir=os.path.join(home, "fetchlocks"),
    )


def main():
    _repoint_state()
    http_srv = app.make_server()
    mcp_srv = mcp_server.make_server()
    threading.Thread(target=mcp_srv.serve_forever, daemon=True).start()
    print("webaccess service up: HTTP on :%d, MCP on :%d/mcp, state in %s"
          % (app.PORT, mcp_server.MCP_PORT, handlers._WEBACCESS_HOME), flush=True)
    try:
        http_srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
