#!/usr/bin/env python3
"""browse_task.py — carry out a web task in a real browser and report the result.

Give it a plain-English task and it opens a browser, works through the pages to
completion, and returns one JSON object with what it found or did.

Read-only by default: without --confirm the agent is told to only read and
report — never to sign in, submit a form, buy, book, post, send, or change any
account or site state. Pass --confirm (only after the user approved that exact
task) when the task must ACT on a site.

Configuration is read from config.env beside this script (see config.env.example).
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
# Config path; overridable via env for testing.
CONFIG = Path(os.environ.get("BROWSE_TASK_CONFIG", str(HERE / "config.env")))
# Every run appends the command and the FULL agent output here so failures are
# diagnosable. Overridable via config (BROWSE_LOG) or env (BROWSE_TASK_LOG).
LOG = Path(os.environ.get("BROWSE_TASK_LOG",
                          str(Path.home() / ".hermes" / "logs" / "browse-task.log")))


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


def load_config():
    cfg = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


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


def resolve_mode(args, cfg, start_url, xvfb):
    """Pick browser mode (headless|headful|browserbase) and why, for this site."""
    override = (getattr(args, "mode", None) or cfg.get("BROWSE_MODE") or "").strip().lower()
    if override in ("headless", "headful", "browserbase"):
        return override, "override"
    legacy = (cfg.get("BROWSE_HEADFUL") or os.environ.get("BROWSE_HEADFUL") or "").strip().lower()
    if legacy in ("false", "0", "no"):
        return "headless", "BROWSE_HEADFUL"
    if legacy in ("true", "1", "yes"):
        return "headful", "BROWSE_HEADFUL"
    rules, default = load_policy(cfg, xvfb)
    host = _host(start_url)
    for pat, mode in rules:
        if pat and pat in host:
            return mode, f"policy:{pat}"
    for pat, mode in load_learned(cfg):   # previously auto-detected sites
        if pat and pat in host:
            return mode, "learned"
    return default, "default"


# A tiny loadability probe: open the start URL and report OK / BLOCKED. Run via
# the Fara venv's python (it has Playwright); headful is wrapped in xvfb-run.
PROBE_SRC = r"""
import sys, re, asyncio
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
            pg = await (await b.new_context()).new_page()
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
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=70)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        return lines[-1].strip() if lines else "ERR"
    except Exception:  # noqa: BLE001
        return "ERR"


def probe_ladder(url, xvfb, has_bb, fara_python):
    """Try headless, then headful; fall to browserbase if both are blocked."""
    for m in ("headless", "headful"):
        if m == "headful" and not xvfb:
            continue
        res = run_probe(url, m, xvfb, fara_python)
        log(f"probe {m}: {res}")
        if res == "OK":
            return m
    return "browserbase" if has_bb else ("headful" if xvfb else "headless")


def read_result(output_dir, stdout):
    """Return (status, answer, steps) from the trajectory, falling back to stdout."""
    status = answer = None
    steps = None
    files = sorted(
        glob.glob(os.path.join(output_dir, "**", "data_point.json"), recursive=True),
        key=os.path.getmtime,
    )
    if files:
        try:
            data = json.loads(Path(files[-1]).read_text())
            status = data.get("status")
            outcome = data.get("outcome") or {}
            if isinstance(outcome, dict):
                answer = outcome.get("answer")
            for key in ("actions", "steps", "observations"):
                if isinstance(data.get(key), list):
                    steps = len(data[key])
                    break
        except Exception:
            pass
    if answer is None:
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
    p.add_argument("--task", required=True, help="plain-English web task to carry out")
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
    p.add_argument("--mode", choices=["auto", "headless", "headful", "browserbase"],
                   default="auto",
                   help="browser mode; 'auto' (default) picks the optimal one per "
                        "site (see README's site policy).")
    args = p.parse_args()

    cfg = load_config()
    global LOG
    if cfg.get("BROWSE_LOG"):
        LOG = Path(cfg["BROWSE_LOG"])
    log("START " + json.dumps({"task": args.task, "acted": bool(args.confirm),
                               "start_url": args.start_url, "max_steps": args.max_steps}))
    fara_home = cfg.get("FARA_HOME") or ""
    base_url = cfg.get("BROWSE_BASE_URL") or ""
    model = cfg.get("BROWSE_MODEL") or ""
    api_key = cfg.get("BROWSE_API_KEY") or "none"
    if not (fara_home and base_url and model):
        fail("browse-task is not configured. Copy config.env.example to config.env "
             "and set FARA_HOME, BROWSE_BASE_URL, and BROWSE_MODEL (see README).")
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
            has_bb = bool(cfg.get("BROWSERBASE_API_KEY") and cfg.get("BROWSERBASE_PROJECT_ID"))
            log(f"unknown site {_host(args.start_url)} — probing browser modes")
            mode = probe_ladder(args.start_url, xvfb, has_bb, fara_python)
            save_learned(cfg, _host(args.start_url), mode)
            why = "probed"
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
            bb_key = cfg.get("BROWSERBASE_API_KEY")
            bb_proj = cfg.get("BROWSERBASE_PROJECT_ID")
            if not (bb_key and bb_proj):
                fail("this site needs BrowserBase (a managed browser that gets past "
                     "heavy bot protection), but it isn't configured. Set "
                     "BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID in config "
                     "(see README).")
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
        status, answer, steps = read_result(tmp, stdout or "")
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
        tail = (proc.stderr or "").strip()[-300:]
        fail("the browser agent returned no result; try again or narrow the task."
             + (f" [{tail}]" if tail else ""))


if __name__ == "__main__":
    main()
