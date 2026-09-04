#!/usr/bin/env python3
"""The pipeline's fetcher — a thin binding to the ONE implementation, in the webaccess service.

The implementation moved into the Docker container on the docker host (.226) on 2026-08-23:
one handler core, two facades (HTTP and MCP). This pipeline is plain Python and calls
`fetch()` as a function, so it uses the HTTP facade — POST to the service, the same JSON
contract the CLI shim uses. The import contract is unchanged: the pipeline still does
`import rxfetch` and gets `fetch`, `fetch_text`, `looks_unusable`, `cache_path`,
`MIN_USABLE_CHARS`, `_metrics_enabled`, `_LOKI_URL` — the only thing that swapped is the
transport underneath.

A MISSING OR DOWN SERVICE FAILS ON USE, NOT ON IMPORT. `verify.py` imports this module at
load time and `rx_test.py` runs in CI where the service is not reachable, so nothing here
may raise at import. `available()` reports whether the service is reachable NOW; when it is
not, fetch calls raise at the moment they are made. There is no fallback to a local copy —
silently fetching with stale code is how the old two-copy divergence happened.

The client keeps NO cache of its own. The service owns the page cache (one shared cache,
every caller); a re-pointed or shadowed cache here would make every audit a miss and prove
nothing the cache did not already prove. `cache_path()` therefore names a client-side
shadow location that nothing writes to — it exists so the import contract holds, not so
the pipeline reads from it.
"""

import hashlib
import json
import os
import re
import urllib.request

# The service endpoint: same variable the CLI shim uses.
SERVICE_URL = (os.environ.get("WEBACCESS_URL") or "http://docker.putzolu.com:8910").rstrip("/")

# ── constants (same values as the implementation; callers compare at import) ─────────
MIN_USABLE_CHARS = 2000
MIN_DOCUMENT_CHARS = int(os.environ.get("ANALYSIS_MIN_DOCUMENT_CHARS") or 200)
SUBSTANTIAL_CHARS = 20_000
DEFAULT_HOST_INTERVAL = 0.4
ATTEMPTS = 3
SOURCES = os.path.expanduser("~/.hermes/cache/web-access/sources")
LOCKDIR = os.path.expanduser(
    os.environ.get("ANALYSIS_FETCH_LOCKDIR") or "~/.hermes/.fetchlocks")

# Where per-fetch events are pushed; the service writes its own copy server-side, and
# verify.py pushes its verdict events to the same Loki directly.
_LOKI_URL = os.environ.get("RX_LOKI_URL") or "http://docker.putzolu.com:3100/loki/api/v1/push"


def _metrics_enabled():
    return os.environ.get("RX_METRICS", "1") != "0"


def _service_down_error():
    return ("webaccess service is not reachable at %s — fetch is unavailable until it is "
            "up" % SERVICE_URL)


# ── the interstitial test, kept client-side ───────────────────────────────────────────
# verify.py and rx_test.py call looks_unusable on text they already have (fixture strings,
# fetched bodies): that is a text judgement, not a fetch, and it must not need the network.
# It is vendored here, deliberately identical to the implementation's algorithm — the one
# place the two copies of this logic may agree is by construction, and a divergence would
# silently change what the audit calls an interstitial.

BOT_WALL_STRONG_RE = re.compile(
    r"checking your browser|just a moment|verify you are human|are you a robot"
    r"|attention required|needs javascript to work|site requires javascript"
    r"|javascript is (not available|disabled|required)", re.I)
BOT_WALL_WEAK_RE = re.compile(
    r"enable javascript|captcha|access denied|cloudflare|403 forbidden", re.I)
STEALTH_BLOCK_RE = re.compile(r"\u26a0\s*blocked:", re.I)


def _reads_like_prose(text):
    return len(text.split()) >= 10 and bool(re.search(r"[.!?]\s", text))


