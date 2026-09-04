#!/usr/bin/env python3
"""Capture rx-review card timings AND server throughput as one comparable record.

Why this exists: card durations alone cannot tell you whether a serving-config change helped.
Comparing an `mtp n=4 -np 3` run against a `dspark n=7 -np 2` run showed every card type ~70%
slower, which looked damning until you notice the board still ran `max_in_progress: 3` against
two server slots. The third request waits INSIDE llama-server, and that wait lands in the
card's duration. Card time measures the pipeline; only the server's own counters measure the
model.

So this records both, plus the exact `llama-server` invocation, and warns when board
concurrency and `-np` disagree. Run it at the END of a run: llama.cpp's metrics are cumulative
since process start and are lost on restart or model swap.

    tools/rx-timings.py --label "dspark 7 -np2" > docs/rx-review-timings-dspark7-np2.md
    tools/rx-timings.py --label "mtp 4 -np3" --board rx-review

The backend is DISCOVERED via litellm, never hardcoded: serving moved .4 -> .16 and every
pinned address in the pipeline broke. Ask the proxy where the model lives, then ask that host.
"""
import argparse
import json
import os
import re
import sqlite3
import statistics as st
import sys
import time
import urllib.request

LITELLM = os.environ.get("LITELLM_URL", "http://192.168.1.226:4000")
HERMES_CONFIG = os.path.expanduser("~/.hermes/config.yaml")

CARD_KINDS = ("Transcribe labs", "Look up product", "Context audit", "Logic audit",
              "Counter-evidence", "Overreach", "Status quo", "Trend:", "Marker:", "Research:",
              "Consolidate", "Sweep", "Merge", "Advance", "Refresh", "Intake", "Split",
              "CONFIRM", "Adversarial", "Reconcile", "Assemble", "Citation audit",
              "Interactions", "Schedule review", "Deep refutation")


def _get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _hermes_conf():
    """(model, api_key, declared_context) from Hermes' own config."""
    try:
        cfg = open(HERMES_CONFIG, encoding="utf-8").read()
    except OSError:
        return None, None, None
    model = re.search(r"^\s*default:\s*(\S+)", cfg, re.M)
    key = re.search(r"^\s*api_key:\s*(\S+)", cfg, re.M)
    ctx = re.search(r"^\s*context_length:\s*(\d+)", cfg, re.M)
    return (model.group(1) if model else None,
            key.group(1) if key else None,
            int(ctx.group(1)) if ctx else None)


def discover_backend(model):
    """The llama-swap base URL litellm routes `model` to, or None.

    Discovered rather than pinned. Every hardcoded backend address in this pipeline broke when
    serving moved hosts, and one of them silently halved the planning window for a week.
    """
    _m, key, _c = _hermes_conf()
    try:
        d = json.loads(_get(LITELLM + "/v1/model_info" if False else LITELLM + "/v1/model/info",
                            headers={"Authorization": "Bearer %s" % (key or "")}))
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not ask litellm where %s lives (%s)" % (model, exc), file=sys.stderr)
        return None
    for m in d.get("data") or []:
        if m.get("model_name") == model:
            base = (m.get("litellm_params") or {}).get("api_base") or ""
            return base.rsplit("/v1", 1)[0] or None
    return None


def server_state(base):
    """(resident_model, cmdline, per_slot_ctx, slots, metrics) from llama-swap."""
    resident = cmd = None
    try:
        for r in (json.loads(_get(base + "/running")).get("running") or []):
            resident, cmd = r.get("model"), " ".join((r.get("cmd") or "").split())
            break
    except Exception:                                          # noqa: BLE001
        pass
    ctx = slots = None
    if resident:
        try:
            p = json.loads(_get("%s/upstream/%s/props" % (base, resident)))
            ctx = (p.get("default_generation_settings") or {}).get("n_ctx")
            slots = p.get("total_slots")
        except Exception:                                      # noqa: BLE001
            pass
    metrics = {}
    if resident:
        try:
            for line in _get("%s/upstream/%s/metrics" % (base, resident)).splitlines():
                g = re.match(r"^(llamacpp:\S+)\s+([0-9.eE+-]+)", line)
                if g:
                    metrics[g.group(1)] = float(g.group(2))
        except Exception as exc:                               # noqa: BLE001
            print("  ! no /metrics from %s (%s)" % (base, exc), file=sys.stderr)
    return resident, cmd, ctx, slots, metrics


