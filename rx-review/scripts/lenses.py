#!/usr/bin/env python3
"""Adversarial review, fanned out — because one card cannot read the corpus.

The four lenses used to be four cards, each handed every report in {reports}/. That is 30
files and ~974KB against a window the server serves at ~640KB, so each one compacted and then
answered confidently from its own summary. A compacted verdict is indistinguishable from a real
one, which is the whole reason this failure survived so long: nothing downstream can tell.

The citation audit already learned this and was split into per-chunk cards. The lens cards sat
beside it, unchanged, for the same corpus. Fixing one instance of a scoping bug and leaving its
neighbours is how the same failure gets rediscovered twice.

So: reports are packed into chunks that fit, every chunk is reviewed by every lens
independently, and each lens's parts are merged into the one file the reconciler already reads.

  fanout           create chunk x lens cards, a merge per lens, and gate them on the barrier
  merge            concatenate this lens's parts into its report file

Lenses are deliberately independent - run together they converge, and three voices agreeing
because they read each other is worth less than one honest voice.
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rxkanban                                                     # noqa: E402
from verify import (KANBAN_BODY_CAP, REPORTS, SKIP,                 # noqa: E402
                    write_manifest, refuse_if_incomplete,
                    announce, create, worker_context)

# A chunk is read whole by one worker, so it is bounded by the window that worker will get -
# Hermes' configured context_length, read via worker_context(), NOT a GPU host probed directly.
# n_ctx is in TOKENS; the reports are measured in bytes, and conflating the two is how the
# first version of this sized every chunk at 2.5% of the window and produced 104 cards for 26
# reports. Convert, then take a quarter: the failure being fixed here is not "did not fit" but
# "fit, and compacted anyway", so the model needs room to think and not merely room to hold.
CHARS_PER_TOKEN = 4
WINDOW_FRACTION = 0.25
LENS_BUDGET_CHARS = 150_000        # hard ceiling regardless of a generous window
MAX_REPORTS_PER_CARD = 8

# A report longer than a whole chunk is split at its own headings instead. Below this a
# fragment carries no argument worth attacking - it is a heading or a one-line summary.
MIN_SECTION_CHARS = 400

CARD_RUNTIME_MINUTES = 40

SEVERITY = ("fatal", "serious", "minor", "clean")

# One scale for every lens, and the reconciler consumes all of it. Two lenses used to grade
# `fatal`/`qualifying` and `fatal`/`minor`, and the survival rule read only `fatal` - so every
# middle grade was invisible to the gate that decides what reaches the brief.
SEVERITY_RULE = """severity is `fatal` (the claim cannot stand), `serious` (the claim must be weakened before
it is used), or `minor` (imprecision worth fixing). If the chunk survives your lens with
nothing to report, write exactly one line:

  clean | <report> | - | nothing found under this lens

Saying `clean` when the work is sound is a real result and is expected often. Manufacturing a
finding to look thorough is the failure this lens exists to catch in others."""

LENSES = {
    "logic": {
        "profile": "rx-logic",
        "out": "LOGIC.md",
        "title": "Logic audit %s: does the reasoning hold?",
        "ask": """Attack the REASONING in these reports. You are not arguing the opposite position and you
are not writing a rebuttal - you are checking whether each argument as written is sound.

Look for, and name where you find them:
  - a conclusion that does not follow from the premises actually stated
  - a premise smuggled in without support
  - correlation presented as causation
  - a single study generalised to a population it did not sample
  - a range or estimate reported as a measurement
  - a dose, duration or population in the claim that differs from the one in the evidence
  - equivocation: a term meaning one thing in the premise and another in the conclusion
  - a comparison against an unstated or shifting baseline

A report can be entirely factual and still fail here. Judge the inference, not the topic.""",
    },
    "counter": {
        "profile": "rx-redteam",
        "out": "REFUTATION.md",
        "title": "Counter-evidence %s: what do these reports ignore?",
        "ask": """Hunt for evidence these reports IGNORED or CONTRADICT. You are not writing the opposing
case - you are testing whether the support offered survives contact with what else is known.

Search for: a more recent finding that supersedes the one used, a larger or better-designed
study reaching a different conclusion, a retraction or correction, a contraindication or
interaction the report omits, a population in which the result does not hold.

Report only what you can cite. "There might be counter-evidence" is not a finding. If the
support holds up against what you can find, say so plainly - a claim that survives this lens is
a stronger claim, and reporting it as weak would be as wrong as missing a flaw.""",
    },
    "overreach": {
        "profile": "rx-logic",
        "out": "OVERREACH.md",
        "title": "Overreach %s: is more claimed than shown?",
        "ask": """Compare the STRENGTH of each claim against the strength of its support.

