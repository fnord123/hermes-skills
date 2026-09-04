#!/usr/bin/env python3
"""rx — run the medication/supplement review end to end.

    python3 ~/.hermes/rx-review/rx.py stage           # start a review: stage 1 of 8
    python3 ~/.hermes/rx-review/rx.py status          # where everything stands
    python3 ~/.hermes/rx-review/rx.py analyze-research # build the research substages (6a-6d)

A review runs as eight stages. Stage 1 (`stage` + `start`) creates the WHOLE Begin/Barrier chain
for stages 2-8 up front; each later stage is a Stage Begin card - intake-regimen (2),
intake-regimen-items (3), intake-labs (4), review_labs (5), analyze-research (6),
analyze-adversarial (7), analyze-conclude (8) - released when the Barrier ahead of it completes.
Add lab PDFs to inputs/raw/, edit inputs/regimen.txt, run `stage`, then `start`.

Everything is idempotent. Cards carry idempotency keys, and a card is only created when its
output is missing or older than its input, so re-running costs nothing.

WHY ONE CARD PER PDF: a worker transcribing several documents overflows its context window and
Hermes compacts — which summarizes away the very numbers being transcribed. One document per
worker keeps each well under the limit and isolates a failure to one lab result.
"""

import argparse
import difflib
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxcache
import rxsplit                                               # noqa: E402
import rxkanban                                              # noqa: E402
from rxkanban import announce, subscribe                    # noqa: E402,F401
from rxkanban import discord_channel as _discord_channel    # noqa: E402,F401
import sys
import time


def stable_key(prefix, *parts):
    """Idempotency key that survives across processes.

    Python's builtin hash() is salted per interpreter run (PYTHONHASHSEED), so using it here
    produced a NEW key — and therefore a duplicate card — on every invocation.
    """
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return "%s-%s" % (prefix, h[:12])

HERMES = os.path.expanduser("~/.local/bin/hermes")
BOARD = os.environ.get("RX_BOARD", "rx-review")
# Profile whose gateway actually delivers Discord notifications.
NOTIFIER_PROFILE = os.environ.get("RX_NOTIFIER_PROFILE", "default")
# The pipeline's working dir is THIS script's own directory, wherever the skill is installed —
# inputs/, .phase.json, the per-run artifacts, sources/ and fanout.py all live beside rx.py.
# Anchoring here (not to a hardcoded path) is what lets the skill move without a code change.
HOME = os.path.dirname(os.path.abspath(__file__))
# RX_INPUTS lets the parsing be exercised against fixtures without touching the real inputs -
# the same hook fanout.py already had. Every lab-parsing rule in this file was written against
# a specific document that broke it, and rx_test.py is where those documents now live.
INPUTS = os.path.expanduser(os.environ.get("RX_INPUTS", os.path.join(HOME, "inputs")))
RAW = os.path.join(INPUTS, "raw")
PHOTOS = os.path.join(INPUTS, "supplements")
# Scratch for the transcribe flow: the pipeline writes each range's extracted results text here
# (<token>.src.txt) and the model writes its table here (<token>.tbl.md); check-transcription reads
# both, verifies, and writes the real labs-doc-*.md. Dotted so reset() clears it explicitly.
XCRIBE = os.path.join(INPUTS, ".xcribe")
# Each invocation gets its OWN timestamped output dir REPORTS_ROOT/<YYYY-MM-DD-HHMMSS>/, and
# `current` is a symlink to the active one. REPORTS resolves through that symlink, so all eight
# stages — dozens of separate worker processes over hours — write into the SAME run dir. Stage 1
# (start_run) is the one writer that creates the dir and swaps the symlink, before any parallel
# card exists. Past run dirs are kept as the deliverables; only `reset --clear-reports` purges them.
REPORTS_ROOT = os.path.expanduser(os.environ.get("RX_REPORTS_ROOT", "~/.hermes/reports/rx-review"))
CURRENT_LINK = os.path.join(REPORTS_ROOT, "current")
REPORTS = CURRENT_LINK
TILDE = "~/.hermes/rx-review/inputs"


def start_run():
    """Create a fresh timestamped run dir and point REPORTS_ROOT/current at it. Returns (dir, stamp).

    Called once at Stage 1, before any parallel card exists, so it is the single writer of the
    pointer — the concurrency rule the whole pipeline lives by. The symlink swap is atomic (temp
    link + os.replace), so a crash mid-swap never leaves `current` dangling.
    """
    os.makedirs(REPORTS_ROOT, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    run = os.path.join(REPORTS_ROOT, stamp)
    os.makedirs(run, exist_ok=True)
    # A real dir literally named `current` (e.g. a pre-symlink corpus) would block the swap; retire
    # it rather than delete, so no output is ever lost to this migration.
    if os.path.isdir(CURRENT_LINK) and not os.path.islink(CURRENT_LINK):
        os.rename(CURRENT_LINK, os.path.join(REPORTS_ROOT, "legacy-" + stamp))
    tmp = CURRENT_LINK + ".swap.%d" % os.getpid()
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(stamp, tmp)                                  # relative target → tree stays relocatable
    os.replace(tmp, CURRENT_LINK)                           # atomic; replaces any existing symlink
    return run, stamp


def run_stamp():
    """The active run's stamp (dir basename), or '' if no run is current."""
    try:
        return os.path.basename(os.path.realpath(REPORTS))
    except OSError:
        return ""


def brief_name():
    """Canonical final-brief filename for the active run: <date>-rx-review.md. Dated from the run
    dir so a review that concludes past midnight still names the brief by the day it started."""
    stamp = run_stamp()
    day = stamp[:10] if len(stamp) >= 10 and stamp[4] == "-" else time.strftime("%Y-%m-%d")
    return "%s-rx-review.md" % day


def run_dirs():
    """Every past run's output dir under REPORTS_ROOT — the timestamped deliverables (and any
    retired legacy-* dir). The `current` symlink and stray top-level files are not run dirs."""
    out = []
    try:
        for name in sorted(os.listdir(REPORTS_ROOT)):
            p = os.path.join(REPORTS_ROOT, name)
            if name != "current" and os.path.isdir(p) and not os.path.islink(p):
                out.append(p)
    except OSError:
        pass
    return out
# THE regimen lives at inputs/regimen.txt — see regimen_path(), which is a function rather than a
# constant so it follows INPUTS wherever that is repointed (a test, another corpus) instead of
# freezing the value this module happened to import with.
# A card body cannot exceed this, so neither can anything carried inline in one. Same value the
# adversarial chunker packs to (verify.KANBAN_BODY_CAP), stated here so stage 2 does not import
# the citation-audit module to size a card.
KANBAN_BODY_CAP = 8 * 1024

# Pipeline files that also match the `labs-*.md` glob but are NOT per-PDF transcriptions: the
# stage-4 merge output, the stage-5 review file and its succinct view, and the legacy brief name.
# Every glob for transcriptions excludes these, or the merge would fold its own output back in.
PIPELINE_LABS = ("labs-draft.md", "labs-complete.md", "labs-succinct.md", "labs-brief.md")


def transcription_files():
    """The per-PDF `labs-<slug>.md` transcriptions under inputs/, never the pipeline files."""
    return sorted(f for f in glob.glob(os.path.join(INPUTS, "labs-*.md"))
                  if os.path.basename(f) not in PIPELINE_LABS)


CHARS_PER_MINUTE = 6000      # pessimistic: slices are re-read, P40 serves one request at a time
MIN_MINUTES, MAX_MINUTES = 20, 120


def mtime(p):
    return os.path.getmtime(p) if os.path.exists(p) else 0


def stale(out, *ins):
    """True when `out` is missing or older than any input."""
    o = mtime(out)
    return o == 0 or any(mtime(i) > o for i in ins if i)


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def send_detail(text):
    """Send the FULL text to Discord as a real message.

    Card notifications go through gateway/kanban_watchers.py, which truncates a block reason
    to 160 chars — enough to say something is waiting, nowhere near enough to act on. `hermes
    send` posts a normal message using the gateway's own credentials (no LLM, no agent loop),
    and the Discord adapter chunks it properly at 2000 chars.
    """
    chan = _discord_channel()
    if not chan or not text.strip():
        return False
    out = sh([HERMES, "send", "-t", "discord:%s" % chan, "-q", text])
    return out.returncode == 0


PHASE_FILE = os.path.join(HOME, ".phase.json")


def phase_start(name, message):
    """Announce a phase ONCE, and record when it began.

    Idempotent by design. An intake stage runs more than once - a rebuilt inventory or a fresh
    upload sends the pipeline back through it - so any announcement keyed on "did this pass
    create cards" fires again on the repeat. It cannot even be guarded by "were any cards created",
    because create() is idempotent: given a stable key it returns the EXISTING card's id, so
    the id list is non-empty on every pass. That produced two "Lab transcription started"
    messages three minutes apart, quoting 5 PDFs and then 3.

    A phase is open from its first announcement until phase_end clears it. Re-entry is silent.
    """
    try:
        st = json.load(open(PHASE_FILE)) if os.path.exists(PHASE_FILE) else {}
    except Exception:                                          # noqa: BLE001
        st = {}
    if st.get(name):
        return                                                 # already open - say nothing
    st[name] = time.time()
    try:
        json.dump(st, open(PHASE_FILE, "w"))
    except Exception:                                          # noqa: BLE001
        pass
    send_detail(message)


def phase_tokens(since_ts):
    """Token totals for rx-* worker sessions since `since_ts`, from Hermes' own storage.

    Each profile is a full HERMES_HOME with its OWN state.db - the top-level one has
    profile_name NULL on every row and knows nothing about workers. Nothing else is polled.
    Returns {} rather than guessing when there is no data.
    """
    import sqlite3
    tin = tout = n = 0
    for db in glob.glob(os.path.expanduser("~/.hermes/profiles/rx-*/state.db")):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            r = c.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                          "COUNT(*) FROM sessions WHERE started_at >= ?", (since_ts,)).fetchone()
            c.close()
        except Exception:                                      # noqa: BLE001
            continue
        if r:
            tin += r[0] or 0; tout += r[1] or 0; n += r[2] or 0
    return {"input": tin, "output": tout, "sessions": n} if n else {}


def phase_end(name, headline, extra=()):
    """Announce a phase finishing, with wall clock and tokens when Hermes recorded them."""
    started = 0
    try:
        started = (json.load(open(PHASE_FILE)) or {}).get(name) or 0
    except Exception:                                          # noqa: BLE001
        pass
    lines = [headline] + list(extra)
    bits = []
    if started:
        m, sec = divmod(int(time.time() - started), 60)
        bits.append("%dm %02ds wall" % (m, sec))
        tk = phase_tokens(started)
        if tk:
            bits.append("%s in / %s out tokens" % ("{:,}".format(tk["input"]),
                                                   "{:,}".format(tk["output"])))
            bits.append("%d worker session(s)" % tk["sessions"])
    if bits:
        lines.append(" · ".join(bits))
    # Close the phase so a later, genuinely new one can announce again.
    try:
        st = json.load(open(PHASE_FILE)) if os.path.exists(PHASE_FILE) else {}
        st.pop(name, None)
        json.dump(st, open(PHASE_FILE, "w"))
    except Exception:                                          # noqa: BLE001
        pass
    send_detail("\n".join(lines))


IGNORE_PREFIX = "ignore: "
LABS_FP_PREFIX = "labs-sha: "


def _labs_fingerprint():
    """SHA of the transcribed labs being rejected. '' when there is none.

    Tied into the halt record so a rejection names exactly which reading was refused.
    """
    for name in ("labs-complete.md", "labs-draft.md"):
        path = os.path.join(INPUTS, name)
        if os.path.exists(path):
            return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    return ""


def dropped_items():
    """Regimen items the user could not place, flattened for tolerant matching.

    `regimen-confirm --unknown` records an item as answered-but-unanswerable in ITS OWN per-item
    file (inputs/regimen-item-<slug>.md): it stops being outstanding, so the `Stage 3: Finalize
    Regimen` Barrier can complete, and it is excluded from research. Read from the per-item files
    rather than regimen-final.md, because during stage 3 the barrier has not gathered them yet and
    regimen-final.md does not exist — the per-item files are the source of truth, and they persist
    after the gather so this stays correct in stage 6 too.

    A substance with no dose cannot be reasoned about - every research part asks dose-dependent
    questions - so researching it against a guess produces a confident brief about a regimen the
    user does not have. The brief reports what was dropped.
    """
    out = set()
    for f in glob.glob(os.path.join(INPUTS, "regimen-item-*.md")):
        for line in open(f, encoding="utf-8", errors="replace"):
            if "|" not in line or UNKNOWN_ANSWER not in line:
                continue
            # A dropped item is a plain `name | UNKNOWN` line, never a table row (which starts "|").
            if line.lstrip().startswith("|"):
                continue
            name = line.split("|", 1)[0].strip()
            if name:
                out.add(_flat(name))
    return out


def is_dropped(name):
    """True when this regimen item was marked unknown and must not be researched."""
    return _flat(name) in dropped_items()


def ignored_markers():
    """Marker names the user asked not to research, from the review decisions in labs-complete.md.

    Recorded by `marker-review --ignore` as `ignore: <name>` lines. The marker stays in
    labs-complete.md and in the report; only its research cards are skipped. Flattened for
    tolerant matching, because the same analyte is written several ways across panels.
    """
    path = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(path):
        return set()
    out = set()
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if s.lower().startswith(IGNORE_PREFIX):
            name = s[len(IGNORE_PREFIX):].strip()
            if name:
                out.add(_flat(name))
    return out


def is_ignored(name):
    """True when `name` is one the user excluded.

    Matched on alphanumerics only, because the same analyte is written several ways across
    panels - "APOLIPOPROTEIN B", "Apolipoprotein B", "Apolipoprotein-B" - and asking the user
    to reproduce a lab's punctuation to be heard would be its own defect.
    """
    return _flat(name) in ignored_markers()

# Files under inputs/ that end in .txt but say nothing about the regimen.
#
# regimen_sources() globs inputs/*.txt to find the regimen (regimen.txt). The halt records
# REGIMEN-REJECTED.txt and LABS-REJECTED.txt also land in inputs/, so they must be excluded here
# — otherwise a halted run's rejection record would be read back as regimen text on the next run.
CONTROL_TXT = {"REGIMEN-REJECTED.txt", "LABS-REJECTED.txt"}

# What `regimen-confirm --unknown` writes as the answer. It reads as an answer to check_regimen -
# the item stops being outstanding, so the gate can close - and as a DROP to the research fan-out,
# which skips the substance entirely. A dose nobody can reconstruct is not a dose to research
# against a guess, and the brief reports what it did not cover.
UNKNOWN_ANSWER = "UNKNOWN — user could not confirm; excluded from research"


def regimen_path():
    """The one file the regimen lands in, whatever INPUTS currently is."""
    return os.path.join(INPUTS, "regimen.txt")


def regimen_sources(inputs_dir):
    """The .txt files under inputs/ that actually describe the regimen."""
    return sorted(p for p in glob.glob(os.path.join(inputs_dir, "*.txt"))
                  if os.path.basename(p) not in CONTROL_TXT)


SALVAGE = os.path.join(os.path.dirname(INPUTS), "salvage")

LABS_REJECTED = os.path.join(INPUTS, "LABS-REJECTED.txt")
REGIMEN_REJECTED = os.path.join(INPUTS, "REGIMEN-REJECTED.txt")


def _halt(args, record, reason, derived, fingerprint=None, forget_pdfs=()):
    """End the review. Both reject verbs are this function with different artifacts.

    A rejection is not "answer differently" - it says the pipeline's reading of an input cannot
    be trusted, and nothing downstream should reason about it. Four things have to happen, and
    they are the same for either gate:

    WRITE THE RECORD, with the reason and, where there is one, the fingerprint of the artifact
    being rejected, so it is tied to exactly what was refused.

    ARCHIVE EVERY OPEN CARD, including the gate. Halting has to remove the cards: a flag each
    card checks is a halt that keeps running for as long as it takes every in-flight card to
    notice, and the cards already running would finish their work first.

    MAKE THE REJECTED READING UNREPEATABLE. Derived artifacts MOVE to salvage/ - moved, not
    deleted, because the reason for the rejection usually has to be found in them, and salvage/
    survives `reset` for that reason. Content-addressed cache entries are dropped outright,
    since a cache replays regardless of where the file went.

    KEEP WHAT THE PIPELINE DID NOT PRODUCE: the user's own uploads, and anything that came from
    an authoritative source rather than from the reading being rejected.
    """
    if args.dry_run:
        print("would halt the review: %s" % reason)
        return 0

    os.makedirs(INPUTS, exist_ok=True)
    with open(record, "w", encoding="utf-8") as fh:
        fh.write("rejected %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        if fingerprint:
            fh.write("%s%s\n" % (LABS_FP_PREFIX, fingerprint))
        fh.write("reason: %s\n" % reason)
    print("Recorded in %s" % os.path.basename(record))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(SALVAGE, "rejected-%s" % stamp)
    moved = 0
    for path in derived:
        for p in sorted(glob.glob(path)):
            if not os.path.exists(p):
                continue
            os.makedirs(dest, exist_ok=True)
            shutil.move(p, os.path.join(dest, os.path.basename(p)))
            moved += 1
    if moved:
        print("Moved %d derived file(s) to %s"
              % (moved, dest.replace(os.path.expanduser("~"), "~")))
        print("   kept: the uploads themselves, and anything from an authoritative source")

    forgotten = 0
    for pdf in forget_pdfs:
        try:
            forgotten += 1 if rxcache.forget(pdf) else 0
        except Exception:                                      # noqa: BLE001
            pass
    if forgotten:
        print("Dropped %d cached transcription(s) — a rejection withdraws the confirmation "
              "they were admitted under" % forgotten)

    ids = board_task_ids(include_archived=False)
    if ids:
        sh([HERMES, "kanban", "--board", BOARD, "archive"] + ids)
        print("Archived %d open card(s). Nothing further will dispatch." % len(ids))
    else:
        print("No open cards to archive.")

    print("\nThe review is halted and does not resume. Fix the input this names, then:")
    print("   python3 ~/.hermes/rx-review/rx.py reset --confirm")
    print("   ...re-upload / re-resolve, then `rx.py stage` and `rx.py start`.")
    return 0


def cmd_labs_reject(args):
    """HALT: the transcription is wrong.

    Not an exclusion. `--ignore` says "this value is right, do not research it" and the value
    still reaches the brief; this says the numbers cannot be trusted, and excluding a wrong
    value would publish it.

    Why halt rather than re-transcribe: the user saw one bad row, not the set of bad rows, and
    every downstream stage reasons about labs.md as a whole. Re-running the same cards over the
    same PDFs reproduces the same answer - it would be asking the model that misread the
    document to check its own reading. What has to change is the input or the method, and both
    are human decisions.
    """
    if not args.reason:
        print("Say why, in the user's words:  rx.py labs-reject --reason \"...\"")
        return 1
    pdfs = unique_pdfs(RAW)[0]
    return _halt(args, LABS_REJECTED, args.reason,
                 derived=[os.path.join(INPUTS, "labs-draft.md"),
                          os.path.join(INPUTS, "labs-complete.md"),
                          os.path.join(INPUTS, "labs-succinct.md"),
                          os.path.join(INPUTS, "labs-*.md")],
                 fingerprint=_labs_fingerprint(), forget_pdfs=pdfs)


def cmd_regimen_reject(args):
    """HALT: the inventory is wrong.

    For a draft that is wrong in a way answering cannot fix - the photo pass read the wrong
    bottles, a substance is in there the user does not take, half the regimen is missing.
    Answering the questions differently is `regimen-confirm`; this is for when the questions
    themselves are about the wrong things.

    regimen.txt and the product panels stay: the first is the user's own words and the second
    came from a manufacturer, neither is the reading being rejected.
    """
    if not args.reason:
        print("Say why, in the user's words:  rx.py regimen-reject --reason \"...\"")
        return 1
    return _halt(args, REGIMEN_REJECTED, args.reason,
                 derived=[os.path.join(INPUTS, "regimen-draft.txt"),
                          os.path.join(INPUTS, "regimen-final.md")])


def halted():
    """(record_path, reason) when the review was halted, else None."""
    for path in (LABS_REJECTED, REGIMEN_REJECTED):
        if not os.path.exists(path):
            continue
        reason = ""
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.lower().startswith("reason:"):
                reason = line.split(":", 1)[1].strip()
        return path, reason
    return None


# The per-item file a `Regimen Intake:` worker writes, and the numbered file the barrier gathers.
REGIMEN_ITEM_HEADER = "| Name | Ingredients | Quantity | Schedule | Started | Confidence |"
REGIMEN_FINAL_HEADER = "| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |"


def _regimen_item_path(name):
    """The per-item file this regimen item owns: inputs/regimen-item-<slug>.md.

    ONE file per regimen item, so the `Regimen Intake:` workers — which the dispatcher runs in
    PARALLEL — never share a write target. A shared regimen-final.md was appended to concurrently
    and the writes clobbered each other; each worker owning its own file removes the contention.
    The `Stage 3: Finalize Regimen` Barrier gathers every per-item file into regimen-final.md.
    """
    _slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return os.path.join(INPUTS, "regimen-item-%s.md" % _slug)


def _marker_question_path(name):
    """The question file an out-of-range marker owns: inputs/marker-question-<slug>.md.

    Stage 5 writes one per out-of-range marker; the `Stage 5: Labs Complete` Barrier gathers them
    into the batched review, and `marker-review` / `labs-accept` delete them as they are resolved.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return os.path.join(INPUTS, "marker-question-%s.md" % slug)


# A batched-review reply is parsed by CODE, never by the model. The number the user WROTE is the
# number recorded, so item N's answer can never land on item M — the misroute that once wrote one
# drug's dose onto two unrelated items. The model's only job is to hand the reply text to the
# script verbatim (via the reply file).
ACCEPT_WORDS = {"looks good", "looksgood", "lgtm", "accept", "accepted", "all good", "allgood",
                "all correct", "keep all", "keepall", "no changes", "nochanges", "good", "ok",
                "okay", "fine", "yes"}
CONFIRM_WORDS = {"confirm", "confirmed", "correct", "yes", "ok", "okay", "good", "right", "yep",
                 "keep", "keep it", "keepit", "fine", "as is", "asis", "significant", "y"}
DROP_WORDS = {"drop", "drop it", "dropit", "unknown", "unsure", "not sure", "notsure",
              "no idea", "noidea", "dont know", "don't know", "dontknow", "skip", "remove",
              "n/a", "na"}
IGNORE_WORDS = {"ignore", "ignore it", "ignoreit", "skip", "drop", "exclude", "not significant",
                "notsignificant", "benign", "no"}


_ACTION_WORDS = DROP_WORDS | IGNORE_WORDS | CONFIRM_WORDS


def _parse_numbered_reply(text):
    """Parse a batched-review reply into ({number: rest_text}, unparsed_lines).

    Three shapes are understood, and the number the user WROTE is always the key:
      * `N: rest`  — a number, a delimiter, then the answer/verb ('3: NOW brand, 1000mg', '7. drop it').
      * `N[, M …] [verb]` — a list of numbers with one shared action word or none ('2,5 ignore',
        'ignore 1 3', '3, 7'); each number gets that verb (or an empty rest).
    A line that is (or only contains) an accept phrase contributes nothing — the accept verb handles
    those. Anything else is returned as UNPARSED so the caller can refuse loudly rather than silently
    drop the user's intent. The caller validates each number against the review's index and refuses
    the whole batch on any miss, so a typo cannot misroute.
    """
    action_flat = {_flat(w) for w in _ACTION_WORDS}
    directives, unparsed = {}, []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line:
            continue
        if _flat(line) in ACCEPT_WORDS:
            continue
        m = re.match(r"^(\d+)\s*[:.)\-]\s*(.*)$", line)
        if m:
            directives[int(m.group(1))] = m.group(2).strip()
            continue
        # a list of numbers with an optional single shared verb, in either order
        nums = re.findall(r"\d+", line)
        residue = re.sub(r"[,;]", " ", re.sub(r"\d+", " ", line)).strip()
        if nums and (not residue or _flat(residue) in action_flat):
            for n in nums:
                directives.setdefault(int(n), residue)
            continue
        if any(w in _flat(line) for w in ACCEPT_WORDS):
            continue
        unparsed.append(line)
    return directives, unparsed


def _batch_reply_text(args, default_name):
    """Read the user's verbatim reply from --reply-file (default inputs/<default_name>).

    The model writes the reply to this file and runs the batch verb; passing the text through a
    file (not a shell arg) keeps arbitrary user prose off the command line and past the terminal
    allowlist. Returns (text, path); text is None when the file is missing.
    """
    path = getattr(args, "reply_file", None) or os.path.join(INPUTS, default_name)
    if not os.path.exists(path):
        return None, path
    return open(path, encoding="utf-8", errors="replace").read(), path


def _fit(text, limit=155):
    """Keep a notification reason inside the notifier's 160-char cut.

    gateway/kanban_watchers.py truncates block reasons to 160 chars before the Discord adapter
    (which does chunk correctly at 2000) ever sees them. Anything longer is lost mid-word, so
    reasons are written to fit and the detail is carried in the card body instead.
    """
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def _my_card_id():
    """The card THIS process is running as, or None when a person ran the command by hand.

    Hermes sets HERMES_KANBAN_TASK for a dispatcher-spawned worker. Creating a card is not what
    orders it - a card with no parents is `ready` the instant it exists - so a command that
    creates work for later has to name itself as that work's parent. A hand run has no card and
    therefore no edge to draw, which is what a person running a stage directly is asking for.
    """
    return os.environ.get("HERMES_KANBAN_TASK")


def _card_body(cid):
    """This card's instruction body, read through the Hermes CLI (never the DB), or "" if it cannot
    be read. Used to tell an auto-settle verb whether it is running AS this card's assigned command
    or was invoked ad hoc by a worker doing something else."""
    try:
        out = sh([HERMES, "kanban", "--board", BOARD, "show", cid, "--json"])
        return ((json.loads(out.stdout) or {}).get("task") or {}).get("body") or ""
    except Exception:                                          # noqa: BLE001
        return ""


def _hold(reason, detail_lines=(), dry=False):
    """Hold this stage: BLOCK the running card for a human, then tell the agent to stop.

    A backstop that only `return 1`s has no teeth. The card body's terminal action is the agent's,
    and a gated Begin body does "exactly what the final line says" — so a hold has to (1) move the
    card OUT of `running` itself, or the agent's clean exit is a protocol violation, and (2) make
    the final printed line say stop, so the agent does not kanban_complete over the problem. Block
    with kind=needs_input, which surfaces to the user; on reply and re-run the check passes and the
    stage proceeds. Returns 1 so a hand run still exits non-zero.
    """
    global _CARD_ACTED
    print("HELD — %s\n" % reason)
    for ln in detail_lines:
        print("   %s" % ln)
    mine = _my_card_id()
    if mine and not dry:
        sh([HERMES, "kanban", "--board", BOARD, "block", mine, _fit(reason),
            "--kind", "needs_input"])
        # TELL THE USER. `needs_input` alone reaches nobody: cards on this board are not
        # subscribed, so a hold sat on the board until someone happened to look — the pipeline
        # stopped and the only person who could clear it was never told (Stage 6, 2026-08-10).
        # The stage barriers already post their reviews directly; a hold does the same. It
        # REPORTS, it does not ask: the fix is an operator action, and the two batched questions
        # (Stage 3, Stage 5) remain the only things the pipeline asks a human.
        send_detail("rx-review HELD — %s\n\n%s\n\nThe pipeline has stopped and is waiting on you."
                    % (reason, "\n".join(str(ln) for ln in detail_lines)))
        _CARD_ACTED = True
        # THE READER IS THE MODEL, so the human is named in the third person. "BLOCKED for you to
        # fix" told the model to fix what only a person can, and a model that answers a review on
        # the user's behalf does not stall the run — it puts an approval nobody gave in the brief.
        print("\nThis card is now held for the user. Tell them what it says above, and stop.")
    else:
        print("\n(hand run — not a card; fix the above and re-run.)")
    return 1


# Set to True the moment _complete_self()/_hold() moves THIS card out of `running`. The dispatcher
# (main) reads it: a verb that already settled its own card is left alone; one that just returned is
# completed (rc 0) or blocked (rc 1) centrally, so no card body ever needs to call kanban_complete.
_CARD_ACTED = False


def _complete_self(summary="", dry=False):
    """Complete THIS card from the script — the worker-protocol terminal action a verb can own so
    the model never has to call kanban_complete. No-op on a hand run (no card)."""
    global _CARD_ACTED
    mine = _my_card_id()
    if mine and not dry:
        cmd = [HERMES, "kanban", "--board", BOARD, "complete", mine]
        if summary:
            cmd += ["--summary", summary]
        sh(cmd)
        _CARD_ACTED = True
    return mine


def create(args, title, body, minutes, priority, parents=(), key=None, assignee="rx-intake"):
    """Intake card rooted in inputs/. Mechanics live in rxkanban.

    Parents are normalised HERE so a caller can write the edge it means - `[_my_card_id(), x]` -
    without a comprehension at every call site to strip the None of a hand run or the "DRY" of a
    preview. Those comprehensions were how a parent list quietly became `[]`.
    """
    parents = [p for p in parents if p and not rxkanban.is_dry(p)]
    tid = rxkanban.create_card(
        title, assignee, body, INPUTS, parents=parents, runtime="%dm" % minutes,
        priority=priority, key=key or ("rx-" + rxkanban.slugify(title)),
        dry=args.dry_run)
    if args.dry_run:
        print("   would create: %-50s %3dm" % (title[:50], minutes))
    else:
        print("   %s  %-50s %3dm" % (tid, title[:50], minutes))
    return tid


def _card_id_by_title(title):
    """Id of an unfinished card whose title exactly equals `title`, or None.

    Used to splice a worker in front of a Barrier the pipeline created up front. A finished
    Barrier cannot be held back, so only unfinished cards are candidates.
    """
    out = sh([HERMES, "kanban", "--board", BOARD, "list", "--json"])
    try:
        tasks = json.loads(out.stdout)
    except Exception:                                          # noqa: BLE001
        return None
    for t in tasks:
        if (t.get("title") or "") == title \
                and t.get("status") not in ("done", "archived", "cancelled", "failed"):
            return t.get("id")
    return None


def _parent_worker_to_barrier(worker_id, barrier_title, dry=False):
    """Make `worker_id` a parent of the named Barrier, so the Barrier waits on it.

    `kanban link A B` makes A a parent of B. A worker created inside a stage is linked in front
    of that stage's Barrier before its creator completes, so the next stage's Begin — sitting
    behind the Barrier — cannot start while the worker is outstanding.
    """
    if not worker_id or rxkanban.is_dry(worker_id) or dry:
        return
    bid = _card_id_by_title(barrier_title)
    if bid:
        sh([HERMES, "kanban", "--board", BOARD, "link", worker_id, bid])
        print("   linked %s in front of %s (%s)" % (worker_id, barrier_title, bid))
    else:
        print("   ! could not find Barrier %r to link %s in front of"
              % (barrier_title, worker_id))


def regimen_items():
    """Every supplement and medication named in regimen-draft.md, in table order.

    The stage-3 worker set is one `Regimen Intake:` per item, so this is the enumeration that
    decides how many workers stage 3 creates. Column labels, blanks and parenthetical
    placeholders are skipped.
    """
    draft = os.path.join(INPUTS, "regimen-draft.md")
    if not os.path.exists(draft):
        return []
    header, rows = _first_table(open(draft, encoding="utf-8").read())
    if not header or "product" not in header:
        return []
    i_prod = header.index("product")
    out, seen = [], set()
    for cells in rows:
        name = cells[i_prod] if i_prod < len(cells) else ""
        if not name or name.startswith("(") or name.lower() in header:
            continue
        if name.lower() in ("product", "brand", "unit", "type"):
            continue
        if _flat(name) in seen:
            continue
        seen.add(_flat(name))
        out.append(name)
    return out


# ── card bodies ────────────────────────────────────────────────────────────

# The model-facing transcribe card. It speaks ONLY the domain: read a results file, write a table,
# add nothing that is not in the results. No source filename (which primes a weak model to invent a
# panel from the name), no PDF/page/split bookkeeping, no extraction command — the pipeline extracts
# the text and stamps provenance itself. The card ends by running a script that VERIFIES the table
# against the source and completes or blocks the card; the model never touches kanban mechanics.
TRANSCRIBE_BODY = """The lab results printed below are the complete and only source for this task.
Write one Markdown table row per printed result to {outfile} — copy each result exactly as printed,
and add NO row that is not printed below:

    | marker | value | unit | reference range | specimen | date |

Keep the lab's flag on the value (e.g. `186 H`). Write UNREADABLE for any value that is not printed
below. Set specimen to the panel heading above the row. When the table is written, run:

    python3 ~/.hermes/rx-review/rx.py check-transcription {token}

Then do exactly what it prints. Do nothing else.

--- results ---
{results}
"""

# The results ride INLINE in the card body — the worker never opens a file to read them — so a page
# range is sized to leave the whole transcribe body inside the 8KB card cap. INLINE_BUDGET is the
# bytes left for the results after the template, its output path, and the token.
_TRANSCRIBE_OVERHEAD = len(TRANSCRIBE_BODY.format(outfile="I" * 96, token="t" * 12, results="").encode())
INLINE_BUDGET = 8 * 1024 - _TRANSCRIBE_OVERHEAD - 64

# Adjacent windows overlap by this many WHOLE lines. It must exceed the tallest single result block
# (marker → value → unit → range → specimen, plus a multi-tier reference range) so a reading that
# straddles a window boundary still sits wholly inside at least one window and is transcribed intact.
LINE_OVERLAP = 12

LAB_PLAN_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/.hermes/rx-review/rx.py plan-lab {token}
"""


# ── the spine: a DAG created up front ─────────────────────────────────────────
#
# `rx.py start` creates every stage's Begin/Barrier pair in one pass; execution order is edges in
# that graph, not code a card runs (see cmd_start and its `begin_after` map, where the topology
# lives). The regimen branch (2→3) and the labs branch (4) run in parallel; Stage 5 waits on BOTH
# the Stage 4 and Stage 3 Barriers, so the marker review is never posted while the regimen review
# is open — one human question at a time, held by an edge; Stage 6 joins the two branches.


REGIMEN_BODY = """Write {tilde}/regimen-draft.txt — one line per product the user takes:

    product | brand | quantity | schedule | started

Copy what the regimen below says. Every line keeps its four `|`, and a field the regimen does
not give is left empty:

    product    as written
    brand      as written, or empty
    quantity   how much the user takes — 1 capsule, 1 shot, 1 pill, 5g scoop — or empty
    schedule   when they take it — morning / noon / evening / weekly / as needed
    started    when the user STARTED taking it — a month or date (e.g. 2026-04 or 2026-04-01) —
               or empty when not stated (supplements are usually empty)

Patient-fact lines (a `Name:`, `Age:` or `DOB:` line) are NOT products — never transcribe one
into the draft. Schedule headings (WEEKLY, MORNING, NOON, EVENING, …) are likewise not products.

kanban_complete with metadata: {{"products": N}}. Do nothing else.

--- the user's regimen ---
{regimen}
"""


REGIMEN_INTAKE_BODY = """Write {itemfile} with the label ingredients of {name}. The user takes {quantity}, {schedule}.
{brand_note}
Find its label — AT MOST two searches, then decide:
    python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "{name} supplement facts" --scope products --timeout 60
    python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "URL" --timeout 60

A search hit that is a DIFFERENT product (different strength, a combination, another form) is not
a match — do not keep hunting for a closer one. Decide from what you have:
  * A label for exactly this product → Ingredients from it, Confidence `high`.
  * No exact-match label after two searches (typical for generic ingredients with no brand, e.g.
    plain vitamin C or aspirin — the ingredient and strength are still well established) →
    write the ingredient and amount carried by the name itself, source "product name; no brand
    label found", Confidence `low`. That is a COMPLETE answer, not a failure.

Write {itemfile} as this table. Ingredients = the active ingredients and serving size; Quantity =
what the user takes ({quantity}); Started = when they began taking it ({started}) — copied as
given, never looked up:
    | Name | Ingredients | Quantity | Schedule | Started | Confidence |
    |---|---|---|---|---|---|
    | {name} | active ingredients and serving size | {quantity} | {schedule} | {started} | high |

kanban_complete. Do nothing else.
"""


# ── commands ───────────────────────────────────────────────────────────────

def cmd_intake_regimen(args):
    """STAGE 2 of 8. Read the regimen into regimen-draft.txt.

    One worker: `Worker: Read regimen` is HANDED the regimen text in its card body and transcribes
    it into regimen-draft.txt as one pipe-delimited line per product — `product | brand | quantity |
    schedule | started`. The regimen is always text and always ONE file — a Google Doc, an attached text file,
    or the message itself, each landing in regimen.txt — so there is nothing to classify and no
    lookup: labels are stage 3. The worker is linked in front of the `Stage 2: Regimen Read` Barrier.
    """
    me = _my_card_id()

    # NO REGIMEN IS AN ERROR, refused before any card exists — the mirror of the labs refusal in
    # stage 4.
    text = ""
    if os.path.exists(regimen_path()):
        text = open(regimen_path(), encoding="utf-8", errors="replace").read().strip()
    if not text:
        print("NO REGIMEN — there is nothing describing what the user takes.")
        print("   looked for: regimen.txt in %s" % INPUTS.replace(os.path.expanduser("~"), "~"))
        print("\nResolve it and run this again, whichever the user offered:")
        print("   python3 ~/.hermes/rx-review/rx.py regimen --from-gdoc <doc-id>")
        print("   python3 ~/.hermes/rx-review/rx.py regimen --from <path>")
        print("\nA review of no substances is not a shorter review: the brief exists to relate")
        print("substances to lab markers.")
        return 1

    # HANDED, NOT FETCHED. The worker gets the text itself, so it opens no file and names no
    # path — a path in a card body is a literal the worker must reproduce, and one reproduced
    # wrongly stalled the lab branch for hours on 2026-08-10.
    body = REGIMEN_BODY.format(tilde=TILDE, regimen=text)
    if len(body.encode()) > KANBAN_BODY_CAP:
        # Refused before any card exists, like the other stage 1 and 2 refusals. A regimen too
        # large to inline is far more likely to be the wrong document than a real one, and a
        # fallback to a file read would restore the second code path inlining exists to remove.
        print("REGIMEN TOO LARGE to carry in a card body: %s bytes of text, cap is %s."
              % (format(len(text.encode()), ","), format(KANBAN_BODY_CAP, ",")))
        print("   %s" % regimen_path().replace(os.path.expanduser("~"), "~"))
        print("\nA regimen this size is usually the wrong document. Check what was captured with")
        print("the user, resolve it again, and run this again.")
        return 1

    print("Regimen: %s bytes, carried in the card body." % format(len(text.encode()), ","))
    # KEYED ON THE CONTENT, not a constant. The body is data now: with a constant key a corrected
    # regimen would return the existing card and be silently ignored, leaving a card whose text
    # disagrees with regimen.txt. Keyed on the text, a correction is a different card and an
    # unchanged regimen is a free no-op.
    worker = create(args, "Worker: Read regimen", body, 90, 100,
                    parents=[me], assignee="rx-intake",
                    key=stable_key("rx-read-regimen", hashlib.sha1(text.encode()).hexdigest()))
    _parent_worker_to_barrier(worker, "Stage 2: Regimen Read", dry=args.dry_run)
    print("\nStage 2 of 8: 1 worker (`Worker: Read regimen`) created, linked in front of the "
          "`Stage 2: Regimen Read` Barrier.%s" % ("  (DRY RUN)" if args.dry_run else ""))
    return 0


def _draft_regimen_rows():
    """Each supplement/medication row of regimen-draft.txt as a 5-tuple.

    Returns (name, brand, quantity, schedule, started). The draft is one pipe-delimited line per
    product — `product | brand | quantity | schedule | started`. Outer pipes are optional and the
    split is from the right, so a product name stays whole. `name` is <brand> <product> when a
    real brand is given, else <product>; `brand` is the raw brand field ("" for generics — the
    generic-ness itself is information Stage 3's lookup cards need). A header line, blanks and
    parentheticals are skipped; deduped by name.
    """
    draft = os.path.join(INPUTS, "regimen-draft.txt")
    if not os.path.exists(draft):
        return []
    out, seen = [], set()
    for line in open(draft, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "(")):
            continue
        parts = [c.strip() for c in line.strip("|").rsplit("|", 4)]   # outer pipes optional
        if len(parts) < 4:
            continue
        product, brand, quantity, schedule = parts[:4]
        started = parts[4] if len(parts) >= 5 else ""
        if not product or product.lower() == "product":       # skip a header line
            continue
        if (brand in ("", "—", "-", "–", "N/A", "n/a") or brand.upper() == "UNREADABLE") \
                or _flat(product).startswith(_flat(brand)):
            name, brand = product, ""                         # no brand, or product already carries it
        else:
            name = "%s %s" % (brand, product)                 # Name is <brand> <product>
        if _flat(name) in seen:
            continue
        seen.add(_flat(name))
        out.append((name, brand, quantity, schedule, started))
    return out


