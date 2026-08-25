#!/usr/bin/env python3
"""browse_task.py — carry out a web task in a real browser and report the result.

Give it a plain-English task and it opens a browser, works through the pages to
completion, and returns one JSON object with what it found or did.

Read-only by default: without --confirm the agent is told to only read and
report — never to sign in, submit a form, buy, book, post, send, or change any
account or site state. Pass --confirm (only after the user approved that exact
task) when the task must ACT on a site.

Configuration is read from config.env beside this script (see templates/config.env.example).
"""

import argparse
import datetime
import glob
import json
import contextlib
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
# Config path; overridable via env for testing.
CONFIG = Path(os.environ.get("BROWSE_TASK_CONFIG", str(HERE / "config.env")))


# ONE identity for every layer. rxfetch sends this from urllib; the renders below send it from
# Playwright; fara sets its own in its environment. They must agree, because a site that sees
# Chrome/124 on the cheap tier and HeadlessChrome on the render is being told two different
# stories by one client — and on 2026-08-03 Lowe's answered the first with 393KB and the second
# with a 195-character "Access Denied".
UA = os.environ.get("RXFETCH_UA") or (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36")

# Playwright announces automation twice over: `HeadlessChrome` in the UA and navigator.webdriver
# set true. Both are trivially checked and both were being sent unmodified. Setting a real UA and
# clearing the flag is the difference between the browser rungs being useful and being refused
# on every bot-walled site.
STEALTH_INIT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def _default_log():
    """A per-day log file under the OS temp dir. Old days get auto-cleaned by the
    OS (e.g. systemd-tmpfiles ages /tmp) instead of one file growing forever."""
    d = Path(tempfile.gettempdir()) / "browse-task"
    try:
        d.mkdir(mode=0o700, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return d / f"browse-task-{datetime.date.today().isoformat()}.log"


# Every run appends the command and the FULL agent output here so failures are
# diagnosable. Overridable via config (BROWSE_LOG) or env (BROWSE_TASK_LOG); set
# one of those to a persistent path if you don't want the OS to age it out.
LOG = (Path(os.environ["BROWSE_TASK_LOG"]) if os.environ.get("BROWSE_TASK_LOG")
       else _default_log())


def trace(layer, event, **fields):
    """Append one execution record to RXFETCH_TRACE, if the caller asked for a trace.

    Same file as rxfetch's, so one fetch produces one ordered story across both processes:
    the curl-equivalent of each HTTP attempt, then the exact browser argv, then the agent's
    prompt and reply. Tracing must never break a render, hence the blanket except.
    """
    path = os.environ.get("RXFETCH_TRACE") or ""
    if not path:
        return
    try:
        rec = {"ts": datetime.datetime.now().strftime("%H:%M:%S"), "layer": layer,
               "event": event}
        rec.update(fields)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str)[:4000] + "\n")
    except Exception:  # noqa: BLE001
        pass



def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:  # noqa: BLE001 — logging must never break the tool
        pass

# Appended to the task in read-only mode. Best-effort instruction, not a browser
# sandbox — see README.
READONLY_DIRECTIVE = (
    " CONSTRAINT: This is a read-only lookup. You MAY search, set a location or ZIP "
    "for availability, and apply filters. You must NOT sign in, enter payment or "
    "personal contact details, purchase, book, post, send messages, or submit any "
    "order or account change. If the task would require any of those, stop and "
    "report what you found and what action would be needed."
)


def out(d, code=0):
    log("RESULT " + json.dumps(d, ensure_ascii=False)[:600])
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(code)


def fail(msg):
    out({"ok": False, "error": str(msg)}, 1)


def _read_env_file(path):
    d = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    except Exception:  # noqa: BLE001
        pass
    return d


def load_config():
    return _read_env_file(CONFIG)


_HERMES_ENV = None


def hermes_env():
    """Hermes' central secrets file (~/.hermes/.env) — a fallback for creds like
    BrowserBase. Path overridable via BROWSE_HERMES_ENV (for testing)."""
    global _HERMES_ENV
    if _HERMES_ENV is None:
        _HERMES_ENV = _read_env_file(os.environ.get("BROWSE_HERMES_ENV")
                                     or str(Path.home() / ".hermes" / ".env"))
    return _HERMES_ENV


def bb_cred(cfg, name):
    """A BrowserBase setting from config.env, then the process env, then
    ~/.hermes/.env."""
    return cfg.get(name) or os.environ.get(name) or hermes_env().get(name)


# Per-site browser policy: pick the lightest mode that actually loads a site.
# (hostname substring, mode) — first match wins. Verified empirically:
#   costco   — Akamai blocks headless AND headful past the homepage -> browserbase
#   amazon   — headless is 503-blocked on search; headful loads it -> headful
#   reddit   — loads fine headless
#   newegg   — loads fine headless
DEFAULT_SITE_POLICY = [
    ("costco.com", "browserbase"),
    ("amazon.", "headful"),
    ("reddit.com", "headless"),
    ("newegg.com", "headless"),
]


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def load_policy(cfg, xvfb):
    """Return (rules, default_mode). Rules from BROWSE_SITE_POLICY (a JSON file)
    are checked before the built-ins, so a user can override any site."""
    rules, default = [], None
    pf = cfg.get("BROWSE_SITE_POLICY")
    if pf and Path(pf).exists():
        try:
            data = json.loads(Path(pf).read_text())
            for r in data.get("rules", []):
                if r.get("match") and r.get("mode"):
                    rules.append((r["match"].lower(), r["mode"].lower()))
            default = (data.get("default") or "").lower() or None
        except Exception:  # noqa: BLE001
            pass
    rules += DEFAULT_SITE_POLICY
    return rules, (default or ("headful" if xvfb else "headless"))


