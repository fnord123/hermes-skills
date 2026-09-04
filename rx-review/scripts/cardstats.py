#!/usr/bin/env python3
"""Per-card context and compaction stats, from the provider's own accounting.

Hermes records nothing durable about how much context a card actually used. The worker log
prints token counts only inside WARNINGS, so the only cards you can measure from logs are the
ones that already went wrong - and its figures are the agent's own estimate, which undercounts
this workload badly: on 2026-07-31 the logs reported peaks of ~104k and ~96k for cards whose
real prompts, per the provider, were 146k-153k. Roughly 40% low, in the direction that hides
the problem.

litellm logs every request with the true prompt_tokens, but nothing tied a request to a card:
its session_id is a per-request UUID, so with six workers running concurrently, attribution by
timestamp was hopeless - 75 of 77 runs had an overlapping window, and four different cards were
credited with the same 149,550-token peak.

The fix is one header. Each profile sends

    default_headers:
      x-litellm-tags: "rxcard=${HERMES_KANBAN_TASK}"

and Hermes expands ${VAR} from the worker's environment, where the dispatcher has already set
HERMES_KANBAN_TASK. litellm stores it in LiteLLM_SpendLogs.request_tags. Sessions that are not
kanban workers leave the variable unset and are simply not tagged.

COMPACTIONS ARE INFERRED, NOT MEASURED. There is no compression hook in CLI worker context -
only the gateway gets session:compress, and the kanban log records nothing (the per-card .log
shows only the "Compacting context" marker; task_events carries no token payload). So this counts
the sharp DROPS in prompt_tokens within a card's request sequence. Context grows monotonically
through a conversation and only falls when history is discarded, so a large fall is a compaction.
A drop that is small or gradual is not counted, which means this can undercount when a compaction
barely shrinks the history.

The `compacted at` column reports, per card, the context size at EACH compaction — the peak the
conversation reached just before history was discarded, in true provider tokens. That is the
number nothing else records: it is what actually tripped the 0.6 x context_length trigger, seen
in accurate tokens rather than the harness estimate.

    python3 cardstats.py                 # this run
    python3 cardstats.py --since '2 days'
"""

import argparse
import json
import os
import re
import subprocess
import sys

# The compaction trigger is threshold_percent x context_length, and both live in the worker's
# PROFILE, not here — so this reads them from the live config rather than baking a number into a
# report. model.context_length is the agent window; the top-level compression.threshold is the
# fraction of it at which history is discarded. Trend and Research cards run under rx-research, so
# that profile is the representative window (every rx-* worker shares 200000/0.6 today). Falls back
# to Hermes' own ContextEngine defaults if the file or a key is missing.
WINDOW_PROFILE = os.environ.get("RX_STATS_PROFILE", "rx-research")
CONTEXT_LENGTH_DEFAULT = 200000
THRESHOLD_PERCENT_DEFAULT = 0.75          # agent/context_engine.py ContextEngine.threshold_percent


def profile_trigger(profile=WINDOW_PROFILE):
    """(context_length, threshold_percent) the live worker compacts at, read from its profile.

    model.context_length is the FIRST context_length in the file; the providers block repeats the
    key far below, so anchor on the `model:` header. The top-level `compression:` block carries
    threshold; a second, indented compression: elsewhere is a sub-key and is skipped by requiring
    the header at column 0.
    """
    ctx, thr = CONTEXT_LENGTH_DEFAULT, THRESHOLD_PERCENT_DEFAULT
    path = os.path.expanduser("~/.hermes/profiles/%s/config.yaml" % profile)
    try:
        cfg = open(path, encoding="utf-8").read()
    except OSError:
        return ctx, thr
    m = re.search(r"^model:\s*$.*?^\s*context_length:\s*([0-9]+)", cfg, re.S | re.M)
    if m:
        ctx = int(m.group(1))
    m = re.search(r"^compression:\s*$.*?^\s*threshold:\s*([0-9.]+)", cfg, re.S | re.M)
    if m:
        thr = float(m.group(1))
    return ctx, thr


DOCKER_HOST = "docker"
# SQL goes in on STDIN, never interpolated into a shell string. The first version built
# `psql -tAc '<sql>'` and escaped quotes the SQL way (doubling them) for a SHELL context, then
# dropped the -c flag while slicing the command list - so psql silently received no query and
# the report read "no tagged requests" while the rows were sitting in the table.
PG = "docker exec -i litellm-postgres psql -U litellm -d litellm -tA -f -"