def cmd_intake_regimen_items(args):
    """STAGE 3 of 8. Create one `Regimen Intake:` worker per supplement and medication.

    Enumerates the rows of regimen-draft.txt and creates one `Regimen Intake: <name>` worker per
    row — each researches its item's ingredients and dose and writes its own
    inputs/regimen-item-<slug>.md, linked in front of the `Stage 3: Finalize Regimen` Barrier and
    capped at 2 minutes of wall-clock. Workers run in parallel; each owns its own file so they
    never clobber a shared one.

    Idempotent: workers are keyed per item slug, so re-running is stable. It works on whatever
    stage 2 produced — stage 2 already refused if there was nothing.
    """
    rows = _draft_regimen_rows()
    if not rows:
        print("No regimen items found in regimen-draft.txt yet.")
        print("   (stage 2 already refused if there was nothing to read)")
    made = 0
    for name, brand, quantity, schedule, started in rows:
        slug = product_slug(name)
        itemfile = "%s/%s" % (TILDE, os.path.basename(_regimen_item_path(name)))
        # A row with no brand is a GENERIC product: say so in the card. The lookup worker cannot
        # infer it, and without the note it hunts for a canonical label that does not exist —
        # 41 tool calls in 4 minutes on "Vitamin C 100mg" (2026-08-23), three timeouts, card
        # blocked. Deterministic from the draft row; no product knowledge lives here.
        brand_note = ("\nNOTE: no brand was given — this is a GENERIC ingredient product. Do NOT"
                      "\nsearch for a brand or a specific manufacturer's label; go straight to the"
                      "\ntwo-search budget below and expect to finish at Confidence `low`.\n"
                      ) if not brand else ""
        # No Begin parent: this verb IS what creates the worker, so the worker cannot exist before
        # its stage runs — the edge it would add is always already satisfied. It becomes ready the
        # moment it is created and researches in parallel; the barrier edge below is what holds
        # stage 4 back until every item is in.
        wid = create(args, "Regimen Intake: %s" % name,
                     REGIMEN_INTAKE_BODY.format(name=name, quantity=quantity or "as written",
                                                schedule=schedule or "as needed",
                                                started=started or "(not stated)", itemfile=itemfile,
                                                brand_note=brand_note),
                     # 4 min, not 2: an intake does a web search + fetch + write, and at 2 min five
                     # of them timed out at 120-122s on 2026-08-12 (a cold cache made it worse).
                     4, 110, assignee="rx-research",
                     key=stable_key("rx-regitem", slug))
        _parent_worker_to_barrier(wid, "Stage 3: Finalize Regimen", dry=args.dry_run)
        made += 1
    print("\nStage 3 of 8: %d `Regimen Intake:` worker(s) created (each linked in front of the "
          "`Stage 3: Finalize Regimen` Barrier).%s"
          % (made, "  (DRY RUN)" if args.dry_run else ""))
    return 0


def cmd_intake_labs(args):
    """STAGE 4 of 8. Transcribe every staged lab PDF, then merge and condense them.

    Its Begin starts as soon as Stage 1 completes — in PARALLEL with the regimen branch (Stages
    2-3), not after it — because transcription is by far the longest work and there is no reason
    to hold it behind the regimen. Stage 6 joins the two branches: it waits on both the Stage 3
    and Stage 5 Barriers, so nothing downstream sees labs before the regimen is settled.
    """

    # Refuse to plan transcription over a partial set. Every PDF Hermes received is knowable,
    # so "the assistant forgot to copy one" should never be a thing the user discovers later
    # from a report that quietly omits a panel. Stage 1 holds for the same reason; this is the
    # same check for a document that arrived after staging ran.
    _unstaged = unstaged_documents()
    if _unstaged and not getattr(args, "force", False):
        print("HELD — %d PDF(s) Hermes received have not been staged:" % len(_unstaged))
        for _f in _unstaged[:10]:
            print("   %s" % os.path.basename(_f))
        if len(_unstaged) > 10:
            print("   ... and %d more" % (len(_unstaged) - 10))
        print("\nStage them first:  python3 ~/.hermes/rx-review/rx.py stage")
        print("Starting now would review a partial set and say nothing about it.")
        return 1

    # ZERO STAGED PDFs IS AN ERROR, and --force does not override it: there is nothing to force.
    # Two of the three research families are keyed on markers and the safety part of every
    # substance card reads the labs, so a run with none would complete, produce a document that
    # looks exactly like the real output, and be missing the half that justified it. The
    # overwhelmingly likely cause is that staging did not pick the documents up - which is the
    # failure stage 1 was split out to catch - so failing loudly sends the user back to upload.
    if not unique_pdfs(RAW)[0]:
        print("NO LAB PDFs — nothing staged to transcribe.")
        print("   looked in: %s" % RAW.replace(os.path.expanduser("~"), "~"))
        print("\nRun `python3 ~/.hermes/rx-review/rx.py stage` first. A review with no labs is")
        print("not a supplements-only brief; it is an upload that did not arrive.")
        return 1

    made = 0
    pdfs, _dupe_pdfs = unique_pdfs(RAW)
    # this stage is the only writer of raw/, so it alone quarantines; a moved stray
    # transcription means labs.md summarises a set that no longer exists.
    quarantine_duplicates(RAW, _dupe_pdfs)

    # One `Lab: <file>` card per PDF — a per-document owner. Each runs `plan-lab`, which does the
    # OCR-detect + split-plan for its one document and creates that PDF's transcription child
    # card(s). The `Lab:` card is a parent of the Barrier, so the Barrier waits for it to register
    # its children (also Barrier parents) before it can complete. No PDF is opened here — plan-lab
    # does that — so this stage needs no PDF library and stays runnable where one is absent.
    lab_card_ids = []
    print("Labs:")
    if not pdfs:
        print("   no PDFs in inputs/raw/")
    for pdf in pdfs:
        base = os.path.basename(pdf)
        # No Begin parent: this verb (the Stage 4 Begin) creates the `Lab:` card, so an edge back
        # to it is always already satisfied and only delays it. Parentless, each per-PDF owner is
        # eligible the moment it exists; the barrier edge below is what holds Stage 5 back.
        # RECORD THE BINDING HERE, AND PASS A TOKEN — never the path. This verb is holding the
        # document as it creates the card, so this is the one place the binding can be written
        # without anyone re-deriving it. A path in a card body is a ~110-character literal the
        # worker must copy verbatim: on 2026-08-10 one came back with `kanban/rx-review/` spliced
        # into the middle, plan-lab correctly reported no such PDF, the card was blocked, and the
        # whole lab branch stalled behind a card whose work the worker's own retry had finished.
        # A token is short and verifiable — a corrupted one matches nothing and says so, where a
        # corrupted path merely looks plausible.
        token = _doc_token(pdf)
        if not args.dry_run:
            _xcribe_put(token, {"pdf": pdf})
        lid = create(args, "Lab: %s" % base, LAB_PLAN_BODY.format(token=token), 20, 90,
                     key="rx-lab-plan-" + _lab_slug(pdf))
        _parent_worker_to_barrier(lid, "Stage 4: Labs Transcribed", dry=args.dry_run)
        lab_card_ids.append(lid)
        made += 1
        print("   %s" % base)

    if not args.dry_run and lab_card_ids:
        phase_start("labs",
                    "**Lab intake started** %s\n%d lab PDF(s) queued; each is transcribed on its "
                    "own card. I will report once they are all transcribed and merged."
                    % (time.strftime("%H:%M"), len(lab_card_ids)))

    print("\nStage 4 of 8: %d `Lab:` card(s), each linked in front of the "
          "`Stage 4: Labs Transcribed` Barrier.%s"
          % (len(lab_card_ids), "  (DRY RUN)" if args.dry_run else ""))
    print("\n%d card(s) created.%s" % (made, "  (DRY RUN)" if args.dry_run else ""))
    if made and not args.dry_run:
        print("Watch:  python3 %s status" % __file__)
    return 0


def _resolve_plan_lab_document(args):
    """The document this `plan-lab` run owns, or None having said what to do about it.

    Three callers, answered in their own terms:
      * a `Lab:` card, which names its token — resolved through the token's record;
      * an operator debugging by hand with the hidden `--pdf`;
      * a worker that dropped its token, which is what a bare run almost always is. It is asked
        for a TOKEN and nothing else: naming a document would hand back the one concept the
        token exists to keep out of the worker's reach, and there is no document to name anyway,
        since without a token nothing here knows which one was meant.

    Never a hold. Every case is the caller's to fix by running the command properly, and a card
    blocked for that is stranded even after a good retry (see `_hold`).
    """
    if getattr(args, "pdf", None):                             # hand run, operator's terms
        return os.path.expanduser(args.pdf)
    token = (getattr(args, "token", None) or "").strip()
    if not token:
        print("This command needs the token from this card's body:")
        print("\n    python3 ~/.hermes/rx-review/rx.py plan-lab <token>\n")
        print("Re-read the card body, copy the token exactly, and run it again.")
        return None
    rec = _xcribe_get(token)
    if not rec or not rec.get("pdf"):
        print("No record for token %s." % token)
        print("\nRe-read this card's body, copy the token exactly, and run it again:")
        print("\n    python3 ~/.hermes/rx-review/rx.py plan-lab <token>")
        return None
    return rec["pdf"]


def cmd_plan_lab(args):
    """STAGE 4 per-PDF card. Prepare ONE lab PDF and create its transcription child card(s).

    Runs as a `Lab: <file>` card. Reads the PDF's text layer; a text-less scan is OCR'd to a
    searchable PDF via the OCR service and read instead (if that fails, holds the card and
    reports to chat — the caller cannot fix it, so the run stops). Flattens the text to
    furniture-free result lines, splits those into overlapping
    line-windows each small enough to inline whole into a card body, and creates one
    `Transcribe Lab` child per window — each child parented on this card and set as a parent of
    the `Stage 4: Labs Transcribed` Barrier. Idempotent: children are keyed per slug/window.
    """
    import fitz

    me = _my_card_id()
    pdf = _resolve_plan_lab_document(args)
    if pdf is None:
        return 1                                               # already explained; NOT a hold
    base = os.path.basename(pdf)
    slug = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(base)[0].lower()).strip("-")
    if not os.path.exists(pdf):
        # The record names a document that is no longer staged — a person removed or renamed it,
        # which no re-run fixes.
        return _hold("the document this card was given is no longer in inputs/raw/",
                     ["- %s" % base,
                      "",
                      "Re-stage it, or re-run `rx.py intake-labs` to rebuild the lab cards."],
                     dry=args.dry_run)

    out = os.path.join(INPUTS, _lab_out_name(pdf))
    if not stale(out, pdf):
        print("up to date: %s — already transcribed" % base)
        return 0
    # A verified transcription of this exact document already exists (matched on CONTENT), so no
    # child is created and the values cannot drift.
    hit = rxcache.get(pdf)
    if hit:
        text, meta = hit
        open(out, "w", encoding="utf-8").write(text)
        print("cached: %s  (%d verified value(s), confirmed %s)"
              % (base, meta.get("values_verified", 0), meta.get("confirmed_by", "?")))
        return 0

    d = fitz.open(pdf)
    texts = [p.get_text() for p in d]
    pages, chars = d.page_count, sum(len(t) for t in texts)
    read_pdf = pdf                          # what the child extracts and planning reads
    if chars < 200:
        # Text-less scan: OCR it to a searchable PDF and read that instead. If the OCR service
        # is unavailable or recovers nothing, hold the card and report to chat — the caller
        # cannot fix it, so the run stops rather than skipping the document.
        ocr_pdf = _ocr_pdf_path(pdf)
        if rxsplit.ocr_to_searchable(pdf, ocr_pdf):
            d.close()
            d = fitz.open(ocr_pdf)
            texts = [p.get_text() for p in d]
            pages, chars = d.page_count, sum(len(t) for t in texts)
            read_pdf = ocr_pdf
            print("OCR'd: %s — recovered %d char(s) from a scan" % (base, chars))
        if chars < 200:
            return _hold(
                "%s has no text layer and OCR recovered none" % base,
                ["- the OCR service is unavailable, or the scan produced no text",
                 "",
                 "Restore the OCR service (or re-upload a text-based PDF), then re-run this",
                 "card and unblock it."],
                dry=args.dry_run)

    def _mins(nchars, npages):
        return int(max(MIN_MINUTES, min(MAX_MINUTES,
                   round(max(nchars / CHARS_PER_MINUTE, npages * 1.5) / 5.0) * 5)))

    made = 0
    lines = _flat_lines(texts)
    windows = _line_windows(lines, INLINE_BUDGET, LINE_OVERLAP)
    multi = len(windows) > 1
    if multi:
        print("split: %s — %d result line(s) into %d overlapping window(s) within the card cap"
              % (base, len(lines), len(windows)))
    if not args.dry_run:
        os.makedirs(XCRIBE, exist_ok=True)
    for idx, (first, last) in enumerate(windows, 1):
        rng = (first, last) if multi else None
        rout = os.path.join(INPUTS, _lab_out_name(pdf, rng))
        if multi and not stale(rout, pdf):
            print("   up to date: %s" % os.path.basename(rout))
            continue
        # The PIPELINE flattens the PDF to furniture-free result lines and inlines THIS window of
        # them in the card body, so the worker reads the printed results straight from its
        # instructions — no filename, no extraction command, no page or line bookkeeping reaches
        # it. The same text is stashed for the verifier; check-transcription confirms every row.
        token = hashlib.sha1(("%s|%d-%d" % (read_pdf, first, last)).encode()).hexdigest()[:12]
        srcfile = os.path.join(XCRIBE, token + ".src.txt")
        tblfile = os.path.join(XCRIBE, token + ".tbl.md")
        results = _window_text(lines, first, last)
        if not args.dry_run:
            open(srcfile, "w", encoding="utf-8").write(results)
            _xcribe_put(token, {"pdf": read_pdf, "first": first, "last": last, "out": rout})
        _body = TRANSCRIBE_BODY.format(outfile=tblfile, token=token, results=results)
        _mn = _mins(len(results), 1)
        # Two literal-titled create() calls (not one computed title) so the card map can see them.
        if multi:
            cid = create(args, "Transcribe Lab %s (part %d)" % (base, idx),
                         _body, _mn, 90, parents=[me],
                         key="rx-lab-%s-%s" % (slug, _win_tag(first, last)))
        else:
            cid = create(args, "Transcribe Lab %s" % base, _body, _mn, 90, parents=[me],
                         key="rx-lab-" + slug)
        _parent_worker_to_barrier(cid, "Stage 4: Labs Transcribed", dry=args.dry_run)
        made += 1

    print("\n%d transcription card(s) created for %s.%s"
          % (made, base, "  (DRY RUN)" if args.dry_run else ""))
    return 0


# A page footer/header — "Page 4 of 13", "PAGE 4 OF 13", or the "[…Report OZ776061F-1.pdf…]"
# identity line a report prints on every page. It is not a result, and the filename inside it is
# exactly what primed a fabricated panel, so it is stripped before the text ever reaches a window.
_PAGE_FURNITURE = re.compile(r"page\s+\d+\s+of\s+\d+", re.I)
# A report-identity line names the report by a code, e.g. "Enhanced PDF Report OZ776061F-1". It is
# a section header, not a result, and only appears on some pages so repeated-chrome detection can
# miss it — an explicit pattern catches "Report <code containing a digit>".
_REPORT_IDENTITY = re.compile(r"\breport\s+[A-Za-z]*\d[A-Za-z0-9-]{2,}", re.I)


def _strip_page_furniture(text):
    """Drop page-marker and report-identity lines, keeping every result line — INCLUDING a bare
    numeric value, which must never be treated as chrome.

    A short line is furniture when it carries a `Page N of M` marker, names a `.pdf` file, points at
    an appendix, or is a report-identity line ("Enhanced PDF Report OZ776061F-1"). Left in, that
    identity line is transcribed as a fabricated marker that fails verification and blocks the run.

    Repeated-chrome (`boilerplate`) detection is deliberately NOT used here: it digit-blanks a line,
    so every value like `13.8` collapses to `#.#`, which recurs on most pages and would be stripped
    as chrome — deleting the entire value column. That regression is exactly what this note guards."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and len(s) <= 120 and (_PAGE_FURNITURE.search(s)
                                    or _REPORT_IDENTITY.search(s)
                                    or ".pdf" in s.lower()
                                    or "see appendix" in s.lower()):
            continue
        out.append(line)
    return "\n".join(out)


def _flat_lines(texts):
    """One PDF's non-reference pages flattened to a single furniture-free list of result lines.

    Pages are concatenated, page markers and filename/appendix/identity references stripped, prose
    and chart axes decluttered, and blank lines dropped — so what remains is a plain top-to-bottom
    run of result lines (values included) with no page concept, ready to be windowed by line count."""
    pages = [texts[n - 1] for n in range(1, len(texts) + 1)
             if not rxsplit.is_reference_page(texts[n - 1])]
    kept, _removed = rxsplit.declutter(_strip_page_furniture("\n".join(pages)))
    return [l for l in kept.splitlines() if l.strip()]


def _window_text(lines, first, last):
    """The inlined text for a line window — lines first..last, 1-indexed and inclusive."""
    return "\n".join(lines[first - 1:last])


def _line_windows(lines, budget, overlap):
    """Overlapping windows of WHOLE lines, each fitting `budget` bytes, as (first, last) 1-indexed
    inclusive ranges. A window grows one whole line at a time until the next line would exceed the
    budget — a line is never split — and the next window begins `overlap` lines back, so a reading
    that straddles a boundary sits wholly inside at least one window (overlap must exceed the
    tallest single result block). A lone line larger than the budget stands as its own window
    rather than being cut. Empty input yields one empty window so the Lab card still has a child."""
    n = len(lines)
    if n == 0:
        return [(1, 0)]
    windows, start = [], 1                                    # 1-indexed
    while start <= n:
        last = start
        while last < n and len(_window_text(lines, start, last + 1).encode()) <= budget:
            last += 1
        windows.append((start, last))
        if last >= n:
            break
        nxt = last - overlap + 1
        start = nxt if nxt > start else last + 1              # always make progress
    return windows


def _doc_token(pdf):
    """The token standing for one staged document. DETERMINISTIC, so `intake-labs` is idempotent.

    A random token would write a new card body on every run while the idempotency key returned
    the ORIGINAL card — body and record disagreeing, with the card naming a token nothing had
    recorded. Derived from the basename rather than the full path so a document keeps its token
    if the tree moves.
    """
    return hashlib.sha1(os.path.basename(pdf).encode()).hexdigest()[:12]


def _xcribe_record_path(token):
    return os.path.join(XCRIBE, "%s.json" % token)


def _xcribe_put(token, entry):
    """Record this token's binding as ITS OWN file, so concurrent writers never contend.

    ONE FILE PER TOKEN, never a shared manifest. `Lab:` cards are parentless and therefore all
    eligible at once, and the dispatcher runs `max_in_progress` of them together — so several
    `plan-lab` processes write here simultaneously. The previous shared `manifest.json` was
    read-modify-write: the publishing rename was atomic, the read-modify-write around it was not,
    and a sibling's entry written in between was simply lost — six times in the run of
    2026-08-10, each surfacing much later as a token `check-transcription` could not find. It
    also gave every writer the same temp path (so a malformed publish was possible) and treated
    an unreadable manifest as an empty one (so the next writer erased every prior entry). A file
    per token has exactly one writer and removes all three.
    """
    os.makedirs(XCRIBE, exist_ok=True)
    p = _xcribe_record_path(token)
    tmp = "%s.%d.tmp" % (p, os.getpid())                       # per-process: no shared temp name
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entry, fh)
    os.replace(tmp, p)


def _xcribe_get(token):
    """This token's record, or None. A token that names nothing is a caller error, not a hold."""
    if not token or "/" in token or os.path.sep in token:      # a token is a name, never a path
        return None
    try:
        with open(_xcribe_record_path(token), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _parse_transcription_rows(path):
    """The (marker, value, unit, range, specimen, date) rows from the model's table file."""
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in _fold_escaped_pipes(s).strip("|").split("|")]
        low = [c.lower() for c in cells]
        if "marker" in low and "value" in low:                 # header row
            continue
        if cells and all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):   # separator
            continue
        if not cells or not cells[0]:
            continue
        rows.append(tuple((cells + [""] * 6)[:6]))
    return rows


def _row_in_source(marker, value, source_text):
    """(ok, reason) — does this transcribed row actually appear in the source results text?

    Mirrors check_labs: the marker NAME must occur (a trailing qualifier is dropped for a second
    try), and the value (minus its lab flag) must occur. An explicitly UNREADABLE value passes."""
    if not _marker_in_source(marker, _flat(source_text)):
        return False, "marker name not in the results"
    if (value or "").upper() == "UNREADABLE":
        return True, ""
    # WHITESPACE- and CASE-insensitive: the source prints a marker, its value, and its unit on
    # SEPARATE lines, and the LLM correctly folds them into one cell ("3.6 mg/dL"). A raw substring
    # check rejected that (and "Positive" vs "POSITIVE"). Collapsing whitespace and case keeps the
    # match tolerant of line wraps and spacing while STILL catching a changed digit ("3.6" != "36").
    if not _value_in_text(value, source_text):
        return False, "value %r not in the results" % value
    return True, ""


def _value_in_text(value, text):
    """True when a transcribed value (minus its lab flag) occurs in the source, ignoring whitespace
    and case but NOT digits/punctuation — so a two-line 'value + unit' still matches, a mis-keyed
    decimal does not. An empty value trivially matches."""
    needle = re.sub(r"\s+", "", value_without_flag(value).lower())
    return (not needle) or needle in re.sub(r"\s+", "", (text or "").lower())