def browserbase_disabled(args, cfg):
    """True when the managed remote browser is off limits for this run.

    `browserbase` is the only rung that leaves the machine and costs money, so it is worth being
    able to switch off independently of the free local ones — both to test the cheaper layers
    honestly, and to keep a metered account from being spent by an automatic escalation nobody
    watched. `--no-browserbase` beats config; config is BROWSE_NO_BROWSERBASE.
    """
    if getattr(args, "no_browserbase", False):
        return True
    v = (cfg.get("BROWSE_NO_BROWSERBASE")
         or os.environ.get("BROWSE_NO_BROWSERBASE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


# How long the agent rung may run. Kept BELOW rxfetch's BROWSER_TIMEOUT_FLOOR on purpose: if the
# parent's budget expires first the child is killed blind and its trace ends mid-run, which is
# how a 173-second cutoff came back as an unexplained "browser timed out".
AGENT_RUN_TIMEOUT = int(os.environ.get("BROWSE_AGENT_TIMEOUT") or 900)


def run_agent_dump(url, mode, xvfb, cfg, wait_ms, max_steps=12):
    """Layer 6 as a TEXT source: let the agent navigate, then take the landed page verbatim.

    The agent is used as a navigator, not a reader. It can dismiss a consent wall, clear an
    interstitial or click through to the real page — things a plain render cannot — and then
    FARA_DUMP_MARKDOWN makes it write the landed page's markdown before the browser closes.
    What comes back is the document, not the model's prose, so a citation audit that locates
    verbatim quotes still works. (The agent's own read action does the opposite: it extracts
    the markdown and returns only an answer.)
    """
    fara_home = cfg.get("FARA_HOME") or os.environ.get("FARA_HOME") or ""
    # Process env is the container's configuration path (config.env is a dev-machine
    # convention); file first, so a dev config still wins over a stray env var.
    base_url = cfg.get("BROWSE_BASE_URL") or os.environ.get("BROWSE_BASE_URL") or ""
    model = cfg.get("BROWSE_MODEL") or os.environ.get("BROWSE_MODEL") or ""
    cli = Path(fara_home) / ".venv" / "bin" / "fara-cli"
    if not (fara_home and base_url and model and cli.exists()):
        return {"error": "agent rung unavailable (browser agent not configured)"}
    with tempfile.TemporaryDirectory(prefix="agent_dump_") as tmp:
        md = Path(tmp) / "page.md"
        cmd = [str(cli), "--task",
               "Make THIS page's main content visible: dismiss any cookie, consent, location or "
               "sign-in prompt covering it, and reload if the site served an error page. Stay on "
               "this exact URL — if a reload or dismissal moves you elsewhere, navigate back to "
               "it before stopping. Do not open a different product, a search, or the homepage. "
               "Then stop." + READONLY_DIRECTIVE,
               "--start_page", url, "--output_folder", tmp,
               "--base_url", base_url,
               "--api_key", cfg.get("BROWSE_API_KEY") or os.environ.get("BROWSE_API_KEY") or "none",
               "--model", model, "--max_rounds", str(max_steps)]
        if mode == "browserbase":
            cmd.insert(1, "--browserbase")
        elif mode == "headful":
            cmd.insert(1, "--headful")
            if xvfb:
                cmd = [xvfb, "-a"] + cmd
        env = dict(os.environ, FARA_DUMP_MARKDOWN=str(md), RXFETCH_GATE_HELD=_host(url))
        log("AGENT CMD %s" % " ".join("<redacted>" if i and cmd[i - 1] == "--api_key" else a
                                      for i, a in enumerate(cmd)))
        trace("agent:%s" % mode, "spawn", url=url, model=model, base_url=base_url,
              max_rounds=max_steps, prompt=cmd[cmd.index("--task") + 1],
              argv=["<redacted>" if i and cmd[i - 1] == "--api_key" else a
                    for i, a in enumerate(cmd)])
        try:
            with host_gate(url):
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, env=env, timeout=AGENT_RUN_TIMEOUT,
                                      start_new_session=True)
        except subprocess.TimeoutExpired:
            trace("agent:%s" % mode, "timeout", seconds=AGENT_RUN_TIMEOUT)
            return {"error": "agent rung timed out"}
        except Exception as exc:                               # noqa: BLE001
            trace("agent:%s" % mode, "spawn-failed", exc=type(exc).__name__)
            return {"error": "agent rung failed: %s" % type(exc).__name__}
        # The agent narrates its rounds on stdout; keep the whole thing in the log and the tail
        # in the trace, so "what did fara actually do" is answerable after the fact.
        trace_rounds(tmp)
        log("AGENT STDOUT:\n" + (proc.stdout or ""))
        if proc.stderr:
            log("AGENT STDERR:\n" + proc.stderr[-2000:])
        trace("agent:%s" % mode, "exit", code=proc.returncode,
              reply_tail=(proc.stdout or "")[-1500:], stderr_tail=(proc.stderr or "")[-500:],
              markdown_written=md.exists(),
              markdown_chars=(md.stat().st_size if md.exists() else 0))
        if not md.exists():
            return {"error": "agent produced no page markdown (browser never loaded a page)"}

        # WHERE it landed decides whether this is the document at all. The agent reports
        # success for its own task, which is not the same as "still on the URL you asked for":
        # Home Depot's error page, one refresh, and fara was on the homepage calling it done.
        # A long, healthy-looking dump of site navigation is exactly the false positive
        # looks_unusable cannot catch, so compare the paths and refuse a mismatch.
        landed_file = Path(str(md) + ".url")
        landed = landed_file.read_text(encoding="utf-8").strip() if landed_file.exists() else ""
        want, got = urlparse(url), urlparse(landed or url)
        drifted = bool(landed) and (want.netloc.lower().lstrip("www.")
                                    != got.netloc.lower().lstrip("www.")
                                    or want.path.rstrip("/") != got.path.rstrip("/"))
        trace("agent", "landed", requested=url, landed=landed, drifted=drifted)
        if drifted:
            return {"error": "agent navigated away from the requested page (landed on %s); "
                             "its dump is a different document" % (landed[:120] or "?")}
        return {"text": md.read_text(encoding="utf-8", errors="ignore"), "status": 0,
                "title": "", "landed": landed}


# Rung order, cheapest first. The agent rung runs after all of these; browserbase is last of
# the renders because it is the only one that costs money.
RUNG_ORDER = ["headless", "headful", "browserbase"]


def dump_ladder_modes(args, cfg, start_url, xvfb):
    """The rungs `fetch` will climb for this URL, cheapest first.

    `fetch` climbs. It used to resolve ONE mode from the site policy and run it once, so Home
    Depot picked headful by default, returned a 155-character shell and stopped, while the agent
    path would have probed and escalated. A tier that does not climb is not a tier.

    Prior experience is allowed to SKIP the rungs below it, not to replace the ladder: if this
    host is known to need headful, start at headful and keep browserbase above it in reserve.
    Retrying a rung already known to fail for this site is pure latency.

    --all-layers throws that knowledge away and climbs from the bottom. It exists because the
    learned cache is only as good as the code that wrote it: entries recorded while a rung was
    broken say "this rung failed" when the truth was "we had a bug". www.bestbuy.com was learned
    as `browserbase` by a probe whose browserbase attempt then died on a plan error — a winner
    that never won. Use it after fixing anything in the browser path.
    """
    override = (getattr(args, "mode", None) or cfg.get("BROWSE_MODE") or "").strip().lower()
    if override in RUNG_ORDER:
        return [override], "override"

    rungs = [m for m in RUNG_ORDER
             if (m != "headful" or xvfb)
             and (m != "browserbase" or (not browserbase_disabled(args, cfg)
                                         and bb_cred(cfg, "BROWSERBASE_API_KEY")
                                         and bb_cred(cfg, "BROWSERBASE_PROJECT_ID")))]

    if getattr(args, "all_layers", False):
        return rungs, "all-layers"

    preferred, why = resolve_mode(args, cfg, start_url, xvfb)
    if preferred in rungs and why != "default":
        # Known to need this rung: start there, keep the dearer ones above it as fallbacks.
        return rungs[rungs.index(preferred):], why
    return rungs, why


def resolve_mode(args, cfg, start_url, xvfb):
    """Pick browser mode (headless|headful|browserbase) and why, for this site."""
    no_bb = browserbase_disabled(args, cfg)

    def _demote(mode, why):
        """browserbase is off: fall back to the best local mode instead."""
        if mode == "browserbase" and no_bb:
            local = "headful" if xvfb else "headless"
            log(f"browserbase disabled; {why} wanted browserbase, using {local}")
            return local, why + "+no-browserbase"
        return mode, why

    override = (getattr(args, "mode", None) or cfg.get("BROWSE_MODE") or "").strip().lower()
    if override in ("headless", "headful", "browserbase"):
        return _demote(override, "override")
    legacy = (cfg.get("BROWSE_HEADFUL") or os.environ.get("BROWSE_HEADFUL") or "").strip().lower()
    if legacy in ("false", "0", "no"):
        return "headless", "BROWSE_HEADFUL"
    if legacy in ("true", "1", "yes"):
        return "headful", "BROWSE_HEADFUL"
    rules, default = load_policy(cfg, xvfb)
    host = _host(start_url)
    for pat, mode in rules:
        if pat and pat in host:
            return _demote(mode, f"policy:{pat}")
    for pat, mode in load_learned(cfg):   # previously auto-detected sites
        if pat and pat in host:
            return _demote(mode, "learned")
    return _demote(default, "default")


# A tiny loadability probe: open the start URL and report OK / BLOCKED. Run via
# the Fara venv's python (it has Playwright); headful is wrapped in xvfb-run.
PROBE_SRC = r"""
import sys, re, asyncio
import os as _os
_UA = _os.environ.get("RXFETCH_UA") or ""
_STEALTH = _os.environ.get("RXFETCH_STEALTH") or ""
from playwright.async_api import async_playwright
_B = re.compile(r"access denied|robot check|are you a robot|unusual traffic|"
                r"enter the characters|captcha|verify you are human|"
                r"pardon our interruption|not a robot|something went wrong", re.I)
_url = sys.argv[1]
_headful = len(sys.argv) > 2 and sys.argv[2] == "headful"
async def _main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=not _headful)
        try:
            _ctx = await b.new_context(user_agent=_UA, locale="en-US",
                                       viewport={"width": 1440, "height": 900})
            await _ctx.add_init_script(_STEALTH)
            pg = await _ctx.new_page()
            r = await pg.goto(_url, wait_until="domcontentloaded", timeout=25000)
            await pg.wait_for_timeout(3000)
            body = (await pg.inner_text("body"))[:4000]
            blocked = (r is not None and r.status in (403, 503, 429)) \
                or bool(_B.search(body)) or bool(_B.search(await pg.title()))
            print("BLOCKED" if blocked else "OK")
        except Exception as e:
            print("ERR " + str(e).splitlines()[0][:80])
        finally:
            await b.close()
asyncio.run(_main())
"""


# Raw text dump: navigate, let the page render, return inner_text. Deliberately NO agent in
# the loop — the moment a model summarises, the text stops being verbatim, and the citation
# audit downstream works by locating an exact quote inside fetched text. A paraphrase breaks
# that silently, which is the worst way for it to break.
DUMP_SRC = r"""
import os, sys, asyncio, json
import os as _os
_UA = _os.environ.get("RXFETCH_UA") or ""
_STEALTH = _os.environ.get("RXFETCH_STEALTH") or ""
from playwright.async_api import async_playwright
_url = sys.argv[1]
_mode = sys.argv[2] if len(sys.argv) > 2 else "headless"
_headful = _mode == "headful"
_wait = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

def _envflag(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")

async def _launch(p):
    # A browser, local or managed. Returns (browser, session_id_or_None).
    if _mode != "browserbase":
        return await p.chromium.launch(headless=not _headful), None
    from browserbase import Browserbase
    bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])
    settings = {"enablePdfViewer": True}
    if _envflag("BROWSERBASE_ADVANCED_STEALTH", False):
        settings["advanced_stealth"] = True
    sess = bb.sessions.create(
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
        proxies=_envflag("BROWSERBASE_PROXIES", True),
        browser_settings=settings, keep_alive=False, timeout=600)
    return await p.chromium.connect_over_cdp(sess.connect_url), sess.id

async def _main():
    async with async_playwright() as p:
        b, _sid = await _launch(p)
        try:
            _ctx = await b.new_context(user_agent=_UA, locale="en-US",
                                       viewport={"width": 1440, "height": 900})
            await _ctx.add_init_script(_STEALTH)
            pg = await _ctx.new_page()
            r = await pg.goto(_url, wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(_wait)
            body = await pg.inner_text("body")
            print("__DUMP__" + json.dumps({
                "status": (r.status if r is not None else 0),
                "title": await pg.title(),
                "mode": _mode, "session": _sid,
                "text": body}))
        except Exception as e:
            print("__DUMP__" + json.dumps({"error": str(e).splitlines()[0][:200]}))
        finally:
            await b.close()
asyncio.run(_main())
"""


# Throttling lives in the web-access skill's fetcher, so that EVERY route to a site - a plain
# fetch, an escalation to a browser, or this script run on its own - is spaced by one shared
# per-host timer. Without this, driving a browser directly was the one escalation level that
# ignored the rate limit entirely.
@contextlib.contextmanager
def host_gate(url):
    """Hold the shared per-host gate for `url`, if the shared fetcher is installed."""
    impl = str(HERE / "rxfetch.py")
    try:
        # Loaded by path, not imported as a package: it is a file beside this one, not a
        # dependency on PyPI, and sys.path stays untouched. Resolved relative to __file__ so a
        # relocated skill directory still finds it — this used to be an absolute path into
        # ~/hermes-skills, which only worked where the repo happened to be checked out.
        spec = importlib.util.spec_from_file_location("rxfetch_gate", impl)
        rxfetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rxfetch)
    except Exception:                                          # noqa: BLE001
        yield                                                  # not installed: do not block work
        return
    host = rxfetch._host_of(url)
    # Our caller may already hold this host's gate (rxfetch spawns us for its browser tier).
    # flock is per-process, so taking it again would block on our own parent until the timeout.
    if os.environ.get("RXFETCH_GATE_HELD") == host:
        yield
        return
    with rxfetch.host_gate(host):
        yield