def looks_unusable(text):
    """True when what came back is a wall / JS shell / near-empty response, not the document.

    Mirror of the service-side test: markers at any length; long text is the document;
    short text is kept only if it reads like prose.
    """
    s = (text or "").strip()
    if not s:
        return True
    if STEALTH_BLOCK_RE.search(s):
        return True
    if len(s) >= SUBSTANTIAL_CHARS:
        return False
    head = text[:4000]
    if BOT_WALL_STRONG_RE.search(head):
        return True
    if re.search(r"(?i)you need to enable javascript|please enable javascript to (run|view)|"
                 r"enable javascript to run this app|this app requires javascript", head):
        return True
    if len(s) >= MIN_DOCUMENT_CHARS:
        return len(s) < MIN_USABLE_CHARS and bool(BOT_WALL_WEAK_RE.search(head))
    return not _reads_like_prose(s) or bool(BOT_WALL_WEAK_RE.search(head))


# ── the result object (same surface as the implementation's Result) ───────────────────
class Result(object):
    __slots__ = ("text", "outcome", "detail", "via", "attempts")

    def __init__(self, text="", outcome="unreachable", detail="", via="", attempts=None):
        self.text, self.outcome, self.detail, self.via = text, outcome, detail, via
        self.attempts = list(attempts or [])

    @property
    def ok(self):
        return self.outcome == "ok"

    def __repr__(self):
        return "Result(%s, %d chars, %r)" % (self.outcome, len(self.text), self.detail[:60])


def _post(path, payload, timeout):
    req = urllib.request.Request(
        SERVICE_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8", "replace"))


def available():
    """Whether the service answers right now. No service means the fetch behaviour tests
    in rx_test.py skip rather than fail — an unreachable endpoint is not a fetch failure."""
    try:
        req = urllib.request.Request(SERVICE_URL + "/health")
        with urllib.request.urlopen(req, timeout=3) as fh:
            return 200 <= fh.status < 500
    except Exception:                                          # noqa: BLE001
        return False


def cache_path(url):
    """Client-side shadow location for a URL's cached text (see the module docstring:
    nothing here writes to it — the service owns the page cache)."""
    key = hashlib.sha1((url or "").encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".fetch-shadow", key + ".txt")


def configure(**kw):
    """No-op on this side of the wire: the service owns its state directories.
    Kept so the import contract (and any stray call) keeps working."""


def fetch(url, timeout=45, use_cache=True, allow_browser=False):
    """One fetch through the service. The transport budget must exceed the service's
    worst case for the same request (attempts x timeout plus backoff), so the client
    never times out a fetch the service is still honestly running.

    `allow_browser` maps to the service's `no_browser` flag: a pipeline fetch has
    always run with the browser tier off (the in-process default), and the contract is
    preserved — the service's own callers opt in explicitly.
    """
    payload = {"url": url, "timeout": timeout, "use_cache": bool(use_cache),
               "no_browser": not allow_browser}
    # The service's own budget for this request is bounded by its attempt/backoff
    # schedule; give it generous headroom plus the HTTP round trip.
    http_timeout = max(timeout * 4 + 60, 300)
    try:
        body = _post("/fetch", payload, http_timeout)
    except Exception as exc:                                   # noqa: BLE001
        return Result(text="", outcome="unreachable",
                      detail=_service_down_error() + " (%s: %s)"
                             % (type(exc).__name__, exc))
    # Map the service's answer onto the result surface. `ok` is DERIVED from outcome,
    # not taken from the JSON field: the two may diverge in a bug, and the pipeline's
    # only reads must agree with what the implementation would have said.
    outcome = body.get("outcome") or ("ok" if body.get("ok") else "unreachable")
    return Result(text=body.get("text") or "", outcome=outcome,
                  detail=body.get("detail") or "", via=body.get("via") or "",
                  attempts=body.get("attempts") or [])


def fetch_text(url, timeout=45):
    """Back-compatible shim: the text, or "" when it could not be obtained.
    Calls THIS module's fetch (module-global lookup), so a caller that patches
    rxfetch.fetch sees its patch here too."""
    return fetch(url, timeout=timeout).text