# A fall of at least this fraction of the running peak is a compaction rather than one turn
# happening to carry less than the last. Compaction targets ~20% of the window, so a real one
# is a cliff; tool results merely dropping out of a turn are not.
DROP_FRACTION = 0.25

# A compacted conversation is still a conversation: compression targets ~20% of the window, so
# the request after a real compaction is tens of thousands of tokens, not hundreds. Workers also
# end with a small auxiliary call - a 124-token prompt returning a 1,551-token summary - which
# is a fresh prompt, not a compaction. Without this floor (and the "not last" rule below) every
# card in a run reported exactly one compaction: that final call, counted 42 times out of 42.
MIN_COMPACTED_TOKENS = 2000


def query(sql):
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=8", DOCKER_HOST, PG],
                         input=sql, capture_output=True, text=True, timeout=120)
    if out.returncode != 0 or "ERROR" in out.stderr:
        print("query failed: %s" % (out.stderr or out.stdout).strip()[:300], file=sys.stderr)
        raise SystemExit(1)
    return [l for l in out.stdout.splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="1 day", help="postgres interval, e.g. '2 days'")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = query(
        "select substring(request_tags::text from 'rxcard=(t_[0-9a-f]+)'), "
        "prompt_tokens, extract(epoch from \"startTime\") "
        "from \"LiteLLM_SpendLogs\" "
        "where request_tags::text like '%%rxcard=t_%%' and status='success' "
        "and \"startTime\" > now() - interval '%s' order by \"startTime\"" % args.since)

    cards = {}
    for line in rows:
        parts = line.split("|")
        if len(parts) < 3 or not parts[0]:
            continue
        cards.setdefault(parts[0], []).append(int(parts[1]))

    if not cards:
        print("No tagged requests in the last %s.\n"
              "Cards tag themselves only if the profile sends x-litellm-tags — see the module\n"
              "docstring. Runs from before that header was added cannot be recovered."
              % args.since)
        return 0

    out = []
    for card, seq in cards.items():
        # `running` is the peak the conversation reached before the current request. When a drop
        # is a compaction, that peak IS the context size at the moment history was discarded — the
        # number we want per compaction, in TRUE provider tokens.
        peak, sizes, running = 0, [], 0
        for i, p in enumerate(seq):
            is_last = i == len(seq) - 1
            if (running and p < running * (1 - DROP_FRACTION)
                    and p >= MIN_COMPACTED_TOKENS and not is_last):
                sizes.append(running)
                running = p
            else:
                running = max(running, p)
            peak = max(peak, p)
        out.append({"card": card, "requests": len(seq), "max_context": peak,
                    "compactions": len(sizes), "compacted_at": sizes})
    out.sort(key=lambda r: -r["max_context"])

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    def _sizes(ss):
        # Each compaction's context size at the moment it fired, in K of TRUE provider tokens.
        return ", ".join("%dK" % round(s / 1000.0) for s in ss)

    print("%-12s %9s %8s %5s  %s" % ("card", "max_ctx", "requests", "comp", "compacted at"))
    for r in out:
        print("%-12s %9s %8d %5d  %s"
              % (r["card"], format(r["max_context"], ","), r["requests"],
                 r["compactions"], _sizes(r["compacted_at"])))
    peaks = sorted(r["max_context"] for r in out)
    allsizes = [s for r in out for s in r["compacted_at"]]
    tail = ""
    if allsizes:
        allsizes.sort()
        ctx, thr = profile_trigger()
        tail = ("; compactions fired at median %dK (range %dK–%dK) — trigger is %gx%dk=%dk est."
                % (round(allsizes[len(allsizes) // 2] / 1000.0),
                   round(allsizes[0] / 1000.0), round(allsizes[-1] / 1000.0),
                   thr, round(ctx / 1000.0), round(thr * ctx / 1000.0)))
    print("\n%d card(s); median peak %s, max %s; %d compaction(s) total%s"
          % (len(out), format(peaks[len(peaks) // 2], ","), format(peaks[-1], ","),
             sum(r["compactions"] for r in out), tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