Flag where the text asserts more than the evidence carries:
  - "will" where the support says "may"
  - "demonstrates" or "proves" where the support is suggestive
  - a point estimate where the source gives a range
  - a causal verb over a correlational finding
  - a general recommendation resting on one trial, one dose or one population
  - confidence language ("clearly", "obviously", "well established") standing in for evidence

For each, quote the sentence and state what the support would actually license. This lens does
not dispute facts; it disputes the distance between the fact and the sentence.""",
    },
    "nullhyp": {
        "profile": "rx-nullhyp",
        "out": "NULLHYP.md",
        "title": "Status quo %s: what is the case for changing nothing?",
        "ask": """Argue for changing NOTHING. Steelman the status quo against these reports.

This pipeline is biased toward manufacturing action items - it was built to research candidate
changes, so it produces candidate changes. You exist to push back. For each change these
reports propose:

  - what is the cost of doing nothing, honestly stated? If it is low, say so.
  - is the benefit within the noise of ordinary variation?
  - does the evidence support the CHANGE, or merely the existence of an association?
  - what does the change risk that the current regimen does not - interactions, adherence,
    cost, another thing to get wrong?
  - would a reasonable clinician looking at this decline to act, and why?

A proposed change that survives an honest steelman is worth much more than one that was never
challenged. Where the case for acting genuinely is stronger, say so - this lens is not obliged
to object.""",
    },
}

CARD_BODY = """{ask}

## What to read

Read {items_file} in this working directory, then read each report it names.

## What to write

Append ONE line per finding to {part_file} as you go, writing only to that file:

  <severity> | <report> | <the sentence at fault, quoted> | <what is wrong, in one line>

{severity_rule}

Quote the sentence you are attacking.

When done, kanban_complete with metadata: {{"chunk": "{chunk}", "findings": N}}
"""

MERGE_BODY = """Run this, then kanban_complete with the totals it prints:

    python3 ~/.hermes/rx-review/lenses.py merge --lens {lens}

Do nothing else.
"""


def _stem(lens):
    return "LENS-%s" % lens


def report_files():
    """The research reports the lenses judge: everything except the pipeline's own outputs."""
    out = []
    for path in sorted(glob.glob(os.path.join(REPORTS, "*.md"))):
        name = os.path.basename(path)
        if name in SKIP or name.startswith(("AUDIT-chunk", "CONTEXT-", "LENS-", "CRITIQUE-",
                                            "PART-")):
            continue
        if any(name == v["out"] for v in LENSES.values()):
            continue
        out.append(path)
    return out


SLICE_PREFIX = "LENS-slice-"


def split_report(path, budget):
    """Slice one oversized report at its own headings into pieces that fit. Returns paths.

    This is what MIN_SECTION_CHARS was declared for. The constant was referenced nowhere: the
    heading split was never written, so a report larger than a whole chunk was handed to a card
    WHOLE - the exact failure this module exists to prevent, since that card compacts and then
    answers from its own summary. The only trace was a stdout line at fanout time that nothing
    consumed.

    Slices carry the LENS- prefix so report_files() will not re-chunk them on a later round,
    and each one names its parent so a finding is still attributed to the real report rather
    than to a slice number.
    """
    text = open(path, encoding="utf-8", errors="ignore").read()
    lines = text.splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if re.match(r"^#{1,6}\s+\S", l)] or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    sections = [("".join(lines[a:b]))
                for a, b in zip(starts, starts[1:] + [len(lines)])]

    groups, cur = [], ""
    for sec in sections:
        # A section that alone exceeds the budget is hard-split at line boundaries; there is
        # no smaller structure to cut on, and passing it whole is what this function exists
        # to stop.
        while len(sec) > budget:
            groups.append((cur + sec[:budget]) if cur else sec[:budget])
            cur, sec = "", sec[budget:]
        if cur and len(cur) + len(sec) > budget:
            groups.append(cur)
            cur = ""
        cur += sec
    if cur.strip():
        # Too small to carry an argument worth attacking - fold it back rather than spend a
        # card on it.
        if groups and len(cur) < MIN_SECTION_CHARS:
            groups[-1] += cur
        else:
            groups.append(cur)

    stem = os.path.splitext(os.path.basename(path))[0]
    out = []
    for i, g in enumerate(groups, 1):
        sp = os.path.join(REPORTS, "%s%s-%02d.md" % (SLICE_PREFIX, stem, i))
        with open(sp, "w", encoding="utf-8") as fh:
            fh.write("# %s — slice %d of %d\n\n"
                     "This is part of a report too large to review in one card. When you "
                     "report a finding, name the report as `%s`, never this slice.\n\n---\n\n"
                     % (os.path.basename(path), i, len(groups), os.path.basename(path)))
            fh.write(g)
        out.append(sp)
    return out


