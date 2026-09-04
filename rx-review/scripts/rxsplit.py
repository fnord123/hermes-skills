"""PDF-to-text utilities for the lab branch: OCR a scan to a searchable PDF, strip prose and
chart clutter, spot reference pages, check coverage. `plan-lab` uses these to flatten a document
into the overlapping line windows its transcription cards carry inline; the `extract` subcommand
below is an operator tool for one page range — cards no longer run it.

A 29-page panel is a single card today: one worker, one context, an hour of wall clock during
which nothing else on the lab branch moves. It is also the shape most likely to exhaust the
context window mid-table and die (2026-07-30), and the shape where a worker quietly dropping
the last thirty rows is invisible - verification proves every transcribed value appears in the
PDF, never that every value in the PDF was transcribed.

Splitting is only safe if the cut cannot fall inside a row and the pieces can be shown to add
up. Structure gives the first; deliberate redundancy gives the second.

  * PyMuPDF never splits a text LINE across pages, so a page boundary cannot bisect a printed
    line. Every cut here is a page boundary. (The 2-page CMP breaks between LYM# and MON#:
    two whole rows, nothing torn.)
  * A cut can still land inside a marker in a cell-per-line layout, where the name, the value
    and the reference range extract as three separate lines - as they do on the Function
    panel's native pages. RANGES THEREFORE OVERLAP BY ONE PAGE: the page at every internal
    seam is transcribed twice, by two workers that never see each other's output.
  * Where the seams go comes from the document's own identity strings ("Page 13 of 16",
    "Appendix 1 [...] - Page 1 of 13"), which mark where one bound report ends and the next
    begins. Font size does NOT work: the largest font on all 16 native Function pages is the
    patient's name, so heading-by-size finds zero real boundaries.
  * Row arithmetic is a WARNING only. Counting is genuinely ill-defined on a cell-per-line
    layout, so it can support "this looks short, go and look" and nothing stronger. The
    overlap agreement is the real evidence; see reconcile() in rx.py.
"""

import os
import re


# The OCR service that turns a text-less (scanned/bitmap) lab PDF into a searchable one:
# ocrmypdf-web on the docker host, POST /ocr (multipart `file`) -> searchable PDF in the body,
# with the ocrmypdf exit code in the X-OCR-Exit-Code header. Overridable for a different host.
OCR_URL = os.environ.get("RX_OCR_URL", "http://192.168.1.226:8093/ocr")


def ocr_to_searchable(src, out, url=None, timeout=300):
    """POST a text-less PDF to the OCR service and write the returned searchable PDF to `out`.

    Returns True only when the service returns a PDF with ocrmypdf exit code 0. Any failure —
    service down, transport error, non-zero exit, empty body — returns False, leaving the caller
    to fall back. Deterministic OCR (tesseract under the hood); no model, no vision.
    """
    import urllib.request                                       # noqa: PLC045
    import uuid                                                 # noqa: PLC045
    with open(src, "rb") as fh:
        payload = fh.read()
    boundary = "----rxocr" + uuid.uuid4().hex
    head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: application/pdf\r\n\r\n' % (boundary, os.path.basename(src))).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(url or OCR_URL, data=head + payload + tail, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            exit_code = resp.headers.get("X-OCR-Exit-Code")
            data = resp.read()
    except Exception:                                          # noqa: BLE001
        return False
    if (exit_code not in (None, "0")) or not data:
        return False
    outdir = os.path.dirname(out)
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(data)
    return True


# Split only what is actually a problem. Four of the five real documents on hand are 1-2
# pages: for those a split adds moving parts and buys nothing.
SPLIT_MIN_PAGES = 8
SPLIT_MIN_CHARS = 15000

# Per-range budget. Deliberately far below the model's context: the worker also holds the card
# body, its tool output and the table it is building, and the point is to stay clear of the
# ceiling rather than to pack it.
RANGE_CHAR_BUDGET = 12000

# A line pattern on this fraction of a SEGMENT's pages is chrome, not data.
BOILERPLATE_FRACTION = 0.6

