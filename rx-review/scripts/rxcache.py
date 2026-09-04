#!/usr/bin/env python3
"""A content-addressed cache of lab transcriptions, admitted only on proof.

Transcribing a lab PDF costs a card and a few minutes, and a clean re-run repeats all of it.
But speed is the smaller argument. The larger one is that transcription is done by a model, so
the SAME PDF can yield a DIFFERENT answer each run - the trailing-flag defect ("Negative N")
was exactly that. A verified cache makes one document produce one trusted set of values, and a
change to the transcriber invalidates cleanly instead of silently shifting results underneath
a review someone already signed off.

That inverts the usual risk. A bad transcription re-derived every run gets another chance to
be caught; a bad one in cache is authoritative forever. So admission is deliberately STRICTER
than the pipeline's own gate, and needs two independent signals:

  mechanical   every transcribed value appears VERBATIM in that PDF - deterministic, no model
  human        the user confirmed the labs at the CONFIRM YOUR LABS card

Neither alone is enough. The mechanical check cannot see a correct number attached to the
wrong marker; the human cannot re-read forty markers character by character.

Entries store the evidence, not just the verdict, so an entry can be re-audited later without
its original run. The key carries a format version: changing what a transcription looks like
must invalidate every entry rather than mix two shapes in one corpus.

Layout (under ~/.hermes/cache/rx-review/, with the other rx-review caches; kept out of
~/hermes-skills/rx-review/scripts/ so a plain `rx.py reset` does not take it — only `--clear-cache` does):

    ~/.hermes/cache/rx-review/transcriptions/
        labs/<sha256>.md         the transcription
        labs/<sha256>.json       provenance and admission evidence
        unreadable/<sha256>.json a PDF with no text layer - remembered so it is not retried
"""

import hashlib
import json
import os
import re
import time

CACHE_HOME = os.path.expanduser(
    os.environ.get("RX_CACHE_HOME", "~/.hermes/cache/rx-review/transcriptions"))
LABS = os.path.join(CACHE_HOME, "labs")
UNREADABLE = os.path.join(CACHE_HOME, "unreadable")

# Bump when the transcription FORMAT changes - a new column, a different flag convention. Old
# entries then miss rather than silently supplying a shape the parser no longer expects.
CACHE_FORMAT = 2


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_for(pdf_path):
    """Cache key: the PDF's content hash bound to the transcription format version."""
    return "%s-v%d" % (_sha(pdf_path)[:32], CACHE_FORMAT)


def _paths(key):
    return (os.path.join(LABS, key + ".md"), os.path.join(LABS, key + ".json"))


def get(pdf_path):
    """(text, meta) for this PDF if it was admitted, else None.

    Matching is on CONTENT, so a re-upload under a different name is a hit. That matters here:
    each upload round gets a fresh random doc_<hash>_ filename prefix, so nothing filename-based
    would ever match twice.
    """
    try:
        md, js = _paths(key_for(pdf_path))
    except OSError:
        return None
    if not (os.path.exists(md) and os.path.exists(js)):
        return None
    try:
        meta = json.load(open(js, encoding="utf-8"))
        text = open(md, encoding="utf-8").read()
    except Exception:                                          # noqa: BLE001
        return None
    if not text.strip():
        return None
    return text, meta


def unreadable_reason(pdf_path):
    """Why this PDF was previously found untranscribable, or None.

    A scan with no text layer fails the same way every run. Remembering that is worth as much
    as remembering a success - it stops a card being created to fail again.
    """
    try:
        p = os.path.join(UNREADABLE, key_for(pdf_path) + ".json")
    except OSError:
        return None
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8")).get("reason")
    except Exception:                                          # noqa: BLE001
        return None