# HTTP statuses that mean the server answered and withheld the page. A rung that returns one of
# these has not got the document, however many characters of refusal it printed.
BLOCKED_STATUS = {401, 403, 429, 503}


def _rxfetch_looks_unusable(text):
    """rxfetch's interstitial test, if it is installed. Reused rather than reimplemented: two
    copies of "is this a document?" is exactly how the two halves come to disagree."""
    impl = str(HERE / "rxfetch.py")
    try:
        spec = importlib.util.spec_from_file_location("rxfetch_judge", impl)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return bool(mod.looks_unusable(text))
    except Exception:  # noqa: BLE001
        return False


def rung_failure(d, min_chars):
    """Why this rung did not get the document, or None if it did.

    Length alone is not the test. lowes.com answers a blocked product page with a 403 and a
    240-character 'Access Denied — You don't have permission to access...' body; that cleared a
    200-character floor, so the ladder declared success on a bot wall and never tried headful or
    the agent. The caller then rejected the same text as an interstitial. The ladder has to
    apply the caller's judgement, not a weaker one, or it stops climbing exactly when climbing
    is the point.
    """
    if d.get("error"):
        return d["error"]
    status = d.get("status")
    if status in BLOCKED_STATUS:
        return "HTTP %s (%s)" % (status, (d.get("title") or "no title")[:40])
    text = (d.get("text") or "").strip()
    if len(text) < min_chars:
        return "%d chars, under the %d floor" % (len(text), min_chars)
    if _rxfetch_looks_unusable(text):
        return "%d chars, but an interstitial (%s)" % (len(text), (d.get("title") or "")[:40])
    return None