def _write_check_log(token, verdicts, flat_src):
    """Record WHY each transcribed row passed or was rejected, so a rejection is inspected, not
    guessed at. One line per row in .xcribe/<token>.check.log; on a marker miss it also reports
    which of the name's words were found in the flattened source and which were not."""
    lines = []
    for (ok, why), marker, value in verdicts:
        tag = "OK    " if ok else "REJECT"
        extra = ""
        if not ok and why.startswith("marker"):
            words = re.findall(r"[a-z0-9]+", (marker or "").lower())
            missing = [w for w in words if w not in flat_src]
            extra = " | words_missing=%s" % (missing or "none (order/gap)")
        elif not ok:
            extra = " | needle=%r" % re.sub(r"\s+", "", value_without_flag(value).lower())
        lines.append("%s marker=%r value=%r%s%s"
                     % (tag, marker, value, (" | " + why) if why else "", extra))
    try:
        with open(os.path.join(XCRIBE, token + ".check.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _marker_in_source(marker, flat_src):
    """True when the marker NAME occurs in the alnum-flattened source, tolerant of how a PDF
    fractures a name. Three escalating tries:
      1. contiguous (the common case);
      2. the same with a trailing '(qualifier)' dropped;
      3. the marker's words as an ordered match with only a few stray characters allowed between
         them — a real report interleaves a value or a watermark fragment mid-name
         ('THYROID PEROXIDASE' <EN> 'ANTIBODIES'), which a contiguous match wrongly rejects.
    The ordered path keeps order and a tight gap, so a genuinely absent name still fails."""
    fm = _flat(marker)
    if not fm or fm in flat_src:
        return True
    base = _flat(re.sub(r"\s*\([^)]*\)\s*$", "", marker))
    if base and base in flat_src:
        return True
    words = re.findall(r"[a-z0-9]+", marker.lower())
    if len(words) >= 2:
        pat = r".{0,6}?".join(re.escape(w) for w in words)
        if re.search(pat, flat_src):
            return True
    return False


def _write_transcription_final(out, rows, source_file):
    """Write the per-doc labs file, stamping the source_file column the model never saw."""
    lines = ["| marker | value | unit | reference range | specimen | date | source file |",
             "|---|---|---|---|---|---|---|"]
    for marker, value, unit, rng, specimen, date in rows:
        cells = [marker, value, unit, rng, specimen, date, source_file]
        lines.append("| " + " | ".join((c or "").replace("|", r"\|") for c in cells) + " |")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cmd_check_transcription(args):
    """Verify a transcription against its source, then complete this card (script-owned).

    The model wrote a table from the results inlined in its card body. Here the pipeline confirms
    every row's marker and value actually appear in that source, writes the real labs-doc-*.md with
    the source file stamped, and completes the card. A fabricated row (not in the source) does NOT
    block: the command returns non-zero naming the rows, and the model removes them and re-runs it
    in the same turn. The model never touched a filename, the extraction, or kanban_complete.
    """
    ent = _xcribe_get(args.token)
    if not ent:
        print("No transcription manifest entry for %s — was `plan-lab` run for this document?"
              % args.token)
        return 1
    tbl = os.path.join(XCRIBE, args.token + ".tbl.md")
    src = os.path.join(XCRIBE, args.token + ".src.txt")
    if not os.path.exists(tbl):
        # NOT a block: writing the table is the model's job. Tell it, and let it re-run this command.
        print("No table written yet. Write it to %s, then re-run this command." % tbl)
        return 1
    source_text = open(src, encoding="utf-8", errors="replace").read() if os.path.exists(src) else ""
    rows = _parse_transcription_rows(tbl)
    flat_src = _flat(source_text)
    verdicts = [(_row_in_source(m, v, source_text), m, v) for m, v, *_ in rows]
    _write_check_log(args.token, verdicts, flat_src)
    problems = [(m, why) for (ok, why), m, v in verdicts if not ok]
    if problems and not getattr(args, "force", False):
        # A fabricated row is something the MODEL fixes (remove it), not a human — so DO NOT block.
        # Return non-zero with the offending rows; the card body's "do what it prints" makes the
        # model delete them and re-run this command IN THE SAME TURN, and completion follows on the
        # clean pass. A card that never gets clean stays running -> dispatcher retry -> triage.
        print("%d transcribed row(s) are NOT in the results and must be removed:\n" % len(problems))
        for m, w in problems[:15]:
            print("   - %-28s %s" % (m[:28], w))
        print("\nRemove those rows from the table (transcribe only rows printed in the results "
              "file), then re-run this command.")
        return 1
    base = os.path.basename(ent["pdf"])
    if not args.dry_run:
        _write_transcription_final(ent["out"], rows, base)
        mine = _my_card_id()
        if mine:
            sh([HERMES, "kanban", "--board", BOARD, "complete", mine,
                "--summary", "%d row(s) verified against the source" % len(rows)])
    print("%d row(s) verified and written. This card is complete — do nothing else." % len(rows))
    return 0


CRITICAL_COLS = ("amount per serving", "unit")


def product_slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def _variant_sections(body):
    """Variants written as `## Variant 1: <name>` sections rather than one table.

    The card asks for "every variant you found" without naming a format, so a worker writes
    what reads well: a heading per variant with the serving size in the prose beneath. A parser
    that only understood a `## Variants` TABLE returned nothing for it, so the question the user
    got was "Super EPA — amount per serving, unit" while the file underneath held both forms
    with their doses. The instruction now names a format; this keeps the ones already written,
    and any future worker who words it differently, readable.
    """
    heads = list(re.finditer(r"^##+\s*Variant\s*\d*\s*[:.\-]?\s*(.+?)\s*$", body, re.I | re.M))
    out = []
    for i, h in enumerate(heads):
        seg = body[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(body)]
        row = {"variant": h.group(1).strip()}
        for label in ("serving size", "amount per serving"):
            m = re.search(r"\*{0,2}%s\*{0,2}\s*[:：]\s*(.+)$" % label, seg, re.I | re.M)
            if m:
                # Take the whole line and strip markdown after the fact. Stopping the capture
                # at the first `*` truncated '2 gelcaps (**user's "1 pill" = half serving**)'
                # to '2 gelcaps (' - dropping the very warning that makes the choice matter.
                row[label] = re.sub(r"\*+", "", m.group(1)).strip()
                break
        # The differing DOSE is the thing the user is being asked to choose between, so carry
        # the first ingredient row of the variant's table when there is one.
        t = re.search(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", seg, re.M)
        if t and t.group(1).strip().lower() not in ("ingredient", "---"):
            row["amount"] = "%s %s %s" % (t.group(1).strip(), t.group(2).strip(),
                                          t.group(3).strip())
        out.append(row)
    return out


def check_regimen():
    """BACKSTOP: has stage 3 settled the regimen? Returns (unresolved, acknowledged).

    Stage 3's Barrier is what actually settles the regimen into regimen-final.md; this only
    catches a card reached out of order. Unresolved is empty once regimen-final.md exists with at
    least one data row; otherwise it names the one thing missing. acknowledged is always [] — the
    old per-item ack machinery is gone.
    """
    if _read_regimen_final_rows():
        return [], []
    return [{"item": "(regimen)",
             "why": "regimen-final.md missing or empty — stage 3 has not settled the regimen"}], []


def unique_pdfs(directory):
    """PDFs in `directory`, de-duplicated by CONTENT.

    The agent copies lab PDFs into the intake folder, and copying is not idempotent: every
    copy gets a fresh random `doc_<hash>_` prefix, so re-running never collides on filename.
    One failed regimen step, one retry, and the whole set lands twice - five labs became ten,
    labs.md reported 516 markers instead of ~192, and the out-of-range list the user confirmed
    was drawn from doubled data.

    Filenames cannot fix this because the prefix is random by design. Content can: identical
    bytes are the same lab however many times it arrives, and hashing five PDFs is free next
    to transcribing them.

    WHICH copy survives matters as much as the dedupe. This used to keep the first in
    sorted-glob order - alphabetical on a random prefix, i.e. a coin flip per re-upload.
    When a re-upload's id sorted before the original's it displaced it as canonical: `staged`
    reported already-finished work as waiting, and intake wrote a SECOND transcription beside
    the first, doubling every one of those labs in labs.md (980 markers instead of 642,
    2026-07-30). The keeper is now the copy whose transcription already exists on disk
    (earliest transcription if several do), else the oldest upload. Once content is
    transcribed its identity is frozen; no later upload of the same bytes can displace it.
    """
    groups, order, keep, dupes = {}, [], [], []
    for f in sorted(glob.glob(os.path.join(directory, "*.pdf"))):
        try:
            h = hashlib.sha1(open(f, "rb").read()).hexdigest()
        except OSError:
            keep.append(f)                                     # unreadable: let it fail loudly
            continue
        if h not in groups:
            order.append(h)
        groups.setdefault(h, []).append(f)

    def rank(p):
        out = os.path.join(INPUTS, _lab_out_name(p))
        return (0, mtime(out), p) if has_transcription(p) else (1, mtime(p), p)

    for h in order:
        copies = sorted(groups[h], key=rank)
        keep.append(copies[0])
        dupes += [(d, copies[0]) for d in copies[1:]]
    keep.sort()
    for dup, orig in dupes:
        print("   duplicate ignored: %s (same content as %s)"
              % (os.path.basename(dup), os.path.basename(orig)))
    return keep, dupes


def quarantine_duplicates(directory, dupes):
    """Move content-duplicate losers - and any transcription they left - to raw/.duplicates/.

    Only intake calls this: `staged` and `check_labs` must stay read-only. Each loser is
    re-hashed at the moment of the move and only a PROVEN byte-identical copy is moved -
    these are the user's medical documents, so a wrong guess must stay recoverable, which is
    also why this moves and never deletes. `reset` clears the quarantine with everything else.

    A loser's stray transcription (written back when keeper selection was a coin flip) moves
    with it, but only when the keeper's own transcription exists - the merge must never be
    left without the only copy. Returns the number of stray transcriptions moved, so intake
    knows labs.md was built from a now-changed set and must re-merge even though labs.md is
    newer than every remaining transcription.
    """
    qdir = os.path.join(directory, ".duplicates")
    moved_strays = 0
    for dup, keeper in dupes:
        try:
            same = (hashlib.sha1(open(dup, "rb").read()).hexdigest()
                    == hashlib.sha1(open(keeper, "rb").read()).hexdigest())
        except OSError:
            continue
        if not same:
            continue
        os.makedirs(qdir, exist_ok=True)
        moved = [_move_into(dup, qdir)]
        stray = os.path.join(INPUTS, _lab_out_name(dup))
        if (os.path.exists(stray)
                and os.path.exists(os.path.join(INPUTS, _lab_out_name(keeper)))):
            moved.append(_move_into(stray, qdir))
            moved_strays += 1
        print("   quarantined:  %s" % ", ".join(os.path.basename(m) for m in moved))
    return moved_strays


def _move_into(path, dirpath):
    dest = os.path.join(dirpath, os.path.basename(path))
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(dirpath, "%d-%s" % (n, os.path.basename(path)))
        n += 1
    shutil.move(path, dest)
    return dest


# A lab prints its own flag column — N normal, H high, L low, A/AB abnormal, HH/LL critical.
# On a PDF whose columns sit close together that flag lands in the value cell ("Negative N",
# "0.2 N"): it is the flag column bleeding in, not part of the reading. Verification therefore
# compares the value WITHOUT the flag, while the flag stays on the row — out-of-range detection
# reads H/L straight out of the value text, so stripping it there would hide a real finding.
# Deliberately no bare "A": a single trailing A is indistinguishable from an ordinary word
# ending ("Vitamin A"), and a wrong strip could let a mangled value verify. AB is unambiguous.
LAB_FLAGS = r"(?:HH|LL|AB|WNL|[HLN])"


def value_without_flag(value):
    """A transcribed value minus a trailing lab-flag token, for comparison against the source."""
    return re.sub(r"\s+%s$" % LAB_FLAGS, "", value or "").strip()


def _measure_key(row):
    """Deprecated shim: the identity of a reading is observation_key(row).

    This was invented during the 2026-07-31 outage as an ad-hoc (analyte, unit) pair for ONE
    call site, because a flat marker name could not tell blood glucose from urine dipstick
    glucose. observation_key now carries that distinction properly - specimen and scale, both
    observed - so this exists only so the reconciler reads the same way as everything else.
    """
    return observation_key(row)


def _range_rows(path):
    """{measurement key: (printed marker, value)} from one range transcription."""
    try:
        header, rows = _first_table(open(path, encoding="utf-8", errors="replace").read())
    except OSError:
        return {}
    if not header:
        return {}
    out = {}
    for cells in rows:
        def col(name):
            i = header.index(name) if name in header else -1
            return cells[i] if 0 <= i < len(cells) else ""
        marker = col("marker")
        if marker:
            out[_measure_key({"marker": marker, "value": col("value"),
                              "specimen": col("specimen")})] = (marker, col("value"))
    return out


def reconcile_ranges():
    """Compare the two transcriptions of every overlapping window. Returns (agreed, conflicts, thin).

    A long PDF is flattened to result lines and split into windows that overlap by `LINE_OVERLAP`
    lines, so a reading in the overlap is transcribed twice by two workers with no shared context.
    Where both name the same reading, they must report the same value: that agreement is the actual
    evidence that splitting did not lose or corrupt a row — far stronger than counting marker-shaped
    lines, which is ill-defined on a layout that extracts one CELL per line. Two overlapping windows
    naming NO reading in common is the missed-marker signal: the band between them held a result one
    of them dropped.

    The NAME alone is not enough to identify a reading. A comprehensive panel measures blood GLUCOSE
    and urine dipstick GLUCOSE — one name, two tests — so matching is on analyte + specimen + scale
    (`observation_key`), keeping genuinely different readings apart. The cost is that a disagreement
    whose specimen was ALSO mistranscribed goes unseen here; verification against the source still
    covers the value itself.

    Comparison is on the pipeline's own normalisations, so `216 H` vs `216` and `CHOL/HDLC RATIO`
    vs `Cholesterol/HDL ratio` are agreement, not conflict. Only a real difference is reported.
    """
    by_doc = {}
    for path in sorted(glob.glob(os.path.join(INPUTS, "labs-*-L*.md"))):
        tag = _parse_win_tag(os.path.basename(path))
        if tag:
            stem = os.path.basename(path)[len("labs-"):-len(".md")]
            by_doc.setdefault(stem[:stem.rfind("-L")], []).append((tag, path))

    agreed, conflicts, thin = 0, [], []
    for slug, items in sorted(by_doc.items()):
        items.sort()
        for (a1, b1), f1 in items:
            for (a2, b2), f2 in items:
                if a2 <= b1 < b2 and a1 < a2:                  # f2 starts inside f1: they overlap
                    r1, r2 = _range_rows(f1), _range_rows(f2)
                    both = set(r1) & set(r2)
                    if not both:
                        # Suspicion, not proof: the overlap may hold only narrative, or the two
                        # workers may have qualified a marker name differently. It is reported and
                        # never blocks — a false block on a heuristic is how this check stops being
                        # trusted.
                        thin.append((slug, "windows %s and %s overlap but name no measurement in "
                                           "common — a reading in the overlap may have been missed"
                                     % (_win_tag(a1, b1), _win_tag(a2, b2))))
                        continue
                    for key in sorted(both):
                        v1, v2 = value_without_flag(r1[key][1]), value_without_flag(r2[key][1])
                        if v1.strip().lower() == v2.strip().lower():
                            agreed += 1
                        else:
                            conflicts.append(
                                (r1[key][0], "transcribed twice in overlapping windows %s and %s "
                                             "and disagrees: %r vs %r"
                                 % (_win_tag(a1, b1), _win_tag(a2, b2), r1[key][1], r2[key][1])))
    return agreed, conflicts, thin


def check_labs():
    """Verify every transcribed lab value actually appears in its source PDF.

    Deterministic, no LLM: re-extract each PDF with PyMuPDF and confirm each transcribed value
    is present verbatim. This catches fabrication, typos, and dropped digits — the failures
    that would otherwise flow silently into every downstream conclusion.

    It canNOT catch a correct number attached to the wrong marker, which is why the user still
    gets a summary to eyeball. Returns (stats, problems).
    """
    labs = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(labs):
        return {"rows": 0}, [("(no labs-complete.md)", "run `review_labs` first")]
    try:
        import fitz
    except ImportError:
        return {"rows": 0}, [("(PyMuPDF missing)", "cannot verify — install pymupdf")]

    text_of = {}
    for pdf in unique_pdfs(RAW)[0]:
        try:
            text_of[os.path.basename(pdf)] = "\n".join(p.get_text() for p in fitz.open(pdf))
        except Exception as e:  # noqa: BLE001
            text_of[os.path.basename(pdf)] = ""

    rows = checked = unreadable = 0
    problems, out_of_range, by_source, unsourced = [], [], {}, []
    readable_ids = _readable_reading_ids(labs)
    hdr = None
    for line in open(labs, encoding="utf-8"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        low = [c.lower() for c in cells]
        # A header row, first or repeated. The per-PDF transcriptions emit the column header once
        # per section, so the merged file carries several — sometimes with a blank trailing column
        # (confidence) so they are NOT byte-identical. Match on marker+value, not equality: a
        # repeat parsed as data became a phantom marker='marker' / source='source file' row that
        # failed the "source not among the PDFs" check and held the whole research phase.
        if "marker" in low and "value" in low:
            if hdr is None:
                hdr = low
            continue
        if hdr is None or all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        rows += 1
        def col(name):
            return cells[hdr.index(name)] if name in hdr and hdr.index(name) < len(cells) else ""
        marker, value, src = col("marker"), col("value"), col("source file")
        if not marker:
            continue
        # The marker NAME must be in the source too, not just the value. An UNREADABLE row
        # skipped the value check entirely, so a transcriber could invent a row wholesale and
        # nothing would notice: the Omega-3 Index report produced "Estimated Omega-3 Index"
        # and "Estimated cardiovascular death risk", both UNREADABLE, and the words
        # "estimated", "risk", "death" and "cardiovascular" appear ZERO times in that PDF.
        # They are chart furniture the transcriber turned into rows. Checked across the whole
        # merged file this flags 2 rows in 684 - the two fabricated ones, nothing else.
        # A trailing qualifier is dropped before the second attempt. Lab PDFs interleave the
        # value between a marker and its note - "IRON BINDING CAPACITY 332 250-425 mcg/dL
        # (calc)" - so the transcriber records "IRON BINDING CAPACITY (calc)", which is right,
        # while a flattened contiguous search for "ironbindingcapacitycalc" finds nothing and
        # the row is called fabricated. The base name IS in the document, and the VALUE is
        # verified separately, so matching on the base name is enough to show the row is real.
        _base = re.sub(r"\s*\([^)]*\)\s*$", "", marker).strip()
        if (src in text_of and _flat(marker) and _flat(marker) not in _flat(text_of[src])
                and not (_flat(_base) and _flat(_base) in _flat(text_of[src]))):
            unsourced.append((marker, src))
            problems.append((marker, "marker name does not appear in %s — the row is not in "
                                     "the source at all" % src))
            continue
        if is_unreadable(value):
            # NOT A MEASUREMENT AT ALL: no value, no unit, no reference range. A printed footnote
            # the transcriber read as a result row (`NOTE`, in the urinalysis panel) held the
            # whole research phase on 2026-08-11. merge-labs drops these now; this keeps a
            # labs-complete.md merged before that fix from blocking on one.
            if is_furniture_row({"value": value, "unit": col("unit"),
                                 "reference range": col("reference range")}):
                continue
            # Subsumed: another window READ this analyte on this date from this document, so the
            # value is in hand and this row is the absence of evidence, not a missing reading.
            # merge-labs now drops these, but a labs-complete.md merged before that fix still
            # carries them, and holding the whole research phase over one is what this backstop
            # did on 2026-08-10 (ZINC, while ZINC 82 mcg/dL sat two rows below).
            if (src, _marker_key(marker), col("date")) in readable_ids:
                continue
            unreadable += 1
            problems.append((marker, "value is UNREADABLE"))
            continue
        by_source[src] = by_source.get(src, 0) + 1
        if src not in text_of:
            problems.append((marker, "source file %r not among the PDFs" % src))
            continue
        # Same tolerance as the transcribe-time check (_row_in_source): the source prints a value
        # and its flag on SEPARATE lines ("A" then "NW"), which the model correctly folds into one
        # cell ("A NW"). A raw substring rejected that here while _row_in_source accepted it, so this
        # backstop blocked Stage 6 over a faithful transcription. Collapse whitespace and case, keep
        # digits, so a two-line value verifies but a mis-keyed number still fails.
        if not _value_in_text(value, text_of[src]):
            problems.append((marker, "value %r not found in %s" % (value, src)))
            continue
        checked += 1

    out_of_range = out_of_range_entries()

    # A disagreement between two independent transcriptions of one page is PROOF that one of
    # them is wrong, so it joins the unverifiable values rather than the advisory warnings.
    agreed, conflicts, thin = reconcile_ranges()
    problems += conflicts
    # Coverage is a heuristic count and stays advisory: it exists to catch a range that
    # silently produced nothing, and must never hold up a review over an approximation.
    gaps = rxsplit.coverage_gaps(by_source, RAW) + thin

    return ({"rows": rows, "verified": checked, "unreadable": unreadable,
             "pdfs": len(text_of), "out_of_range": out_of_range, "unsourced": unsourced,
             "overlap_agreed": agreed, "coverage_gaps": gaps}, problems)


def _first_table(text):
    """Rows of the FIRST contiguous markdown table only, as (header, cells) pairs.

    Intake also writes summary tables further down the draft (e.g. a
    `| product | brand | what is missing |` table under Needs confirmation). Latching onto the
    first header and then parsing every later table with those column positions turned that
    second header row into a data row — which is how a lookup card for "what is missing brand"
    and another for "Unit Interplexus" got created.
    """
    header, rows, started = None, [], False
    for line in text.splitlines():
        t = line.strip()
        if not t.startswith("|"):
            if started:
                break          # table ended — ignore everything after it
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        if all(__import__("re").fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        low = [c.lower() for c in cells]
        if header is None:
            if "product" in low or "marker" in low:
                header, started = low, True
            continue
        # a second header inside the same block also means a new table
        if "product" in low and "brand" in low:
            break
        rows.append(cells)
    return header, rows


def products_needing_lookup():
    """Products whose dose is missing but whose NAME is specific enough to look up.

    A branded SKU ("Thorne Super EPA", "Life Extension Neuro-Mag") has a published Supplement
    Facts panel — asking the user to type it is asking them to do a web lookup by hand. Only
    rows with a real product name qualify; a bare "my multivitamin" cannot be looked up.
    """
    draft = os.path.join(INPUTS, "regimen-draft.md")
    if not os.path.exists(draft):
        return []
    out = []
    header, rows = _first_table(open(draft, encoding="utf-8").read())
    if not header or "product" not in header:
        return []
    i_prod = header.index("product")
    for cells in rows:
        name = cells[i_prod] if i_prod < len(cells) else ""
        if not name or name.startswith("("):
            continue
        # never treat a column label as a product
        if name.lower() in header or name.lower() in ("product", "brand", "unit", "type"):
            continue
        def cell(col):
            return cells[header.index(col)] if col in header and header.index(col) < len(cells) else ""

        # Blank counts as missing, not just the literal UNREADABLE.
        missing = any(cell(col).upper() in ("UNREADABLE", "") for col in CRITICAL_COLS)
        if not missing:
            continue
        # NO word-count/brand heuristic. It skipped exactly the products most worth looking up:
        # "Neuro-Mag", "Sacro-B", "Seriphos" are single words with an empty brand column, yet
        # they are distinctive trade names. Generic multi-word names are the harder case.
        # Only skip things that genuinely cannot be searched; a lookup that finds nothing costs
        # one card and reports NOT FOUND, which is a better outcome than not trying.
        if re.fullmatch(r"(my |a )?(multi[- ]?vitamin|vitamin|supplement|pill|capsule)s?",
                        name.strip(), re.I):
            continue
        brand = cell("brand")
        full = ("%s %s" % (brand, name)).strip() \
            if brand and brand.lower() not in name.lower() else name
        # The user's own line is the best search string — it carries the brand even when the
        # draft's brand column came back empty ("Life Extension Neuro-Mag").
        out.append((full, _regimen_line_for(name)))
    seen, uniq = set(), []
    for full, hint in out:
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        uniq.append((full, hint))
    return sorted(uniq)


def _regimen_line_for(name):
    """The user's original line for this product, if it can be found."""
    p = os.path.join(INPUTS, "regimen.txt")
    if not os.path.exists(p):
        return ""
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    words = [w for w in key.split() if len(w) > 2]
    best, score = "", 0
    for line in open(p, encoding="utf-8", errors="replace"):
        low = re.sub(r"[^a-z0-9]+", " ", line.lower())
        hit = sum(1 for w in words if w in low)
        if hit > score:
            best, score = line.strip(), hit
    return best if score else ""


def pending_transcriptions():
    """Staged PDFs with no transcription yet, as (file, why).

    Uploads arrive in rounds, so intake runs several times and each run creates a merge card
    keyed on the SET of PDFs it can see. An EARLY merge card therefore completes over a
    partial set and advances the pipeline, which is how the user was asked to confirm 600
    markers from 20 PDFs while two were still being transcribed (2026-07-30). The answer is
    not to reason about which merge card fired: it is to ask the inputs directory whether
    every staged document has landed before putting a question to a human.

    A document already known to be untranscribable is not pending - nothing will ever arrive
    for it, and waiting on it would stall the review permanently.
    """
    out = []
    for pdf in unique_pdfs(RAW)[0]:
        if rxcache.unreadable_reason(pdf):
            continue
        base = os.path.basename(pdf)
        if os.path.exists(os.path.join(INPUTS, _lab_out_name(pdf))):
            continue
        try:
            windows = _line_windows(_flat_lines(rxsplit.page_texts(pdf)), INLINE_BUDGET, LINE_OVERLAP)
        except Exception:                                      # noqa: BLE001
            windows = []
        # A single-window PDF writes the unsuffixed name checked just above, so reaching here means
        # it is not transcribed; a multi-window PDF is pending until every window's file exists.
        if len(windows) <= 1:
            out.append((base, "not transcribed"))
            continue
        missing = [(a, b) for a, b in windows
                   if not os.path.exists(os.path.join(INPUTS, _lab_out_name(pdf, (a, b))))]
        if missing:
            out.append((base, "%d of %d window(s) still to transcribe: %s"
                              % (len(missing), len(windows),
                                 ", ".join(_win_tag(a, b) for a, b in missing[:4]))))
    return out


def _draw_dates():
    """The distinct draw dates in labs.md, oldest first.

    Only used by the M = 0 gate message. With nothing out of range there is no list to check, so
    the answerable question becomes provenance - whose results these are, and whether they are
    all of them - and the dates are what makes it answerable by eye.
    """
    labs = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(labs):
        return []
    hdr, seen = None, set()
    for line in open(labs, encoding="utf-8", errors="replace"):
        ln = line.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        low = [c.lower() for c in cells]
        if hdr is None and "marker" in low:
            hdr = low
            continue
        if hdr is None or "date" not in hdr:
            continue
        d = cells[hdr.index("date")] if hdr.index("date") < len(cells) else ""
        d = _norm_date(d) or d
        if d and not re.fullmatch(r":?-{2,}:?", d):
            seen.add(d)
    return sorted(seen)


def pending_lookups():
    """Work that must finish before any supplement question is worth asking.

    Two distinct waits, and missing the second one is what produced a round of questions the
    lookups had already answered:

    1. a lookup that has not produced its product-*.md yet;
    2. a lookup that HAS, but whose data is not in supplements-draft.md yet — the refresh card
       folds those files back in, and until it runs the draft still says UNREADABLE.
    """
    out = []
    draft = os.path.join(INPUTS, "regimen-draft.md")
    draft_t = mtime(draft)
    for name, _hint in products_needing_lookup():
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
        f = os.path.join(INPUTS, "product-%s.md" % slug)
        if not os.path.exists(f):
            out.append(name)
        elif mtime(f) > draft_t:
            out.append("%s (awaiting refresh)" % name)
    return out


def cmd_confirm(args):
    """Report the settled regimen: the rows in regimen-final.md and any low-confidence items.

    Stage 3's Barrier is what settles the regimen with the user; this only reports the state it
    left. Low confidence is informational — it does not hold an item back from research; only the
    user dropping an item at the review keeps it out.
    """
    rows = _read_regimen_final_rows()
    low = [name for (_n, name, _ing, _qty, _sch, _st, conf) in rows if conf.strip().lower() == "low"]
    if getattr(args, "json", False):
        print(json.dumps({
            "settled": [{"n": n, "name": name, "ingredients": ing, "quantity": qty,
                         "schedule": sch, "started": started, "confidence": conf}
                        for (n, name, ing, qty, sch, started, conf) in rows],
            "low_confidence": low, "clear": bool(rows)}, indent=2))
        return 0 if rows else 1
    if not rows:
        print("regimen-final.md is missing or empty — stage 3 has not settled the regimen yet.")
        return 1
    print("Regimen (final) — %d item(s):" % len(rows))
    for (n, name, ing, qty, sch, started, conf) in rows:
        print("   %2d. %-28s %s [%s] (%s)%s"
              % (n, name[:28], ing or "(dose not found)", sch or "?", conf or "?",
                 " started %s" % started if started else ""))
    if low:
        print("\nLow-confidence item(s) (%d): %s" % (len(low), ", ".join(low)))
        print("Informational only — the user can correct or drop any at the Stage 3 review.")
    return 0


def cmd_trends(args):
    """Markers moving consistently in one direction over their last three or more draws."""
    ts = trends()
    if args.json:
        print(json.dumps({"ok": True, "trends": ts}, default=str))
        return 0
    if not ts:
        print("No marker has three or more readings moving consistently in one direction.")
        return 0
    print("Trending markers (%d) — direction over the last %d+ draws:\n" % (ts and len(ts), MIN_TREND_POINTS))
    for t in ts:
        pts = " -> ".join("%g" % n for _, n in t["series"])
        width = ("%.2f ref-widths" % t["delta_over_ref"]) if t["delta_over_ref"] else "ref n/a"
        note = "" if t["in_range_throughout"] else "  (abnormal at some draw)"
        print("   %-26s %-7s %d pts  %-30s %s%s"
              % (t["marker"][:26], t["direction"], t["points"], pts, width, note))
    print("\nA trend inside the reference range is still a finding: nothing is flagged, and the")
    print("direction is the whole signal.")
    return 0


def cmd_before_after(args):
    """Before/after for one marker split at a medication start date (arithmetic only)."""
    r = before_after(args.marker, args.since)
    if args.json:
        print(json.dumps({"ok": True, **r}, default=str))
        return 0
    if not r.get("found"):
        print("%s: %s" % (r["marker"], r.get("reason", "no readings")))
        return 0
    if r.get("error"):
        print("%s: %s" % (r["marker"], r["error"]))
        return 1

    def fmt(seg):
        return " -> ".join("%s: %g" % (d, n) for d, n in seg) or "(none)"

    print("%s  (since %s)" % (r["marker"], r["since"]))
    print("   pre  (%d): %s" % (r["pre_n"], fmt(r["pre"])))
    print("   post (%d): %s" % (r["post_n"], fmt(r["post"])))
    if r["baseline"] is None:
        print("   baseline: none (no pre-start readings) — direction only")
    else:
        d = r["delta"]
        p = (" (%.1f%%)" % r["pct"]) if r["pct"] is not None else ""
        print("   delta: %s -> %s  =  %s%s  post-direction: %s"
              % (r["baseline"], r["endpoint"],
                 ("%.4g" % d) if d is not None else "n/a", p, r["direction"] or "n/a"))
    if r["too_early"]:
        print("   TOO EARLY TO TELL — %d post-start draw(s)." % r["post_n"])
    return 0


def _lab_slug(pdf):
    return re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(pdf))[0].lower()).strip("-")


def _ocr_pdf_path(pdf):
    """Where the searchable (OCR'd) copy of a text-less lab PDF lives, keyed by its filename.

    Kept out of raw/ so it is never re-counted as a staged source; cleared with inputs/ on reset.
    """
    return os.path.join(INPUTS, "raw-ocr", os.path.basename(pdf))


def _win_tag(first, last):
    """The filename fragment for a line window: stable, sortable, zero-padded."""
    return "L%04d-%04d" % (first, last)


def _parse_win_tag(name):
    """(first, last) line window from a labs-*-LNNNN-MMMM.md filename, or None."""
    m = re.search(r"-L(\d+)-(\d+)\.md$", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _lab_out_name(pdf, rng=None):
    """The transcription filename intake will write for this PDF, or for one line window of it.

    A document small enough to transcribe whole keeps the original unsuffixed name, so cached
    transcriptions and every pre-split run stay valid.
    """
    slug = _lab_slug(pdf)
    if rng is None:
        return "labs-%s.md" % slug
    return "labs-%s-%s.md" % (slug, _win_tag(rng[0], rng[1]))


def has_transcription(pdf):
    """True when this PDF has been transcribed whole OR as overlapping line windows.

    unique_pdfs ranks a content-duplicate group by "already transcribed", which must recognise
    a split document too - otherwise a re-upload of a split PDF outranks the original and the
    displacement bug this ranking exists to prevent comes back for exactly the largest files.
    """
    if os.path.exists(os.path.join(INPUTS, _lab_out_name(pdf))):
        return True
    return bool(glob.glob(os.path.join(INPUTS, "labs-%s-p*.md" % _lab_slug(pdf))))


# A lab panel is a table of markers, values, units and reference ranges. A clinical NARRATIVE
# - an endoscopy or radiology report, a discharge summary - has none of that, and the
# transcriber is told to produce marker rows, so handing it one yields nothing useful or worse:
# invented rows. One arrived in an upload batch and a card was spent before anyone noticed.
#
# This screen WARNS, never blocks. Refusing a real lab is far more costly than transcribing one
# non-lab: a missing panel silently weakens every downstream analysis, while a wasted card
# costs two minutes. Everything ambiguous is treated as a lab.
LAB_SIGNALS = [
    (r"\bref(erence)?\s*(range|interval)\b", "a reference-range column"),
    (r"\b\d+(\.\d+)?\s*[-\u2013]\s*\d+(\.\d+)?\b", "numeric reference intervals"),
    (r"\b(mg/dL|ng/mL|mmol/L|g/dL|U/L|IU/L|mIU/L|nmol/L|pg/mL|ug/dL|mcg/dL|mEq/L)\b",
     "clinical-chemistry units"),
    (r"\b(collected|specimen|drawn|fasting|ordering physician)\b", "specimen metadata"),
]
NON_LAB_SIGNALS = [
    (r"\b(endoscopy|colonoscopy|gastroscopy|sigmoidoscopy)\b", "an endoscopy report"),
    (r"\b(radiolog|ultrasound|\bMRI\b|\bCT scan\b|x-ray|mammogra)", "an imaging report"),
    (r"\b(pathology|biopsy|histolog|cytolog)\b", "a pathology report"),
    (r"\b(discharge summary|operative (note|report)|progress note|consultation note)\b",
     "a clinical note"),
]


def screen_pdf(path):
    """(verdict, why) for a staged PDF: "lab", "not-a-lab", "scan" or "unclear".

    Mechanical and cheap - one text extraction, no model. Deliberately biased toward "lab":
    only a document with non-lab evidence AND no lab evidence is called out.
    """
    try:
        import fitz
    except ImportError:
        return "unclear", "PyMuPDF unavailable, cannot screen"
    try:
        doc = fitz.open(path)
        text = "\n".join(pg.get_text() for pg in doc)
        doc.close()
    except Exception as exc:                                   # noqa: BLE001
        return "unclear", "could not read the PDF (%s)" % exc
    return classify_lab_text(text)


def classify_lab_text(text):
    """The verdict for already-extracted text. Split out so it is testable without PyMuPDF."""
    if len(text.strip()) < 200:
        return "scan", ("only %d characters of text layer — a scan needs OCR first"
                        % len(text.strip()))
    lab = [why for pat, why in LAB_SIGNALS if re.search(pat, text, re.I)]
    non = [why for pat, why in NON_LAB_SIGNALS if re.search(pat, text, re.I)]
    if non and len(lab) < 2:
        return "not-a-lab", "looks like %s (found %s; no marker table)" % (non[0], ", ".join(non))
    if len(lab) >= 2:
        return "lab", "found %s" % ", ".join(lab[:3])
    return "unclear", "few lab signals (%s)" % (", ".join(lab) or "none")


def cmd_staged(args):
    """What is waiting in inputs/raw/ that has not been transcribed yet.

    Discord caps an upload at 10 attachments, so a full history arrives in several rounds. The
    agent copies each round in and calls this to confirm receipt; nothing starts until the user
    says to begin. Content-hash dedupe means re-sending the same PDF is free.
    """
    keep, dupes = unique_pdfs(RAW)
    done = {os.path.basename(p) for p in transcription_files()}
    pending = [p for p in keep if _lab_out_name(p) not in done]
    screened = [(p, ) + screen_pdf(p) for p in pending]
    flagged = [(p, v, why) for p, v, why in screened if v == "not-a-lab"]
    if args.json:
        print(json.dumps({"ok": True, "staged": len(keep), "pending": len(pending),
                          "duplicates": len(dupes), "flagged": len(flagged),
                          "files": [os.path.basename(p) for p in pending],
                          "not_labs": [{"file": os.path.basename(p), "verdict": v, "why": why}
                                       for p, v, why in flagged]}))
        return 0
    print("Staged lab PDFs: %d  (%d already transcribed, %d duplicate%s ignored)"
          % (len(keep), len(keep) - len(pending), len(dupes), "" if len(dupes) == 1 else "s"))
    for p, verdict, why in screened:
        mark = {"lab": "waiting", "unclear": "waiting", "not-a-lab": "CHECK  ",
                "scan": "OCR    "}[verdict]
        print("   %s: %s" % (mark, os.path.basename(p)))
        if verdict == "not-a-lab":
            print("            ^ %s" % why)
        elif verdict == "scan":
            print("            ^ no text layer — transcription will OCR it first")
    if dupes:
        for dup, _orig in dupes:
            print("   duplicate of one already staged, ignored: %s" % os.path.basename(dup))
    if flagged:
        print("\n%d file(s) marked CHECK do not look like lab panels. Ask the user whether each"
              % len(flagged))
        print("was meant to be included — a narrative report has no marker table, so the")
        print("transcriber has nothing to read. Remove it from inputs/raw/ if it was a mistake.")
        print("This is a WARNING, not a refusal: if the user says it is a lab, transcribe it.")
    # The count above is only what REACHED the pipeline. Staging used to be a per-attachment
    # copy performed by the assistant, so an upload arrived only if the model noticed it: ten
    # PDFs were sent, the reply to "is that the complete set?" carried ten more as attachments,
    # the reply was read as a promise to send them later, and the pipeline saw half the labs
    # with nothing to say otherwise. Now the discrepancy is stated wherever it can be seen.
    _missing = unstaged_documents()
    if _missing:
        print("\n   !! %d PDF(s) Hermes received are NOT staged:" % len(_missing))
        for _f in _missing[:8]:
            print("      %s" % os.path.basename(_f))
        if len(_missing) > 8:
            print("      ... and %d more" % (len(_missing) - 8))
        print("      Stage them:  python3 ~/.hermes/rx-review/rx.py stage")
    print("\nASK THE USER whether more labs are coming, and WAIT for their answer. Nothing runs")
    print("until they say the set is complete; when they do, record it and start:")
    print("   python3 ~/.hermes/rx-review/rx.py uploads-done")
    print("   python3 ~/.hermes/rx-review/rx.py start")
    return 0


def board_cards():
    """Every card on the board, or [] when the CLI cannot be reached. Read-only, via the CLI."""
    try:
        out = sh([HERMES, "kanban", "--board", BOARD, "list", "--json"])
        cards = json.loads(out.stdout)
        return cards if isinstance(cards, list) else []
    except Exception:                                          # noqa: BLE001
        return []


# What a stage's blocked barrier is waiting to be told, and the verb that tells it. Keyed on the
# card title so a barrier renamed in one place cannot silently lose its answer here.
_ANSWERS = {
    "Stage 3: Finalize Regimen": (
        "'approved' accepts it; '<n> <correction>' fixes one line; '<n> drop' removes one",
        ["When they answer, pass their reply verbatim as the argument:",
         "    python3 ~/.hermes/rx-review/rx.py correct-item-slug-request \"<their reply>\""]),
    "Stage 5: Labs Complete": (
        "'looks good' keeps every marker; '2,5 ignore' skips those two",
        # NAME THE TOOL. "Write their reply to inputs/marker-reply.txt" left the method open and
        # a worker chose `echo "looks good" > ~/.hermes/rx-review/inputs/marker-reply.txt`, which
        # the security scan flagged HIGH — everything under ~/.hermes is a dotfile path, so any
        # shell redirect there reads as overwriting shell configuration. The user then had to
        # approve a HIGH warning to say "looks good", which is how approving warnings becomes a
        # habit. write_file passes the reply as DATA: no shell, nothing to scan, and no quoting
        # for the metacharacters a correction legitimately contains.
        ["When they answer, write their reply verbatim to inputs/marker-reply.txt using the",
         "write_file tool, then run:",
         "    python3 ~/.hermes/rx-review/rx.py marker-review --batch"]),
}


def _card_summary(cid):
    """A card's latest summary — the reason a hold gives — or "" when it cannot be read."""
    if not cid:
        return ""
    try:
        out = sh([HERMES, "kanban", "--board", BOARD, "show", cid, "--json"])
        return ((json.loads(out.stdout) or {}).get("latest_summary") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def pipeline_state():
    """(state, headline, lines) — ONE computed answer to "what is happening, and what happens next".

    THE ONE FACT THAT MATTERS RANKS FIRST. `status` used to print the tail of the card list, so on
    a mature board its last 14 lines were all `done` transcriptions and a barrier blocked on the
    user was structurally invisible: on 2026-08-11 the user answered a marker review, the agent
    ran `status`, read a wall of ticks, concluded "no blocked cards right now" and dropped the
    answer. Nothing here is truncated ahead of a hold.

    EVERY LINE ADDRESSES THE READER, WHICH IS THE MODEL. The human is "the user", in the third
    person, always. A hold that says "waiting on you" tells the model that IT should answer, and a
    model that answers a regimen or marker review on the user's behalf does not stall the pipeline
    — it puts an approval nobody gave into the brief.
    """
    h = halted()
    if h:
        return "halted", "HALTED — %s" % (h[1] or os.path.basename(h[0])), [
            "The review ended here and does not resume.",
            "Tell the user what was rejected and why. To start again, they fix the input, then:",
            "    python3 ~/.hermes/rx-review/rx.py reset --confirm"]

    cards = board_cards()
    if not cards:
        return "unknown", "NO BOARD — nothing has been started, or the CLI is unreachable.", [
            "If the user is starting a review, stage their labs first:",
            "    python3 ~/.hermes/rx-review/rx.py stage"]

    by_status = {}
    for c in cards:
        by_status.setdefault(c.get("status"), []).append(c)
    blocked = by_status.get("blocked", [])
    if blocked:
        # Held cards outrank everything: the pipeline cannot move until a person answers.
        c = blocked[0]
        title = c.get("title") or "(untitled)"
        hint, how = _ANSWERS.get(title, (None, None))
        lines = ["The user has not answered yet. Ask them, and wait for their reply."]
        if hint:
            lines.append("Their options: %s." % hint)
        if how:
            lines += how
        else:
            # Not every hold is a question. A backstop hold is a REPAIR: nothing the user can
            # reply will clear it, so saying "the user has not answered" invites a wait for an
            # answer that is not coming. Say what stopped, in the card's own words.
            reason = _card_summary(c.get("id"))
            lines = ["The pipeline stopped here and cannot go on by itself."]
            if reason:
                lines.append("Reason: %s" % reason)
            lines += ["Tell the user that, and that it needs fixing rather than answering.",
                      "For the detail behind it, run:",
                      "    python3 ~/.hermes/rx-review/rx.py doctor"]
        if len(blocked) > 1:
            lines.append("%d other card(s) are also held." % (len(blocked) - 1))
        return "held", "HOLDING FOR THE USER — %s" % title, lines

    running = by_status.get("running", [])
    ready = by_status.get("ready", [])
    todo = by_status.get("todo", [])
    done = by_status.get("done", [])
    if not (running or ready or todo):
        briefs = sorted(glob.glob(os.path.join(REPORTS, "*rx-review.md")))
        if briefs:
            return "finished", "FINISHED — %s" % os.path.basename(briefs[-1]), [
                "Tell the user the brief is ready, and where it is:",
                "    %s" % briefs[-1]]
        return "idle", "IDLE — no cards left to run and no brief written.", [
            "Report this to the user; it is not a state the pipeline reaches on its own. Run:",
            "    python3 ~/.hermes/rx-review/rx.py doctor"]

    return "running", "RUNNING — %d card(s) working, %d queued, %d done." % (
        len(running), len(ready) + len(todo), len(done)), [
        "Nothing to do. Tell the user it is still working, and that the pipeline will "
        "surface anything it needs."]


def cmd_status(args):
    """Where the pipeline is, in one ranked headline. `--detail` adds the full dump."""
    state, headline, lines = pipeline_state()
    print(headline)
    for ln in lines:
        print("   %s" % ln)
    if not getattr(args, "detail", False):
        return 0
    print()
    return _status_detail(args)


def _status_detail(args):
    # A HALTED BOARD LOOKS EXACTLY LIKE A HUNG ONE otherwise: no open cards, nothing moving,
    # and no indication whether anybody is waiting. Said first, because it changes what every
    # section below means.
    _h = halted()
    if _h:
        print("=== HALTED ===")
        print("   %s" % os.path.basename(_h[0]))
        print("   reason: %s" % (_h[1] or "(none recorded)"))
        print("   The review ended here and does not resume. Fix the input, then `rx.py reset`")
        print("   and start again. Everything below is what it left behind.\n")
    print("=== inputs ===")
    for label, path in (("lab PDFs", RAW), ("photos", PHOTOS)):
        n = len([f for f in glob.glob(os.path.join(path, "*")) if os.path.isfile(f)])
        print("   %-22s %d file(s)" % (label, n))
    for f in ("regimen.txt", "patient.md", "regimen-draft.txt", "regimen-final.md",
              "labs-draft.md", "labs-complete.md", "labs-succinct.md"):
        p = os.path.join(INPUTS, f)
        print("   %-22s %s" % (f, "%d bytes" % os.path.getsize(p) if os.path.exists(p) else "-"))
    n = len(transcription_files())
    print("   %-22s %d" % ("labs-*.md (per PDF)", n))

    st = rxcache.stats()
    print("=== cache ===")
    print("   verified transcriptions  %d" % st["transcriptions"])
    print("   known-unreadable PDFs    %d" % st["unreadable"])
    print("   on disk                  %.1f KB  (%s)" % (st["bytes"] / 1024.0, st["home"]))
    print("=== board ===")
    out = sh([HERMES, "kanban", "--board", BOARD, "list"])
    print("\n".join("   " + l for l in out.stdout.strip().splitlines()[-14:]))

    print("=== reports ===")
    rs = sorted(glob.glob(os.path.join(REPORTS, "*.md")))
    print("   %d file(s)%s" % (len(rs), ": " + ", ".join(os.path.basename(r) for r in rs[:6])
                               if rs else ""))
    return 0


def cmd_doctor(args):
    """State, deterministically, where the regimen and marker review stand.

    Human input is the Stage 3 Barrier and the `Marker review:` cards, answered in chat. This makes
    their state legible in one place: the settled regimen rows, any low-confidence items, whether a
    correction is mid-flight, which markers are excluded, and which cards are still open. Read-only:
    it states facts and never changes state.
    """
    _h = halted()
    if _h:
        print("HALTED — %s" % os.path.basename(_h[0]))
        print("   reason: %s" % (_h[1] or "(none recorded)"))
        print("\nThe review was rejected and ended. Nothing below is waiting on anyone.\n")

    print("=== regimen-final.md (the settled regimen) ===")
    rows = _read_regimen_final_rows()
    if not rows:
        print("   MISSING/empty — stage 3 has not gathered the regimen yet.")
        print("   `gather-regimen-slugs` writes it from the per-item regimen-item-*.md files.")
    else:
        for (n, name, ing, qty, sch, started, conf) in rows:
            print("   %2d. %-28s %s [%s] (%s)%s"
                  % (n, name[:28], ing or "(dose not found)", sch or "?", conf or "?",
                     " started %s" % started if started else ""))
        low = [name for (_n, name, _i, _q, _s, _st, conf) in rows if conf.strip().lower() == "low"]
        if low:
            print("\n   low-confidence (%d): %s" % (len(low), ", ".join(low)))
            print("   informational only — low confidence does not hold an item back; the user")
            print("   drops an item at the review to keep it out of research.")

    pend = os.path.join(INPUTS, ".correction-pending")
    if os.path.exists(pend):
        print("\n   a correction is pending on line %s (awaiting `correct-item-slug-response`)."
              % open(pend, encoding="utf-8").read().strip().splitlines()[0])

    print("\n=== labs-complete.md (the review decisions) ===")
    ig = sorted(ignored_markers())
    if not os.path.exists(os.path.join(INPUTS, "labs-complete.md")):
        print("   MISSING — stage 5 (`review_labs`) has not run.")
    else:
        print("   %d marker(s) excluded from research%s"
              % (len(ig), (": " + ", ".join(ig)) if ig else ""))

    print("\n=== open human-input cards on the board ===")
    # doctor is what you run WHEN things are broken, so a missing/unreachable hermes must degrade
    # to "board unavailable", not a traceback.
    try:
        out = sh([HERMES, "kanban", "--board", BOARD, "list", "--json"])
        tasks = json.loads(out.stdout)
    except FileNotFoundError:
        print("   board unavailable — the `hermes` CLI is not on PATH here.")
        tasks = []
    except Exception:                                          # noqa: BLE001
        tasks = []
    # ASK THE BOARD WHAT IS HELD, never a list of titles. This filtered on
    # `Stage 3: Finalize` and `Marker review:` — the second is a card class the batched-barrier
    # redesign stopped creating, and the first matched whatever its STATUS. So on 2026-08-11
    # this section reported a *done* Stage 3 card as the open human-input item while the actually
    # blocked Stage 5 barrier went unlisted, in the one verb whose job is to say what is waiting.
    waiting = [t for t in tasks if t.get("status") == "blocked"]
    if not waiting:
        print("   no card is held for the user.")
    for t in waiting:
        print("   %s  [%s]  %s" % (t.get("id", "?"), t.get("status", "?"), t.get("title", "")))
        if t.get("status") == "triage":
            print("      ^ in TRIAGE: it was unblocked and re-asked. Answer with the verb "
                  "(the Stage 3 correction verbs / `marker-review`), do not unblock.")
    return 0


def _gdocs_script():
    """Path to the google-docs skill's docs.py, or None.

    Resolution order: explicit override (RX_GDOCS_SCRIPT, also the test hook), the invoking
    skill's sibling (HERMES_SKILL_DIR is set when the agent runs inside a skill), then the
    external-skills checkout this installation actually uses.
    """
    override = os.environ.get("RX_GDOCS_SCRIPT")
    if override:
        # An explicit override is authoritative: falling back behind the caller's back would
        # run a reader they specifically pointed away from (and makes tests hit the network).
        return os.path.abspath(override) if os.path.isfile(override) else None
    cands = []
    sk = os.environ.get("HERMES_SKILL_DIR")
    if sk:
        cands.append(os.path.join(sk, "..", "google-docs", "scripts", "docs.py"))
    cands.append(os.path.expanduser("~/hermes-skills/google-docs/scripts/docs.py"))
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


def cmd_regimen(args):
    """Ingest the regimen from a pointer the caller resolved.

    The document is the patient's ONE input: in addition to the regimen lines it may carry a
    `Name:`, `Age:` or `DOB:` line, which is materialised to inputs/patient.md here - the same
    "the document is the surface, the file is what the pipeline reads from" shape the regimen
    itself has (regimen.txt). A document with no fact line changes nothing and says nothing.

    Sources: a Google Doc by id (--from-gdoc), a local file (--from), or stdin. --from-gdoc
    exists because the two-command alternative (docs.py read --out, then --from) kept being
    "optimised" into `docs.py … | rx.py regimen --stdin` by the agent — python3 piped into
    python3, which the security scanner flags as pipe-to-interpreter and holds for manual
    approval (2026-07-30, and prior). A single verb leaves nothing to pipe: this runs the
    google-docs reader itself and ingests the result.
    """
    if getattr(args, "from_gdoc", None):
        docs = _gdocs_script()
        if docs is None:
            print("Cannot find the google-docs skill (docs.py). Set RX_GDOCS_SCRIPT to its "
                  "path, or install the google-docs skill.")
            return 1
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="regimen-gdoc-", suffix=".txt")
        os.close(fd)
        try:
            out = sh([sys.executable, docs, "read", args.from_gdoc, "--out", tmp])
            if out.returncode != 0:
                print("Reading the Google Doc failed: %s"
                      % (out.stderr or out.stdout).strip()[:300])
                return 1
            text = open(tmp, encoding="utf-8", errors="replace").read()
        finally:
            os.unlink(tmp)
        origin = "google doc %s" % args.from_gdoc
    elif args.stdin:
        text = sys.stdin.read()
        origin = "stdin"
        # Accept the house JSON envelope directly, so a document can be piped straight in:
        #     docs.py read <id> | rx.py regimen --stdin
        # Without this the agent has to unwrap the envelope itself, and the only tool it has
        # for that is an inline `python3 -c`, which makes the pipeline `python3 | python3` and
        # trips the security scanner's pipe-to-interpreter rule. The user then gets an approval
        # prompt for what is really just "read my regimen doc". One less step, one less gate.
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                if obj.get("ok") is False:
                    print("The source reported an error: %s" % obj.get("error", "(no detail)"))
                    return 1
                for key in ("text", "body", "content"):
                    if isinstance(obj.get(key), str) and obj[key].strip():
                        text = obj[key]
                        origin = "stdin (%s)" % (obj.get("title") or "document")
                        break
    elif args.source:
        src = os.path.expanduser(args.source)
        if not os.path.exists(src):
            print("No such file: %s" % src)
            print("Pass a path this machine can read, or use --from-gdoc <doc-id> for a "
                  "Google Doc.")
            return 1
        text = open(src, encoding="utf-8", errors="replace").read()
        origin = src
    else:
        print("Give a source: --from-gdoc <doc-id>, --from <path>, or --stdin.")
        return 1

    if not text.strip():
        print("That source is empty — nothing to record.")
        return 1

    os.makedirs(INPUTS, exist_ok=True)
    dest = os.path.join(INPUTS, "regimen.txt")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    lines = [l for l in text.splitlines() if l.strip()]
    print("Recorded %d line(s) from %s" % (len(lines), origin))
    facts = _write_patient_facts(text)
    if facts:
        print("Patient facts: %s -> inputs/patient.md" % facts)
    print("Now run:  python3 ~/.hermes/rx-review/rx.py stage")
    return 0


def derived_state():
    """Run artifacts that live OUTSIDE inputs/ and reports/, which reset must also take.

    reset used to glob only inputs/, raw/, supplements/ and reports/, so everything a run
    dropped at the top level survived it. That was invisible for a long time because the leftovers
    were being cleared by hand alongside each reset - which is exactly how a gap stays hidden.
    A reset that leaves state behind is worse than one that fails loudly:

      .phase.json      phase START timestamps. phase_end() subtracts one to report a duration
                       and phase_tokens() counts from it, so a stale entry silently attributes
                       yesterday's elapsed time and token count to today's run - and the whole
                       point of these timings is comparing serving configurations.
      sources/,        the fetched-source corpus and its locator index. Keyed by URL, so a stale
      locations.json   entry serves the PREVIOUS run's copy of a page to this run's audit.
      trend-*.md &c    strays. Cards are told to write to an ABSOLUTE reports path, but a model
                       that writes the basename relative lands here instead. Cleaned as run
                       output; see the misplacement itself, which is the more serious defect.
      *.lock           per-host fetch locks; a lock left by a killed worker is stale by definition.

    Deliberately a DECLARED list, not a blanket wipe of everything that is not a script:
    `salvage/` and `archive-*/` are kept on purpose and must survive a reset.
    """
    # __pycache__ is deliberately NOT here: importing rx.py recreates it immediately, so listing
    # it would mean reset never reports a clean result. It is build output, not run state, and
    # cannot carry data from one review into the next.
    import verify                                              # noqa: PLC0415 - avoids a cycle
    paths = [PHASE_FILE, verify.SOURCES, verify.LOCATIONS]
    for pat in ("trend-*.md", "LENS-*.md", "CONTEXT-AUDIT.md", "*.tmp"):
        paths += glob.glob(os.path.join(HOME, pat))
    for d in (os.path.join(HOME, ".fetchlocks"),
              os.path.expanduser(os.environ.get("ANALYSIS_FETCH_LOCKDIR")
                                 or "~/.hermes/.fetchlocks")):
        paths += glob.glob(os.path.join(d, "*.lock"))
    # The web-access skill's OWN fetched-text cache (WEB_CACHE) is deliberately NOT here. It used
    # to be swept unconditionally, on the reasoning that a page fetched in one review could then
    # answer a citation fetch in the next. That cross-run reuse is now a WANTED property: the same
    # substances are researched every run, the cache is expensive to refill, and its hit rate is
    # monitored on its own Grafana board. reset keeps it by default; only `--clear-web-cache`
    # drops it. The run's own derived artifacts (verify.LOCATIONS, and any leftovers in the
    # legacy verify.SOURCES corpus) still go every time — those are per-run state; the page
    # cache itself belongs to the skill and serves every run (2026-08-10).
    return [p for p in paths if os.path.exists(p)]


DOC_CACHE = os.path.expanduser("~/.hermes/cache/documents")
# The two web-access caches, treated as one unit: the fetcher's page-text cache and the search
# result cache. Both are shared, reused run-to-run on purpose, and expensive to refill, so reset
# ALWAYS leaves them alone unless --clear-web-cache is passed. (Standing rule, 2026-08-07.)
WEB_CACHE = os.path.expanduser("~/.hermes/cache/web-access/sources")
WEB_SEARCH_CACHE = os.path.expanduser("~/.hermes/cache/web-access/searches")


def web_cache_entries():
    """Files in the web-access skill's shared caches — the fetch (page-text) cache AND the search
    result cache — kept across runs by default.

    The ONE page cache every caller shares (2026-08-10) — the audit included; verify.SOURCES is
    a legacy per-run corpus that reset merely cleans up. Entries are keyed by URL/query and
    reused run-to-run on purpose since every review researches the same substances. reset
    REPORTS the count and only --clear-web-cache removes them.
    """
    out = []
    for d in (WEB_CACHE, WEB_SEARCH_CACHE):
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d)]
    return out


def cached_documents():
    """Lab PDFs still sitting in Hermes' upload cache after a reset.

    Every document sent to Hermes is cached under ~/.hermes/cache/documents and kept
    indefinitely. reset empties inputs/ but has never touched that cache, so a "clean" reset
    left every lab ever uploaded on disk - 239 of them when this was found - and the same
    document can be pulled back into a later run. That is the opposite of what reset promises.

    NOT deleted automatically. The cache is Hermes-wide: it holds documents from every skill
    and conversation, not just this one, and there is no marker saying which came from a lab
    upload. Guessing would delete someone else's file. reset REPORTS the count, and
    --clear-documents removes the ones that look like lab PDFs, so the choice is explicit and
    the user can see what is at stake first.

    Zip archives count too: staging unpacks them into inputs/raw, so a surviving zip re-enters
    a later run exactly like a surviving PDF.
    """
    if not os.path.isdir(DOC_CACHE):
        return []
    return [os.path.join(DOC_CACHE, f) for f in os.listdir(DOC_CACHE)
            if f.lower().endswith((".pdf", ".zip"))]


def board_task_ids(include_archived=True):
    """Every task id on the board, in every status."""
    cmd = [HERMES, "kanban", "--board", BOARD, "list", "--json"]
    if include_archived:
        cmd.append("--archived")
    out = sh(cmd)
    try:
        return [t["id"] for t in json.loads(out.stdout) if t.get("id")]
    except Exception:                                          # noqa: BLE001
        return []


def clear_board(verbose=True):
    """Empty the board by DELETING TASKS, never by deleting the board directory.

    reset used to do `boards rm --delete` then `boards create`, which unlinks kanban.db and
    makes a new one. That corrupted the database on 2026-07-29: the DASHBOARD is a separate
    long-running process (hermes-dashboard.service) that holds kanban.db open continuously,
    and - the part that made this so easy to miss - `hermes gateway restart --all` does not
    touch it. So the file went away while a live fd still pointed at the old inode, the
    dashboard kept writing through it, and integrity_check came back with a page referenced
    twice, NULLs in tasks.created_at, and four index rows missing. A whole run was lost.

    Deleting ROWS is safe with any number of readers: SQLite is built for concurrent access,
    and the file is never replaced underneath anyone. `archive` then `archive --rm` is the
    supported route - there is no bulk task-delete verb - and it leaves the board, its
    settings and its open handles intact.
    """
    ids = board_task_ids()
    if not ids:
        return 0
    # archive first: --rm only purges tasks that are already archived.
    sh([HERMES, "kanban", "--board", BOARD, "archive"] + ids)
    sh([HERMES, "kanban", "--board", BOARD, "archive", "--rm"] + ids)
    left = board_task_ids()
    if left and verbose:
        print("   WARNING: %d task(s) would not delete: %s"
              % (len(left), ", ".join(left[:5])))
    return len(ids) - len(left)


def declared_outputs(body):
    """Report basenames a card body tells the worker to write.

    Bodies are formatted with an ABSOLUTE reports path before they are sent, so the card that
    produces trend-creatinine.md literally contains "Write /home/.../reports/rx-review/
    trend-creatinine.md." Reading the requirement back off the body means no second list to
    keep in sync with fanout.py - the instruction IS the specification.
    """
    # Match a file named directly under EITHER the per-run dir (REPORTS -> .../current, what fanout
    # emits today) OR the reports root (.../reports/rx-review, the pre-per-run form some bodies and
    # the tests still use), each in absolute and tilde form. The filename group has no "/", so
    # ".../reports/rx-review/current/x.md" is only picked up by the REPORTS (current) base — never
    # double-counted.
    bases = set()
    for b in (REPORTS, REPORTS_ROOT):
        bases.add(b)
        bases.add(b.replace(os.path.expanduser("~"), "~"))
    out = set()
    for b in bases:
        out |= set(re.findall(r"%s/([A-Za-z0-9][A-Za-z0-9._-]*\.md)" % re.escape(b), body or ""))
    return out


# Where a stray can land. A worker told to write an absolute path but writing the basename
# instead drops the file in its cwd, which is the script directory or its kanban workspace.
def _stray_candidates(basename):
    roots = [HOME, os.getcwd(),
             os.path.expanduser("~/.hermes/kanban/boards/%s/workspaces" % BOARD)]
    hits = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        hits.append(os.path.join(r, basename))
        hits += glob.glob(os.path.join(r, "*", basename))
        hits += glob.glob(os.path.join(r, "*", "*", basename))
    return [h for h in hits if os.path.isfile(h) and os.path.dirname(h) != REPORTS]


def cmd_check_reports(args):
    """Verify every report a card was told to write actually landed in REPORTS.

    A card is told to write an ABSOLUTE path, but a model that writes the basename instead
    lands the file in its cwd. That happened to trend-creatinine.md: it sat in the script
    directory while the lens fan-out, the citation audit and the reconciler all scan REPORTS.
    The card completed, reported success, and its analysis was silently excluded from every
    stage meant to consume it - the worst failure shape in this pipeline, because nothing
    anywhere says a word.

    Strays are moved into REPORTS. Anything still missing is reported and exits non-zero, so
    the barrier card that runs this cannot pass a hole downstream.
    """
    tasks = []
    out = sh([HERMES, "kanban", "--board", BOARD, "list", "--json", "--archived"])
    try:
        tasks = json.loads(out.stdout)
    except Exception:                                          # noqa: BLE001
        print("could not read the board")
        return 1

    moved, missing, ok = [], [], 0
    for t in tasks:
        body = t.get("body") or t.get("description") or ""
        for name in sorted(declared_outputs(body)):
            dest = os.path.join(REPORTS, name)
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                ok += 1
                continue
            strays = _stray_candidates(name)
            if strays:
                src = max(strays, key=os.path.getsize)
                if args.dry_run:
                    moved.append((src, dest, t.get("title", "?")))
                else:
                    os.makedirs(REPORTS, exist_ok=True)
                    shutil.move(src, dest)
                    moved.append((src, dest, t.get("title", "?")))
                continue
            if (t.get("status") or "").lower() in ("done", "review"):
                missing.append((name, t.get("title", "?"), t.get("id", "?")))

    print("%d declared report(s) present in %s"
          % (ok, REPORTS.replace(os.path.expanduser("~"), "~")))
    for src, dest, title in moved:
        print("   RELOCATED %s\n             from %s  (%s)"
              % (os.path.basename(dest), src.replace(os.path.expanduser("~"), "~"), title))
    for name, title, tid in missing:
        print("   MISSING   %s — card '%s' (%s) completed without it" % (name, title, tid))

    if moved and not args.dry_run:
        rxkanban.announce(
            "Relocated %d report(s) that were written outside the reports directory: %s"
            % (len(moved), ", ".join(os.path.basename(d) for _s, d, _t in moved)))
    if missing:
        print("\n%d declared report(s) are missing with no stray copy found." % len(missing))
        return 1
    return 0


def _files_in(dirpath):
    """Every FILE directly in `dirpath`, DOTTED NAMES INCLUDED.

    `glob("*")` silently skips dotted names, so every hidden state file had to be remembered by
    hand in reset's path list — and the list is what kept being forgotten. `.regimen-review-pending`
    survived a reset, and the next run's Stage 3 barrier read it as "the review was already
    posted", blocked its card and posted nothing: a stalled pipeline waiting on a message the
    user never got (2026-08-10). Enumerating the directory removes the need to remember.
    Directories are left to the caller — `.xcribe/` and `raw/.duplicates/` are removed wholesale.
    """
    try:
        return [e.path for e in os.scandir(dirpath) if e.is_file()]
    except OSError:
        return []


def cmd_reset(args):
    """Wipe every card and every input/output so a review can start from scratch.

    Deliberately destructive and deliberately explicit: requires --confirm. Leaves the scripts,
    the profiles, and the user's own uploads alone — only run artifacts go.
    """
    derived = derived_state()
    # ``inputs/`` alone misses raw/ and supplements/ — the lab PDFs live one level down.
    # raw/.duplicates/ is the quarantine for content-duplicate uploads.
    files = sorted(_files_in(INPUTS) + _files_in(RAW) +
                   _files_in(os.path.join(RAW, ".duplicates")))
    photos = _files_in(PHOTOS)
    n_cards = len(board_task_ids())

    print("This will permanently delete:")
    print("   %d kanban card(s) on board '%s' (the board and its DB are kept)"
          % (n_cards, BOARD))
    print("   %d input file(s) incl. lab PDFs, regimen.txt, transcriptions" % len(files))
    print("   %d photo(s)" % len(photos))
    runs = run_dirs()
    if runs:
        print("   %d past run dir(s) in %s"
              % (len(runs), REPORTS_ROOT.replace(os.path.expanduser("~"), "~")))
        print("      %s" % ("WILL BE DELETED" if getattr(args, "clear_reports", False)
                            else "kept — the timestamped deliverables; use --clear-reports to remove them"))
    print("   %d derived artifact(s): %s"
          % (len(derived),
             ", ".join(sorted(os.path.basename(p) for p in derived)) or "none"))
    docs = cached_documents()
    if docs:
        print("   %d PDF(s) in Hermes' upload cache %s"
              % (len(docs), DOC_CACHE.replace(os.path.expanduser("~"), "~")))
        print("      %s — these SURVIVE reset and can re-enter a later run."
              % ("WILL BE DELETED" if getattr(args, "clear_documents", False)
                 else "kept; use --clear-documents to remove them"))
    web = web_cache_entries()
    if web:
        print("   %d file(s) in the web-access fetch + search caches %s"
              % (len(web), os.path.dirname(WEB_CACHE).replace(os.path.expanduser("~"), "~")))
        print("      %s"
              % ("WILL BE DELETED" if getattr(args, "clear_web_cache", False)
                 else "kept; use --clear-web-cache to remove them"))
    if not args.confirm:
        print("\nNothing was deleted. Re-run with --confirm to confirm:")
        print("   python3 ~/.hermes/rx-review/rx.py reset --confirm")
        return 1

    # Tasks are deleted; the board and its kanban.db are left in place. See clear_board().
    n_cleared = clear_board()
    print("\n   board '%s' emptied (%d task(s) deleted, kanban.db left in place)"
          % (BOARD, n_cleared))

    for f in files + photos:
        try:
            os.remove(f)
        except OSError as e:
            print("   could not remove %s: %s" % (f, e))
    for p in derived:
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError as e:
            print("   could not remove %s: %s" % (p, e))
    shutil.rmtree(os.path.join(RAW, ".duplicates"), ignore_errors=True)
    shutil.rmtree(XCRIBE, ignore_errors=True)          # dotted, so glob("*") above skips it
    # Run dirs are the deliverables — kept by default. Only the `current` pointer is dropped, so the
    # next review's start_run() creates a fresh timestamped dir; --clear-reports purges the history.
    if getattr(args, "clear_reports", False):
        for r in runs:
            shutil.rmtree(r, ignore_errors=True)
        if runs:
            print("   %d run dir(s) removed from %s"
                  % (len(runs), REPORTS_ROOT.replace(os.path.expanduser("~"), "~")))
    try:
        os.remove(CURRENT_LINK)
    except OSError:
        pass
    for d in (RAW, PHOTOS, REPORTS_ROOT):
        os.makedirs(d, exist_ok=True)
    print("   %d file(s) removed" % (len(files) + len(photos)))
    if derived:
        print("   %d derived artifact(s) removed" % len(derived))
    if getattr(args, "clear_documents", False):
        gone = 0
        for d in cached_documents():
            try:
                os.remove(d)
                gone += 1
            except OSError as e:
                print("   could not remove %s: %s" % (d, e))
        print("   %d cached upload(s) removed from %s"
              % (gone, DOC_CACHE.replace(os.path.expanduser("~"), "~")))
    if getattr(args, "clear_cache", False):
        n = rxcache.clear()
        print("   cache cleared (%d verified transcription(s) discarded)" % n)
    else:
        st = rxcache.stats()
        if st["transcriptions"]:
            print("   cache KEPT: %d verified transcription(s) — a re-upload of the same PDF\n"
                  "               will not be transcribed again. Use --clear-cache to drop it."
                  % st["transcriptions"])
    if getattr(args, "clear_web_cache", False):
        gone = 0
        for f in web_cache_entries():
            try:
                shutil.rmtree(f) if os.path.isdir(f) else os.remove(f)
                gone += 1
            except OSError as e:
                print("   could not remove %s: %s" % (f, e))
        print("   web-access fetch + search caches cleared (%d file(s) discarded)" % gone)
    else:
        _w = web_cache_entries()
        if _w:
            print("   web caches KEPT: %d fetched page(s)/search(es) — the next review reuses them\n"
                  "               instead of re-fetching. Use --clear-web-cache to drop them." % len(_w))

    # Workers spawned for cards that no longer exist neither notice nor stop: they keep
    # burning model tokens against deleted tasks until their runtime expires (observed
    # three runs running). Reset owns the board's death, so it reaps its workers too.
    try:
        import reap as _reap
        _killed, _desc = _reap.reap(dry_run=getattr(args, "dry_run", False))
        if _desc:
            print("\n   %d orphaned worker(s) %s:"
                  % (len(_desc), "found (dry-run)" if getattr(args, "dry_run", False) else "terminated"))
            for _l in _desc:
                print("      " + _l)
    except Exception as e:                       # reaping must never fail the reset
        print("   (worker reaper skipped: %s)" % e)

    # A cleared board with the dispatcher off looks exactly like a hung pipeline: cards sit at
    # `ready` and nothing spawns. Reset is preparation for the NEXT run, so say plainly whether
    # that run can actually start.
    _disp = ""
    for _cfg in (os.path.expanduser("~/.hermes/profiles/archivist/config.yaml"),
                 os.path.expanduser("~/.hermes/config.yaml")):
        try:
            _t = open(_cfg, encoding="utf-8").read()
        except OSError:
            continue
        _m = re.search(r"^\s*dispatch_in_gateway:\s*(\S+)", _t, re.M)
        if _m and _m.group(1).lower() in ("false", "no", "off"):
            _disp = _cfg.replace(os.path.expanduser("~"), "~")
            break
    if _disp:
        print("\n   !! kanban dispatch is DISABLED in %s" % _disp)
        print("      A new board will sit at 'ready' and spawn nothing. Re-enable with:")
        print("      sed -i 's/dispatch_in_gateway: false/dispatch_in_gateway: true/' \\")
        print("         ~/.hermes/config.yaml ~/.hermes/profiles/archivist/config.yaml")
        print("      hermes gateway restart --profile archivist")

    print("\nClean. Start a new review by talking to Hermes:  /rx-review")
    return 0


# Vendors abbreviate the same analyte differently on the same draw. "CHOL/HDLC RATIO" and
# "Cholesterol/HDL ratio" are one test, and "TRIGLYCERIDES" and "Triglyceride" are one test -
# but they normalised apart, so the advanced panel's value was orphaned from five standard
# readings and neither superseded the other nor joined its trend.
MARKER_SYNONYMS = {"chol": "cholesterol", "hdlc": "hdl", "ldlc": "ldl",
                   "trig": "triglyceride", "trigs": "triglyceride", "tg": "triglyceride",
                   "apob": "apolipoprotein b", "lpa": "lipoprotein a"}


def _fold_token(tok):
    """One token reduced to its canonical form: vendor synonym, then a cautious de-plural.

    The plural rule deliberately skips -ss, -us and -is, so "status" and "analysis" are left
    alone. Blanket stemming in a clinical vocabulary is how two different tests merge into one,
    and a merged marker silently supersedes across analytes.
    """
    tok = MARKER_SYNONYMS.get(tok, tok)
    if len(tok) > 4 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        tok = tok[:-1]
    return tok


def _flat(s):
    """Alphanumerics only, lowercased - for asking whether a name occurs in a document.

    Tolerant of the ways a PDF breaks a name that a table writes on one line: case,
    punctuation, and whitespace including line wraps.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _scale(value):
    """`Qn` when the result is a number, `Ord` when it is a graded word.

    Already computed and discarded: _numeric() returns None for "Negative" precisely because
    that is not a quantity. Recording the distinction instead of throwing it away is what
    stops a dipstick NEGATIVE being compared against a serum concentration.
    """
    return "Qn" if _numeric(value) is not None else "Ord"


# Only what a heading LITERALLY says. A panel called "COMPREHENSIVE METABOLIC PANEL" is serum
# in practice, but inferring that is exactly the over-specification this design refuses: an
# unstated specimen stays unknown, and unknown never equals "urine", so it still refuses to
# merge rather than guessing.
_SPECIMEN_WORDS = ((r"\burin|\bdipstick", "urine"),
                   (r"\bserum|\bplasma", "serum/plasma"),
                   (r"\bwhole blood", "whole blood"),
                   (r"\bcsf|cerebrospinal", "csf"),
                   (r"\bstool|\bfecal|\bfaecal", "stool"),
                   (r"\bsaliva", "saliva"))


def _norm_specimen(text):
    """The specimen a heading or qualifier names, or "" when it names none."""
    t = (text or "").lower()
    for pat, name in _SPECIMEN_WORDS:
        if re.search(pat, t):
            return name
    return ""


def observation_key(row):
    """What makes two rows the SAME observation: analyte, specimen and scale.

    A reading is not identified by its name. The Function panel measures glucose, protein and
    bilirubin in BLOOD and again in URINE by dipstick, and the transcriber writes both as
    "GLUCOSE" unless a heading made it qualify them - so on the name alone two correct
    transcriptions look like one reading with two contradictory values. That produced six
    false disagreements and blocked the research stage for hours on 2026-07-31, and the same
    collapse merges "PROTEIN, TOTAL" with "PROTEIN" and "BILIRUBIN, TOTAL" with "BILIRUBIN".

    Both extra axes are OBSERVED, never inferred: scale from whether the value is a number,
    specimen from the panel heading the row sits under (which was printed on the page and
    simply never captured). A row whose specimen is unrecorded gets "", which equals no other
    specimen - so the failure mode is "refuses to merge", not "merges wrongly".
    """
    specimen = _norm_specimen(row.get("specimen", "")) or _norm_specimen(row.get("marker", ""))
    return (_norm_marker(row.get("marker", "")), specimen, _scale(row.get("value", "")))


def series_for(series, name):
    """Readings for a component when it identifies exactly ONE observation, else [].

    marker_series() is keyed by observation, but derived-marker arithmetic and the
    marker-family rules address analytes by NAME, out of narrative text. Where a component
    names one observation the lookup is unambiguous; where it names several - blood and urine
    protein, say - the text has not said which, and applying a rule to the wrong specimen is
    worse than not applying it.
    """
    comp = _norm_marker(name)
    hits = [v for k, v in series.items() if k[0] == comp]
    return hits[0] if len(hits) == 1 else []


def resolve_component(index, name):
    """The one entry in `index` whose component matches `name`, or None if 0 or several.

    Narrative sections ("LDL-CHOLESTEROL: 127 H (ref <100 mg/dL)") carry a name and nothing
    else - no unit, no specimen, no scale - so they cannot build an observation key. Where the
    component identifies exactly one observation the answer is unambiguous; where it identifies
    several, the text genuinely does not say which, and every caller here already has a
    "cannot tell" branch that keeps the entry rather than guessing.
    """
    comp = _norm_marker(name)
    if not comp:
        return None
    hits = [v for k, v in index.items() if k[0] == comp]
    return hits[0] if len(hits) == 1 else None


def _norm_marker(name):
    """Canonical marker key, tolerant of how different labs write the same test.

    Panels disagree on naming, and a mismatch silently defeats the newest-draw-wins rule:
    "CHOLESTEROL, TOTAL" (216 H on one panel) never matched "Cholesterol" (152, in range, on a
    later one), so the stale high value was still reported and researched.

    Handled: case, punctuation, parentheticals ("(direct)", "(calc)"), word order, and filler
    words. "cholesterol" is dropped only when something else identifies the test, so
    HDL/LDL/non-HDL stay distinct from total cholesterol.
    """
    n = (name or "").strip()
    n = value_without_flag(n)                      # trailing lab flag
    # Parentheticals are NOT all noise. "(calc)" / "(direct)" are method details and can be
    # dropped, but "(urinalysis)" / "(Dipstick)" identify the SPECIMEN — dropping those merged
    # urine glucose with serum glucose, so a normal blood result could supersede an abnormal
    # urine one. Keep anything that is not a known method qualifier.
    METHOD_ONLY = {"calc", "calculated", "direct", "est", "estimated", "ia",
                   "serum", "plasma", "fasting", "measured"}

    def _paren(mo):
        inner = re.sub(r"[^A-Za-z0-9 ]+", " ", mo.group(1)).lower().split()
        return " " if inner and all(w in METHOD_ONLY for w in inner) else " " + " ".join(inner) + " "

    n = re.sub(r"\(([^)]*)\)", _paren, n)
    n = re.sub(r"[^A-Za-z0-9%]+", " ", n).lower()
    NOISE = {"total", "direct", "serum", "plasma", "level", "levels", "calc",
             "calculated", "ia", "w", "with", "test", "measured"}
    toks = [_fold_token(t) for t in n.split() if t and t not in NOISE]
    if len(toks) > 1 and "cholesterol" in toks:
        toks = [t for t in toks if t != "cholesterol"]
    if not toks:
        toks = [t for t in n.split() if t] or [n.strip()]
    return " ".join(sorted(toks))


# A transaminase, in the normalised forms rx.py's own matching produces. The trend-triage
# "ordinary variation" band is most likely to dismiss exactly this family - the liver's canary -
# and a dismissed transaminase trend is the one the user most needs researched. One predicate,
# used by the deterministic gate in fanout's trend dispatch; the normalised set is checked here,
# next to the normaliser, so the two never drift apart.
TRANSGAMINASES = ("alt", "ast", "alt sgpt", "ast sgot")


def is_transaminase(name):
    """True when this marker is a liver transaminase (AST or ALT, however the vendor spells it)."""
    return _norm_marker(name) in TRANSGAMINASES


def _norm_date(t):
    """Normalise a date to YYYY-MM-DD so a table cell and a section heading compare equal."""
    t = (t or "").strip()
    # ISO FIRST, and both patterns bounded so neither can match inside a longer number.
    # The US pattern used to run first and matched the TAIL of an ISO date: "2026-03-31"
    # contains "26-03-31", read as 26/03/31 -> 2031-26-03. Every ISO-dated draw was therefore
    # stamped years away - 2026-03-31 became 2031 (the future) and 2025-08-13 became 2013 -
    # which made the oldest lipid panel the NEWEST draw in every series. Its particle counts
    # then survived supersession while the genuinely newer panels were discarded, and trends
    # ran backwards: cholesterol falling 216 -> 150 was reported as rising 150 -> 216.
    m = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", t)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)", t)
        if not m:
            return ""
        mo, d, y = m.groups()
        y = ("20" + y) if len(y) == 2 else y
    mo, d = int(mo), int(d)
    # A date that cannot exist is a parse failure, not a date. Returning "" makes the caller
    # treat the row as undated rather than silently sorting it to one end of the series.
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return "%s-%02d-%02d" % (y, mo, d)


def _parse_since(t):
    """Normalize a medication start marker to YYYY-MM-DD, or "" if unparseable.

    `YYYY-MM` splits at the first of that month (the common granularity the user writes);
    anything else delegates to `_norm_date`. An unparseable value returns "" — a bad start
    date is a parse failure, never a silently one-sided split.
    """
    t = (t or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        y, mo = m.groups()
        mo = int(mo)
        return ("%s-%02d-01" % (y, mo)) if 1 <= mo <= 12 else ""
    return _norm_date(t)


# Three readings is the smallest series that distinguishes a direction from a single change.
MIN_TREND_POINTS = 3


def _numeric(value):
    """The leading number in a lab value, or None. "141 H" -> 141.0, "Negative" -> None."""
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", value_without_flag(value or ""))
    return float(m.group(1)) if m else None


def before_after(marker, since):
    """Split one marker's dated series at `since`. Arithmetic only — no judgement, no drug knowledge.

    The CALLER (an LLM card) decides which markers a substance moves; this only splits the
    numbers a confirmed lab series already holds. The marker is resolved through `series_for`,
    so an ambiguous name (blood vs urine protein) returns no series rather than a guess, and
    `marker_series()` has already deduplicated overlapping draws by (marker, date).
    """
    pts = series_for(marker_series(), marker)     # [] when absent OR ambiguous
    since_d = _parse_since(since)
    base = {"marker": marker, "since": since_d, "pre": [], "post": [],
            "pre_n": 0, "post_n": 0, "baseline": None, "endpoint": None,
            "delta": None, "pct": None, "direction": None, "too_early": True}
    if not pts:
        return dict(base, found=False,
                    reason="no single reading series (not measured, or ambiguous name)")
    if not since_d:
        return dict(base, found=True, error="unparseable start date: %r" % since)
    pre = [(d, n) for d, n, _ in pts if d < since_d]
    post = [(d, n) for d, n, _ in pts if d >= since_d]
    baseline = pre[-1][1] if pre else None      # last pre-start draw = baseline
    endpoint = post[-1][1] if post else None    # latest post-start draw
    delta = (endpoint - baseline) if (baseline is not None and endpoint is not None) else None
    pct = (100.0 * delta / baseline) if (delta is not None and baseline) else None
    direction = None
    if len(post) >= 2:
        rising = all(b[1] > a[1] for a, b in zip(post, post[1:]))
        falling = all(b[1] < a[1] for a, b in zip(post, post[1:]))
        direction = "rising" if rising else ("falling" if falling else "mixed")
    return dict(base, found=True, pre=pre, post=post, pre_n=len(pre), post_n=len(post),
                baseline=baseline, endpoint=endpoint, delta=delta, pct=pct,
                direction=direction, too_early=(len(post) < 2))


def marker_series():
    """{marker: [(date, number, row)]} in date order, one reading per draw, numerics only.

    Deduplicated by (marker, date): overlapping panel PDFs transcribe the same draw twice, and
    a repeated point is not a trend.
    """
    series = {}
    for r in _lab_rows():
        name = r.get("marker") or ""
        date = _norm_date(r.get("date", ""))
        num = _numeric(r.get("value", ""))
        if not name or not date or num is None:
            continue
        series.setdefault(observation_key(r), {})[date] = (date, num, r)
    return {k: [v[d] for d in sorted(v)] for k, v in series.items()}


def trends(min_points=MIN_TREND_POINTS):
    """Markers whose most recent readings move consistently in one direction.

    A trend is clinically interesting even when every value sits INSIDE the reference range -
    a creatinine walking 0.9 -> 1.1 -> 1.3 is normal at every single draw and is still the most
    informative thing in the panel. Out-of-range detection cannot see it by construction,
    because nothing is ever out of range. That is why this is a separate pass and not a filter
    on the existing one.

    Deliberately mechanical: direction and magnitude are arithmetic, not judgement. Whether a
    drift matters is for the analysis to argue, so the reference width is reported rather than
    used to suppress anything - a guard that drops a real signal is worse than one that passes
    a dull one through.
    """
    out = []
    for key, pts in marker_series().items():
        if len(pts) < min_points:
            continue
        tail = pts[-min_points:] if len(pts) >= min_points else pts
        # Extend backwards while the direction holds, so a 5-point run reports as 5.
        i = len(pts) - min_points
        rising = all(b[1] > a[1] for a, b in zip(tail, tail[1:]))
        falling = all(b[1] < a[1] for a, b in zip(tail, tail[1:]))
        if not (rising or falling):
            continue
        while i > 0:
            a, b = pts[i - 1], pts[i]
            if (rising and b[1] > a[1]) or (falling and b[1] < a[1]):
                i -= 1
            else:
                break
        run = pts[i:]
        first, last = run[0], run[-1]
        row = last[2]
        span = _ref_width(row.get("reference range", ""))
        delta = last[1] - first[1]
        out.append({
            "marker": (row.get("marker") or key).strip(),
            "direction": "rising" if rising else "falling",
            "points": len(run),
            "series": [(d, n) for d, n, _ in run],
            "delta": delta,
            "pct": (100.0 * delta / first[1]) if first[1] else None,
            "ref": row.get("reference range", ""),
            "ref_width": span,
            "delta_over_ref": (abs(delta) / span) if span else None,
            "unit": row.get("unit", ""),
            "latest_value": row.get("value", ""),
            "in_range_throughout": not any(
                _flagged_value(p[2].get("value", "")) for p in run),
        })
    return sorted(out, key=lambda t: (-(t["delta_over_ref"] or 0), t["marker"]))


def _write_patient_facts(text):
    """Write inputs/patient.md from `Name:` / `Age:` / `DOB:` lines of the patient document.

    The patient's Google Doc is the ONE input document: regimen and patient facts arrive
    together, so the facts are materialised at ingest the same way the regimen text is -
    extracted here, written to patient.md, read from there. Only lines the patient_age() reader
    understands are copied, in a fixed order (Name, Age, DOB), and a re-ingest REPLACES the file,
    so the document stays the single source of truth. It never DELETES: a document that drops its
    fact lines leaves the last recorded facts in place - a stale, visible age beats a silent
    score computed from nothing, and nothing else in the pipeline writes this file.
    """
    facts = []
    m = re.search(r"^\s*Name\s*[:=]\s*(\S.*?)\s*$", text, re.I | re.M)
    if m:
        facts.append(("Name", m.group(1)))
    m = re.search(r"^\s*Age\s*[:=]\s*(\d{1,3})\b.*$", text, re.I | re.M)
    if m:
        facts.append(("Age", m.group(1)))
    m = re.search(r"^\s*(?:DOB|Date of birth)\s*[:=]\s*([^\s(]+)", text, re.I | re.M)
    if m and _norm_date(m.group(1)):
        facts.append(("DOB", m.group(1)))
    if not facts:
        return None
    lines = [
        "# Patient facts - materialised from the patient document by `rx.py regimen`.",
        "# The document is the single input; this file is what the pipeline reads from.",
    ]
    lines += ["%s: %s" % (label, value) for label, value in facts]
    with open(os.path.join(INPUTS, "patient.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return ", ".join(label for label, _ in facts)


def patient_age():
    """The user's age, from inputs/patient.md - an explicit `Age:` line, else computed from `DOB:`.

    patient.md is materialised from the patient's single input document at `regimen` ingest
    (see _write_patient_facts), which is why the reader reads a file rather than the document:
    the document is the surface, the file is what the pipeline works from. FIB-4 (and any
    age-weighted score) is the first pipeline need for the user's age, which the pipeline has
    never carried: every lab value is a per-marker reading and age is a property of the person.
    0 means "not recorded", which a caller reports as not-computable rather than guessing -
    an invented age in a clinical score is worse than no score.
    """
    path = os.path.join(INPUTS, "patient.md")
    if not os.path.exists(path):
        return 0
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^\s*Age\s*[:=]\s*(\d{1,3})\b", txt, re.I | re.M)
    if m:
        return int(m.group(1))
    m = re.search(r"^\s*(?:DOB|Date of birth)\s*[:=]\s*(\S+)", txt, re.I | re.M)
    if m:
        d = _norm_date(m.group(1))
        if d:
            y, mo, dd = map(int, d.split("-"))
            today = time.gmtime()
            age = today.tm_year - y
            if (today.tm_mon, today.tm_mday) < (mo, dd):
                age -= 1
            return age
    return 0


def fib4(age=None):
    """The FIB-4 liver-fibrosis risk score, from the newest draw that reports all three inputs.

    FIB-4 = (age x AST) / (platelets x sqrt(ALT)). It is the single more-informative number a
    panel already carries but does not surface: a normal AST/ALT does NOT exclude a rising
    FIB-4, so the risk that matters here is a score, not an enzyme.

    Computed only from ONE draw that measured AST, ALT and a platelet count together. FIB-4 is a
    single-time-point formula: its validation used all four values from the same occasion, so a
    platelet count from one panel paired with an AST from another would be a value the score was
    never validated on, and reference ranges across draws are only comparable within one assay.
    (This is stricter than DERIVED_MARKERS, whose inputs may come from separate draws because
    non-HDL is an exact definition - FIB-4 is a validated ratio, not an identity.) When no draw
    has all three, the score is not computed; it is not invented.
    """
    age = patient_age() if age is None else age
    if not age:
        return {"found": False,
                "reason": "no patient age recorded (add `Age: <n>` to inputs/patient.md)"}
    by_date = {}                                       # date -> {ast, alt, plt}
    for key, pts in marker_series().items():
        name = (key[0] if isinstance(key, tuple) else str(key)).lower()
        if re.search(r"\bast\b|\bsgot\b", name):
            kind = "ast"
        elif re.search(r"\balt\b|\bsgpt\b", name):
            kind = "alt"
        elif re.search(r"platelet|thromb", name):
            kind = "plt"
        else:
            continue
        for d, n, _row in pts:
            by_date.setdefault(d, {})[kind] = n
    full = sorted(d for d, v in by_date.items() if {"ast", "alt", "plt"} <= set(v))
    if not full:
        present = set().union(*[set(v) for v in by_date.values()]) if by_date else set()
        missing = sorted({"ast", "alt", "plt"} - present)
        return {"found": False,
                "reason": "no single draw reports AST, ALT and a platelet count together"
                          + ("; no draw carries %s" % ", ".join(missing) if missing
                             else "; the panels reporting them are different draws")}
    d = full[-1]
    v = by_date[d]
    score = (age * v["ast"]) / (v["plt"] * math.sqrt(v["alt"]))
    return {"found": True, "date": d, "age": age, "ast": v["ast"], "alt": v["alt"],
            "plt": v["plt"], "score": score, "tier": fib4_tier(score)}


def fib4_tier(score):
    """The risk band a FIB-4 falls in, with the conventional next step as a hint - not a call."""
    if score < 1.30:
        return "LOW risk (<1.30)"
    if score < 3.27:
        return "INDETERMINATE (1.30-3.27) - consider non-invasive fibrosis assessment"
    return "HIGH risk (>3.27) - consider non-invasive fibrosis assessment"


def cmd_fib4(args):
    """Compute the FIB-4 liver-fibrosis risk score from the transcribed labs."""
    r = fib4()
    if getattr(args, "json", False):
        print(json.dumps(r, indent=2))
        return 0 if r["found"] else 1
    if not r["found"]:
        print("FIB-4 not computable: %s" % r["reason"])
        return 1
    print("FIB-4 = %.3f   [%s]" % (r["score"], r["tier"]))
    print("  as of %s:  AST %g, ALT %g, platelets %g (age %d)"
          % (r["date"], r["ast"], r["alt"], r["plt"], r["age"]))
    print("  FIB-4 is a fibrosis RISK score, more informative than the transaminases alone: a "
          "normal AST/ALT does not rule out a rising FIB-4.")
    return 0


def _ref_width(ref):
    """Width of a "53 - 128" style reference interval, or None."""
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)", ref or "")
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    return (hi - lo) or None


def _flagged_value(value):
    """True when the lab itself marked this reading abnormal."""
    return bool(re.search(r"\b(H|L|HH|LL|AB)\b", (value or "").strip()))


def _latest_per_marker(rows):
    """Newest row per marker, plus every superseded one.

    Labs overlap: a panel in May and another in July both report ALT. Only the NEWEST draw
    describes the user's current state, so an older out-of-range value must not generate a
    question or a research card — it has been superseded. The older rows stay in labs.md
    because trend matters to the research stage; they just do not count as "currently out
    of range".
    """
    by_marker = {}
    for r in rows:
        key = observation_key(r)
        if not key[0]:
            continue
        by_marker.setdefault(key, []).append(r)
    latest, superseded = {}, []
    for key, rs in by_marker.items():
        rs_sorted = sorted(rs, key=lambda x: _norm_date(x.get("date", "")) or "")
        latest[key] = rs_sorted[-1]
        superseded.extend(rs_sorted[:-1])
    return latest, superseded


# "(ref: 53 - 128)" / "(reference range 53 - 128)" - a formatting difference between two
# transcriptions of the same draw, not a different finding.
_REF_NOTE_RE = re.compile(r"\s*\(\s*(?:ref|reference range)\b[^)]*\)\s*$", re.I)


# A marker that a NEWER panel does not measure directly but fully DEFINES is not stale - it is
# computable. non-HDL cholesterol is total cholesterol minus HDL-C by definition, so a later
# basic panel reporting both supersedes an earlier advanced panel's direct figure. This is
# arithmetic, not inference: no claim is made about one analyte from a different one. Anything
# needing a correlation rather than a definition does NOT belong here.
DERIVED_MARKERS = [
    {
        "marker": r"^hdl non$|^non hdl",
        "inputs": ["cholesterol", "hdl"],
        "fn": lambda v: v["cholesterol"] - v["hdl"],
        "why": "non-HDL cholesterol is defined as total cholesterol minus HDL-C",
    },
]

# Markers that travel together on one requisition. When a newer draw re-runs a family, the
# analytes that draw did NOT include are not superseded - nothing newer measured them - but
# they are no longer current either. Reporting them undated as though they were today's result
# is what made a 57-day-old particle count look like a live finding.
MARKER_FAMILIES = [
    {"name": "lipid",
     "members": r"cholesterol|\bldl\b|\bhdl\b|triglycerid|vldl|apolipoprotein|lipoprotein",
     "anchors": ["cholesterol", "hdl", "ldl", "triglyceride"]},
]


def _reading_on_or_after(marker_key, date):
    """The newest reading of this marker on/after `date`, or None."""
    got = series_for(marker_series(), marker_key)
    later = [p for p in got if p[0] >= date]
    return later[-1] if later else None


# Markers dropped because a newer panel computes them exactly. Filled by drop_derived and read
# by the report, so the reason reaches the user instead of stdout. Cleared per call.
DERIVED_DROPPED = []


def drop_derived(entries):
    del DERIVED_DROPPED[:]
    """Drop findings a newer panel can compute exactly, and say what it computes to."""
    kept = []
    for date, text in entries:
        name = re.split(r"[:—–]", text, 1)[0].strip()
        key = _norm_marker(name)
        # marker_series() keys on NORMALISED dates; the narrative heading is raw ("03/31/2026").
        # Comparing the two lexicographically made a marker look newer than itself.
        date = _norm_date(date) or date
        hit = None
        for rule in DERIVED_MARKERS:
            if not re.search(rule["marker"], key, re.I):
                continue
            vals, newest = {}, date
            for need in rule["inputs"]:
                pt = _reading_on_or_after(need, date)
                if pt is None or pt[0] <= date:
                    vals = None
                    break
                vals[need] = pt[1]
                newest = max(newest, pt[0])
            if vals:
                hit = (rule, rule["fn"](vals), newest)
                break
        if hit:
            rule, computed, when = hit
            # RECORDED, not printed. This was the only one of six sibling filters that wrote to
            # stdout, so every caller - including cards whose summary is their output - carried
            # three lines of diagnostic above the actual report. But a flagged marker being
            # dropped is not nothing, so it goes where the user reads: labs-report names it
            # under "not asked about".
            DERIVED_DROPPED.append((name, computed, when, rule["why"]))
            continue
        kept.append((date, text))
    return kept


def mark_unrepeated(entries):
    """Label findings whose family was re-drawn later without them.

    Not dropped: an abnormal ApoB from two months ago is still the most recent ApoB there is,
    and "not re-measured" is itself worth acting on. But it must not read as today's number.
    """
    series = marker_series()
    out = []
    for date, text in entries:
        name = re.split(r"[:—–]", text, 1)[0].strip()
        key = _norm_marker(name)
        # Both sides of every date comparison below must be normalised. They were not, and
        # "2026-03-31" > "03/31/2026" is lexicographically true, so every marker appeared to
        # have a reading newer than its own draw and no entry was ever labelled stale.
        date = _norm_date(date) or date
        note = ""
        for fam in MARKER_FAMILIES:
            if not re.search(fam["members"], key, re.I):
                continue
            newer = [a for a in fam["anchors"]
                     if any(p[0] > date for p in series_for(series, a))]
            if len(newer) >= 2 and not any(p[0] > date for p in series_for(series, name)):
                when = max(p[0] for a in newer for p in series_for(series, a) if p[0] > date)
                note = (" [last measured %s; the %s panel was re-drawn %s WITHOUT this "
                        "marker, so it is the most recent value but not a current one]"
                        % (date or "undated", fam["name"], when))
            break
        out.append((date, text + note) if note else (date, text))
    return out


def out_of_range_entries():
    """THE list of currently out-of-range findings. One implementation, every caller.

    There used to be two: check_labs() scraped the "## Out of range" narrative section, while
    cmd_labs_report re-derived flags from the table rows. They disagreed - the gate card said
    18 (including LDL-CHOLESTEROL 127 from 03/31, superseded by 65 on 05/27) while the report
    said 14, and even once both said 14 they named different markers, one including GFR and
    the other Urobilinogen. Two answers to "what is abnormal" means at least one is wrong, and
    the user is the one who has to notice.
    """
    labs = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(labs):
        return []
    txt = open(labs, encoding="utf-8").read()
    m = re.search(r"^(#+)\s*Out of range.*$", txt, re.I | re.M)
    if not m:
        return []
    lvl = len(m.group(1))
    sec = txt[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % lvl, sec, re.M)
    sec = sec[:nxt.start()] if nxt else sec
    # Track the date sub-heading each entry sits under (### 05/27/2026), so supersede can
    # compare draws. Without the date, "is this the newest value" is unanswerable.
    # A draw's date arrives as either "### 05/27/2026" or "**05/27/2026**" - the merge is
    # written by a model and has used both. Accept both, and tell them apart from findings by
    # the BULLET, not by the leading character: "**05/27/2026**" starts with "*", so a
    # startswith(("-", "*")) test files the date itself as a finding AND leaves cur_date empty,
    # which disables supersede entirely. That is exactly what happened - Alkaline Phosphatase
    # was reported four times across four draws, and a year of superseded values reached the
    # confirmation gate as though every one of them were current.
    bullet = re.compile(r"^[-*]\s+")
    dated, cur_date = [], ""
    for l in sec.splitlines():
        t = l.strip()
        if not t:
            continue
        if not bullet.match(t):
            h = re.match(r"^#+\s*(.+?)\s*$", t) or re.match(r"^\*\*\s*(.+?)\s*\*\*:?$", t)
            if h:
                cur_date = h.group(1).strip()
            continue
        dated.append((cur_date, bullet.sub("", t)))
    # Overlapping panel PDFs transcribe the same draw twice, and the two transcriptions format
    # the reference note differently - "Iron: 141 H" and "Iron: 141 H (ref: 35 - 140)" are one
    # finding reported twice. Deduplicate on the finding itself and keep the longer text, which
    # is the one carrying the range.
    seen = {}
    for date, text in dated:
        key = (date, _REF_NOTE_RE.sub("", text).strip().lower())
        if key not in seen or len(text) > len(seen[key][1]):
            seen[key] = (date, text)
    dated = [seen[k] for k in seen]
    # drop_superseded flattens (date, text) to text, so the two date-aware passes run first.
    dated = mark_unrepeated(drop_derived(dated))
    return drop_not_interpretable(
        drop_superseded_by_method(drop_marked_normal(drop_superseded(dated))))


def out_of_range_markers():
    """Normalised marker NAMES for the findings above.

    Only safe for asking "was this marker ever abnormal". It cannot answer "is THIS row
    abnormal" - see out_of_range_keys(), which is what membership tests want.
    """
    return {_norm_marker(re.split(r"[:—–]", e, 1)[0].strip())
            for e in out_of_range_entries()}


_ENTRY_DATE_RE = re.compile(r"\[([^\]]+)\]\s*$")


def out_of_range_keys():
    """(date, marker, value) identifying the exact ROW each finding names.

    A marker is not abnormal; a READING of it is. Keying membership on the name alone meant
    that once Cholesterol was 222 H in Dec 2025, every later Cholesterol row matched too - so
    152 mg/dL against a 108-199 range was reported to the user as "flagged out of range" and,
    in the same message, as "no longer out of range, normal on the newest". Ten of the fourteen
    findings on one draw were that bug (2026-07-30). The triple matches the key cmd_labs_report
    builds per row, so a finding pins one reading on one date and nothing else.
    """
    keys = set()
    for entry in out_of_range_entries():
        date = ""
        m = _ENTRY_DATE_RE.search(entry)
        if m:
            date = _norm_date(m.group(1).strip())
            entry = _ENTRY_DATE_RE.sub("", entry).strip()
        head, _sep, rest = entry.partition(":")
        value = value_without_flag(_REF_NOTE_RE.sub("", rest).strip())
        # Built through the same observation_key as a table row, from a synthetic row, so the
        # two sides of every membership test have the same SHAPE. The narrative carries no
        # specimen column, so its specimen comes from the name if qualified and is otherwise
        # "" - see keys_match, which treats "" as "the text did not say".
        keys.add((date, observation_key({"marker": head.strip(), "value": value}), value))
    return keys


def keys_match(a, b):
    """Do two (date, observation, value) keys name the same finding?

    Component, scale and value must agree exactly. Specimen agrees when both sides state one,
    and an unstated specimen ("" - which is every narrative entry, since the Out of range
    section has no column for it) matches either way. Scale carries most of the weight here:
    a urine dipstick result is Ord and a serum concentration is Qn, so the pair that collided
    on the name alone is still kept apart even when neither side names a specimen.
    """
    if a[0] != b[0] or a[2] != b[2]:
        return False
    (ca, sa, la), (cb, sb, lb) = a[1], b[1]
    return ca == cb and la == lb and (not sa or not sb or sa == sb)


# Some markers are measured by more than one method, and the methods are not equally
# trustworthy. Recency cannot settle this: a newer draw by an inferior method does not
# supersede an older one by the definitive method. Each entry is (marker family, pattern that
# identifies the definitive method, why) - the pattern is matched against the marker name AND
# the source filename, because the method is often only visible in the filename.
DEFINITIVE_METHODS = [
    (r"\be?gfr\b",
     r"cystatin",
     "Cystatin-C eGFR is definitive; creatinine-based estimates (CKD-EPI, MDRD) are "
     "confounded by muscle mass and are ignored when a cystatin-C result exists."),
]


# Some results are only interpretable inside a window defined by ANOTHER marker. Outside that
# window the reference range does not apply, so a value sitting outside it is not a finding -
# it is a number the lab itself declines to interpret. Each rule names the dependent marker,
# the marker it depends on, the window, and the lab's stated reason.
INTERPRETABLE_ONLY_IF = [
    {
        "marker": r"psa.*%\s*free|%\s*free.*psa|\bpsa,\s*%\s*free\b",
        "requires": r"psa,?\s*total|total\s*psa",
        "min": 2.6,
        "max": 10.0,
        "why": ("the assay footnote states \"The diagnostic usefulness of % Free PSA has not "
                "been established in patients with total PSA below 2.6 ng/mL\", and that above "
                "10 ng/mL risk is determined by total PSA alone"),
    },
]


def _latest_value_for(pattern):
    """Newest numeric value for the first marker matching pattern, or None."""
    best = None
    for r in _lab_rows():
        if not re.search(pattern, (r.get("marker") or ""), re.I):
            continue
        m = re.search(r"-?\d+(?:\.\d+)?", r.get("value") or "")
        if not m:
            continue
        d = _norm_date(r.get("date", "")) or ""
        if best is None or d >= best[0]:
            best = (d, float(m.group(0)))
    return best[1] if best else None


def drop_not_interpretable(entries):
    """Drop findings whose reference range does not apply at the patient's other values.

    % free PSA is the case that prompted this: it was flagged 18 L against a >25% range, but
    the user's total PSA is 1.1 ng/mL and the lab's own footnote says the measure has no
    established diagnostic usefulness below 2.6. Flagging it sends the pipeline researching a
    prostate finding that the assay explicitly declines to make.

    Fails safe: if the companion marker is missing or unparseable, the entry is KEPT.
    """
    kept = []
    for e in entries:
        name = re.split(r"[:—–]", e, 1)[0].strip()
        drop = False
        for rule in INTERPRETABLE_ONLY_IF:
            if not re.search(rule["marker"], name, re.I):
                continue
            val = _latest_value_for(rule["requires"])
            if val is None:
                continue                                       # cannot tell - keep it
            if val < rule["min"] or val > rule["max"]:
                drop = True
        if not drop:
            kept.append(e)
    return kept


def _definitive_rows_for(family_re):
    """(has_definitive, definitive_rows) for a marker family across all draws."""
    hits = []
    for r in _lab_rows():
        name = (r.get("marker") or "")
        if not re.search(family_re, name, re.I):
            continue
        blob = "%s %s" % (name, r.get("source file", ""))
        hits.append((r, blob))
    return hits


def drop_superseded_by_method(entries):
    """Drop findings from a method that a definitive method overrules.

    The user has both a creatinine-based GFR (CKD-EPI, 70.7, flagged low) and a cystatin-C
    eGFR (95, normal). Flagging the creatinine one sends the whole pipeline researching a
    kidney-function abnormality that the definitive assay says is not there.
    """
    kept = []
    for e in entries:
        name = re.split(r"[:—–]", e, 1)[0].strip()
        drop = False
        for family_re, definitive_re, _why in DEFINITIVE_METHODS:
            if not re.search(family_re, name, re.I):
                continue
            hits = _definitive_rows_for(family_re)
            definitive = [(r, b) for r, b in hits if re.search(definitive_re, b, re.I)]
            if not definitive:
                continue                                       # no better measurement exists
            mine_is_definitive = re.search(definitive_re, name, re.I)
            if not mine_is_definitive:
                drop = True
        if not drop:
            kept.append(e)
    return kept


def _method_suppressed_markers():
    """Marker keys excluded because a definitive method overrules them (see DEFINITIVE_METHODS)."""
    out = set()
    for family_re, definitive_re, _why in DEFINITIVE_METHODS:
        hits = _definitive_rows_for(family_re)
        if not any(re.search(definitive_re, b, re.I) for _r, b in hits):
            continue
        for r, b in hits:
            if not re.search(definitive_re, b, re.I):
                out |= _marker_keys(r.get("marker", ""))
    return out


def _uninterpretable_markers():
    """Marker keys excluded because a companion value puts them outside their valid window."""
    out = set()
    for rule in INTERPRETABLE_ONLY_IF:
        val = _latest_value_for(rule["requires"])
        if val is None or (rule["min"] <= val <= rule["max"]):
            continue
        for r in _lab_rows():
            if re.search(rule["marker"], (r.get("marker") or ""), re.I):
                out |= _marker_keys(r.get("marker", ""))
    return out

def marked_normal_markers():
    """Markers the narrative section explicitly flagged (N) for normal.

    Needed separately because the flag lives ONLY in the narrative - the table row for
    urobilinogen just reads `0.2`, with no (N) anywhere - so a caller working from table rows
    cannot see that the lab called it normal, and re-flags it.
    """
    labs = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(labs):
        return set()
    txt = open(labs, encoding="utf-8").read()
    m = re.search(r"^(#+)\s*Out of range.*$", txt, re.I | re.M)
    if not m:
        return set()
    lvl = len(m.group(1))
    sec = txt[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % lvl, sec, re.M)
    sec = sec[:nxt.start()] if nxt else sec
    out = set()
    for l in sec.splitlines():
        t = l.strip()
        if t.startswith(("-", "*")) and re.search(r"\((?:N|NORMAL)\)", t, re.I):
            out |= _marker_keys(re.split(r"[:—–]", t.lstrip("-* "), 1)[0].strip())
    return out


def _marker_keys(name):
    """Both the qualified and bare keys for a marker name.

    _norm_marker deliberately KEEPS specimen qualifiers, so blood and urine tests of the same
    analyte never merge. But the narrative section writes `Urobilinogen (Dipstick)` while the
    table row says plain `Urobilinogen`, giving keys 'dipstick urobilinogen' and 'urobilinogen'
    - so a suppression keyed on one form silently missed the other, and the report kept
    flagging a value the lab had marked normal.
    """
    bare = re.sub(r"\s*\([^)]*\)\s*", " ", name or "").strip()
    keys = {_norm_marker(name)}
    if bare:
        keys.add(_norm_marker(bare))
    return {k for k in keys if k}


def drop_marked_normal(entries):
    """Remove entries the lab itself marked normal.

    The merge card is an LLM writing the "## Out of range" section, and it listed
    `Glucose (Dipstick): Negative (N)` and `Urobilinogen: 0.2 E.U./dL (N)` as out of range -
    both explicitly flagged (N) for normal, and the urobilinogen value sits inside its own
    quoted reference range. Asking the user to confirm abnormalities that are not abnormal
    burns the credibility of the whole gate.

    Only drops an EXPLICIT normal marker. A missing flag is not treated as normal.
    """
    out = []
    for e in entries:
        # the flag sits right after the value: "... 0.2 E.U./dL (N) — reference ..."
        if re.search(r"\((?:N|NORMAL)\)", e, re.I):
            continue
        out.append(e)
    return out


def drop_superseded(entries):
    """Remove out-of-range entries whose marker is normal on a newer draw.

    check_labs() builds its out-of-range list by scraping the narrative "## Out of range"
    section of labs.md, which is a different mechanism from the table-based supersede logic in
    _latest_per_marker - so fixing labs-report and fanout left this third path still reporting
    superseded values. That is how the gate card kept asking about LDL-CHOLESTEROL 127 from
    03/31 when the 05/27 draw put it at 65: the pipeline had two different opinions about what
    was out of range, and the card showed the wrong one.

    Filtering here rather than at the call site means every consumer of check_labs() inherits
    it, instead of each new caller having to remember.

    Fails safe: anything that cannot be matched back to a table row is KEPT. Silently dropping
    a real abnormality is far worse than asking about one extra.
    """
    rows = _lab_rows()
    if not rows or not entries:
        return entries
    latest, _superseded = _latest_per_marker(rows)
    kept = []
    for entry_date, e in entries:
        # entries look like "LDL-CHOLESTEROL: 127 H (ref <100 mg/dL)"
        name = re.split(r"[:—–]", e, 1)[0].strip()
        row = resolve_component(latest, name) if name else None
        if row is None:
            kept.append(e)                                     # cannot tell - ask about it
            continue
        newest = _norm_date(row.get("date", "")) or ""
        mine = _norm_date(entry_date or "") or ""
        # Only a STRICTLY NEWER draw can supersede. Testing "is the newest row flagged"
        # without this comparison threw away the current abnormalities: the 05/27 table rows
        # carry no H/L in the value column (the flag lives only in the narrative section), so
        # every newest row read as unflagged and GFR, alkaline phosphatase, iron, TIBC and
        # LYM% all vanished. Dropping a real, current abnormality is the worst thing this
        # function can do.
        if not (newest and mine and newest > mine):
            kept.append(e)
    return kept


def _lab_rows():
    """Every transcribed row as a dict, plus which ones the lab flagged."""
    labs = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(labs):
        return []
    rows, hdr = [], None
    for line in open(labs, encoding="utf-8"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        low = [c.lower() for c in cells]
        # A header row, first or repeated. The per-PDF transcriptions emit the column header once
        # per section, so the merged file carries several — sometimes with a blank trailing column
        # (confidence) so they are NOT byte-identical. Match on marker+value, not equality: a
        # repeat parsed as data became a phantom marker='marker' / source='source file' row that
        # failed the "source not among the PDFs" check and held the whole research phase.
        if "marker" in low and "value" in low:
            if hdr is None:
                hdr = low
            continue
        if hdr is None or all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        rows.append({hdr[k]: cells[k] for k in range(min(len(hdr), len(cells)))})
    return rows


def cmd_labs_report(args):
    """Emit a readable, Discord-ready review of the out-of-range markers.

    Pre-formatted and pre-split so the agent sends the blocks verbatim instead of composing a
    summary itself — reviewing these was clunky precisely because the shape of the message was
    left to the model.
    """
    rows = _lab_rows()
    if not rows:
        print("No labs transcribed yet.")
        return 1

    # Use the lab's OWN flags, taken from the '## Out of range' section the transcription
    # cards produced. Inferring from the value/unit is wrong: `\b[HL]\b` matches the L in
    # U/L, mmol/L and mg/dL, so every marker with those units was reported out of range
    # (26 "flagged" against the 20 the lab actually marked, including ALT 28 with a 9-46 range).
    labs_txt = open(os.path.join(INPUTS, "labs-complete.md"), encoding="utf-8").read()
    flagged_names = set()
    m = re.search(r"^(#+)\s*Out of range.*$", labs_txt, re.I | re.M)
    if m:
        lvl = len(m.group(1))
        sec = labs_txt[m.end():]
        nxt = re.search(r"^#{1,%d}\s" % lvl, sec, re.M)
        sec = sec[:nxt.start()] if nxt else sec
        # Track the date sub-heading: the SAME marker can be flagged on one draw and normal on
        # another (Alkaline Phosphatase 35 against 53-128 on one panel, 37 against 35-144 on
        # another), so a name-only match wrongly flags every draw of it.
        cur_date = ""
        saw_date_heading = False
        for line in sec.splitlines():
            t = line.strip()
            if t.startswith("#"):
                cur_date = _norm_date(t.lstrip("# ").strip())
                saw_date_heading = saw_date_heading or bool(cur_date)
                continue
            t = t.lstrip("-*• ")
            if not t:
                continue
            name = re.split(r"[:|(]", t)[0].strip().strip("*")
            # Capture the VALUE too. The same marker appears on several draws — one low, one
            # normal — and the section is not always grouped by date, so name alone flags every
            # draw of it. Name+value pins the exact row.
            mval = re.search(r":\s*([<>]?=?\s*[-\d.]+)", t)
            val = (mval.group(1).replace(" ", "") if mval else "")
            if name:
                flagged_names.add((cur_date,
                                   observation_key({"marker": name, "value": val}), val))

    latest, _superseded = _latest_per_marker(rows)
    latest_ids = {id(r) for r in latest.values()}
    # Row-keyed for "is this reading abnormal"; name-keyed ONLY for the suppression rule
    # below, which asks whether the marker was considered at all.
    authoritative = out_of_range_keys()
    auth_names = out_of_range_markers()
    _suppressed = (marked_normal_markers() | _method_suppressed_markers()
                   | _uninterpretable_markers())

    def _flagged(r):
        # Same flag vocabulary as the source-verification path. These used to be two regexes
        # in one file - a narrow [HL] here and the full set at value_without_flag() - so a
        # value the verifier accepted as "0.2 N" was keyed here as "0.2 N" and never matched
        # the narrative entry it belongs to. One helper, one answer.
        rv = value_without_flag(r.get("value", ""))
        key = (_norm_date(r.get("date", "")), observation_key(r), rv)
        # fall back to a dateless match when the merge did not group by date
        # Only fall back to a dateless match when the section had NO date headings at all.
        # Otherwise an ungrouped entry would flag that marker on every draw date — which is
        # how Alkaline Phosphatase 37 (ref 35-144, in range) got reported as out of range.
        # Value makes the match unambiguous, so a dateless entry is safe to accept.
        # Membership in the ONE authoritative list, not a second opinion derived here. The
        # regex below still runs, but only as a widening fallback for a row the narrative
        # section never mentioned - it can no longer contradict the list the gate card uses.
        is_flagged = (any(keys_match(key, a) for a in authoritative)
                      or any(keys_match(("", key[1], key[2]), a) for a in authoritative)
                      or any(keys_match(key, f) for f in flagged_names)
                      or any(keys_match(("", key[1], key[2]), f) for f in flagged_names)
                      or re.search(r"\b[HL]\b|\bhigh\b|\blow\b|abnormal|critical",
                                   r.get("value", ""), re.I))
        # The authoritative list has already applied the (N) and definitive-method rules.
        # A row it excluded must not be re-flagged here by the widening fallback, or the two
        # paths disagree again - which is the bug this consolidation exists to remove.
        if auth_names and _norm_marker(r.get("marker", "")) not in auth_names:
            if (_norm_marker(r.get("marker", "")) in _suppressed
                    or re.search(r"\((?:N|NORMAL)\)", r.get("value", ""), re.I)):
                is_flagged = False
        return bool(is_flagged)

    # Decide every row BEFORE partitioning. "No longer out of range" has to know whether the
    # NEWEST reading is itself flagged, which is not knowable while still walking the rows -
    # and without it a marker abnormal on both draws (Alkaline Phosphatase 33 L then 35 L)
    # lands in the abnormal list AND in the resolved list, contradicting itself.
    flags = {id(r): _flagged(r) for r in rows}
    # out_of_range_entries() is THE list of current findings - its docstring says so, and the
    # rules it applies (supersession, derived markers, method changes) are the considered ones.
    # This renderer used to re-derive "flagged" from the rows and therefore disagreed with the
    # gate card built from that function: the card said 6, the message said 7, differing on
    # NON HDL CHOLESTEROL, which is total minus HDL and is dropped as arithmetically derived.
    # Two implementations, two answers - the exact defect that function was written to end.
    # It decides; this only renders.
    _current = {_norm_marker(re.split(r"[:—–]", e, 1)[0].strip()) for e in out_of_range_entries()}

    flagged, dates, resolved = [], {}, []
    for r in rows:
        if flags[id(r)] and _norm_marker(r.get("marker", "")) not in _current:
            # Flagged by the lab, but not a CURRENT finding: superseded, derived, or measured
            # by a method the newer panel replaced. It still counts for the resolved list below.
            dates.setdefault(r.get("date", "?"), []).append(r)
            continue
        if flags[id(r)]:
            # A flag on an older draw is history, not a current finding.
            if id(r) in latest_ids:
                flagged.append(r)
            else:
                nl = latest.get(observation_key(r))
                # "No longer out of range" requires a LATER draw. Without the date test, the
                # same reading transcribed twice on one panel - once carrying the lab's L flag,
                # once without - resolved itself: the unflagged twin won "latest" and the user
                # was told "LDL PEAK SIZE was 221.6 L (03/31/2026), now 221.6 (03/31/2026)".
                # Five lipid-fraction markers were reported as recovered on the strength of
                # their own duplicate, which reads exactly like the pipeline quietly dropping
                # findings it was asked about.
                if (nl is not None and not flags.get(id(nl))
                        and _norm_date(nl.get("date")) > _norm_date(r.get("date"))):
                    resolved.append((r, nl))
        dates.setdefault(r.get("date", "?"), []).append(r)

    LIMIT = 1800
    msgs, buf = [], []

    def flush():
        if buf:
            msgs.append("\n".join(buf))
            buf.clear()

    def add(line):
        if sum(len(x) + 1 for x in buf) + len(line) > LIMIT:
            flush()
        buf.append(line)

    add("**Lab review** — %d markers across %d draw date(s)" % (len(rows), len(dates)))
    add("")
    if not flagged:
        add("Nothing was flagged out of range by the lab.")
    else:
        add("**%d marker(s) flagged out of range.** Please check these against your results:"
            % len(flagged))
        by_date = {}
        for r in flagged:
            by_date.setdefault(r.get("date", "?"), []).append(r)
        for d in sorted(by_date, reverse=True):
            add("")
            add("__%s__" % d)
            for r in sorted(by_date[d], key=lambda x: x.get("marker", "")):
                unit = (" " + r.get("unit", "")).rstrip()
                ref = r.get("reference range", "")
                add("• **%s** — %s%s   (ref %s)" % (r.get("marker", "?"),
                                                    r.get("value", "?"), unit, ref or "n/a"))
    if DERIVED_DROPPED:
        add("")
        add("**Not asked about** — a newer panel computes these exactly:")
        for _n, _v, _w, _why in DERIVED_DROPPED:
            add("• %s — computes to %.0f on %s (%s)" % (_n, _v, _w, _why))
    if resolved:
        seen = set()
        add("")
        add("**No longer out of range** — flagged on an earlier draw, normal on the newest:")
        for old_r, new_r in resolved:
            k = _norm_marker(old_r.get("marker", ""))
            if k in seen:
                continue
            seen.add(k)
            add("• %s — was %s (%s), now %s (%s)" % (
                old_r.get("marker", "?"), old_r.get("value", "?"), old_r.get("date", "?"),
                new_r.get("value", "?"), new_r.get("date", "?")))
        add("_These are not asked about; the newest draw supersedes the older one._")

    # Derived risk scores the panel defines but does not print. FIB-4 is the liver-fibrosis
    # score: more informative than the transaminases alone (a normal AST/ALT does not rule out a
    # rising FIB-4), so it earns a line of its own. It is computed from ONE draw only - never
    # stitched across panels - and is reported as a risk score, not a finding.
    _f = fib4()
    add("")
    add("**Derived scores** — computed from your labs, not printed on the panel:")
    if _f["found"]:
        add("• **FIB-4 (liver fibrosis risk)** — %.3f, %s   (AST %g, ALT %g, platelets %g "
            "on %s)" % (_f["score"], _f["tier"], _f["ast"], _f["alt"], _f["plt"], _f["date"]))
    else:
        add("• FIB-4 (liver fibrosis risk) — not computable: %s" % _f["reason"])

    add("")
    add("_Every value above was checked against the source PDF and appears there verbatim._")
    add("_What that cannot catch is a right number on the wrong marker — that is what your eyes")
    add("are for. Does this match your results?_")
    flush()

    if getattr(args, "json", False):
        print(json.dumps({"messages": msgs, "markers": len(rows),
                          "flagged": len(flagged)}, indent=2))
        return 0
    for i, m in enumerate(msgs, 1):
        if len(msgs) > 1:
            print("----- message %d/%d -----" % (i, len(msgs)))
        print(m)
    return 0


def cmd_prune_unsourced(args):
    """Remove rows whose marker name is not in their source PDF, and say which.

    A row the transcriber invented carries nothing: no real marker, no value. That is why it
    can be deleted, and why deleting it is NOT the same as dropping a genuine UNREADABLE - an
    unreadable value names a real test whose number could not be read, which is a fact worth
    a human's attention and stays.

    Removes them from labs.md and from the per-PDF transcription that produced them, so a
    re-merge does not bring them back.
    """
    stats, _problems = check_labs()
    bad = stats.get("unsourced") or []
    if not bad:
        print("Nothing to prune — every marker appears in its source PDF.")
        return 0
    print("%d row(s) name a marker that is not in the source PDF:" % len(bad))
    for m, src in bad:
        print("   %-52s %s" % (m[:52], src[:44]))
    if not args.confirm:
        print("\nNothing changed. Re-run with --confirm to delete them:")
        print("   python3 ~/.hermes/rx-review/rx.py prune-unsourced --confirm")
        return 1
    names = {_flat(m) for m, _ in bad}
    removed = 0
    for path in [os.path.join(INPUTS, "labs-draft.md")] + transcription_files():
        if not os.path.exists(path):
            continue
        kept, drop = [], 0
        for line in open(path, encoding="utf-8"):
            t = line.strip()
            if t.startswith("|"):
                first = [c.strip() for c in t.strip("|").split("|")][:1]
                if first and _flat(first[0]) in names:
                    drop += 1
                    continue
            kept.append(line)
        if drop:
            open(path, "w", encoding="utf-8").write("".join(kept))
            print("   -%d from %s" % (drop, os.path.basename(path)))
            removed += drop
    print("\n%d row(s) removed. Re-run `verify-labs`." % removed)
    return 0


# Columns the RESEARCH cards reason about. `source file` and `confidence` are deliberately not
# here — see labs_brief() for why.
BRIEF_COLUMNS = ["marker", "value", "unit", "reference range", "specimen", "date"]

LABS_BRIEF = os.path.join(INPUTS, "labs-succinct.md")


def labs_brief(text):
    """The card-facing view of labs.md: same observations, half the tokens.

    WHY. Every research, marker and trend card is told to read labs.md first, and labs.md is
    109,017 characters — about 27,000 tokens, ~45% of a card's average prompt. That is a FIXED
    cost per card, so sharding a card into more parts does not reduce it, it multiplies it.
    Cutting the file is the lever that sharding is not.

    `source file` is 54% of the data: a ~74-character filename repeated on all 725 rows, with
    only 22 distinct values. No card reasons about it. Three things DO, and all of them are
    scripts reading labs.md directly, which is untouched: verify-labs checks every row's source
    against the real PDFs (the anti-fabrication check), the transcription merge uses it to tell
    two draws apart, and the method is sometimes only visible in the filename. Provenance lives
    in labs.md; this file is for reading, not for auditing.

    `confidence` is empty on 547 of 725 rows and 0.8% of the data.

    `specimen` STAYS, though it is 15% of the file and looks redundant. It is not: GLUCOSE on
    03/31/2026 is "87" in the metabolic panel and "NEGATIVE" in the urinalysis. Dropping it
    would merge a blood value with a urine one under a single marker name, which is the kind of
    quiet error this pipeline exists to avoid.

    Rows identical across the kept columns are collapsed — the same observation transcribed from
    two PDFs is one observation.
    """
    lines = [l for l in text.splitlines() if l.startswith("|") and l.count("|") >= 3]
    if not lines:
        return "", 0, 0
    head = [c.strip().lower() for c in lines[0].split("|")[1:-1]]
    idx = [head.index(c) for c in BRIEF_COLUMNS if c in head]
    if len(idx) < len(BRIEF_COLUMNS):
        # A column we need is missing: emit nothing rather than a silently narrower table.
        return "", 0, 0

    kept, seen = [], set()
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) <= max(idx) or not cells[idx[0]]:
            continue
        if set(cells[idx[0]]) <= set("-: "):          # the header separator row
            continue
        row = tuple(cells[i] for i in idx)
        if row in seen:
            continue
        seen.add(row)
        kept.append(row)

    out = ["# Lab results", "",
           "Derived from labs-complete.md by `rx.py labs-brief`. Every observation is here; the "
           "source filename and confidence columns are not, because no card reasons about them.",
           "For provenance — which PDF a value came from — see inputs/labs-complete.md.", "",
           "| " + " | ".join(BRIEF_COLUMNS) + " |",
           "|" + "|".join("---" for _ in BRIEF_COLUMNS) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in kept]
    # Count real observations in, not table furniture: `lines` holds the header AND the
    # |---| separator, so a naive len()-1 reported one row more than exists.
    n_in = sum(1 for l in lines[1:]
               if (c := [x.strip() for x in l.split("|")[1:-1]])
               and c[0] and not set(c[0]) <= set("-: "))
    return "\n".join(out) + "\n", n_in, len(kept)


MERGE_COLUMNS = ["marker", "value", "unit", "reference range", "specimen", "date",
                 "source file", "confidence"]


def _marker_key(name):
    """Identity for a marker NAME, keeping the characters that distinguish analytes.

    "%" and "#" are meaning, not punctuation: NEU% is a proportion of white cells and NEU# is
    an absolute count with its own units and reference range. Collapsing them loses a distinct
    measurement.
    """
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _fold_escaped_pipes(text):
    r"""Fold a transcriber's escaped / line-continuation pipe back INTO the cell it belongs to.

    A multi-tier reference range is often written across lines joined with `\|` or ` \ |` — the
    markdown escape for a literal pipe, sometimes with a stray space. `str.split("|")` does not
    know markdown escaping, so each of those pipes reads as a COLUMN boundary and shifts every
    column after it: the LEPTIN row `... 23.7 \ | Adult BMI ... 38.9 \ | Pediatric ...` split into
    three range columns, pushing `source file` onto a repeated `LEPTIN` cell and holding the whole
    research phase. Folding `\|`/` \ |` to `; ` keeps the range in one cell so the split aligns.
    """
    return re.sub(r"\s*\\\s*\|", "; ", text)


def _readable_reading_ids(path):
    """Every (source file, analyte, date) in `path` that has a READABLE value.

    The subsumption `merge_labs` applies, recomputed from a merged file so `check_labs` reaches
    the same verdict on a labs-complete.md written before that fix existed.
    """
    ids, hdr = set(), None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return ids
    with fh:
        for line in fh:
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            low = [c.lower() for c in cells]
            if "marker" in low and "value" in low:
                if hdr is None:
                    hdr = low
                continue
            if hdr is None or all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue
            row = {hdr[i]: cells[i] for i in range(min(len(hdr), len(cells)))}
            if row.get("marker") and not is_unreadable(row.get("value", "")):
                ids.add(_reading_id(row))
    return ids


def is_unreadable(value):
    """True when a transcription reports it could not read the value.

    A worker writes UNREADABLE for any value not printed in its window (TRANSCRIBE_BODY), so
    this is an absence of evidence, never an observation.
    """
    return (value or "").strip().upper() == "UNREADABLE"


def _reading_id(row):
    """What a reading IS, for subsumption: analyte, date, document — NOT specimen.

    Specimen is part of `observation_key` because blood and urine glucose are different
    readings. It is deliberately absent here: the specimen cell of an UNREADABLE row is itself
    unread ("UNREADABLE", or a fragment like "EN"), so it cannot be trusted to tell two
    specimens apart, and keying on it is what let an unreadable row survive beside the real one.
    """
    return (row.get("source file", ""), _marker_key(row.get("marker", "")), row.get("date", ""))


def is_furniture_row(row):
    """True when a row carries NO measurement at all — value, unit and reference range all unread.

    A lab prints footnotes in the same shape as results. The urinalysis panel of the Function
    report prints a line labelled `NOTE`, carrying the lab's `NW` flag in the flag column with
    prose beneath, directly under `NONE SEEN /LPF` — to a transcriber walking a flattened list of
    result lines the two are indistinguishable, so it emitted a row and wrote UNREADABLE for the
    value that was never there, exactly as its card instructs.

    Nothing downstream could tell that row from a real gap: the overlap subsumption keeps it
    (no window read it, because there is nothing to read), the fabrication check passes it (NOTE
    IS printed on the page), and the Stage 6 backstop then held the whole research phase over a
    footnote (2026-08-11, again on 2026-08-12 as `NOTE — value is UNREADABLE`).

    "Unread" is a BLANK cell as much as the literal UNREADABLE: the transcriber writes UNREADABLE
    only for the value it went looking for, and leaves a footnote's unit and reference range
    simply empty. Requiring UNREADABLE in all three missed the `| NOTE | UNREADABLE |  |  |` shape
    and held Stage 6 on it. A measurement prints a unit OR a reference range even when its value is
    unreadable; a value that is unread with neither a unit nor a reference range was never one.
    """
    def _absent(col):
        v = (row.get(col, "") or "").strip()
        return not v or is_unreadable(v)
    return is_unreadable(row.get("value", "")) and _absent("unit") and _absent("reference range")


def _drop_superseded_unreadable(rows):
    """(kept, superseded) — drop each UNREADABLE row whose analyte WAS read elsewhere.

    A long document is transcribed as overlapping line windows, so a window that does not reach
    an analyte's value writes UNREADABLE for it while the neighbouring window reads it. Both
    rows then survived, because the collapse key includes specimen and an unreadable row's
    specimen is unreadable too. Stage 6's backstop saw the leftover and held the whole research
    phase: `ZINC — value is UNREADABLE`, while two rows carried `ZINC 82 mcg/dL` from the same
    PDF and date (2026-08-10).

    A row is dropped ONLY when a readable reading of the same analyte, date and document exists.
    An analyte that is unreadable everywhere keeps its row — that is a real gap, and the backstop
    must still see it.
    """
    readable = {_reading_id(r) for r in rows if not is_unreadable(r.get("value", ""))}
    superseded, kept = [], []
    for r in rows:
        if is_unreadable(r.get("value", "")) and _reading_id(r) in readable:
            superseded.append(r)
        else:
            kept.append(r)
    return kept, superseded


def merge_labs(files):
    """Concatenate per-PDF transcriptions into one table. Deterministic, no model.

    WHY THIS IS NOT A CARD ANY MORE. The merge was the heaviest card on the board by a wide
    margin: 31 transcription files, 140KB, pulled into a model's context to be copied back out
    a row at a time. It peaked at 97,987 tokens against a board median of 21,661, was the only
    card on the board to compact, and was still running at 14 minutes. And it is the one place
    where a dropped or silently altered row would never be noticed - the output is the input.

    Every rule the card gave the model is mechanical:

      * keep all columns, including specimen; a blank specimen stays blank
      * sort by source file, then marker
      * the same marker from DIFFERENT source files is a different draw - keep both
      * the same marker, value and date from the SAME source file is one reading transcribed
        twice, because long documents are split into overlapping line windows - keep one
      * unless those duplicates DISAGREE on the value, which is a finding, not noise

    Returns (rows, notes). `notes` carries the disagreements, which the caller reports rather
    than resolving.
    """
    seen, rows, notes = {}, [], []
    for path in sorted(files):
        head, body = None, []
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in _fold_escaped_pipes(line.strip()).strip("|").split("|")]
            low = [c.lower() for c in cells]
            if head is None:
                head = low
                continue
            # A repeated header inside one per-PDF file (emitted once per section) is not data —
            # written through it becomes a phantom marker='marker' / source='source file' row.
            if "marker" in low and "value" in low:
                continue
            if cells and set("".join(cells)) <= set("-: "):     # separator row
                continue
            row = {head[i]: cells[i] for i in range(min(len(head), len(cells)))}
            if not row.get("marker"):
                continue
            body.append(row)

        for row in body:
            # Identity for the overlap case: SAME source file, marker, date. Two rows that
            # match here are the same reading seen twice through overlapping line windows.
            # NOT _flat() on the marker. _flat strips every non-alphanumeric, so "NEU%" and
            # "NEU#" both become "neu" - the percentage and the absolute count of the same cell
            # line collapse into one observation. Every CBC differential in the set then
            # reported a "disagreement" between its own two columns (NEU# 39.3 vs 2.1), 41 of
            # the 44 raised. Worse than the noise: had the two columns ever held the SAME
            # number, one of them would have been silently dropped as a duplicate.
            # % and # are the distinction that matters here, so only case and spacing are
            # normalised away.
            key = (row.get("source file", ""), _marker_key(row.get("marker", "")),
                   row.get("date", ""), _flat(row.get("specimen", "")))
            prior = seen.get(key)
            if prior is None:
                seen[key] = row
                rows.append(row)
                continue
            if (prior.get("value", "") or "").strip() != (row.get("value", "") or "").strip():
                # A disagreement between two transcriptions of the same reading is EVIDENCE
                # that one of them is wrong. Keep both and say so; picking one silently is how
                # a mis-transcribed value reaches the analysis wearing a clean face.
                notes.append("%s on %s in %s: %r vs %r"
                             % (row.get("marker"), row.get("date"),
                                row.get("source file"), prior.get("value"), row.get("value")))
                rows.append(row)

    rows, superseded = _drop_superseded_unreadable(rows)
    dropped = [(r, "superseded") for r in superseded]
    kept = []
    for r in rows:
        if is_furniture_row(r):
            dropped.append((r, "nothing readable"))
        else:
            kept.append(r)
    kept.sort(key=lambda r: (r.get("source file", ""), _flat(r.get("marker", "")),
                             r.get("date", "")))
    return kept, notes, dropped


def unstaged_documents():
    """Lab PDFs Hermes has received that never reached inputs/raw.

    WHY THIS EXISTS. Staging was a per-attachment `cp` performed by the assistant, so an upload
    reached the pipeline only if the model noticed it. On 2026-07-31 the user sent ten PDFs,
    was asked "is that the complete set?" through a CHOICE FORM, and replied by attaching ten
    more. The reply was read as a promise of future uploads - "send them over whenever you're
    ready" - and the second batch was never copied. Both batches were sitting in Hermes' cache
    the whole time; the pipeline saw half of them and nothing said otherwise.

    Matching is by basename: Hermes names a cached upload doc_<hash>_<original>.pdf and the
    staged copy keeps that name, so the same document is the same file on both sides.
    """
    if not os.path.isdir(DOC_CACHE):
        return []
    staged = {os.path.basename(f) for f in glob.glob(os.path.join(RAW, "*"))}
    done = {os.path.basename(f) for f in transcription_files()}
    out = []
    for f in sorted(os.listdir(DOC_CACHE)):
        if not f.lower().endswith(".pdf") or f in staged:
            continue
        # Already transcribed in an earlier round: staged then cleared, not missed.
        if any(_lab_slug(f) in d for d in done):
            continue
        out.append(os.path.join(DOC_CACHE, f))
    return out


def received_documents():
    """Every lab PDF Hermes has received, staged or not.

    unstaged_documents() answers "did staging miss anything" and returns [] for two situations
    that are not the same: everything is staged, and nothing was ever sent. Staging is the only
    place that can still see what arrived, so it is the only place the difference is knowable -
    and the difference decides whether the run continues or stops.
    """
    if not os.path.isdir(DOC_CACHE):
        return []
    return [os.path.join(DOC_CACHE, f) for f in sorted(os.listdir(DOC_CACHE))
            if f.lower().endswith(".pdf")]


def received_zips():
    """Every zip archive Hermes has received (staging unpacks these into inputs/raw)."""
    if not os.path.isdir(DOC_CACHE):
        return []
    return [os.path.join(DOC_CACHE, f) for f in sorted(os.listdir(DOC_CACHE))
            if f.lower().endswith(".zip")]


def _wait_for_upload_quiescence():
    """Block until the Hermes upload cache stops growing, so cmd_stage does not copy a
    half-delivered batch — BOUNDED to fit under the terminal command_timeout.

    Discord caches attachments ASYNCHRONOUSLY: on 2026-08-13 twenty lab PDFs took ~a minute to
    land, continuing after the /rx-review turn began, so the agent staged only the first handful
    and asked about labs the user had in fact sent. This waits for the cache's (count, newest
    mtime) to hold steady for RX_STAGE_SETTLE_S seconds before staging.

    The wait is CAPPED at RX_STAGE_MAX_WAIT_S because the terminal tool kills a command at
    `kanban.command_timeout` (30s today); the cap keeps a stage call from being killed mid-copy.
    Anything that lands after the cap is still caught downstream: cmd_start REFUSES while any
    received PDF is unstaged, so the agent re-stages rather than starting a partial set. Set
    RX_STAGE_SETTLE_S=0 to disable (tests, and hand runs where the files are already in place).
    """
    settle = float(os.environ.get("RX_STAGE_SETTLE_S", "10"))
    cap = float(os.environ.get("RX_STAGE_MAX_WAIT_S", "22"))
    if settle <= 0:
        return

    def _sig():
        try:
            pdfs = [f for f in os.listdir(DOC_CACHE) if f.lower().endswith(".pdf")]
        except OSError:
            return (0, 0.0)
        return (len(pdfs), max((os.path.getmtime(os.path.join(DOC_CACHE, f)) for f in pdfs),
                               default=0.0))

    begin = last_change = time.time()
    prev = _sig()
    if prev[0] and begin - prev[1] >= settle:
        return                                          # newest upload is already older than settle
    while time.time() - begin < cap:
        time.sleep(2)
        cur = _sig()
        if cur != prev:
            print("   uploads still arriving: %d document(s) cached..." % cur[0])
            prev, last_change = cur, time.time()
        elif time.time() - last_change >= settle:
            if prev[0]:
                print("   uploads settled: %d document(s), steady for %ds." % (prev[0], int(settle)))
            return
    print("   hit the %ds upload-wait cap with %d document(s) — if the upload is still in "
          "progress, run stage again once the cache stops growing; start refuses while any "
          "received PDF is unstaged." % (int(cap), prev[0]))


def cmd_stage(args):
    """STAGE 1 of 8. Copy every document Hermes has received into the intake folder.

    The head of the chain. A review runs as five stages in a fixed order - stage, regimen,
    supplements, labs, research - and each one ends by creating the card that runs the next.
    Nothing polls and nothing checks "is the other branch done yet": a stage's cards simply do
    not exist until the stage before it has finished.

    Staging is its own stage because a partial set is silently wrong. Every later stage reasons
    about "the documents", and a PDF that arrives after transcription has been planned is a
    panel the report will quietly omit.
    """
    # Discord caches attachments asynchronously, so a bare /rx-review can reach here mid-upload.
    # Wait for the cache to settle before copying, so a half-delivered batch is not staged as the
    # complete set (see _wait_for_upload_quiescence). Skipped on --dry-run.
    if not args.dry_run:
        _wait_for_upload_quiescence()
    # NOTHING RECEIVED IS AN ERROR, not an empty review. An empty input set means the upload
    # failed or went somewhere else, and this is the only place that can still tell "nothing
    # arrived" apart from "everything is already staged" - unstaged_documents() returns [] for
    # both. Continuing would create a stage-2 card for a review with no labs to review.
    received = received_documents()
    zips = received_zips()
    if not received and not zips and not glob.glob(os.path.join(RAW, "*")):
        print("NOTHING TO STAGE — Hermes has received no lab PDFs.")
        print("   looked in: %s" % DOC_CACHE.replace(os.path.expanduser("~"), "~"))
        print("\nUpload the lab PDFs and run this again. A review with no labs is not a shorter")
        print("review: the brief exists to relate substances to lab markers.")
        return 1

    # A zip attachment is a first-class lab upload (Discord caps files at 25 MB, so a full
    # history often arrives as one archive). Unpack it into inputs/raw HERE - staging is the
    # intake boundary, so it owns every shape an upload can take. CRC-keyed: re-running is
    # free, and a re-sent zip re-extracts nothing.
    if zips:
        import zipfile
        for zpath in zips:
            try:
                with zipfile.ZipFile(zpath) as z:
                    members = [i for i in z.infolist() if not i.is_dir()]
            except (zipfile.BadZipFile, OSError) as e:
                print("HELD — cannot read %s (%s)." % (os.path.basename(zpath), e))
                print("The archive is corrupt or unreadable; ask for it again rather than")
                print("starting a review whose lab set is silently incomplete.")
                return 1
            pdfs = [i.filename for i in members
                    if i.filename.lower().endswith(".pdf") and "/" not in os.path.basename(i.filename)]
            ghosts = [i for i in members
                      if "__MACOSX/" in ("/" + i.filename)
                      or os.path.basename(i.filename).startswith("._")]
            visible = [i.filename for i in members
                       if i not in ghosts and not i.filename.lower().endswith(".pdf")]
            if args.dry_run:
                print("%s: would extract %d PDF(s)" % (os.path.basename(zpath), len(pdfs)))
                continue
            os.makedirs(RAW, exist_ok=True)
            n = 0
            with zipfile.ZipFile(zpath) as z:
                for info in z.infolist():
                    base = os.path.basename(info.filename).lstrip("/")
                    if not base:
                        continue
                    if ("__MACOSX/" in ("/" + info.filename)
                            or base.startswith("._")):
                        continue                     # macOS resource-fork ghost
                    if not info.filename.lower().endswith(".pdf"):
                        continue
                    target = os.path.join(RAW, base)
                    if os.path.exists(target):
                        continue                     # same-named member already staged
                    with z.open(info) as src, open(target + ".part", "wb") as dst:
                        dst.write(src.read())
                    os.replace(target + ".part", target)   # atomic, never a half PDF
                    n += 1
            print("Extracted %d new PDF(s) from %s." % (n, os.path.basename(zpath)))
            if visible:
                print("   skipped (not PDFs): %s" % ", ".join(sorted(visible)[:6])
                      + ("…" if len(visible) > 6 else ""))

    pending = unstaged_documents()
    if not pending:
        print("Nothing new — every PDF Hermes has received is already staged.")
    else:
        print("%d document(s) received but not staged:" % len(pending))
        for f in pending:
            print("   %s" % os.path.basename(f))
        if args.dry_run:
            print("\nwould copy them into %s" % RAW.replace(os.path.expanduser("~"), "~"))
        else:
            os.makedirs(RAW, exist_ok=True)
            import shutil
            for f in pending:
                shutil.copy2(f, os.path.join(RAW, os.path.basename(f)))
            print("\nStaged %d file(s). Run `rx.py staged` to see the full set." % len(pending))

    # Verify before reporting success, rather than trusting the copy loop. This is the one place
    # that can still see what Hermes received, so it is the only place the question is answerable.
    left = unstaged_documents()
    if left:
        print("\nHELD — %d document(s) are still unstaged after copying:" % len(left))
        for f in left[:10]:
            print("   %s" % os.path.basename(f))
        print("Everything downstream reasons about the full set, so this reports the gap")
        print("rather than letting a partial set be reviewed.")
        return 1

    print("\n%d document(s) staged in total." % len(glob.glob(os.path.join(RAW, "*"))))
    print("Run this again after every upload; when the user says the set is complete, start the")
    print("review with:  python3 ~/.hermes/rx-review/rx.py start")
    return 0


# The verb settles its OWN card (completes on success, blocks on a hold), so the body never mentions
# kanban_complete — the model just runs it and reports what it said.
STAGE_BEGIN_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/.hermes/rx-review/rx.py {verb}
"""

STAGE_BARRIER_CHECK_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/.hermes/rx-review/rx.py check-output --stage {n}
"""

STAGE_BARRIER_CMD_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/.hermes/rx-review/rx.py {verb}
"""


STAGE_BARRIER_LOOP_BODY = """Run this, then do exactly what it prints — running each command it
names as the user replies:

    python3 ~/.hermes/rx-review/rx.py {verb}

Do nothing else.
"""


# The spine, stages 2-8, each a (Begin, Barrier) pair. Stage 1 creates the WHOLE chain up front —
# every later stage's Begin and Barrier — each Barrier parented in front of the next stage's
# Begin, so the execution order is an edge in a graph that exists from the first minute. Nothing
# after stage 1 creates a stage boundary. The one nesting is Stage 6: `Stage 6: Research Begin`
# creates the four substage (6a-6d) Begin/Barrier cards dynamically (see fanout.py).
STAGE_SPINE = [
    dict(n=2, begin="Stage 2: Read Regimen", verb="intake-regimen",
         begin_purpose="Carries regimen.txt into a worker card and transcribes it into regimen-draft.txt.",
         barrier="Stage 2: Regimen Read",
         barrier_purpose="Confirm the regimen was read into a draft.",
         output="inputs/regimen-draft.txt", barrier_verb=None),
    dict(n=3, begin="Stage 3: Settle the Regimen", verb="intake-regimen-items",
         begin_purpose="Creates one `Regimen Intake:` worker per supplement and medication; "
                       "each researches its item's ingredients and dose into its own "
                       "regimen-item-<slug>.md.",
         barrier="Stage 3: Finalize Regimen",
         barrier_purpose="Gather every per-item regimen file into the numbered regimen-final.md, "
                         "post it for review, and settle any corrections the user makes.",
         output="inputs/regimen-final.md", barrier_verb="gather-regimen-slugs", barrier_loop=True),
    dict(n=4, begin="Stage 4: Transcribe Labs", verb="intake-labs",
         begin_purpose="Creates one `Lab: <file>` card per staged PDF; each OCR-detects, "
                       "flattens, windows, and creates its own transcription workers.",
         barrier="Stage 4: Labs Transcribed",
         barrier_purpose="Merge the per-PDF transcriptions into labs-draft.md, once every "
                         "transcription is done.",
         output="inputs/labs-draft.md", barrier_verb="merge-labs"),
    dict(n=5, begin="Stage 5: Review Labs", verb="review_labs",
         begin_purpose="Seeds labs-complete.md from labs-draft.md and creates one "
                       "`Marker review:` worker per out-of-range or trending marker.",
         barrier="Stage 5: Labs Complete",
         barrier_purpose="Copy the significant markers into labs-succinct.md, once every "
                         "`Marker review:` card is answered.",
         output="inputs/labs-succinct.md", barrier_verb="labs-brief", barrier_loop=True),
    dict(n=6, begin="Stage 6: Research Begin", verb="analyze-research",
         begin_purpose="Creates the four research substage shells (6a-6d), each a Begin+Barrier; "
                       "each substage Begin then researches its own family.",
         barrier="Stage 6: Research Complete",
         barrier_purpose="Confirm all four research substages (6a-6d) have completed.",
         output="reports/", barrier_verb=None),
    dict(n=7, begin="Stage 7: Adversarial Review", verb="analyze-adversarial",
         begin_purpose="Chunks the Stage 6 reports and fans out the four lenses and the citation "
                       "audit over them.",
         barrier="Stage 7: Adversarial Complete",
         barrier_purpose="Confirm the four lens reports and CONTEXT-AUDIT.md are written.",
         output="reports/", barrier_verb=None),
    dict(n=8, begin="Stage 8: Conclusion", verb="analyze-conclude",
         begin_purpose="Reconciles the adversarial verdicts and assembles the final brief.",
         barrier="Stage 8: Conclusion Complete",
         barrier_purpose="The run is done — the brief has been written.",
         output="the prescriber discussion brief", barrier_verb=None),
]


def _stage_output_ready(out):
    """(ready, detail) — is a CHECK barrier's declared output present? Concrete inputs/ files are
    checked for non-empty existence; a `reports/` output is satisfied by any report on disk; a
    prose output (the brief) by the dated brief file. Anything else is trusted (nothing to check)."""
    out = (out or "").strip()
    if out.startswith("inputs/"):
        p = os.path.join(INPUTS, out[len("inputs/"):])
        return (os.path.exists(p) and os.path.getsize(p) > 0), out
    if out.rstrip("/") == "reports":
        md = glob.glob(os.path.join(REPORTS, "*.md"))
        return (len(md) > 0), "%d report(s) in reports/" % len(md)
    if "brief" in out.lower():
        md = glob.glob(os.path.join(REPORTS, "*rx-review.md"))
        return (len(md) > 0), "%d brief(s) in reports/" % len(md)
    return True, out or "no concrete output"


def cmd_check_output(args):
    """Confirm a stage's output exists, then complete or BLOCK this barrier (script-owned).

    Replaces the model doing a read_file existence check and then kanban_complete. Runs as the
    barrier worker, so it completes or blocks its own card via _complete_self()/_hold()."""
    s = next((x for x in STAGE_SPINE if x["n"] == args.stage), None)
    out = (s or {}).get("output", "")
    ready, detail = _stage_output_ready(out)
    if not ready and not getattr(args, "force", False):
        return _hold("stage %d produced no output (%s)" % (args.stage, out),
                     ["The worker cards in front of this barrier did not write %s." % out],
                     dry=args.dry_run)
    _complete_self("stage %d output present: %s" % (args.stage, detail), dry=args.dry_run)
    print("Stage %d output present (%s). This card is complete — do nothing else."
          % (args.stage, detail))
    return 0


def cmd_settle(args):
    """Complete THIS card — a trivial sync barrier whose kanban parents already guarantee the work
    in front of it finished; it exists only to release the next stage, so it just completes."""
    _complete_self("synced", dry=args.dry_run)
    print("Synced — this card is complete. Do nothing else.")
    return 0


# Records that the USER said every lab document has been sent. Dotted, so `reset` sweeps it and
# the next review must be confirmed afresh (see _files_in).
UPLOADS_DONE = os.path.join(INPUTS, ".uploads-done.json")


def _staged_fingerprints():
    """One string per staged PDF: name and size. The identity of "the set the user confirmed".

    Name AND size, because a re-upload of the same document under the same name is the same
    document, while a different document is a different name — and a truncated re-send of one
    is a different size.
    """
    out = []
    for p in unique_pdfs(RAW)[0]:
        try:
            out.append("%s:%d" % (os.path.basename(p), os.path.getsize(p)))
        except OSError:                                        # noqa: PERF203 - vanished mid-scan
            continue
    return sorted(out)


def _uploads_confirmation():
    """(confirmed, arrived_since) — has the user said the labs are complete, and is that still true?

    `arrived_since` is every staged PDF that was NOT in the set the user confirmed. Both halves
    matter and neither is answerable by staging alone: staging can prove that everything Hermes
    RECEIVED is staged, never that everything the user MEANT to send has been sent. Only the user
    knows that, and until 2026-08-10 nothing asked — the review was started 23 seconds after the
    first attachment and 23 seconds before the remaining eleven arrived.
    """
    try:
        with open(UPLOADS_DONE, encoding="utf-8") as fh:
            confirmed = set(json.load(fh).get("files") or [])
    except (OSError, ValueError):
        return False, []
    return True, [f for f in _staged_fingerprints() if f not in confirmed]


def cmd_uploads_done(args):
    """Record that the user says every lab document has been sent — the gate `start` waits on."""
    staged = _staged_fingerprints()
    if not staged:
        print("NOTHING STAGED — no lab PDFs to confirm.")
        print("   Upload the labs, run `python3 ~/.hermes/rx-review/rx.py stage`, then confirm.")
        return 1
    if unstaged_documents():
        print("HELD — documents Hermes received are not staged yet, so this would confirm an")
        print("incomplete set. Run `python3 ~/.hermes/rx-review/rx.py stage` first.")
        return 1
    if args.dry_run:
        print("would record the user's confirmation of %d staged lab document(s)" % len(staged))
        return 0
    with open(UPLOADS_DONE, "w", encoding="utf-8") as fh:
        json.dump({"at": int(time.time()), "files": staged}, fh)
    print("Recorded: the user confirmed all %d lab document(s) have been sent." % len(staged))
    print("\nNow start the review:  python3 ~/.hermes/rx-review/rx.py start")
    return 0


def cmd_start(args):
    """Stage 1 of 8. Create the WHOLE Begin/Barrier chain for stages 2-8 in one pass.

    SEPARATE FROM `stage` BECAUSE THEY RUN A DIFFERENT NUMBER OF TIMES. Labs arrive over
    several rounds - chat platforms cap attachments per message - so `stage` is run after every
    one of them, while `start` runs once.

    Every card is created up front. The spine is a DAG, not one chain: the regimen branch (Stages
    2-3) and the labs branch (Stages 4-5) both start from Stage 1 and run IN PARALLEL, and Stage 6
    JOINS them — its Begin waits on BOTH the Stage 3 and Stage 5 Barriers, so nothing downstream
    ever sees labs before the regimen is settled. A branch head (`Stage 2: Read Regimen`, `Stage 4:
    Transcribe Labs`) is created parentless and is ready at once — on a hand run `_my_card_id()` is
    None and normalises away anyway. Keys are constants — there is exactly one beginning per review.
    """
    if unstaged_documents():
        print("HELD — documents Hermes received are not staged yet.")
        print("Run `python3 ~/.hermes/rx-review/rx.py stage` first; starting now would review")
        print("a partial set and say nothing about it.")
        return 1

    if not unique_pdfs(RAW)[0]:
        print("NO LAB PDFs — nothing staged to review.")
        print("   looked in: %s" % RAW.replace(os.path.expanduser("~"), "~"))
        print("\nUpload the labs and run `rx.py stage`, then start again.")
        return 1

    # THE REGIMEN HAS TO BE RESOLVED FIRST. It does not arrive as an attachment - it is a
    # Google Doc, a file, or something the user typed - so nothing upstream can have staged it,
    # and stage 2 would be the first thing to notice it was missing, by which time the board
    # has a dead chain on it. Refusing here means the review never starts half-formed.
    if not os.path.exists(regimen_path()) or not open(
            regimen_path(), encoding="utf-8", errors="replace").read().strip():
        print("NO REGIMEN — the labs are staged, but nothing says what the user takes.")
        print("\nResolve it first, whichever they offered:")
        print("   python3 ~/.hermes/rx-review/rx.py regimen --from-gdoc <doc-id>")
        print("   python3 ~/.hermes/rx-review/rx.py regimen --from <path>")
        print("\nThen start again. The brief exists to relate substances to lab markers, so a")
        print("review with only one of the two halves is not a shorter review.")
        return 1

    # THE USER HAS TO SAY THE LABS ARE COMPLETE — the LAST gate, because it is the go signal
    # rather than a missing input: staging and the regimen are things to go and fix, this is
    # simply "are you finished sending?". Staging proves everything Hermes RECEIVED is copied;
    # it cannot know whether more is still coming, and only the user does. The gate used to be
    # prose in the skill ("ask whether more are coming, and wait"), which is advice a small model
    # can race: on 2026-08-10 the review was started 23s after the FIRST attachment and 23s
    # before the other eleven arrived, so every later stage reasoned about a twelfth of the labs.
    # Prose the model may skip is now a refusal it cannot.
    confirmed, arrived_since = _uploads_confirmation()
    if not confirmed:
        print("NOT CONFIRMED — the user has not said the labs are complete.")
        print("   %d lab document(s) are staged." % len(_staged_fingerprints()))
        print("\nASK THE USER whether more labs are coming, and WAIT for their answer. Starting")
        print("early reviews a fraction of the labs and says nothing about it; more history is")
        print("strictly better, since three readings of a marker are what make a trend visible.")
        print("\nWhen they say that is all of them:")
        print("   python3 ~/.hermes/rx-review/rx.py uploads-done")
        return 1
    if arrived_since:
        print("MORE LABS ARRIVED since the user confirmed the set was complete:")
        for f in arrived_since[:8]:
            print("   %s" % f.rsplit(":", 1)[0])
        if len(arrived_since) > 8:
            print("   ... and %d more" % (len(arrived_since) - 8))
        print("\nASK THE USER whether that is now everything, then re-confirm:")
        print("   python3 ~/.hermes/rx-review/rx.py uploads-done")
        return 1

    if args.dry_run:
        print("would create the whole Begin/Barrier chain for stages 2-8")
    else:
        run_dir, stamp = start_run()
        print("Run %s — artifacts land in %s"
              % (stamp, run_dir.replace(os.path.expanduser("~"), "~")))

    made = 0
    total = STAGE_SPINE[-1]["n"]   # 8 — the highest stage number, for the "Stage N of {total}" text
    root = _my_card_id()          # None on a hand run, normalises away to "parentless"

    # The spine topology, as each Begin's dependency. "root" = start after Stage 1 (a branch head).
    # Stage 4 (labs transcription) runs in parallel with the regimen branch (2->3) — it is the long
    # pole and has no reason to wait. But Stage 5 (the marker review, a HUMAN gate) waits on BOTH
    # the Stage 4 AND the Stage 3 Barriers, so the user is only ever asked one thing at a time: the
    # regimen review (Stage 3) settles before the marker review (Stage 5) is ever posted. 6 JOINS
    # both branches (Stage 3 AND Stage 5 Barriers); 7 and 8 chain behind 6. Change ordering HERE.
    begin_after = {2: ["root"], 3: [2], 4: ["root"], 5: [4, 3], 6: [3, 5], 7: [6], 8: [7]}

    barrier_of = {}               # stage number -> its Barrier id, so a later Begin can name it
    first_begin = None
    for s in STAGE_SPINE:
        bparents = []
        for dep in begin_after[s["n"]]:
            if dep == "root":
                if root:
                    bparents.append(root)
            else:
                bparents.append(barrier_of[dep])
        begin = create(
            args, s["begin"],
            STAGE_BEGIN_BODY.format(title=s["begin"], n=s["n"], total=total, verb=s["verb"],
                                   purpose=s["begin_purpose"], barrier=s["barrier"]),
            20, 140 - s["n"], parents=bparents,
            key="rx-%s-begin" % s["begin"].split(":")[0].strip().lower().replace(" ", ""))
        made += 1
        if first_begin is None:
            first_begin = begin
        if s.get("barrier_loop"):
            # A settle-and-correct loop with the user, driven by the verb's own stdout.
            bbody = STAGE_BARRIER_LOOP_BODY.format(verb=s["barrier_verb"])
        elif s["barrier_verb"]:
            bbody = STAGE_BARRIER_CMD_BODY.format(verb=s["barrier_verb"])
        else:
            bbody = STAGE_BARRIER_CHECK_BODY.format(n=s["n"])
        barrier = create(
            args, s["barrier"], bbody, 15, 139 - s["n"], parents=[begin],
            key="rx-%s-barrier" % s["begin"].split(":")[0].strip().lower().replace(" ", ""))
        made += 1
        barrier_of[s["n"]] = barrier

    print("\nStage 1 of %d complete: %d document(s) staged, regimen resolved."
          % (total, len(unique_pdfs(RAW)[0])))
    print("Created the whole Begin/Barrier chain: %d spine card(s). Stage 2 begins as %s.%s"
          % (made, first_begin, "  (DRY RUN)" if args.dry_run else ""))
    return 0


def derive_out_of_range_lines(rows):
    """The "## Out of range" narrative section for the given table rows, newest draw first.

    Moved out of the merge and INTO stage 5 (`review_labs`), which owns out-of-range derivation.
    out_of_range_entries() parses this narrative section, not the table, so labs-complete.md must
    carry it. ONLY THE NEWEST READING PER OBSERVATION, and only if still flagged: "currently out
    of range" is what stage 5 turns into `Marker review:` cards. Emitting every flagged row would
    count history — Total Bilirubin has seven readings across seven draws, four flagged, normal
    on the newest.
    """
    latest_map, _superseded = _latest_per_marker(rows)
    latest_ids = {id(r) for r in latest_map.values()}
    flagged = [r for r in rows if _flagged_value(r.get("value")) and id(r) in latest_ids]
    if not flagged:
        return []

    def _dkey(d):
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d or "")
        return (m.group(3), m.group(1).zfill(2), m.group(2).zfill(2)) if m else ("0000",)
    by_date = {}
    for r in flagged:
        by_date.setdefault(r.get("date", ""), []).append(r)
    out = ["", "## Out of range", ""]
    for d in sorted(by_date, key=_dkey, reverse=True):
        out += ["### %s" % (d or "(undated)"), ""]
        for r in sorted(by_date[d], key=lambda x: x.get("marker", "").lower()):
            ref = (" (ref: %s)" % r["reference range"]) if r.get("reference range") else ""
            unit = (" %s" % r["unit"]) if r.get("unit") else ""
            out.append("- %s: %s%s%s" % (r.get("marker", ""), r.get("value", ""), unit, ref))
        out.append("")
    return out


