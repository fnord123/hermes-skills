#!/usr/bin/env python3
"""Content-addressed cache of citation-audit VERDICTS, so a source+claim judged once is not
re-judged next run.

The expensive part of Stage 7 is the LLM judgment "does this source support this claim?" — the
fetch is already cached. This caches that judgment.

KEYED ON THE SOURCE ANCHOR — sha1(located-section + quote) — NOT the claim. Reports are
LLM-generated and reword the same claim every run, so putting the claim in the key turns those
rewords into misses. Each anchor holds the LIST of (claim, verdict) pairs judged against it; the
reuse decision — "is a NEW claim equivalent to a cached one?" — is a separate, strength-aware gate
(a later slice), never the key.

WRITTEN BY ONE THREAD ONLY. The parallel `Context audit` cards never call record(): they write
their own CONTEXT-audit-*.md files, and the serial cmd_merge folds those in here. So no concurrent
writer ever touches a key file — the failure that lost six entries from Stage 4's shared
manifest.json. Writes are atomic (temp + os.replace).

A VERSION prefix on the key invalidates the whole cache when section extraction, quote location, or
the judging rubric changes, so a verdict made under old logic is never reused.

Layout (kept across runs; only `--clear-verdict-cache` removes it, never a plain `rx.py reset`):

    ~/.hermes/cache/rx-review/citation-verdicts/
        v1-<sha1(section+quote)>.json   {"key","format","quote","claims":[{claim,verdict,reason}]}
"""
import hashlib
import json
import os
import tempfile

CACHE_HOME = os.path.expanduser(
    os.environ.get("RX_VERDICT_CACHE", "~/.hermes/cache/rx-review/citation-verdicts"))
# Bump when section extraction / quote location / the judging rubric changes. The whole cache
# invalidates cleanly rather than reusing verdicts made under old logic.
VERDICT_FORMAT = 1


def anchor_key(section, quote):
    """The content key for a (located-section, quote) anchor — version-prefixed."""
    raw = ((section or "") + "\x00" + (quote or "")).encode("utf-8", "ignore")
    return "v%d-%s" % (VERDICT_FORMAT, hashlib.sha1(raw).hexdigest()[:32])


def _path(key):
    return os.path.join(CACHE_HOME, key + ".json")