def run_dump(url, mode, xvfb, fara_python, wait_ms, cfg=None):
    """Rendered page text via the browser, with no agent. Returns a dict."""
    # Pass the mode THROUGH. This used to read `"headful" if mode == "headful" else "headless"`,
    # which silently turned a browserbase policy into a headless render — costco.com resolves to
    # browserbase, so fetch quietly rendered it in the one mode the site is known to block and
    # returned a shell. A downgrade that says nothing is worse than a failure that does.
    base = [str(fara_python), "-c", DUMP_SRC, url, mode, str(wait_ms)]
    cmd = ([xvfb, "-a"] + base) if (mode == "headful" and xvfb) else base
    env = dict(os.environ, RXFETCH_UA=UA, RXFETCH_STEALTH=STEALTH_INIT)
    if mode == "browserbase":
        for k in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID",
                  "BROWSERBASE_PROXIES", "BROWSERBASE_ADVANCED_STEALTH"):
            v = bb_cred(cfg, k)
            if v:
                env[k] = v
        if not (env.get("BROWSERBASE_API_KEY") and env.get("BROWSERBASE_PROJECT_ID")):
            return {"error": "browserbase mode needs BROWSERBASE_API_KEY and "
                             "BROWSERBASE_PROJECT_ID (see README)"}
    log("DUMP CMD[%s] %s" % (mode, " ".join(cmd)))
    trace("browser:%s" % mode, "spawn", argv=cmd, url=url, wait_ms=wait_ms)
    try:
        with host_gate(url):
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, env=env, timeout=180)
    except Exception as exc:  # noqa: BLE001
        trace("browser:%s" % mode, "spawn-failed", exc=type(exc).__name__, detail=str(exc)[:300])
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    trace("browser:%s" % mode, "exit", code=r.returncode,
          stderr_tail=(r.stderr or "")[-500:], stdout_bytes=len(r.stdout or ""))
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("__DUMP__"):
            try:
                d = json.loads(ln[len("__DUMP__"):])
                trace("browser:%s" % mode, "rendered", http_status=d.get("status"),
                      title=(d.get("title") or "")[:120], chars=len(d.get("text") or ""),
                      session=d.get("session"), error=d.get("error"),
                      text_head=(d.get("text") or "")[:300])
                return d
            except Exception:  # noqa: BLE001
                break
    return {"error": (r.stderr or r.stdout or "no output").strip().splitlines()[-1][:200]
            if (r.stderr or r.stdout) else "no output"}