def cmd_merge_labs(args):
    """Write labs-draft.md from every per-PDF transcription. One command, no judgement.

    Stage 4 output: the full transcription with provenance. It does NOT derive "## Out of range"
    — that moved to stage 5's `review_labs`, which seeds labs-complete.md and owns the review.
    """
    files = transcription_files()
    if not files:
        print("No lab transcriptions found — nothing to merge.")
        return 1

    empty = [os.path.basename(f) for f in files if os.path.getsize(f) < 40]
    if empty:
        # A partial merge that looks complete is the failure this guards: labs-draft.md would be
        # short by a whole document and nothing downstream could tell.
        print("These transcriptions are empty — merge them once they are written:")
        for e in empty:
            print("   %s" % e)
        return 1

    rows, notes, dropped = merge_labs(files)
    out = ["# Lab results", "",
           "Merged from %d transcription(s) by `rx.py merge-labs`. Values are copied verbatim."
           % len(files), "",
           "| " + " | ".join(MERGE_COLUMNS) + " |",
           "|" + "|".join("---" for _ in MERGE_COLUMNS) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r.get(c, "") for c in MERGE_COLUMNS) + " |")

    if notes:
        out += ["", "## Transcription disagreements", "",
                "The same reading was transcribed twice with different values. Both rows are "
                "kept above; this is a finding, not noise.", ""]
        out += ["- %s" % n for n in notes]

    _sup = [r for r, why in dropped if why == "superseded"]
    _bare = [r for r, why in dropped if why == "nothing readable"]
    if _sup:
        # Listed, not silently dropped: the reading is in the table above, and this says which
        # window could not see it. A drop nobody can audit is how a real gap would hide.
        out += ["", "## Unreadable rows superseded", "",
                "One window could not see these values and wrote UNREADABLE; another window read "
                "them. The readings are in the table above.", ""]
        out += ["- %s on %s in %s" % (r.get("marker"), r.get("date"), r.get("source file"))
                for r in _sup]
    if _bare:
        out += ["", "## Rows with nothing readable", "",
                "No value, no unit and no reference range — a line that looked like a result but "
                "carries no measurement, such as a printed footnote. Listed here because the "
                "alternative to dropping them is holding the whole review over one.", ""]
        out += ["- %s on %s in %s" % (r.get("marker"), r.get("date"), r.get("source file"))
                for r in _bare]

    dest = os.path.join(INPUTS, "labs-draft.md")
    if args.dry_run:
        print("would write %s: %d file(s) -> %d row(s), %d disagreement(s), "
              "%d row(s) dropped"
              % (dest, len(files), len(rows), len(notes), len(dropped)))
        return 0
    open(dest, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("Wrote %s" % dest)
    print("   %d transcription(s) merged, %d row(s)" % (len(files), len(rows)))
    if _sup:
        print("   %d unreadable row(s) superseded by a window that read the value" % len(_sup))
    if _bare:
        print("   %d row(s) carried no measurement at all (no value, unit or range):" % len(_bare))
        for r in _bare[:8]:
            print("      %s in %s" % (r.get("marker"), r.get("source file")))
    if notes:
        print("   %d DISAGREEMENT(S) between duplicate transcriptions:" % len(notes))
        for n in notes:
            print("      %s" % n)
    return 0


_REGIMEN_COLS = ["type", "product", "brand", "active ingredient(s)", "amount per serving",
                 "unit", "serving size", "time(s) taken", "source", "confidence"]


def _read_item_file(path):
    """The (name, ingredients, quantity, schedule, started, confidence) row of a regimen-item-<slug>.md, or None.

    Each `Regimen Intake:` worker writes one 6-field table into its own file; this reads the first
    data row back, skipping the header and separator. Missing cells come back as "".
    """
    for raw in open(path, encoding="utf-8", errors="replace"):
        s = raw.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        low = [c.lower() for c in cells]
        if "name" in low and "ingredients" in low:
            continue                                            # the header row
        while len(cells) < 6:
            cells.append("")
        return cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]
    return None