def board_rows(board):
    db = os.path.expanduser("~/.hermes/kanban/boards/%s/kanban.db" % board)
    if not os.path.exists(db):
        sys.exit("no board db at %s" % db)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    return list(con.execute("SELECT id,title,assignee,status,created_at,started_at,"
                            "completed_at,max_runtime_seconds FROM tasks"))


def kind(title):
    for k in CARD_KINDS:
        if title.startswith(k):
            return k.rstrip(":")
    return "other"


def p90(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * 0.9))]


def kanban_concurrency():
    try:
        cfg = open(HERMES_CONFIG, encoding="utf-8").read()
    except OSError:
        return None, None
    a = re.search(r"^\s*max_in_progress:\s*(\d+)", cfg, re.M)
    b = re.search(r"^\s*max_in_progress_per_profile:\s*(\d+)", cfg, re.M)
    return (int(a.group(1)) if a else None), (int(b.group(1)) if b else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True,
                    help='serving config, e.g. "mtp 4 -np3" — becomes the report title')
    ap.add_argument("--board", default="rx-review")
    args = ap.parse_args()

    rows = board_rows(args.board)
    done = [r for r in rows if r["completed_at"] and r["started_at"]]
    if not done:
        sys.exit("no completed cards on board %r — nothing to report" % args.board)

    model, _key, declared = _hermes_conf()
    base = discover_backend(model) if model else None
    resident = cmd = ctx = slots = None
    metrics = {}
    if base:
        resident, cmd, ctx, slots, metrics = server_state(base)

    mip, mipp = kanban_concurrency()
    dur = lambda r: (r["completed_at"] - r["started_at"]) / 60.0            # noqa: E731
    queued = lambda r: max(0.0, (r["started_at"] - r["created_at"]) / 60.0)  # noqa: E731
    first = min(r["created_at"] for r in rows)
    last = max(r["completed_at"] or 0 for r in rows)
    wall = (last - first) / 60.0
    card = sum(dur(r) for r in done)
    qs = [queued(r) for r in done]

    agg = {}
    for r in done:
        agg.setdefault(kind(r["title"]), []).append(r)

    o = []
    o.append("# rx-review pipeline timings — %s\n" % args.label)
    o.append("Card timings and server throughput for one `rx-review` run, so serving-config")
    o.append("changes can be compared. **Label: `%s`.**\n" % args.label)
    o.append("_Recorded: %s._\n" % time.strftime("%Y-%m-%d %H:%M"))

    o.append("## Serving configuration\n")
    o.append("| | |")
    o.append("|---|---|")
    o.append("| Backend (discovered via litellm) | `%s` |" % (base or "UNKNOWN"))
    o.append("| Model requested by Hermes | `%s` |" % (model or "?"))
    o.append("| Resident on the backend | `%s` |" % (resident or "?"))
    o.append("| Context per slot | %s |" % (ctx or "?"))
    o.append("| Server slots (`-np`) | **%s** |" % (slots or "?"))
    o.append("| Hermes declared context_length | %s |" % (declared or "?"))
    o.append("| Kanban concurrency | `max_in_progress: %s`, per-profile `%s` |" % (mip, mipp))
    o.append("")
    if cmd:
        o.append("```\n%s\n```\n" % cmd)
    if slots and mip and mip > slots:
        o.append("> **Oversubscribed: %d cards in flight against %d server slot(s).** The extra"
                 % (mip, slots))
        o.append("> request waits inside `llama-server`, and that wait is counted in the card's")
        o.append("> duration — so card times here measure queueing as much as the model. Set")
        o.append("> `max_in_progress: %d` to compare card times against another config fairly."
                 % slots)
        o.append("")

    o.append("## Server throughput\n")
    if metrics:
        pt = metrics.get("llamacpp:prompt_tokens_total", 0)
        ps = metrics.get("llamacpp:prompt_seconds_total", 0)
        gt = metrics.get("llamacpp:predicted_tokens_total", 0)
        gs = metrics.get("llamacpp:predicted_seconds_total", 0)
        o.append("Cumulative since the server started, so **independent of kanban queueing** —")
        o.append("this is the number to compare across configs.\n")
        o.append("| | tokens | seconds | tok/s |")
        o.append("|---|---:|---:|---:|")
        o.append("| prompt (prefill) | %.0f | %.1f | **%.1f** |" % (pt, ps, pt / ps if ps else 0))
        o.append("| generated | %.0f | %.1f | **%.1f** |" % (gt, gs, gt / gs if gs else 0))
        o.append("")
        o.append("| reported instantaneous | tok/s |")
        o.append("|---|---:|")
        o.append("| prompt | %.1f |" % metrics.get("llamacpp:prompt_tokens_seconds", 0))
        o.append("| predicted | %.1f |" % metrics.get("llamacpp:predicted_tokens_seconds", 0))
        o.append("")
        if not gt:
            o.append("> `predicted_tokens_total` is zero: this llama.cpp build exposes only the")
            o.append("> instantaneous gauge. Use the instantaneous `predicted` figure, and take")
            o.append("> it while the server is under load or it reads as the last request only.")
            o.append("")
    else:
        o.append("**No metrics captured.** Counters are cumulative since process start and are")
        o.append("lost on restart or model swap, so this must run before either.\n")

    o.append("## Summary\n")
    o.append("| | |")
    o.append("|---|---|")
    o.append("| Cards completed | %d of %d |" % (len(done), len(rows)))
    o.append("| Wall clock | **%.0f min** (%.1f h) |" % (wall, wall / 60))
    o.append("| Total card time | **%.0f min** |" % card)
    o.append("| Effective parallelism | **%.1fx** |" % (card / wall if wall else 0))
    o.append("| Queue time median | **%.1f min** |" % st.median(qs))
    o.append("| Queue time p90 / max | %.1f / %.1f min |" % (p90(qs), max(qs)))
    o.append("")
    if len(done) < len(rows):
        o.append("> **Partial run** — %d of %d cards had completed. Per-card figures are real;"
                 % (len(done), len(rows)))
        o.append("> the totals are a floor.\n")

    o.append("## By card type\n")
    o.append("| card type | n | median | p90 | max | total | alloted | headroom |")
    o.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, v in sorted(agg.items(), key=lambda x: -sum(dur(r) for r in x[1])):
        ds = [dur(r) for r in v]
        al = st.median([(r["max_runtime_seconds"] or 0) / 60.0 for r in v])
        md = st.median(ds)
        o.append("| %s | %d | %.1f | %.1f | %.1f | %.1f | %.0f | %.1fx |"
                 % (k, len(v), md, p90(ds), max(ds), sum(ds), al, (al / md) if md else 0))
    o.append("| **all** | **%d** | | | | **%.1f** | | |" % (len(done), card))
    o.append("")

    o.append("## Individual cards\n")
    o.append("| min | queue | type | assignee | card |")
    o.append("|---:|---:|---|---|---|")
    for r in sorted(done, key=lambda r: -dur(r)):
        o.append("| %.1f | %.1f | %s | %s | %s |"
                 % (dur(r), queued(r), kind(r["title"]), r["assignee"] or "",
                    r["title"][:78].replace("|", "/")))

    outstanding = [r for r in rows if not r["completed_at"]]
    if outstanding:
        o.append("")
        o.append("## Not completed at time of recording\n")
        o.append("| status | card |")
        o.append("|---|---|")
        for r in sorted(outstanding, key=lambda r: (r["status"], r["title"])):
            o.append("| %s | %s |" % (r["status"], r["title"][:78].replace("|", "/")))
    o.append("")
    print("\n".join(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