def _learned_path(cfg):
    return (cfg.get("BROWSE_LEARNED_POLICY") or os.environ.get("BROWSE_LEARNED_POLICY")
            or str(Path.home() / ".config" / "browse-task" / "learned.json"))


def load_learned(cfg):
    p = Path(_learned_path(cfg))
    if p.exists():
        try:
            return [(k.lower(), v.lower()) for k, v in json.loads(p.read_text()).items()]
        except Exception:  # noqa: BLE001
            return []
    return []


def save_learned(cfg, host, mode):
    if not host:
        return
    p = Path(_learned_path(cfg))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text()) if p.exists() else {}
        data[host] = mode
        p.write_text(json.dumps(data, indent=2))
    except Exception:  # noqa: BLE001
        pass


def run_probe(url, mode, xvfb, fara_python):
    hook = os.environ.get("BROWSE_PROBE_MAP")  # test hook
    if hook:
        try:
            return json.loads(hook).get(mode, "BLOCKED")
        except Exception:  # noqa: BLE001
            return "BLOCKED"
    base = [str(fara_python), "-c", PROBE_SRC, url] + (["headful"] if mode == "headful" else [])
    cmd = ([xvfb, "-a"] + base) if (mode == "headful" and xvfb) else base
    try:
        # Through the host gate like every other request. A probe is a page load, and firing
        # two of them at a site inside six seconds is exactly the traffic shape that earns the
        # block the probe then goes on to measure.
        with host_gate(url):
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env=dict(os.environ, RXFETCH_UA=UA, RXFETCH_STEALTH=STEALTH_INIT),
                               timeout=70)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        verdict = lines[-1].strip() if lines else "ERR"
        return verdict if verdict in ("OK", "BLOCKED") else "ERR"
    except Exception:  # noqa: BLE001
        return "ERR"


# A refused probe leaves a site warier of the next one. Space the rungs so the cheap attempt does
# not manufacture the block the dear one then measures: on 2026-08-03 headless was refused by
# lowes.com at 20:15:53 and headful was refused six seconds later, yet the same headful probe run
# on its own minutes afterwards came back OK.
PROBE_COOLDOWN = float(os.environ.get("BROWSE_PROBE_COOLDOWN") or 5)


def probe_ladder(url, xvfb, has_bb, fara_python):
    """Try headless, then headful, then browserbase. Returns (mode, proven).

    `proven` is True ONLY when a probe actually loaded the page in that mode. It is False when
    every rung failed and we are returning a guess, and False when a rung failed for reasons that
    are ours rather than the site's.

    That distinction is the whole point. `ERR` — no xvfb, a launch failure, a timeout — says
    nothing about the site, but it used to be indistinguishable from BLOCKED, so an infrastructure
    problem got written into the learned cache as though it were the site's behaviour. And when
    everything was blocked the fallback was saved too, which is how www.bestbuy.com came to be
    remembered as `browserbase` on the strength of a browserbase attempt that never ran.

    `has_bb` is False when browserbase is unconfigured OR switched off, so a disabled managed
    browser simply ends the ladder at the best local mode.
    """
    rungs = [m for m in ("headless", "headful") if m != "headful" or xvfb]
    errors = []
    for i, m in enumerate(rungs):
        if i:
            time.sleep(PROBE_COOLDOWN)
        res = run_probe(url, m, xvfb, fara_python)
        log(f"probe {m}: {res}")
        if res == "OK":
            return m, True
        if res == "ERR":
            errors.append(m)
    if errors:
        log(f"probe: {', '.join(errors)} failed for our own reasons, not the site's — "
            f"not recording this as site knowledge")
    fallback = "browserbase" if has_bb else ("headful" if xvfb else "headless")
    return fallback, False


