#!/usr/bin/env python3
"""Context-aware citation verification.

The previous audit asked "does this quoted sentence appear on this page". That is the wrong
question, and it is the reason 271 citations came back `supported` on evidence far thinner
than the number suggests. Locating a sentence proves the sentence exists. It does not show
that the surrounding text means what the report used it to mean - the quote may sit in a
Limitations paragraph, describe a hypothesis the paper then refutes, or report a different
indication, population or dose than the claim assumes.

So the mechanical part and the judgement part are separated:

  locate (code)   - fetch the source, find the quote, identify the ENCLOSING SECTION
  judge (model)   - given the claim, the quote, the section heading and the section body,
                    decide whether that section supports the use made of the quote

Sections, not character windows. A +/-200 char window around a match cannot tell you the
sentence lives under "6.1 Adverse Reactions in Atopic Dermatitis" while the claim is about
rheumatoid arthritis - and the heading is exactly what catches scope errors.

This is also what makes a full re-audit affordable. The enclosing section of the quote in the
90-page Rinvoq label is ~630 tokens; the whole label is ~52,000. The model never sees the
document.

PDFs are re-extracted with PyMuPDF rather than read from Hermes' web cache: firecrawl markdown
arrives with zero newlines and zero headings, so section structure is destroyed. PyMuPDF keeps
5,915 line breaks and 166 numbered sections on the same file.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import time

# This script's own dir = the pipeline's working dir (inputs/, per-run artifacts live beside it),
# wherever the skill is installed. Anchored here so the skill can move without a code change.
HOME = os.path.dirname(os.path.abspath(__file__))
# Per-run output dir, resolved through the `current` symlink rx.py's start_run() swaps at Stage 1 —
# so the citation audit writes into the SAME timestamped run dir as every other stage.
REPORTS_ROOT = os.path.expanduser(os.environ.get("RX_REPORTS_ROOT", "~/.hermes/reports/rx-review"))
REPORTS = os.path.join(REPORTS_ROOT, "current")
# LEGACY: the per-run corpus directory. Nothing writes here since 2026-08-10 — page fetching
# goes through the web-access skill's one shared cache — but reset still clears leftovers.
SOURCES = os.path.join(HOME, "sources")
LOCATIONS = os.path.join(HOME, "locations.json")
BOARD = "rx-review"
HERMES = os.path.expanduser("~/.local/bin/hermes")

SKIP = {"AUDIT.md", "REFUTATION.md", "LOGIC.md", "NULLHYP.md", "VETTED.md",
        "BRIEF.md", "CRITIQUE.md", "reasoning-audit.md"}

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# A section longer than this is centred on the match rather than passed whole - some documents
# have one enormous "Adverse Reactions" section, and the point is to stay small.
# Bound the worst single item. The median section is ~2.9k chars, but the tail runs to 9k -
# and one fat item drags a whole card past its budget.
MAX_SECTION_CHARS = 5000
CONTEXT_IF_NO_SECTION = 3000

# Cards are sized by the section text they carry, which is tiny compared with whole pages.
CHARS_PER_TOKEN = 4
# Target: NO CARD TAKES MORE THAN 20 MINUTES.
#
# Measured over completed parts: median 0.78 min/item, p90 1.80. At the p90, 10 items is
# ~18 min; the old 25 was ~45, which is exactly what the cards were dying at. Body size drops
# with it (~9k tokens instead of ~22k), so prefill is cheaper too and the model is not
# re-reading a huge prompt before each verdict.
CARD_BUDGET_CHARS = 36_000        # ~9k tokens of sections per card
MAX_CITATIONS_PER_CARD = 10
# Measured over completed parts: median 0.78 min/item, p90 1.80. At the p90 a full 25-item
# card needs ~45 min - which is exactly what the cap was, so two cards timed out twice and
# tripped the circuit breaker. There is no context pressure here (a card is ~17k of a 160k
# window), so buy headroom with time rather than by splitting into more cards.
# Above the 20-minute design target, not at it: a cap set exactly at the expected duration
# turns ordinary variance into a timeout, which is how parts 03 and 05 burned four attempts.
CARD_RUNTIME_MINUTES = 30

# build_worker_context() caps task.body at 8KB (kanban_db.py _CTX_MAX_BODY_BYTES) and appends
# a truncation marker rather than failing. Refuse to create a card that would be silently
# clipped: a body over the cap is a defect whether or not the worker happens to read the
# uncapped task.body from kanban_show instead.
KANBAN_BODY_CAP = 8 * 1024


# ── source text ────────────────────────────────────────────────────────────
#
# One fetcher for the whole pipeline, in rxfetch.py. It rate-limits per host across threads
# AND processes, retries what deserves retrying, and never caches an interstitial. Everything
# below is a thin re-export so existing call sites keep working.
#
# This module used to carry its own bare parallel urllib GET while citations.py — the module
# it replaced — held per-host locks, backoff and a plausibility check written after "8 threads
# straight at NCBI got 41 URLs rate-limited". The lesson was lost with the module that learned
# it, and a full audit run then judged claims against "Checking your browser".

import sys                                                  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # importable however we are run

import rxfetch                                              # noqa: E402,F401
import rxkanban                                             # noqa: E402
import rxverdict                                            # noqa: E402
from rxkanban import announce, subscribe                    # noqa: E402,F401
from rxkanban import discord_channel as _discord_channel    # noqa: E402,F401
from rxfetch import (fetch, fetch_text, looks_unusable,     # noqa: E402,F401
                     cache_path as _cache_path,
                     MIN_USABLE_CHARS)


# ── section structure ──────────────────────────────────────────────────────

HEADING_PATTERNS = [
    r"^\s*(#{1,6})\s+(.{3,80})\s*$",                       # markdown
    r"^\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z ,/()-]{5,70})\s*$",   # 5.9 Laboratory Abnormalities
    r"^\s*([A-Z][A-Z &/-]{5,60})\s*$",                     # ALL CAPS heading
]


def section_index(text):
    """[(offset, label)] for every heading-looking line, in document order."""
    marks = []
    for pat in HEADING_PATTERNS:
        for m in re.finditer(pat, text, re.M):
            label = " ".join(x for x in m.groups() if x).strip()
            label = re.sub(r"^#+\s*", "", label)
            if label:
                marks.append((m.start(), label))
    marks.sort()
    # Drop navigation furniture. Site chrome ("JOIN NOW", "PERMALINK", "Your RSS Feed") matches
    # the ALL-CAPS heading pattern perfectly, and a junk heading is worse than none: the whole
    # point of passing the heading is that it tells the auditor what SCOPE the quote sits in,
    # so "PERMALINK" actively misleads.
    JUNK = re.compile(r"^(join now|permalink|menu|search|skip to|sign in|log in|subscribe|"
                      r"share|print|download|cookie|privacy|terms|follow us|newsletter|"
                      r"your rss feed|rss|advertisement|related articles|references|"
                      r"table of contents|back to top|home)\b", re.I)
    out = []
    for i, (off, lab) in enumerate(marks):
        if out and off - out[-1][0] < 3:
            continue
        if JUNK.match(lab.strip()):
            continue
        # A heading followed by almost nothing is a link, not a section.
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        if nxt - off < 200:
            continue
        out.append((off, lab))
    return out


def enclosing_section(text, pos, marks):
    """(heading, body) around pos. Falls back to a plain window when unstructured."""
    prev = [m for m in marks if m[0] <= pos]
    nxt = [m for m in marks if m[0] > pos]
    if not prev:
        lo = max(0, pos - CONTEXT_IF_NO_SECTION // 2)
        return "(no section heading found)", text[lo:lo + CONTEXT_IF_NO_SECTION]
    start, heading = prev[-1]
    end = nxt[0][0] if nxt else len(text)
    body = text[start:end]
    if len(body) > MAX_SECTION_CHARS:
        rel = pos - start
        lo = max(0, rel - MAX_SECTION_CHARS // 2)
        body = body[lo:lo + MAX_SECTION_CHARS]
        body = "[section truncated around the quote]\n" + body
    return heading, body


# ── quote location ─────────────────────────────────────────────────────────

# Typography the endnote and the source disagree about. A quote copied from a rendered page
# carries curly quotes, en-dashes and non-breaking spaces; the extracted text may carry the
# ASCII equivalents, or the other way round. Every one of these mismatches manufactures an
# `absent` verdict, which reads downstream as "the source does not support this claim".
_PUNCT_MAP = {
    "\u2013": "-", "\u2014": "-", "\u2212": "-",          # en dash, em dash, minus sign
    "\u2018": "'", "\u2019": "'", "\u201a": "'",          # curly single quotes
    "\u201c": '"', "\u201d": '"',                         # curly double quotes
    "\u00a0": " ", "\u2009": " ", "\u202f": " ",          # nbsp, thin, narrow-nbsp
    "\u00ad": "",                                          # soft hyphen
    "\u2264": "<=", "\u2265": ">=",
}

# Punctuation an endnote absorbs INSIDE its closing quote mark and the source does not have.
# `"Understanding Polycythemia."` is present in its source as `Understanding Polycythemia`;
# the period is the citation's, not the document's, and it alone caused an `absent` verdict
# that then blocked a citation-audit sweep.
_TRAILING_PUNCT = " .,;:!?\u2026\"'"


def _norm(s):
    s = s or ""
    for a, b in _PUNCT_MAP.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_needle(quote):
    """A quote normalised for searching, without punctuation the citation added itself."""
    return _norm(quote).rstrip(_TRAILING_PUNCT)


def find_best_quote(text, quotes):
    """Best match across every quoted run in the endnote. exact beats fuzzy beats absent."""
    best = ("absent", None, "")
    for q in quotes or []:
        kind, pos = find_quote(text, q)
        if kind == "exact":
            return kind, pos, q
        if kind == "fuzzy" and best[0] != "fuzzy":
            best = (kind, pos, q)
    if best[0] == "absent" and quotes:
        best = ("absent", None, max(quotes, key=len))
    return best


def find_quote(text, quote):
    """(match_kind, offset). exact -> fuzzy shingle -> absent.

    `absent` is NOT a verdict of dishonesty: a report may legitimately cite a source for a
    claim it paraphrased. It is handed to the model to adjudicate, with the section it would
    have appeared in when that can be guessed.
    """
    if not text or not quote:
        return "absent", None
    hay, needle = _norm(text), _norm_needle(quote)
    i = hay.find(needle)
    if i >= 0:
        return "exact", _raw_offset(text, needle)
    words = needle.split()
    for size in (10, 8, 6):
        for k in range(0, max(1, len(words) - size + 1)):
            sh = " ".join(words[k:k + size])
            if len(sh) < 30:
                continue
            if sh in hay:
                return "fuzzy", _raw_offset(text, sh)
    return "absent", None


def _raw_offset(text, normalised_needle):
    """Approximate offset in the ORIGINAL text for a needle found in the normalised copy."""
    head = normalised_needle[:40]
    low = text.lower()
    i = low.find(head)
    if i >= 0:
        return i
    probe = re.sub(r"\s+", r"\\s+", re.escape(head))
    m = re.search(probe, text, re.I)
    return m.start() if m else 0


# ── endnotes and the claims that use them ──────────────────────────────────

def endnotes():
    """[(report, number, quote, url)] across the research reports."""
    out = []
    for path in sorted(glob.glob(os.path.join(REPORTS, "*.md"))):
        name = os.path.basename(path)
        # PART-* are research FRAGMENTS, not reports: their endnotes are renumbered into
        # the synthesised report, so auditing both double-counts every citation.
        if name in SKIP or name.startswith(("AUDIT-chunk", "PART-")) or name.endswith("-rx-review.md"):
            continue
        for line in open(path, encoding="utf-8", errors="ignore"):
            t = line.strip()
            m = re.match(r"^\[(\d+)\]\s+(.*)$", t)
            if not m:
                continue
            u = re.search(r"https?://[^\s)\"'>]+", t)
            if not u:
                continue
            # Every quoted run, not just the longest. An endnote usually carries the article
            # TITLE in quotes as well as the claim it is cited for, and picking the longest
            # string grabs whichever happens to be longer - which is how a title ended up
            # being "located" (or not) instead of the quote that matters.
            quotes = [q for q in re.findall(r'"([^"]{20,})"', t)]
            out.append((name, int(m.group(1)), quotes, u.group(0).rstrip(".,;)")))
    return out


def claim_for(report, number):
    """The sentence(s) in the report that cite [number].

    Without this the auditor can only judge whether the quote exists. The question that matters
    is what the report DID with it, and that lives in the body text, not the endnote.
    """
    path = os.path.join(REPORTS, report)
    if not os.path.exists(path):
        return ""
    text = open(path, encoding="utf-8", errors="ignore").read()
    text = re.split(r"\n#+\s*(?:References|Endnotes)\b", text)[0]
    hits = []
    for para in re.split(r"\n\s*\n", text):
        if "[%d]" % number not in para:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", para):
            if "[%d]" % number in sent:
                hits.append(" ".join(sent.split()))
    return " ... ".join(hits[:3])[:900]


# ── build ──────────────────────────────────────────────────────────────────

def _emit_verdict(kind, evs):
    """Best-effort verdict-cache metrics to Loki, mirroring rxfetch's fetch/search events so the
    RX-Review dashboard can chart them. `evs` is a list of dicts each carrying at least
    {outcome, report}. Grouped into one Loki stream per distinct outcome and pushed in a single
    batched request per stream; every event also carries `card` (HERMES_KANBAN_TASK) and `report`
    for the per-card / per-reviewer breakdowns. Fire-and-forget: any failure — endpoint down,
    metrics opted out — is swallowed and never touches the audit. Honours RX_METRICS=0 like the
    fetch/search emitters, so the test suite does not push fixtures into the real dashboard."""
    if not evs:
        return
    # The metrics gate is best-effort too: without the web-access skill installed, `import
    # rxfetch` binds the explain-when-called stub, whose __getattr__ RAISES on any attribute —
    # including _metrics_enabled(). That exception escaped this emitter into callers'
    # fail-safe blocks, where a swallowed error looks like "no reuse" (rx_test.py died in CI,
    # 2026-08-14). Metrics are fire-and-forget; nothing they do may break real work.
    try:
        if not rxfetch._metrics_enabled():
            return
    except Exception:                                          # noqa: BLE001
        return
    try:
        import urllib.request                                   # noqa: PLC0415
        card = os.environ.get("HERMES_KANBAN_TASK", "")
        base_ns = int(time.time() * 1000) * 1_000_000
        streams, seq = {}, 0
        for e in evs:
            ev = dict(e)
            ev["card"], ev["kind"] = card, kind
            ev.setdefault("ts", base_ns // 1_000_000)
            # Strictly increasing ns within the push so Loki never rejects a same-ms batch.
            streams.setdefault(str(e.get("outcome", "")), []).append(
                [str(base_ns + seq), json.dumps(ev)])
            seq += 1
        payload = {"streams": [
            {"stream": {"job": "rx-verdict", "kind": kind, "outcome": out}, "values": vals}
            for out, vals in streams.items()]}
        req = urllib.request.Request(
            rxfetch._LOKI_URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2.5).read()
    except Exception:                                          # noqa: BLE001
        pass


def cmd_build(args):
    notes = endnotes()
    print("  endnotes            : %d" % len(notes))
    urls = sorted({u for _, _, _, u in notes})
    print("  unique sources      : %d" % len(urls))

    texts, index = {}, {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        for u, t in zip(urls, ex.map(fetch_text, urls)):
            texts[u] = t
            index[u] = section_index(t) if t else []
    got = sum(1 for t in texts.values() if t)
    print("  sources fetched     : %d of %d" % (got, len(urls)))

    rows, kinds = [], {"exact": 0, "fuzzy": 0, "absent": 0, "unfetched": 0}
    for report, n, quote, url in notes:
        text = texts.get(url) or ""
        if not text:
            kinds["unfetched"] += 1
            rows.append({"report": report, "n": n, "url": url,
                         "quote": max(quote, key=len) if quote else "",
                         "claim": claim_for(report, n), "match": "unfetched",
                         "heading": "", "section": ""})
            continue
        kind, pos, quote = find_best_quote(text, quote)
        kinds[kind] += 1
        if pos is None:
            heading, section = "(quote not located)", text[:CONTEXT_IF_NO_SECTION]
        else:
            heading, section = enclosing_section(text, pos, index[url])
        rows.append({"report": report, "n": n, "url": url, "quote": quote,
                     "claim": claim_for(report, n), "match": kind,
                     "heading": heading, "section": " ".join(section.split())})

    # Content key + verdict-cache probe. Only located citations (exact/fuzzy) have a real section
    # to anchor on. This stamps the key onto each row and MEASURES the reuse ceiling; actually
    # skipping a judgment on a hit is a later slice (the strength-aware claim-equivalence gate).
    located = [r for r in rows if r["match"] in ("exact", "fuzzy")]
    hit_anchor = hit_exact = 0
    probe_evs = []
    for r in located:
        r["anchor"] = rxverdict.anchor_key(r["section"], r["quote"])
        cached = rxverdict.lookup(r["section"], r["quote"])
        exact = False
        if cached:
            hit_anchor += 1
            if any(c.get("claim") == r["claim"] for c in cached.get("claims", [])):
                hit_exact += 1
                exact = True
        probe_evs.append({"outcome": "hit" if cached else "miss", "report": r["report"],
                          "n": r["n"], "anchor": r["anchor"], "exact": exact})
    _emit_verdict("probe", probe_evs)

    json.dump({"rows": rows}, open(LOCATIONS, "w"), indent=1)
    vc = rxverdict.stats()
    print("  verdict cache       : %d anchor(s)/%d claim(s) stored; this run %d/%d located hit a "
          "cached anchor (%d already this exact claim)"
          % (vc["anchors"], vc["claims"], hit_anchor, len(located), hit_exact))
    print("  located             : exact=%d fuzzy=%d absent=%d unfetched=%d"
          % (kinds["exact"], kinds["fuzzy"], kinds["absent"], kinds["unfetched"]))
    sec = [len(r["section"]) for r in rows if r["section"]]
    if sec:
        print("  section size        : median %d chars (~%d tokens), max %d"
              % (sorted(sec)[len(sec) // 2], sorted(sec)[len(sec) // 2] // CHARS_PER_TOKEN,
                 max(sec)))
    print("  written             : %s" % LOCATIONS)
    if rxverdict.REUSE_ENABLED:
        _resolve_from_cache(rows)


def _resolve_from_cache(rows):
    """Reuse a cached verdict for any located citation whose (section, quote) anchor was judged
    before AND whose claim is confirmed equivalent to a cached claim. Writes the reused verdicts to
    CONTEXT-audit-cache.md, so cmd_fanout (which skips `already_judged`) drops them from the audit
    and cmd_merge folds them in. FAIL-SAFE throughout: any endpoint failure → no reuse → the
    citation is judged normally. Off unless RX_VERDICT_REUSE=1."""
    try:
        cand = [(r, rxverdict.lookup(r["section"], r["quote"])) for r in rows
                if r.get("match") in ("exact", "fuzzy") and r.get("section")]
        cand = [(r, e) for r, e in cand if e and e.get("claims")]
        if not cand:
            return
        new_claims = [r["claim"] for r, _ in cand]
        cached_lists = [[c["claim"] for c in e["claims"]] for _, e in cand]
        flat = [c for lst in cached_lists for c in lst]
        embs = rxverdict.embed(new_claims + flat)
        if not embs:
            print("  verdict reuse       : embeddings unavailable — judging all citations")
            return
        new_e, cached_e = embs[:len(new_claims)], embs[len(new_claims):]
        per, i = [], 0
        for lst in cached_lists:
            per.append(cached_e[i:i + len(lst)]); i += len(lst)
        reused, reuse_evs = [], []
        for (r, e), ne, ces in zip(cand, new_e, per):
            best_i, best_s = -1, -1.0
            for j, ce in enumerate(ces):
                s = rxverdict.cosine(ne, ce)
                if s > best_s:
                    best_i, best_s = j, s
            if best_i < 0 or best_s < rxverdict.EMBED_THRESHOLD:
                reuse_evs.append({"outcome": "rejudged", "report": r["report"], "n": r["n"],
                                  "cos": round(best_s, 3), "why": "below_threshold"})
                continue                                       # no close candidate → re-judge
            c = e["claims"][best_i]
            if rxverdict.confirm_equivalent(r["claim"], c["claim"]):
                reused.append((r, c, best_s))
                reuse_evs.append({"outcome": "reused", "report": r["report"], "n": r["n"],
                                  "cos": round(best_s, 3), "verdict": c.get("verdict", "")})
            else:
                reuse_evs.append({"outcome": "rejudged", "report": r["report"], "n": r["n"],
                                  "cos": round(best_s, 3), "why": "confirm_no"})
        _emit_verdict("reuse", reuse_evs)
        if reused:
            with open(os.path.join(REPORTS, "CONTEXT-audit-cache.md"), "w", encoding="utf-8") as fh:
                fh.write("# Reused from the verdict cache (confirmed claim-equivalent)\n\n")
                for r, c, s in reused:
                    fh.write("%s | %s | %s | %s | reused from cache (cos=%.2f): %s\n"
                             % (c["verdict"], r["report"], r["n"], r.get("heading", ""),
                                s, (c.get("claim") or "")[:80]))
        print("  verdict reuse       : %d candidate(s) -> %d reused, %d re-judged"
              % (len(cand), len(reused), len(cand) - len(reused)))
    except Exception as e:                                     # noqa: BLE001
        print("  verdict reuse       : skipped (%s) — judging all citations" % str(e)[:80])
    return rows


BODY = """Judge whether each source below supports the use the report made of it.