_UNIT = (r"(?:m?g/d?L|g/L|mmol/L|nmol/L|pmol/L|umol/L|ng/m?L|pg/m?L|mcg/dL|ug/dL|U/L|"
         r"IU/m?L|mIU/L|uIU/mL|ng/dL|K/uL|M/uL|mL/min|x10|fL|pg|%)")
_RANGE = r"(?:\d[\d,.]*\s*[-–]\s*\d|[<>]\s*(?:OR\s*=\s*)?\d)"


def _norm_line(s):
    """A line with its digits blanked, for frequency comparison.

    The footer "PAGE 1 OF 13" differs on every page and so escapes exact-line matching
    entirely - which is how twelve page footers were mistaken for marker names the first time
    this was measured. Normalised, they collapse to one pattern and are recognised as chrome.
    """
    return re.sub(r"\d+", "#", s.strip())


# A reference range printed per demographic is documentation for ONE marker, not more markers.
# The Insulin/Leptin panel reports exactly two analytes and then lists eight bracket lines for
# leptin - "Males: 0.3-13.4 ng/mL", "10-13.9 years: 1.4-16.5 ng/mL" - each carrying a range and
# a unit. Counting those said the PDF held about 7 markers when it held 2, and the review was
# told a complete transcription looked short.
_BRACKET = re.compile(
    r"^\s*(?:males?|females?|men|women|adults?|adult\s+\w+|pediatric|children|child|"
    r"neonat\w*|infants?|both\s+sexes|either\s+sex|optimal|moderate|high|low|normal|"
    r"desirable|borderline|therapeutic|toxic|risk|reference\s+ranges?|"
    r"\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\s*(?:years?|yrs?|months?|mos?|weeks?|days?))"
    r"\s*[:.]?\s", re.I)


def _is_marker_row(line):
    """A line carrying a reference range AND a unit - one per marker in either layout.

    In a row-per-line layout that is the row itself; in a cell-per-line layout it is the
    marker's reference-range cell. Requiring BOTH keeps out prose and citations that merely
    contain a dash between numbers ("JAMA. 2013;310(19): 2061-2068"), and _BRACKET keeps out a
    marker's own per-demographic reference brackets.
    """
    if _BRACKET.match(line):
        return False
    return bool(re.search(_RANGE, line) and re.search(_UNIT, line, re.I))


# ── removing what is not a result ────────────────────────────────────────────────────────
#
# The transcriber is handed extracted text and asked to find results in it. That text also
# carries the report's prose and its charts' furniture, and neither is distinguishable from
# data once it is a line of characters. A gauge on page 1 of an Omega-3 report arrives as
#
#     YOUR LEVEL / Desirable Range: 8% - 12% / 12% / 1% / 2% ... / 11% / 8.73%
#
# - twelve axis labels around one real value. A page of seafood nutrition data and educational
# prose produced two rows naming tests the document never mentions.
#
# Both rules below are GENERIC and are validated against every PDF in the corpus: no line that
# carries a verified result may be removed. Deliberately conservative - reference material that
# merely looks like data (a food composition table) is NOT removed, because telling it from a
# results table needs meaning, not shape, and deleting a real result is far worse than passing
# noise through to a worker that has been told an empty answer is correct.

_FUNCTION_WORDS = re.compile(
    r"\b(the|and|are|for|with|that|this|your|you|from|have|has|been|were|was|which|when|"
    r"about|into|than|them|they|their|these|those|will|would|should|can|may|because|"
    r"consult|please|note|healthy|help|more|most|some|also|other|between)\b", re.I)


def _is_prose(line):
    """A sentence, not a row. Long, mostly letters, several function words, few digits."""
    t = line.strip()
    if len(t) < 60:
        return False
    digits = sum(c.isdigit() for c in t)
    if digits / float(len(t)) > 0.08:
        return False
    return len(_FUNCTION_WORDS.findall(t)) >= 3

_AXIS_LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(%|mg/dL|mmol/L)?\s*$")
AXIS_RUN_MIN = 5