def trace_rounds(output_dir, keep_dir=None):
    """Describe every page fara was shown, and what it decided to do about it.

    The trajectory holds a pre/post screenshot per round and an events log carrying the model's
    own reasoning. Both were being deleted with the temp dir, so "what did the agent actually
    see" was unanswerable after the fact — which is how a run that wandered onto a homepage and
    declared success went unnoticed. With a trace enabled this emits one record per round (the
    action, the coordinates, the model's stated reasoning, the screenshot path) and optionally
    keeps the images.
    """
    if not os.environ.get("RXFETCH_TRACE"):
        return
    evs = sorted(glob.glob(os.path.join(output_dir, "**", "solver_log", "events.jsonl"),
                           recursive=True), key=os.path.getmtime)
    if not evs:
        trace("agent", "rounds", note="no events.jsonl in the trajectory")
        return
    run_dir = Path(evs[-1]).parent.parent
    rounds = 0
    for ln in Path(evs[-1]).read_text(encoding="utf-8", errors="ignore").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("type") != "action":
            continue
        # The event schema is action_name + action_nl_description + llm_conversation, NOT a
        # nested {"action": {...}} — assuming the latter produced a trace of 13 empty rounds.
        think = ""
        for m in ((r.get("llm_conversation") or {}).get("messages") or []):
            think = m.get("reasoning") or m.get("raw_response") or ""
            if think:
                break
        think = re.sub(r"</?think>", "", think)
        think = re.sub(r"<tool_call>.*", "", think, flags=re.S).strip()
        shot = run_dir / ("screenshot_%d_pre.png" % rounds)
        trace("agent", "round", n=rounds, action=r.get("action_name"),
              did=(r.get("action_nl_description") or "")[:200],
              # the model's own account of what it saw, which IS the page description
              saw=think[:700],
              screenshot=str(shot) if shot.exists() else None)
        rounds += 1
    if keep_dir:
        try:
            dest = Path(keep_dir)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(run_dir, dest)
            trace("agent", "trajectory-kept", path=str(dest), rounds=rounds)
        except Exception as exc:                               # noqa: BLE001
            trace("agent", "trajectory-keep-failed", exc=type(exc).__name__)
    trace("agent", "rounds", total=rounds)


def read_result(output_dir, stdout):
    """Return (status, answer, steps) from the trajectory, falling back to stdout."""
    status = answer = None
    steps = None
    files = sorted(
        glob.glob(os.path.join(output_dir, "**", "data_point.json"), recursive=True),
        key=os.path.getmtime,
    )
    if files:
        dp = Path(files[-1])
        try:
            # status + final answer live under solver_log (SolverLog / Outcome).
            sl = json.loads(dp.read_text()).get("solver_log") or {}
            status = sl.get("status")
            outcome = sl.get("outcome") or {}
            if isinstance(outcome, dict):
                answer = outcome.get("answer")
        except Exception:  # noqa: BLE001
            pass
        try:  # steps = count of action events in the sibling log (best-effort)
            ev = dp.parent / "solver_log" / "events.jsonl"
            if ev.exists():
                steps = sum(1 for ln in ev.read_text().splitlines()
                            if '"type": "action"' in ln or '"type":"action"' in ln)
        except Exception:  # noqa: BLE001
            pass
    if not answer:
        for marker, st in (("Final Answer:", "complete"), ("Fara asks:", "waiting_for_user")):
            i = stdout.rfind(marker)
            if i != -1:
                tail = stdout[i + len(marker):].strip().splitlines()
                answer = tail[0].strip() if tail else ""
                status = status or st
                break
    return status, answer, steps