The quote is already located in the source; judge from the section text you are given rather than
fetching — whether the surrounding section actually means what the claim takes it to mean.

Your items are in {items_file}, in this working directory — the only file you need. For each
item you are given:
  CLAIM    - what the report asserts, in its own words
  QUOTE    - the sentence it cites
  MATCH    - exact / fuzzy / absent (absent = the quote was not found in the source text)
  SECTION  - the heading the quote sits under, and that section's text

Verdict per item, choosing the FIRST that applies:

  supported        - the section says this, and the claim uses it for what it says
  context-reversed - the surrounding text negates, refutes or contradicts the quoted sentence;
                     it is a hypothesis, a straw man, a limitation, or a position the source
                     goes on to reject
  scope-mismatch   - the quote is real but the section covers a different indication,
                     population, dose, route, species, or endpoint than the claim assumes.
                     Check the HEADING against the claim: a statement under "Adverse Reactions
                     in Atopic Dermatitis" does not support a claim about rheumatoid arthritis
  overstated       - the section supports something weaker: an association reported as
                     causation, an exploratory or secondary finding presented as primary,
                     a relative risk presented as absolute, a single trial presented as settled
  misquoted        - the section addresses the point but the quoted wording is not what it says
  unsupported      - the section does not address this claim at all
  absent           - the quote is not in the source. Say whether the claim is nonetheless
                     supported by the section you were given (a paraphrase is legitimate;
                     a fabrication is not)

