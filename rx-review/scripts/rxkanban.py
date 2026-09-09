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
# The DEFAULT profile's config: the Discord fallback channel lives in the gateway that delivers
# it. HERMES_REAL_HOME is the user's home (profile homes nest under it), so the profile-aware
# fallback resolves to the same file the old hardcoded path meant; a profile-less CLI run has no
# HERMES_REAL_HOME and ~ is the same place.
_REAL_HOME = os.path.expanduser(os.environ.get("HERMES_REAL_HOME") or os.path.expanduser("~"))
CONFIG = os.path.join(_REAL_HOME, ".hermes", "config.yaml")
# Per-run output dirs: the reports root, with the same env override rx.py applies, so a test
# that repoints RX_REPORTS_ROOT sees its origin file where it looks for it.
REPORTS_ROOT = os.path.expanduser(os.environ.get("RX_REPORTS_ROOT", "~/.hermes/reports/rx-review"))
ORIGIN_REL = "run-origin"
# A run is recorded against a MESSAGING chat only. Anything else (cli, api, tui, no session
# at all) is not a delivery target, so the run falls back to the Discord default channel.
GATEWAY_PLATFORMS = {"discord", "matrix", "telegram", "slack", "signal", "whatsapp"}

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


def record_origin(run_dir):
    """Record the chat this run was started from into the run dir. Returns the target dict or None.

    Called ONCE by `start`, the same call that creates the run dir — so every later card and
    notification, spawned by worker profiles that carry no session of their own, resolves the
    delivery target FROM THE RUN DIR instead of from whatever process happens to be posting.
    A run started from a messaging gateway (Matrix, Discord, ...) is recorded against THAT chat;
    a run started from the CLI or any non-gateway surface records nothing and keeps the Discord
    fallback that predates this file.
    """
    platform = os.environ.get("HERMES_SESSION_PLATFORM", "").strip().lower()
    chat_id = os.path.expanduser(os.environ.get("HERMES_SESSION_CHAT_ID", "")).strip()
    if platform not in GATEWAY_PLATFORMS or not chat_id:
        return None
    profile = os.environ.get("HERMES_SESSION_PROFILE", "").strip()
    if not profile:
        try:
            profile = os.path.basename(os.environ.get("HERMES_HOME", "").rstrip("/"))
        except Exception:                                   # noqa: BLE001
            profile = ""
    target = {"platform": platform, "chat_id": chat_id, "profile": profile}
    try:
        with open(os.path.join(run_dir, ORIGIN_REL), "w", encoding="utf-8") as fh:
            json.dump(target, fh)
    except OSError as exc:
        print("  ! could not record run origin (%s) — notifications fall back to Discord" % exc)
    return target


def _origin_target():
    """The active run's recorded origin, or None. The `current` symlink is the single reader —
    workers never hardcode a run name, they follow the pointer `start` swapped."""
    try:
        with open(os.path.join(os.path.realpath(REPORTS_ROOT), "current", ORIGIN_REL),
                  encoding="utf-8") as fh:
            t = json.load(fh)
    except (OSError, ValueError):
        return None
    platform = (t.get("platform") or "").strip().lower()
    chat_id = (t.get("chat_id") or "").strip()
    if platform not in GATEWAY_PLATFORMS or not chat_id:
        return None
    return {"platform": platform, "chat_id": chat_id, "profile": (t.get("profile") or "").strip()}