def mark_unreadable(pdf_path, reason):
    os.makedirs(UNREADABLE, exist_ok=True)
    p = os.path.join(UNREADABLE, key_for(pdf_path) + ".json")
    json.dump({"reason": reason, "basename": os.path.basename(pdf_path),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
              open(p, "w", encoding="utf-8"), indent=1)


# ── admission ────────────────────────────────────────────────────────────────

def verify_transcription(md_text, pdf_text, value_cleaner=None):
    """(checked, problems) - does every transcribed value appear verbatim in the PDF?

    Deterministic and model-free. `value_cleaner` strips the lab's flag column from a value
    ("141 H" -> "141") and is passed in rather than imported so this module stays free of the
    domain parser, and so the test can exercise it directly.
    """
    clean = value_cleaner or (lambda v: (v or "").strip())
    hdr, checked, problems = None, 0, []
    for line in md_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        low = [c.lower() for c in cells]
        if hdr is None and "marker" in low:
            hdr = low
            continue
        if hdr is None or all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue

        def col(name):
            return cells[hdr.index(name)] if name in hdr and hdr.index(name) < len(cells) else ""
        marker, value = col("marker"), col("value")
        if not marker:
            continue
        if value.upper() == "UNREADABLE":
            problems.append((marker, "value is UNREADABLE"))
            continue
        needle = clean(value)
        if needle and needle not in pdf_text:
            problems.append((marker, "value %r not found in the source PDF" % value))
            continue
        checked += 1
    return checked, problems


def admit(pdf_path, md_path, pdf_text, confirmed_by, value_cleaner=None):
    """Admit a transcription, or refuse and say why. Returns (ok, detail).

    Refusal is the default. A transcription with ONE unverifiable value is not cached: the
    whole point is that a cached entry never has to be doubted again, and "mostly verified" is
    exactly the state that erodes into "assumed correct".
    """
    if not os.path.exists(md_path):
        return False, "no transcription at %s" % md_path
    md_text = open(md_path, encoding="utf-8").read()
    checked, problems = verify_transcription(md_text, pdf_text, value_cleaner)
    if problems:
        return False, ("%d value(s) could not be verified against the PDF: %s"
                       % (len(problems), "; ".join("%s: %s" % p for p in problems[:3])))
    if checked == 0:
        return False, "the transcription contains no values to verify"
    if not confirmed_by:
        return False, "the labs have not been confirmed by a human yet"

    key = key_for(pdf_path)
    md, js = _paths(key)
    os.makedirs(LABS, exist_ok=True)
    open(md, "w", encoding="utf-8").write(md_text)
    json.dump({
        "key": key,
        "format": CACHE_FORMAT,
        "source_basename": os.path.basename(pdf_path),
        "sha256": _sha(pdf_path),
        "values_verified": checked,
        "verified_verbatim_against_source": True,
        "confirmed_by": confirmed_by,
        "admitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, open(js, "w", encoding="utf-8"), indent=1)
    return True, "admitted %d verified value(s)" % checked


# ── reporting and maintenance ────────────────────────────────────────────────

def stats():
    labs = len([f for f in os.listdir(LABS) if f.endswith(".md")]) if os.path.isdir(LABS) else 0
    bad = len(os.listdir(UNREADABLE)) if os.path.isdir(UNREADABLE) else 0
    size = 0
    for root, _d, files in os.walk(CACHE_HOME):
        for f in files:
            try:
                size += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return {"transcriptions": labs, "unreadable": bad, "bytes": size, "home": CACHE_HOME}


def forget(pdf_path):
    """Drop this PDF's entry, if any. Returns True when something was removed.

    A rejection WITHDRAWS the human confirmation an entry was admitted under. The cache is
    content-addressed, so leaving the entry would replay the rejected transcription on the next
    run no matter where the file itself went - the one thing a rejection has to prevent.
    """
    removed = False
    for p in _paths(key_for(pdf_path)):
        if os.path.exists(p):
            os.remove(p)
            removed = True
    return removed


def clear():
    """Remove every entry. Only ever called explicitly - `reset` keeps the cache by default."""
    import shutil
    n = stats()["transcriptions"]
    shutil.rmtree(CACHE_HOME, ignore_errors=True)
    return n