def chunk_reports(paths, budget, split=True):
    """Pack reports into chunks that fit, keeping each report whole where possible.

    Bounded by BOTH characters and count: the char budget stops one fat report blowing the
    card, and the count cap stops a dozen small ones blowing the wall clock. A report larger
    than the whole budget is SPLIT at its own headings (split_report) rather than passed
    whole - silent truncation, and silently over-filling a card, both read as "covered
    everything" when they did not.

    `split=False` reports what would be oversized without writing slice files, for callers
    that only want to measure (the tests, and --dry-run).
    """
    chunks, cur, cur_chars = [], [], 0
    oversized = []
    for p in paths:
        n = os.path.getsize(p)
        if n > budget:
            if cur:
                chunks.append(cur)
                cur, cur_chars = [], 0
            oversized.append((os.path.basename(p), n))
            for sp in (split_report(p, budget) if split else [p]):
                chunks.append([sp])
            continue
        if cur and (cur_chars + n > budget or len(cur) >= MAX_REPORTS_PER_CARD):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(p)
        cur_chars += n
    if cur:
        chunks.append(cur)
    return chunks, oversized


def cmd_fanout(args):
    paths = report_files()
    if not paths:
        print("no research reports found in %s" % REPORTS)
        return 1

    n_ctx = worker_context()
    window_chars = n_ctx * CHARS_PER_TOKEN
    budget = min(LENS_BUDGET_CHARS, int(window_chars * WINDOW_FRACTION))
    chunks, oversized = chunk_reports(paths, budget, split=not args.dry_run)

    print("reports      : %d" % len(paths))
    print("worker window: %d tokens (~%d chars) -> chunk budget %d chars (%.0f%% of window)"
          % (n_ctx, window_chars, budget, 100.0 * budget / window_chars))
    print("chunks       : %d  x %d lenses = %d card(s)"
          % (len(chunks), len(LENSES), len(chunks) * len(LENSES)))
    for name, n in oversized:
        print("  ! oversized, own chunk: %s (%d bytes)" % (name, n))

    rnd = getattr(args, "round", 1) or 1
    suffix = "" if rnd == 1 else "-r%d" % rnd          # filenames
    rtag = "" if rnd == 1 else " r%d" % rnd            # card titles

    created, per_lens = [], {}
    for lens, spec in LENSES.items():
        stem = _stem(lens)
        ids, expected = [], []
        for i, chunk in enumerate(chunks, 1):
            tag = "%02d/%02d" % (i, len(chunks))
            items_file = os.path.join(REPORTS, "%s-items%s-%02d.md" % (stem, suffix, i))
            if not args.dry_run:
                with open(items_file, "w", encoding="utf-8") as fh:
                    fh.write("# %s chunk %s%s\n\n" % (lens, tag, rtag))
                    for p in chunk:
                        fh.write("- %s\n" % os.path.basename(p))
            part_file = "%s-part%s-%02d.md" % (stem, suffix, i)
            expected.append(part_file)
            body = CARD_BODY.format(
                ask=spec["ask"], items_file=os.path.basename(items_file),
                part_file=part_file, severity_rule=SEVERITY_RULE, chunk=tag + rtag)
            if len(body.encode()) > KANBAN_BODY_CAP:
                raise SystemExit("lens card body over the %d-byte cap for %s %s"
                                 % (KANBAN_BODY_CAP, lens, tag))
            cid = create(spec["title"] % (tag + rtag), spec["profile"], body,
                         parents=[], runtime="%dm" % CARD_RUNTIME_MINUTES,
                         priority=30, dry=args.dry_run, notify=False)
            ids.append(cid)
            created.append(cid)
        if not args.dry_run:
            # Written before the cards run: the merge must be able to tell a part that has
            # not finished from a part that finished and wrote nothing.
            write_manifest(stem, expected)
        # Round-suffixed, because the idempotency key is derived from the title. A constant
        # title returned the EXISTING merge and silently discarded the new --parent arguments,
        # so the merge stayed gated on round 1's chunks; once those were archived it became
        # ready (recompute_ready treats archived as satisfied) and published a complete-looking
        # lens report before a single round-2 card had run. verify.py fixed exactly this and
        # lenses.py was left with the bug.
        merge = create("Consolidate the %s lens%s" % (lens, rtag), "rx-intake",
                       MERGE_BODY.format(lens=lens, stem=stem, out=spec["out"]),
                       parents=[i for i in ids if not rxkanban.is_dry(i)],
                       runtime="15m", priority=29, dry=args.dry_run, notify=False)
        per_lens[lens] = merge
        created.append(merge)
        print("  %-9s %d card(s) -> merge %s" % (lens, len(ids), merge))

    _gate_barrier([m for m in per_lens.values()
                   if m and not rxkanban.is_dry(m)], args.dry_run)
    print(json.dumps({"chunks": len(chunks), "lenses": len(LENSES),
                      "cards": len(created), "oversized": len(oversized)}))
    return 0