def _write_regimen_final_rows(rows):
    """Write regimen-final.md as the numbered 6-field table from `rows` [(name, ing, qty, sch, started, conf)].

    Renumbers from 1 in the order given — the single settled table every Stage 6 research family
    reads. Idempotent: it fully rewrites the file each time.
    """
    out = ["# Regimen (final)", "", REGIMEN_FINAL_HEADER, "|---|---|---|---|---|---|---|"]
    for i, (name, ing, qty, sch, started, conf) in enumerate(rows, 1):
        cells = [str(i)] + [(c or "").replace("|", "\\|") for c in (name, ing, qty, sch, started, conf)]
        out.append("| " + " | ".join(cells) + " |")
    open(os.path.join(INPUTS, "regimen-final.md"), "w",
         encoding="utf-8").write("\n".join(out) + "\n")


def _read_regimen_final_rows():
    """The numbered rows of regimen-final.md as [(n, name, ingredients, quantity, schedule, started, confidence)]."""
    path = os.path.join(INPUTS, "regimen-final.md")
    rows = []
    if not os.path.exists(path):
        return rows
    for raw in open(path, encoding="utf-8", errors="replace"):
        s = raw.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        low = [c.lower() for c in cells]
        if "name" in low and "ingredients" in low:
            continue                                            # the header row
        if not cells or not cells[0].isdigit():
            continue
        while len(cells) < 7:
            cells.append("")
        rows.append((int(cells[0]), cells[1], cells[2], cells[3], cells[4], cells[5], cells[6]))
    return rows


