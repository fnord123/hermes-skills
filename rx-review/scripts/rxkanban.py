#!/usr/bin/env python3
"""Kanban and notification plumbing, in one place.

Every script here grew its own copy. Measured across the pipeline before this module existed:
`_discord_channel` had four implementations, `create` four, `announce` two, `subscribe` two -
and no two copies were identical. That is not a tidiness complaint. One copy of announce() was
missing the helper it calls and raised NameError, which failed a run whose 22 cards had all
succeeded; one copy of the fetcher was missing its throttle, and an entire citation audit
judged claims against "Checking your browser".

So the mechanics live here once - resolve the channel, post a message, subscribe a card, create
a card, splice a parent onto a barrier - and each script keeps only the thin wrapper that
supplies its own workspace, key prefix and progress format. Domain choices stay with the
domain; the subprocess call, the id parsing and the failure handling do not.
"""

import json
import os
import re
import subprocess
import time

HERMES = os.path.expanduser("~/.local/bin/hermes")
BOARD = os.environ.get("RX_BOARD", "rx-review")
CONFIG = os.path.expanduser("~/.hermes/config.yaml")

TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{6,}\b")


def slugify(text, limit=None):
    """A title reduced to a stable key. NOT truncated by default.

    The three copies this replaced truncated at 48, 56 and 60 characters, which was arbitrary:
    `idempotency_key` is unbounded TEXT, so truncation bought nothing and cost correctness.
    Two titles sharing a prefix collapse to one key, and a colliding key does not error - the
    create silently returns the EXISTING card and discards the new --parent arguments, so the
    graph quietly wires itself to the wrong node. Pass an explicit limit only where a SHORT
    string is genuinely wanted, such as a filename component.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit] if limit else slug


def discord_channel():
    """The Discord channel card notifications go to, or "" when none is configured.

    Env var first so a test run can redirect itself; otherwise the gateway's own
    free_response_channels. This used to be reimplemented in three other modules and stubbed
    to None in a fourth - where the stub meant every chunk card was created without a
    subscription, so chunk 15 timed out twice, tripped the circuit breaker and stalled the
    whole tail with nobody notified. A pipeline that fails quietly is worse than one that
    fails loudly.
    """
    env = os.environ.get("RX_DISCORD_CHANNEL")
    if env:
        return env.strip()
    try:
        cfg = open(CONFIG, encoding="utf-8").read()
        m = re.search(r"^discord:.*?^\s*free_response_channels:\s*'?\"?([0-9,]+)",
                      cfg, re.S | re.M)
        if m:
            return m.group(1).split(",")[0].strip()
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def announce(message):
    """Post a phase-level message to Discord. Never raises.

    Per-card notifications turn a run into narration of its own bookkeeping; phases are the
    unit a person cares about. Uses `hermes send`, which posts with the gateway's own
    credentials - no LLM, no agent loop.

    A notification is cosmetic and the work it describes has already happened. Letting this
    raise once cost a completed run: 22 audit cards were created and linked, the announcement
    raised, the script exited 1, and the card blocked as though the audit had failed.
    """
    try:
        chan = discord_channel()
        if not chan or not message.strip():
            return False
        return subprocess.run([HERMES, "send", "-t", "discord:%s" % chan, "-q", message],
                              capture_output=True, text=True).returncode == 0
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not announce to discord (%s)" % exc)
        return False


def subscribe(task_id):
    """Push a card's terminal events to Discord. Never raises.

    Subscribe the cards a human waits on. Without this the whole analysis stage - including
    the final brief - completes silently.

    --notifier-profile is load-bearing: without it the subscription is owned by the creating
    profile, and the notifier SKIPS any subscription whose owner has no running gateway, so
    every one of them was silently dropped.
    """
    try:
        chan = discord_channel()
        if not chan or not task_id or task_id.startswith("DRY"):
            return False
        subprocess.run(
            [HERMES, "kanban", "--board", BOARD, "notify-subscribe", task_id,
             "--platform", "discord", "--chat-id", chan,
             "--notifier-profile", os.environ.get("RX_NOTIFIER_PROFILE", "default")],
            capture_output=True, text=True)
        return True
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not subscribe %s (%s)" % (task_id, exc))
        return False


def is_dry(tid):
    """True when this id is a dry-run placeholder rather than a real card.

    ONE predicate, because there are TWO sentinels. create_card() below returns the bare string
    "DRY"; fanout.py's own create() wrapper returns "DRY-<slug>" so a preview can tell two cards
    apart. Every caller compared `!= "DRY"`, which is always True against "DRY-xxx" - so the
    dry-run parent filters in fanout.py and lenses.py were dead code that read as guards, while
    the identical-looking ones here and in rx.py worked. Nothing is created in a dry run either
    way, but the previewed graph was wrong, which is the thing a preview is for.
    """
    return isinstance(tid, str) and (tid == "DRY" or tid.startswith("DRY-"))


# Seconds to leave between consecutive card creations. Every card in this pipeline is created
# through create_card() below, and each one is a SEPARATE `hermes kanban create` subprocess:
# process start, open the board, write, close. On 2026-08-11 `verify.py fanout` ran 86 of those
# back to back (768 citations -> 86 cards) while the dispatcher was spawning workers and the
# dashboard was polling, and the board's SQLite came out one page shorter than its own header
# claimed — a torn extend, the failure Hermes names in `_check_file_length_invariant`. The burst
# died partway with "could not parse a task id from:", which is the CLI already failing to
# return an id. Pacing the writes gives each one a quiet file to extend into.
#
# Measured 2026-08-12 on an idle board: `kanban create` takes ~0.26s end to end, of which ~0.18s
# is interpreter start and Hermes imports and only ~0.08s is the write. 1s is therefore ~12x the
# write it separates — enough for the file to settle between extensions, without the pacing
# dominating a fan-out. At 5s an 86-card audit spent 7 minutes of a 20-minute card asleep; at 1s
# it spends ~1.5. The cost is bounded and predictable: a fan-out of N cards takes (N-1) x this.
# Tune with the env var rather than editing the code; 0 disables it (what the test suite uses).
CREATE_DELAY_S = float(os.environ.get("RX_CARD_CREATE_DELAY", "1"))
_created_one = [False]                     # list, so the module-level flag is writable in-place


def _pace_creates():
    """Leave CREATE_DELAY_S between creations — before each one except the first."""
    if CREATE_DELAY_S > 0 and _created_one[0]:
        time.sleep(CREATE_DELAY_S)
    _created_one[0] = True


def create_card(title, assignee, body, workspace, parents=(), runtime="45m",
                priority=0, key=None, dry=False, notify=False):
    """Create one kanban card and return its id, or a DRY_PREFIX placeholder when previewing.

    A create failure is fatal, never assumed: the caller's graph is wrong from that point on,
    and continuing builds the rest of it on a parent that does not exist.

    `key` is the idempotency key. Callers prefix it per module so two scripts cannot collide
    on a shared title, and round-dependent titles must carry the round - re-planning with the
    same title returns the EXISTING card and silently discards the new --parent arguments.
    """
    cmd = [HERMES, "kanban", "--board", BOARD, "create", title,
           "--assignee", assignee, "--max-runtime", runtime,
           "--workspace", "dir:" + os.path.expanduser(workspace),
           "--priority", str(priority),
           "--idempotency-key", key or slugify(title),
           "--body", body]
    for p in parents:
        if p and not is_dry(p):
            cmd += ["--parent", p]
    if dry:
        return "DRY"                       # a preview writes nothing, so it paces nothing
    _pace_creates()
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print("  FAILED %s: %s" % (title, (out.stderr or out.stdout).strip()[:300]))
        raise SystemExit(1)
    m = TASK_ID_RE.search(out.stdout)
    if not m:
        print("  could not parse a task id from: %s" % out.stdout.strip()[:200])
        raise SystemExit(1)
    tid = m.group(0)
    if notify:
        subscribe(tid)
    return tid


def board_cards(title_like=None, statuses=None):
    """Cards on the board via the Hermes CLI (never raw SQLite): [(id, title, status)].

    `statuses` filters to a set of statuses; `title_like` matches the title — a trailing `%` is a
    prefix match (mirroring the old SQL LIKE), otherwise a plain substring test. All the filtering
    is done in Python on the CLI's JSON so the board file is never touched directly.
    """
    out = subprocess.run([HERMES, "kanban", "--board", BOARD, "list", "--json"],
                         capture_output=True, text=True)
    try:
        tasks = json.loads(out.stdout)
    except Exception:                                          # noqa: BLE001
        return []
    res = []
    for t in tasks:
        tid, title, status = t.get("id"), t.get("title") or "", t.get("status")
        if not tid:
            continue
        if statuses is not None and status not in statuses:
            continue
        if title_like is not None:
            if title_like.endswith("%"):
                if not title.startswith(title_like[:-1]):
                    continue
            elif title_like not in title:
                continue
        res.append((tid, title, status))
    return res


def splice(upstream_ids, barrier_like):
    """Link each upstream card in front of a barrier that has NOT started yet.

    Linking a parent onto a running card does nothing - kanban does not un-start it - which is
    how a reconciler once ran three hours ahead of its evidence. Selecting on todo/ready is
    what makes this safe to call from a sweep round that fires long after the graph was built.
    """
    ids = [i for i in upstream_ids if i and not is_dry(i)]
    if not ids:
        return []
    # Read the board through the Hermes CLI — NEVER open kanban.db directly, not even read-only.
    # Hermes owns that file (its dashboard holds it open continuously); a raw connection bypasses
    # kanban_db.connect()'s WAL/synchronous/busy_timeout settings, and a read-write one can
    # CHECKPOINT on close and truncate the main file. The board was corrupted four times on
    # 2026-07-29/30 doing exactly that. The `hermes kanban` API is the only door.
    rows = [cid for (cid, _t, _s) in board_cards(title_like=barrier_like, statuses=("todo", "ready"))]
    linked = []
    for cid in rows:
        for up in ids:
            subprocess.run([HERMES, "kanban", "--board", BOARD, "link", up, cid],
                           capture_output=True, text=True)
            linked.append((up, cid))
    return linked