def _axis_runs(lines):
    """Indices belonging to a chart's axis: a long, evenly-stepped ladder of bare numbers.

    A results table never prints five or more consecutive bare numbers in an even arithmetic
    progression; an axis always does. Requiring a CONSTANT step is what keeps this off
    cell-per-line result columns, whose values are unrelated to each other.
    """
    drop, i = set(), 0
    while i < len(lines):
        run = []
        while i < len(lines):
            m = _AXIS_LINE.match(lines[i])
            if not m:
                break
            run.append((i, float(m.group(1)), m.group(2) or ""))
            i += 1
        # The ladder is not always the whole run. A gauge prints its top label first —
        # "12%, 1%, 2% ... 11%" — so the constant-step sequence is a SUBRUN, and requiring the
        # whole run to share one step let that axis through unaltered.
        k = 0
        while k < len(run):
            j = k + 1
            step = None
            while j < len(run):
                d = round(run[j][1] - run[j - 1][1], 6)
                if step is None:
                    step = d
                elif d != step:
                    break
                j += 1
            sub = run[k:j]
            if (len(sub) >= AXIS_RUN_MIN and step and abs(step) > 0
                    and len({u for _, _, u in sub}) == 1):
                # ONLY the evenly-stepped subrun. Taking the whole contiguous block was tried
                # and destroyed two real results: the gauge prints "12%, 1%, 2% ... 11%,
                # 8.73%", so THE READING SITS INSIDE THE SAME BLOCK AS ITS AXIS. That leaves
                # the ladder's out-of-order head ("12%", "1%") behind as residue, which is a
                # good trade - a stray token is noise a worker can ignore, a deleted result is
                # not recoverable.
                drop |= {ix for ix, _, _ in sub}
            k = j if j > k + 1 else k + 1
        i += 1
    return drop


# ── known reference pages ────────────────────────────────────────────────────────────────
#
# DELIBERATELY BESPOKE, and the exception to the rule that everything else here is generic.
# A page of food-composition data is data-shaped - names with numbers, in columns - so telling
# it from a results table needs MEANING, not shape, and every structural rule tried against it
# either missed it or endangered real results. Matching its title exactly is honest about that:
# it prunes what we know is reference material and claims nothing more.
#
# Brittle on purpose. A re-paginated or re-worded report stops matching, and the fallback is
# the general defence - the card says an empty answer is correct, and verification rejects any
# row whose marker name is not in the source. Adding an entry is cheap; a wrong generic rule
# that deletes a result is not.
REFERENCE_PAGE_OPENERS = (
    "amount of epa and dha in seafood",          # OmegaQuant Omega-3 Index Complete, p7
)


def is_reference_page(text):
    """True when a page opens with a known reference table rather than results."""
    for line in text.splitlines():
        t = line.strip().lower()
        if not t:
            continue
        return any(t.startswith(o) for o in REFERENCE_PAGE_OPENERS)
    return False


def declutter(text):
    """(kept_text, removed_lines) with prose and chart axes stripped out."""
    lines = text.splitlines()
    axis = _axis_runs(lines)
    kept, removed = [], []
    for i, l in enumerate(lines):
        if i in axis or _is_prose(l):
            if l.strip():
                removed.append(l.strip())
            continue
        kept.append(l)
    return "\n".join(kept), removed


def page_texts(pdf):
    """Per-page plain text. Raises if the PDF cannot be opened - callers decide."""
    import fitz                                                # noqa: PLC045
    doc = fitz.open(pdf)
    try:
        return [pg.get_text() for pg in doc]
    finally:
        doc.close()


def _family(text):
    """The document-identity key printed on a page, or None.

    "Page 13 of 16" and "Appendix 1 [Enhanced PDF Report OZ776061F-1.pdf] - Page 1 of 13" are
    two different reports bound into one file. Normalising away the page NUMBER while keeping
    the total and any prefix makes pages of one report compare equal and pages of the other
    compare unequal.
    """
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) > 120:
            continue
        m = re.search(r"page\s+\d+\s+of\s+(\d+)", s, re.I)
        if m:
            return re.sub(r"page\s+\d+\s+of\s+\d+", "page # of %s" % m.group(1), s,
                          flags=re.I).lower()
    return None