Be specific, and where you are unsure say so rather than passing it.

Append ONE line per item to {out} as you go, before starting the next:

  <verdict> | <report> | <endnote> | <heading> | <one-line reason, quoting the section if it
  contradicts the claim>

When {out} already exists, APPEND to it and skip any item that already has a line — a previous
attempt may have got part way. Otherwise start it with `# Context audit - part {n}`.

When every item below has a line, kanban_complete with metadata:
  {{"part": {n}, "supported": N, "context_reversed": N, "scope_mismatch": N,
    "overstated": N, "misquoted": N, "unsupported": N, "absent": N}}

That file holds {count} item(s). Work through every one of them.
"""


def _render(r):
    return "\n".join([
        "### %s [%d]  (match: %s)" % (r["report"], r["n"], r["match"]),
        "CLAIM   : %s" % (r["claim"] or "(no citing sentence found in the report)"),
        "QUOTE   : %s" % (r["quote"] or "(no quoted text in the endnote)"),
        "SOURCE  : %s" % r["url"],
        "HEADING : %s" % (r["heading"] or "(none)"),
        "SECTION : %s" % (r["section"] or "(source could not be fetched)"),
        "",
    ])




# Each Hermes profile is a full HERMES_HOME with its own state.db, so worker token usage
# lives per profile - the top-level ~/.hermes/state.db has profile_name NULL on every row and
# knows nothing about the workers.
PROFILE_DBS = os.path.expanduser("~/.hermes/profiles/rx-*/state.db")


CONTEXT_FLOOR = 64_000


def worker_context():
    """The context window a worker card can actually use, in tokens.

    Read from Hermes' own configuration rather than by probing a GPU host. Two reasons, and
    the second is the one that bites:

      * It is the operative number. These cards are run by Hermes workers through litellm, and
        Hermes will not send more than its configured context_length whatever the backend
        happens to have loaded.
      * Backends move. This used to ask llama-swap directly at a hardcoded 192.168.1.4:10400.
        Serving moved to .16, every probe then failed, and the pipeline quietly planned against
        a quarter of the real window with one warning line as the only trace. The homelab rule
        exists for exactly this case: "clients never talk to a GPU host directly ... litellm is
        the only front door." A pipeline that pins a backend address inherits every migration.
    """
    try:
        cfg = open(os.path.expanduser("~/.hermes/config.yaml"), encoding="utf-8").read()
        m = re.search(r"^\s*context_length:\s*(\d+)", cfg, re.M)
        if m:
            return int(m.group(1))
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not read context_length from ~/.hermes/config.yaml (%s)" % exc)
    print("  ! no configured context_length; falling back to a conservative %d" % CONTEXT_FLOOR)
    return CONTEXT_FLOOR


def phase_stats(since_ts):
    """Token totals for rx-* worker sessions started since `since_ts`.

    Read from Hermes' own per-profile state.db files, so nothing here depends on llama-swap or
    litellm being reachable. Returns {} when there is no data rather than guessing - an
    invented throughput number is worse than none.
    """
    import glob as _glob
    import sqlite3
    tin = tout = treas = n = 0
    for db in _glob.glob(PROFILE_DBS):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            row = c.execute(
                "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                "       COALESCE(SUM(reasoning_tokens),0), COUNT(*) "
                "FROM sessions WHERE started_at >= ?", (since_ts,)).fetchone()
            c.close()
        except Exception:                                      # noqa: BLE001
            continue
        if row:
            tin += row[0] or 0; tout += row[1] or 0
            treas += row[2] or 0; n += row[3] or 0
    if not n:
        return {}
    return {"input": int(tin), "output": int(tout), "reasoning": int(treas), "sessions": int(n)}


def _fmt_stats(st, wall_s):
    """One line of throughput, omitting anything Hermes did not record."""
    bits = []
    if wall_s:
        m, sec = divmod(int(wall_s), 60)
        bits.append("%dm %02ds wall" % (m, sec))
    if st.get("input") or st.get("output"):
        bits.append("%s in / %s out tokens" % ("{:,}".format(st["input"]),
                                               "{:,}".format(st["output"])))
        if st.get("reasoning"):
            bits.append("{:,} reasoning".format(st["reasoning"]))
        if wall_s and st.get("output"):
            bits.append("%.1f tok/s generated" % (st["output"] / max(wall_s, 1)))
    if st.get("sessions"):
        bits.append("%d worker session(s)" % st["sessions"])
    return " · ".join(bits) if bits else "(Hermes recorded no token data for this phase)"



def create(title, assignee, body, parents=(), runtime="45m", priority=0, dry=False,
           notify=False):
    """Card rooted in reports/, keyed on its own title. Mechanics live in rxkanban."""
    tid = rxkanban.create_card(title, assignee, body, REPORTS, parents=parents,
                               runtime=runtime, priority=priority,
                               key=rxkanban.slugify(title), dry=dry, notify=notify)
    if dry:
        print("  would create: %s" % title)
    return tid



def already_judged():
    """(report, endnote) pairs that already carry a verdict line.

    Makes a retry cost one in-flight item instead of the whole card, and lets the fanout be
    re-run after a resize without redoing work. Part 05 wrote 13 verdicts, timed out, and its
    retry was set to overwrite the file from the top - 13 judgements thrown away and the same
    45 minutes spent again.
    """
    done = set()
    for f in glob.glob(os.path.join(REPORTS, "CONTEXT-audit-*.md")):
        for line in open(f, encoding="utf-8", errors="ignore"):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) < 3:
                continue
            # The endnote is written variously as `23`, ` 1`, or `[1]` - accept all three.
            m = re.match(r"^\[?(\d+)\]?$", parts[2])
            if m and parts[1].endswith(".md"):
                done.add((parts[1], int(m.group(1))))
    return done


def cmd_fanout(args):
    rows = json.load(open(LOCATIONS))["rows"] if os.path.exists(LOCATIONS) else cmd_build(args)
    done = already_judged()
    if done:
        before = len(rows)
        rows = [r for r in rows if (r["report"], r["n"]) not in done]
        print("  already judged      : %d skipped" % (before - len(rows)))
    if not rows:
        print("  nothing left to judge.")
        return []
    rnd = getattr(args, "round", 1)
    parts, cur, size = [], [], 0
    for r in rows:
        w = len(r["section"]) + len(r["claim"]) + len(r["quote"]) + 200
        if cur and (size + w > CARD_BUDGET_CHARS or len(cur) >= MAX_CITATIONS_PER_CARD):
            parts.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += w
    if cur:
        parts.append(cur)
    print("\n  %d citation(s) -> %d card(s)" % (len(rows), len(parts)))

    ids, expected_parts = [], []
    for i, part in enumerate(parts, 1):
        # Round-suffixed so a re-plan after a resize does not append unrelated items to the
        # files a previous round already finished. merge() and already_judged() glob both.
        out = ("CONTEXT-audit-%02d.md" % i) if rnd == 1 else ("CONTEXT-audit-r%d-%02d.md" % (rnd, i))

        # Items go to a FILE, not into the card body.
        #
        # build_worker_context() caps task.body at _CTX_MAX_BODY_BYTES = 8KB and appends a
        # truncation marker. Inlining ~10 items produced 31-85KB bodies, of which only the
        # first 1-3 items survived that cap. Nothing broke, because kanban_show also returns
        # the FULL task.body alongside the capped worker_context and these workers read that
        # field - but the card was one incurious worker away from silently auditing 2 of 25
        # citations and reporting done. A body that fits is not luck.
        items_file = ("CONTEXT-items-%02d.md" % i) if rnd == 1 else ("CONTEXT-items-r%d-%02d.md" % (rnd, i))
        if not args.dry_run:
            with open(os.path.join(REPORTS, items_file), "w", encoding="utf-8") as fh:
                fh.write("# Items for context audit part %d\n\n" % i)
                fh.write("\n".join(_render(r) for r in part))
        body = BODY.format(out=out, n=i, count=len(part), items_file=items_file)
        if len(body.encode()) > KANBAN_BODY_CAP:
            raise SystemExit(
                "card body is %d bytes, over the %d-byte kanban cap - it would be silently "
                "truncated in worker_context. Move more of it into %s."
                % (len(body.encode()), KANBAN_BODY_CAP, items_file))
        tid = create("Context audit %02d/%02d%s: do the sources support the claims"
                     % (i, len(parts), "" if rnd == 1 else " r%d" % rnd), "rx-audit", body,
                     runtime="%dm" % CARD_RUNTIME_MINUTES, priority=33, dry=args.dry_run)
        expected_parts.append(out)
        ids.append(tid)
        print("  %s  part %02d  %2d items  ~%dk tokens"
              % (tid, i, len(part), sum(len(r["section"]) for r in part) // CHARS_PER_TOKEN // 1000))

    real = [i for i in ids if i != "DRY"]
    if not args.dry_run:
        # Written BEFORE the cards run, so the merge can tell "a part has not finished" from
        # "a part finished and wrote nothing".
        write_manifest("CONTEXT-audit", expected_parts)
    if not args.dry_run:
        import time as _t
        _started = _t.time()
        try:
            _st = json.load(open(LOCATIONS)); _st["phase_started"] = _started
            json.dump(_st, open(LOCATIONS, "w"), indent=1)
        except Exception:                                      # noqa: BLE001
            pass
        announce("**Citation audit started** %s\n%d citation(s) across %d card(s). "
                 "I will report once they are all judged."
                 % (_t.strftime("%H:%M", _t.localtime(_started)), len(rows), len(parts)))

    # Round-suffixed title, because the idempotency key is derived from it. Re-planning with
    # the same title returned the EXISTING merge card and silently DISCARDED the new --parent
    # arguments - so the merge kept round 1's parents, and when those were archived it became
    # ready and started merging 88 of 303 verdicts while the audit was still running.
    # (recompute_ready treats an `archived` parent as satisfied, same as `done`.)
    merge = create("Merge context audit into CONTEXT-AUDIT.md%s"
                   % ("" if rnd == 1 else " (round %d)" % rnd),
                   "rx-intake", MERGE_BODY, parents=real, runtime="15m", priority=32,
                   dry=args.dry_run)
    print("  %s  merge (gated on all %d parts)" % (merge, len(ids)))

    # Put the merge in front of the "Stage 7: Adversarial Complete" barrier, so Stage 8 cannot
    # start until the context audit has actually been consolidated. Done in code, not
    # left as a step someone has to remember - and only ever spliced into a card that has not
    # started, since linking a parent onto a running card does nothing.
    # Each round schedules its own sweep, gated on this round's merge. A card that completes
    # having judged only some of its items is invisible from the board, so something has to
    # go back and look.
    sweep = None
    if rnd < MAX_SWEEP_ROUNDS:
        sweep = create("Sweep: re-judge citations with no verdict (round %d)" % (rnd + 1),
                       "rx-intake", SWEEP_BODY.format(n=rnd + 1), parents=[merge],
                       runtime="15m", priority=32, dry=args.dry_run)
        print("  %s  sweep round %d (gated on the merge)" % (sweep, rnd + 1))

    if not args.dry_run and merge != "DRY":
        # Read the board through the Hermes CLI, never kanban.db directly — see rxkanban.splice().
        cids = [cid for (cid, _t, _s) in rxkanban.board_cards(
            title_like="Stage 7: Adversarial Complete%", statuses=("todo", "ready"))]
        for cid in cids:
            for upstream in [x for x in (merge, sweep) if x and x != "DRY"]:
                subprocess.run([HERMES, "kanban", "--board", BOARD, "link", upstream, cid],
                               capture_output=True, text=True)
                print("  linked %s -> barrier %s" % (upstream, cid))
    return real



MERGE_BODY = """Run this, then kanban_complete with the totals it prints:

    python3 ~/hermes-skills/rx-review/scripts/verify.py merge