def _regimen_final_review():
    """The numbered regimen-final.md as a plain-text review the user reads in chat."""
    lines = []
    for n, name, ing, qty, sch, started, conf in _read_regimen_final_rows():
        line = "%d. %s — %s; %s; %s (%s)"
        args_ = [n, name, ing or "(dose not found)", qty or "(no quantity)",
                 sch or "(no schedule)", conf or "?"]
        if started:
            args_[-2] += "; started %s" % started
        lines.append(line % tuple(args_))
    return "\n".join(lines)


def _clear_correction_pending():
    """Remove inputs/.correction-pending if present."""
    p = os.path.join(INPUTS, ".correction-pending")
    if os.path.exists(p):
        os.remove(p)


def _append_coverage_drop(name):
    """Record a dropped item in coverage.md so the brief lists it as not covered."""
    path = os.path.join(INPUTS, "coverage.md")
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if not exists:
            fh.write("# Excluded from research at the user's request\n\n")
        fh.write("- **%s** (research) — the user could not confirm it and dropped it\n" % name)


_AWAIT_REPLY = (
    "When the user replies, pass their reply VERBATIM to this, then do exactly what it prints:\n"
    "    python3 ~/.hermes/rx-review/rx.py correct-item-slug-request \"<their reply>\"\n"
    "Do nothing else.")


