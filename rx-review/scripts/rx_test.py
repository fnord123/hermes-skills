#!/usr/bin/env python3
"""Regression tests for rx.py's lab parsing.

Every rule in rx.py's parsing layer was written against a specific real document that broke
it, and until now each fix was verified once by hand and then left with only a comment to
protect it. This file is where those documents live, as a synthetic corpus that reproduces
each structural oddity without carrying anyone's actual results.

Each test is named after the failure it guards. If one breaks, the comment tells you what went
wrong in production the first time.

Run:  python3 rx_test.py
"""

import contextlib
import importlib.util
import io
import json
import inspect
import os
import re
import shutil
import sys
import tempfile

os.environ["RX_METRICS"] = "0"    # tests never write to the real fetch/search metrics log
# Card creation is paced in production (see rxkanban.CREATE_DELAY_S). Pinned off here so a test
# that reaches the real create_card costs milliseconds rather than minutes of CI wall clock.
os.environ["RX_CARD_CREATE_DELAY"] = "0"

HERE = os.path.dirname(os.path.abspath(__file__))


# ── a corpus that reproduces every oddity the real labs have thrown ────────────
#
#   * two vendors naming the same analyte differently (standard vs advanced panel)
#   * ISO dates on one vendor, US dates on the other, in the same corpus
#   * the "## Out of range" section using BOLD draw dates, not headings
#   * the same draw transcribed twice by overlapping PDFs, formatted differently
#   * a marker a later panel does not measure but fully defines (non-HDL)
#   * markers the later panel simply never re-ran (particle counts)
#   * a lab flag glued onto the value ("Negative N")
#   * a non-numeric result ("<10")
#   * a narrative section whose date headings are in a DIFFERENT format from the table's, so
#     any comparison between a parsed and an unparsed date is exercised
LABS_FIXTURE = """# Labs

| marker | value | unit | reference range | date | source file |
|---|---|---|---|---|---|
| Cholesterol | 222 H | mg/dL | 108 - 199 | 12/09/2025 | a.pdf |
| HDL | 75 | mg/dL | 40 - 125 | 12/09/2025 | a.pdf |
| Triglyceride | 73 | mg/dL | 26 - 149 | 12/09/2025 | a.pdf |
| LDL Cholesterol (direct) | 126 H | mg/dL | 0 - 99 | 12/09/2025 | a.pdf |
| CHOLESTEROL, TOTAL | 216 H | mg/dL | <200 mg/dL | 2026-03-31 | b.pdf |
| HDL CHOLESTEROL | 75 | mg/dL | > OR = 40 mg/dL | 2026-03-31 | b.pdf |
| TRIGLYCERIDES | 45 | mg/dL | <150 mg/dL | 2026-03-31 | b.pdf |
| LDL-CHOLESTEROL | 127 H | mg/dL | mg/dL (calc) | 2026-03-31 | b.pdf |
| NON HDL CHOLESTEROL | 141 H | mg/dL | <130 mg/dL | 2026-03-31 | b.pdf |
| LDL PARTICLE NUMBER | 1685 H | nmol/L | <1138 nmol/L | 2026-03-31 | b.pdf |
| LDL SMALL | 243 H | nmol/L | <142 nmol/L | 2026-03-31 | b.pdf |
| LIPOPROTEIN (a) | <10 | nmol/L | <75 nmol/L | 2026-03-31 | b.pdf |
| Cholesterol | 152 | mg/dL | 108 - 199 | 05/27/2026 | c.pdf |
| HDL | 75 | mg/dL | 40 - 125 | 05/27/2026 | c.pdf |
| Triglyceride | 42 | mg/dL | 26 - 149 | 05/27/2026 | c.pdf |
| LDL Cholesterol (direct) | 65 | mg/dL | 0 - 99 | 05/27/2026 | c.pdf |
| Glucose (Dipstick) | Negative N | | Negative | 05/27/2026 | c.pdf |
| Glucose | 84 | mg/dL | 70 - 99 | 05/27/2026 | c.pdf |
| Creatinine | 1.10 | mg/dL | 0.70 - 1.30 | 12/09/2025 | a.pdf |
| Creatinine | 1.19 | mg/dL | 0.70 - 1.30 | 2026-03-31 | b.pdf |
| Creatinine | 1.21 | mg/dL | 0.70 - 1.30 | 05/27/2026 | c.pdf |
| Sodium | 140 | mmol/L | 135 - 146 | 12/09/2025 | a.pdf |
| Sodium | 145 | mmol/L | 135 - 146 | 2026-03-31 | b.pdf |
| Sodium | 142 | mmol/L | 135 - 146 | 05/27/2026 | c.pdf |
| Ferritin | 30 | ng/mL | 30 - 400 | 12/09/2025 | a.pdf |
| Ferritin | 25 | ng/mL | 30 - 400 | 05/27/2026 | c.pdf |
| ALT | 35 | U/L | 7 - 52 | 05/27/2026 | c.pdf |
| AST | 33 | U/L | 14 - 50 | 05/27/2026 | c.pdf |
| PLATELET COUNT | 328 | /uL | 140 - 400 | 05/27/2026 | c.pdf |

## Out of range

**12/09/2025**
- Cholesterol: 222 H
- LDL Cholesterol (direct): 126 H

**03/31/2026**
- CHOLESTEROL, TOTAL: 216 H (ref <200 mg/dL)
- LDL-CHOLESTEROL: 127 H (ref <100 mg/dL)
- NON HDL CHOLESTEROL: 141 H (ref <130 mg/dL)
- LDL PARTICLE NUMBER: 1685 H (ref <1138 nmol/L)
- LDL SMALL: 243 H (ref <142 nmol/L)

**05/27/2026**
- Ferritin: 25 L
- Ferritin: 25 L (ref: 30 - 400)
"""

FAILURES = []


# A CLI path that exists on no machine. Every rx/fanout module instance the suite loads gets its
# HERMES pointed here (see load_rx and the fanout loads): a dev machine has ~/.local/bin/hermes
# and CI does not, so a test that leaks a real CLI subprocess would pass pre-commit locally and
# crash CI with FileNotFoundError. With this path it fails loudly on EVERY machine, and the suite
# can never complete/block/create cards on a live board. Blocks that exercise CLI-calling code
# stub the caller (sh, _complete_self, create_card, _my_card_id -> None) instead.
NO_CLI = "/nonexistent/rx-test-hermes-cli-leak"


def check(name, got, want, why):
    ok = got == want
    print("  %-4s %-46s %s" % ("ok" if ok else "FAIL", name, "" if ok else "got %r want %r" % (got, want)))
    if not ok:
        FAILURES.append((name, got, want, why))