Do nothing else.
"""

VERDICTS = ("supported", "context-reversed", "scope-mismatch", "overstated",
            "misquoted", "unsupported", "absent")


def write_manifest(name, parts):
    """Record the part files a fanout EXPECTS, so a merge can tell 'none yet' from 'lost one'.

    Every gather here globbed what existed and errored only at zero, so a part card that was
    killed at its runtime cap, or completed writing nothing, produced a merge that read
    complete: "N finding(s) across 11 part(s)" when twelve were planned. The count the merge
    needs is known at fanout time and was only ever printed to stdout, where nothing consumed
    it.

    Accumulates across rounds, because a later round adds parts without invalidating earlier
    ones. Stored as .json so no `*.md` glob in this pipeline can mistake it for a report.
    """
    path = os.path.join(REPORTS, "%s.manifest.json" % name)
    merged = sorted(set(read_manifest(name)) | set(parts))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"parts": merged}, fh, indent=1)
    return merged


def read_manifest(name):
    path = os.path.join(REPORTS, "%s.manifest.json" % name)
    if not os.path.exists(path):
        return []
    try:
        return list(json.load(open(path, encoding="utf-8")).get("parts", []))
    except (ValueError, OSError):
        return []


def missing_parts(name):
    """Expected part files that are not on disk. Empty when no manifest exists (pre-upgrade)."""
    return [p for p in read_manifest(name)
            if not os.path.exists(os.path.join(REPORTS, p))]


def refuse_if_incomplete(name, what):
    missing = missing_parts(name)
    if not missing:
        return
    print("REFUSING to merge: %d of %d expected %s part(s) are missing.\n"
          "A killed or empty part card would otherwise be published as a complete result."
          % (len(missing), len(read_manifest(name)), what))
    for m in missing[:20]:
        print("   missing: %s" % m)
    print("Re-run the part card(s) that did not write, then merge again.")
    raise SystemExit(1)


def cmd_merge(args):
    refuse_if_incomplete("CONTEXT-audit", "context audit")
    parts = sorted(glob.glob(os.path.join(REPORTS, "CONTEXT-audit-*.md")))
    if not parts:
        print("no CONTEXT-audit-*.md found")
        raise SystemExit(1)
    counts = {v: 0 for v in VERDICTS}
    rows, seen, unparsed = [], set(), []
    for p in parts:
        for line in open(p, encoding="utf-8", errors="ignore"):
            t = line.strip()
            if not t or t.startswith("#") or "|" not in t:
                continue
            v = t.split("|")[0].strip().lower()
            if t in seen:
                continue
            # This drop was the worst bug in the file, because TWO parsers disagreed about
            # what a verdict is. already_judged() and _verdict_lines() accept any line with
            # three fields, a .md name and a number - they never look at the vocabulary. So
            # "context reversed" (space, not hyphen) counted as JUDGED for the sweep, which
            # then reported SWEEP: CLEAN, while this loop discarded it. The citation was
            # announced as audited and appeared nowhere in CONTEXT-AUDIT.md, and a refuted
            # claim reached the prescriber brief with nothing marking it.
            if v not in counts:
                unparsed.append((os.path.basename(p), t))
                continue
            seen.add(t)
            counts[v] += 1
            rows.append(t)
    if unparsed:
        print("REFUSING to merge: %d line(s) carry no recognised verdict. The sweep counts\n"
              "these as judged, so publishing without them reports full coverage of citations\n"
              "that were never actually recorded. Fix the part file(s) and re-run."
              % len(unparsed))
        for src, t in unparsed[:20]:
            print("   %-26s %s" % (src, t[:96]))
        print("Expected first field: %s" % ", ".join(VERDICTS))
        raise SystemExit(1)
    total = sum(counts.values())
    index = _locator_index()
    grouped = {"supported": [], "evidence": [], "unverified": []}
    for r in sorted(rows):
        grouped[_row_basis(r, index)].append(r)

    with open(os.path.join(REPORTS, "CONTEXT-AUDIT.md"), "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT-AUDIT - do the sources support the claims?\n\n")
        fh.write("Each citation was located in its source mechanically; a reviewer then judged\n"
                 "the ENCLOSING SECTION, not just the sentence. %d citations across %d parts.\n\n"
                 % (total, len(parts)))
        for v in VERDICTS:
            fh.write("- **%s**: %d\n" % (v, counts[v]))
        fh.write("\nSplit by whether the auditor actually had the text:\n")
        fh.write("- **supported**: %d\n" % len(grouped["supported"]))
        fh.write("- **evidence findings** (source read, claim not supported): %d\n"
                 % len(grouped["evidence"]))
        fh.write("- **unverified** (source unreadable or quote never located): %d\n"
                 % len(grouped["unverified"]))
        fh.write("\nNo claim below `supported` may enter the brief. But the two failure classes\n"
                 "are NOT the same finding: an evidence finding says the literature does not\n"
                 "support the claim; an unverified one says we could not check. Report each as\n"
                 "what it is.\n")
        fh.write("\n`verdict | report | endnote | heading | reason`\n")
        for title, key in (("Supported", "supported"),
                           ("Evidence findings - the source was read and does not support the claim",
                            "evidence"),
                           ("Unverified - the source could not be read or the quote never located",
                            "unverified")):
            fh.write("\n## %s (%d)\n\n" % (title, len(grouped[key])))
            for r in grouped[key]:
                fh.write("%s\n" % r)

    # Populate the verdict cache — SERIAL writer (this merge runs on ONE card; the parallel audit
    # cards only wrote their own CONTEXT-audit-*.md). Join each verdict back to its located section
    # through locations.json and record it, so a later run's cmd_build can measure/reuse it. Guarded
    # so a cache hiccup never fails the merge — CONTEXT-AUDIT.md is already written.
    try:
        def _en(x):
            return str(x).strip("[] ").strip()
        _loc = {(r.get("report", ""), _en(r.get("n", ""))): r
                for r in json.load(open(LOCATIONS)).get("rows", [])}
        _cached = 0
        for line in rows:
            f = [x.strip() for x in line.split("|")]
            lr = _loc.get((f[1], _en(f[2]))) if len(f) >= 3 else None
            if lr and lr.get("match") in ("exact", "fuzzy") and lr.get("section"):
                rxverdict.record(lr["section"], lr["quote"], lr["claim"],
                                 f[0].lower(), f[4] if len(f) > 4 else "")
                _cached += 1
        if _cached:
            print("  verdict cache       : recorded %d verdict(s); store now %d anchor(s)"
                  % (_cached, rxverdict.stats()["anchors"]))
    except Exception as _e:                                    # noqa: BLE001
        print("  verdict cache       : not updated (%s)" % str(_e)[:80])

    # Verdict distribution to the dashboard: one event per merged verdict line, outcome = the
    # verdict itself, tagged with the reviewer (report) for the per-card panels.
    _verdict_evs = []
    for line in rows:
        f = [x.strip() for x in line.split("|")]
        if len(f) >= 3:
            _verdict_evs.append({"outcome": f[0].lower(), "report": f[1], "n": f[2].strip("[] ")})
    _emit_verdict("verdict", _verdict_evs)

    print(json.dumps({"parts": len(parts), "checked": total,
                      "evidence_findings": len(grouped["evidence"]),
                      "unverified": len(grouped["unverified"]), **counts}))

    import time as _t
    _started = 0
    try:
        _started = json.load(open(LOCATIONS)).get("phase_started") or 0
    except Exception:                                          # noqa: BLE001
        pass
    _wall = (_t.time() - _started) if _started else 0
    _clean = counts.get("supported", 0)
    _msg = ["**Citation audit complete** %s" % _t.strftime("%H:%M", _t.localtime()),
            "%d citation(s) judged - %d supported, %d not." % (total, _clean, total - _clean)]
    _bad = ", ".join("%s %d" % (k, v) for k, v in counts.items() if v and k != "supported")
    if _bad:
        _msg.append("Problems: %s." % _bad)
    _msg.append(_fmt_stats(phase_stats(_started) if _started else {}, _wall))
    announce("\n".join(_msg))



MAX_SWEEP_ROUNDS = 4

SWEEP_BODY = """Run this, then act on its FIRST line:

    python3 ~/hermes-skills/rx-review/scripts/verify.py sweep --round {n}