def _block_for_review(reason, delivered, what, artifact):
    """Block THIS barrier for the user, saying so when the review never reached chat.

    The send is conditional; the block is not. So a review that failed to post — or was
    suppressed by a stale marker — used to leave a card blocked on a question nobody had been
    asked, indistinguishable from one the user is simply slow to answer. An undelivered review
    now says so in the block reason (what `rx.py doctor` and the dashboard show) and on stdout,
    which the worker reports.
    """
    mine = _my_card_id()
    if not delivered:
        reason = _fit("%s NOT delivered to chat — read %s" % (what.capitalize(), artifact))
    if mine:
        sh([HERMES, "kanban", "--board", BOARD, "block", mine, reason, "--kind", "needs_input"])
    if not delivered:
        print("! the %s could NOT be posted to chat, so the user has NOT seen it. The card is "
              "blocked. Tell the user to read %s, and that chat delivery failed."
              % (what, artifact))
    return delivered


def _send_review():
    """Post the numbered regimen review to the user. True when chat accepted it."""
    return send_detail(
        "Regimen review — reply `approved` to accept, or `<n> <correction>` or `<n> drop`:"
        "\n\n" + _regimen_final_review())


# Approval is matched HERE, in the script, not by the LLM: the barrier is completed by the
# `regimen-accept` verb (mirroring stage 5's `labs-accept`), never by a raw kanban_complete on a
# self-blocked card — which is what sent the agent hunting, re-running gather, and trying to unblock.
_APPROVE_WORDS = {"approved", "approve", "accept", "accepted", "looks good", "lgtm", "yes", "ok",
                  "okay", "good", "confirm", "confirmed", "fine", "perfect", "all good", "ship it",
                  "go", "done", "sounds good"}
_APPROVE_FLAT = {_flat(w) for w in _APPROVE_WORDS}


def _is_approval(text):
    """True when the WHOLE reply is an approval word (no leading item number)."""
    return _flat(text) in _APPROVE_FLAT


def _accept_regimen(dry=False):
    """Complete the `Stage 3: Finalize Regimen` barrier — the script owns the state transition."""
    _clear_correction_pending()
    p = os.path.join(INPUTS, ".regimen-review-pending")
    if os.path.exists(p):
        os.remove(p)
    if dry:
        print("would complete the Stage 3: Finalize Regimen barrier")
        return 0
    bid = _card_id_by_title("Stage 3: Finalize Regimen")
    if not bid:
        print("! could not find the Stage 3: Finalize Regimen barrier to complete.")
        return 1
    sh([HERMES, "kanban", "--board", BOARD, "complete", bid,
        "--summary", "Regimen approved by the user."])
    print("Regimen approved — completed the Stage 3: Finalize Regimen barrier (%s). "
          "Do nothing else." % bid)
    return 0


def cmd_regimen_accept(args):
    """`approved` — accept the regimen and complete the Stage 3 barrier, releasing
    `Stage 5: Review Labs` jointly with the Stage 4 Barrier and feeding the Stage 6 join."""
    return _accept_regimen(dry=getattr(args, "dry_run", False))


def cmd_gather_regimen_slugs(args):
    """STAGE 3 barrier. Gather the per-item files into regimen-final.md, post it, and block.

    Combines every regimen-item-<slug>.md (sorted for stability) into the numbered 5-field
    regimen-final.md, posts that review to chat with ONE send_detail, and blocks its OWN card
    needs_input — the Barrier is the one card in the stage allowed to wait on a human. Idempotent.
    """
    files = sorted(glob.glob(os.path.join(INPUTS, "regimen-item-*.md")))
    rows = []
    for f in files:
        parsed = _read_item_file(f)
        if not parsed:
            continue
        name, ing, qty, sch, started, conf = parsed
        if not name:
            continue
        rows.append((name, ing, qty, sch, started, conf))

    # COMPLETENESS GUARD. gather used to combine whatever regimen-item files existed; a lookup
    # card that died (timeout, gave_up) meant its ingredient SILENTLY VANISHED from the regimen
    # review and from everything downstream of it. Every draft row must be present — by slug,
    # the same key the per-item cards are keyed on — or this barrier holds and names the gaps.
    draft_rows = _draft_regimen_rows()
    have = {_flat(name) for name, *_ in rows}
    missing = [name for name, *_ in draft_rows if _flat(name) not in have]
    if missing and not getattr(args, "force", False):
        return _hold("stage 3 is missing %d item file(s)" % len(missing),
                     ["No regimen-item file for: %s" % ", ".join(missing),
                      "The lookup worker(s) died before writing their file. Re-run "
                      "`intake-regimen-items` to recreate only the missing cards, then unblock."],
                     dry=args.dry_run)

    if args.dry_run:
        print("would gather %d per-item file(s) into regimen-final.md and block the barrier"
              % len(rows))
        return 0

    _write_regimen_final_rows(rows)
    # Post THIS review once — not "post once, ever". A re-run (the agent following the card body
    # again) must re-block without re-sending the whole list, which is what spammed the channel;
    # but the marker used to record only THAT something had been posted, so one left behind by an
    # earlier run suppressed the next run's review entirely — a blocked card and no message
    # (2026-08-10). It now records a FINGERPRINT of what was posted, so a review the user has not
    # seen is always delivered.
    pending = os.path.join(INPUTS, ".regimen-review-pending")
    fingerprint = hashlib.sha1(_regimen_final_review().encode("utf-8")).hexdigest()
    try:
        already = open(pending, encoding="utf-8").read().strip()
    except OSError:
        already = ""
    delivered = True
    if already != fingerprint:
        delivered = _send_review()
        with open(pending, "w", encoding="utf-8") as fh:
            fh.write(fingerprint)
    _block_for_review(_fit("Regimen review — %d item(s) to confirm" % len(rows)),
                      delivered, "regimen review", "inputs/regimen-final.md")
    print(_AWAIT_REPLY)
    return 0


def cmd_correct_item_slug_request(args):
    """Handle one user reply to the regimen review: a `<n> drop`, a `<n> <correction>`, or neither.

    Reads the LEADING integer from the reply — the number the user wrote picks the line, so no
    correction can land on another item. `<n> drop` removes and renumbers; a correction records the
    target line to inputs/.correction-pending and prints the current line plus the correction for
    the LLM to merge. A reply with no leading number is re-prompted and nothing is recorded.
    """
    text = (getattr(args, "text", None) or "").strip()
    # Approval is the whole state transition, and it is the SCRIPT's job — not the LLM's. Every reply
    # comes through here; an approval word completes the barrier deterministically.
    if _is_approval(text):
        return _accept_regimen()
    rows = _read_regimen_final_rows()
    numbers = {n for (n, *_r) in rows}
    m = re.match(r"^\s*(\d+)\s*[:.\)\-]?\s*(.*)$", text, re.S)
    if not m:
        print("Not a numbered reply. Ask the user for `approved`, `<n> <correction>`, or `<n> drop`, "
              "then run this again with their answer. Do nothing else.")
        return 1
    num = int(m.group(1))
    rest = m.group(2).strip()
    if num not in numbers:
        print("No item numbered %d (the review is 1..%d). Ask the user for a valid number, then run "
              "this again. Do nothing else." % (num, max(numbers) if numbers else 0))
        return 1

    target = next(r for r in rows if r[0] == num)
    if re.match(r"^drop\b", rest, re.I) or _flat(rest) == "drop":
        kept = [(nm, ing, qty, sch, started, conf) for (n, nm, ing, qty, sch, started, conf) in rows if n != num]
        _write_regimen_final_rows(kept)
        _append_coverage_drop(target[1])
        _clear_correction_pending()
        _send_review()
        print("Dropped %d (%s).\n%s" % (num, target[1], _AWAIT_REPLY))
        return 0

    with open(os.path.join(INPUTS, ".correction-pending"), "w", encoding="utf-8") as fh:
        fh.write("%d\n" % num)
    print("Merge the correction into this line, then run:\n"
          "    python3 ~/.hermes/rx-review/rx.py correct-item-slug-response \"<merged line>\"\n"
          "LINE:       | %s | %s | %s | %s | %s | %s |\nCORRECTION: %s\nDo nothing else."
          % (target[1:] + (rest,)))
    return 0