def main():
    p = argparse.ArgumentParser(prog="browse_task")
    p.add_argument("--task", help="plain-English web task to carry out "
                                  "(not needed with --dump-text)")
    p.add_argument("--dump-text", dest="dump_text", action="store_true",
                   help="return the page's RENDERED TEXT verbatim, with no agent in the loop. "
                        "For a caller that wants the document, not an answer.")
    p.add_argument("--keep-trajectory", dest="keep_trajectory", default=None,
                   help="copy the agent's trajectory (per-round screenshots and its own "
                        "reasoning) to this directory instead of discarding it with the temp dir")
    p.add_argument("--all-layers", dest="all_layers", action="store_true",
                   help="ignore what was learned about this site and climb from the cheapest "
                        "rung. Use after fixing anything in the browser path, since a learned "
                        "entry can record a bug as if it were the site's behaviour")
    p.add_argument("--no-agent", dest="no_agent", action="store_true",
                   help="stop the dump ladder at the plain renders; do not escalate to the "
                        "model-driven agent rung")
    p.add_argument("--min-chars", dest="min_chars", type=int, default=200,
                   help="a dump shorter than this counts as blocked, and the ladder climbs to "
                        "the next browser mode")
    p.add_argument("--wait-ms", dest="wait_ms", type=int, default=3000,
                   help="how long to let the page settle before reading it")
    p.add_argument("--start-url", dest="start_url", default="https://www.bing.com/",
                   help="page to open first (default: a search engine)")
    p.add_argument("--max-steps", dest="max_steps", type=int, default=25,
                   help="cap on browser actions before giving up (default 25)")
    p.add_argument("--confirm", action="store_true",
                   help="allow the agent to ACT (sign in, submit, buy, book, post, "
                        "send). Required for any state-changing task and only after "
                        "the user approved this exact task. Omit for read-only lookups.")
    p.add_argument("--cookies", default=None,
                   help="path to a JSON list of browser cookies to pre-seed before "
                        "the agent starts (e.g. a site's delivery location or login) "
                        "so it need not click through that setup. Overrides "
                        "BROWSE_COOKIES from config.")
    p.add_argument("--no-browserbase", dest="no_browserbase", action="store_true",
                   help="stay on the free local browser modes. browserbase is a paid remote "
                        "service; this keeps a run from escalating onto it. Config equivalent: "
                        "BROWSE_NO_BROWSERBASE=true")
    p.add_argument("--mode", choices=["auto", "headless", "headful", "browserbase"],
                   default="auto",
                   help="browser mode; 'auto' (default) picks the optimal one per "
                        "site (see README's site policy).")
    args = p.parse_args()

    cfg = load_config()
    global LOG
    if cfg.get("BROWSE_LOG"):
        LOG = Path(cfg["BROWSE_LOG"])
    if not args.dump_text and not args.task:
        fail("--task is required (or use --dump-text to get the page's text verbatim)")

    fara_home = cfg.get("FARA_HOME") or ""

    # --dump-text needs a browser but NOT the agent, so it runs before the model config is
    # required and never loads fara-cli. It reuses the per-host mode ladder, which is the part
    # worth reusing: this skill already knows which sites need headful or browserbase.
    if args.dump_text:
        fara_python = Path(fara_home) / ".venv" / "bin" / "python"
        if not fara_python.exists():
            fail("browser not installed at %s — run the setup in README." % fara_python)
        xvfb0 = shutil.which("xvfb-run")
        modes, why0 = dump_ladder_modes(args, cfg, args.start_url, xvfb0)
        log("DUMP " + json.dumps({"url": args.start_url, "ladder": modes, "start_why": why0}))
        attempts, d, used = [], {}, None
        for m in modes:
            d = run_dump(args.start_url, m, xvfb0, fara_python, args.wait_ms, cfg)
            text = d.get("text") or ""
            why_not = rung_failure(d, args.min_chars)
            note = why_not or ("%d chars" % len(text))
            attempts.append({"mode": m, "result": note[:120]})
            log("DUMP %s -> %s" % (m, note[:160]))
            if why_not is None:
                used = m
                break
        if used is None and not args.no_agent:
            # Layer 6: the same free local browser, but driven. Tried after every plain render
            # and before anything paid.
            amode = "headful" if xvfb0 else "headless"
            log("DUMP escalating to the agent rung (%s)" % amode)
            ad = run_agent_dump(args.start_url, amode, xvfb0, cfg, args.wait_ms)
            atext = ad.get("text") or ""
            a_why_not = rung_failure(ad, args.min_chars)
            attempts.append({"mode": "agent:%s" % amode,
                             "result": (a_why_not or "%d chars" % len(atext))[:120]})
            if a_why_not is None:
                d, used = ad, "agent:%s" % amode
        if used is None:
            out({"ok": False, "url": args.start_url, "mode": modes[-1] if modes else None,
                 "attempts": attempts,
                 "error": (d.get("error") or "every browser mode returned less than %d chars"
                                             % args.min_chars)}, 1)
        # Remember what worked, so the next fetch of this site starts there. The ladder still
        # runs in full if that mode later stops working.
        if not used.startswith("agent:"):
            save_learned(cfg, _host(args.start_url), used)
        text = d.get("text") or ""
        out({"ok": True, "url": args.start_url, "mode": used, "attempts": attempts,
             "http_status": d.get("status"), "title": d.get("title") or "",
             "chars": len(text), "text": text})
        return

    log("START " + json.dumps({"task": args.task, "acted": bool(args.confirm),
                               "start_url": args.start_url, "max_steps": args.max_steps}))
    base_url = cfg.get("BROWSE_BASE_URL") or os.environ.get("BROWSE_BASE_URL") or ""
    model = cfg.get("BROWSE_MODEL") or os.environ.get("BROWSE_MODEL") or ""
    api_key = cfg.get("BROWSE_API_KEY") or os.environ.get("BROWSE_API_KEY") or "none"
    if not (fara_home and base_url and model):
        fail("the browser agent is not configured. Copy templates/config.env.example to "
             "scripts/config.env and set FARA_HOME, BROWSE_BASE_URL, and BROWSE_MODEL "
             "(see README).")
    cli = Path(fara_home) / ".venv" / "bin" / "fara-cli"
    if not cli.exists():
        fail(f"browser agent not installed at {cli} — run the setup in README.")

    task = args.task.strip()
    if not args.confirm:
        task = task + READONLY_DIRECTIVE

    # Pick the browser mode per-site (headless is lightest; headful-under-xvfb
    # loads sites that reject headless; browserbase is a managed browser for
    # bot-hardened sites). See resolve_mode / DEFAULT_SITE_POLICY.
    xvfb = shutil.which("xvfb-run")
    mode, why = resolve_mode(args, cfg, args.start_url, xvfb)
    # Unknown site (no override / policy / learned rule): probe headless ->
    # headful -> browserbase to auto-detect what it needs, then remember it.
    autoprobe = (cfg.get("BROWSE_AUTOPROBE") or "true").strip().lower() not in ("false", "0", "no")
    if why == "default" and autoprobe:
        fara_python = Path(fara_home) / ".venv" / "bin" / "python"
        if fara_python.exists():
            has_bb = bool(bb_cred(cfg, "BROWSERBASE_API_KEY")
                          and bb_cred(cfg, "BROWSERBASE_PROJECT_ID")
                          and not browserbase_disabled(args, cfg))
            log(f"unknown site {_host(args.start_url)} — probing browser modes")
            mode, proven = probe_ladder(args.start_url, xvfb, has_bb, fara_python)
            if proven:
                save_learned(cfg, _host(args.start_url), mode)
                why = "probed"
            else:
                why = "probe-inconclusive"
    if mode == "headful" and not xvfb:
        log("WARN: headful needs xvfb-run (not found); falling back to headless")
        mode = "headless"
    log(f"browser mode={mode} ({why})")

    # Pre-seed cookies (delivery location, login, consent) so the agent doesn't
    # have to click through that setup. Cookies are domain-scoped, so a Costco
    # location file has no effect on other sites.
    cookies_file = args.cookies or cfg.get("BROWSE_COOKIES") or ""
    env = dict(os.environ)
    if cookies_file:
        if Path(cookies_file).exists():
            env["FARA_INIT_COOKIES"] = cookies_file
            log(f"pre-seed cookies: {cookies_file}")
        else:
            log(f"WARN: cookies file not found, ignoring: {cookies_file}")

    with tempfile.TemporaryDirectory(prefix="browse_task_") as tmp:
        fara = [str(cli), "--task", task, "--start_page", args.start_url,
                "--output_folder", tmp, "--base_url", base_url,
                "--api_key", api_key, "--model", model,
                "--max_rounds", str(args.max_steps)]
        if mode == "browserbase":
            bb_key = bb_cred(cfg, "BROWSERBASE_API_KEY")
            bb_proj = bb_cred(cfg, "BROWSERBASE_PROJECT_ID")
            if not (bb_key and bb_proj):
                miss = "BROWSERBASE_PROJECT_ID" if bb_key else "BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID"
                fail(f"this site needs BrowserBase (a managed browser that gets past "
                     f"heavy bot protection), but it isn't fully configured — {miss} "
                     f"missing. Set it in config or ~/.hermes/.env (see README).")
            # pass all BrowserBase settings through (key, project, proxies, stealth)
            for src in (hermes_env(), os.environ):
                for k, v in src.items():
                    if k.startswith("BROWSERBASE_") and v:
                        env[k] = v
            env["BROWSERBASE_API_KEY"] = bb_key
            env["BROWSERBASE_PROJECT_ID"] = bb_proj
            fara.insert(1, "--browserbase")
            cmd = fara
        elif mode == "headful":
            fara.insert(1, "--headful")
            cmd = [xvfb, "-a"] + fara
        else:  # headless
            cmd = fara
        redacted, skip = [], False
        for a in cmd:
            redacted.append("<redacted>" if skip else a)
            skip = (a == "--api_key")
        log("CMD " + " ".join(redacted))
        trace("do:%s" % mode, "spawn", url=args.start_url, model=model, base_url=base_url,
              max_rounds=args.max_steps, acted=bool(args.confirm), mode_why=why,
              prompt=task, argv=redacted, cookies=cookies_file or None)
        # Take the host's gate and release it immediately, rather than holding it for the whole
        # session. The gate means "one request in flight, spaced by an interval"; an agent
        # session is minutes of many page loads, so holding it would stall every other caller
        # for the duration. Ticking it here still spaces one session's START from the last
        # request to that site, which is the part a rate limit actually counts.
        with host_gate(args.start_url):
            pass
        try:
            # Own session so a timeout can kill the WHOLE tree (xvfb-run -> Xvfb,
            # fara-cli -> chromium), not just the direct child. /dev/null stdin so
            # the agent's post-task interactive prompt gets EOF instead of blocking.
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True, env=env)
        except Exception as e:  # noqa: BLE001
            log(f"fara-cli launch error: {e}")
            fail(f"could not start the browser agent: {e}")

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=1800)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""
            log("fara-cli TIMEOUT after 1800s; killed process group")

        log(f"fara-cli exit={proc.returncode}")
        if stdout:
            log("STDOUT:\n" + stdout)
        if stderr:
            log("STDERR:\n" + stderr)
        trace("do:%s" % mode, "exit", code=proc.returncode, timed_out=timed_out,
              stdout_tail=(stdout or "")[-2000:], stderr_tail=(stderr or "")[-500:])
        status, answer, steps = read_result(tmp, stdout or "")
        trace_rounds(tmp, args.keep_trajectory)
        trace("do:%s" % mode, "result", status=status, steps=steps, answer=(answer or "")[:1200])
        if timed_out and (status or "").lower() not in ("complete", "waiting_for_user"):
            status = "timed_out"

    base = {"task": args.task, "acted": bool(args.confirm)}
    if steps is not None:
        base["steps"] = steps
    st = (status or "").lower()

    if st == "complete":
        out({"ok": True, "status": "complete", "answer": answer or "", **base})
    elif st == "waiting_for_user":
        out({"ok": True, "status": "needs_input",
             "question": answer or "The agent needs more information to continue.",
             **base})
    elif st in ("max_rounds", "timed_out", "aborted"):
        out({"ok": False, "status": st, **base,
             "error": f"the task did not complete ({st}); "
                      f"partial finding: {answer or 'none'}"}, 1)
    elif answer:
        out({"ok": True, "status": "complete", "answer": answer, **base})
    else:
        tail = (stderr or "").strip()[-300:]
        fail("the browser agent returned no result; try again or narrow the task."
             + (f" [{tail}]" if tail else ""))


if __name__ == "__main__":
    main()