- `SWEEP: CLEAN` — kanban_complete with the metadata printed.
- `SWEEP: SCHEDULED` — kanban_complete, declaring the ids it printed in created_cards=[...], with
  the metadata printed.
- `SWEEP: BLOCKED` — kanban_block with the full output.

Do nothing else.
"""


UNREADABLE_MATCHES = {"absent", "unfetched"}

# Below this, the section captured at judge time was not a document - a placeholder, a
# bot wall, or nothing. Above it, the auditor had real text in front of them.
MIN_JUDGED_SECTION_CHARS = 200

# Verdicts that cannot be reached without the section in front of you: each one compares the
# claim against what the text actually says. Measured over a full run they were returned only
# on located citations. `absent` and `unsupported` carry no such guarantee — they are also what
# comes back when the reviewer was handed nothing — so those two defer to the locator.
TEXT_IN_HAND_VERDICTS = {"misquoted", "scope-mismatch", "overstated", "context-reversed"}


def _locator_index():
    """{(report, endnote): locator match type} from the build phase."""
    try:
        rows = json.load(open(LOCATIONS))["rows"]
    except Exception:                                          # noqa: BLE001
        return {}
    return {(r.get("report", ""), str(r.get("n", ""))): (r.get("match") or "") for r in rows}


def _row_basis(row, index):
    """Whether a verdict is a statement about the LITERATURE or about our own reach.

    A verdict only speaks to the claim when the quote was actually located. Measured over a
    full run: misquoted, scope-mismatch, overstated and context-reversed were returned ONLY on
    exact or fuzzy matches — never once on a citation whose source was unreachable. `absent`
    collapsed almost perfectly onto match=absent (37 of 39), and `unsupported` split down the
    middle: 22 judged with the section in hand, 32 handed nothing to read.

    Conflating the two is how a bot wall becomes "the literature contradicts this" in a brief
    written for a prescriber. Both classes are demoted; only one is a finding about evidence.
    An unjoinable row defaults to `unverified`, because overstating our reach is the more
    dangerous error of the two.
    """
    parts = [c.strip() for c in row.split("|")]
    verdict = parts[0].lower() if parts else ""
    report = re.sub(r"\s*\[\d+\].*$", "", parts[1]) if len(parts) > 1 else ""
    endnote = re.sub(r"\D", "", parts[2]) if len(parts) > 2 else ""
    if verdict == "supported":
        return "supported"
    if verdict in TEXT_IN_HAND_VERDICTS:
        return "evidence"
    match = index.get((report, endnote), "")
    return "unverified" if (not match or match in UNREADABLE_MATCHES) else "evidence"


def _verdict_lines():
    """[(path, line, (report, n))] for every verdict line across the audit parts."""
    out = []
    for f in sorted(glob.glob(os.path.join(REPORTS, "CONTEXT-audit-*.md"))):
        for line in open(f, encoding="utf-8", errors="ignore"):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) < 3:
                continue
            m = re.match(r"^\[?(\d+)\]?$", parts[2])
            if m and parts[1].endswith(".md"):
                out.append((f, line, (parts[1], int(m.group(1)))))
    return out


def stale_unverified_items(revived=()):
    """Citations judged WITHOUT the text, whose source now reads.

    A verdict is sticky: already_judged() counts any verdict line at all, so a citation judged
    `absent` because its source answered a bot wall keeps that verdict for good and the sweep
    reports CLEAN over it. That turns a two-second network failure into a permanent demotion of
    a claim while the pipeline reports success - the exact shape of every other bug this
    pipeline has had.

    Re-judging is gated on NEW EVIDENCE, not on hope: the source has to return usable text now.
    A genuinely unreachable source is never rescheduled, so this cannot spin. Each citation is
    revived at most once (tracked in the sweep state), so a source that reads fine but whose
    quote is genuinely absent settles after one retry instead of looping.
    """
    if not os.path.exists(LOCATIONS):
        return []
    rows = {(r["report"], r["n"]): r for r in json.load(open(LOCATIONS))["rows"]}
    index = _locator_index()
    revived = set(tuple(x) for x in revived)
    candidates = []
    for _f, line, key in _verdict_lines():
        if key in revived or key not in rows:
            continue
        if _row_basis(line, index) == "unverified":
            candidates.append(key)
    out = []
    for key in candidates:
        row = rows[key]
        # Revival needs evidence the source was UNREADABLE WHEN JUDGED - not merely that it
        # reads now. `match: absent` covers two different situations that must not be
        # conflated: the source could not be read, or it was read and the quote is not in it.
        # The second is a legitimate verdict, and re-judging it can only produce the same
        # answer. Measured over one run, 33 of 33 revivals were of the second kind: every one
        # had thousands of characters of section text captured at judge time, and two of the
        # three that then blocked the sweep were looking for a page TITLE rather than body
        # text. The locator already records what we need, so ask it instead of the network.
        section = row.get("section") or ""
        if row.get("match") != "unfetched" and len(section) >= MIN_JUDGED_SECTION_CHARS:
            continue
        url = row.get("url") or ""
        if url and rxfetch.fetch(url).ok:
            out.append(row)
    return out


def invalidate_verdicts(keys):
    """Drop verdict lines for these citations so the sweep will schedule them again.

    Rewrites the audit parts in place. Only ever called for verdicts reached without the text,
    whose source has since become readable - a judgement made on real text is never discarded.
    """
    drop = set(keys)
    if not drop:
        return 0
    by_file = {}
    for f, line, key in _verdict_lines():
        if key in drop:
            by_file.setdefault(f, set()).add(line)
    removed = 0
    for f, lines in by_file.items():
        kept = [l for l in open(f, encoding="utf-8", errors="ignore") if l not in lines]
        removed += sum(1 for _ in lines)
        open(f, "w", encoding="utf-8").writelines(kept)
    return removed


def outstanding_items():
    """Located citations that carry no verdict yet."""
    if not os.path.exists(LOCATIONS):
        return []
    rows = json.load(open(LOCATIONS))["rows"]
    done = already_judged()
    return [r for r in rows if (r["report"], r["n"]) not in done]


def _sweep_state(d=None, dry=False):
    try:
        st = json.load(open(LOCATIONS)) if os.path.exists(LOCATIONS) else {}
    except Exception:                                          # noqa: BLE001
        st = {}
    if d is None:
        return st.get("sweep", {}) or {}
    if dry:
        return st.get("sweep", {}) or {}
    st["sweep"] = d
    json.dump(st, open(LOCATIONS, "w"), indent=1)
    return d


def cmd_sweep(args):
    """Re-plan any citation that never got judged; stop when there is nothing left.

    A context-audit card that completes having judged only some of its items looks identical
    to a clean one on the board. Nothing else notices, and the brief is then assembled as
    though those citations had been checked.

    Loop-until-dry, with a no-progress guard so a genuinely unjudgeable item cannot spin
    forever.
    """
    rnd = getattr(args, "round", 1)
    state = _sweep_state()
    prev = state.get("outstanding")
    revived = state.get("revived") or []

    # Before deciding the audit is clean, look for verdicts that were never really judgements:
    # reached with no text in hand, against a source that now reads. Those are rescheduled once.
    #
    # But NEVER on the final round. Reviving discards a verdict in order to re-judge it, and on
    # the last round there is no round left to do the judging - so the citation goes from
    # "judged, unverified basis" to "no verdict at all", and is then reported as unjudged. That
    # is strictly worse than leaving it alone. It happened: three trend-rbc citations were
    # revived by round 4 of 4 and immediately declared outstanding.
    last_round = rnd >= MAX_SWEEP_ROUNDS
    stale = [] if last_round else stale_unverified_items(revived)
    if last_round:
        print("  final round — not reviving anything, there is no round left to re-judge it")
    if stale and not args.dry_run:
        keys = [(r["report"], r["n"]) for r in stale]
        n = invalidate_verdicts(keys)
        revived = revived + [list(k) for k in keys]
        print("  revived %d citation(s) judged without their source, now readable "
              "(%d stale verdict line(s) dropped)" % (len(stale), n))
    elif stale:
        print("  would revive %d citation(s) judged without their source" % len(stale))

    out = outstanding_items()

    if not out:
        print("SWEEP: CLEAN")
        print(json.dumps({"round": rnd, "outstanding": 0}))
        _sweep_state({"round": rnd, "outstanding": 0, "revived": revived}, args.dry_run)
        return 0

    # A revival deliberately ADDS work, so an outstanding count that grew because of one is
    # progress, not a stall. Counting it as a stall would block a sweep that is working.
    stalled = prev is not None and len(out) >= prev and not stale
    if rnd >= MAX_SWEEP_ROUNDS or stalled:
        print("SWEEP: BLOCKED")
        why = ("round %d made no progress (%d outstanding, was %s)" % (rnd, len(out), prev)
               if stalled else "reached the %d-round limit with %d outstanding"
               % (MAX_SWEEP_ROUNDS, len(out)))
        print("%s. These citations have no verdict:" % why)
        for r in out[:40]:
            print("  - %s [%s]  %s" % (r["report"], r["n"], r["url"][:80]))
        if len(out) > 40:
            print("  ... and %d more" % (len(out) - 40))
        print("\nRecorded as unjudged; the reconciler must treat any claim resting on them "
              "as unsupported.")
        print(json.dumps({"round": rnd, "outstanding": len(out), "blocked": True}))
        _sweep_state({"round": rnd, "outstanding": len(out), "blocked": True,
                      "revived": revived}, args.dry_run)
        # TELL THE USER. The card body has the worker kanban_block this, but a block on this board
        # reaches nobody — cards are not subscribed, so the audit sat blocked until someone looked
        # (t_638ad614, 2026-08-12: a round-limit block with no call to action in chat). The stage
        # backstops already post directly on a hold (rx.py `_hold`); the sweep is a hold too, so it
        # does the same. It REPORTS the state and the two operator choices; it does not ask.
        if not args.dry_run:
            preview = "\n".join("  - %s [%s]  %s" % (r["report"], r["n"], r["url"][:80])
                                for r in out[:12])
            more = ("\n  ... and %d more" % (len(out) - 12)) if len(out) > 12 else ""
            announce("**rx-review — citation audit HELD (sweep round %d)**\n%s.\n\n"
                     "These citations have no verdict and are recorded as unsupported:\n%s%s\n\n"
                     "The pipeline has stopped here. The reconciler will treat any claim resting "
                     "on them as unsupported — complete this sweep card to let the brief assemble "
                     "on that basis, or fix the source access and re-run the audit."
                     % (rnd, why, preview, more))
        return 0

    print("SWEEP: SCHEDULED")
    print("  %d citation(s) unjudged; planning round %d" % (len(out), rnd + 1))
    _sweep_state({"round": rnd, "outstanding": len(out), "revived": revived}, args.dry_run)
    args.round = rnd + 1
    cmd_fanout(args)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("build", cmd_build), ("fanout", cmd_fanout),
                     ("merge", cmd_merge), ("sweep", cmd_sweep)):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--round", type=int, default=1)
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