def cmd_correct_item_slug_response(args):
    """Apply the LLM's merged line to the line correct-item-slug-request handed out.

    Reads the pending line number from inputs/.correction-pending (refuses if absent), validates the
    returned line has the 5 data fields with a non-blank Schedule, replaces THAT numbered row in
    regimen-final.md, clears the pending state, and reprints the numbered review.
    """
    pend = os.path.join(INPUTS, ".correction-pending")
    if not os.path.exists(pend):
        print("No pending correction — run `correct-item-slug-request` first.")
        return 1
    try:
        num = int(open(pend, encoding="utf-8").read().strip().splitlines()[0])
    except (ValueError, IndexError):
        print("No pending correction — run `correct-item-slug-request` first.")
        return 1

    line = (getattr(args, "text", None) or "").strip()
    if not line.startswith("|"):
        print("The updated line must be a table row: | Name | Ingredients | Quantity | Schedule | Started | Confidence |")
        return 1
    cells = [c.strip() for c in line.strip("|").split("|")]
    if cells and cells[0].isdigit():
        cells = cells[1:]                                       # tolerate a leading number cell
    if len(cells) != 6:
        print("The updated line must have 6 fields (Name | Ingredients | Quantity | Schedule | "
              "Started | Confidence); got %d. Nothing was changed." % len(cells))
        return 1
    name, ing, qty, sch, started, conf = cells
    if not sch:
        print("Schedule must not be blank — it is the user's own timing. Nothing was changed.")
        return 1

    rows = _read_regimen_final_rows()
    if num not in {n for (n, *_r) in rows}:
        print("Line %d is no longer in the review. Nothing was changed." % num)
        _clear_correction_pending()
        return 1
    new_rows = [((name, ing, qty, sch, started, conf) if n == num else (nm, i, q, s, st, c))
                for (n, nm, i, q, s, st, c) in rows]
    _write_regimen_final_rows(new_rows)
    _clear_correction_pending()
    _send_review()
    print("Updated line %d.\n%s" % (num, _AWAIT_REPLY))
    return 0


def _write_labs_succinct(args_dry=False):
    """Write labs-succinct.md from labs-complete.md. Returns True on success (or on a dry run).

    The card-facing condensed labs table; deterministic, no model involved. Shared by the stage-5
    barrier (`labs-brief`) and `labs-accept`.
    """
    src = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(src):
        print("No labs-complete.md yet — nothing to condense.")
        return False
    raw = open(src, encoding="utf-8", errors="replace").read()
    brief, n_in, n_out = labs_brief(raw)
    if not brief:
        print("labs-complete.md is missing a column %s needs; left alone." % BRIEF_COLUMNS)
        return False
    # Computed from INPUTS at call time, not the import-time constant: a re-pointed INPUTS (the
    # test harness, or RX_INPUTS) must land the succinct view beside the file it was built from.
    dest = os.path.join(INPUTS, "labs-succinct.md")
    if args_dry:
        print("would write %s: %d row(s) -> %d, %s -> %s chars"
              % (dest, n_in, n_out, format(len(raw), ","), format(len(brief), ",")))
        return True
    open(dest, "w", encoding="utf-8").write(brief)
    print("Wrote %s" % dest)
    print("   %d row(s) in, %d out (%d duplicate row(s) collapsed)" % (n_in, n_out, n_in - n_out))
    print("   %s chars -> %s  (%.0f%% smaller, ~%s tokens saved per card that reads it)"
          % (format(len(raw), ","), format(len(brief), ","),
             100 * (1 - len(brief) / len(raw)), format((len(raw) - len(brief)) // 4, ",")))
    return True


def _read_marker_batch_index():
    """{number: marker-name} from marker-batch-index.md, so an ignore-by-number is deterministic."""
    path = os.path.join(INPUTS, "marker-batch-index.md")
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*(\d+)\.\s+(.+?)\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _write_marker_batch_index(index):
    """Write number -> marker-name to marker-batch-index.md, the map the batch is numbered by."""
    dest = os.path.join(INPUTS, "marker-batch-index.md")
    out = ["# Marker review — answer by number", ""]
    out += ["%d. %s" % (n, name) for n, name in index]
    open(dest, "w", encoding="utf-8").write("\n".join(out) + "\n")


def _marker_questions():
    """[(marker, detail-line)] for every marker-question-*.md, sorted by filename."""
    out = []
    for f in sorted(glob.glob(os.path.join(INPUTS, "marker-question-*.md"))):
        first = open(f, encoding="utf-8", errors="replace").readline().strip()
        name = first.split("—", 1)[0].strip() if "—" in first else first
        out.append((name or os.path.basename(f)[len("marker-question-"):-3], first))
    return out


def cmd_labs_brief(args):
    """STAGE 5 barrier. Post ONE batched marker review, or — when nothing is flagged — condense.

    Gathers the out-of-range markers from marker-question-*.md into a NUMBERED list, writes the
    number -> marker map to marker-batch-index.md. If any remain, posts the list to chat with ONE
    `send_detail`, blocks its OWN card needs_input, and RETURNS without writing labs-succinct.md;
    the user replies 'looks good' (`labs-accept`) or gives numbers to ignore (`marker-review`). If
    none remain — stage 5 flagged nothing — it writes labs-succinct.md and returns so the barrier
    completes on its own.
    """
    questions = _marker_questions()
    index = [(i, marker) for i, (marker, _d) in enumerate(questions, 1)]
    lines = ["%d. %s" % (i, detail) for i, (_m, detail) in enumerate(questions, 1)]

    if questions:
        if args.dry_run:
            print("would post a %d-marker review and block the barrier" % len(questions))
            return 0
        _write_marker_batch_index(index)
        msg = ("Out-of-range markers — these will be kept as significant:\n\n"
               + "\n".join(lines)
               + "\n\nReply 'looks good' to keep all as significant, or give the number(s) to "
                 "ignore, e.g. '2,5 ignore'.")
        delivered = _block_for_review(
            _fit("Labs review — %d out-of-range marker(s) to confirm" % len(questions)),
            send_detail(msg), "marker review", "inputs/labs-complete.md")
        print("%s the marker review (%d marker(s)); blocked the barrier until it is accepted."
              % ("Posted" if delivered else "COULD NOT POST", len(questions)))
        print("WHEN THE USER REPLIES: write their reply VERBATIM to inputs/marker-reply.txt using "
              "the write_file tool (do not interpret or renumber it), then run — in order — and "
              "report what each prints:")
        print("    python3 ~/.hermes/rx-review/rx.py marker-review --batch")
        print("    python3 ~/.hermes/rx-review/rx.py labs-accept")
        print("The script routes each decision by the number the user wrote. If marker-review "
              "--batch reports lines it could not read, ask the user to restate just those, update "
              "the reply file, and run it again before labs-accept.")
        return 0

    # Nothing flagged — condense and tell the worker to complete the barrier.
    if args.dry_run:
        return 0 if _write_labs_succinct(args_dry=True) else 1
    if not _write_labs_succinct():
        return 1
    _complete_self("no out-of-range markers to confirm")
    print("No out-of-range markers to confirm. This card is complete — do nothing else.")
    return 0


def cmd_labs_accept(args):
    """'looks good' — keep every remaining out-of-range marker as significant, then complete stage 5.

    Markers carry no MISSING information, so accepting all-as-significant is a valid safe default:
    each remaining marker-question-*.md is recorded as CONFIRMED (kept for research), its file
    deleted, labs-succinct.md written, and the `Stage 5: Labs Complete` card completed.
    """
    questions = _marker_questions()
    confirmed = [m for m, _d in questions]
    if args.dry_run:
        for m in confirmed:
            print("   would confirm significant (keeps its research): %s" % m)
        print("would write labs-succinct.md and complete the Stage 5: Labs Complete barrier")
        return 0
    if confirmed:
        _rewrite_ignores(remove=confirmed)          # confirming keeps research: not on the ignore list
    for f in sorted(glob.glob(os.path.join(INPUTS, "marker-question-*.md"))):
        os.remove(f)
    for m in confirmed:
        print("   confirmed significant (keeps its research): %s" % m)
    if not _write_labs_succinct():
        return 1
    bid = _card_id_by_title("Stage 5: Labs Complete")
    if bid:
        sh([HERMES, "kanban", "--board", BOARD, "complete", bid,
            "--summary", "Markers accepted by the user."])
        print("   completed the Stage 5: Labs Complete barrier (%s)" % bid)
    else:
        print("   ! could not find the Stage 5: Labs Complete barrier to complete")
    return 0


def _exec_fanout(args, phase, family=None):
    """Exec fanout.py for one analyze phase. os.execv replaces this process, so the fanout's
    output IS the card's output — nothing runs here after it."""
    fan = os.path.join(HOME, "fanout.py")
    cmd = [sys.executable, fan, "--phase", phase]
    if family:
        cmd += ["--family", family]
    for opt in ("slug", "triage"):                         # trend-dispatch passes these through
        val = getattr(args, opt, None)
        if val:
            cmd += ["--" + opt, val]
    if args.dry_run:
        cmd.append("--dry-run")
    os.execv(sys.executable, cmd)


def cmd_analyze_research(args):
    """STAGE 6 of 8. Build the research substages (6a-6d) by exec'ing fanout.py.

    With no --family this is the `Stage 6: Research Begin` card: it creates the four substage
    shells (6a-6d). With --family it is a substage Begin, building that family's worker cards.

    By the time this runs, the Barriers in front of it guarantee the regimen is finalized and the
    markers are reviewed — that is not something analyze re-derives. The check below is a BACKSTOP
    for a card reached out of order, never the mechanism; the edges are what hold the ordering. It
    runs ONLY for the main Research Begin (no --family): the substage Begins are downstream of it,
    so the same lab/regimen data is already verified and re-checking would just re-block them.
    """
    family = getattr(args, "family", None)
    if not family and not args.force:
        lab_stats, lab_problems = check_labs()
        if lab_problems:
            return _hold(
                "%d lab value(s) could not be verified against the source PDFs" % len(lab_problems),
                ["- %-28s %s" % (m[:28], w) for m, w in lab_problems[:20]]
                + ["", "A mis-transcribed lab value flows into every conclusion and no downstream",
                   "check catches it. Re-run the affected lab card (or use --force), then unblock."],
                dry=args.dry_run)

        unresolved, _ = check_regimen()
        if unresolved:
            return _hold(
                "%d regimen item(s) are not settled" % len(unresolved),
                ["- %-28s %s" % (u["item"][:28], u["why"]) for u in unresolved]
                + ["", "Researching a wrong drug name or a 100x-off dose produces a confident brief",
                   "about the wrong thing. Settle the regimen at the Stage 3 barrier, then unblock."],
                dry=args.dry_run)
    _exec_fanout(args, "research", family=family)


def cmd_trend_dispatch(args):
    """STAGE 6c per-trend — read the triage's verdict and either skip or deepen, by exec'ing
    fanout.py. The `Trend: <marker> — dispatch` card runs this; the fanout verb self-completes it."""
    _exec_fanout(args, "trend-dispatch")


def cmd_analyze_adversarial(args):
    """STAGE 7 of 8. Chunk the Stage 6 reports and fan out the four lenses + the citation audit.

    Released only when `Stage 6: Research Complete` has completed, so the reports exist on disk;
    it hands them to fanout.py, which packs them into window-sized chunks and creates the
    chunk x lens and per-chunk audit cards plus their merges.
    """
    _exec_fanout(args, "adversarial")


def cmd_analyze_conclude(args):
    """STAGE 8 of 8. Reconcile the adversarial verdicts and assemble the final brief.

    Released only when `Stage 7: Adversarial Complete` has completed; it builds the fixed
    Reconcile -> Assemble -> Adversarial-review-of-the-brief chain.
    """
    _exec_fanout(args, "conclude")


def cmd_review_labs(args):
    """STAGE 5 of 8. Flag every OUT-OF-RANGE marker for the batched review at the barrier.

    Seeds labs-complete.md from labs-draft.md and derives the "## Out of range" section (moved here
    from the merge — stage 5 owns out-of-range derivation). ONLY out-of-range markers are flagged:
    for each, the SCRIPT writes inputs/marker-question-<slug>.md naming its out-of-range detail. No
    per-marker cards — the `Stage 5: Labs Complete` Barrier gathers the question files into one
    batched review. Trends stay in labs-complete.md and are still analysed in stage 6; they are not
    questioned. Idempotent: keyed per marker slug.
    """
    draft = os.path.join(INPUTS, "labs-draft.md")
    complete = os.path.join(INPUTS, "labs-complete.md")
    if not os.path.exists(draft):
        print("NO labs-draft.md — stage 4 has not merged the transcriptions yet.")
        return 1

    if not args.dry_run:
        # Carry forward any ignore decisions already recorded, then reseed from the draft and
        # re-append the derived out-of-range section.
        prior = []
        if os.path.exists(complete):
            prior = [l.rstrip("\n") for l in open(complete, encoding="utf-8", errors="replace")
                     if l.strip().lower().startswith(IGNORE_PREFIX)]
        text = open(draft, encoding="utf-8").read()
        if not text.endswith("\n"):
            text += "\n"
        open(complete, "w", encoding="utf-8").write(text)
        rows = _lab_rows()                        # reads labs-complete.md's table
        section = derive_out_of_range_lines(rows)
        with open(complete, "a", encoding="utf-8") as fh:
            if section:
                fh.write("\n".join(section) + "\n")
            for l in prior:
                fh.write(l + "\n")
        print("Seeded labs-complete.md from labs-draft.md (%d out-of-range line(s))."
              % max(0, len(section) - 3))

    # WHICH MARKERS GET A REVIEW: out of range only. Trends are not questioned.
    flagged = []
    for e in out_of_range_entries():
        name = re.split(r"[:—–]", e, 1)[0].strip()
        if name and name not in [n for n, _ in flagged]:
            flagged.append((name, e))

    made = 0
    for name, detail in flagged:
        if not args.dry_run:
            with open(_marker_question_path(name), "w", encoding="utf-8") as fh:
                fh.write("%s — %s\n" % (name, " ".join(str(detail).split())))
        print("   flagged out of range (batched at the barrier): %s" % name)
        made += 1
    print("\nStage 5 of 8: %d out-of-range marker(s) flagged for the batched review at the "
          "`Stage 5: Labs Complete` Barrier.%s"
          % (made, "  (DRY RUN)" if args.dry_run else ""))
    if not made:
        print("   (no out-of-range markers — the Barrier completes on its own)")
    return 0


def _split_markers(chunks):
    """Flatten repeatable, comma/semicolon-separated marker arguments into a clean list."""
    out = []
    for chunk in (chunks or []):
        out += [x.strip() for x in re.split(r"[,;\n]", chunk) if x.strip()]
    return out


def _rewrite_ignores(add=(), remove=(), clear=False):
    """Rewrite the `ignore:` decisions in labs-complete.md. --ignore ACCUMULATES, --drop clears.

    A second answer ADDS rather than replaces, so "also skip ferritin" cannot quietly re-enable
    research the user already declined. --confirm removes a marker from the list (keep research);
    --drop clears it and starts over.
    """
    complete = os.path.join(INPUTS, "labs-complete.md")
    lines = open(complete, encoding="utf-8", errors="replace").read().splitlines() \
        if os.path.exists(complete) else []
    kept = [l for l in lines if not l.strip().lower().startswith(IGNORE_PREFIX)]
    current = [l.strip()[len(IGNORE_PREFIX):].strip() for l in lines
               if l.strip().lower().startswith(IGNORE_PREFIX)]
    if clear:
        current = []
    for r in remove:
        current = [c for c in current if _flat(c) != _flat(r)]
    for a in add:
        if not any(_flat(a) == _flat(c) for c in current):
            current.append(a)
    with open(complete, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept).rstrip("\n") + "\n")
        for c in current:
            fh.write("%s%s\n" % (IGNORE_PREFIX, c))
    return current


def cmd_marker_review(args):
    """Answer part of the batched marker review: record a decision and remove that marker's question.

    Identify the marker(s) by `--number N` (mapped through marker-batch-index.md, comma-separated
    and repeatable) or by `--marker NAME`, and give the decision: `--ignore` leaves the marker in
    labs-complete.md and the report but skips its research cards, `--confirm` keeps its research,
    `--drop` clears the exclusions already on record. Exclusions ACCUMULATE across answers. Each
    resolved marker's marker-question-<slug>.md is deleted. The stage completes on `labs-accept`.

    A number or name matching no flagged marker refuses the WHOLE command with the close matches
    named — recording the part that matched would leave the user believing an answer landed.

    `--batch` reads the user's whole reply from the reply file and routes every decision by the
    number the user wrote — see `_marker_review_batch`. It is the path the barrier uses; `--number`
    / `--marker` remain for a single decision.
    """
    if getattr(args, "batch", False):
        return _marker_review_batch(args)
    index = _read_marker_batch_index()
    flagged = [m for m, _d in _marker_questions()]
    names = list(index.values()) or flagged
    ignore_flag = bool(getattr(args, "ignore", False))
    confirm_flag = bool(getattr(args, "confirm", False))
    drop = getattr(args, "drop", False)

    # Resolve the targeted markers from --number and/or --marker.
    targets = []
    for num in _split_markers(getattr(args, "number", None)):
        try:
            k = int(num)
        except ValueError:
            print("Not a number: %r — give --number as it appears in the review." % num)
            return 1
        if k not in index:
            print("No marker numbered %s in the current review." % k)
            if index:
                print("\nThe review is numbered:")
                for kk in sorted(index):
                    print("   %d. %s" % (kk, index[kk]))
            print("\nNothing was recorded.")
            return 1
        targets.append(index[k])
    for name in _split_markers(getattr(args, "marker", None)):
        if names and not any(_flat(n) == _flat(name) for n in names):
            print("NOT A FLAGGED marker: %r" % name)
            near = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
            if near:
                print("\nThe closest flagged markers are:")
                for n in near:
                    print("   %s" % n)
            else:
                print("\nStill flagged:")
                for n in names[:10]:
                    print("   %s" % n)
            print("\nNothing was recorded. Re-run with the number or name the review lists.")
            return 1
        targets.append(next((n for n in names if _flat(n) == _flat(name)), name))

    if not targets and not drop:
        print("Name the marker(s) with --number N or --marker NAME, and give --ignore or --confirm "
              "(or --drop to clear the exclusions on record).")
        return 1
    if targets and not ignore_flag and not confirm_flag and not drop:
        print("Say what to do with the marker(s): --ignore (skip research) or --confirm (keep it).")
        return 1

    add = targets if ignore_flag else []
    remove = targets if confirm_flag else []
    if not args.dry_run:
        current = _rewrite_ignores(add=add, remove=remove, clear=drop)
        for m in targets:
            qpath = _marker_question_path(m)
            if os.path.exists(qpath):
                os.remove(qpath)
    else:
        current = []
    if drop:
        print("   --drop: previous exclusions cleared")
    if ignore_flag:
        for m in targets:
            print("   ignoring (research skipped, value kept): %s" % m)
    if confirm_flag:
        for m in targets:
            print("   confirmed significant (keeps its research): %s" % m)
    if not args.dry_run:
        print("   %d marker(s) now excluded from research%s"
              % (len(current), (": " + ", ".join(current)) if current else ""))
    return 0


def _marker_review_batch(args):
    """Route a whole batched marker reply by the number the user wrote — no model in the loop.

    The marker review keeps every flagged marker by default, so the user only speaks up to IGNORE
    one. Reads the reply file, VALIDATES all numbers against the index first (any miss refuses the
    whole batch), then a bare number or an ignore-word ignores that marker (research skipped, value
    kept) and a keep/confirm-word keeps it. Unparsable lines make the verb return non-zero.
    """
    text, path = _batch_reply_text(args, "marker-reply.txt")
    if text is None:
        print("No reply to route: %s does not exist." % path.replace(os.path.expanduser("~"), "~"))
        print("Write the user's reply there verbatim, then re-run `marker-review --batch`.")
        return 1
    index = _read_marker_batch_index()
    directives, unparsed = _parse_numbered_reply(text)
    bad = sorted(n for n in directives if n not in index)
    if bad:
        print("These number(s) are not in the marker review: %s" % ", ".join(map(str, bad)))
        if index:
            print("\nThe review is numbered:")
            for k in sorted(index):
                print("   %d. %s" % (k, index[k]))
        print("\nNothing was recorded — fix the numbers and re-run.")
        return 1

    ignore, keep = [], []
    for num in sorted(directives):
        name = index[num]
        low = _flat(directives[num])
        if low in {_flat(w) for w in CONFIRM_WORDS}:
            keep.append((num, name))
        else:                                          # bare number or an ignore-word -> ignore
            ignore.append((num, name))
    if not args.dry_run:
        current = _rewrite_ignores(add=[n for _i, n in ignore], remove=[n for _i, n in keep])
        for _i, n in ignore + keep:
            qp = _marker_question_path(n)
            if os.path.exists(qp):
                os.remove(qp)
    for num, n in ignore:
        print("   %d. %s — ignored (research skipped, value kept)" % (num, n))
    for num, n in keep:
        print("   %d. %s — kept significant (research runs)" % (num, n))
    print("\nRouted %d ignore(s), %d keep(s) from the reply.%s"
          % (len(ignore), len(keep), "  (DRY RUN)" if args.dry_run else ""))
    if unparsed:
        print("\nCould NOT read %d line(s) — nothing recorded for them:" % len(unparsed))
        for u in unparsed:
            print("   ? %s" % u)
        print("Ask the user to re-state those as `N: ignore`, then re-run `marker-review --batch`.")
        return 1
    return 0


# Stage verbs that just do deterministic work (create workers, write a file, merge) and return.
# The dispatcher completes their card on rc 0 and blocks it on rc != 0, so their bodies say only
# "run this." NOT here: check-* (settle themselves), the barrier reply-loops (block/await), the
# accept/correction verbs, and analyze-* (exec fanout, which settles the card in fanout.py).
_AUTO_SETTLE = frozenset({"intake-regimen", "intake-regimen-items", "intake-labs",
                          "review_labs", "merge-labs", "plan-lab"})


def _verb_is_assigned(cmd):
    """True unless this card's body is readable AND does not name this verb. A worker on card X can
    run an auto-settle verb (`plan-lab`, `merge-labs`, …) ad hoc while exploring; without this guard
    that run would settle or BLOCK card X — which is how a Transcribe worker that ran `plan-lab` to
    peek at a PDF blocked its own card. Fails OPEN: if the body cannot be read, settle as before so a
    legitimate card still completes rather than hanging."""
    cid = _my_card_id()
    if not cid:
        return True                              # hand run: settle helpers no-op without a card
    body = _card_body(cid)
    if not body:
        return True                              # unreadable body: keep the old behaviour
    return ("rx.py %s" % cmd) in body


def _autosettle(cmd, rc, dry=False):
    """COMPLETE an auto-settle verb's own card on success. A failure is left alone.

    A non-zero exit means one of two things and only one of them is a hold, so blocking on all of
    them is wrong. A verb that needs a human calls `_hold()` itself — which blocks AND posts, and
    sets _CARD_ACTED so this function defers. Everything else non-zero is the caller's to fix in
    this turn: a mistyped token, an unknown record, a fabricated row. Those must NOT block,
    because a block is terminal — Hermes does not clear a card that blocked itself
    (NousResearch/hermes-agent#40312) — so the card would be stranded even after the retry that
    fixes it. On 2026-08-10 a worker mistyped the path in a `Lab:` card body, `plan-lab`
    correctly reported no such PDF, this function blocked the card, and the worker's own retry
    two minutes later did the work with nowhere to put it: the card stayed blocked and the whole
    lab branch waited behind it.

    A card left running is not a card left broken. The worker re-runs the command in the same
    turn; if it genuinely cannot proceed, the dispatcher's retry limit blocks it after repeated
    failure, which is the difference between a backstop and a hair trigger.
    """
    if _CARD_ACTED:
        return rc
    if not _verb_is_assigned(cmd):
        return rc                                # exploratory run on another card — leave it alone
    if rc == 0:
        _complete_self(dry=dry)
    return rc


def main():
    ap = argparse.ArgumentParser(description="run the rx-review pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
        ("labs-report", cmd_labs_report, "readable out-of-range list, ready to send"),
        ("gather-regimen-slugs", cmd_gather_regimen_slugs,
         "stage 3 barrier — gather the per-item files into regimen-final.md, post it, block"),
        ("correct-item-slug-request", cmd_correct_item_slug_request,
         "stage 3 barrier — apply a user reply: `approved`, `<n> drop`, or `<n> <correction>`"),
        ("correct-item-slug-response", cmd_correct_item_slug_response,
         "stage 3 barrier — apply the LLM's merged line to the pending correction"),
        ("regimen-accept", cmd_regimen_accept,
         "`approved` — accept the regimen and complete the Stage 3 barrier"),
        ("marker-review", cmd_marker_review,
         "record a decision in the batched marker review (by --number or --marker)"),
        ("labs-accept", cmd_labs_accept,
         "'looks good' — keep all remaining markers significant, write labs-succinct.md, "
         "complete stage 5"),
        ("labs-reject", cmd_labs_reject,
         "HALT the review — the transcription is wrong"),
        ("regimen-reject", cmd_regimen_reject,
         "HALT the review — the regimen inventory is wrong"),
        ("confirm", cmd_confirm, "report the settled regimen and any low-confidence items"),
        ("staged", cmd_staged, "lab PDFs waiting, across upload rounds"),
        ("status", cmd_status,
         "where the pipeline is, in one line — `--detail` adds inputs, board and reports"),
        ("doctor", cmd_doctor,
         "state the regimen and marker-review status: settled rows, low-confidence, open cards"),
        ("trends", cmd_trends, "markers moving consistently in one direction"),
        ("fib4", cmd_fib4,
         "FIB-4 liver-fibrosis risk score, from the newest draw reporting AST, ALT and platelets"),
        ("before-after", cmd_before_after,
         "before/after values for one marker, split at a medication start date"),
        ("analyze-research", cmd_analyze_research,
         "stage 6 of 8 — build the research substages (6a-6d) and their workers"),
        ("trend-dispatch", cmd_trend_dispatch,
         "stage 6c per-trend — read the triage verdict and either skip or deepen"),
        ("analyze-adversarial", cmd_analyze_adversarial,
         "stage 7 of 8 — chunk the reports and fan out the lenses + citation audit"),
        ("analyze-conclude", cmd_analyze_conclude,
         "stage 8 of 8 — reconcile the verdicts and assemble the brief"),
        ("regimen", cmd_regimen, "record the regimen from a file or stdin"),
        ("check-reports", cmd_check_reports,
         "confirm every report a card was told to write reached the reports directory"),
        ("prune-unsourced", cmd_prune_unsourced,
         "delete transcribed rows whose marker is not in the source PDF"),
        ("stage", cmd_stage,
         "copy every document Hermes has received into the intake folder — run after "
         "every upload round"),
        ("uploads-done", cmd_uploads_done,
         "record that the user says every lab document has been sent — `start` waits on this"),
        ("start", cmd_start,
         "stage 1 of 8 — begin the review, creating the whole Begin/Barrier chain"),
        ("intake-regimen", cmd_intake_regimen,
         "stage 2 of 8 — read the regimen into regimen-draft.txt"),
        ("intake-regimen-items", cmd_intake_regimen_items,
         "stage 3 of 8 — one `Regimen Intake:` worker per item into regimen-item-<slug>.md"),
        ("intake-labs", cmd_intake_labs,
         "stage 4 of 8 — create one `Lab: <file>` card per staged PDF"),
        ("plan-lab", cmd_plan_lab,
         "stage 4 per-PDF — OCR-detect, split, and create one PDF's transcription card(s)"),
        ("check-transcription", cmd_check_transcription,
         "stage 4 per-card — verify a transcription against its source, then complete or block it"),
        ("check-output", cmd_check_output,
         "a CHECK barrier — confirm a stage's output exists, then complete or block this card"),
        ("settle", cmd_settle, "complete THIS sync barrier card (its parents guarantee the work)"),
        ("review_labs", cmd_review_labs,
         "stage 5 of 8 — review out-of-range markers with the user"),
        ("merge-labs", cmd_merge_labs,
         "concatenate the per-PDF transcriptions into labs-draft.md (deterministic)"),
        ("labs-brief", cmd_labs_brief,
         "write the compact card-facing labs table (labs-succinct.md)"),
        ("reset", cmd_reset, "delete all cards and inputs and start over"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--json", action="store_true",
                       help="machine-readable output (for the Hermes skill)")
        if name in ("correct-item-slug-request", "correct-item-slug-response"):
            p.add_argument("text", nargs="?", default=argparse.SUPPRESS,
                           help="for request: the user's reply verbatim (`<n> drop` or "
                                "`<n> <correction>`); for response: the LLM's merged line")
            p.add_argument("--text", dest="text", default="",
                           help="the reply / merged line, as an option instead of a positional")
        if name in ("analyze-research", "analyze-adversarial", "analyze-conclude",
                    "confirm", "intake-labs", "check-transcription", "check-output"):
            p.add_argument("--force", action="store_true",
                           help="proceed despite an outstanding hold")
        if name == "check-transcription":
            p.add_argument("token", help="the opaque transcription token from the card")
        if name == "before-after":
            p.add_argument("--marker", required=True, help="the lab marker name")
            p.add_argument("--since", required=True,
                           help="start date: YYYY-MM or YYYY-MM-DD")
        if name == "check-output":
            p.add_argument("--stage", type=int, required=True,
                           help="the stage number whose output to confirm")
        if name == "analyze-research":
            p.add_argument("--family",
                           choices=("substances", "markers", "trends", "screens"),
                           help="build one research substage's workers (6a-6d); omit to create "
                                "the four substage shells")
        if name == "trend-dispatch":
            p.add_argument("--slug", required=True, help="the trend marker slug")
            p.add_argument("--triage", required=True,
                           help="the triage card id to parent the synthesis on")
        if name == "status":
            p.add_argument("--detail", action="store_true",
                           help="add the full inputs / cache / board / reports dump")
        if name == "plan-lab":
            p.add_argument("token", nargs="?", help="the token from this card's body")
            # HIDDEN (SUPPRESS), not merely undocumented. A flag the worker can discover in
            # --help is a flag it can pass a hallucinated path to, which is the failure the
            # token removes. It stays for an operator debugging one document by hand, and is
            # described in ARCHITECTURE.md where people look rather than where workers do.
            p.add_argument("--pdf", help=argparse.SUPPRESS)
        if name == "reset":
            p.add_argument("--confirm", action="store_true",
                           help="required — confirms the deletion")
            # The cache survives by default. Its entries are content-addressed and were each
            # admitted only after verbatim verification AND human confirmation, so they are
            # not run state - re-deriving them costs cards and risks a different answer from
            # the same document.
            p.add_argument("--clear-cache", action="store_true",
                           help="also discard the verified transcription cache")
            # Off by default: ~/.hermes/cache/documents is Hermes-wide and holds uploads from
            # every skill, so deleting it is the user's call, not a side effect of reset.
            p.add_argument("--clear-documents", action="store_true",
                           help="also delete PDFs from Hermes' upload cache, which otherwise "
                                "survive reset and can re-enter a later run")
            # Off by default: the web-access fetch cache is expensive to refill and reused every
            # run on purpose (same substances researched each time). Only an explicit ask drops it.
            p.add_argument("--clear-web-cache", action="store_true",
                           help="also discard the web-access fetch cache (kept across runs by "
                                "default so the next review reuses fetched pages)")
            # Off by default: each run's timestamped output dir is the deliverable and is kept
            # forever. Only an explicit ask purges the accumulated history.
            p.add_argument("--clear-reports", action="store_true",
                           help="also delete every past run's timestamped output dir under "
                                "reports/rx-review (kept by default as the deliverables)")
        if name in ("labs-reject", "regimen-reject"):
            p.add_argument("--reason", metavar="TEXT",
                           help="why the user rejected it, in their words")
        if name == "marker-review":
            p.add_argument("--number", action="append", metavar="NUMBERS",
                           help="marker number(s) from the review (marker-batch-index.md), "
                                "comma-separated and repeatable")
            p.add_argument("--marker", action="append", metavar="MARKERS",
                           help="marker name(s) from the review, comma-separated and repeatable")
            p.add_argument("--ignore", action="store_true",
                           help="leave the named marker(s) unresearched — kept in labs-complete.md "
                                "and the report; only the research cards are skipped. ADDITIVE.")
            p.add_argument("--confirm", action="store_true",
                           help="keep the named marker(s) significant — they keep their research")
            p.add_argument("--drop", action="store_true",
                           help="clear the exclusions already on record and start the list over")
            p.add_argument("--batch", action="store_true",
                           help="route the user's whole reply (from the reply file) by number")
            p.add_argument("--reply-file", dest="reply_file", metavar="PATH",
                           help="the user's verbatim reply (default inputs/marker-reply.txt)")
        if name == "prune-unsourced":
            p.add_argument("--confirm", action="store_true",
                           help="required — confirms the deletion")
        if name == "regimen":
            p.add_argument("--from-gdoc", dest="from_gdoc", metavar="DOC_ID",
                           help="read the regimen straight from a Google Doc "
                                "(runs the google-docs skill's reader itself)")
            p.add_argument("--from", dest="source", metavar="PATH",
                           help="a local file holding the regimen text")
            p.add_argument("--stdin", action="store_true",
                           help="read the regimen text from stdin instead")
        p.set_defaults(func=fn, force=False, autosettle=(name in _AUTO_SETTLE))
    args = ap.parse_args()
    rc = args.func(args) or 0
    if getattr(args, "autosettle", False):
        _autosettle(args.cmd, rc, dry=getattr(args, "dry_run", False))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
