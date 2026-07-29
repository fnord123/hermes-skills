#!/usr/bin/env python3
"""Adversarial evaluation of generated text: is this argument correct?

Not a debate. The bull case and the bear case in an investment memo are both standard sections
and both legitimately supported - they differ in which aspects they weight, not in whether they
are true. Setting them against each other produces rhetoric, not a check.

What gets attacked is the SUPPORT for each argument, separately and with equal rigour. For
every claim-bearing section of a finished document, an adversary asks: does the reasoning hold,
is the evidence real, does the cited source carry the weight put on it, is the conclusion
stronger than what the support allows. A bear case whose evidence fails is demoted exactly as a
bull case would be - the reconciler does not care which direction an argument points.

Four lenses, run independently so they cannot converge:

  evidence  - do the citations in this section survive the audit? (fed from EVIDENCE-AUDIT.md)
  logic     - does the conclusion follow from the premises actually stated?
  counter   - is there stronger evidence this section ignored or contradicts?
  overreach - is the claim stronger than the support carries?

Each returns per-claim verdicts, not prose. The reconciler applies a survival rule; the judge
renders the verdict last, and never authors the argument it is judging.
"""

import glob
import json
import os
import re

from pipeline import KANBAN_BODY_CAP

# A section shorter than this carries no argument worth attacking (a heading, a TL;DR line).
MIN_SECTION_CHARS = 400
MAX_SECTION_CHARS = 12_000

LENSES = {
    "logic": {
        "profile": "logic",
        "title": "Logic: does %s hold together?",
        "ask": """Attack the REASONING in this section. You are not arguing the opposite position and you
are not writing a rebuttal - you are checking whether the argument as written is sound.

Look for, and name where you find them:
  - a conclusion that does not follow from the premises actually stated
  - a premise smuggled in without support
  - correlation presented as causation
  - a single data point generalised to a trend
  - a range or estimate reported as a measurement
  - an analogy doing load-bearing work it cannot carry
  - equivocation: a term meaning one thing in the premise and another in the conclusion
  - a comparison against an unstated or shifting baseline

A section can be entirely factual and still fail here. Judge the inference, not the topic.""",
    },
    "counter": {
        "profile": "redteam",
        "title": "Counter-evidence: what does %s ignore?",
        "ask": """Hunt for evidence this section IGNORED or CONTRADICTS. You are not writing the opposing
case - you are testing whether the support offered survives contact with what else is known.

Search for: a more recent figure that supersedes the one used, a larger or better-designed
study reaching a different conclusion, a restatement or correction, a disclosure that changes
the picture, a peer or comparable the section omits that would weaken it.

Report only what you can cite. "There might be counter-evidence" is not a finding. If the
section's support holds up against what you can find, say so plainly - a section that survives
this lens is a stronger section, and reporting it as weak would be as wrong as missing a flaw.""",
    },
    "overreach": {
        "profile": "logic",
        "title": "Overreach: is %s claiming more than it shows?",
        "ask": """Compare the STRENGTH of each claim against the strength of its support.

Flag where the text asserts more than the evidence carries:
  - "will" where the support says "may"
  - "demonstrates" where the support is suggestive
  - a point estimate where the source gives a range
  - a causal verb over a correlational finding
  - a company-wide claim resting on one segment or one quarter
  - confidence language ("clearly", "obviously") standing in for evidence

For each, quote the sentence and state what the support would actually license. This lens does
not dispute facts; it disputes the distance between the fact and the sentence.""",
    },
}

SECTION_BODY = """{ask}

The section is in {section_file}, in this working directory. Read that file. It contains the
section's full text and, where the evidence audit has already judged them, the verdict on every
source that section cites.

Do NOT fetch anything. Do NOT open other reports. You are judging this passage as written.

Append ONE line per finding to {out} as you go:

  <severity> | <section> | <the sentence at fault, quoted> | <what is wrong, in one line>

severity is `fatal` (the claim cannot stand), `serious` (the claim must be weakened), or
`minor` (imprecision worth fixing). If the section survives your lens with nothing to report,
write exactly one line:

  clean | <section> | - | nothing found under this lens

Saying "clean" when the section is sound is a real result and is expected often. Manufacturing
a finding to look thorough is the failure this lens guards against in others.

When done, kanban_complete with metadata: {{"section": "{section}", "findings": N}}
"""