def _discord_fallback():
    """The pre-existing default: the gateway's first Discord free_response_channel, or ''."""
    try:
        cfg = open(CONFIG, encoding="utf-8").read()
        m = re.search(r"^discord:.*?^\s*free_response_channels:\s*'?\"?([0-9,]+)",
                      cfg, re.S | re.M)
        if m:
            return m.group(1).split(",")[0].strip()
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def notify_target():
    """Where this run's card notifications go: (platform, chat_id, notifier_profile), or ("", "", "").

    Resolution order, first hit wins:
    1. RX_NOTIFY_PLATFORM / RX_NOTIFY_CHAT_ID — an explicit override so a test run can redirect
       itself (the role RX_DISCORD_CHANNEL always played; the legacy env var still works).
    2. The run's origin file — the chat the run was STARTED from. This is the whole fix: a run
       begun in a Matrix DM gets its gates in that DM, even though every card that posts them
       runs under a worker profile with no session of its own.
    3. The Discord free_response_channels default, unchanged.

    notifier_profile is the gateway that owns the adapter for that platform: the origin's
    profile when it has one, otherwise the RX_NOTIFIER_PROFILE default (the profile whose
    gateway delivers card notifications today).
    """
    env_platform = os.environ.get("RX_NOTIFY_PLATFORM", "").strip().lower()
    env_chat = os.environ.get("RX_NOTIFY_CHAT_ID", "").strip()
    if env_platform and env_chat:
        return (env_platform, env_chat, os.environ.get("RX_NOTIFIER_PROFILE", "default"))
    legacy = os.environ.get("RX_DISCORD_CHANNEL", "").strip()
    if legacy:
        return ("discord", legacy, os.environ.get("RX_NOTIFIER_PROFILE", "default"))
    t = _origin_target()
    if t:
        return (t["platform"], t["chat_id"], t["profile"] or os.environ.get("RX_NOTIFIER_PROFILE", "default"))
    chan = _discord_fallback()
    return ("discord", chan, os.environ.get("RX_NOTIFIER_PROFILE", "default"))


def discord_channel():
    """Backward-compatible view of notify_target(): the Discord chat id, or "" for another platform."""
    platform, chan, _profile = notify_target()
    return chan if platform == "discord" else ""


def send_cmd(message, quiet=True):
    """The `hermes send` argv for the run's notification chat.

    -p <notifier profile> is load-bearing: hermes send signs in with the
    CALLING process's bot token, but the run's chat belongs to the origin
    profile's bot. A #house-md gate posted by an rx-* worker's credentials
    (the default gateway's bot) was refused by Discord - the review never
    reached chat and the Stage 3 card blocked on silence (2026-09-09).
    The profile recorded in run-origin owns that bot, the same way
    --notifier-profile does for subscribe(). Returns (cmd, profile).
    """
    platform, chan, profile = notify_target()
    cmd = [HERMES]
    if profile:
        cmd += ["-p", profile]
    cmd += ["send", "-t", "%s:%s" % (platform, chan)]
    if quiet:
        cmd.append("-q")
    cmd.append(message)
    return cmd, profile


def announce(message):
    """Post a phase-level message to the run's notification chat. Never raises.

    Per-card notifications turn a run into narration of its own bookkeeping; phases are the
    unit a person cares about. Uses `hermes send`, which posts with the gateway's own
    credentials - no LLM, no agent loop.

    The target is where the run was STARTED (see notify_target()): a run begun in a Matrix DM
    announces into that DM, a run begun in Discord into Discord, a CLI run into the Discord
    default. Before the origin file existed every message here went to Discord no matter who
    started the run - which is how a Matrix review's gate questions landed in a Discord DM.

    A notification is cosmetic and the work it describes has already happened. Letting this
    raise once cost a completed run: 22 audit cards were created and linked, the announcement
    raised, the script exited 1, and the card blocked as though the audit had failed.
    """
    try:
        platform, chan, _profile = notify_target()
        if not platform or not chan or not message.strip():
            return False
        out = subprocess.run(send_cmd(message)[0], capture_output=True, text=True)
        if out.returncode != 0:
            print("  ! announce to %s:%s failed (exit %d): %s"
                  % (platform, chan, out.returncode, (out.stderr or out.stdout).strip()[:300]))
        return out.returncode == 0
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not announce to %s:%s (%s)" % (*notify_target()[:2], exc))
        return False


def subscribe(task_id):
    """Push a card's terminal events to the run's notification chat. Never raises.

    Subscribe the cards a human waits on. Without this the whole analysis stage - including
    the final brief - completes silently.

    --notifier-profile is load-bearing: without it the subscription is owned by the creating
    profile, and the notifier SKIPS any subscription whose owner has no running gateway, so
    every one of them was silently dropped. The origin's profile is the gateway that holds the
    adapter for the chat we are notifying into - that is what the delivery check needs.
    """
    try:
        platform, chan, profile = notify_target()
        if not platform or not chan or not task_id or task_id.startswith("DRY"):
            return False
        cmd = [HERMES, "kanban", "--board", BOARD, "notify-subscribe", task_id,
               "--platform", platform, "--chat-id", chan]
        if profile:
            cmd += ["--notifier-profile", profile]
        subprocess.run(cmd, capture_output=True, text=True)
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