def _columns(pdf, index):
    """Rounded left edges of a page's text blocks - a coarse fingerprint of its layout."""
    import fitz                                                # noqa: PLC045
    doc = fitz.open(pdf)
    try:
        return frozenset(round(b[0], -1) for b in doc[index].get_text("blocks") if b[4].strip())
    finally:
        doc.close()


def segments(pdf, texts=None):
    """Page ranges (1-indexed, inclusive) belonging to distinct bound reports.

    Identity strings are authoritative where they exist; a page without one continues the
    segment it follows, because a missing footer is far more common than a report boundary.
    With no identity strings anywhere, layout similarity is the fallback: a page sharing less
    than half its column positions with the previous page starts a new segment.
    """
    texts = page_texts(pdf) if texts is None else texts
    if not texts:
        return []
    fams = [_family(t) for t in texts]
    bounds = []
    if any(fams):
        cur = None
        for i, f in enumerate(fams):
            if f is not None and cur is not None and f != cur:
                bounds.append(i)
            if f is not None:
                cur = f
    else:
        prev = _columns(pdf, 0)
        for i in range(1, len(texts)):
            cols = _columns(pdf, i)
            union = prev | cols
            if union and len(prev & cols) / float(len(union)) < 0.5:
                bounds.append(i)
            prev = cols
    out, start = [], 0
    for b in bounds:
        out.append((start + 1, b))
        start = b
    out.append((start + 1, len(texts)))
    return out


def boilerplate(texts):
    """Digit-blanked line patterns repeated on most of these pages.

    Counted once per page, so a line printed twice on one page cannot inflate its own
    frequency. Callers pass ONE SEGMENT's pages: the Quest appendix footer is on 13 of 29
    pages of the whole file (44%, below the threshold) but on 13 of 13 of its own segment.
    """
    if len(texts) < 3:
        return set()
    seen = {}
    for t in texts:
        for pat in set(_norm_line(x) for x in t.splitlines() if x.strip()):
            seen[pat] = seen.get(pat, 0) + 1
    need = max(2, int(len(texts) * BOILERPLATE_FRACTION))
    return {pat for pat, n in seen.items() if n >= need}


def count_rows(text, boiler=()):
    """Marker-shaped lines on a page, ignoring known chrome. Approximate by nature."""
    boiler = set(boiler)
    return sum(1 for line in text.splitlines()
               if line.strip() and _norm_line(line) not in boiler and _is_marker_row(line))


def header_line(texts):
    """The column header a continuation range would otherwise have to infer, or None."""
    words = ("marker", "test", "analyte", "result", "value", "unit", "units",
             "reference", "range", "flag", "interval")
    best = None
    for t in texts:
        for line in t.splitlines():
            s = line.strip()
            if not 8 <= len(s) <= 120:
                continue
            hits = sum(1 for w in words if re.search(r"\b%s\b" % w, s, re.I))
            if hits >= 2 and (best is None or hits > best[0]):
                best = (hits, s)
    return best[1] if best else None


def plan(pdf, texts=None):
    """Page ranges to transcribe as [(first, last, expected_rows), ...], 1-indexed inclusive.

    Ranges OVERLAP BY ONE PAGE at every internal seam: [(1,5), (5,9), (9,12)]. The shared page
    is transcribed twice on purpose - two workers, no shared context - so a marker whose cells
    straddle the cut is seen whole by at least one of them, and the two results can be checked
    against each other afterwards. Overlap stops at a SEGMENT boundary: pages either side of
    it belong to different reports, so transcribing one twice proves nothing.

    A document not worth splitting comes back as a single range, so callers have one path.
    A page too large for the budget becomes its own range rather than being divided.
    """
    texts = page_texts(pdf) if texts is None else texts
    if not texts:
        return []
    total = sum(len(t) for t in texts)
    if len(texts) < SPLIT_MIN_PAGES and total < SPLIT_MIN_CHARS:
        return [(1, len(texts), sum(count_rows(t, boilerplate(texts)) for t in texts))]

    out = []
    for first, last in segments(pdf, texts):
        seg = texts[first - 1:last]
        boiler = boilerplate(seg)
        packed, start, chars = [], first, 0
        for n in range(first, last + 1):
            t = texts[n - 1]
            if chars and chars + len(t) > RANGE_CHAR_BUDGET:
                packed.append((start, n - 1))
                start, chars = n, 0
            chars += len(t)
        packed.append((start, last))
        for i, (a, b) in enumerate(packed):
            a = a - 1 if i else a                              # reach back over the seam
            out.append((a, b, sum(count_rows(texts[n - 1], boiler) for n in range(a, b + 1))))
    return out