def _gate_barrier(merge_ids, dry):
    """Splice each lens merge in front of the Stage 7 adversarial barrier."""
    if dry:
        return
    for up, cid in rxkanban.splice(merge_ids, "Stage 7: Adversarial Complete%"):
        print("  linked %s -> barrier %s" % (up, cid))


def cmd_merge(args):
    lens = args.lens
    if lens not in LENSES:
        print("unknown lens %r; expected one of %s" % (lens, ", ".join(LENSES)))
        return 1
    spec = LENSES[lens]
    refuse_if_incomplete(_stem(lens), "%s lens" % lens)
    sources = sorted(glob.glob(os.path.join(REPORTS, "%s-part-*.md" % _stem(lens))))
    if not sources:
        print("no %s-part-*.md found for the %s lens" % (_stem(lens), lens))
        raise SystemExit(1)
    rows, seen, unparsed = [], set(), []
    counts = {s: 0 for s in SEVERITY}
    for p in sources:
        for line in open(p, encoding="utf-8", errors="ignore"):
            t = line.strip()
            if not t or t.startswith("#") or "|" not in t:
                continue
            sev = t.split("|")[0].strip().lower()
            if t in seen:
                continue
            # A pipe line whose first field is not a severity used to be dropped here in
            # silence. The prompt says "append ONE line per finding" and a model bullets by
            # reflex, so "- fatal | ..." or "**fatal** | ..." lost the finding ENTIRELY -
            # counts, total and body all agreed with each other and were all wrong. A dropped
            # `fatal` is an unsound claim reaching the brief with nothing marking it.
            if sev not in counts:
                unparsed.append((os.path.basename(p), t))
                continue
            seen.add(t)
            counts[sev] += 1
            rows.append(t)
    if unparsed:
        print("REFUSING to merge: %d line(s) name no severity. Fix the part file(s) and re-run;\n"
              "publishing a count that silently excludes them is the failure this guards."
              % len(unparsed))
        for src, t in unparsed[:20]:
            print("   %-28s %s" % (src, t[:96]))
        print("Expected first field: %s" % ", ".join(SEVERITY))
        raise SystemExit(1)
    total = sum(counts.values())
    actionable = total - counts["clean"]
    with open(os.path.join(REPORTS, spec["out"]), "w", encoding="utf-8") as fh:
        fh.write("# %s\n\n" % spec["title"].replace(" %s", "").rstrip(":?"))
        fh.write("%d finding(s) across %d part(s).\n\n" % (total, len(sources)))
        for s in SEVERITY:
            fh.write("- **%s**: %d\n" % (s, counts[s]))
        fh.write("\n`severity | report | sentence | what is wrong`\n\n")
        for r in sorted(rows):
            fh.write("%s\n" % r)
    print(json.dumps({"lens": lens, "parts": len(sources), "findings": total,
                      "actionable": actionable, **counts}))
    announce("**%s lens complete** — %d finding(s): %s"
             % (lens, actionable,
                ", ".join("%s %d" % (s, counts[s]) for s in SEVERITY if counts[s])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fanout", help="create chunk x lens cards and their merges")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--round", type=int, default=1,
                   help="re-plan round; suffixes files and titles so a re-run cannot "
                        "overwrite a finished round's items or reuse its cards")
    p.set_defaults(fn=cmd_fanout)
    p = sub.add_parser("merge", help="consolidate one lens's findings")
    p.add_argument("--lens", required=True, choices=sorted(LENSES))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_merge)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