class Critique:
    """Per-section adversarial evaluation of a finished document."""

    def __init__(self, pipe, runner, target, audit_file="EVIDENCE-AUDIT.md"):
        self.p = pipe
        self.runner = runner
        self.target = target            # e.g. "MEMO.md"
        self.audit_file = audit_file

    # ── sections ────────────────────────────────────────────────────────────

    def sections(self):
        """Claim-bearing sections of the target document, as (name, text).

        Split on the document's own headings. A section under MIN_SECTION_CHARS carries no
        argument to attack - a heading, a one-line summary - and is skipped rather than given
        a card that can only report "clean".
        """
        path = os.path.join(self.p.reports, self.target)
        if not os.path.exists(path):
            return []
        text = open(path, encoding="utf-8", errors="ignore").read()
        # Footnote definitions are references, not argument.
        text = "\n".join(l for l in text.split("\n") if not re.match(r"^\s*\[\^?\d+\]:", l))
        marks = [(m.start(), m.group(2).strip())
                 for m in re.finditer(r"^(#{2,4})\s+(.+?)\s*$", text, re.M)]
        out = []
        for i, (off, name) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            body = text[off:end].strip()
            if len(body) < MIN_SECTION_CHARS:
                continue
            out.append((name, body[:MAX_SECTION_CHARS]))
        return out

    def audit_lines_for(self, section_text):
        """Verdicts on the citations this section uses, so the adversary sees what the evidence
        audit already found rather than re-deriving it."""
        path = os.path.join(self.p.reports, self.audit_file)
        if not os.path.exists(path):
            return []
        used = set(re.findall(r"\[\^?(\d+)\]", section_text))
        if not used:
            return []
        hits = []
        for line in open(path, encoding="utf-8", errors="ignore"):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 3 and re.match(r"^\[?\^?\d+\]?$", parts[2]):
                n = re.sub(r"\D", "", parts[2])
                if n in used and not line.startswith("supported"):
                    hits.append(line.rstrip())
        return hits

    # ── fanout ──────────────────────────────────────────────────────────────

    def fanout(self, dry_run=False):
        p = self.p
        secs = self.sections()
        if not secs:
            print("  no claim-bearing sections found in %s" % self.target)
            return []
        print("  %d section(s) x %d lens(es) = %d card(s)"
              % (len(secs), len(LENSES), len(secs) * len(LENSES)))

        ids = []
        for si, (name, body) in enumerate(secs, 1):
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "section-%d" % si
            section_file = "CRITIQUE-section-%02d.md" % si
            if not dry_run:
                lines = ["# %s" % name, "", body, ""]
                bad = self.audit_lines_for(body)
                if bad:
                    lines += ["", "## Evidence audit findings on this section's citations", "",
                              "These citations did NOT pass the evidence audit. Weigh any claim "
                              "resting on them accordingly.", ""]
                    lines += ["- %s" % b for b in bad]
                with open(os.path.join(p.reports, section_file), "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines))
            for key, lens in LENSES.items():
                out_file = "CRITIQUE-%s-%02d.md" % (key, si)
                card_body = SECTION_BODY.format(
                    ask=lens["ask"], section_file=section_file, out=out_file,
                    section=name.replace('"', "'"))
                if len(card_body.encode()) > KANBAN_BODY_CAP:
                    raise SystemExit("critique card body over the 8KB cap for %s/%s"
                                     % (name, key))
                tid = p.create(lens["title"] % ('"%s"' % name[:40]), lens["profile"],
                               card_body, runtime_min=25, priority=28, dry_run=dry_run)
                ids.append(tid)
        return [i for i in ids if i != "DRY"]

    # ── consolidate ─────────────────────────────────────────────────────────

    SEVERITIES = ("fatal", "serious", "minor", "clean")

    def merge(self, out_name="CRITIQUE.md"):
        p = self.p
        parts = sorted(glob.glob(os.path.join(p.reports, "CRITIQUE-*-*.md")))
        parts = [f for f in parts if "-section-" not in os.path.basename(f)]
        if not parts:
            print("no critique parts found")
            raise SystemExit(1)
        counts = {s: 0 for s in self.SEVERITIES}
        rows, seen = [], set()
        for f in parts:
            lens = os.path.basename(f).split("-")[1]
            for line in open(f, encoding="utf-8", errors="ignore"):
                t = line.strip()
                if not t or t.startswith("#") or "|" not in t:
                    continue
                sev = t.split("|")[0].strip().lower()
                if sev not in counts or t in seen:
                    continue
                seen.add(t)
                counts[sev] += 1
                rows.append("%-9s | %s" % (lens, t))
        with open(os.path.join(p.reports, out_name), "w", encoding="utf-8") as fh:
            fh.write("# Critique - adversarial evaluation of the argument\n\n")
            fh.write("Each claim-bearing section was attacked independently under four lenses: "
                     "evidence, logic, counter-evidence and overreach. The lenses judge whether "
                     "an argument is SUPPORTED, not which direction it points - a bear case "
                     "whose evidence fails is demoted exactly as a bull case would be.\n\n")
            for s in self.SEVERITIES:
                fh.write("- **%s**: %d\n" % (s, counts[s]))
            fh.write("\n`lens | severity | section | sentence | what is wrong`\n\n")
            for r in sorted(rows):
                fh.write("%s\n" % r)
        print(json.dumps({"parts": len(parts), **counts}))
        return counts
