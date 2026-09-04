#!/usr/bin/env python3
"""Kill orphaned rx-review worker processes (used by `rx.py reset`).

A card worker is a spawned `hermes -p rx-<name> --cli ... chat -q work kanban task <id>`
process. When reset wipes the board, running workers neither notice nor stop — they
grind on against deleted cards, burning model tokens until their runtime expires.
reset calls this after clearing the board so the next run starts with no stragglers.

Scope is deliberately narrow: only processes whose args match BOTH the rx- profile
pattern AND a kanban task invocation. A human sitting in `hermes --profile rx-devil`
chat is left alone.
"""
import os
import signal
import subprocess
import time


def _rx_workers():
    """(pid, cmdline) for every live process that looks like an rx-* card worker."""
    out = []
    try:
        ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True,
                            timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in ps.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, args = parts
        if not pid.isdigit():
            continue
        if "-p" not in args and " --profile " not in args:
            continue
        # profile flag value must be an rx- profile ...
        if not _has_rx_profile(args):
            continue
        # ... and it must be a kanban task worker, not interactive chat use
        if "kanban task " not in args:
            continue
        out.append((int(pid), args))
    return out


def _has_rx_profile(args):
    for flag in ("-p ", "--profile "):
        i = args.find(flag)
        if i == -1:
            continue
        rest = args[i + len(flag):].strip()
        return rest.split(None, 1)[0].startswith("rx-")
    return False


def reap(dry_run=False):
    """Kill orphaned workers. Returns (killed_pids, descriptions)."""
    killed, desc = [], []
    for pid, args in _rx_workers():
        short = "pid %d: %s" % (pid, args[:110])
        if dry_run:
            desc.append("(would kill) " + short)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
            desc.append(short)
        except (ProcessLookupError, PermissionError):
            continue  # already gone / not ours — nothing to do
    if killed:
        time.sleep(1.0)  # let SIGTERM land; survivors get reported on next scan
    return killed, desc


if __name__ == "__main__":
    pids, d = reap()
    for line in d:
        print(line)
    print("%d orphaned worker(s) terminated" % len(pids))
