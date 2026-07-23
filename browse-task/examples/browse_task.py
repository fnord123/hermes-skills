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
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Config path; overridable via env for testing.
CONFIG = Path(os.environ.get("BROWSE_TASK_CONFIG", str(HERE / "config.env")))

# Appended to the task in read-only mode. Best-effort instruction, not a browser
# sandbox — see README.
READONLY_DIRECTIVE = (
    " IMPORTANT CONSTRAINT: Only read and report information. Do not sign in, "
    "submit forms, purchase, book, post, send messages, or change any account or "
    "site state. If the task would require any such action, stop and report what "
    "you found and what action would be needed."
)


def out(d, code=0):
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
    args = p.parse_args()

    cfg = load_config()
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

    with tempfile.TemporaryDirectory(prefix="browse_task_") as tmp:
        cmd = [str(cli), "--task", task, "--start_page", args.start_url,
               "--output_folder", tmp, "--base_url", base_url,
               "--api_key", api_key, "--model", model,
               "--max_rounds", str(args.max_steps)]
        try:
            # /dev/null stdin: the task runs first, then the agent's interactive
            # prompt gets EOF and exits instead of blocking.
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            fail("the web task ran too long (30 min) and was stopped. Try a narrower "
                 "task or a lower --max-steps.")
        except Exception as e:  # noqa: BLE001
            fail(f"could not start the browser agent: {e}")

        status, answer, steps = read_result(tmp, proc.stdout)

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