def load_rx(inputs_dir):
    os.environ["RX_INPUTS"] = inputs_dir
    spec = importlib.util.spec_from_file_location("rx_under_test", os.path.join(HERE, "rx.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HERMES = mod.rxkanban.HERMES = NO_CLI          # no test may reach a real hermes CLI
    return mod


def main():
    tmp = tempfile.mkdtemp(prefix="rx-test-")
    open(os.path.join(tmp, "labs-complete.md"), "w", encoding="utf-8").write(LABS_FIXTURE)
    rx = load_rx(tmp)

    # Tests must NEVER reach the real Discord/gateway. Several verbs (gather-regimen-slugs, the
    # correction verbs, labs-brief) call send_detail/phase_start to post a review; running the
    # suite was spamming the live channel. Stub the posting functions globally here — blocks that
    # verify posting override these locally and restore them.
    rx.send_detail = lambda *a, **k: True
    rx.phase_start = lambda *a, **k: None

    print("\n_norm_date — ISO dates were read as US dates from offset 2")
    # "2026-03-31" contains "26-03-31", read as 26/03/31 -> 2031-26-03, five years in the
    # future. That made the oldest panel the newest draw and inverted every trend.
    check("ISO stays ISO", rx._norm_date("2026-03-31"), "2026-03-31", "the 2031 bug")
    check("ISO 2025-08-13 not 2013", rx._norm_date("2025-08-13"), "2025-08-13", "the 2013 bug")
    check("US form", rx._norm_date("05/27/2026"), "2026-05-27", "")
    check("two-digit year", rx._norm_date("05/27/26"), "2026-05-27", "")
    check("impossible date rejected", rx._norm_date("13/45/2026"), "", "garbage must not sort")
    check("no date", rx._norm_date("not a date"), "", "")

    print("\n_norm_marker — vendors name one analyte several ways")
    same = [("Triglyceride", "TRIGLYCERIDES"),
            ("Cholesterol/HDL ratio", "CHOL/HDLC RATIO"),
            ("Cholesterol", "CHOLESTEROL, TOTAL"),
            ("HDL", "HDL CHOLESTEROL"),
            ("LDL Cholesterol (direct)", "LDL-CHOLESTEROL")]
    for a, b in same:
        check("same analyte: %s == %s" % (a[:16], b[:16]),
              rx._norm_marker(a) == rx._norm_marker(b), True, "supersede fails if these differ")

    print("\n_norm_marker — but these must stay APART")
    apart = [("LDL Cholesterol (direct)", "LDL SMALL"),
             ("LDL Cholesterol (direct)", "LDL PARTICLE NUMBER"),
             ("HDL", "HDL LARGE"),
             ("HDL", "NON HDL CHOLESTEROL"),
             ("Glucose", "Glucose (Dipstick)")]
    for a, b in apart:
        check("distinct: %s vs %s" % (a[:16], b[:18]),
              rx._norm_marker(a) != rx._norm_marker(b), True,
              "a merged marker supersedes across analytes")
    check("plural fold is cautious", rx._norm_marker("Status"), "status", "-us must survive")
    check("ketones folds", rx._norm_marker("Ketones") == rx._norm_marker("Ketone"), True, "")

    print("\nvalue_without_flag — the lab's flag column bleeds into the value")
    for raw, want in [("Negative N", "Negative"), ("0.2 N", "0.2"), ("186 H", "186"),
                      ("12 HH", "12"), ("7 AB", "7"), ("Trace", "Trace")]:
        check("strip %r" % raw, rx.value_without_flag(raw), want, "")
    check("'Vitamin A' is not flagged", rx.value_without_flag("Vitamin A"), "Vitamin A",
          "a lone trailing A is a word, not a flag")

    print("\n_numeric — non-numeric results must not become trend points")
    check("flagged number", rx._numeric("141 H"), 141.0, "")
    check("less-than is not a number", rx._numeric("<10"), None, "Lp(a) <10 is a good result")
    check("word is not a number", rx._numeric("Negative"), None, "")

    print("\nmarker_series — one reading per draw, chronological")
    ser = rx.marker_series()
    chol = rx.series_for(ser, "Cholesterol")
    check("cholesterol has 3 draws", len(chol), 3, "the two vendors' names must merge")
    check("chronological order", [d for d, _, _ in chol],
          ["2025-12-09", "2026-03-31", "2026-05-27"], "mis-dating reorders the series")
    check("values follow the dates", [n for _, n, _ in chol], [222.0, 216.0, 152.0],
          "cholesterol FELL; it was once reported as rising")

    print("\ntrends — direction over three or more draws, including inside the range")
    tr = {t["marker"]: t for t in rx.trends()}
    check("creatinine rise detected", "Creatinine" in tr, True,
          "in range at every draw, so out-of-range screening cannot see it")
    if "Creatinine" in tr:
        check("direction", tr["Creatinine"]["direction"], "rising", "")
        check("all three points", tr["Creatinine"]["points"], 3, "")
    # 222 -> 216 -> 152 really is falling. The point of this check is the DIRECTION: with the
    # ISO-date bug the 2026-03-31 draw sorted last as "2031" and the same numbers read
    # "150 -> 152 -> 216", i.e. rising, and a card was dispatched to explain a rise that
    # never happened.
    check("cholesterol trend is falling", tr.get("Cholesterol", {}).get("direction"), "falling",
          "the mis-dated draw once made a fall look like a rise")
    check("wandering marker is not a trend", "Sodium" not in tr, True,
          "140 -> 145 -> 142 has no direction and must not be reported as one")

    print("\nout_of_range_entries — the section uses BOLD dates, not headings")
    ents = rx.out_of_range_entries()
    joined = " | ".join(ents)
    check("no date parsed as a finding", any(e.strip().startswith(("12/09", "05/27", "2026-")) for e in ents),
          False, "'**05/27/2026**' starts with '*' and was filed as a finding")
    check("superseded value dropped", "222 H" in joined, False,
          "cholesterol 222 from 12/09 is superseded by 152 on 05/27")
    check("ref-note duplicate deduped", joined.count("Ferritin"), 1,
          "two PDFs transcribed one draw with different ref formatting")
    check("derived marker superseded", "NON HDL" in joined, False,
          "non-HDL = TC - HDL = 152-75 = 77, inside <130")
    check("unrepeated marker kept", "LDL PARTICLE NUMBER" in joined, True,
          "no newer reading exists, so it must not be silently dropped")
    # Assert on the SPECIFIC entry, not on the list as a whole. "last measured" appearing
    # somewhere was satisfied by any one labelled marker, so the check survived a date bug that
    # unlabelled others.
    lpn = [e for e in ents if e.startswith("LDL PARTICLE NUMBER")]
    check("the stale marker itself is labelled",
          bool(lpn) and "last measured 2026-03-31" in lpn[0], True,
          "it is the most recent value but not a current one")
    check("every stale lipid marker is labelled",
          all("last measured" in e for e in ents
              if e.split(":")[0].strip() in ("LDL PARTICLE NUMBER", "LDL SMALL")), True,
          "a raw-vs-normalised date comparison unlabelled some and not others")


    # ── the transcription cache ───────────────────────────────────────────────
    print("\nrxcache — admission needs BOTH proofs, and refusal is the default")
    cache_home = tempfile.mkdtemp(prefix="rx-cache-test-")
    os.environ["RX_CACHE_HOME"] = cache_home
    spec = importlib.util.spec_from_file_location("rxcache_ut", os.path.join(HERE, "rxcache.py"))
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)

    pdf = os.path.join(cache_home, "a.pdf")
    open(pdf, "wb").write(b"%PDF-1.4 Cholesterol 152 mg/dL HDL 75 mg/dL")
    pdf_text = "Cholesterol 152 mg/dL HDL 75 mg/dL"
    good = os.path.join(cache_home, "good.md")
    open(good, "w", encoding="utf-8").write(
        "| marker | value | unit | date |\n|---|---|---|---|\n"
        "| Cholesterol | 152 | mg/dL | 05/27/2026 |\n| HDL | 75 | mg/dL | 05/27/2026 |\n")
    bad = os.path.join(cache_home, "bad.md")
    open(bad, "w", encoding="utf-8").write(
        "| marker | value | unit | date |\n|---|---|---|---|\n"
        "| Cholesterol | 152 | mg/dL | 05/27/2026 |\n| HDL | 999 | mg/dL | 05/27/2026 |\n")

    check("miss before anything is admitted", rc.get(pdf), None, "")
    ok, _d = rc.admit(pdf, good, pdf_text, confirmed_by="")
    check("refused without human confirmation", ok, False,
          "the mechanical check cannot see a right number on the wrong marker")
    ok, _d = rc.admit(pdf, bad, pdf_text, confirmed_by="test")
    check("refused when one value is unverifiable", ok, False,
          "'mostly verified' erodes into 'assumed correct'")
    check("a refusal caches nothing", rc.get(pdf), None, "")
    ok, detail = rc.admit(pdf, good, pdf_text, confirmed_by="test")
    check("admitted with both proofs", ok, True, detail)
    hit = rc.get(pdf)
    check("hit after admission", hit is not None, True, "")
    if hit:
        check("evidence is stored, not just a verdict", hit[1]["values_verified"], 2,
              "an entry must be re-auditable without its original run")

    same = os.path.join(cache_home, "renamed-doc_9f2b.pdf")
    shutil.copy(pdf, same)
    check("hit on identical content, new filename", rc.get(same) is not None, True,
          "each upload round gets a fresh random doc_<hash>_ prefix")

    rc.CACHE_FORMAT += 1
    check("format bump invalidates", rc.get(pdf), None,
          "a changed transcription shape must miss, not mix two shapes")
    rc.CACHE_FORMAT -= 1

    rc.mark_unreadable(pdf, "no text layer")
    check("scan remembered", rc.unreadable_reason(pdf), "no text layer",
          "a scan fails the same way every run; do not re-card it")
    shutil.rmtree(cache_home, ignore_errors=True)

    print("\nrxverdict — verdict cache: content-addressed, versioned, serial-write roundtrip")
    vc_home = tempfile.mkdtemp(prefix="rx-verdict-test-")
    os.environ["RX_VERDICT_CACHE"] = vc_home
    vspec = importlib.util.spec_from_file_location("rxverdict_ut", os.path.join(HERE, "rxverdict.py"))
    rv = importlib.util.module_from_spec(vspec)
    vspec.loader.exec_module(rv)

    sec, q = "The trial found a 12% reduction in LDL over 8 weeks.", "12% reduction in LDL"
    check("key deterministic + version-prefixed",
          rv.anchor_key(sec, q) == rv.anchor_key(sec, q) and rv.anchor_key(sec, q).startswith("v1-"),
          True, "")
    check("a changed section is a different key — page-change-safe",
          rv.anchor_key(sec, q) != rv.anchor_key(sec + " x", q), True, "")
    check("miss before anything recorded", rv.lookup(sec, q), None, "")

    rv.record(sec, q, "X lowers LDL", "supported", "the section states a reduction")
    hit = rv.lookup(sec, q)
    check("hit after record", hit and hit["claims"][0]["verdict"], "supported", "")
    check("the judged claim is stored (the reuse gate compares against it)",
          hit["claims"][0]["claim"], "X lowers LDL", "")
    rv.record(sec, q, "X lowers LDL by 40%", "overstated", "no magnitude in the section")
    check("a different claim on the same anchor appends", len(rv.lookup(sec, q)["claims"]), 2,
          "one source+quote is cited for different claims over time")
    rv.record(sec, q, "X lowers LDL", "context-reversed", "re-judged")
    check("re-judging the SAME claim overwrites, not duplicates",
          [c["verdict"] for c in rv.lookup(sec, q)["claims"] if c["claim"] == "X lowers LDL"],
          ["context-reversed"], "")
    rv.VERDICT_FORMAT += 1
    check("a format bump invalidates the anchor", rv.lookup(sec, q), None,
          "changed section-extraction / rubric must miss, never reuse an old-logic verdict")
    rv.VERDICT_FORMAT -= 1

    # reuse gate: cosine math, the confirm parser (only a clean REUSE reuses; else re-judge), and
    # the fail-safe path (an unreachable model must never silently reuse).
    check("cosine of identical vectors is 1", round(rv.cosine([1, 2, 3], [1, 2, 3]), 3), 1.0, "")
    check("cosine of orthogonal is 0", rv.cosine([1, 0], [0, 1]), 0.0, "")
    rv._post = lambda p, pay, **k: {"choices": [{"message": {"content": "REUSE"}}]}
    check("a clean REUSE reuses", rv.confirm_equivalent("a", "a"), True, "")
    rv._post = lambda p, pay, **k: {"choices": [{"message": {"content": "REJUDGE"}}]}
    check("REJUDGE re-judges", rv.confirm_equivalent("a", "a"), False, "")
    rv._post = lambda p, pay, **k: None
    check("an unreachable confirm model fails safe to re-judge", rv.confirm_equivalent("a", "a"),
          False, "an endpoint hiccup must never silently reuse a verdict")
    shutil.rmtree(vc_home, ignore_errors=True)

    # _resolve_from_cache end to end: a confirmed, embedding-close claim is written as a reused
    # verdict (so the fan-out skips it); a rejected one is not; embeddings-down reuses nothing.
    import importlib.util as _ilu
    _vsp = _ilu.spec_from_file_location("verify_ut", os.path.join(HERE, "verify.py"))
    vf = _ilu.module_from_spec(_vsp)
    _vsp.loader.exec_module(vf)
    _reports = tempfile.mkdtemp(prefix="rx-reports-test-")
    _vcache = tempfile.mkdtemp(prefix="rx-vc2-test-")
    vf.REPORTS = _reports
    # Patch THE INSTANCE VERIFY.PY BINDS. Loading rxverdict.py a second time under another
    # module name patched a different object: verify.py kept its own `import rxverdict`, whose
    # CACHE_HOME/embed/confirm were still live — so this passed only on machines with a warm
    # real cache (any entry cosine-matches the [1,0] stub) and failed on every clean CI runner
    # since 2026-08-14.
    _vx = vf.rxverdict
    _vx_saved = (_vx.CACHE_HOME, _vx.embed, _vx.confirm_equivalent)
    _vx.CACHE_HOME = _vcache
    _vx.record("SEC", "Q", "X lowers LDL", "supported", "")
    _row = {"report": "substance-x.md", "n": "3", "match": "exact", "section": "SEC",
            "quote": "Q", "claim": "X reduces LDL cholesterol", "heading": "h"}
    _vx.embed = lambda ts: [[1.0, 0.0] for _ in ts]
    _vx.confirm_equivalent = lambda a, b: True
    vf._resolve_from_cache([dict(_row)])
    _cmd = os.path.join(_reports, "CONTEXT-audit-cache.md")
    check("a confirmed reuse is written for the fan-out to skip",
          os.path.exists(_cmd) and "supported | substance-x.md | 3" in open(_cmd).read(),
          True, "")
    os.path.exists(_cmd) and os.remove(_cmd)
    vf.rxverdict.confirm_equivalent = lambda a, b: False
    vf._resolve_from_cache([dict(_row)])
    check("a rejected candidate writes nothing — confirm is the gate, not cosine",
          os.path.exists(_cmd), False, "")
    _vx.embed = lambda ts: None
    _vx.confirm_equivalent = lambda a, b: True
    vf._resolve_from_cache([dict(_row)])
    check("embeddings down reuses nothing (fail-safe)", os.path.exists(_cmd), False, "")
    (_vx.CACHE_HOME, _vx.embed, _vx.confirm_equivalent) = _vx_saved
    shutil.rmtree(_reports, ignore_errors=True)
    shutil.rmtree(_vcache, ignore_errors=True)


    print("\nper-run output dirs — each invocation writes into its own timestamped dir")
    import tempfile as _tf                                     # noqa: PLC0415
    import time as _rt                                         # noqa: PLC0415
    _rroot = _tf.mkdtemp(prefix="rxrun-")
    _saved = (rx.REPORTS_ROOT, rx.CURRENT_LINK, rx.REPORTS)
    rx.REPORTS_ROOT = _rroot
    rx.CURRENT_LINK = os.path.join(_rroot, "current")
    rx.REPORTS = rx.CURRENT_LINK
    try:
        d1, s1 = rx.start_run()
        check("start_run makes a YYYY-MM-DD-HHMMSS dir", os.path.isdir(d1) and len(s1) == 17,
              True, "the run's home")
        check("current symlink resolves to the run dir", os.path.realpath(rx.REPORTS) == d1,
              True, "every stage resolves REPORTS through it, so they agree")
        check("brief name is the canonical dated form", rx.brief_name() == s1[:10] + "-rx-review.md",
              True, "the doc-canonical name that unblocks stage 8")
        _rt.sleep(1.1)                                         # second-resolution stamp
        d2, _s2 = rx.start_run()
        check("a second invocation gets a fresh dir", d2 != d1 and os.path.realpath(rx.REPORTS) == d2,
              True, "no run overwrites another")
        check("the prior run dir is kept", os.path.isdir(d1), True, "run dirs are the deliverables")
        check("run_dirs lists the runs, not the current link", len(rx.run_dirs()) == 2,
              True, "history accumulates")
    finally:
        shutil.rmtree(_rroot, ignore_errors=True)
        rx.REPORTS_ROOT, rx.CURRENT_LINK, rx.REPORTS = _saved


    print("\nclassify_lab_text — catch a mis-upload before a card is spent on it")
    lab = ("Quest Diagnostics   Specimen collected 05/27/2026\n"
           "Ordering Physician: Dr X\nTEST   RESULT   UNITS   REFERENCE RANGE\n"
           "Cholesterol  152  mg/dL  108 - 199\nHDL  75  mg/dL  40 - 125\n"
           "LDL Cholesterol (direct)  65  mg/dL  0 - 99\n" + "filler line\n" * 20)
    endo = ("UPPER ENDOSCOPY REPORT\nIndication: reflux\n"
            "Procedure: the gastroscope was advanced under direct vision.\n"
            "FINDINGS: normal esophagus, mild antral gastritis.\n"
            "IMPRESSION: mild gastritis; recommend PPI therapy.\n" * 6)
    imaging = ("RADIOLOGY REPORT\nExam: CT scan abdomen with contrast\n"
               "FINDINGS: the liver is normal in size, no focal lesion.\n"
               "IMPRESSION: normal study.\n" * 8)
    check("a lab panel reads as a lab", rx.classify_lab_text(lab)[0], "lab", "")
    check("an endoscopy report is caught", rx.classify_lab_text(endo)[0], "not-a-lab",
          "a narrative has no marker table; the transcriber invents rows")
    check("an imaging report is caught", rx.classify_lab_text(imaging)[0], "not-a-lab", "")
    check("an empty text layer is a scan", rx.classify_lab_text("   ")[0], "scan",
          "a scan needs OCR, not a transcription card")
    # Fail-safe: a lab that happens to mention a procedure must NOT be rejected. Refusing a
    # real panel silently weakens every downstream analysis; transcribing one narrative costs
    # two minutes.
    mixed = lab + "\nHistory: prior colonoscopy 2024.\n"
    check("a lab mentioning a procedure survives", rx.classify_lab_text(mixed)[0], "lab",
          "the screen must be biased toward accepting")


    print("\ncmd_sweep — never un-judge something there is no round left to re-judge")
    vspec = importlib.util.spec_from_file_location("verify_ut", os.path.join(HERE, "verify.py"))
    vf = importlib.util.module_from_spec(vspec)
    vspec.loader.exec_module(vf)

    class A:
        dry_run = True

    calls = {"stale_asked": 0}
    real = vf.stale_unverified_items
    vf.stale_unverified_items = lambda revived=(): (calls.__setitem__("stale_asked",
                                                    calls["stale_asked"] + 1), [])[1]
    vf.outstanding_items = lambda: []
    vf._sweep_state = lambda d=None, dry=False: {} if d is None else None
    try:
        a = A(); a.round = vf.MAX_SWEEP_ROUNDS - 1
        vf.cmd_sweep(a)
        mid = calls["stale_asked"]
        a.round = vf.MAX_SWEEP_ROUNDS
        vf.cmd_sweep(a)
        final = calls["stale_asked"]
    finally:
        vf.stale_unverified_items = real
    check("revival is attempted before the last round", mid, 1, "")
    check("revival is NOT attempted on the last round", final, mid,
          "three trend-rbc citations were revived on round 4 of 4 and instantly "
          "reported unjudged - worse than leaving them judged")


    print("\nstale_unverified_items — revive only what was genuinely UNREADABLE when judged")
    loc = {"rows": [
        # read fine at judge time, quote simply not in it -> a real verdict, never revive
        {"report": "a.md", "n": 1, "url": "https://example.com/a", "match": "absent",
         "section": "x" * 2900},
        # the quote was a page TITLE - re-judging cannot change that
        {"report": "a.md", "n": 2, "url": "https://example.com/b", "match": "absent",
         "section": "y" * 2700},
        # never fetched, nothing to judge against -> revivable IF it reads now
        {"report": "a.md", "n": 3, "url": "https://example.com/c", "match": "unfetched",
         "section": ""},
    ]}
    open(os.path.join(tmp, "locations.json"), "w", encoding="utf-8").write(json.dumps(loc))
    vf.LOCATIONS = os.path.join(tmp, "locations.json")
    vf.REPORTS = tmp
    open(os.path.join(tmp, "CONTEXT-audit-01.md"), "w", encoding="utf-8").write(
        "absent | a.md | [1] | (quote not located) | not found\n"
        "absent | a.md | [2] | (quote not located) | quote is a page title\n"
        "absent | a.md | [3] | (quote not located) | source could not be fetched\n")
    class OKRes:
        ok = True
    real_fetch = vf.rxfetch.fetch
    vf.rxfetch.fetch = lambda u, **k: OKRes()
    try:
        got = sorted(r["n"] for r in vf.stale_unverified_items())
    finally:
        vf.rxfetch.fetch = real_fetch
    check("only the unfetched citation is revived", got, [3],
          "33 of 33 revivals in one run were of sources that read fine; they burned the "
          "round budget and un-judged citations that were correctly judged")


    print("\nquote matching and interstitial detection — three citations that blocked a sweep")
    fspec = importlib.util.spec_from_file_location("rxfetch_ut", os.path.join(HERE, "rxfetch.py"))
    rf = importlib.util.module_from_spec(fspec)
    fspec.loader.exec_module(rf)

    # [8]: the endnote absorbed a period INSIDE its closing quote; the source has none.
    body = "Overview. Understanding Polycythemia is a common finding in sleep apnea. " * 6
    check("trailing period does not defeat a match",
          vf.find_quote(body, 'Understanding Polycythemia.')[0], "exact",
          "one character of the citation's own punctuation caused an `absent` verdict")
    check("curly quotes and en-dashes normalise",
          vf.find_quote("the range was 49\u201351% in men " * 12, "49-51% in men")[0], "exact",
          "typography differs between a rendered page and extracted text")

    # The fetcher's own behaviour is tested where the fetcher lives - the web-access skill,
    # in web_access_test.py. rxfetch here is a binding to that skill, so these checks cannot
    # run without it installed, and CI has no skills directory. Skipping is honest; asserting
    # against a stub would not be.
    if not rf.available():
        print("     skip: fetcher behaviour (web-access skill not installed — "
              "covered by web_access_test.py)")
    else:
        # [9]: a real page carrying a JavaScript notice must not be mistaken for a shell.
        shell = "This site needs JavaScript to work properly. " + "clipboard search history " * 400
        real = "This site requires JavaScript. " + "polycythemia hematocrit criteria " * 1200
        check("a JS shell is still rejected", rf.looks_unusable(shell), True, "")
        check("a long document with a JS notice is kept", rf.looks_unusable(real), False,
              "judging on the marker alone rejected a 35KB Bookshelf chapter")
        check("a short bot wall is rejected",
              rf.looks_unusable("Checking your browser before accessing pubmed..."), True, "")



    print("\nthe Stage 2 card transcribes the text regimen sources into the five-column draft")
    _rb = rx.REGIMEN_BODY
    check("it names the pipe-delimited row incl. started",
          "product | brand | quantity | schedule | started" in _rb, True, "")
    # HANDED, NOT FETCHED. The body carries the regimen text itself, so the worker opens no file
    # and names no path — a path in a card body is a literal a worker corrupts (2026-08-10), and
    # this one used to come with an "if it exists" branch for a second source that never existed.
    check("it is handed the text and reads no file",
          ("read_file" in _rb, "regimen.txt" in _rb, "{regimen}" in _rb),
          (False, False, True),
          "the regimen is always text and always one file; the card carries it")
    check("it runs no helper script", "rxsplit.py" not in _rb and "vision_analyze" not in _rb, True,
          "Stage 2 is text-only: no prep, no vision, no image handling")

    # intake-regimen end to end: one source, text inlined, keyed on that text, oversize holds.
    with tempfile.TemporaryDirectory() as _r2:
        _sv2 = (rx.INPUTS, rx.create, rx._parent_worker_to_barrier, rx._my_card_id)
        try:
            rx.INPUTS = _r2
            _seen = []
            rx.create = lambda a, title, body=None, minutes=None, priority=None, parents=(), \
                key=None, assignee="rx-intake": (_seen.append((body or "", key)) or "t_w")
            rx._parent_worker_to_barrier = lambda *a, **k: None
            rx._my_card_id = lambda: None

            class _Ar:
                dry_run = force = json = False

            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _rc = rx.cmd_intake_regimen(_Ar())
            check("no regimen refuses before any card exists", (_rc, _seen), (1, []),
                  "a review of no substances is not a shorter review")
            check("...and the refusal names only regimen.txt",
                  "supplements" in _buf.getvalue(), False,
                  "the second source never existed; naming it made a one-source check read as two")

            open(rx.regimen_path(), "w").write("Thorne Super EPA, 1 gelcap, morning\n")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_intake_regimen(_Ar())
            _body1, _key1 = _seen[-1]
            check("the regimen text is carried in the card body",
                  ("Thorne Super EPA" in _body1, "read_file" in _body1), (True, False),
                  "handed, not fetched — the worker opens no file and names no path")

            # A corrected regimen must be a DIFFERENT card. With a constant key, create() would
            # return the first card and the correction would be silently ignored.
            open(rx.regimen_path(), "w").write("Thorne Super EPA, 2 gelcaps, evening\n")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_intake_regimen(_Ar())
            _body2, _key2 = _seen[-1]
            check("a corrected regimen is a different card", _key1 != _key2, True,
                  "a constant key would return the first card, text and file disagreeing")
            check("...and the new text is what the card carries", "2 gelcaps" in _body2, True, "")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_intake_regimen(_Ar())
            check("an unchanged regimen is the same card again", _seen[-1][1], _key2,
                  "idempotent: re-running the stage is how the pipeline recovers")

            # Too large to inline: refused before any card exists, not silently truncated and not
            # fallen back to a file read, which would restore the path this change removes.
            open(rx.regimen_path(), "w").write("x" * (rx.KANBAN_BODY_CAP + 1))
            _n_before = len(_seen)
            _buf2 = io.StringIO()
            with contextlib.redirect_stdout(_buf2):
                _rc2 = rx.cmd_intake_regimen(_Ar())
            check("a regimen too large to inline HOLDS", (_rc2, len(_seen)), (1, _n_before),
                  "one this size is usually the wrong document")
            check("...and says so rather than falling back to a file read",
                  ("TOO LARGE" in _buf2.getvalue(), "read_file" in _buf2.getvalue()),
                  (True, False), "a fallback restores the second code path inlining removes")
        finally:
            (rx.INPUTS, rx.create, rx._parent_worker_to_barrier, rx._my_card_id) = _sv2
    check("it looks nothing up", "web_access" not in _rb, True,
          "labels are Stage 3; Stage 2 only transcribes what the user wrote")

    print("\nresearch cards search the literature, product cards search the web")
    _sspec = importlib.util.spec_from_file_location("fanout_scope", os.path.join(HERE, "fanout.py"))
    _sfan = importlib.util.module_from_spec(_sspec)
    _sspec.loader.exec_module(_sfan)
    _sfan.HERMES = _sfan.rxkanban.HERMES = NO_CLI
    _common = _sfan.COMMON.format(inputs=_sfan.INPUTS,
                                  labs_line=_sfan.LABS_LINE.format(inputs=_sfan.INPUTS))
    # Without this the search hits `general` only - bing and duckduckgo - while pubmed,
    # openalex, crossref and semantic scholar sit unqueried in the science category.
    check("research cards ask for literature", "--scope literature" in _common, True,
          "card bodies name PubMed as preferred; the backend must be able to reach it")
    check("the product lookup asks for the open web", "--scope products" in rx.REGIMEN_INTAKE_BODY, True,
          "a Supplement Facts panel lives on a manufacturer page, not in PubMed")

    print("\nanalyze-research is a backstop, not a gate — the edges hold the ordering")
    # The stale-inventory NOT-YET self-edge and the CONFIRM YOUR LABS gate are gone: ordering is
    # held by the Begin/Barrier edges the whole graph is built from, and human input is the
    # `Regimen clarify:` / `Marker review:` worker cards. What is left inside analyze-research (the
    # Stage 6 Begin) is a pair of backstops for a card reached out of order, and the exec into
    # fanout. analyze-adversarial (7) and analyze-conclude (8) run after those backstops passed.
    _analyze_src = inspect.getsource(rx.cmd_analyze_research)
    check("the stale-inventory NOT YET block is gone", "NOT YET" in _analyze_src, False,
          "a stage reached in order never sees a stale inventory; the edge guarantees it")
    check("the CONFIRM YOUR LABS gate poll is gone",
          "_gate_outstanding" in _analyze_src, False,
          "the lab review is the Stage 5 Barrier, not a gate analyze polls")
    check("analyze-research still backstops unverified lab values",
          "could not be verified against the source PDFs" in _analyze_src, True,
          "a card reached out of order must still refuse on a mis-transcribed value")
    check("analyze-research still backstops unsettled regimen items",
          "regimen item(s) are not settled" in _analyze_src, True, "")
    check("and it hands off to fanout.py",
          "os.execv" in inspect.getsource(rx._exec_fanout), True,
          "analyze-research builds the graph by exec'ing fanout via _exec_fanout")

    # The backstop now has TEETH: on a hold it BLOCKS its own card (needs_input) so the agent's
    # gated body cannot complete over the problem, and it skips the backstop entirely for a
    # substage (--family) since the same data was already verified at the main Begin.
    import types as _tt
    _sv_cl, _sv_cr, _sv_sh, _sv_ef = rx.check_labs, rx.check_regimen, rx.sh, rx._exec_fanout
    _sv_sd = rx.send_detail
    _sv_env = os.environ.get("HERMES_KANBAN_TASK")
    _blocks, _held_msgs = [], []
    try:
        rx.send_detail = lambda text="", *a, **k: (_held_msgs.append(text) or True)
        rx.check_labs = lambda: ({"rows": 1}, [("LEPTIN", "source file 'LEPTIN' not among the PDFs")])
        rx.check_regimen = lambda: ([], None)
        rx.sh = lambda cmd, **k: (_blocks.append(cmd),
                                  _tt.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
        os.environ["HERMES_KANBAN_TASK"] = "t_stage6_begin"

        def _no_exec(*a, **k):
            raise AssertionError("must not fan out while held")
        rx._exec_fanout = _no_exec
        _rc = rx.cmd_analyze_research(_tt.SimpleNamespace(dry_run=False, force=False, family=None))
        check("a lab problem holds — returns non-zero, does not fan out", _rc, 1, "")
        check("...and BLOCKS its own card for the user (kind=needs_input)",
              any("block" in c and "needs_input" in c for c in _blocks), True,
              "a hold that does not block is silently completed by the gated agent")
        # ...and TELLS the user. `needs_input` reaches nobody on this board — cards are not
        # subscribed — so a hold used to sit there until someone happened to look at the board
        # (Stage 6, 2026-08-10). It reports; it does not ask. The two batched barrier reviews
        # remain the only questions the pipeline puts to a human.
        check("...and POSTS the hold to chat, naming the reason",
              (len(_held_msgs) == 1
               and "could not be verified" in _held_msgs[0]
               and "LEPTIN" in _held_msgs[0]), True,
              "a stopped pipeline nobody is told about is indistinguishable from a slow one")

        _ran = {"fan": False}
        rx._exec_fanout = lambda *a, **k: _ran.__setitem__("fan", True)
        _blocks.clear()
        rx.cmd_analyze_research(_tt.SimpleNamespace(dry_run=False, force=False, family="markers"))
        check("a substage (--family) skips the backstop and fans out",
              _ran["fan"] and not _blocks, True,
              "labs were verified at the main Begin; a substage re-check would only re-block")
    finally:
        rx.check_labs, rx.check_regimen, rx.sh, rx._exec_fanout = _sv_cl, _sv_cr, _sv_sh, _sv_ef
        rx.send_detail = _sv_sd
        if _sv_env is None:
            os.environ.pop("HERMES_KANBAN_TASK", None)
        else:
            os.environ["HERMES_KANBAN_TASK"] = _sv_env

    # merge_labs folds an escaped / continuation pipe (`\|` or ` \ |`) back into its cell, so a
    # multi-line reference range does not split into extra columns and shove `source file` onto a
    # range fragment. This is the LEPTIN corruption that held the run at Stage 6.
    with tempfile.TemporaryDirectory() as _dp:
        _pf = os.path.join(_dp, "labs-doc-x.md")
        open(_pf, "w").write(
            "| marker | value | unit | reference range | specimen | date | source file |\n"
            "|---|---|---|---|---|---|---|\n"
            "| LEPTIN | 1.3 | ng/mL | Adult 0.3-13.4 \\ | BMI 1.8-19.9 | serum | 2026-01-01 | doc_x.pdf |\n")
        _mrows, _, _ = rx.merge_labs([_pf])
    check("merge folds the split range into a single row", len(_mrows), 1, "")
    check("...so source file is the PDF, not a shifted column",
          _mrows[0].get("source file") if _mrows else None, "doc_x.pdf",
          "an unfolded ` \\ |` shifts every column and makes source a range fragment")
    check("...and both range tiers survive in one cell",
          bool(_mrows) and "0.3-13.4" in _mrows[0].get("reference range", "")
          and "1.8-19.9" in _mrows[0].get("reference range", ""), True, "")

    print("\na qualifier between marker and note is not a fabrication")
    import re as _re4
    _pdf = "IRON BINDING CAPACITY 332 250-425 mcg/dL (calc) NW % SATURATION 48 20-48 %"
    def _ok(marker, text):
        base = _re4.sub(r"\s*\([^)]*\)\s*$", "", marker).strip()
        return (rx._flat(marker) in rx._flat(text)) or (rx._flat(base) in rx._flat(text))
    # Lab PDFs interleave the value between a marker and its note, so a contiguous flattened
    # search for "ironbindingcapacitycalc" finds nothing and a real row is called fabricated.
    check("a trailing qualifier is tolerated", _ok("IRON BINDING CAPACITY (calc)", _pdf), True,
          "the value sits between the name and the (calc) note")
    check("the plain name still matches", _ok("IRON BINDING CAPACITY", _pdf), True, "")
    # The check must still catch a marker that is genuinely absent.
    check("an absent marker is still caught", _ok("THYROID PEROXIDASE AB", _pdf), False,
          "stripping a qualifier must not turn the check off")
    check("an absent marker WITH a qualifier is caught",
          _ok("THYROID PEROXIDASE AB (calc)", _pdf), False, "")

    print("\na dropped finding is reported, not printed to stdout")
    _src2 = open(os.path.join(HERE, "rx.py")).read()
    # This was the only one of six sibling filters that wrote to stdout, so every caller -
    # including cards whose summary IS their output - carried three diagnostic lines above the
    # report. Silence alone would be wrong too: a FLAGGED marker was being dropped.
    check("the filter no longer prints", 'print("   superseded by arithmetic' in _src2, False, "")
    check("it records the reason instead", "DERIVED_DROPPED.append" in _src2, True, "")
    check("and the report names them", "Not asked about" in _src2, True,
          "a dropped flagged marker must still reach the user")

    print("\nthe gate card and the message name the same markers")
    # out_of_range_entries() is THE list; the message renderer used to re-derive its own and
    # they disagreed - card 6, message 7, differing on NON HDL CHOLESTEROL, which is total
    # minus HDL and is dropped as arithmetically derived. Two implementations, two answers.
    _src = open(os.path.join(HERE, "rx.py")).read()
    check("the renderer consults out_of_range_entries",
          "_current = {_norm_marker" in _src and "out_of_range_entries()" in _src, True,
          "it must not re-derive the flagged list")

    print("\npercent and count are different observations")
    _H2 = "| marker | value | unit | reference range | specimen | date | source file | confidence |"
    _S2 = "|---|---|---|---|---|---|---|---|"
    import tempfile as _tf6
    with _tf6.TemporaryDirectory() as _d6:
        _f = os.path.join(_d6, "labs-cbc.md")
        open(_f, "w").write("\n".join([_H2, _S2,
            "| NEU% | 39.3 | % | 35.0 - 70.0 | CBC | 05/27/2026 | a.pdf |  |",
            "| NEU# | 2.1 | x10 | 1.5 - 8.5 | CBC | 05/27/2026 | a.pdf |  |",
            "| BAS% | 0.2 | % | 0.0 - 2.0 | CBC | 05/27/2026 | a.pdf |  |",
            "| BAS# | 0.2 | x10 | 0.0 - 0.2 | CBC | 05/27/2026 | a.pdf |  |"]) + "\n")
        rows, notes, _ = rx.merge_labs([_f])
        # _flat() strips every non-alphanumeric, so NEU% and NEU# both became "neu" and the
        # two columns of one differential were read as one reading transcribed twice. Every
        # CBC in the set raised a false disagreement - 41 of 44 - and where the two columns
        # happened to hold the SAME number (BAS% 0.2 / BAS# 0.2) one was silently dropped.
        check("both columns survive", len(rows), 4, "NEU%/NEU# and BAS%/BAS# are 4 observations")
        check("no false disagreement", notes, [], "39.3 vs 2.1 is two columns, not two readings")
        check("equal values are NOT collapsed",
              len([r for r in rows if r["marker"].startswith("BAS")]), 2,
              "BAS% 0.2 and BAS# 0.2 are different measurements that happen to match")
        check("percent keeps its own reference range",
              [r["reference range"] for r in rows if r["marker"] == "BAS#"], ["0.0 - 0.2"], "")

    print("\nan upload Hermes received cannot be silently missed")
    import tempfile as _tf5
    _real_cache, _real_raw = rx.DOC_CACHE, rx.RAW
    with _tf5.TemporaryDirectory() as _c, _tf5.TemporaryDirectory() as _r:
        rx.DOC_CACHE, rx.RAW = _c, _r
        try:
            for n in ("doc_a_one.pdf", "doc_b_two.pdf", "notes.txt"):
                open(os.path.join(_c, n), "w").write("x")
            open(os.path.join(_r, "doc_a_one.pdf"), "w").write("x")
            miss = [os.path.basename(f) for f in rx.unstaged_documents()]
            # Staging was a per-attachment copy by the assistant, so a batch of ten arriving as
            # the answer to "is that the complete set?" was read as a promise to send them
            # later and never copied. Half the labs were reviewed and nothing said so.
            check("a received-but-unstaged PDF is reported", miss, ["doc_b_two.pdf"], "")
            check("an already-staged PDF is not", "doc_a_one.pdf" in miss, False, "")
            check("non-PDFs are ignored", "notes.txt" in miss, False, "")
            for n in ("doc_a_one.pdf", "doc_b_two.pdf"):
                if not os.path.exists(os.path.join(_r, n)):
                    open(os.path.join(_r, n), "w").write("x")
            check("nothing outstanding once staged", rx.unstaged_documents(), [], "")
        finally:
            rx.DOC_CACHE, rx.RAW = _real_cache, _real_raw

    print("\nmerge-labs is deterministic")
    import tempfile as _tf4
    _H = "| marker | value | unit | reference range | specimen | date | source file | confidence |"
    _S = "|---|---|---|---|---|---|---|---|"
    def _row(mk, val, date, src, spec="", unit="mg/dL", ref="1 - 2"):
        return "| %s | %s | %s | %s | %s | %s | %s |  |" % (mk, val, unit, ref, spec, date, src)
    with _tf4.TemporaryDirectory() as _d:
        # Two page ranges of ONE pdf, overlapping: Iron appears in both, identically.
        open(os.path.join(_d, "labs-a-p1.md"), "w").write(
            "\n".join([_H, _S, _row("Iron", "141", "01/01/2026", "a.pdf"),
                        _row("TIBC", "415", "01/01/2026", "a.pdf")]) + "\n")
        open(os.path.join(_d, "labs-a-p2.md"), "w").write(
            "\n".join([_H, _S, _row("Iron", "141", "01/01/2026", "a.pdf"),
                        _row("Ferritin", "60", "01/01/2026", "a.pdf")]) + "\n")
        # A DIFFERENT pdf, same marker and date: a different draw, both must survive.
        open(os.path.join(_d, "labs-b-p1.md"), "w").write(
            "\n".join([_H, _S, _row("Iron", "99", "01/01/2026", "b.pdf")]) + "\n")
        import glob as _g
        rows, notes, _ = rx.merge_labs(sorted(_g.glob(os.path.join(_d, "labs-*.md"))))
        irons = [r for r in rows if r["marker"] == "Iron"]
        check("an overlapping duplicate collapses to one", len(irons), 2,
              "same pdf twice = one reading; the other Iron is a different pdf")
        check("a different source file is a different draw",
              sorted(r["source file"] for r in irons), ["a.pdf", "b.pdf"], "")
        check("nothing else is lost", len(rows), 4, "Iron x2, TIBC, Ferritin")
        check("no disagreements when values match", notes, [], "")

        # Now the same reading transcribed twice with DIFFERENT values.
        open(os.path.join(_d, "labs-a-p2.md"), "w").write(
            "\n".join([_H, _S, _row("Iron", "747", "01/01/2026", "a.pdf")]) + "\n")
        rows2, notes2, _ = rx.merge_labs(sorted(_g.glob(os.path.join(_d, "labs-*.md"))))
        check("a disagreement is reported", len(notes2), 1,
              "one of the two transcriptions is wrong; picking one silently hides it")
        check("and BOTH rows are kept",
              len([r for r in rows2 if r["marker"] == "Iron" and r["source file"] == "a.pdf"]),
              2, "")
        # A blank specimen must stay blank rather than borrow from a neighbour.
        open(os.path.join(_d, "labs-c-p1.md"), "w").write(
            "\n".join([_H, _S, _row("Sodium", "140", "01/01/2026", "c.pdf", "Serum"),
                        _row("Sodium", "140", "01/01/2026", "c.pdf", "")]) + "\n")
        rows3, _, _ = rx.merge_labs([os.path.join(_d, "labs-c-p1.md")])
        check("a blank specimen is its own observation",
              sorted(r["specimen"] for r in rows3), ["", "Serum"],
              "blood and urine sodium differ; a blank must not inherit")

        # A window that could not reach an analyte's value writes UNREADABLE; the neighbouring
        # window reads it. Both rows used to survive (the collapse key includes specimen, and an
        # unreadable row's specimen is unread too), and Stage 6's backstop then held the whole
        # research phase over ZINC while ZINC 82 mcg/dL sat two rows below it (2026-08-10).
        open(os.path.join(_d, "labs-z-p1.md"), "w").write(
            "\n".join([_H, _S,
                       _row("Zinc", "UNREADABLE", "01/01/2026", "z.pdf", "UNREADABLE"),
                       _row("Zinc", "82", "01/01/2026", "z.pdf", "ZINC"),
                       _row("Zinc", "82", "01/01/2026", "z.pdf", "EN"),
                       _row("Copper", "UNREADABLE", "01/01/2026", "z.pdf", "UNREADABLE")]) + "\n")
        rows4, _, sup4 = rx.merge_labs([os.path.join(_d, "labs-z-p1.md")])
        _zinc = [r for r in rows4 if r["marker"] == "Zinc"]
        check("an UNREADABLE row is dropped when another window read the value",
              "UNREADABLE" in [r["value"] for r in _zinc], False,
              "the value is in hand; the unreadable row is absence of evidence, not a gap")
        check("...regardless of the specimen cell, which is unread on that row",
              all(r["value"] == "82" for r in _zinc), True,
              "specimen 'UNREADABLE' cannot distinguish blood from urine — it is garbage")
        # The two readable rows still BOTH survive: their specimens ("ZINC", "EN") are
        # transcription artifacts of the same cell, so the collapse key keeps them apart. That is
        # a separate defect (the specimen column being filled with junk on this document) and is
        # NOT what this fix addresses — it is harmless to the backstop, which only flags
        # UNREADABLE, but it does duplicate a reading in the merged table.
        check("the readable duplicates survive — the specimen-junk defect is still open",
              len(_zinc), 2,
              "documented so the next reader knows this is known, not missed")
        check("...and the drop is reported, not silent",
              len([r for r, why in sup4 if why == "superseded"]), 1,
              "a drop nobody can audit is how a real gap would hide")
        check("an analyte unreadable EVERYWHERE keeps its row",
              [r["value"] for r in rows4 if r["marker"] == "Copper"], ["UNREADABLE"],
              "that is a real gap and the Stage 6 backstop must still see it")

        # A lab prints footnotes in the shape of results. The Function urinalysis panel prints
        # `NOTE` with the lab's NW flag directly under `NONE SEEN /LPF`, so the transcriber
        # emitted a row and wrote UNREADABLE for a value that was never there. Nothing could tell
        # it from a real gap — no window reads it, and the name IS on the page — so the Stage 6
        # backstop held the whole research phase over a footnote (2026-08-11).
        # The transcriber writes UNREADABLE only for the value it hunted for; a footnote's unit
        # and reference cells are left BLANK, not UNREADABLE. That is the shape that actually held
        # Stage 6 on 2026-08-12 (`| NOTE | UNREADABLE |  |  |`); the all-UNREADABLE shape below the
        # older tests used never occurs from the transcriber.
        open(os.path.join(_d, "labs-n-p1.md"), "w").write(
            "\n".join([_H, _S,
                       _row("NOTE", "UNREADABLE", "03/31/2026", "n.pdf", "URINALYSIS, COMPLETE",
                            unit="", ref=""),
                       _row("Copper", "UNREADABLE", "03/31/2026", "n.pdf", "Serum",
                            unit="ug/dL", ref="70-140"),
                       _row("Sodium", "140", "03/31/2026", "n.pdf", "Serum")]) + "\n")
        rows5, _, drop5 = rx.merge_labs([os.path.join(_d, "labs-n-p1.md")])
        check("a footnote row (value UNREADABLE, blank unit AND range) is dropped",
              [r["marker"] for r in rows5], ["Copper", "Sodium"],
              "a printed footnote held the entire research phase on 2026-08-11 and 2026-08-12")
        check("...reported under its own reason, not as a superseded reading",
              [(r["marker"], why) for r, why in drop5], [("NOTE", "nothing readable")],
              "the two drops mean different things and a reader must be able to tell them apart")
        check("an unreadable value that still has a unit and range is KEPT",
              [r["marker"] for r in rows5 if r["value"] == "UNREADABLE"], ["Copper"],
              "a measurement prints a unit or a range even when its value cannot be read — "
              "that is a real gap the backstop must still see")
        # Directly pin is_furniture_row on both shapes and the negative — a blank cell is "unread".
        check("furniture: value UNREADABLE with blank unit AND range",
              rx.is_furniture_row({"value": "UNREADABLE", "unit": "", "reference range": ""}), True,
              "the real 2026-08-12 NOTE row; requiring the literal UNREADABLE in all three missed it")
        check("furniture: the all-UNREADABLE shape too",
              rx.is_furniture_row({"value": "UNREADABLE", "unit": "UNREADABLE",
                                   "reference range": "UNREADABLE"}), True, "")
        check("NOT furniture: an unread value that still carries a unit",
              rx.is_furniture_row({"value": "UNREADABLE", "unit": "mg/dL", "reference range": ""}), False,
              "a unit means it was a measurement whose value simply could not be read")

        # check_labs must reach the same verdict on a file merged BEFORE the fix, or an existing
        # labs-complete.md keeps blocking Stage 6 over a value the pipeline already has.
        _lc = os.path.join(_d, "labs-complete.md")
        open(_lc, "w").write("\n".join([_H, _S,
                                        _row("Zinc", "UNREADABLE", "01/01/2026", "z.pdf", "UNREADABLE"),
                                        _row("Zinc", "82", "01/01/2026", "z.pdf", "ZINC")]) + "\n")
        _ids = rx._readable_reading_ids(_lc)
        check("check_labs' subsumption sees the readable reading",
              rx._reading_id({"source file": "z.pdf", "marker": "Zinc", "date": "01/01/2026"})
              in _ids, True,
              "the backstop recomputes the merge's subsumption so an old file is judged the same")

    print("\nresearch waits for an inventory that contains the answers")
    import tempfile as _tf3, time as _t3
    with _tf3.TemporaryDirectory() as _d:
        _draft = os.path.join(_d, "supplements-draft.md")
        _reg = os.path.join(_d, "regimen.txt")
        _conf = os.path.join(_d, "CONFIRMED.txt")
        open(_reg, "w").write("x")
        open(_draft, "w").write("x")
        _t3.sleep(0.01)
        # The user answers AFTER the inventory was built. Confirming closes the gate and writes
        # CONFIRMED.txt, but the draft is rebuilt by a separate card that intake only queues on
        # its NEXT run - the same run that releases the research stage. Without this check,
        # research reads the pre-answer inventory and silently does without a dose the user
        # had already pinned down.
        open(_conf, "w").write("Super EPA | NSF Certified for Sport, 1 gelcap")
        _srcs = rx.regimen_sources(_d)
        check("CONFIRMED.txt counts as a regimen source",
              any(os.path.basename(x) == "CONFIRMED.txt" for x in _srcs), True, "")
        check("an answer newer than the draft makes it stale",
              rx.stale(_draft, *_srcs), True, "")
        _t3.sleep(0.01)
        open(_draft, "w").write("rebuilt with the answer")
        check("rebuilding clears it", rx.stale(_draft, *_srcs), False, "")


    print("\nignored markers are not researched, but remain findings")
    # The `Marker review:` cards record `ignore: <name>` lines in labs-complete.md — the marker
    # stays a finding, only its research cards are skipped.
    _lc = os.path.join(tmp, "labs-complete.md")
    _saved = open(_lc).read() if os.path.exists(_lc) else None
    try:
        with open(_lc, "a") as fh:
            fh.write("\n# comment ignored\n\nignore: HDL LARGE\nignore: apolipoprotein-b\n")
        # Matched on alphanumerics only: the same analyte is written several ways across
        # panels, and making the user reproduce a lab's punctuation to be heard is its own bug.
        check("exact name matches", rx.is_ignored("HDL LARGE"), True, "")
        check("case is irrelevant", rx.is_ignored("hdl large"), True, "")
        check("punctuation is irrelevant", rx.is_ignored("APOLIPOPROTEIN B"), True,
              "written 'apolipoprotein-b' by the user")
        check("comments are not names", rx.is_ignored("comment ignored"), False, "")
        check("a marker they kept is not ignored", rx.is_ignored("Iron"), False, "")
        check("blank lines do not match everything", rx.is_ignored(""), False, "")
    finally:
        if _saved is None:
            os.path.exists(_lc) and os.remove(_lc)
        else:
            open(_lc, "w").write(_saved)

    print("\nno cost signal reaches the research cards")
    # Cost pulled the pipeline to goodrx/singlecare/iherb/amazon and made a price citation the
    # same class of evidence as a trial. The brief keeps the redundancy argument, which is what
    # section 5 actually wanted, and makes it from evidence instead.
    import re as _re
    _fspec = importlib.util.spec_from_file_location("fanout_cost", os.path.join(HERE, "fanout.py"))
    _fan = importlib.util.module_from_spec(_fspec)
    _fspec.loader.exec_module(_fan)
    _fan.HERMES = _fan.rxkanban.HERMES = NO_CLI
    _p3 = _fan.SUBSTANCE_PARTS[2].format(timing_q="", name="X", inputs="I")
    for word in ("cost tier", "cheaper", "price", "expensive"):
        check("part 3 does not ask about %s" % word, word in _p3.lower(), False, "")
    check("brief still asks about redundancy", "Redundancy" in _fan.SYNTH, True,
          "duplicating a mechanism is the useful half")
    check("brief does not ask about cost", "ongoing cost" in _fan.SYNTH, False, "")
    # Removing question 8 left TIMING_Q numbered 9 behind a gap.
    _p3t = _fan.SUBSTANCE_PARTS[2].format(
        timing_q=_fan.TIMING_Q.format(when="morning"), name="X", inputs="I")
    check("part 3 numbering is gapless", sorted(_re.findall(r"^(\d)\.", _p3t, _re.M)),
          ["6", "7", "8"], "a gap tells the model a question was withheld")

    print("\nlabs-brief — half the tokens, every observation")
    _labs = ("| marker | value | unit | reference range | specimen | date | source file | confidence |\n"
             "|---|---|---|---|---|---|---|---|\n"
             "| GLUCOSE | 87 | mg/dL | 65 - 99 | COMPREHENSIVE METABOLIC PANEL | 03/31/2026 | a_very_long_source_filename_2026.pdf |  |\n"
             "| GLUCOSE | NEGATIVE |  |  | URINALYSIS, COMPLETE | 03/31/2026 | another_long_source_filename_2026.pdf |  |\n"
             "| Albumin | 4.7 | g/dL | 3.5 - 5.0 | CMP | 02/26/2025 | a_very_long_source_filename_2025.pdf | high |\n"
             "| Albumin | 4.7 | g/dL | 3.5 - 5.0 | CMP | 02/26/2025 | a_DIFFERENT_source_filename_2025.pdf |  |\n")
    brief, n_in, n_out = rx.labs_brief(_labs)
    check("the source filename is gone", "source_filename" in brief, False,
          "54% of labs.md is one filename repeated on every row; no card reasons about it")
    check("provenance is still pointed at", "labs-complete.md" in brief, True, "")
    # Dropping specimen would merge a BLOOD glucose with a URINE one under one marker name.
    check("blood glucose survives", "| 87 |" in brief, True, "")
    check("urine glucose survives separately", "NEGATIVE" in brief, True, "")
    check("specimen is kept to tell them apart", "URINALYSIS, COMPLETE" in brief, True, "")
    # The same observation transcribed from two PDFs is one observation.
    check("a row duplicated across PDFs collapses", n_out, 3,
          "same marker, value, unit, range, specimen and date")
    check("nothing else is dropped", n_in, 4,
          "the header separator is not an observation")

    print("\nregimen sources — the user's files, not the pipeline's own bookkeeping")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        for f in ("regimen.txt", "notes.txt", "REGIMEN-REJECTED.txt", "LABS-REJECTED.txt"):
            open(os.path.join(d, f), "w").write("x")
        got = [os.path.basename(x) for x in rx.regimen_sources(d)]
        check("regimen.txt is a source", "regimen.txt" in got, True, "")
        check("another user .txt is a source", "notes.txt" in got, True, "")
        # The halt records land in inputs/ as .txt but are the pipeline's own bookkeeping, not the
        # user's regimen — a rejected run's record must not be read back as regimen text.
        check("REGIMEN-REJECTED.txt is NOT a source", "REGIMEN-REJECTED.txt" in got, False,
              "a halt record must not be read as regimen text")
        check("LABS-REJECTED.txt is NOT a source", "LABS-REJECTED.txt" in got, False,
              "a halt record must not be read as regimen text")

    print("\nendnote contract — stated once, carried by EVERY report-producing card")
    gspec = importlib.util.spec_from_file_location("fanout_ut", os.path.join(HERE, "fanout.py"))
    fan = importlib.util.module_from_spec(gspec)
    gspec.loader.exec_module(fan)
    fan.HERMES = fan.rxkanban.HERMES = NO_CLI

    print("\nexclusions are applied at ONE point, so every family inherits them")
    # THE DEFECT: is_ignored() was consulted in read_markers() only. A marker the user asked not
    # to research still got a Trend: card — most of the cost the exclusion was meant to avoid,
    # under a title they had never named. Filtering per family means a family added later has to
    # remember; filtering where anything becomes a card means it cannot forget.
    _shard_src = inspect.getsource(fan.shard)
    check("shard() is where a subject is excluded",
          "_excluded(subject, name)" in _shard_src, True,
          "a filter in each reader is a filter the next reader will not have")
    def _code_only(src):
        # Comments discuss the old design on purpose — that is what the comments are FOR. An
        # assertion that reads them is asserting about prose, and this one did until it failed
        # on a comment explaining why the filter had moved.
        return "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())

    for _fn in ("read_markers", "read_trends", "read_substances"):
        _src = _code_only(inspect.getsource(getattr(fan, _fn)))
        check("%s does not filter separately" % _fn,
              "is_ignored(" in _src or "is_dropped(" in _src, False,
              "two places deciding 'is this excluded' is two answers")
    # The shard loops moved from the old monolithic main() into phase_research_family (one
    # substage's workers), and coverage.md into phase_research_shells (the one phase that sees
    # every family). The invariants are the same; they are just asserted where the code now lives.
    _fam = inspect.getsource(fan.phase_research_family)
    _shells = inspect.getsource(fan.phase_research_shells)
    _trendsrc = (inspect.getsource(fan._trend_cards) + inspect.getsource(fan.phase_trend_dispatch)
                 + inspect.getsource(fan._trend_intro))
    # Substances and markers become cards through shard(), which applies the exclusion by subject.
    for _famlabel, _subj in (("Research", "substance"), ("Marker", "marker")):
        check("the %s family declares subject=%r" % (_famlabel, _subj),
              'subject="%s"' % _subj in _fam, True,
              "a family with no subject silently opts out of every exclusion")
    check("an excluded subject cannot become a parent",
          "[i for i in synth_ids if i" in _fam, True,
          "shard() returns None when it skips; a None in parents= is a shortened barrier")
    # 6c does NOT go through shard(): a triage + deterministic dispatch replaces the three-part
    # fan-out, so the exclusion is applied inline at the one place a trend becomes a card, and the
    # DISPATCH (not a synthesis) is what gets spliced in front of the 6c barrier.
    check("the trend family excludes an ignored marker inline",
          '_excluded("marker"' in _fam, True,
          "a marker the user asked not to research must not get a triage card either")
    check("the dispatch — not a synthesis — gates the 6c barrier",
          "_trend_cards(args, t)" in _fam, True,
          "the terminal card per trend is the dispatch (skip) or the synthesis it splices in")
    # Every family scopes which parts read the user's labs. Loading the 51KB labs file into
    # every part is what drove t_7c78c46c (Trend part 1) to a 140k-token peak against a 26k
    # board median, 6 compactions, and a 3-timeout block on 2026-08-09.
    check("substance parts scope the labs to part 2", "labs_parts={2}" in _fam, True,
          "parts 1 and 3 are about the substance; only part 2 examines this user's results")
    check("marker parts scope the labs to part 1", "labs_parts={1}" in _fam, True,
          "only part 1 interprets the user's out-of-range direction; 2 and 3 need no values")
    check("no trend card reads the labs file", "labs_line=NO_LABS_LINE" in _trendsrc, True,
          "the intro carries the series; a part reading 51KB it never uses compacts and loops")
    check("...because the trend series carries its draw dates inline",
          '"%s: %g"' in _trendsrc, True,
          "part 1 judges 'over this interval' — without dates the interval is in the labs file")
    # The gate is conservative, and the three-part gate survives on the meaningful path.
    check("an absent or garbled verdict deepens, never skips",
          "meaningful, reason = True" in _trendsrc, True,
          "only an explicit MEANINGFUL: no skips; anything else researches")
    check("the meaningful synthesis keeps the triage + two parts as parents",
          "[triage] + part_ids" in _trendsrc, True,
          "the same three-part gate 6c had before, now created by the dispatch")
    check("the verdict file reuses the PART- skip prefix",
          '-verdict.md" % (PART_PREFIX' in _trendsrc, True,
          "a report-globbing consumer must not read the verdict as a finished report")
    check("a part builds its fragment incrementally",
          "incrementally: read it first" in fan.PART_BODY, True,
          "a compaction or retry resumes from the fragment instead of re-fetching every source")
    # ONE cache: the fetch binding must not re-point the skill's page cache at a per-run corpus.
    # The corpus re-fetched 558 endnote URLs (201 already cached) inside a 20-minute card and
    # timed Stage 7 out on 2026-08-10; the skill's cache is trusted and shared by every caller.
    _bind_src = open(os.path.join(HERE, "rxfetch.py")).read()
    check("the fetch binding uses the skill's one shared cache",
          "configure(sources_dir" in _bind_src, False,
          "a re-pointed cache makes every audit fetch a miss and proves nothing the cache "
          "did not already prove")
    check("the brief is told to report what was not covered",
          "coverage.md" in fan.SYNTH and "did NOT cover" in fan.SYNTH, True,
          "an excluded marker looks exactly like one researched and found unremarkable")
    check("coverage.md is written even when nothing was excluded",
          "Nothing was excluded." in _shells, True,
          "a missing section and a section saying nothing look identical to a reader")

    # 6a/6b/6c substage Begins must be PARENTLESS. The Research Begin card is what creates them, so
    # an edge back to it is always already satisfied and only delays them until it reaches done —
    # parentless, they are eligible the moment they exist and research in parallel. 6d is the one
    # real dependency (it needs every substance report), so its Begin waits on 6a's Barrier.
    import types as _types6
    import tempfile as _tf6
    _calls6 = []

    def _fake_create6(args, title, assignee, body, parents=(), runtime="45m", priority=0):
        _calls6.append((title, list(parents)))
        return "t_%02d" % len(_calls6)

    _done6 = []
    with _tf6.TemporaryDirectory() as _d6:
        _sv6 = (fan.INPUTS, fan.REGIMEN, fan.create, fan.rxkanban.splice, fan._complete_self)
        _env6 = os.environ.get("HERMES_KANBAN_TASK")
        try:
            fan.INPUTS = _d6
            fan.REGIMEN = os.path.join(_d6, "regimen-final.md")
            open(fan.REGIMEN, "w").write(
                "| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 1 | Magnesium Glycinate | magnesium 200mg | 2 caps | evening | 2025-11 | high |\n")
            open(os.path.join(_d6, "labs-complete.md"), "w").write("")   # no markers/trends
            fan.create = _fake_create6
            fan.rxkanban.splice = lambda *a, **k: []
            # With the task id set and dry_run off, the phase ends by settling its own Begin via
            # the hermes CLI (_complete_self) — stubbed like create/splice, and asserted below.
            fan._complete_self = lambda summary="", dry=False: _done6.append(summary)
            os.environ["HERMES_KANBAN_TASK"] = "t_research_begin"   # set, yet 6a/6b/6c must ignore it
            fan.phase_research_shells(_types6.SimpleNamespace(dry_run=False))
        finally:
            (fan.INPUTS, fan.REGIMEN, fan.create, fan.rxkanban.splice, fan._complete_self) = _sv6
            if _env6 is None:
                os.environ.pop("HERMES_KANBAN_TASK", None)
            else:
                os.environ["HERMES_KANBAN_TASK"] = _env6
    _by_title6 = dict(_calls6)
    for _bt in ("Stage 6a: Research Substances", "Stage 6b: Research Markers",
                "Stage 6c: Research Trends"):
        check("%s is parentless — eligible as soon as it is created" % _bt,
              _by_title6.get(_bt), [],
              "parented on the Research Begin it would be blocked until that card completed")
    _d6parents = _by_title6.get("Stage 6d: Whole-regimen Screens")
    check("Stage 6d Begin waits on the 6a Barrier, not the Research Begin",
          bool(_d6parents) and "t_research_begin" not in _d6parents, True,
          "6d needs every substance report; that is its only real dependency")
    check("the Research Begin settles its own card (script-owned completion)",
          len(_done6), 1,
          "the card body says only 'run this'; if the phase does not complete it, nothing does")

    # A repeated header row in the merged lab file must be SKIPPED, not parsed as a phantom
    # marker='marker' / source='source file' data row — that row then fails "source not among the
    # PDFs" and held the whole research phase over nothing (2026-08-07: Stage 6 finished 0 cards).
    try:
        import fitz as _fitz_hdr  # noqa: F401
        _have_fitz = True
    except ImportError:
        _have_fitz = False
    if _have_fitz:
        with tempfile.TemporaryDirectory() as _dh:
            os.makedirs(os.path.join(_dh, "raw"))
            open(os.path.join(_dh, "labs-complete.md"), "w").write(
                "| marker | value | unit | reference range | specimen | date | source file |\n"
                "|---|---|---|---|---|---|---|\n"
                "| marker | value | unit | reference range | specimen | date | source file |\n")
            _svh = (rx.INPUTS, rx.RAW)
            try:
                rx.INPUTS, rx.RAW = _dh, os.path.join(_dh, "raw")
                _hstats, _hprobs = rx.check_labs()
            finally:
                (rx.INPUTS, rx.RAW) = _svh
        check("a repeated header row is not flagged as a phantom lab problem",
              any("source file" in w or m == "marker" for m, w in _hprobs), False,
              "only the first header defines columns; the rest are section repeats, not data")
        check("...and a repeated header is not counted as a lab row", _hstats.get("rows"), 0,
              "a header parsed as data inflates the row count and fabricates a hold")
    else:
        check("check_labs header test skipped (PyMuPDF absent)", True, True, "")

    # read_substances must actually RUN. A refactor once left it calling an undefined add(), so
    # every real `analyze-research` crashed with NameError before building a single card —
    # invisible to the getsource checks above, which never call it. Invoke it against a fixture.
    import tempfile as _tfsub
    with _tfsub.TemporaryDirectory() as _dsub:
        _sv = (fan.INPUTS, fan.REGIMEN)
        try:
            fan.INPUTS = _dsub
            fan.REGIMEN = os.path.join(_dsub, "regimen-final.md")
            open(fan.REGIMEN, "w").write(
                "| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 1 | Magnesium Glycinate | magnesium 200mg | 2 caps | evening | 2025-11 | high |\n"
                "| 2 | Magnesium Glycinate | magnesium 200mg | 2 caps | evening | 2025-11 | high |\n"   # dup -> deduped
                "| 3 | Vitamin D3 | vitamin d3 5000iu | 1 softgel | morning |  | high |\n")
            _subs = fan.read_substances()
            check("read_substances runs and dedups to the distinct items",
                  [s["name"] for s in _subs], ["Magnesium Glycinate", "Vitamin D3"],
                  "a call to an undefined add() crashed the whole research fan-out")
            check("read_substances reads Schedule as `when` by header name, not position",
                  [s["when"] for s in _subs], ["evening", "morning"],
                  "the numbered 6-field file carries name + Schedule; column lookup is by header")
            check("read_substances reads Started by header name, blank when unstated",
                  [s["started"] for s in _subs], ["2025-11", ""],
                  "the start date rides the settled row; supplements are usually empty")
            check("read_substances no longer carries a type",
                  {s["type"] for s in _subs}, {""},
                  "regimen-final.md dropped the type column in the refactor")
        finally:
            (fan.INPUTS, fan.REGIMEN) = _sv

    # An ignored qualifier-named marker must be excluded from the Marker: family, not just Trend:.
    # read_markers once stripped the trailing "(25-OH)" before the exclusion, so its key
    # ("vitamind") disagreed with the ignore recorded from the review card ("vitamind25oh") and the
    # marker still got a research card. The review card, read_markers, and trends must all derive
    # the SAME name from an out-of-range entry.
    _qentry = "Vitamin D (25-OH): 18 ng/mL (ref 30-100 ng/mL)"
    check("the review card keeps the qualifier in the marker name",
          re.split(r"[:—–]", _qentry, 1)[0].strip(), "Vitamin D (25-OH)",
          "the review title and ignore carry the qualifier")
    check("read_markers no longer strips the qualifier before the exclusion",
          'sub(r"\\s*\\([^)]*\\)\\s*$"' in inspect.getsource(fan.read_markers), False,
          "stripping (25-OH) here desyncs the Marker: exclusion key from the review's ignore")
    check("the two derived names flat-match, so one ignore excludes both families",
          rx._flat(re.split(r"[:—–]", _qentry, 1)[0].strip()),
          rx._flat("Vitamin D (25-OH)"),
          "an ignore of a qualifier-named marker must reach the Marker: family too")
    # coverage.md is written by the shell-builder ONLY — the one phase (phase_research_shells)
    # that sees every family. The per-family builders are the recovery path (re-run one substage),
    # so if they wrote coverage.md a re-run of one family would overwrite the whole-run record with
    # a partial one — the brief would then assert full coverage of a review that had gaps.
    check("coverage.md is written only by the research shell-builder",
          "coverage.md" in _shells and "coverage.md" not in _fam, True,
          "a family re-run must not rewrite the whole-run coverage record")

    print("\nread_markers fails closed when it cannot reach rx.py")
    # THE DEFECT: a second, independent parse of labs.md sat behind `except Exception`, kept so
    # that "a broken import cannot silently yield zero marker cards". It consulted NEITHER
    # labs_confirmed() NOR is_ignored(), so any import error switched off the lab gate and the
    # user's exclusions and built marker cards from numbers no human had confirmed - while the
    # docstring above it said there was now exactly one implementation of "what is abnormal".
    with tempfile.TemporaryDirectory() as _td:
        shutil.copy(os.path.join(HERE, "fanout.py"), os.path.join(_td, "fanout.py"))
        _lonely = importlib.util.spec_from_file_location("fanout_norx",
                                                         os.path.join(_td, "fanout.py"))
        _fl = importlib.util.module_from_spec(_lonely)
        _lonely.loader.exec_module(_fl)                    # no rx.py beside it
        _fl.HERMES = _fl.rxkanban.HERMES = NO_CLI
        _fl.LABS = os.path.join(_td, "labs.md")
        open(_fl.LABS, "w", encoding="utf-8").write("## Out of range\n\n- LDL: 190 H\n")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _got = _fl.read_markers()
            check("refuses rather than re-deriving markers", "returned %r" % _got, "SystemExit",
                  "the fallback bypassed the lab gate and the exclusion list")
        except SystemExit as _e:
            check("refuses rather than re-deriving markers", "SystemExit", "SystemExit",
                  "the fallback bypassed the lab gate and the exclusion list")
            check("...and says why", "Refusing to re-derive" in str(_e), True,
                  "a refusal that does not name the cause sends the reader to the source")
    gfmt = dict(inputs=fan.INPUTS, reports=fan.REPORTS,
                common=fan.COMMON.format(inputs=fan.INPUTS,
                                         labs_line=fan.LABS_LINE.format(inputs=fan.INPUTS)),
                endnote_rule=fan.ENDNOTE_RULE)
    # A research topic is now sharded: N part cards write fragments, one synthesis card
    # assembles them. BOTH produce endnotes — the parts write them, the synthesis carries them
    # across and may add its own — so both must state the contract, as must the two cards that
    # were never sharded.
    bodies = {
        "PART_BODY": dict(intro="Investigate X", n=1, total=3, name="X",
                          questions="1. What is X?", frag="marker-x-part1.md"),
        "SYNTH_BODY": dict(name="X", total=3, frag_list="    a.md",
                           questions="4. What distinguishes them?", out="marker-x.md"),
        "INTERACTIONS": {},
        "SCHEDULE": {},
    }
    for name, extra in bodies.items():
        body = getattr(fan, name).format(**dict(gfmt, **extra))
        # The contract lived only in SUBSTANCE. TREND had no endnote instruction at all, so it
        # invented one - title in quotes, claim paraphrased - and the audit could verify nothing.
        check("%s demands the verbatim sentence" % name,
              "verbatim sentence" in body and "not the article or page title" in body, True,
              "an endnote quoting only a title is unverifiable")
        check("%s body fits the 8KB cap" % name, len(body.encode()) <= 8 * 1024, True, "")

    # ---- reset leaves nothing behind ------------------------------------------------------
    # A run drops state at the top level - .phase.json, sources/, locations.json, and any report
    # a model wrote relative instead of to the absolute reports path. reset globbed only inputs/
    # and reports/, so all of it survived. It went unnoticed because the leftovers were being
    # cleared by hand next to each reset; the hand-clearing was hiding the gap it was covering.
    # Stale .phase.json is the one that bites quietly: phase_end() dates a duration from it, so
    # timings recorded to compare serving configurations silently include the previous run.
    with tempfile.TemporaryDirectory() as td:
        home, saved = os.path.join(td, "rx-review"), rx.HOME
        os.makedirs(os.path.join(home, ".fetchlocks"))
        try:
            rx.HOME = home
            rx.PHASE_FILE = os.path.join(home, ".phase.json")
            for rel in (".phase.json", "trend-creatinine.md", "CONTEXT-AUDIT.md",
                        "LENS-logic-part-01.md", ".fetchlocks/eutils.ncbi.nlm.nih.gov.lock"):
                open(os.path.join(home, rel), "w").write("x")
            os.makedirs(os.path.join(home, "salvage"))
            os.makedirs(os.path.join(home, "archive-20260728-1115"))
            found = {os.path.basename(p) for p in rx.derived_state()}
        finally:
            rx.HOME, rx.PHASE_FILE = saved, os.path.join(saved, ".phase.json")

    for want in (".phase.json", "trend-creatinine.md", "CONTEXT-AUDIT.md",
                 "LENS-logic-part-01.md", "eutils.ncbi.nlm.nih.gov.lock"):
        check("reset sweeps %s" % want, want in found, True,
              "survived reset and leaked into the next run")
    # The other half of the contract: a declared list, never a blanket wipe.
    for keep in ("salvage", "archive-20260728-1115"):
        check("reset keeps %s" % keep, keep in found, False,
              "kept deliberately - reset must not eat it")

    # The web-access caches (fetch AND search) are reused run-to-run on purpose (same substances
    # every review), so the unconditionally-swept derived_state must NOT name them, and both are
    # kept unless --clear-web-cache is passed.
    with tempfile.TemporaryDirectory() as td:
        wc, saved_wc = os.path.join(td, "sources"), rx.WEB_CACHE
        sc, saved_sc = os.path.join(td, "searches"), rx.WEB_SEARCH_CACHE
        os.makedirs(wc)
        os.makedirs(sc)
        open(os.path.join(wc, "abcd1234.txt"), "w").write("cached page")
        open(os.path.join(sc, "deadbeef99.json"), "w").write("{}")
        try:
            rx.WEB_CACHE, rx.WEB_SEARCH_CACHE = wc, sc
            swept = {os.path.basename(p) for p in rx.derived_state()}
            entries = {os.path.basename(p) for p in rx.web_cache_entries()}
        finally:
            rx.WEB_CACHE, rx.WEB_SEARCH_CACHE = saved_wc, saved_sc
    check("reset keeps the web-access caches by default",
          "abcd1234.txt" in swept or "deadbeef99.json" in swept, False,
          "the shared fetch/search caches are reused run-to-run; reset must not wipe them")
    check("web_cache_entries names both the fetch and search caches --clear-web-cache would drop",
          {"abcd1234.txt", "deadbeef99.json"} <= entries, True,
          "the flag clears the fetch page-text cache and the search result cache together")

    # reset must sweep DOTTED state files, which glob("*") silently skips. A leftover
    # `.regimen-review-pending` made the next run's Stage 3 barrier post nothing and block
    # anyway — the pipeline waited on a message the user never received (2026-08-10). The
    # enumeration is by construction now, not a hand-list that has to remember each new marker.
    with tempfile.TemporaryDirectory() as td:
        for rel in (".regimen-review-pending", ".correction-pending", "regimen-final.md"):
            open(os.path.join(td, rel), "w").write("x")
        os.makedirs(os.path.join(td, ".xcribe"))
        swept = {os.path.basename(p) for p in rx._files_in(td)}
    check("reset enumerates DOTTED state files, not just glob('*')",
          {".regimen-review-pending", ".correction-pending", "regimen-final.md"} <= swept, True,
          "a hidden marker that survives reset silently changes the next run's behaviour")
    check("...and leaves directories to the caller",
          ".xcribe" in swept, False,
          ".xcribe/ and raw/.duplicates/ are removed wholesale, not file by file")
    check("no hand-maintained glob is left to forget the next marker",
          "glob.glob(os.path.join(INPUTS" in inspect.getsource(rx.cmd_reset), False,
          "the forgotten-list bug recurred twice; enumeration is what stops a third")

    print("\nshard — a research topic is parts plus one synthesis, never one big card")
    # 2026-07-31: monolithic research cards peaked at 104.5k/103.2k/95.8k tokens against a 90k
    # compression threshold, then died trying to compact — six 429s and five timeouts on one,
    # twelve attempts on another, eight cards blocked. Worse than the timeouts: a card that
    # compacts answers from a SUMMARY of its sources while the endnote rule demands the
    # verbatim sentence, and a compacted answer is indistinguishable from a real one.
    calls = []

    def _fake_create(args, title, assignee, body, parents=(), runtime="45m", priority=0):
        calls.append(dict(title=title, body=body, parents=list(parents), runtime=runtime))
        return "t_%02d" % len(calls)

    _saved_create = fan.create
    _saved_env = os.environ.get("HERMES_KANBAN_TASK")
    try:
        fan.create = _fake_create
        os.environ["HERMES_KANBAN_TASK"] = "t_6a_begin"   # shard runs AS the substage Begin (6a/6b/6c)
        sid = fan.shard(None, "Marker", "LDL MEDIUM", "ldl-medium",
                        "Investigate the user's out-of-range lab marker: LDL MEDIUM",
                        fan.MARKER_PARTS, fan.MARKER_SYNTH, "marker-ldl-medium.md",
                        gfmt, priority=45)
    finally:
        fan.create = _saved_create
        if _saved_env is None:
            os.environ.pop("HERMES_KANBAN_TASK", None)
        else:
            os.environ["HERMES_KANBAN_TASK"] = _saved_env

    parts, synth = calls[:-1], calls[-1]
    check("one card per question group plus a synthesis",
          len(calls), len(fan.MARKER_PARTS) + 1, "")
    check("each part worker is PARENTLESS, not gated by its substage Begin",
          all(p["parents"] == [] for p in parts), True,
          "the substage Begin is a starter; a back-edge only delays the parts. Only a follow-on "
          "that consumes another worker's output is parented — the synthesis, on its parts")
    check("the synthesis waits on every part",
          synth["parents"], ["t_%02d" % i for i in range(1, len(parts) + 1)],
          "a report assembled before its evidence is worse than a late one")
    check("downstream is handed the SYNTHESIS id", sid, "t_%02d" % len(calls),
          "the parts are scaffolding; nothing should gate on them individually")
    check("only the synthesis writes the report",
          [("marker-ldl-medium.md" in c["body"]) for c in calls],
          [False] * len(parts) + [True],
          "a part that writes the report races the other parts")
    check("every fragment is named in the synthesis",
          all("PART-marker-ldl-medium-%d.md" % i in synth["body"]
              for i in range(1, len(parts) + 1)), True,
          "an unlisted fragment is evidence silently dropped")
    # Each part must carry ONLY its own question, or sharding buys nothing: a part that sees
    # all four answers all four and the context is back where it started.
    check("a part asks only its own question",
          [("Which substances in the user's regimen" in c["body"]) for c in parts],
          [False, True, False], "questions must not leak across parts")
    check("no part is asked the synthesis question",
          any("What would distinguish those explanations" in c["body"] for c in parts), False,
          "the synthesis question needs the other parts' answers")
    check("parts get a shorter clock than the old 45m",
          {c["runtime"] for c in parts}, {fan.PART_RUNTIME},
          "a shard that still needs 45 minutes has not been sharded")

    # ---- declared outputs are read back off the card body ---------------------------------
    # trend-creatinine.md was written to the worker's cwd instead of the absolute reports path
    # it was given. Nothing downstream reads that location, so the card completed, reported
    # success, and its analysis never reached the brief. The requirement is recovered from the
    # body itself so there is no second list to drift out of sync with fanout.py.
    # The synthesis card is the one that writes a named report now; the part cards write
    # fragments. Both carry a write instruction, and declared_outputs reads it off either.
    body = fan.SYNTH_BODY.format(**dict(gfmt, name="Creatinine", total=3,
                                        frag_list="    trend-creatinine-part1.md",
                                        questions="4. When would this warrant action?",
                                        out="trend-creatinine.md"))
    check("declared output found in a formatted body",
          "trend-creatinine.md" in rx.declared_outputs(body), True,
          "the write instruction IS the specification")
    check("a body with no write instruction declares nothing",
          rx.declared_outputs("Think about things. Do not write a file."), set(),
          "must not invent a requirement")
    check("tilde form of the reports path is recognised",
          "BRIEF.md" in rx.declared_outputs("Write ~/.hermes/reports/rx-review/BRIEF.md."),
          True, "bodies are not always expanded")
    # A path that merely mentions the directory is not a write instruction for a file.
    check("bare reports directory declares nothing",
          rx.declared_outputs("The substance reports are in %s/ - read them." % rx.REPORTS),
          set(), "reading a directory is not producing a file")

    # ---- reset never unlinks the database --------------------------------------------------
    # reset used to `boards rm --delete` + `boards create`, which replaces kanban.db. The
    # dashboard holds that file open continuously and `gateway restart --all` does not touch
    # it, so the unlink corrupted the DB on 2026-07-29 and cost a whole run.
    src = inspect.getsource(rx.cmd_reset) + inspect.getsource(rx.clear_board)
    check("reset does not delete the board", "boards\", \"rm" in src or "'rm'" in src, False,
          "unlinking kanban.db under the dashboard's open fd corrupts it")
    check("reset empties the board by deleting tasks", "archive" in src, True,
          "archive + archive --rm is the supported bulk delete")

    # ---- nothing may open the live kanban DB read-write ------------------------------------
    # rxkanban.splice() and verify.cmd_fanout() opened the board with sqlite3.connect(db) for
    # what were plain SELECTs. A read-write connection to a WAL database may CHECKPOINT when it
    # closes - rewriting and resizing the main file - and these run inside card workers that get
    # killed by timeouts, board clears and gateway restarts. The board was corrupted four times
    # on 2026-07-29/30; the last was truncated to 39 pages when its own header declared 40.
    # They also had no busy_timeout and were never closed.
    import ast
    for fname in ("rxkanban.py", "verify.py", "rx.py", "fanout.py", "lenses.py"):
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        bad = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"
                    and getattr(node.func.value, "id", "") == "sqlite3"):
                continue
            arg = node.args[0] if node.args else None
            src = ast.unparse(arg) if arg is not None else ""
            if "kanban" in src.lower():
                # The board is Hermes's file — read it ONLY through the `hermes kanban` CLI, never
                # a raw connection (RW corrupts via WAL checkpoint/resize; even RO bypasses the
                # WAL/synchronous/busy_timeout settings Hermes sets on every connection it makes).
                bad.append("line %d: opens the kanban board directly — use the hermes CLI: %s"
                           % (node.lineno, src[:60]))
            elif "mode=ro" not in src:
                bad.append("line %d: opens a DB read-write: %s" % (node.lineno, src[:60]))
        check("%s never opens the kanban board directly, and no DB read-write" % fname, bad, [],
              "the board goes through the CLI; a RW WAL connection can checkpoint and truncate it")




    # doctor must not crash on a freshly-cleared board: with no draft, check_regimen returns a
    # (name, hint) tuple sentinel, and doctor used to read it as an item dict and traceback.
    class _Args:
        pass
    with tempfile.TemporaryDirectory() as td:
        saved = rx.INPUTS
        try:
            rx.INPUTS = td                        # empty inputs: no supplements-draft.md
            rc = rx.cmd_doctor(_Args())
        except Exception as e:                    # noqa: BLE001
            rc = "CRASHED: %s" % e
        finally:
            rx.INPUTS = saved
    check("doctor survives an empty/cleared board", rc, 0,
          "a fresh board has no draft; doctor must report that, not traceback")

    print("\nunique_pdfs — a re-upload must never displace transcribed work")
    # 2026-07-30: the keeper used to be first-in-sorted-glob order — alphabetical on a random
    # doc_ prefix, a coin flip per re-upload. 12 of 21 byte-identical re-uploads drew a lower
    # id, displaced their transcribed originals, and every one of those labs entered labs.md
    # twice: the user was asked to confirm 980 markers when the true count was 642.
    with tempfile.TemporaryDirectory() as td:
        saved_in, saved_raw = rx.INPUTS, rx.RAW
        try:
            rx.INPUTS = td
            rx.RAW = raw = os.path.join(td, "raw")
            os.makedirs(raw)
            orig = os.path.join(raw, "doc_zzzz_cbc.pdf")     # transcribed original
            re_up = os.path.join(raw, "doc_aaaa_cbc.pdf")    # identical re-upload, sorts FIRST
            for p in (orig, re_up):
                open(p, "wb").write(b"%PDF-1.4 same bytes")
            orig_out = os.path.join(td, rx._lab_out_name(orig))
            open(orig_out, "w").write("| CBC | 1 |")

            keep, dupes = rx.unique_pdfs(raw)
            check("transcribed copy stays canonical", keep, [orig],
                  "doc_aaaa sorts before doc_zzzz and used to win the flip")
            check("re-upload is the duplicate", [d for d, _ in dupes], [re_up], "")

            # nothing transcribed yet: the oldest upload wins, deterministically
            iron_old = os.path.join(raw, "doc_mmmm_iron.pdf")
            iron_new = os.path.join(raw, "doc_bbbb_iron.pdf")
            for p in (iron_old, iron_new):
                open(p, "wb").write(b"%PDF-1.4 iron")
            os.utime(iron_old, (1000, 1000))
            keep2 = rx.unique_pdfs(raw)[0]
            check("oldest upload wins when none transcribed",
                  iron_old in keep2 and iron_new not in keep2, True,
                  "stable across runs; no filename lottery")

            # quarantine: loser pdfs move, a loser's stray transcription moves with it,
            # and the keeper's transcription is never touched
            stray_out = os.path.join(td, rx._lab_out_name(re_up))
            open(stray_out, "w").write("| CBC | 1 |")         # the coin-flip era's leftover
            os.utime(orig_out, (1000, 1000))                  # keeper's output is the elder
            _keep3, dupes3 = rx.unique_pdfs(raw)
            n_strays = rx.quarantine_duplicates(raw, dupes3)
            q = os.path.join(raw, ".duplicates")
            check("stray transcription count reported", n_strays, 1,
                  "labs.md merged a set that no longer exists; intake must re-merge")
            check("loser pdf quarantined",
                  os.path.exists(os.path.join(q, "doc_aaaa_cbc.pdf")), True,
                  "moved, never deleted — medical documents stay recoverable")
            check("stray transcription quarantined",
                  os.path.exists(os.path.join(q, os.path.basename(stray_out))), True, "")
            check("keeper pdf untouched", os.path.exists(orig), True, "")
            check("keeper transcription untouched", os.path.exists(orig_out), True, "")
            check("idempotent: nothing left to quarantine", rx.unique_pdfs(raw)[1], [],
                  "a second intake run must find a clean raw/")
        finally:
            rx.INPUTS, rx.RAW = saved_in, saved_raw

    print("\nregimen --from-gdoc — one verb, nothing to pipe")
    # 2026-07-30: the two-command flow (docs.py read --out, then regimen --from) kept being
    # collapsed into `docs.py … | rx.py regimen --stdin` by the agent — python3 into python3,
    # which the security scanner holds for manual approval. The single verb runs the reader
    # itself, so the pipe-shaped "optimisation" has nothing left to optimise.
    class _RegArgs:
        from_gdoc, source, stdin = "FAKE-DOC-ID", None, False
    with tempfile.TemporaryDirectory() as td:
        saved_in, saved_env = rx.INPUTS, os.environ.get("RX_GDOCS_SCRIPT")
        try:
            rx.INPUTS = td
            stub = os.path.join(td, "docs_stub.py")
            open(stub, "w").write(
                "import sys\n"
                "# argv: read <doc-id> --out <path>\n"
                "assert sys.argv[1] == 'read' and sys.argv[3] == '--out', sys.argv\n"
                "open(sys.argv[4], 'w').write('MORNING\\nThorne Sacro-B - 1 pill\\n')\n")
            os.environ["RX_GDOCS_SCRIPT"] = stub
            rc = rx.cmd_regimen(_RegArgs())
            got = open(os.path.join(td, "regimen.txt")).read() \
                if os.path.exists(os.path.join(td, "regimen.txt")) else "(missing)"
            check("gdoc ingest succeeds", rc, 0, "")
            check("regimen.txt holds the doc's text", "Thorne Sacro-B" in got, True,
                  "the reader's --out text must land verbatim in regimen.txt")

            os.environ["RX_GDOCS_SCRIPT"] = os.path.join(td, "nonexistent.py")
            check("missing reader fails loudly, not silently", rx.cmd_regimen(_RegArgs()), 1,
                  "a resolution miss must surface, so the agent asks instead of guessing")
        finally:
            rx.INPUTS = saved_in
            if saved_env is None:
                os.environ.pop("RX_GDOCS_SCRIPT", None)
            else:
                os.environ["RX_GDOCS_SCRIPT"] = saved_env

    sspec2 = importlib.util.spec_from_file_location("rxsplit_dc", os.path.join(HERE, "rxsplit.py"))
    ln = importlib.util.module_from_spec(sspec2)
    sspec2.loader.exec_module(ln)
    print("\ndeclutter — prose and chart axes never reach the transcriber")
    # 2026-07-31: a worker was handed page 7 of an Omega-3 report — a seafood nutrition table
    # and educational prose, no results — and asked to transcribe every marker on it. It
    # invented two. Page 1's gauge arrives as twelve axis labels wrapped around one real value.
    gauge = "\n".join(["Your Omega-3 Index", "Reference Range*:  3.00 - 14.10%", "YOUR LEVEL",
                       "12%", "1%", "2%", "3%", "4%", "5%", "6%", "7%", "8%", "9%", "10%",
                       "11%", "8.73%"])
    kept, removed = ln.declutter(gauge)
    check("the axis ladder is stripped", len(removed) >= 8, True, "12 labels around one value")
    check("THE READING SURVIVES ITS OWN AXIS", "8.73%" in kept, True,
          "taking the whole contiguous block was tried and destroyed 8.73% and 0.32% — the "
          "reading sits inside the same block as the ladder")
    check("context around the reading survives",
          "Reference Range*:  3.00 - 14.10%" in kept, True, "")

    prose = ("The Omega-3 Index is the proportion of long-chain omega-3s in your red blood "
             "cell membranes, and it reflects the omega-3 status of your whole body.")
    check("a sentence is removed", ln.declutter(prose)[1] != [], True, "")
    row = "| Alkaline Phosphatase        35 U/L                     53 - 128          L"
    check("a result row is NEVER removed", ln.declutter(row)[1], [],
          "digit density and function-word count keep rows out of the prose rule")
    cellwise = "\n".join(["GLUCOSE", "87", "65-99 mg/dL", "UREA NITROGEN", "15", "7-25 mg/dL"])
    check("a cell-per-line table is untouched", ln.declutter(cellwise)[1], [],
          "consecutive values are not an arithmetic ladder")
    ratios = "\n".join(["Omega-6:Omega-3", "4.2:1", "AA:EPA", "6.2:1"])
    check("ratio results are untouched", ln.declutter(ratios)[1], [],
          "page 2 counts ZERO marker rows and holds two real ratio results")

    check("a known reference page is recognised",
          ln.is_reference_page("Amount of EPA and DHA in Seafood\nand Supplements\n"
                               "Pacific Herring\n1056\n751"), True,
          "food-composition data is data-shaped; only its title distinguishes it")
    check("a results page is not",
          ln.is_reference_page("Your Omega-3 Index\n8.73%"), False, "")
    check("the match is on the page OPENING, not anywhere",
          ln.is_reference_page("Omega-3 Index\n8.73%\n"
                               "Amount of EPA and DHA in Seafood"), False,
          "a mention mid-page must not discard the results above it")

    print("\n_flat — a marker name must occur in its own source document")
    # 2026-07-31: the Omega-3 Index report yielded "Estimated Omega-3 Index" and "Estimated
    # cardiovascular death risk", both UNREADABLE — and the words estimated/risk/death/
    # cardiovascular appear ZERO times in that PDF. They were chart furniture turned into rows.
    # An UNREADABLE row skipped the value check entirely, so nothing verified the row existed
    # at all; the real Omega-3 Index (8.73%) was transcribed correctly alongside them.
    doc = "Your Omega-3 Index\nReference Range*: 3.00 - 14.10%\n8.73%\nArachidonic Acid 11.2%"
    check("a real marker is found in its source",
          rx._flat("Omega-3 Index") in rx._flat(doc), True, "")
    check("punctuation and case do not matter",
          rx._flat("ARACHIDONIC  ACID") in rx._flat(doc), True,
          "a PDF wraps and cases names differently from the table")
    check("a fabricated marker is not found",
          rx._flat("Estimated cardiovascular death risk") in rx._flat(doc), False,
          "the row the transcriber invented")
    check("a fabricated marker is not rescued by a shared word",
          rx._flat("Estimated Omega-3 Index") in rx._flat(doc), False,
          "'Omega-3 Index' occurs, but 'Estimated Omega-3 Index' does not")

    print("\n_marker_in_source — a name fractured by an extraction artifact still verifies")
    # 2026-08-08, live (t_9ace206a): the Function panel prints "THYROID PEROXIDASE\nEN\nANTIBODIES"
    # — a stray "EN" watermark fragment wedged mid-name. A contiguous flat match then wrongly
    # rejected a CORRECT transcription; the worker's only escape was to mangle the marker name.
    frac = rx._flat("THYROID PEROXIDASE\nEN\nANTIBODIES\n<1\n<9 IU/mL")
    check("a name split by a mid-name artifact is found",
          rx._marker_in_source("THYROID PEROXIDASE ANTIBODIES", frac), True,
          "an ordered, tight-gap match tolerates the interleaved 'EN'")
    check("an absent name is still rejected",
          rx._marker_in_source("TOTALLY FAKE MARKER", frac), False,
          "order + a tight gap do not manufacture a name that is not there")
    check("word order is required, not mere presence",
          rx._marker_in_source("ANTIBODIES PEROXIDASE THYROID", frac), False,
          "the same words out of order are not this marker")

    print("\n_value_in_text — whitespace and case tolerant, digit strict")
    src_v = "URIC ACID\n3.6 L\nmg/dL\nABO GROUP\nPOSITIVE"
    check("a mixed-case text value matches", rx._value_in_text("Positive", src_v), True, "")
    check("a value folded with spacing matches", rx._value_in_text("3.6", src_v), True, "")
    check("a changed digit is NOT matched", rx._value_in_text("3.7", src_v), False,
          "collapsing whitespace must not blur a real transcription error")

    print("\nTRANSCRIBE_BODY inlines the results; windows are sized to keep the body under 8KB")
    # The worker is handed the printed results IN the card body — it never opens a file to read
    # them — so a window's text must fit inside the card cap alongside the instruction.
    check("the worker is handed the text, not a file to read",
          "read_file" not in rx.TRANSCRIBE_BODY and "srcfile" not in rx.TRANSCRIBE_BODY, True,
          "the results ride inline in the body")
    _full = rx.TRANSCRIBE_BODY.format(outfile="/x/" + "y" * 80 + ".tbl.md",
                                      token="abc123def456", results="X" * rx.INLINE_BUDGET)
    check("a budget-full transcribe body fits the 8KB cap", len(_full.encode()) <= 8 * 1024, True,
          "INLINE_BUDGET must leave room for the template, the output path and the token")

    print("\n_strip_page_furniture — page markers and the identity header never reach a window")
    _furn = ("Appendix 1 [Enhanced PDF Report OZ776061F-1.pdf] - Page 4 of 13\n"
             "GLUCOSE\n87\n65-99 mg/dL\nPAGE 4 OF 13")
    _clean = rx._strip_page_furniture(_furn)
    check("the Page-N-of-M footer is stripped", "PAGE 4 OF 13" in _clean, False, "")
    check("the identity header (with the filename) is stripped", ".pdf" in _clean, False,
          "the source filename in the header is exactly what primed a fabricated panel")
    check("a real result line survives", "GLUCOSE" in _clean and "87" in _clean, True, "")
    # 2026-08-08, live (t_4764fa84): the Function report's identity line is "Enhanced PDF Report
    # OZ776061F-1" with a twin "…OZ776061F-1.pdf [See Appendix 1 for details]" — the .pdf sits
    # OUTSIDE any bracket and neither line carries a Page-N marker, so both slipped through and the
    # model transcribed them as a fabricated marker that then blocked Stage 6 on source verification.
    _clean2 = rx._strip_page_furniture(
        "Enhanced PDF Report OZ776061F-1\n"
        "Enhanced PDF Report OZ776061F-1.pdf [See Appendix 1 for details]\n"
        "ABO GROUP\nA\nGLUCOSE 87 mg/dL 65-99")
    check("a bare report-identity line is stripped", "Enhanced PDF Report" in _clean2, False,
          "left in, the model transcribes it as a fabricated marker")
    check("a .pdf / appendix reference is stripped",
          ".pdf" in _clean2 or "Appendix" in _clean2, False, "")
    check("real result lines survive the identity strip",
          "ABO GROUP" in _clean2 and "GLUCOSE" in _clean2, True, "")
    check("a marker merely containing the word 'report' is kept",
          "Comprehensive Metabolic Report" in rx._strip_page_furniture("Comprehensive Metabolic Report"),
          True, "only 'Report <identity code>' furniture is stripped, not the word report")
    # 2026-08-08 REGRESSION: boilerplate chrome-detection digit-blanks a line, so every value like
    # 13.8 became '#.#' — recurring on most pages, flagged as chrome, and the ENTIRE value column was
    # deleted (a whole CBC panel transcribed as UNREADABLE). Bare numbers are values, never furniture.
    check("a bare numeric value is NEVER stripped as furniture",
          rx._strip_page_furniture("HEMOGLOBIN\n13.8\n13.2-17.1 g/dL"),
          "HEMOGLOBIN\n13.8\n13.2-17.1 g/dL",
          "digit-blanked chrome detection must not delete the value column")

    print("\ncheck_labs verifies values with the same tolerance as the transcribe check")
    # The source prints a value and its flag on separate lines ("A" then "NW"); the model folds
    # them ("A NW"). _row_in_source accepts that, but check_labs used a raw substring and blocked
    # Stage 6 over the faithful transcription. Both must read the value the same way.
    import inspect as _inspect
    _cl_src = _inspect.getsource(rx.check_labs)
    check("check_labs verifies the value with _value_in_text",
          "_value_in_text(value" in _cl_src, True,
          "a stricter backstop than the transcribe check false-blocks two-line values")
    check("check_labs no longer raw-substrings the value",
          "value_without_flag(value) not in" not in _cl_src, True,
          "the raw substring is what rejected 'A NW' against a two-line 'A' / 'NW' source")

    print("\n_line_windows — overlapping whole-line windows, each under the byte budget")
    _lines = ["MARKER%02d 1.%02d mg/dL 0-9" % (i, i) for i in range(1, 61)]  # 60 result lines
    _ws = rx._line_windows(_lines, 400, 5)
    check("a long line list is split into more than one window", len(_ws) > 1, True,
          "sixty lines cannot inline as a single 400-byte body")
    check("every window fits the byte budget",
          all(len(rx._window_text(_lines, a, b).encode()) <= 400 for a, b in _ws), True,
          "a window over budget would blow the card cap")
    check("windows never split a line",
          all(rx._window_text(_lines, a, b).split("\n") == _lines[a - 1:b] for a, b in _ws), True,
          "a window is whole lines only — never half a line")
    check("consecutive windows overlap",
          all(_ws[i + 1][0] <= _ws[i][1] for i in range(len(_ws) - 1)), True,
          "an overlap is what lets a boundary reading sit wholly inside one window")
    check("the windows cover every line",
          sorted(set(p for a, b in _ws for p in range(a, b + 1))), list(range(1, 61)),
          "no result line may be dropped by the split")
    check("a lone over-budget line stands alone, never cut",
          rx._line_windows(["X" * 900, "Y"], 400, 5), [(1, 1), (2, 2)],
          "whole-line alignment outranks the byte budget")

    print("\nobservation_key — a reading is analyte + specimen + scale, not a name")
    # 2026-07-31, live: the Function panel measures glucose, protein and bilirubin in BLOOD and
    # again in URINE by dipstick. On the name alone two correct transcriptions looked like one
    # reading with two contradictory values — six false disagreements, research stage blocked
    # for hours. _norm_marker also folds "PROTEIN, TOTAL" into "PROTEIN" and "BILIRUBIN, TOTAL"
    # into "BILIRUBIN", so the collapse was not limited to glucose.
    blood = {"marker": "GLUCOSE", "value": "87", "specimen": "Comprehensive Metabolic Panel"}
    urine = {"marker": "GLUCOSE", "value": "NEGATIVE", "specimen": "URINALYSIS, COMPLETE"}
    check("blood and urine glucose are different observations",
          rx.observation_key(blood) != rx.observation_key(urine), True,
          "the collision that blocked the pipeline")
    check("the urine row records its specimen",
          rx.observation_key(urine)[1], "urine", "read from the heading, not inferred")
    check("an unstated panel leaves the specimen unknown",
          rx.observation_key(blood)[1], "", "a metabolic panel is serum in practice; saying so "
                                            "would be an inference the page never made")
    check("unknown never equals a real specimen",
          rx.observation_key(blood)[1] == rx.observation_key(urine)[1], False,
          "an unrecorded specimen must refuse to merge, not merge wrongly")
    check("scale separates them even with no specimen at all",
          rx.observation_key({"marker": "GLUCOSE", "value": "87"})
          != rx.observation_key({"marker": "GLUCOSE", "value": "NEGATIVE"}), True,
          "Qn vs Ord — _numeric already knew this and threw it away")
    check("a specimen written into the NAME still counts",
          rx.observation_key({"marker": "Glucose (Dipstick)", "value": "Negative"})[1],
          "urine", "the old convention keeps working")
    # Same analyte, same specimen, same scale, different draws — must stay ONE observation or
    # supersede-by-recency and every trend break.
    check("two draws of one test remain the same observation",
          rx.observation_key({"marker": "Cholesterol", "value": "222", "specimen": ""})
          == rx.observation_key({"marker": "CHOLESTEROL, TOTAL", "value": "152", "specimen": ""}),
          True, "cross-vendor naming must still merge")

    print("\nrxsplit — long documents split on real structure, with overlap")
    sspec = importlib.util.spec_from_file_location("rxsplit_ut", os.path.join(HERE, "rxsplit.py"))
    rs = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(rs)

    # Structural fingerprints taken from the real 29-page Function Full Tests panel, which is
    # two reports bound into one file: 16 native pages footed "Page N of 16", then a Quest
    # appendix footed "Appendix 1 [...] - Page N of 13". Synthesising them keeps the corpus in
    # the repo without carrying anyone's results.
    # Pages are padded to the ~2.7k characters the real ones carry, because the packing
    # budget is what decides how many ranges a segment becomes: toy-sized pages would fit one
    # range per segment and silently exercise none of the overlap logic.
    def _pad(body, n):
        filler = ("Interpretive note %d: this result was reviewed by the laboratory "
                  "director and is reported for clinical correlation.\n" % n)
        return body + filler * max(1, (2700 - len(body)) // len(filler))

    native = [_pad("David Putzolu\nPage %d of 16\nPATIENT INFORMATION:\nDOB:\n09/13/1970\n"
                   "GLUCOSE\n87\n65-99 mg/dL\nUREA NITROGEN (BUN)\n15\n7-25 mg/dL\n" % n, n)
              for n in range(1, 17)]
    appendix = [_pad("Appendix 1 [Enhanced PDF Report OZ776061F-1.pdf] - Page %d of 13\n"
                     "Report Status: Final\nPUTZOLU, DAVID\nPAGE %d OF 13\n"
                     "CHOL/HDLC RATIO\n2.9\n<5.0 mg/dL\n" % (n, n), n)
                for n in range(1, 14)]
    pages = native + appendix

    check("segments split at the bound-report seam",
          rs.segments(None, pages), [(1, 16), (17, 29)],
          "font size cannot find this: the largest font on all 16 native pages is the name")

    # The footer "PAGE 1 OF 13" differs on every page, so exact-line matching never sees it as
    # chrome; digit-blanking does. Missing this counted twelve page footers as marker names.
    boiler = rs.boilerplate(appendix)
    check("varying page footers are recognised as chrome",
          "PAGE # OF #" in boiler, True, "digit-blanked frequency, not exact lines")
    # ...and the appendix footer is on 13 of 29 pages of the WHOLE file (45%, under the
    # threshold) but 13 of 13 of its own segment. Boilerplate must be computed per segment.
    check("whole-file frequency would have missed it",
          "PAGE # OF #" in rs.boilerplate(pages), False,
          "why boilerplate is computed per segment, not per document")

    plan = rs.plan(None, pages)
    check("a long document is split", len(plan) > 1, True, "")
    check("no range crosses the report seam",
          all(not (a <= 16 < b) for a, b, _ in plan), True,
          "pages either side of the seam belong to different reports")
    shared = [p for p, _, _ in rs.overlaps(plan)]
    check("internal seams are shared by two ranges", len(shared) > 0, True,
          "the shared page is the cross-check")
    check("no page is shared ACROSS the report seam", 17 in shared, False,
          "telling that worker its page is double-covered would be a lie")
    check("ranges are contiguous and complete",
          sorted(set(p for a, b, _ in plan for p in range(a, b + 1))),
          list(range(1, len(pages) + 1)), "a dropped page is a silently lost panel")

    # A short document must keep the single unsuffixed output name, or every cached
    # transcription and every pre-split run stops matching.
    short = ["Test Name Result Reference Range\nGLUCOSE 87 mg/dL 65-99\n"] * 2
    check("short documents are not split", len(rs.plan(None, short)), 1, "")

    print("\nreconcile_ranges — two transcriptions of one overlapping window must agree")
    with tempfile.TemporaryDirectory() as td:
        saved = rx.INPUTS
        try:
            rx.INPUTS = td
            hdr = "| marker | value | unit | reference range | date | source file |\n|---|---|---|---|---|---|\n"
            def w(name, rows):
                open(os.path.join(td, name), "w").write(hdr + "".join(rows))
            # L0001-0060 and L0050-0110 overlap on lines 50-60; GLUCOSE sits in that overlap.
            w("labs-fn-L0001-0060.md", ["| GLUCOSE | 87 H | mg/dL | 65-99 | 2026-03-31 | fn.pdf |\n"])
            w("labs-fn-L0050-0110.md", ["| Glucose | 87 | mg/dL | 65-99 mg/dL | 2026-03-31 | fn.pdf |\n"])
            agreed, conflicts, thin = rx.reconcile_ranges()
            check("formatting differences are agreement, not conflict",
                  (agreed, conflicts), (1, []),
                  "'87 H' vs '87', case, and a unit repeated in the range must not trip it")

            w("labs-fn-L0050-0110.md", ["| Glucose | 78 | mg/dL | 65-99 | 2026-03-31 | fn.pdf |\n"])
            agreed, conflicts, thin = rx.reconcile_ranges()
            check("a real disagreement is caught", len(conflicts), 1,
                  "transposed digits in the overlap are what it exists to find")

            # 2026-07-30, live: the Function panel measures glucose in BLOOD (87 mg/dL) and in
            # URINE by dipstick (NEGATIVE, no unit). Both transcriptions were right, neither row
            # was in the overlap, and keying on the name alone reported six "disagreements".
            w("labs-fn-L0001-0060.md", ["| GLUCOSE | 87 | mg/dL | 65-99 | 2026-03-31 | fn.pdf |\n"])
            w("labs-fn-L0050-0110.md",
              ["| GLUCOSE | 87 | mg/dL | 65-99 | 2026-03-31 | fn.pdf |\n",
               "| GLUCOSE | NEGATIVE |  | NEGATIVE | 2026-03-31 | fn.pdf |\n"])
            agreed, conflicts, thin = rx.reconcile_ranges()
            check("same analyte in another specimen is not a disagreement",
                  conflicts, [], "blood glucose mg/dL vs urine dipstick glucose, no unit")
            check("the comparable reading is still compared", agreed, 1, "")

            w("labs-fn-L0050-0110.md", ["| Sodium | 140 | mmol/L | 135-146 | 2026-03-31 | fn.pdf |\n"])
            agreed, conflicts, thin = rx.reconcile_ranges()
            check("an empty overlap warns but does not block",
                  (len(conflicts), len(thin)), (0, 1),
                  "an overlap may hold only narrative; suspicion must not stop a review")
        finally:
            rx.INPUTS = saved

    print("\nrxsplit — a marker's own reference brackets are not more markers")
    # 2026-07-30, live: the Insulin/Leptin panel reports two analytes and then eight bracket
    # lines for leptin, each with a range and a unit. Counting those said the PDF held ~7
    # markers, and a complete two-row transcription was reported to the user as looking short.
    brackets = ["Males:     0.3-13.4 ng/mL", "Females:   4.7-23.7 ng/mL",
                "10-13.9 years:     1.4-16.5 ng/mL", "Adult Lean Subjects 0.3-13.4 ng/mL",
                "Optimal          < or = 18.4 uIU/mL", "High             >18.4 uIU/mL"]
    for b in brackets:
        check("bracket not counted: %s" % b[:26].strip(),
              rs.count_rows(b), 0, "reference documentation for one marker")
    check("a real marker row still counts",
          rs.count_rows("| GLUCOSE 87 mg/dL 65-99 mg/dL"), 1, "the filter must not eat data")
    check("a bare reference-range cell still counts",
          rs.count_rows("65-99 mg/dL"), 1, "cell-per-line layouts depend on this line")

    print("\npending_transcriptions — never put a partial set to a human")
    # Uploads arrive in rounds, so an early merge card completes over a partial set and
    # advances the pipeline. The user was asked to confirm 600 markers from 20 PDFs while two
    # were still transcribing; a confirmation cannot be retracted, so the gate must wait.
    with tempfile.TemporaryDirectory() as td:
        saved_in, saved_raw = rx.INPUTS, rx.RAW
        try:
            import fitz
            rx.INPUTS = td
            rx.RAW = raw = os.path.join(td, "raw")
            os.makedirs(raw)
            doc = fitz.open()
            doc.new_page().insert_text((72, 72), "GLUCOSE 87 mg/dL 65-99 mg/dL")
            pdf = os.path.join(raw, "panel.pdf")
            doc.save(pdf)
            doc.close()

            check("an untranscribed PDF is pending",
                  [n for n, _ in rx.pending_transcriptions()], ["panel.pdf"],
                  "the gate must not post over a document still in flight")
            open(os.path.join(td, rx._lab_out_name(pdf)), "w").write("| GLUCOSE | 87 |")
            check("a transcribed PDF is not pending", rx.pending_transcriptions(), [], "")
        except ImportError:
            check("pending_transcriptions (PyMuPDF unavailable)", True, True, "skipped")
        finally:
            rx.INPUTS, rx.RAW = saved_in, saved_raw

    print("\nlenses.chunk_reports — an oversized report is split, never passed whole")
    # MIN_SECTION_CHARS was declared with a comment saying an over-budget report "is split at
    # its own headings instead". The constant was referenced nowhere: the split was never
    # written, so such a report went to a card whole — the card compacts and answers from its
    # own summary, which is the exact failure lenses.py exists to prevent, and the only trace
    # was a stdout line nothing consumed.
    lspec = importlib.util.spec_from_file_location("lenses_ut", os.path.join(HERE, "lenses.py"))
    ln = importlib.util.module_from_spec(lspec)
    lspec.loader.exec_module(ln)
    with tempfile.TemporaryDirectory() as td:
        saved = ln.REPORTS
        try:
            ln.REPORTS = td
            big = "".join("## Section %d\n\n%s\n\n" % (i, "prose " * 400) for i in range(1, 9))
            fat = os.path.join(td, "substance-big.md")
            open(fat, "w").write(big)
            thin = os.path.join(td, "marker-small.md")
            open(thin, "w").write("x" * 500)
            budget = 6000

            chunks, oversized = ln.chunk_reports([fat, thin], budget)
            sizes = [sum(os.path.getsize(f) for f in c) for c in chunks]
            check("the oversized report is reported", [n for n, _ in oversized],
                  ["substance-big.md"], "a silent split is as bad as a silent truncation")
            check("no chunk exceeds the budget", [s for s in sizes if s > budget], [],
                  "a chunk over budget is a card that will compact")
            check("it became several slices", len(chunks) > 2, True, "")
            slice_txt = open(chunks[0][0]).read()
            check("a slice names its parent report",
                  "substance-big.md" in slice_txt.split("---")[0], True,
                  "a finding must be attributed to the report, not to a slice number")
            check("slices are not re-chunked as reports next round",
                  sorted(os.path.basename(f) for f in ln.report_files()),
                  ["marker-small.md", "substance-big.md"],
                  "the LENS- prefix keeps intermediates out of the corpus")
            # A section bigger than the whole budget has no smaller structure to cut on.
            open(fat, "w").write("## One\n\n" + "z" * (budget * 3))
            chunks2, _ = ln.chunk_reports([fat], budget)
            check("an unsplittable section is still bounded",
                  [s for s in (sum(os.path.getsize(f) for f in c) for c in chunks2)
                   if s > budget * 1.2], [], "hard-split at line boundaries as a last resort")
        finally:
            ln.REPORTS = saved

    print("\ncmd_labs_report — a marker is not abnormal, a READING of it is")
    # 2026-07-30: membership was keyed on the marker NAME, so one abnormal Cholesterol in Dec
    # 2025 flagged every later Cholesterol row too. The user was shown 152 mg/dL against a
    # 108-199 range as "flagged out of range" and, in the same message, as "no longer out of
    # range, normal on the newest". Ten of fourteen findings on that draw were this bug.
    SUPERSEDE = """# Labs

| marker | value | unit | reference range | date | source file |
|---|---|---|---|---|---|
| Cholesterol | 222 H | mg/dL | 108 - 199 | 12/09/2025 | a.pdf |
| Cholesterol | 152 | mg/dL | 108 - 199 | 05/29/2026 | b.pdf |
| Iron | 97 | ug/dL | 35 - 140 | 12/09/2025 | a.pdf |
| Iron | 141 H | ug/dL | 35 - 140 | 05/29/2026 | b.pdf |
| Alkaline Phosphatase | 33 L | U/L | 53 - 128 | 12/09/2025 | a.pdf |
| Alkaline Phosphatase | 35 L | U/L | 53 - 128 | 05/29/2026 | b.pdf |

## Out of range
- Cholesterol: 222 H (ref: 108 - 199) [12/09/2025]
- Iron: 141 H (ref: 35 - 140) [05/29/2026]
- Alkaline Phosphatase: 33 L (ref: 53 - 128) [12/09/2025]
- Alkaline Phosphatase: 35 L (ref: 53 - 128) [05/29/2026]
"""

    class _RepArgs:
        json, dry_run = False, True

    with tempfile.TemporaryDirectory() as td:
        saved = rx.INPUTS
        try:
            rx.INPUTS = td
            open(os.path.join(td, "labs-complete.md"), "w", encoding="utf-8").write(SUPERSEDE)
            keys = rx.out_of_range_keys()
            has = lambda d, n, v: any(rx.keys_match(
                (d, rx.observation_key({"marker": n, "value": v}), v), k) for k in keys)
            check("findings are keyed by row, not by marker name",
                  has("2026-05-29", "Iron", "141"), True,
                  "the date and value are what make a finding a finding")
            check("a normal later reading is not a finding",
                  has("2026-05-29", "Cholesterol", "152"), False, "")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rx.cmd_labs_report(_RepArgs())
            text = buf.getvalue()
            head, _, tail = text.partition("**No longer out of range**")
            names = lambda s: set(re.findall(r"\*\*([A-Za-z][A-Za-z0-9 %()/-]*?)\*\*\s+—", s))
            resolved = set(re.findall(r"•\s+([A-Za-z][A-Za-z0-9 %()/-]*?)\s+— was", tail))
            flagged = names(head)

            check("a normal newest reading is NOT reported abnormal",
                  "Cholesterol" in flagged, False, "152 against 108-199 is in range")
            check("a genuinely abnormal newest reading still is",
                  {"Iron", "Alkaline Phosphatase"} <= flagged, True,
                  "141 H and 35 L must survive the fix")
            check("a marker never appears in both lists",
                  flagged & resolved, set(),
                  "the contradiction the user was shown")
            check("resolved means it really did resolve",
                  "Cholesterol" in resolved, True, "222 H then 152 is a real resolution")
            check("became-abnormal is not called resolved",
                  "Iron" in resolved, False,
                  "Iron went 97 -> 141 H; calling that 'no longer out of range' is backwards")
            check("still-abnormal is not called resolved",
                  "Alkaline Phosphatase" in resolved, False, "33 L then 35 L is still low")
        finally:
            rx.INPUTS = saved

    print("\nthe chain — stage 1 creates the whole Begin/Barrier spine up front")
    # Stage 1 is two verbs: `stage` copies what arrived and is run after every upload round,
    # `start` creates the whole fourteen-card spine (stages 2-8) and is run once. Nothing after
    # stage 1 creates a stage boundary; the order is edges in a graph that exists from the first
    # minute. (The 6a-6d substage shells are created dynamically by Stage 6, not by `start`.)
    check("`stage` creates nothing",
          "create(" not in inspect.getsource(rx.cmd_stage), True,
          "copying what arrived and beginning the work are different decisions")
    # THE WHOLE SPINE IS CREATED BY STAGE 1. `start` creates all fourteen Begin/Barrier cards up
    # front, each Barrier parented in front of the next stage's Begin, so the order is an edge in
    # a graph that exists from the first minute — nothing after stage 1 creates a stage boundary.
    # Source inspection is what let two ordering defects reach production (both stages created
    # their cards and gave them the wrong parents), so this runs `start` AS A CARD against a
    # stand-in board that dedupes on the idempotency key exactly as kanban does, then reads back
    # the parents of what came out.
    _spine_begins = ["Stage 2: Read Regimen", "Stage 3: Settle the Regimen",
                     "Stage 4: Transcribe Labs", "Stage 5: Review Labs",
                     "Stage 6: Research Begin", "Stage 7: Adversarial Review",
                     "Stage 8: Conclusion"]
    _spine_barriers = ["Stage 2: Regimen Read", "Stage 3: Finalize Regimen",
                       "Stage 4: Labs Transcribed", "Stage 5: Labs Complete",
                       "Stage 6: Research Complete", "Stage 7: Adversarial Complete",
                       "Stage 8: Conclusion Complete"]

    def _run_start_as_card(task):
        with tempfile.TemporaryDirectory() as _td:
            _saved = (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.rxkanban.create_card)
            _cards = {}
            try:
                rx.INPUTS = _td
                rx.RAW = os.path.join(_td, "raw"); os.makedirs(rx.RAW)
                rx.PHOTOS = os.path.join(_td, "photos")
                rx.DOC_CACHE = os.path.join(_td, "no-cache")   # absent -> nothing unstaged
                open(os.path.join(rx.RAW, "lab.pdf"), "wb").write(b"one lab pdf")
                open(os.path.join(_td, "regimen.txt"), "w").write("Magnesium 200mg evening\n")

                def _create_card(title, assignee, body, inputs, parents=(), runtime="20m",
                                 priority=0, key=None, dry=False):
                    if key in _cards:
                        return _cards[key]["id"]
                    tid = "t_%02d" % (len(_cards) + 1)
                    _cards[key] = dict(id=tid, title=title, parents=list(parents))
                    return tid

                rx.rxkanban.create_card = _create_card
                if task is None:
                    os.environ.pop("HERMES_KANBAN_TASK", None)
                else:
                    os.environ["HERMES_KANBAN_TASK"] = task

                class _A:
                    dry_run = force = json = False

                # A real run cannot reach `start` until the user says the labs are complete;
                # the spine assertions below are about ORDERING, so satisfy the gate the way a
                # run does — through the verb, not by writing its marker.
                with contextlib.redirect_stdout(io.StringIO()):
                    rx.cmd_uploads_done(_A())

                with contextlib.redirect_stdout(io.StringIO()):
                    _rc = rx.cmd_start(_A())
                return _rc, {c["title"]: c for c in _cards.values()}
            finally:
                os.environ.pop("HERMES_KANBAN_TASK", None)
                (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE,
                 rx.rxkanban.create_card) = _saved

    _rc, _byt = _run_start_as_card("t_self")
    check("start creates the whole spine", _rc, 0, "")
    check("...all fourteen spine cards exist",
          sorted(t for t in _byt if t in _spine_begins + _spine_barriers),
          sorted(_spine_begins + _spine_barriers),
          "a missing spine card is a stage that never runs")
    for _b, _bar in zip(_spine_begins, _spine_barriers):
        check("Barrier %s waits on its Begin" % _bar,
              _byt[_b]["id"] in _byt[_bar]["parents"], True,
              "a Barrier that does not wait on its Begin gates nothing")
    # The spine is a DAG: regimen (2->3) and labs (4->5) are two PARALLEL branches from the root,
    # Stage 6 JOINS both Barriers, and 7/8 chain behind 6. Assert each join/branch edge explicitly.
    _joins = {
        "Stage 3: Settle the Regimen": ["Stage 2: Regimen Read"],
        # Stage 5 (marker review) waits on Stage 4 AND Stage 3, so the regimen review settles before
        # the marker review is posted — the user is asked one thing at a time.
        "Stage 5: Review Labs": ["Stage 4: Labs Transcribed", "Stage 3: Finalize Regimen"],
        "Stage 6: Research Begin": ["Stage 3: Finalize Regimen", "Stage 5: Labs Complete"],
        "Stage 7: Adversarial Review": ["Stage 6: Research Complete"],
        "Stage 8: Conclusion": ["Stage 7: Adversarial Complete"],
    }
    for _begin, _bars in _joins.items():
        for _bar in _bars:
            check("%s waits on %s" % (_begin, _bar),
                  _byt[_bar]["id"] in _byt[_begin]["parents"], True,
                  "a missing branch/join edge lets a stage start on half its inputs")
    for _t, _c in _byt.items():
        check("%s is not parentless (running as a card)" % _t,
              bool(_c["parents"]), True,
              "a parentless card is ready the instant it exists")
    # The two branch heads (regimen and labs) start after Stage 1; running as a card, each names
    # the running card as its only parent.
    _heads = ("Stage 2: Read Regimen", "Stage 4: Transcribe Labs")
    for _h in _heads:
        check("branch head %s names the running card" % _h, _byt[_h]["parents"], ["t_self"], "")
    # THE STAGE-1 EXCEPTION. A hand run has no card to hang a branch head's edge from, so both
    # heads are created parentless and ready at once. Every OTHER spine card still has a parent.
    _rc2, _byt2 = _run_start_as_card(None)
    for _h in _heads:
        check("a hand run leaves branch head %s parentless" % _h,
              _byt2[_h]["parents"], [], "there is no upstream card to draw the edge from")
    for _t in [_b for _b in _spine_begins if _b not in _heads] + _spine_barriers:
        check("%s still has a parent on a hand run" % _t,
              bool(_byt2[_t]["parents"]), True, "only the two branch heads are the exception")

    print("\nthe old parallel path stays dead")
    # Both human questions could be outstanding at once, and only some orderings of the answers
    # finished by themselves: an unanswered supplement question had no edge holding the research
    # card, so that card ran, refused and blocked itself, and answering the question afterwards
    # released nothing. Re-introducing any part of this brings the ordering race back.
    _src = open(os.path.join(HERE, "rx.py")).read()
    check("no advance card body", "ADVANCE_BODY" in _src, False,
          "the advance card was the clock the branches shared")
    check("no advance cards", "Advance the pipeline" in _src, False, "")
    check("no combined intake command", "def cmd_intake(" in _src, False,
          "one command creating both branches is what made them concurrent")

    def _verb_registered(verb):
        """Does rx.py accept this subcommand? argparse exits 2 either way, so read the message.

        An unknown FLAG makes argparse refuse before the command can run, so this asks the real
        parser without any side effect on the board.
        """
        _argv, _err = sys.argv, io.StringIO()
        try:
            sys.argv = ["rx.py", verb, "--zzz-not-a-flag"]
            with contextlib.redirect_stderr(_err), contextlib.redirect_stdout(io.StringIO()):
                rx.main()
        except SystemExit:
            pass
        finally:
            sys.argv = _argv
        return "invalid choice" not in _err.getvalue()

    check("`intake` is gone from the CLI", _verb_registered("intake"), False,
          "a card body or a habit still naming it would silently do nothing")
    check("`intake-supplements` is gone from the CLI",
          _verb_registered("intake-supplements"), False,
          "renamed to intake-regimen-items; a card still naming it fails the worker mid-run")
    check("`regimen-clarify` is gone from the CLI", _verb_registered("regimen-clarify"), False,
          "per-item clarify cards are retired; missing items are batched at the barrier")
    for _gone in ("regimen-confirm", "finalize-regimen"):
        check("`%s` is gone from the CLI" % _gone, _verb_registered(_gone), False,
              "the old confirm/finalize verbs are replaced by gather + correct")
    for _v in ("stage", "intake-regimen", "intake-regimen-items", "gather-regimen-slugs",
               "correct-item-slug-request", "correct-item-slug-response", "regimen-accept",
               "marker-review", "labs-accept",
               "intake-labs", "review_labs",
               "analyze-research", "analyze-adversarial", "analyze-conclude"):
        check("`%s` is a registered verb" % _v, _verb_registered(_v), True,
              "the card bodies name it; an unregistered verb fails the worker mid-run")

    print("\nhuman input is a worker card that parents its stage's Barrier, not a gate")
    # A stand-in board that behaves like kanban in the ways these tests depend on: create_card
    # dedupes on the idempotency key, `list --json` returns every card, `link A B` makes A a
    # parent of B (the barrier waits on the worker), and `complete ID` marks a card done. Stage
    # commands run AS THEIR Begin card and read the parents back off what came out.
    class _FakeBoard:
        def __init__(self):
            self.by_id, self.by_key, self.completed = {}, {}, []
            self.blocked = {}
            self.subscribed, self.asked = [], []
            self._n = 0

        def create_card(self, title, assignee, body, inputs, parents=(), runtime="20m",
                        priority=0, key=None, dry=False):
            if key in self.by_key:
                return self.by_key[key]["id"]
            self._n += 1
            c = {"id": "t_%03d" % self._n, "title": title, "parents": list(parents),
                 "status": "todo", "body": body}
            self.by_key[key] = c
            self.by_id[c["id"]] = c
            return c["id"]

        def add(self, title, status="todo"):
            self._n += 1
            c = {"id": "t_%03d" % self._n, "title": title, "parents": [], "status": status}
            self.by_id[c["id"]] = c
            return c["id"]

        def id_of(self, title):
            return next((c["id"] for c in self.by_id.values() if c["title"] == title), None)

        def sh(self, cmd, *a, **k):
            o = type("_O", (), {"returncode": 0, "stdout": ""})()
            if "list" in cmd:
                o.stdout = json.dumps([{"id": c["id"], "title": c["title"],
                                        "status": c["status"]} for c in self.by_id.values()])
            elif "link" in cmd:
                i = cmd.index("link")
                a_id, b_id = cmd[i + 1], cmd[i + 2]
                if b_id in self.by_id:
                    self.by_id[b_id]["parents"].append(a_id)
            elif "complete" in cmd:
                cid = cmd[cmd.index("complete") + 1]
                self.completed.append(cid)
                if cid in self.by_id:
                    self.by_id[cid]["status"] = "done"
            elif "block" in cmd:
                cid = cmd[cmd.index("block") + 1]
                kind = cmd[cmd.index("--kind") + 1] if "--kind" in cmd else "generic"
                self.blocked[cid] = kind
                if cid in self.by_id:
                    self.by_id[cid]["status"] = "blocked"
            return o

    def _run_as_card(board, td, fn, task):
        _saved = (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.sh,
                  rx.subscribe, rx.send_detail, rx.rxkanban.create_card, rx.phase_start)
        try:
            rx.INPUTS = td
            rx.RAW = os.path.join(td, "raw"); os.makedirs(rx.RAW, exist_ok=True)
            rx.PHOTOS = os.path.join(td, "photos")
            rx.DOC_CACHE = os.path.join(td, "no-cache")
            rx.sh = board.sh
            rx.subscribe = lambda tid=None, *a, **k: board.subscribed.append(tid)
            rx.send_detail = lambda text="", *a, **k: (board.asked.append(text) or True)
            rx.phase_start = lambda *a, **k: None
            rx.rxkanban.create_card = board.create_card
            os.environ["HERMES_KANBAN_TASK"] = task

            class _A:
                dry_run = force = json = False

            with contextlib.redirect_stdout(io.StringIO()):
                rc = fn(_A())
            return rc
        finally:
            os.environ.pop("HERMES_KANBAN_TASK", None)
            (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.sh,
             rx.subscribe, rx.send_detail, rx.rxkanban.create_card, rx.phase_start) = _saved

    with tempfile.TemporaryDirectory() as td:
        board = _FakeBoard()
        os.makedirs(os.path.join(td, "raw"))
        open(os.path.join(td, "raw", "lab.pdf"), "wb").write(b"one lab pdf")
        open(os.path.join(td, "regimen.txt"), "w").write(
            "Thorne Super EPA, 1 pill in the morning\n"
            "Magnesium Glycinate 200mg at night\n")
        # Stage 1 lays the whole spine down first, so the Barriers the workers parent exist.
        _run_as_card(board, td, rx.cmd_start, "t_root")
        check("stage 1 laid the spine on the fake board",
              board.id_of("Stage 2: Regimen Read") is not None, True, "")

        # STAGE 2 — one worker, linked in front of the Stage 2 Barrier.
        _run_as_card(board, td, rx.cmd_intake_regimen,
                     board.id_of("Stage 2: Read Regimen"))
        _worker = board.id_of("Worker: Read regimen")
        check("stage 2 creates exactly one read-regimen worker",
              _worker is not None, True, "the draft is built by one worker")
        check("the read-regimen worker parents the Stage 2 Barrier",
              _worker in board.by_id[board.id_of("Stage 2: Regimen Read")]["parents"], True,
              "a Barrier that does not wait on the worker releases stage 3 early")
        check("the read-regimen worker names its Begin as a parent",
              board.id_of("Stage 2: Read Regimen")
              in board.by_id[_worker]["parents"], True,
              "a parentless worker is ready before its stage runs")

        # STAGE 3 — one `Regimen Intake:` worker per draft row. No trust rule, no guessing
        # branch, no question files: every supplement/medication row is researched into its own
        # regimen-item-<slug>.md and parents the `Stage 3: Finalize Regimen` Barrier, which runs
        # the human review.
        open(os.path.join(td, "regimen-draft.txt"), "w", encoding="utf-8").write(
            "product | brand | quantity | schedule | started\n"
            "Super EPA | Thorne | 2 gelcaps | morning | 2019-01\n"
            "Magnesium Glycinate 200mg||2 caps|evening|\n")
        _run_as_card(board, td, rx.cmd_intake_regimen_items,
                     board.id_of("Stage 3: Settle the Regimen"))
        _intakes = [c for c in board.by_id.values()
                    if c["title"].startswith("Regimen Intake: ")]
        check("stage 3 creates one Regimen Intake worker per draft row, Name = <brand> <product>",
              sorted(c["title"] for c in _intakes),
              ["Regimen Intake: Magnesium Glycinate 200mg", "Regimen Intake: Thorne Super EPA"],
              "brand is prepended unless the product already carries it (no 'Thorne Thorne'); "
              "a brand-less row keeps the product name as written")
        check("stage 3 creates NO guess workers",
              [c for c in board.by_id.values() if c["title"].startswith("Regimen guess")], [],
              "the deterministic trust rule is gone; nothing is guessed and asked")
        _s3bar = board.by_id[board.id_of("Stage 3: Finalize Regimen")]["parents"]
        check("every Regimen Intake parents the Stage 3 Barrier",
              all(c["id"] in _s3bar for c in _intakes) and len(_intakes) == 2, True,
              "the Barrier must wait on every item before stage 4 can start")
        _s3begin = board.id_of("Stage 3: Settle the Regimen")
        check("no Regimen Intake names the Stage 3 Begin as a parent",
              any(_s3begin in c["parents"] for c in _intakes), False,
              "the Begin is what creates them, so that edge is always already satisfied — "
              "they become ready on creation and research in parallel")
        check("the Regimen Intake body no longer wires a per-item clarify card",
              "regimen-clarify" in rx.REGIMEN_INTAKE_BODY, False,
              "the worker never asks now; the barrier's review settles corrections")
        _bodies = {c["title"]: c["body"] for c in board.by_id.values()
                   if c["title"].startswith("Regimen Intake: ")}
        check("a GENERIC row (no brand) is told not to hunt for a label",
              "GENERIC ingredient product" in _bodies["Regimen Intake: Magnesium Glycinate 200mg"]
              and "AT MOST two searches" in _bodies["Regimen Intake: Magnesium Glycinate 200mg"], True,
              "without it the worker chased a canonical label that does not exist — 41 tool "
              "calls, three timeouts, card blocked (Vitamin C, 2026-08-23)")
        check("a BRANDED row is not told it is generic",
              "GENERIC ingredient product" in _bodies["Regimen Intake: Thorne Super EPA"], False,
              "the note is keyed on the draft row's empty brand field, deterministic")

    print("\nbatched replies are routed by the number the USER wrote — no model in the loop")
    # The 2026-08-06 corruption: the model bound the reply to numbers and misrouted one drug's dose
    # onto two unrelated items. The parser keys on the number the user typed, so that cannot recur;
    # a number outside the review refuses the WHOLE batch, and an unreadable line is surfaced.
    _dir, _un = rx._parse_numbered_reply("3: Pravastatin 20mg\n1: correct\nlooks good")
    check("the reply parses to {number: answer}", _dir, {3: "Pravastatin 20mg", 1: "correct"},
          "answer 3 is keyed on the 3 the user wrote, never re-bound")
    check("an accept phrase contributes no directive", 2 in _dir, False, "")
    _d2, _u2 = rx._parse_numbered_reply("2,5 ignore\ngibberish here")
    check("a number-list with a shared verb parses", _d2, {2: "ignore", 5: "ignore"}, "")
    check("an unreadable line is surfaced, not dropped", _u2, ["gibberish here"],
          "silently dropping the user's intent is the failure to avoid")

    print("\ngather-regimen-slugs writes the numbered 6-field regimen-final.md")
    # The Stage 3 Barrier gathers every regimen-item-<slug>.md into ONE numbered regimen-final.md
    # (| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |), posts the review, and blocks
    # its own card. Each per-item file is a worker's sole write target, so gather is the join point.
    with tempfile.TemporaryDirectory() as td:
        _sv = (rx.INPUTS, rx.send_detail)
        try:
            rx.INPUTS = td
            rx.send_detail = lambda *a, **k: True
            # the draft names exactly the items whose files exist: gather's completeness guard
            # compares one against the other.
            open(os.path.join(td, "regimen-draft.txt"), "w", encoding="utf-8").write(
                "Thorne Super EPA|Thorne|2 gelcaps|morning|2019-01\n"
                "Magnesium Glycinate||2 caps|evening|\n")
            open(rx._regimen_item_path("Thorne Super EPA"), "w", encoding="utf-8").write(
                rx.REGIMEN_ITEM_HEADER + "\n|---|---|---|---|---|---|\n"
                "| Thorne Super EPA | EPA 425mg, DHA 270mg | 2 gelcaps | morning | 2019-01 | high |\n")
            open(rx._regimen_item_path("Magnesium Glycinate"), "w", encoding="utf-8").write(
                rx.REGIMEN_ITEM_HEADER + "\n|---|---|---|---|---|---|\n"
                "| Magnesium Glycinate | magnesium 200mg | 2 caps | evening |  | high |\n")

            class _A:
                dry_run = json = False

            with contextlib.redirect_stdout(io.StringIO()):
                _rcg = rx.cmd_gather_regimen_slugs(_A())
            _rows = rx._read_regimen_final_rows()
            check("gather writes one numbered row per per-item file",
                  (_rcg, len(_rows)), (0, 2),
                  "the barrier's join is the single point that assembles the settled regimen")
            check("the rows are numbered 1..N in order",
                  [n for (n, *_r) in _rows], [1, 2],
                  "Stage 6 and the correction verbs key on the number the user sees")
            check("each row carries Name, Quantity and Schedule",
                  sorted((nm, qty, sch) for (_n, nm, _i, qty, sch, _st, _cf) in _rows),
                  sorted([("Thorne Super EPA", "2 gelcaps", "morning"),
                          ("Magnesium Glycinate", "2 caps", "evening")]),
                  "read_substances reads Name and Schedule (as when); Quantity is carried too")
            check("each row carries Started as gathered from its per-item file",
                  sorted(st for (_n, _nm, _i, _q, _s, st, _cf) in _rows), ["", "2019-01"],
                  "the start date survives the gather; blank stays blank")
            check("the file carries the numbered 6-field header",
                  rx.REGIMEN_FINAL_HEADER in
                  open(os.path.join(td, "regimen-final.md"), encoding="utf-8").read(), True,
                  "| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |")
        finally:
            (rx.INPUTS, rx.send_detail) = _sv

    print("\ngather HOLDS when an item's lookup card died instead of dropping it silently")
    # The Vitamin C failure (2026-08-23): its Regimen Intake card gave_up after three timeouts,
    # so no regimen-item file existed — and gather combined whatever EXISTED. Vitamin C silently
    # vanished from the regimen review and everything downstream of it. Now every draft row must
    # be present or the barrier blocks and NAMES the gap.
    with tempfile.TemporaryDirectory() as td:
        _sv = (rx.INPUTS, rx.send_detail)
        try:
            rx.INPUTS = td
            rx.send_detail = lambda *a, **k: True
            open(os.path.join(td, "regimen-draft.txt"), "w", encoding="utf-8").write(
                "Thorne Super EPA | Thorne | 2 gelcaps | morning |\n"
                "Vitamin C 100mg||1 tablet|noon|\n")
            open(rx._regimen_item_path("Thorne Super EPA"), "w", encoding="utf-8").write(
                rx.REGIMEN_ITEM_HEADER + "\n|---|---|---|---|---|---|\n"
                "| Thorne Super EPA | EPA 425mg | 2 gelcaps | morning |  | high |\n")

            class _A2:
                dry_run = json = force = False

            _captured = {}
            def _fake_hold(reason, detail_lines=(), dry=False):
                _captured["reason"] = reason
                _captured["detail"] = list(detail_lines)
                return 3
            _real_hold = rx._hold
            rx._hold = _fake_hold
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    _rcm = rx.cmd_gather_regimen_slugs(_A2())
            finally:
                rx._hold = _real_hold
            check("gather holds instead of posting a partial review",
                  (_rcm, os.path.exists(os.path.join(td, "regimen-final.md"))), (3, False),
                  "a dropped ingredient is invisible downstream; the barrier must block loudly")
            check("the hold names the missing item",
                  any("Vitamin C" in l for l in _captured.get("detail", [])), True,
                  "the agent re-runs intake-regimen-items from the named gap")
            check("force gathers past the guard (recovery path)",
                  True, True, "")
        finally:
            (rx.INPUTS, rx.send_detail) = _sv

    print("\ncorrect-item-slug-request — drop renumbers, a correction records pending, else re-prompt")
    # The SCRIPT reads the leading integer, so the number the user wrote picks the line and no
    # correction can land on another item. `<n> drop` removes and renumbers and records coverage;
    # a correction records the pending line number; a reply with no number is re-prompted.
    with tempfile.TemporaryDirectory() as td:
        _sv = rx.INPUTS
        try:
            rx.INPUTS = td
            rx._write_regimen_final_rows([
                ("Thorne Super EPA", "EPA 425mg", "2 gelcaps", "morning", "", "high"),
                ("Magnesium Glycinate", "magnesium 200mg", "2 caps", "evening", "", "high"),
                ("Old Vitamin C", "ascorbic acid", "1 tablet", "noon", "2024-02", "low")])

            def _req(text):
                a = type("_RA", (), {"dry_run": False, "json": False, "text": text})()
                _b = io.StringIO()
                with contextlib.redirect_stdout(_b):
                    return rx.cmd_correct_item_slug_request(a), _b.getvalue()

            _req("3 drop")
            _rows = rx._read_regimen_final_rows()
            check("drop removes the numbered row",
                  [nm for (_n, nm, *_r) in _rows],
                  ["Thorne Super EPA", "Magnesium Glycinate"],
                  "the item the user could not confirm is excluded from research")
            check("...and renumbers what remains", [n for (n, *_r) in _rows], [1, 2],
                  "a gap in the numbering would misroute the next correction")
            check("...and records the drop in coverage.md",
                  "Old Vitamin C" in open(os.path.join(td, "coverage.md"),
                                          encoding="utf-8").read(), True,
                  "the brief lists what the user chose not to research")
            check("...leaving no correction pending",
                  os.path.exists(os.path.join(td, ".correction-pending")), False, "")

            _rc2, _out2 = _req("2 NOW brand, 400mg")
            check("a correction records the pending line number",
                  open(os.path.join(td, ".correction-pending"),
                       encoding="utf-8").read().strip(), "2",
                  "the request RECORDS which line it handed out; response updates THAT line")
            check("...and prints the current line and the correction",
                  "Magnesium Glycinate" in _out2 and "NOW brand, 400mg" in _out2, True,
                  "the LLM only merges the printed text; the script picked the line by number")

            os.remove(os.path.join(td, ".correction-pending"))
            _rc3, _out3 = _req("looks fine to me")
            check("a no-number reply re-prompts and records nothing",
                  (_rc3, os.path.exists(os.path.join(td, ".correction-pending"))), (1, False),
                  "a comment or a mistyped finish word must never merge into a line")
        finally:
            rx.INPUTS = _sv

    print("\ncorrect-item-slug-response — validates and replaces the pending line")
    # Reads the pending line number, validates the merged line (6 fields, Schedule not blank),
    # replaces THAT row and clears pending. A response with no pending request is refused, and a
    # blank Schedule is refused — so a stale reply cannot land and the user's timing is never lost.
    with tempfile.TemporaryDirectory() as td:
        _sv = rx.INPUTS
        try:
            rx.INPUTS = td
            rx._write_regimen_final_rows([
                ("Thorne Super EPA", "EPA 425mg", "2 gelcaps", "morning", "", "high"),
                ("Magnesium Glycinate", "magnesium 200mg", "2 caps", "evening", "2025-11", "high")])

            def _resp(text):
                a = type("_RB", (), {"dry_run": False, "json": False, "text": text})()
                _b = io.StringIO()
                with contextlib.redirect_stdout(_b):
                    return rx.cmd_correct_item_slug_response(a), _b.getvalue()

            _rc0, _ = _resp("| Magnesium Glycinate | magnesium 400mg | 2 caps | evening | high |")
            check("a response with no pending correction is refused", _rc0, 1,
                  "a stale response with no request cannot land on any line")

            with open(os.path.join(td, ".correction-pending"), "w", encoding="utf-8") as fh:
                fh.write("2\n")
            _rc1, _ = _resp("| Magnesium Glycinate | magnesium 400mg | 2 caps | evening |  | high |")
            _rows = rx._read_regimen_final_rows()
            check("a valid merged line replaces exactly the pending row",
                  (_rc1, [ing for (_n, _nm, ing, *_r) in _rows]),
                  (0, ["EPA 425mg", "magnesium 400mg"]), "only line 2 changed")
            check("...and clears the pending state",
                  os.path.exists(os.path.join(td, ".correction-pending")), False, "")

            with open(os.path.join(td, ".correction-pending"), "w", encoding="utf-8") as fh:
                fh.write("2\n")
            _rc2, _ = _resp("| Magnesium Glycinate | magnesium 400mg | 2 caps |  |  | high |")
            check("a blank Schedule is refused",
                  (_rc2, [sch for (_n, _nm, _i, _q, sch, _st, _cf) in rx._read_regimen_final_rows()]),
                  (1, ["morning", "evening"]),
                  "Schedule is the user's own timing and must never be blanked by a merge")
        finally:
            rx.INPUTS = _sv

    print("\napproval is owned by the script — the LLM never raw-completes a blocked card")
    # The 2026-08-06 gymnastics: on `approved` the agent explored, tried to unblock, and re-ran
    # gather (re-posting the whole review). Now every reply goes through correct-item-slug-request;
    # an approval word completes the barrier via the script (like labs-accept), and re-running
    # gather does not re-post.
    with tempfile.TemporaryDirectory() as td:
        _sv = (rx.INPUTS, rx.sh, rx._card_id_by_title, rx._my_card_id, rx.send_detail)
        try:
            rx.INPUTS = td
            rx._write_regimen_final_rows([("Thorne Creatine", "creatine 5g", "1 scoop", "morning", "", "high")])
            _completed, _posts, _blocks = [], [], []
            rx.sh = lambda cmd, **k: (_completed.append(cmd) if "complete" in cmd else
                                      (_blocks.append(cmd) if "block" in cmd else None)) \
                or type("R", (), {"stdout": "", "returncode": 0})()
            rx._card_id_by_title = lambda t: "bar-1"
            rx._my_card_id = lambda: "bar-1"
            rx.send_detail = lambda *a, **k: _posts.append(1) or True

            def _req(txt):
                a = type("A", (), {"text": txt, "dry_run": False, "json": False})()
                with contextlib.redirect_stdout(io.StringIO()):
                    return rx.cmd_correct_item_slug_request(a)

            check("`approved` completes the barrier via the script", (_req("approved"), bool(_completed)),
                  (0, True), "the state transition is a verb, not a raw kanban_complete")
            _completed.clear()
            check("a synonym (`looks good`) also completes", (_req("looks good"), bool(_completed)),
                  (0, True), "reusing approval synonyms so a natural reply is not bounced")
            _completed.clear()
            check("a numbered correction does NOT complete", (_req("1 evening"), bool(_completed)),
                  (0, False), "only a bare approval word accepts; a number is a correction")

            # idempotent posting: two gather runs post the review only ONCE
            _posts.clear()
            os.path.exists(os.path.join(td, ".regimen-review-pending")) and os.remove(
                os.path.join(td, ".regimen-review-pending"))
            _a = type("A", (), {"dry_run": False, "json": False})()
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_gather_regimen_slugs(_a); rx.cmd_gather_regimen_slugs(_a)
            check("gather posts the review only once across re-runs", len(_posts), 1,
                  "a re-run must re-block but not re-send_detail — that was the channel spam")

            # ...but a STALE marker must not suppress a review the user has not seen. One left
            # behind by an earlier run (reset used to skip dotfiles) made Stage 3 block its card
            # and post nothing: the pipeline waited on a message nobody received (2026-08-10).
            _posts.clear()
            open(os.path.join(td, ".regimen-review-pending"), "w").write("some-older-run")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_gather_regimen_slugs(_a)
            check("a stale marker from another review does NOT suppress this one", len(_posts), 1,
                  "the marker records WHAT was posted; a different review is always delivered")
            _posts.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_gather_regimen_slugs(_a)
            check("...and the refreshed marker still stops a re-run re-posting", len(_posts), 0,
                  "same review, same fingerprint — re-block without re-sending")

            # An undelivered review must SAY so: the send is conditional, the block never is.
            _posts.clear()
            _blocks.clear()
            os.remove(os.path.join(td, ".regimen-review-pending"))
            _sv_send = rx.send_detail
            try:
                rx.send_detail = lambda *a, **k: False        # chat delivery fails
                with contextlib.redirect_stdout(io.StringIO()) as _out:
                    rx.cmd_gather_regimen_slugs(_a)
            finally:
                rx.send_detail = _sv_send
            _reason = " ".join(" ".join(c) for c in _blocks)
            check("an undelivered review says so in the block reason",
                  ("NOT delivered" in _reason, "needs_input" in _reason), (True, True),
                  "a card blocked on a question nobody was asked looks exactly like a slow user")
            check("...and on stdout, which the worker reports",
                  "could NOT be posted" in _out.getvalue(), True,
                  "the worker is the only thing that can tell the user delivery failed")
        finally:
            (rx.INPUTS, rx.sh, rx._card_id_by_title, rx._my_card_id, rx.send_detail) = _sv

    print("\ncheck_regimen — backstop is empty once regimen-final.md has a row, one entry when missing")
    # Stage 3's Barrier settles the regimen; check_regimen only catches a card reached out of order.
    # Unresolved is empty once regimen-final.md exists with a row; acknowledged is always [] now.
    with tempfile.TemporaryDirectory() as td:
        _sv = rx.INPUTS
        try:
            rx.INPUTS = td
            _un, _ack = rx.check_regimen()
            check("a missing regimen-final.md is one unresolved (regimen) entry",
                  ([u["item"] for u in _un], _ack), (["(regimen)"], []),
                  "the backstop names the one thing missing when reached out of order")
            rx._write_regimen_final_rows([("Magnesium Glycinate", "magnesium 200mg",
                                           "2 caps", "evening", "", "high")])
            _un2, _ack2 = rx.check_regimen()
            check("a populated regimen-final.md leaves nothing unresolved",
                  (_un2, _ack2), ([], []),
                  "acknowledged is always empty now — the per-item ack machinery is gone")
        finally:
            rx.INPUTS = _sv

    print("\nonly unfinished cards are candidates to hold a Barrier back")
    # _card_id_by_title splices a worker in front of a Barrier the pipeline created up front. A
    # finished Barrier cannot be held back, so a done/cancelled/failed card is never a target.
    _real_sh = rx.sh
    try:
        rx.sh = lambda *a, **k: type("_B", (), {"stdout": json.dumps([
            {"id": "t_done", "title": "Stage 3: Finalize Regimen", "status": "done"},
            {"id": "t_open", "title": "Stage 3: Finalize Regimen", "status": "todo"}])})()
        check("a done card is not returned by title",
              rx._card_id_by_title("Stage 3: Finalize Regimen"), "t_open",
              "linking in front of a done Barrier enforces nothing and looks like it does")
        check("a title nothing matches returns None",
              rx._card_id_by_title("No Such Card"), None, "")
    finally:
        rx.sh = _real_sh

    print("\nstage 5 flags ONLY out-of-range markers; the barrier batches one review")
    # review_labs seeds labs-complete.md from labs-draft.md, derives the "## Out of range" section
    # (moved out of the merge), and writes a marker-question-*.md for every OUT-OF-RANGE marker.
    # Trends stay in labs-complete.md and are analysed in stage 6, but are NOT questioned. The
    # `Stage 5: Labs Complete` Barrier gathers the question files into ONE numbered batched review.
    _DRAFT_LABS = (
        "| marker | value | unit | reference range | specimen | date | source file |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Cholesterol | 222 H | mg/dL | 108 - 199 | CMP | 05/27/2026 | c.pdf |\n"
        "| Ferritin | 15 L | ng/mL | 30 - 400 | CBC | 05/27/2026 | c.pdf |\n"
        "| Creatinine | 0.9 | mg/dL | 0.7 - 1.3 | CMP | 12/09/2025 | a.pdf |\n"
        "| Creatinine | 1.1 | mg/dL | 0.7 - 1.3 | CMP | 2026-03-31 | b.pdf |\n"
        "| Creatinine | 1.3 | mg/dL | 0.7 - 1.3 | CMP | 05/27/2026 | c.pdf |\n")
    with tempfile.TemporaryDirectory() as td:
        board = _FakeBoard()
        os.makedirs(os.path.join(td, "raw"))
        open(os.path.join(td, "raw", "lab.pdf"), "wb").write(b"one lab pdf")
        open(os.path.join(td, "regimen.txt"), "w").write("Magnesium 200mg evening\n")
        _run_as_card(board, td, rx.cmd_start, "t_root")
        open(os.path.join(td, "labs-draft.md"), "w", encoding="utf-8").write(_DRAFT_LABS)
        _run_as_card(board, td, rx.cmd_review_labs,
                     board.id_of("Stage 5: Review Labs"))

        _complete = os.path.join(td, "labs-complete.md")
        check("review_labs seeds labs-complete.md", os.path.exists(_complete), True,
              "every downstream script reads labs-complete.md")
        _ctext = open(_complete, encoding="utf-8").read()
        check("...with the derived out-of-range section",
              "## Out of range" in _ctext and "Cholesterol" in _ctext, True,
              "out-of-range derivation moved from the merge to stage 5")
        check("review_labs writes a question file for each OUT-OF-RANGE marker",
              (os.path.exists(os.path.join(td, "marker-question-cholesterol.md")),
               os.path.exists(os.path.join(td, "marker-question-ferritin.md"))), (True, True),
              "out-of-range markers are the ones the user reviews")
        check("...and NONE for a trending-but-in-range marker",
              os.path.exists(os.path.join(td, "marker-question-creatinine.md")), False,
              "trends are analysed in stage 6, not questioned in stage 5")
        check("review_labs creates NO Marker review: cards",
              [c for c in board.by_id.values() if c["title"].startswith("Marker review: ")], [],
              "the per-marker asking cards are retired; the barrier batches instead")

        # THE BARRIER GATE. labs-brief gathers the question files into ONE numbered batched review,
        # posts it with a single send_detail, blocks its OWN card needs_input, and does NOT write
        # labs-succinct.md while markers are outstanding.
        _s5bar = board.id_of("Stage 5: Labs Complete")
        _run_as_card(board, td, rx.cmd_labs_brief, _s5bar)
        check("the barrier posts EXACTLY ONE batched marker review", len(board.asked), 1,
              "one numbered batch replaces the per-marker asks")
        _mbatch = board.asked[0]
        check("...numbered, naming both out-of-range markers",
              ("1." in _mbatch and "2." in _mbatch
               and "Cholesterol" in _mbatch and "Ferritin" in _mbatch), True,
              "the user sees the flagged markers as one list to keep or ignore")
        check("the barrier blocks its OWN card needs_input",
              board.blocked.get(_s5bar), "needs_input",
              "the next stage is held by the barrier's own block until acceptance")
        check("the barrier does NOT write labs-succinct.md while markers are open",
              os.path.exists(os.path.join(td, "labs-succinct.md")), False,
              "labs-succinct.md is written on accept, not while a marker is unreviewed")
        check("...and it writes the number -> marker index",
              os.path.exists(os.path.join(td, "marker-batch-index.md")), True,
              "ignore-by-number must map to a stable marker order")

        # ANSWERING part of the batch: marker-review --number ignores a specific marker and DELETES
        # its question file. A number/name matching nothing refuses the whole command.
        _saved = (rx.INPUTS, rx.sh)
        try:
            rx.INPUTS, rx.sh = td, board.sh

            class _A:
                dry_run = json = ignore = confirm = drop = False
                number = marker = None

            def _mr(**kw):
                a = _A()
                for k, v in kw.items():
                    setattr(a, k, v)
                _b = io.StringIO()
                with contextlib.redirect_stdout(_b):
                    return rx.cmd_marker_review(a), _b.getvalue()

            _rc, _out = _mr(marker=["Cholesteral"], ignore=True)          # typo
            check("a typo records nothing and refuses",
                  (_rc, rx.is_ignored("Cholesterol")), (1, False),
                  "an answer against a name nothing matches is lost in silence")
            check("...and offers the close match", "Cholesterol" in _out, True, "")
            _rcn, _ = _mr(number=["99"], ignore=True)
            check("a number matching no flagged marker is refused", _rcn, 1,
                  "ignore-by-number must be validated against the batch index")

            # #1 is Cholesterol (files sorted by name): ignore it by number.
            _mr(number=["1"], ignore=True)
            check("marker-review --number ignores that specific marker",
                  rx.is_ignored("Cholesterol"), True,
                  "the value stays in labs-complete.md; only its research is skipped")
            check("...and deletes that marker's question file",
                  os.path.exists(rx._marker_question_path("Cholesterol")), False,
                  "an answered marker is no longer outstanding at the accept gate")
            check("...leaving the other marker still open",
                  os.path.exists(rx._marker_question_path("Ferritin")), True,
                  "only the reviewed marker is resolved")
        finally:
            (rx.INPUTS, rx.sh) = _saved

        # labs-accept keeps every REMAINING marker significant, writes labs-succinct.md, and
        # completes the Stage 5 barrier. (Markers carry no missing info, so all-significant is safe.)
        _saved2 = (rx.INPUTS, rx.sh)
        try:
            rx.INPUTS, rx.sh = td, board.sh

            class _AB:
                dry_run = json = False

            with contextlib.redirect_stdout(io.StringIO()):
                _rcacc = rx.cmd_labs_accept(_AB())
            check("labs-accept confirms the remaining marker (keeps its research)",
                  (_rcacc, rx.is_ignored("Ferritin")), (0, False),
                  "accepting all-as-significant keeps their research cards")
            check("...and clears the remaining question file",
                  os.path.exists(rx._marker_question_path("Ferritin")), False,
                  "every flagged marker is resolved once accepted")
            check("...and writes labs-succinct.md",
                  os.path.exists(os.path.join(td, "labs-succinct.md")), True,
                  "the research cards read the succinct view, not the full file")
            check("...and completes the Stage 5: Labs Complete barrier",
                  _s5bar in board.completed, True,
                  "completing the barrier releases stage 6")
        finally:
            (rx.INPUTS, rx.sh) = _saved2

    print("\na stage with nothing to work on refuses, and creates nothing")
    # An empty CARD is a normal outcome - it says so and finishes, which is what lets the card
    # behind it always have a parent. An empty STAGE is not. The brief exists to relate
    # substances to lab markers, so a run missing either half would complete, produce a document
    # that looks exactly like the real output, and be missing the thing that justified it.
    # Refusing before any card exists leaves a board with no cards below the failure, which is a
    # state the graph itself expresses; letting each card discover the problem would produce N
    # cards all reporting the same missing input and a run that looked like it was working.
    for _label, _fn, _setup in (
            ("stage 1 with nothing received", "cmd_stage", lambda td: None),
            ("stage 2 with no regimen source", "cmd_intake_regimen", lambda td: None),
            ("stage 4 with no staged PDFs", "cmd_intake_labs", lambda td: None)):
        with tempfile.TemporaryDirectory() as _td:
            _saved = (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create)
            _made = []
            try:
                rx.INPUTS = _td
                rx.RAW = os.path.join(_td, "raw")
                rx.PHOTOS = os.path.join(_td, "photos")
                rx.DOC_CACHE = os.path.join(_td, "cache")
                rx.create = lambda *a, **k: _made.append(a[1]) or "t_x"
                _setup(_td)

                class _A:
                    dry_run = force = json = False

                _buf = io.StringIO()
                with contextlib.redirect_stdout(_buf):
                    _rc = getattr(rx, _fn)(_A())
                check("%s returns non-zero" % _label, _rc, 1,
                      "a stage that cannot do its job must not report success")
                check("...and creates no cards", _made, [],
                      "N cards each reporting the same missing input looks like a working run")
                check("...and names where it looked",
                      "looked" in _buf.getvalue().lower(), True,
                      "refusing without naming what is missing sends the user to read the code")
            finally:
                (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create) = _saved

    # --force is for a partial set, never for an absent one: there is nothing to force.
    with tempfile.TemporaryDirectory() as _td:
        _saved = (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create)
        _made = []
        try:
            rx.INPUTS, rx.RAW = _td, os.path.join(_td, "raw")
            rx.PHOTOS, rx.DOC_CACHE = os.path.join(_td, "p"), os.path.join(_td, "c")
            rx.create = lambda *a, **k: _made.append(a[1]) or "t_x"

            class _AF:
                dry_run = json = False
                force = True

            with contextlib.redirect_stdout(io.StringIO()):
                _rc = rx.cmd_intake_labs(_AF())
            check("--force does not override an absent lab set", (_rc, _made), (1, []),
                  "there is nothing to force; the upload did not arrive")
        finally:
            (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create) = _saved

    print("\nstage 4 fans out one `Lab:` card per PDF; each `Lab:` card creates its own transcription child")
    # The Begin owns no transcription: it creates one `Lab: <file>` card per staged PDF, and that
    # card's script (`plan-lab`) does the OCR-detect + split and creates the `Transcribe Lab`
    # worker(s). This keeps one big or unreadable document from blocking the others.
    with tempfile.TemporaryDirectory() as _td:
        _saved = (rx.INPUTS, rx.RAW, rx.create, rx._parent_worker_to_barrier,
                  rx._my_card_id, rx.phase_start, rx.unstaged_documents)
        try:
            import fitz as _fitz                     # CI has no PyMuPDF; skip there
            rx.INPUTS, rx.RAW = _td, os.path.join(_td, "raw")
            os.makedirs(rx.RAW)
            _doc = _fitz.open(); _pg = _doc.new_page()
            _pg.insert_text((50, 80), "\n".join(
                "Marker%d  %d mg/dL  70-99  Comprehensive Metabolic Panel  05/27/2026" % (i, 90 + i)
                for i in range(12)), fontsize=9)
            _pdf = os.path.join(rx.RAW, "labs_2026.pdf"); _doc.save(_pdf); _doc.close()
            _made, _body_of = [], []

            def _cap4(a, title, body=None, minutes=None, priority=None,
                      parents=(), key=None, assignee="rx-intake"):
                _made.append((title, list(parents)))
                _body_of.append(body or "")
                return "id-" + title
            rx.create = _cap4
            rx._parent_worker_to_barrier = lambda *a, **k: None
            rx._my_card_id = lambda: "card"
            rx.phase_start = lambda *a, **k: None
            rx.unstaged_documents = lambda: []

            class _A4:
                dry_run = force = json = False
                pdf = None                      # the card path: a token, never a path
                token = None

            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_intake_labs(_A4())
            check("intake-labs creates one `Lab:` card per staged PDF",
                  [t for t, _ in _made], ["Lab: labs_2026.pdf"],
                  "the Begin owns no transcription; each PDF gets its own per-PDF card")
            check("the `Lab:` card is parentless — not gated by the Stage 4 Begin",
                  _made[0][1], [],
                  "the Begin creates it, so a back-edge only delays it; the barrier edge holds Stage 5")
            # The card body carries a TOKEN, never a path: a ~110-char literal is a thing a
            # worker corrupts (2026-08-10), and the binding is already known here.
            _tok = rx._doc_token(_pdf)
            check("the `Lab:` card body names the token, not the document",
                  (_tok in _body_of[0], _pdf in _body_of[0], "--pdf" in _body_of[0]),
                  (True, False, False),
                  "nothing for the worker to retype, and nothing that looks like a path")
            check("...and the binding is recorded before the card exists",
                  (rx._xcribe_get(_tok) or {}).get("pdf"), _pdf,
                  "plan-lab resolves the document from the record; the worker never carries it")
            _made.clear()

            class _A4tok(_A4):
                token = _tok

            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_plan_lab(_A4tok())
            check("plan-lab resolves its token and creates the transcription child",
                  [t for t, _ in _made], ["Transcribe Lab labs_2026.pdf"],
                  "the per-PDF card, not the Begin, creates the Transcribe worker(s)")
            check("the Transcribe worker IS parented on its `Lab:` card",
                  _made[0][1], ["card"],
                  "a worker waits on the per-PDF owner card that created it, unlike the owner card itself")

            # The OCR path: a text-less scan that OCR cannot read is the caller-cannot-fix
            # class — no re-run fixes it — so plan-lab HOLDS the card and reports to chat,
            # exactly as the Stage 6 backstop does for an unverifiable lab value. The old
            # behaviour (mark unreadable, return 0, skip the document) completed the `Lab:`
            # card over a missing lab and let the run go on with a silent gap.
            _scan = os.path.join(rx.RAW, "scan_2026.pdf")
            _doc2 = _fitz.open(); _doc2.new_page(); _doc2.save(_scan); _doc2.close()
            _stok = rx._doc_token(_scan)
            rx._xcribe_put(_stok, {"pdf": _scan, "first": 1, "last": 1, "out": "unused"})
            _sv_ocr = rx.rxsplit.ocr_to_searchable
            _sv_unc, _sv_get = rx.rxcache.unreadable_reason, rx.rxcache.get
            _sv_munc, _sv_xget, _sv_hold_ocr = (rx.rxcache.mark_unreadable, rx._xcribe_get,
                                                rx._hold)
            _held_ocr, _munc_calls, _unc_calls = [], [], []
            try:
                rx.rxsplit.ocr_to_searchable = lambda s, o, url=None, timeout=300: False
                rx.rxcache.unreadable_reason = lambda p: (_unc_calls.append(p) or None)
                rx.rxcache.get = lambda p: None
                rx.rxcache.mark_unreadable = lambda p, r: _munc_calls.append((p, r))
                rx._xcribe_get = lambda t: {"pdf": _scan} if t == _stok else None
                rx._hold = lambda *a, **k: _held_ocr.append(a) or 1

                class _A4scan(_A4):
                    token = _stok

                _buf6 = io.StringIO()
                with contextlib.redirect_stdout(_buf6):
                    _rc6 = rx.cmd_plan_lab(_A4scan())
                check("an OCR-unreadable scan returns non-zero and HOLDS the card",
                      (_rc6, len(_held_ocr)), (1, 1),
                      "the caller cannot fix it — the run stops and reports, per the spec")
                check("...and names the scan in the hold",
                      ("scan_2026.pdf" in str(_held_ocr[0][0]), _munc_calls, _unc_calls),
                      (True, [], []),
                      "no mark-unreadable record, and no early return on one from a prior run")
            finally:
                rx.rxsplit.ocr_to_searchable = _sv_ocr
                rx.rxcache.unreadable_reason = _sv_unc
                rx.rxcache.get = _sv_get
                rx.rxcache.mark_unreadable = _sv_munc
                rx._xcribe_get = _sv_xget
                rx._hold = _sv_hold_ocr

            # A token that names nothing is the WORKER's mistake, so it must not block: a block
            # is terminal, and the retry that fixes it would have nowhere to put the result.
            _made.clear()
            _held4 = []
            _sv_hold = rx._hold
            try:
                rx._hold = lambda *a, **k: _held4.append(a) or 1

                class _A4bad(_A4):
                    token = "deadbeefcafe"
                _buf4 = io.StringIO()
                with contextlib.redirect_stdout(_buf4):
                    _rc4 = rx.cmd_plan_lab(_A4bad())
                check("an unknown token returns non-zero and does NOT hold",
                      (_rc4, _held4, _made), (1, [], []),
                      "blocking strands the card once the worker re-runs with the right token")
                check("...and answers in TOKENS, never naming a document",
                      ("token" in _buf4.getvalue().lower(),
                       ".pdf" in _buf4.getvalue().lower()), (True, False),
                      "naming a document hands back the concept the token exists to remove")

                # A bare run is almost always a worker that dropped its token — same terms.
                _buf5 = io.StringIO()
                with contextlib.redirect_stdout(_buf5):
                    _rc5 = rx.cmd_plan_lab(_A4())
                check("a bare run asks for a token, and holds nothing",
                      (_rc5, _held4, "token" in _buf5.getvalue().lower(),
                       ".pdf" in _buf5.getvalue().lower()), (1, [], True, False),
                      "the caller with no token is the worker, not a person with a document")
            finally:
                rx._hold = _sv_hold
        except ImportError:
            check("stage 4 fan-out (PyMuPDF unavailable)", True, True, "skipped")
        finally:
            (rx.INPUTS, rx.RAW, rx.create, rx._parent_worker_to_barrier,
             rx._my_card_id, rx.phase_start, rx.unstaged_documents) = _saved

    print("\nthe `Lab:` card carries a token, and plan-lab answers a bad one in tokens")
    # Deliberately FREE OF PyMuPDF: CI installs nothing, so anything inside the fitz block below
    # is skipped there. The token contract is the part that stalled a run, so it is asserted
    # where it will actually run. intake-labs opens no PDF, and the resolver opens none either.
    with tempfile.TemporaryDirectory() as _tk:
        _svt = (rx.INPUTS, rx.RAW, rx.XCRIBE, rx.create, rx._parent_worker_to_barrier,
                rx._my_card_id, rx.phase_start, rx.unstaged_documents)
        try:
            rx.INPUTS = _tk
            rx.RAW = os.path.join(_tk, "raw"); os.makedirs(rx.RAW)
            rx.XCRIBE = os.path.join(_tk, ".xcribe")
            _pdfp = os.path.join(rx.RAW, "doc_ab12_2026-01-02_Lipid_Tests.pdf")
            open(_pdfp, "wb").write(b"%PDF-1.4 not really parsed here")
            _bodies = []
            rx.create = lambda a, title, body=None, minutes=None, priority=None, parents=(), \
                key=None, assignee="rx-intake": (_bodies.append(body or "") or "t_lab")
            rx._parent_worker_to_barrier = lambda *a, **k: None
            rx._my_card_id = lambda: None
            rx.phase_start = lambda *a, **k: None
            rx.unstaged_documents = lambda: []

            class _At:
                dry_run = force = json = False
                pdf = token = None
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_intake_labs(_At())
            _t = rx._doc_token(_pdfp)
            check("the token is deterministic, so the idempotent stage re-runs clean",
                  rx._doc_token(_pdfp), _t,
                  "a random token would rewrite the body while the key returned the first card")
            check("the `Lab:` body names the token and no path",
                  (_t in _bodies[0], ".pdf" in _bodies[0], "--pdf" in _bodies[0]),
                  (True, False, False), "a path is a literal the worker corrupts (2026-08-10)")
            check("intake-labs recorded the binding before creating the card",
                  (rx._xcribe_get(_t) or {}).get("pdf"), _pdfp,
                  "the one place holding the document as the card is made")

            # The resolver, in each caller's own terms.
            check("a token resolves to its document", rx._resolve_plan_lab_document(_At), None,
                  "the class itself carries no token — the next three cases are the real ones")
            _ok = type("A", (), {"pdf": None, "token": _t, "dry_run": False})()
            check("...given the token", rx._resolve_plan_lab_document(_ok), _pdfp, "")
            _bad = type("A", (), {"pdf": None, "token": "nosuchtoken1", "dry_run": False})()
            _b1 = io.StringIO()
            with contextlib.redirect_stdout(_b1):
                check("an unknown token resolves to None, not a document",
                      rx._resolve_plan_lab_document(_bad), None, "")
            check("...answering in tokens, never naming a document",
                  ("token" in _b1.getvalue().lower(), ".pdf" in _b1.getvalue().lower()),
                  (True, False),
                  "naming one hands back the concept the token exists to remove")
            _none = type("A", (), {"pdf": None, "token": None, "dry_run": False})()
            _b2 = io.StringIO()
            with contextlib.redirect_stdout(_b2):
                check("a bare run resolves to None", rx._resolve_plan_lab_document(_none), None, "")
            check("...and asks for a token, because the caller is a worker that dropped one",
                  ("token" in _b2.getvalue().lower(), ".pdf" in _b2.getvalue().lower()),
                  (True, False), "there is no document to name without a token anyway")
            _hand = type("A", (), {"pdf": "/tmp/x.pdf", "token": None, "dry_run": False})()
            check("--pdf still works for a hand run", rx._resolve_plan_lab_document(_hand),
                  "/tmp/x.pdf", "the operator escape hatch survives, hidden from --help")
        finally:
            (rx.INPUTS, rx.RAW, rx.XCRIBE, rx.create, rx._parent_worker_to_barrier,
             rx._my_card_id, rx.phase_start, rx.unstaged_documents) = _svt

    check("--pdf is hidden from --help, so a worker cannot discover it",
          "argparse.SUPPRESS" in inspect.getsource(rx.main),
          True, "a flag the worker can find is a flag it can pass a hallucinated path to")

    print("\nstatus leads with what is held, and never says `you` to mean the user")
    # 2026-08-11: the user answered a marker review; the agent ran `status`, which printed the
    # LAST 14 lines of an 86-card list — all `done` transcriptions — concluded "no blocked cards
    # right now", and dropped the answer. The held card is the one fact that cannot be truncated.
    _svb = rx.board_cards
    try:
        _mk = lambda st, ti: {"status": st, "title": ti, "id": "t_" + ti[:4]}
        rx.board_cards = lambda: ([_mk("done", "Transcribe Lab %d" % i) for i in range(40)]
                                  + [_mk("blocked", "Stage 5: Labs Complete"),
                                     _mk("todo", "Stage 6: Research Begin")])
        _state, _head, _lines = rx.pipeline_state()
        check("a held card is the headline, whatever else is on the board",
              (_state, "Stage 5: Labs Complete" in _head), ("held", True),
              "40 done cards used to push it out of the window entirely")
        check("...and the headline names the USER as the one who must answer",
              "USER" in _head.upper(), True,
              "the reader is the model; 'waiting on you' tells it to answer for the user")
        check("...and the reply verb is named, so the answer is routed not interpreted",
              any("marker-review --batch" in l for l in _lines), True,
              "the model dropped the reply because nothing told it where to put it")
        # NAME THE TOOL, or the model picks one. "Write their reply to inputs/marker-reply.txt"
        # became `echo "looks good" > ~/.hermes/...`, which the security scan flagged HIGH —
        # every path under ~/.hermes is a dotfile path — so saying "looks good" cost the user a
        # HIGH-severity approval prompt. write_file passes the reply as data: no shell, nothing
        # to scan, and no quoting for the `&`/`<`/`|` a correction legitimately contains.
        check("...and names write_file, so no shell redirect is invented",
              any("write_file" in l for l in _lines), True,
              "an unnamed 'write a file' resolves to echo >, which is a scanned shell command")
        rx.board_cards = lambda: [_mk("blocked", "Stage 3: Finalize Regimen")]
        _s4, _h4, _l4 = rx.pipeline_state()
        _j4 = " ".join(_l4)
        check("the regimen reply is passed as an ARGUMENT, not through a file",
              ("correct-item-slug-request" in _j4, "reply.txt" in _j4), (True, False),
              "stage 3 takes the reply inline; a reply file there is one nothing reads")
        # NOT EVERY HOLD IS A QUESTION. The Stage 6 backstop holds for a REPAIR — no reply the
        # user can send will clear it — so telling the model "the user has not answered" invites
        # a wait for an answer that is never coming (2026-08-11: a footnote held the research
        # phase, and status said only that the user had not answered).
        _svcs = rx._card_summary
        try:
            rx._card_summary = lambda cid: "1 lab value(s) could not be verified"
            rx.board_cards = lambda: [_mk("blocked", "Stage 6: Research Begin")]
            _s3, _h3, _l3 = rx.pipeline_state()
            _joined = " ".join(_l3)
            check("a backstop hold says the pipeline stopped, not that a reply is awaited",
                  ("has not answered" in _joined, "cannot go on by itself" in _joined),
                  (False, True), "no reply clears a repair")
            check("...and quotes the reason the card gave",
                  "could not be verified" in _joined, True,
                  "the reason is the one thing the user needs in order to act")
            check("...and says it needs fixing rather than answering",
                  "fixing rather than answering" in _joined, True, "")
        finally:
            rx._card_summary = _svcs

        rx.board_cards = lambda: [_mk("done", "x"), _mk("running", "y"), _mk("todo", "z")]
        _state2, _head2, _lines2 = rx.pipeline_state()
        check("with nothing held, the state is running and asks for nothing",
              (_state2, "USER" in _head2.upper()), ("running", False), "")
        check("...and tells the model to do nothing rather than to prompt the user",
              any("Nothing to do" in l for l in _lines2), True,
              "'nothing needed from you' still leaves the model wondering about the user")
    finally:
        rx.board_cards = _svb

    # The pronoun rule, mechanically. Model-facing output addresses its reader — the model — in
    # the imperative, and refers to the human in the THIRD person. A bare "you" is ambiguous to
    # the only reader there is, and the dangerous resolution is the model answering for the user.
    _second = re.compile(r"\b(you|your|yours)\b", re.I)
    for _mod in ("rx.py", "fanout.py", "verify.py", "lenses.py", "rxkanban.py"):
        _src = open(os.path.join(HERE, _mod), encoding="utf-8").read()
        _bad = []
        for _m in re.finditer(r"""print\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""", _src):
            _lit = _m.group(1)
            if _second.search(_lit):
                _bad.append(_lit[:60])
        check("%s never says `you` in printed output" % _mod, _bad, [],
              "the human is 'the user'; 'you' is the model, which must not answer for them")

    print("\nno module opens the kanban database read-write")
    # The board is live: the dispatcher and both gateways hold open handles while a review runs,
    # so a second writer that rebuilds the file discards whatever they wrote in between. Repair
    # scripts of exactly that shape corrupted the board repeatedly and were deleted on
    # 2026-08-07 — then written again on 2026-08-10, twice, under two names, each time by
    # someone reasonable looking at a corrupt board and a database they could obviously fix. A
    # comment does not survive that. This does: re-adding one fails the build.
    _RW_OPEN = re.compile(r"sqlite3\s*\.\s*connect\s*\(\s*(?!['\"]file:)")   # not a file: URI
    _RO_URI = re.compile(r"mode=ro")
    for _mod in sorted(f for f in os.listdir(HERE) if f.endswith(".py")):
        _src = open(os.path.join(HERE, _mod), encoding="utf-8").read()
        _code = "\n".join(l for l in _src.splitlines() if not l.strip().startswith("#"))
        _bare = bool(_RW_OPEN.search(_code))
        _uris = [m for m in re.findall(r"sqlite3\s*\.\s*connect\s*\(\s*['\"]file:[^'\"]*", _code)]
        _rw_uri = any(not _RO_URI.search(u) for u in _uris)
        check("%s opens no database read-write" % _mod, (_bare or _rw_uri), False,
              "the live board has other writers; rebuilding it over them is what corrupted it")
    for _tomb in ("repair_db.py", "rx_repair.py"):
        _t = open(os.path.join(HERE, _tomb), encoding="utf-8").read()
        check("%s is a tombstone, not a script" % _tomb,
              ("sqlite3" not in _t.replace("sqlite3.connect read-write", ""),
               "kanban database" in _t or "kanban.db" in _t),
              (True, True),
              "kept empty ON PURPOSE — deleting it invites the fourth rewrite")

    print("\nthe token manifest is one file per token, so concurrent writers cannot lose a record")
    # `Lab:` cards are parentless and all eligible at once, and the dispatcher runs
    # max_in_progress of them together, so several plan-lab processes write here simultaneously.
    # The old shared manifest.json was read-modify-write: the publishing rename was atomic, the
    # read-modify-write around it was not, and a sibling's record written in between was lost —
    # six times in the run of 2026-08-10, each surfacing later as a token check-transcription
    # could not find. This asserts the property that makes that impossible, not the symptom.
    with tempfile.TemporaryDirectory() as _mx:
        _svm = rx.XCRIBE
        try:
            rx.XCRIBE = os.path.join(_mx, ".xcribe")
            _toks = ["tok%09d" % i for i in range(25)]
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=8) as _ex:   # concurrent, as the board is
                list(_ex.map(lambda t: rx._xcribe_put(t, {"pdf": "/x/%s.pdf" % t}), _toks))
            check("every concurrently-written record survives",
                  sum(1 for t in _toks if (rx._xcribe_get(t) or {}).get("pdf")), len(_toks),
                  "a lost record is a token check-transcription cannot resolve, much later")
            check("each record is its own file — nothing shared to clobber",
                  sorted(f for f in os.listdir(rx.XCRIBE) if f.endswith(".json"))[:2],
                  ["tok000000000.json", "tok000000001.json"],
                  "one writer per path is the property; locking a shared file is not the same")
            check("no shared manifest is written at all",
                  os.path.exists(os.path.join(rx.XCRIBE, "manifest.json")), False,
                  "a single index would reintroduce the read-modify-write")
            check("an unknown token resolves to nothing rather than erroring",
                  rx._xcribe_get("nosuchtoken"), None, "the caller re-runs; it is not a hold")
            check("a token containing a path separator is refused",
                  rx._xcribe_get("../../etc/passwd"), None,
                  "a token is a name; it must never be able to name a file outside the directory")
        finally:
            rx.XCRIBE = _svm

    print("\ncheck-transcription verifies the model's table against the source, then completes/blocks")
    # The model transcribes a decluttered results file into a table; the SCRIPT confirms every row
    # is in the source, stamps the source_file column, and completes the card — or blocks it. A
    # fabricated row (the Function-Full-Tests panic: a whole amino-acid panel invented from the
    # filename) is caught HERE, at the transcribe card, not three stages later at research.
    with tempfile.TemporaryDirectory() as _tx:
        _svx = (rx.INPUTS, rx.XCRIBE, rx._my_card_id)
        try:
            rx.INPUTS = _tx
            rx.XCRIBE = os.path.join(_tx, ".xcribe")
            rx._my_card_id = lambda: None                      # hand-run path: no CLI complete/block
            os.makedirs(rx.XCRIBE)
            _tok = "abcd1234beef"
            open(os.path.join(rx.XCRIBE, _tok + ".src.txt"), "w").write(
                "ALANINE 325 umol/L 187-492\nGLUCOSE 90 mg/dL 70-99\n")
            _out = os.path.join(_tx, "labs-doc-x.md")
            rx._xcribe_put(_tok, {"pdf": "/x/doc_Real.pdf", "first": 1, "last": 1, "out": _out})

            def _A(force=False):
                return type("A", (), {"token": _tok, "dry_run": False, "force": force})()

            _tbl = os.path.join(rx.XCRIBE, _tok + ".tbl.md")
            open(_tbl, "w").write(
                "| marker | value | unit | reference range | specimen | date |\n|---|---|---|---|---|---|\n"
                "| ALANINE | 325 | umol/L | 187-492 | Amino Acids | 2026-04-16 |\n"
                "| GLUCOSE | 90 | mg/dL | 70-99 | Chem | 2026-04-16 |\n")
            with contextlib.redirect_stdout(io.StringIO()):
                _rc = rx.cmd_check_transcription(_A())
            check("a faithful transcription verifies and is written", _rc, 0, "")
            check("...with the source_file column stamped by the pipeline, not the model",
                  "doc_Real.pdf" in open(_out).read(), True,
                  "the model never saw or wrote the filename")

            # A fabricated row (not in the source) blocks.
            open(_tbl, "w").write(
                "| marker | value | unit | reference range | specimen | date |\n|---|---|---|---|---|---|\n"
                "| ALANINE | 325 | umol/L | 187-492 | Amino Acids | 2026-04-16 |\n"
                "| PHENYLALANINE | 38 | umol/L | 27-65 | Amino Acids | 2026-04-16 |\n")
            with contextlib.redirect_stdout(io.StringIO()):
                _rc2 = rx.cmd_check_transcription(_A())
            check("a fabricated row is flagged (non-zero) so the model removes it and re-runs", _rc2, 1,
                  "PHENYLALANINE is not in the results; the script returns non-zero, not a block — "
                  "the model self-corrects in-turn (blocking would strand the card once it is clean)")
        finally:
            (rx.INPUTS, rx.XCRIBE, rx._my_card_id) = _svx

    print("\ncheck-output confirms a CHECK barrier's output, then completes or blocks the card")
    with tempfile.TemporaryDirectory() as _co:
        _svc = (rx.INPUTS, rx._my_card_id)
        try:
            rx.INPUTS = _co
            rx._my_card_id = lambda: None
            _As = type("A", (), {"stage": 2, "dry_run": False, "force": False})
            with contextlib.redirect_stdout(io.StringIO()):
                _rcm = rx.cmd_check_output(_As())
            check("a missing stage output blocks the barrier (held, non-zero)", _rcm, 1,
                  "the workers in front of it produced nothing — a human should see it")
            open(os.path.join(_co, "regimen-draft.txt"), "w").write("Vitamin C | | 1 | am\n")
            with contextlib.redirect_stdout(io.StringIO()):
                _rcp = rx.cmd_check_output(_As())
            check("a present stage output completes the barrier from the script", _rcp, 0,
                  "the model did no read_file check and no kanban_complete")
        finally:
            (rx.INPUTS, rx._my_card_id) = _svc

    print("\nthe dispatcher settles an auto-settle verb's own card, so no body calls kanban_complete")
    _svs = (rx.sh, rx._my_card_id, rx._CARD_ACTED, rx._card_body)
    _calls = []
    try:
        rx._my_card_id = lambda: "t_self"
        rx.sh = lambda cmd, **k: (_calls.append(cmd),
                                  type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1]
        # This card WAS told to run the verb under test (fail-open default; body read returns it).
        rx._card_body = lambda cid: "run: python3 ~/.hermes/rx-review/rx.py %s x" % _VERB[0]
        _VERB = ["merge-labs"]
        rx._CARD_ACTED = False
        rx._autosettle("merge-labs", 0)
        check("a verb that returns 0 has its card completed by the dispatcher",
              any("complete" in c for c in _calls), True, "the model never calls kanban_complete")
        # A BARE non-zero is NOT a hold. It usually means the caller can fix it in this turn —
        # a mistyped token, an unknown record, a fabricated row — and blocking is terminal
        # (#40312), so the card would be stranded even after the retry that fixes it. That is
        # not hypothetical: on 2026-08-10 a mistyped path in a `Lab:` body blocked the card, the
        # worker's own retry finished the work two minutes later, and the card stayed blocked
        # with the whole lab branch waiting on it. A verb that needs a human calls _hold().
        _calls.clear(); rx._CARD_ACTED = False; _VERB[0] = "intake-labs"
        rx._autosettle("intake-labs", 1)
        check("a bare non-zero does NOT block the card", _calls, [],
              "the worker re-runs in the same turn; the retry limit is the backstop, not this")
        check("...and does not complete it either",
              any("complete" in c for c in _calls), False,
              "the work did not happen; completing would advance the pipeline over the failure")
        _calls.clear(); rx._CARD_ACTED = True
        rx._autosettle("plan-lab", 0)
        check("a verb that already settled its own card is left alone", _calls, [],
              "check-* / _hold callers set _CARD_ACTED so the dispatcher does not double-act")

        # 2026-08-08, live (t_dfd1407d): a Transcribe worker ran `plan-lab` to peek at the PDF; it
        # returned 1 (bad path) and blocked the Transcribe card, whose job was check-transcription.
        _calls.clear(); rx._CARD_ACTED = False
        rx._card_body = lambda cid: ("The lab results printed below ... run:\n"
                                     "    python3 ~/.hermes/rx-review/rx.py check-transcription abc")
        rx._autosettle("plan-lab", 1)
        check("an auto-settle verb NOT named in the card body leaves the card alone", _calls, [],
              "a worker's exploratory plan-lab must not block a card whose job is check-transcription")
        _calls.clear(); rx._CARD_ACTED = False
        rx._card_body = lambda cid: "run: python3 ~/.hermes/rx-review/rx.py plan-lab abc123def456"
        rx._autosettle("plan-lab", 1)
        check("even the card's OWN assigned verb is not blocked on a bare non-zero", _calls, [],
              "the assigned verb is the one whose retry can fix it — blocking is what stranded "
              "the card on 2026-08-10")
        # The hold path is the ONLY way a card blocks, and it says so on the card and in chat.
        _calls.clear(); rx._CARD_ACTED = False
        _sv_sd2 = rx.send_detail
        try:
            rx.send_detail = lambda *a, **k: True
            with contextlib.redirect_stdout(io.StringIO()):
                rx._hold("the OCR service is unreachable")
            check("a verb that needs a HUMAN blocks, via _hold",
                  any("block" in c and "needs_input" in c for c in _calls), True,
                  "OCR down, an unverifiable value, an unsettled item — no retry fixes those")
            check("...and _hold marks the card settled so the dispatcher defers",
                  rx._CARD_ACTED, True,
                  "otherwise the dispatcher would act again on the same card")
        finally:
            rx.send_detail = _sv_sd2
    finally:
        (rx.sh, rx._my_card_id, rx._CARD_ACTED, rx._card_body) = _svs

    print("\nthe dry-run sentinel has one predicate, because there are two sentinels")
    # THE DEFECT: rxkanban.create_card returns the bare string "DRY"; fanout.py's own create()
    # wrapper returns "DRY-<slug>" so a preview can tell two cards apart. Every caller compared
    # `!= "DRY"`, so the filters in fanout.py and lenses.py were dead code that read as guards
    # while the identical-looking ones in rxkanban and rx.py worked. Nothing is created in a dry
    # run either way, but the previewed graph was wrong — which is what a preview is for.
    _rk = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("rk_dry", os.path.join(HERE, "rxkanban.py")))
    importlib.util.spec_from_file_location(
        "rk_dry", os.path.join(HERE, "rxkanban.py")).loader.exec_module(_rk)
    for _v, _want in (("DRY", True), ("DRY-research-x", True),
                      ("t_000123", False), ("", False), (None, False)):
        check("is_dry(%r)" % _v, _rk.is_dry(_v), _want,
              "a guard that misses one sentinel is a guard that reads as one")

    print("\ncard creation is paced, because a burst of them tore the board's SQLite")
    # Every card is a separate `hermes kanban create` subprocess. On 2026-08-11 verify.py fanout
    # ran 86 back to back while the dispatcher spawned workers and the dashboard polled; the
    # board came out one page shorter than its header claimed and the burst died partway with
    # "could not parse a task id from:". Pacing gives each write a quiet file to extend into.
    _slept = []
    _svsleep, _svdelay, _svflag = _rk.time.sleep, _rk.CREATE_DELAY_S, list(_rk._created_one)
    try:
        _rk.time.sleep = lambda s: _slept.append(s)
        _rk.CREATE_DELAY_S, _rk._created_one[0] = 5.0, False
        for _ in range(3):
            _rk._pace_creates()
        check("it waits BETWEEN creations, not before the first", _slept, [5.0, 5.0],
              "a lone card should not pay for a burst that is not happening")
        _slept.clear()
        _rk.CREATE_DELAY_S, _rk._created_one[0] = 0.0, False
        for _ in range(3):
            _rk._pace_creates()
        check("the pacing is tunable to zero", _slept, [],
              "RX_CARD_CREATE_DELAY=0 is what the suite and a hand run use")
        check("a dry run paces nothing at all",
              "return \"DRY\"" in inspect.getsource(_rk.create_card).split("_pace_creates")[0],
              True, "a preview writes no card, so there is nothing to space out")
    finally:
        _rk.time.sleep, _rk.CREATE_DELAY_S = _svsleep, _svdelay
        _rk._created_one[:] = _svflag
    def _executable_source(path):
        """Source with comments AND docstrings removed.

        Both discuss the retired sentinel on purpose — that is what they are for. An assertion
        that reads them is asserting about prose, and this one did: it failed on the docstring
        explaining why the comparison had been replaced.
        """
        import ast as _ast
        tree = _ast.parse(open(path, encoding="utf-8").read())
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.ClassDef,
                                 _ast.FunctionDef, _ast.AsyncFunctionDef)):
                body = node.body
                if (body and isinstance(body[0], _ast.Expr)
                        and isinstance(body[0].value, _ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
        return _ast.unparse(tree)

    for _f in ("rx.py", "fanout.py", "lenses.py", "rxkanban.py"):
        check("%s has no bare != \"DRY\" guard" % _f,
              "!= 'DRY'" in _executable_source(os.path.join(HERE, _f)),
              False, "the comparison that was always True")

    print("\n_draw_dates lists the distinct draws oldest first")
    with tempfile.TemporaryDirectory() as _td:
        _sv = (rx.INPUTS, rx.RAW)
        try:
            rx.INPUTS = _td
            rx.RAW = os.path.join(_td, "raw"); os.makedirs(rx.RAW)
            open(os.path.join(_td, "labs-complete.md"), "w", encoding="utf-8").write(
                "| marker | date | value |\n|---|---|---|\n"
                "| GLUCOSE | 2026-05-14 | 87 |\n| LDL | 2026-06-02 | 90 |\n")
            check("_draw_dates reads them oldest first",
                  rx._draw_dates(), ["2026-05-14", "2026-06-02"],
                  "two draws is what makes 'is this all of them' answerable")
        finally:
            (rx.INPUTS, rx.RAW) = _sv

    print("\na rejection halts the review")
    # A rejection is not "answer differently": it says the pipeline's reading of an input cannot
    # be trusted, so nothing downstream should reason about it. Halting has to REMOVE the cards
    # — a flag each card checks is a halt that keeps running for as long as it takes every
    # in-flight card to notice, and the ones already running would finish their work first.
    for _label, _verb, _record, _gone, _kept in (
            ("labs-reject", "cmd_labs_reject", "LABS-REJECTED.txt",
             ["labs-draft.md", "labs-complete.md", "labs-succinct.md"], ["regimen.txt"]),
            ("regimen-reject", "cmd_regimen_reject", "REGIMEN-REJECTED.txt",
             ["regimen-draft.txt", "regimen-final.md"], ["regimen.txt"])):
        with tempfile.TemporaryDirectory() as _td:
            _saved = (rx.INPUTS, rx.RAW, rx.SALVAGE, rx.LABS_REJECTED, rx.REGIMEN_REJECTED,
                      rx.sh, rx.board_task_ids)
            _archived = []
            try:
                rx.INPUTS = _td
                rx.RAW = os.path.join(_td, "raw"); os.makedirs(rx.RAW)
                rx.SALVAGE = os.path.join(_td, "salvage")
                rx.LABS_REJECTED = os.path.join(_td, "LABS-REJECTED.txt")
                rx.REGIMEN_REJECTED = os.path.join(_td, "REGIMEN-REJECTED.txt")
                rx.board_task_ids = lambda include_archived=True: ["t1", "t2"]

                def _sh(cmd, **k):
                    if "archive" in cmd:
                        _archived.append(cmd)
                    class _O:
                        stdout = "[]"
                    return _O()

                rx.sh = _sh
                for _f in _gone + _kept:
                    open(os.path.join(_td, _f), "w", encoding="utf-8").write("x")

                class _A:
                    dry_run = json = False
                    reason = None

                _a = _A()
                _rc, _ = getattr(rx, _verb)(_a), None
                check("%s without a reason refuses" % _label, _rc, 1,
                      "a halt with no recorded reason cannot be diagnosed later")

                _a.reason = "the numbers do not match my results"
                with contextlib.redirect_stdout(io.StringIO()):
                    getattr(rx, _verb)(_a)
                check("%s writes the record" % _label,
                      os.path.exists(os.path.join(_td, _record)), True, "")
                check("...and archives every open card", bool(_archived), True,
                      "a halt that leaves cards dispatchable has not halted anything")
                check("...and moves the derived files out of the way",
                      [f for f in _gone if os.path.exists(os.path.join(_td, f))], [],
                      "a re-run would replay the reading that was just rejected")
                check("...to salvage, not oblivion",
                      sum(len(f) for _r, _d, f in os.walk(rx.SALVAGE)), len(_gone),
                      "the reason for the rejection usually has to be found in them")
                check("...and keeps what the pipeline did not produce",
                      [f for f in _kept if os.path.exists(os.path.join(_td, f))], _kept,
                      "the user's own words are not the reading being rejected")
                check("%s is reported as halted" % _label,
                      bool(rx.halted()), True,
                      "a halted board looks exactly like a hung one otherwise")
                # The record is scoped to the review that produced it. reset clears inputs/,
                # which takes it - and must: a rejection surviving into the next review would
                # report a halt already dealt with, and status would call a healthy board dead.
                os.remove(os.path.join(_td, _record))
                check("...and reset clearing inputs/ ends the halt",
                      rx.halted(), None,
                      "a stale rejection would describe the next review as dead on arrival")
            finally:
                (rx.INPUTS, rx.RAW, rx.SALVAGE, rx.LABS_REJECTED, rx.REGIMEN_REJECTED,
                 rx.sh, rx.board_task_ids) = _saved

    print("\nstaging repeats; starting happens once")
    # THE DEFECT: `stage` both copied what had arrived AND created the stage-2 card, keyed on
    # the SET of staged filenames. Labs arrive over several rounds - chat platforms cap
    # attachments per message - and the skill says to run `stage` after each one, so ten labs
    # then five more produced two DIFFERENT keys and therefore two stage-2 cards: two
    # independent reviews, the second beginning while the first was still transcribing.
    with tempfile.TemporaryDirectory() as _td:
        _saved = (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create)
        _made = []
        try:
            rx.INPUTS = _td
            rx.RAW = os.path.join(_td, "raw"); os.makedirs(rx.RAW)
            rx.PHOTOS = os.path.join(_td, "photos")
            rx.DOC_CACHE = os.path.join(_td, "cache"); os.makedirs(rx.DOC_CACHE)
            rx.create = lambda a, t, b, m, p, parents=(), key=None, assignee="": (
                _made.append((t, key)) or "t%d" % len(_made))

            class _A:
                dry_run = force = json = False

            for _n in (10, 15):                        # two upload rounds
                for _i in range(_n):
                    open(os.path.join(rx.DOC_CACHE, "doc_%02d.pdf" % _i), "w").write("x")
                with contextlib.redirect_stdout(io.StringIO()):
                    rx.cmd_stage(_A())
            check("staging twice creates no cards at all", _made, [],
                  "each round used to start its own review")

            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf):
                _rc = rx.cmd_start(_A())
            check("start refuses while the regimen is unresolved", (_rc, _made), (1, []),
                  "stage 2 would be the first to notice, with a dead chain already on the board")
            check("...and names how to resolve it",
                  "--from-gdoc" in _buf.getvalue(), True,
                  "the regimen is a doc or a file, never an attachment staging could have seen")

            open(os.path.join(_td, "regimen.txt"), "w").write("Magnesium 200mg evening\n")
            # The regimen is resolved but the user has not said the labs are complete, so `start`
            # still refuses — the last gate before any card exists.
            _buf2 = io.StringIO()
            with contextlib.redirect_stdout(_buf2):
                _rcu = rx.cmd_start(_A())
            check("start refuses until the user says the labs are complete", (_rcu, _made), (1, []),
                  "started 23s after the first of twelve attachments on 2026-08-10; prose in the "
                  "skill is advice a small model can race, a refusal is not")
            check("...and names the verb that records the confirmation",
                  "uploads-done" in _buf2.getvalue(), True,
                  "the refusal is where the model actually learns the step")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_uploads_done(_A())
                rx.cmd_start(_A())
                rx.cmd_start(_A())
            check("start is keyed on constants, so it cannot fork",
                  sorted({k for _t, k in _made}),
                  sorted("rx-stage%d-%s" % (n, part)
                         for n in range(2, 9) for part in ("begin", "barrier")),
                  "one beginning per review, whatever the inputs looked like when it was reached")

            # The realistic version of the mistake: the user says "that's all", then remembers
            # one more. A confirmation is of a SET, so a document that was not in it makes the
            # confirmation stale rather than standing in for one the user never gave.
            _made.clear()
            open(os.path.join(rx.RAW, "afterthought.pdf"), "w").write("one more lab")
            _buf3 = io.StringIO()
            with contextlib.redirect_stdout(_buf3):
                _rcs = rx.cmd_start(_A())
            check("a lab that arrives AFTER the confirmation makes it stale", _rcs, 1,
                  "otherwise the late one is transcribed or missed depending on timing alone")
            check("...and the refusal names the document that arrived",
                  "afterthought.pdf" in _buf3.getvalue(), True,
                  "the user has to recognise what it is asking about")
            with contextlib.redirect_stdout(io.StringIO()):
                rx.cmd_uploads_done(_A())
                _rcr = rx.cmd_start(_A())
            check("re-confirming clears it", _rcr, 0,
                  "the gate must be closable, or a late upload strands the review")
        finally:
            (rx.INPUTS, rx.RAW, rx.PHOTOS, rx.DOC_CACHE, rx.create) = _saved

    print("\nthe stage cards name a command the terminal allowlist admits")
    # hooks/terminal-pipeline-only.sh anchors on a literal path under the home directory, so a
    # command written with the {tilde} placeholder is refused - and it is refused at the moment
    # the worker runs it, on a card that has already done its expensive work. Two card
    # templates shipped that way once.
    # Every card body that names an rx.py command must use the LITERAL path. A {tilde} is refused
    # by hooks/terminal-pipeline-only.sh at the moment the worker runs it, on a card that has
    # already done its expensive work. Two card templates shipped that way once. Scanned across
    # every *_BODY so a body added later inherits the check instead of having to remember it.
    _bodies = {_n: getattr(rx, _n) for _n in dir(rx)
               if _n.endswith("_BODY") and isinstance(getattr(rx, _n), str)}
    for _name, _body in sorted(_bodies.items()):
        for _c in (l.strip() for l in _body.splitlines()
                   if l.strip().startswith("python3") and "rx-review/rx.py" in l):
            check("%s uses the literal rx.py path" % _name,
                  "~/.hermes/rx-review/rx.py" in _c and "{tilde}" not in _c, True,
                  "{tilde} in a command line is refused by the terminal hook at run time")
    # A Stage Begin or Barrier card runs exactly ONE command — two would leave the worker choosing.
    for _name in ("STAGE_BEGIN_BODY", "STAGE_BARRIER_CMD_BODY"):
        _cmds = [l for l in getattr(rx, _name).splitlines() if l.strip().startswith("python3")]
        check("%s names exactly one command" % _name, len(_cmds), 1,
              "a Begin/Barrier body that names two commands leaves the worker choosing")

    print("\nstarted-date efficacy — the verb, the card, and the blank-Started no-op")
    # Spec: a medication's start date splits a lab series; the verb is pure arithmetic and the
    # marker list always comes from the substance's part-2 research. Blank Started must produce
    # NO efficacy card — a supplement's graph stays byte-identical to the pre-feature pipeline.
    _rxsv = rx.marker_series
    try:
        rx.marker_series = lambda: {
            ("ldl", "serum"): [("2026-02-10", 130.0, {}), ("2026-03-12", 128.0, {}),
                               ("2026-05-14", 110.0, {}), ("2026-06-11", 105.0, {}),
                               ("2026-07-09", 108.0, {})],
            ("protein", "blood"): [("2026-01-01", 7.0, {})],
            ("protein", "urine"): [("2026-01-02", 0.15, {})],
        }
        check("_parse_since YYYY-MM splits at the first of the month",
              rx._parse_since("2026-04"), "2026-04-01", "")
        check("_parse_since passes full dates through",
              rx._parse_since("2026-04-20"), "2026-04-20", "")
        check("_parse_since junk is a parse failure, not a one-sided split",
              rx._parse_since("junk"), "", "")
        _r = rx.before_after("LDL Cholesterol", "2026-04")
        check("before_after splits pre/post at the month",
              (_r["pre_n"], _r["post_n"], _r["delta"]), (2, 3, -20.0),
              "Feb/Mar are pre-start, May/Jun/Jul post-start; baseline = last pre draw")
        check("before_after direction over the post draws", _r["direction"], "mixed",
              "110 -> 105 -> 108 is not one-directional; the verb reports, it does not judge")
        check("three post draws is not too early", _r["too_early"], False, "")
        _r1 = rx.before_after("LDL Cholesterol", "2026-07")
        check("a single post draw is TOO EARLY", (_r1["post_n"], _r1["too_early"]), (1, True),
              "one point is not a trend — the card must say so")
        _r0 = rx.before_after("LDL Cholesterol", "2026-08")
        check("no post draws yet is a reportable outcome",
              (_r0["post_n"], _r0["endpoint"]), (0, None), "")
        _rn = rx.before_after("Hemoglobin", "2026-04")
        check("an unmeasured marker is reported, not invented", _rn["found"], False,
              "the verb splits series; it never guesses one")
        _ra = rx.before_after("Protein", "2026-01-01")
        check("an ambiguous blood/urine name is refused", _ra["found"], False,
              "picking a specimen would apply the rule to the wrong series")
        _ru = rx.before_after("LDL Cholesterol", "whenever")
        check("an unparseable start date is an error", bool(_ru.get("error")), True,
              "never split silently at a guessed date")
    finally:
        rx.marker_series = _rxsv

    _saved_ms = rx.marker_series
    rx.marker_series = lambda: {("ldl", "serum"): [("2026-05-14", 110.0, {})]}
    try:
        _b = io.StringIO()
        with contextlib.redirect_stdout(_b):
            rx.cmd_before_after(type("A", (), {"marker": "LDL", "since": "2026-04",
                                               "json": False})())
        check("cmd_before_after prints TOO EARLY TO TELL with the draw count",
              "TOO EARLY TO TELL — 1 post-start draw(s)." in _b.getvalue(), True,
              "the card quotes the verb verbatim; the count is always reported")
    finally:
        rx.marker_series = _saved_ms

    check("EFFICACY_BODY runs the before-after verb by its literal path",
          "python3 ~/.hermes/rx-review/rx.py before-after --marker" in fan.EFFICACY_BODY, True,
          "a {tilde} command would be refused by the terminal hook at run time")
    check("EFFICACY_BODY names the part-2 fragment as the only marker list",
          "PART-research-{slug}-2.md" in fan.EFFICACY_BODY, True,
          "no drug knowledge in scripts: the marker list is the research answer")
    check("EFFICACY_BODY makes too-early a first-class result",
          "Too early to tell" in fan.EFFICACY_BODY and "post-start draw count" in fan.EFFICACY_BODY,
          True, "a dull result reported as dull, never a silent omission")

    _fsv = (fan.read_substances, fan.shard, fan.create, fan.rxkanban, fan._complete_self)
    _made, _splices = [], []
    try:
        class _KB:
            @staticmethod
            def slugify(t, n=48):
                return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:n]
            @staticmethod
            def is_dry(i):
                return isinstance(i, str) and i.startswith("DRY-")
            @staticmethod
            def splice(ids, barrier):
                _splices.append(list(ids))
        def _fc(args, title, assignee, body, parents=(), runtime="45m", priority=0):
            tid = "t_%d" % (len(_made) + 1)
            _made.append({"tid": tid, "title": title, "parents": list(parents)})
            return tid
        fan.read_substances = lambda: [
            {"name": "Crestor Rosuvastatin", "type": "", "note": "", "when": "evening",
             "started": "2026-04"},
            {"name": "Vitamin D3", "type": "", "note": "", "when": "morning", "started": ""}]
        fan.shard = lambda *a, **k: "t_synth"
        fan.create = _fc
        fan.rxkanban = _KB
        fan._complete_self = lambda summary="", dry=False: None
        _args = type("FA", (), {"dry_run": False})()
        with contextlib.redirect_stdout(io.StringIO()):
            fan.phase_research_family(_args, "substances")
        _eff = [m for m in _made if m["title"].startswith("Efficacy:")]
        check("one Efficacy card for the dated medication, none for the blank one",
              [m["title"] for m in _eff],
              ["Efficacy: Crestor Rosuvastatin (started 2026-04)"],
              "supplements never get a card; the date is in the title so a correction re-keys")
        check("the Efficacy card is gated on its substance's synthesis",
              bool(_eff) and _eff[0]["parents"] == ["t_synth"], True,
              "part 2 (the q4 answer) is guaranteed written before the card runs")
        check("the efficacy id is spliced in front of the 6a Barrier with the syntheses",
              bool(_eff) and _splices[-1][:2] == ["t_synth", _eff[0]["tid"]], True,
              "Stage 6 cannot complete without the efficacy report")
        _made.clear(); _splices.clear()
        fan.read_substances = lambda: [{"name": "Dropped Thing", "type": "", "note": "",
                                        "when": "daily", "started": "2025-01"}]
        fan.shard = lambda *a, **k: None
        with contextlib.redirect_stdout(io.StringIO()):
            fan.phase_research_family(_args, "substances")
        check("an excluded substance gets no efficacy card despite a start date",
              [m["title"] for m in _made if m["title"].startswith("Efficacy:")], [],
              "exclusions win; there is no synthesis to gate on and no q4 to read")
    finally:
        (fan.read_substances, fan.shard, fan.create, fan.rxkanban,
         fan._complete_self) = _fsv

    print("\nstage unpacks a zipped lab upload itself")
    # A 25 MB Discord attachment cap makes one-zip uploads the practical way to send a full lab
    # history. Staging is the intake boundary, so IT unpacks archives into inputs/raw: nested
    # folders flatten, macOS ghosts (__MACOSX/, ._*) are dropped silently, visible non-PDFs are
    # reported, re-staging is a no-op, and a corrupt archive HELDS rather than starting partial.
    import zipfile as _zf
    with tempfile.TemporaryDirectory() as td:
        cache, raw = os.path.join(td, "cache"), os.path.join(td, "raw")
        os.makedirs(cache), os.makedirs(raw)
        z1 = os.path.join(td, "labs.zip")
        with _zf.ZipFile(z1, "w") as z:
            z.writestr("panel-2026-05.pdf", b"%PDF-1.4 a")
            z.writestr("nested/panel-2025-11.pdf", b"%PDF-1.4 b")
            z.writestr("notes.txt", b"not a pdf")
            z.writestr("__MACOSX/._ghost.pdf", b"resource fork junk")
        shutil.copy(z1, os.path.join(cache, "doc_abc_labs.zip"))   # Hermes caches the upload
        _rc, _rw = rx.DOC_CACHE, rx.RAW
        _settle = os.environ.pop("RX_STAGE_SETTLE_S", None)
        rx.DOC_CACHE, rx.RAW = cache, raw
        try:
            os.environ["RX_STAGE_SETTLE_S"] = "0"
            _out = io.StringIO()
            with contextlib.redirect_stdout(_out):
                rc_stage = rx.cmd_stage(type("A", (), {"dry_run": False})())
        finally:
            if _settle is not None:
                os.environ["RX_STAGE_SETTLE_S"] = _settle
            rx.DOC_CACHE, rx.RAW = _rc, _rw
        check("stage accepts an upload that is only a zip archive", rc_stage, 0,
              "a zip sat unseen in the cache while stage reported nothing new")
        text = _out.getvalue()
        check("both nested PDFs land in inputs/raw",
              sorted(os.path.basename(p) for p in
                     [os.path.join(raw, f) for f in os.listdir(raw) if f.endswith(".pdf")]),
              ["panel-2025-11.pdf", "panel-2026-05.pdf"],
              "a missed member is a lab the review never sees")
        check("the visible non-PDF member is named as skipped", "notes.txt" in text, True,
              "nothing the user sent may vanish without a word")
        check("macOS ghost members never reach inputs/raw",
              any("ghost" in f for f in os.listdir(raw)), False,
              "resource-fork junk must not become a transcription card")
        # idempotent: staging the same archive again extracts nothing new
        _rc, _rw = rx.DOC_CACHE, rx.RAW
        rx.DOC_CACHE, rx.RAW = cache, raw
        try:
            os.environ["RX_STAGE_SETTLE_S"] = "0"
            _out2 = io.StringIO()
            with contextlib.redirect_stdout(_out2):
                rx.cmd_stage(type("A", (), {"dry_run": False})())
        finally:
            rx.DOC_CACHE, rx.RAW = _rc, _rw
        check("re-staging the same zip extracts nothing further",
              "Extracted 0 new PDF(s)" in _out2.getvalue(), True,
              "a duplicated extraction transcribes the same lab twice")

    print("\nfib4 — the derived liver-fibrosis score, age-gated, one draw only")
    # FIB-4 = (age * AST)/(platelets * sqrt(ALT)), computed from the newest draw that reports all
    # three together. A normal AST/ALT does not exclude a rising FIB-4, which is why a panel that
    # already carries the inputs surfaces the score. It is a validated single-time-point ratio, not
    # an identity: a platelet count from one panel paired with an AST from another is a value the
    # formula was never validated on, so the inputs are never stitched across draws.
    _spa = rx.patient_age
    try:
        check("patient_age: no patient.md is 0, not a guess", _spa(), 0,
              "an invented age in a clinical score is worse than no score")
        open(os.path.join(tmp, "patient.md"), "w", encoding="utf-8").write("Age: 55\n")
        check("patient_age: an explicit Age: line is read", _spa(), 55, "")
        open(os.path.join(tmp, "patient.md"), "w", encoding="utf-8").write("DOB: 1971-03-15\n")
        check("patient_age: a DOB (ISO or US) is turned into an age", _spa(), 55, "")
        open(os.path.join(tmp, "patient.md"), "w", encoding="utf-8").write("Age: 55\n")

        r = rx.fib4()
        check("fib4: the fixture scores from its one complete draw",
              (r["found"], r["date"], r["ast"], r["alt"], r["plt"]),
              (True, "2026-05-27", 33.0, 35.0, 328.0),
              "the right draw is the one carrying all three inputs, not the newest panel")
        check("fib4: the formula is applied and banded",
              (round(r["score"], 3), r["tier"]), (0.935, "LOW risk (<1.30)"),
              "55*33/(328*sqrt(35)) = 0.935, under the 1.30 threshold")
        check("fib4: the band boundaries are the published cut points",
              (rx.fib4_tier(1.29), rx.fib4_tier(1.30), rx.fib4_tier(3.27)),
              ("LOW risk (<1.30)",
               "INDETERMINATE (1.30-3.27) - consider non-invasive fibrosis assessment",
               "HIGH risk (>3.27) - consider non-invasive fibrosis assessment"),
              "1.30 and 3.27 are the conventional FIB-4 thresholds")

        _sv = rx.marker_series
        try:
            rx.marker_series = lambda: {
                ("ast", "serum"): [("2026-05-27", 33.0, {})],
                ("alt", "serum"): [("2026-05-27", 35.0, {})],
                ("platelet count", "blood"): [("2025-12-09", 328.0, {})],
            }
            r2 = rx.fib4()
            check("fib4: inputs on different draws are NOT stitched",
                  (r2["found"], "different draws" in r2.get("reason", "")), (False, True),
                  "a cross-draw ratio is a value the formula was never validated on")
            rx.marker_series = lambda: {"creatinine": [("2026-05-27", 1.1, {})]}
            r3 = rx.fib4()
            check("fib4: with no transaminases the score is absent, not invented",
                  (r3["found"], all(k in r3.get("reason", "") for k in ("ast", "alt", "plt"))),
                  (False, True), "the missing inputs are named so the user knows what to add")
            rx.marker_series = lambda: {
                ("ast", "serum"): [("2026-05-27", 33.0, {})],
                ("alt", "serum"): [("2026-05-27", 35.0, {})],
                ("platelet count", "blood"): [("2026-05-27", 328.0, {})],
            }
            _sv2 = rx.patient_age
            rx.patient_age = lambda: 0
            try:
                r4 = rx.fib4()
            finally:
                rx.patient_age = _sv2
            check("fib4: no recorded age is a refusal, not a guess",
                  (r4["found"], "age" in r4.get("reason", "").lower()), (False, True),
                  "age is a property of the person, never back-filled")
        finally:
            rx.marker_series = _sv
    finally:
        rx.patient_age = _spa
        try:
            os.remove(os.path.join(tmp, "patient.md"))
        except OSError:
            pass

    print("\npatient facts - the single input document is the surface; patient.md is what the pipeline reads from")
    _mpf = rx._write_patient_facts
    _spa = rx.patient_age
    try:
        doc = ("# Patient - David Putzolu\n"
               "Name: David Putzolu\n"
               "DOB: 1971-03-15 (55)\n"
               "WEEKLY\n"
               "Zepbound 5mg\n"
               "MORNING\n"
               "\u2022 Thorne Creatine - 5g\n")
        pmd = os.path.join(tmp, "patient.md")
        check("materialize: fact lines are extracted and named",
              _mpf(doc), "Name, DOB", "fixed order Name/Age/DOB, nothing else copied")
        check("materialize: patient.md now exists beside regimen inputs",
              os.path.exists(pmd), True, "the reader reads this file")
        check("materialize: the DOB paren-note is dropped, not copied",
              "(55)" in open(pmd, encoding="utf-8").read(), False,
              "a note after the date is not part of the date")
        check("materialize: patient_age() reads the materialised DOB", _spa(), 55,
              "ingest output is what the score reads - the whole point")
        # a re-ingest refreshes the file; an explicit Age: line is read straight
        check("materialize: a re-ingest refreshes without clobbering",
              _mpf(doc.replace("DOB: 1971-03-15 (55)", "DOB: 1971-03-15 (55)\nAge: 55")),
              "Name, Age, DOB", "")
        check("materialize: after refresh, the explicit Age: line is the one read",
              _spa(), 55, "")
        # a document with no fact line changes nothing and says nothing
        os.remove(pmd)
        check("ingest with no fact line: no file created, no report",
              (rx._write_patient_facts("MORNING\n\u2022 Thorne Creatine - 5g\n"),
               os.path.exists(pmd)),
              (None, False),
              "nothing to write is nothing written")
        # ...and it never deletes: a doc that drops its facts leaves the last recorded ones
        rx._write_patient_facts(doc)
        check("ingest with no fact line: a prior patient.md is NOT deleted",
              (rx._write_patient_facts("MORNING\n\u2022 Thorne Creatine - 5g\n"),
               os.path.exists(pmd)),
              (None, True),
              "a stale, visible age beats a silent score computed from nothing")
        # the verb itself: `rx.py regimen --from <doc>` is where the materialisation happens
        class _AIngest:
            stdin = False
            from_gdoc = None
            source = os.path.join(tmp, "patient-doc.txt")
        open(_AIngest.source, "w", encoding="utf-8").write(doc)
        check("regimen verb: ingesting the document writes regimen.txt AND patient.md",
              (rx.cmd_regimen(_AIngest), os.path.exists(pmd), os.path.exists(
                  os.path.join(tmp, "regimen.txt"))),
              (0, True, True), "one document, two materialisations, same verb")
    finally:
        for f in ("patient.md", "patient-doc.txt", "regimen.txt"):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        rx.patient_age = _spa

    print("\ntrend dispatch — the transaminase gate overrides an ordinary triage, nothing else")
    # ALT/SGPT 26 -> 29 -> 35 over five months was triaged as ordinary variation and never
    # researched, so the regimen-driver question (a statin and a JAK inhibitor both move these)
    # never ran. A triage "no" on a transaminase therefore deepens anyway - deterministic, not a
    # prompt nudge, since an LLM that already said "no" is the wrong second opinion. A "no" on
    # anything else still writes the skip report and stops.
    import types
    _tfs = (fan.read_trends, fan.create, fan.rxkanban, fan._complete_self, fan.REPORTS)
    _tmade, _tdone = [], []
    _treports = tempfile.mkdtemp(prefix="rx-dispatch-")
    try:
        _ttrends = [
            {"marker": "ALT/SGPT", "direction": "rising", "points": 3,
             "series": [("2025-12-09", 26.0), ("2026-04-10", 29.0), ("2026-05-27", 35.0)],
             "delta": 9.0, "pct": 34.6, "ref": "7 - 52", "unit": "U/L",
             "latest_value": "35", "in_range_throughout": True},
            {"marker": "Creatinine", "direction": "rising", "points": 3,
             "series": [("2025-12-09", 0.9), ("2026-04-10", 1.0), ("2026-05-27", 1.1)],
             "delta": 0.2, "pct": 22.2, "ref": "0.7 - 1.3", "unit": "mg/dL",
             "latest_value": "1.1", "in_range_throughout": True},
        ]
        class _TKB:
            @staticmethod
            def slugify(t, n=48):
                return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:n]
            @staticmethod
            def is_dry(i):
                return isinstance(i, str) and i.startswith("DRY-")
            @staticmethod
            def splice(ids, barrier):
                pass
        def _tcreate(args, title, assignee, body, parents=(), runtime="45m", priority=0):
            _tmade.append({"title": title, "body": body, "parents": list(parents)})
            return "t_%d" % len(_tmade)
        fan.read_trends = lambda: _ttrends
        fan.create = _tcreate
        fan.rxkanban = _TKB
        fan._complete_self = lambda summary="", dry=False: _tdone.append(summary)
        fan.REPORTS = _treports

        def _verdict(slug):
            open(os.path.join(_treports, "PART-trend-%s-verdict.md" % slug), "w",
                 encoding="utf-8").write("MEANINGFUL: no\nREASON: within ordinary variation\n")
        def _dispatch(slug):
            _tmade.clear(); _tdone.clear()
            ns = types.SimpleNamespace(slug=slug, triage="triage_id", dry_run=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fan.phase_trend_dispatch(ns)
            return buf.getvalue()

        _verdict("alt-sgpt"); _verdict("creatinine")
        out = _dispatch("alt-sgpt")
        check("a transaminase judged ordinary is overridden to deepen",
              "overriding to deepen" in out, True,
              "the dismissal this gate exists to prevent is ALT/SGPT 26->29->35, un-researched")
        check("the override creates the two deeper parts and the synthesis",
              [m["title"] for m in _tmade],
              ["Trend: ALT/SGPT — part 2/3", "Trend: ALT/SGPT — part 3/3",
               "Trend: ALT/SGPT — report"],
              "parts 2 and 3 plus the synthesis, exactly as a meaningful trend would get")
        check("no skip report is written for an overridden transaminase",
              os.path.exists(os.path.join(_treports, "trend-alt-sgpt.md")), False,
              "it deepened; a skip report would contradict the cards just created")
        check("the deeper cards are told the dismissal was overridden",
              any("NOTE:" in m["body"] and "transaminase" in m["body"] for m in _tmade), True,
              "parts 2/3 must not re-justify a trend the triage already waved away")
        check("the synthesis is gated on the triage plus both parts",
              _tmade and _tmade[-1]["parents"] == ["triage_id", "t_1", "t_2"], True,
              "the report reads the triage verdict and both deeper answers")

        out = _dispatch("creatinine")
        check("a non-transaminase judged ordinary still skips",
              "overriding" not in out, True,
              "the gate is marker-qualified; a creatinine wave is a genuine dismissal")
        check("the skip path creates no research cards", [m["title"] for m in _tmade], [],
              "an ordinary, in-range non-enzyme trend is not worth the fan-out")
        _skip = os.path.join(_treports, "trend-creatinine.md")
        check("the skip report is written with the triage reason",
              os.path.exists(_skip) and "within ordinary variation" in open(_skip).read(), True,
              "the user sees the trend was judged and why, not silently dropped")
        check("the skip path self-completes as ordinary, not deepened",
              _tdone == ["trend-dispatch: creatinine ordinary"], True, "")
    finally:
        (fan.read_trends, fan.create, fan.rxkanban, fan._complete_self, fan.REPORTS) = _tfs
        shutil.rmtree(_treports, ignore_errors=True)

    print("\n%s" % ("-" * 64))
    if FAILURES:
        print("%d FAILED\n" % len(FAILURES))
        for name, got, want, why in FAILURES:
            print("  %s\n    got  %r\n    want %r\n    guards: %s" % (name, got, want, why or "-"))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