def overlaps(ranges):
    """Pages covered by two ranges, as [(page, (a1,b1), (a2,b2)), ...]."""
    out = []
    for i in range(len(ranges) - 1):
        a1, b1 = ranges[i][0], ranges[i][1]
        a2, b2 = ranges[i + 1][0], ranges[i + 1][1]
        shared = set(range(a1, b1 + 1)) & set(range(a2, b2 + 1))
        for p in sorted(shared):
            out.append((p, (a1, b1), (a2, b2)))
    return out


def range_tag(first, last):
    """The filename fragment for a range: stable, sortable, zero-padded."""
    return "p%02d-%02d" % (first, last)


def parse_tag(name):
    """(first, last) from a labs-*-pNN-MM.md filename, or None."""
    m = re.search(r"-p(\d+)-(\d+)\.md$", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def expected_rows(pdf):
    """Marker-shaped lines in the whole document, chrome removed per segment. Warn-only.

    Deliberately approximate: a marker printed across two lines undercounts, a footnote shaped
    like a row overcounts. It can support "this looks short, go and look" and nothing more.
    """
    try:
        texts = page_texts(pdf)
    except Exception:                                          # noqa: BLE001
        return None
    total = 0
    for first, last in segments(pdf, texts):
        seg = texts[first - 1:last]
        boiler = boilerplate(seg)
        total += sum(count_rows(t, boiler) for t in seg)
    return total


def coverage_gaps(counts_by_source, raw_dir, tolerance=0.7):
    """(file, why) for each source whose transcribed rows fall well short of the PDF's.

    Existing verification proves each transcribed value is really in the PDF; it says nothing
    about a value in the PDF that was never transcribed. This is the only check that looks for
    omission across the whole document, so its tolerance is loose on purpose - it exists to
    catch a range that silently produced nothing, not to audit the last few rows.
    """
    gaps = []
    for name in sorted(counts_by_source):
        pdf = os.path.join(raw_dir, name)
        if not os.path.exists(pdf):
            continue
        want = expected_rows(pdf)
        if not want:
            continue
        got = counts_by_source[name]
        if got < want * tolerance:
            gaps.append((name, "%d row(s) transcribed; the PDF looks like it holds about %d"
                               % (got, want)))
    return gaps


def cmd_extract(args):
    """Write one page range's text, decluttered, and say what was removed.

    The transcription cards used to inline a `python3 -c "import fitz..."` dump, which handed
    the worker the report's prose and its charts' axis labels alongside the results. A worker
    cannot tell those apart once they are lines of characters, and on a page carrying no
    results at all it invented two.
    """
    lo, hi = (int(x) for x in args.pages.split("-"))
    texts = page_texts(args.pdf)
    pages, skipped = [], []
    for n in range(lo, hi + 1):
        if is_reference_page(texts[n - 1]):
            skipped.append(n)
            continue
        pages.append(texts[n - 1])
    raw = "\n".join(pages)
    kept, removed = declutter(raw)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(kept)
    print("pages %d-%d of %s" % (lo, hi, os.path.basename(args.pdf)))
    print("  %d line(s) kept, %d removed as prose or chart axis"
          % (len([l for l in kept.splitlines() if l.strip()]), len(removed)))
    if skipped:
        print("  skipped page(s) %s — a known reference table, not results"
              % ", ".join(str(n) for n in skipped))
    if not kept.strip():
        print("  NOTE: nothing left. These pages carry no results — write an empty table "
              "and say so; do not invent rows.")
    print("  wrote %s" % args.out)
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract", help="(operator tool) write a page range's text with prose "
                                       "and axes removed — cards carry their windows inline")
    p.add_argument("--pdf", required=True)
    p.add_argument("--pages", required=True, help="inclusive, 1-indexed, e.g. 4-6")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_extract)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