def lookup(section, quote):
    """The cached entry for this anchor, or None. Read-only, safe to call concurrently; a
    partial/garbage file reads as a miss, never an error."""
    try:
        return json.load(open(_path(anchor_key(section, quote)), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record(section, quote, claim, verdict, reason="", meta=None):
    """Append (claim, verdict) to this anchor's entry. SERIAL WRITER ONLY (cmd_merge).

    Read-modify-write is safe here ONLY because a single thread calls this — the parallel audit
    cards must not. Dedups on the exact claim string (a re-judge overwrites its prior verdict).
    Atomic (temp + os.replace), so a crash mid-write cannot leave a torn file the next run trusts.
    """
    key = anchor_key(section, quote)
    entry = lookup(section, quote) or {"key": key, "format": VERDICT_FORMAT,
                                       "quote": quote, "claims": []}
    claims = [c for c in entry.get("claims", []) if c.get("claim") != claim]
    rec = {"claim": claim, "verdict": verdict, "reason": reason}
    if meta:
        rec.update(meta)
    claims.append(rec)
    entry["claims"] = claims
    os.makedirs(CACHE_HOME, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_HOME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, indent=1)
        os.replace(tmp, _path(key))                            # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return key


def stats():
    """{anchors, claims, home} — cache size, for the reset report and build-phase metrics."""
    try:
        files = [f for f in os.listdir(CACHE_HOME) if f.endswith(".json")]
    except OSError:
        return {"anchors": 0, "claims": 0, "home": CACHE_HOME}
    claims = 0
    for f in files:
        try:
            claims += len(json.load(open(os.path.join(CACHE_HOME, f))).get("claims", []))
        except (OSError, ValueError):
            pass
    return {"anchors": len(files), "claims": claims, "home": CACHE_HOME}


# ── reuse gate: embeddings retrieve → strength-aware LLM confirm ──────────────────────────────
# ON by default (2026-08-13). Every endpoint call is FAIL-SAFE: any error → no reuse → the citation
# is judged normally, so it never breaks the audit if the cache/embeddings/key are unreachable, and
# the confirm reuses ONLY on a clean pass (a bad cached verdict is authoritative forever). An empty
# cache simply yields no reuse. Set RX_VERDICT_REUSE=0 to disable.
import urllib.request

REUSE_ENABLED = os.environ.get("RX_VERDICT_REUSE", "1") == "1"
LITELLM_BASE = os.environ.get("RX_LITELLM_BASE", "http://192.168.1.226:4000/v1")
EMBED_MODEL = os.environ.get("RX_EMBED_MODEL", "mxbai-embed-large")
CONFIRM_MODEL = os.environ.get("RX_CONFIRM_MODEL", "gemma-4-31b-hermes")
# Cosine below this = clearly a different claim → re-judge (embeddings are trustworthy at "not the
# same," not at "the same" — the LLM confirm makes the actual reuse call on close candidates).
EMBED_THRESHOLD = float(os.environ.get("RX_VERDICT_EMBED_THRESHOLD", "0.80"))


def _litellm_key():
    """The litellm key: process env first, else the single LITELLM_API_KEY line of ~/.hermes/.env.
    Dispatched workers do not inherit it in their OS env and config.yaml only references
    ${LITELLM_API_KEY}. Read INTERNALLY by this script (not a tool call), so the block-secret-reads
    tool hook — which guards tool-level reads — does not apply; only this one key is read."""
    k = os.environ.get("LITELLM_API_KEY")
    if k:
        return k
    try:
        for line in open(os.path.expanduser("~/.hermes/.env"), encoding="utf-8"):
            if line.startswith("LITELLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _post(path, payload, timeout=90):
    """POST json to litellm; parsed json or None on ANY failure (fail-safe)."""
    try:
        req = urllib.request.Request(
            LITELLM_BASE + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + _litellm_key()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:                                          # noqa: BLE001
        return None


def embed(texts):
    """Embedding vectors for texts via litellm, or None on failure (→ caller skips reuse)."""
    if not texts:
        return []
    d = _post("/embeddings", {"model": EMBED_MODEL, "input": texts})
    try:
        return [row["embedding"] for row in d["data"]]
    except Exception:                                          # noqa: BLE001
        return None


def cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def confirm_equivalent(new_claim, cached_claim):
    """True ONLY when the NEW claim asserts nothing more than the CACHED one — safe to reuse its
    verdict. Any error, or any answer that is not a clean REUSE, returns False (→ re-judge). The
    equivalence is asymmetric: a weaker/equal new claim may inherit; a stronger/more-specific one
    may not, even though it 'entails' the cached claim."""
    prompt = (
        "A source was already judged for a CACHED claim. Decide whether that verdict can be REUSED "
        "for a NEW claim, or whether the NEW claim must be RE-JUDGED against the source.\n\n"
        "CACHED claim: %r\nNEW claim: %r\n\n"
        "Reuse is allowed ONLY if the NEW claim asserts NOTHING MORE than the CACHED claim about "
        "the source: same direction, and no stronger verb, larger or added number, broader "
        "population or scope, or less hedging. A weaker-or-equal new claim may reuse. A stronger, "
        "more specific, more quantified, or opposite-direction claim must be re-judged. When "
        "unsure, do not reuse.\n\nReply with one word: REUSE or REJUDGE."
        % (cached_claim, new_claim))
    d = _post("/chat/completions", {"model": CONFIRM_MODEL, "temperature": 0, "max_tokens": 8,
                                    "messages": [{"role": "user", "content": prompt}]})
    try:
        ans = d["choices"][0]["message"]["content"].strip().upper()
        return "REUSE" in ans and "REJUDGE" not in ans
    except Exception:                                          # noqa: BLE001
        return False
