#!/usr/bin/env python3
"""Fan out the rx-review analysis graph, one stage-phase per invocation.

rx.py's Stage 6/7/8 Begin cards exec this with a --phase. Each phase builds only its own
cards with `hermes kanban create`, hanging them off the running Begin card (HERMES_KANBAN_TASK)
and splicing what a downstream Barrier must wait on in front of that Barrier:

    --phase research               Stage 6: Research Begin. Creates the four substage (6a-6d)
        (no --family)              Begin+Barrier shells and wires them; writes coverage.md.
    --phase research --family X     A substage Begin (6a/6b/6c/6d). Builds that family's workers:
        substances|markers|         per substance/marker/trend, SHARDED — the card's numbered
        trends|screens              questions become part cards that run in parallel and never
        |                           read each other, plus one synthesis card gated on them.
        |                           screens = 6d's two whole-regimen screens.
    --phase adversarial            Stage 7. Packs reports into window-sized chunks, then chunk x
        |                          lens (logic, counter-evidence, overreach, status-quo) + the
        |                          citation audit; a merge per lens + the citation-audit merge,
        |                          all spliced ahead of `Stage 7: Adversarial Complete`.
    --phase conclude               Stage 8. Reconcile -> Assemble -> hostile final review.

Card bodies are templates — substance and marker names are substituted in, so adding a
row to the regimen file is the only thing needed to get it researched.

Usage:
    python3 ~/hermes-skills/rx-review/scripts/fanout.py --phase research --dry-run   # preview the shells
    python3 ~/hermes-skills/rx-review/scripts/fanout.py --phase research --family substances

Re-running is safe: every card carries an idempotency key derived from its title.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rxkanban                                              # noqa: E402
from rxkanban import announce, subscribe                    # noqa: E402,F401
from rxkanban import discord_channel as _discord_channel    # noqa: E402,F401
import sys

BOARD = os.environ.get("RX_BOARD", "rx-review")
# RX_INPUTS lets the parsing be exercised against fixtures without touching the real inputs.
# The default is the skill's own scripts/ dir (inputs/ lives beside this file), so the pipeline
# relocates with the skill. Card bodies carry the expanded path verbatim (worker's write tool
# expands ~), pointing through `current` so every card writes into this run's timestamped dir.
INPUTS = os.path.expanduser(os.environ.get(
    "RX_INPUTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "inputs")))
# Per-run output dir via the `current` symlink rx.py's start_run() swaps at Stage 1. Kept in tilde
# form (bodies carry it verbatim; the worker's write tool expands ~), pointing through `current` so
# every card the fan-out creates writes into this run's timestamped dir.
REPORTS = os.path.join(os.environ.get("RX_REPORTS_ROOT", "~/.hermes/reports/rx-review"), "current")
REGIMEN = os.path.join(INPUTS, "regimen-final.md")
LABS = os.path.join(INPUTS, "labs-succinct.md")
LABS_FULL = os.path.join(INPUTS, "labs-complete.md")
HERMES = os.path.expanduser("~/.local/bin/hermes")

SKIP_ROW = re.compile(r"example row|^-+$|^\s*$", re.I)


# ── parsing ────────────────────────────────────────────────────────────────

def _rows(path):
    """Yield markdown table rows as lists of stripped cells."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(not c for c in cells):
            continue
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue  # separator row
        yield cells


def _header_index(cells, *names):
    low = [c.lower() for c in cells]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def read_substances():
    """The settled regimen, from the numbered 6-field regimen-final.md.

    regimen-final.md is the definitive regimen — every draft item flows through stage 3 into it,
    and an item the user dropped at the barrier is simply absent from it. It is the SOLE research
    source. Each data row is `| # | Name | Ingredients | Quantity | Schedule | Started | Confidence |`;
    column lookup is by header name, so this returns one dict per row with the Name and the Schedule (as
    `when`) regardless of column order. There is no `type` column any more.
    """
    found, order = {}, []

    def add(name, when, started=""):
        # Dedup by the same alphanumeric-only key rx._flat uses, keeping the FIRST occurrence.
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if not key or key in found:
            return
        found[key] = {"name": name, "type": "", "note": "", "when": when, "started": started}
        order.append(key)

    hdr = None
    i_name = i_when = i_started = None
    for cells in _rows(REGIMEN):
        low = [c.lower() for c in cells]
        if hdr is None and "name" in low and "ingredients" in low:
            hdr = cells
            i_name = _header_index(cells, "name")
            i_when = _header_index(cells, "schedule", "time(s) taken", "when")
            i_started = _header_index(cells, "started")
            continue
        if hdr is None:
            continue
        # skip the leading `#`/number-only column rows and the separator
        name = cells[i_name] if i_name is not None and i_name < len(cells) else ""
        if not name or SKIP_ROW.search(name) or name.startswith("("):
            continue
        if name.lower() in ("name", "#"):
            continue
        if SKIP_ROW.search(" ".join(cells)):
            continue
        when = cells[i_when] if i_when is not None and i_when < len(cells) else ""
        started = cells[i_started] if i_started is not None and i_started < len(cells) else ""
        add(name, when, started)
    return [found[k] for k in order]


def read_trends():
    """Trending markers, from rx.py's single implementation. Never re-derived here."""
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "rx_pipeline_tr", os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx.py"))
        _rx = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_rx)
        return _rx.trends()
    except Exception as exc:                                   # noqa: BLE001
        print("  ! could not read the trend list from rx.py (%s); no trend cards" % exc)
        return []


def read_markers():
    """Markers currently out of range, from rx.py's single authoritative implementation.

    This used to re-derive the list here, and the copy had the same defect the others did: it
    dropped any marker whose newest TABLE row carried no H/L flag. The 05/27 rows record their
    flag only in the narrative section, so alkaline phosphatase, iron, LYM% and TIBC were all
    silently denied a research card while superseded cholesterol markers got one.

    Four implementations of "what is abnormal" produced four answers. There is now one, in
    rx.py, and this defers to it.
    """
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "rx_pipeline", os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx.py"))
        _rx = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_rx)
        # FAIL CLOSED ON A MISSING REVIEW. The lab review is no longer a gate this checks — it is
        # the Stage 5 Barrier sitting in front of this card, and by the time analyze runs it has
        # completed. But research on numbers that were never reviewed is research nobody checked,
        # so if labs-complete.md does not exist at all this refuses rather than reporting "no
        # markers found". out_of_range_entries() reads that file; an absent file is an error.
        if not os.path.exists(LABS_FULL):
            raise SystemExit(
                "labs-complete.md does not exist — stage 5 (`review_labs`) has not run.\n"
                "Marker cards are created from the reviewed labs; there is nothing to build on.")
        names = []
        for e in _rx.out_of_range_entries():
            # Keep the FULL name, qualifier and all. Stripping the trailing "(25-OH)" here made
            # the `Marker:` family's exclusion key ("vitamind") disagree with the ignore recorded
            # from the review card ("vitamind25oh"), so an ignored qualifier-named marker still
            # got a research card — while `Trend:` (which keeps the raw name) correctly excluded
            # it. The qualifier also distinguishes the same analyte under two specimens, which the
            # user must be able to ignore independently. All three now normalise identically.
            nm = re.split(r"[:—–]", e, 1)[0].strip()
            # NOT filtered here. Exclusions are applied in shard(), at the one point where
            # anything becomes a research card, so every family inherits them. The marker is
            # still out of range and still belongs in labs.md, in verify-labs and in the report
            # the user reads - "do not research this" is a different statement from "this
            # finding does not exist".
            if nm and nm not in names:
                names.append(nm)
        if names:
            return names
        print("  (rx.py reported no out-of-range markers)")
        return []
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        # FAIL CLOSED. This used to fall back to a second, independent parse of labs.md, kept
        # so "a broken import cannot silently yield zero marker cards". That traded one silent
        # failure for a worse one: the fallback consulted NEITHER labs_confirmed() NOR
        # is_ignored(), so any import error turned off the lab gate and the user's exclusions
        # and built marker cards on numbers nobody had confirmed. It was also a second
        # implementation of "what is abnormal" inside a function whose own docstring says there
        # is now exactly one.
        #
        # An unreadable input is an error, never "nothing found".
        raise SystemExit(
            "Could not read the out-of-range markers through rx.py (%s).\n"
            "Refusing to re-derive them here: this is the only place that enforces the lab\n"
            "gate and the user's exclusions, and a second parser would answer a different\n"
            "question. Fix the import and re-run." % exc)


# ── card bodies ────────────────────────────────────────────────────────────

# The labs are 13,500 tokens even condensed, so a card only loads them if it has a question
# that consults them. Substance parts 1 (indications and evidence quality) and 3 (absorption,
# timing, cost) have none: they are about the SUBSTANCE, not about this user. Part 3's one
# user-specific question - "the user currently takes this at X" - is interpolated straight into
# the card body, so it needs no file either. Part 2 asks which markers the substance moves and
# what in the user's labs looks related, so it gets them; marker and trend cards are ABOUT the
# labs and always get them.
LABS_LINE = ("Also read {inputs}/labs-succinct.md — the user's lab results. If it is absent, "
             "read {inputs}/labs-complete.md instead and say so in your report.")
NO_LABS_LINE = ("Answer these questions from the literature; every user-specific fact they "
                "need is stated above.")

COMMON = """
Read first: {inputs}/regimen-final.md — the authoritative regimen.
{labs_line}

Every factual claim needs a citation to a page you fetched THIS run. Find pages by SEARCH first,
then fetch a URL taken FROM the results — copy a result's `url` field verbatim:

    python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "..." --scope literature
    python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "..." --scope products
    python3 ~/hermes-skills/web-access/scripts/web_access.py fetch --url "<a url from the results>"

`--scope literature` searches papers, trials and drug labels (PubMed, Cochrane, DailyMed);
`--scope products` searches manufacturer and retailer pages for supplement facts and dosing. The
browser tier is automatic: if fetch reports `"outcome": "unreadable"` (a JS or bot wall, already
given a browser render) or `"unreachable"`, search again and fetch a different result. Cite every
claim; drop any you cannot cite.

Use substance and marker names only in a search query — never the user's values, age, or
conditions. Do not recommend a dose, a change, or a stop; you supply evidence, a clinician decides.
"""


TIMING_Q = """

8. TIMING CHECK. The user currently takes this at: {when}
   Compare that against what the evidence supports. Say whether the current timing is
   well-supported, suboptimal, or actively problematic, and cite why. If a change looks
   indicated, describe it as an option with its rationale — never as an instruction.
   If the evidence on timing is weak or conventional rather than demonstrated, say so."""


INTERACTIONS = """Screen the user's FULL regimen for interactions and timing conflicts.

{common}
Read the `substance-*.md` files in {reports}/ as your inputs.

1. Pairwise interactions across every combination, including supplement-drug, supplement-
   supplement, and food-drug.
2. Additive effects: several substances pushing the same physiology in the same direction.
3. Absorption conflicts and separation windows (e.g. minerals blocking absorption of each
   other or of a drug).
4. Timing: what to keep apart, and what to space relative to meals. Using the `time(s) taken`
   column in {inputs}/regimen-final.md, state for each conflict whether the user's real
   schedule triggers or avoids it.

Cite an interaction database entry or a label section for EVERY interaction asserted, and rank
findings by severity.

Write {reports}/interactions.md.

{endnote_rule}
Then kanban_complete with a summary and metadata:
  {{"pairs_checked": N, "flagged": N, "severe": ["..."]}}
"""

SCHEDULE = """Review the user's ACTUAL daily schedule against evidence-based timing.

{common}
Inputs: the `time(s) taken` column in {inputs}/regimen-final.md (the user's real schedule),
every substance report in {reports}/, and {reports}/interactions.md. List any row with no time
recorded under "not stated".

Produce a table of the current day:

| time | what is taken | with food? | conflicts at this time | evidence for this timing |

Then a proposed alternative schedule, as OPTIONS with reasons:

| time | what to take | why this time | strength of evidence | what it resolves |

Cover:
1. Separation windows the current schedule violates (e.g. a mineral taken alongside something
   whose absorption it blocks); give the hours apart and cite it.
2. With-food / without-food requirements the current schedule contradicts.
3. Substances stacked at one time where splitting them would help absorption or tolerability.
4. Time-of-day effects that are genuinely established, marking where common advice is
   convention rather than demonstrated.
5. Practicality: prefer the fewest changes that resolve the most conflicts, and say which
   single change matters most.

Where a prescription label specifies timing, quote it, treat it as authoritative, and flag the
conflict. Present every change as an option with evidence for a prescriber or pharmacist to
confirm.

Write {reports}/SCHEDULE.md.

{endnote_rule}
Then kanban_complete with a summary and metadata:
  {{"timed": N, "not_stated": N, "conflicts": N, "top_change": "..."}}
"""

RECONCILE = """Merge the adversarial verdicts into a vetted claim set.

Inputs in {reports}/: CONTEXT-AUDIT.md, the four lens reports LOGIC.md, REFUTATION.md,
OVERREACH.md and NULLHYP.md, plus the substance, marker, interaction, SCHEDULE and
EFFICACY reports. Apply the same survival rule to timing claims as to dose claims.

Each lens grades every finding `fatal` (the claim cannot stand), `serious` (weaken before use),
`minor` (imprecision worth fixing) or `clean` (challenged and held). Read all four grades.

A claim enters the brief only when ALL hold:
  - CONTEXT-AUDIT verdict is `supported` (a claim whose citation is absent from
    CONTEXT-AUDIT.md has no verdict; treat that as a failed audit)
  - no lens recorded a `fatal` finding against it
  - no lens recorded a `serious` finding that has not been narrowed to fit
  - `minor` findings are carried into the brief as corrections

CONTEXT-AUDIT.md sorts unsupported claims into two sections; label them differently in your
output:
  - "## Evidence findings" — the source was read and does not support the claim (a finding
    about the literature).
  - "## Unverified" — the source could not be read or the quote was never located (a finding
    about tooling, silent on whether the claim is true).

Failing one test -> move it to "Uncertain — for clinician", stating which test it failed and
why. Failing two or more -> drop it, and record that it was dropped. Where NULLHYP's steelman
survives, keep BOTH sides.

ESCALATION: for any surviving HIGH-STAKES claim — an interaction warning, or anything implying
a regimen change — create a focused per-claim deep-refutation card:
  kanban_create(title="Deep refutation: <claim>", assignee="rx-redteam", body="...")
and declare the ids in kanban_complete(created_cards=[...]). Cap at 5.

Write {reports}/VETTED.md — what survived, what was demoted, what was dropped, each with its
reason. Then kanban_complete with metadata:
  {{"survived": N, "uncertain": N, "dropped": N, "escalated": N}}
"""

SYNTH = """Assemble the prescriber discussion brief from {reports}/VETTED.md.

Write {reports}/{brief}:
  1. Regimen overview
  2. Per-substance evidence summary
  3. Interaction flags, ranked by severity
  4. Schedule — the current day as recorded, the conflicts it creates, and evidence-based
     timing options (from SCHEDULE.md), with the single highest-value change called out and
     recommended when the surviving evidence supports it, stated with that evidence.
  5. Redundancy: substances duplicating a mechanism, and any whose evidence for continuing
     is weak.
  6. Medication efficacy — from the efficacy-*.md reports (via VETTED.md): for each dated
     medication, the before/after comparison of the markers it is expected to move, with the
     post-start draw count. Frame as observation, not conclusion; carry "too early to tell"
     through verbatim.
  7. Lab observations, explicitly framed as hypotheses.
  8. Prioritized questions for the prescriber.
  9. What this review did NOT cover — read {inputs}/coverage.md and reproduce it, stating that
     each listed marker and item was excluded at the user's request and not investigated. When
     the file is absent or empty, write "Nothing was excluded."

Use ONLY the claims that survived in VETTED.md, with every correction it made applied verbatim. A
claim VETTED dropped or demoted must not reappear in any form, and NO recommendation may rest on
one. Carry every citation through; each sentence must trace to a child report. Use web access only
to check something already claimed. Surface uncertainty in plain language — "two trials disagree"
beats "results are mixed" — and keep contradictions visible. Where the surviving evidence supports
it, recommend a dose, a timing change, or a stop and state the evidence behind it; where it does
not, say so plainly.

Then kanban_complete with metadata:
  {{"claims": N, "questions": N, "uncertain": N, "excluded": N}}
"""

DEVIL = """Attack {reports}/{brief} as a hostile reviewer. Assume it is overconfident.

The brief MAY recommend a dose, a timing change, or a stop — that is allowed. What it may not do
is base a recommendation on a claim that did not survive. Look for: overstated confidence;
uncertainty buried in reassuring prose; a hypothesis presented as a finding; a recommendation
that rests on a claim VETTED or AUDIT.md dropped, demoted, or discredited; a claim that arrived
with a citation and appears here without one; a claim AUDIT.md marked misquoted or unsupported
reappearing as though clean; anything a pharmacist would challenge on sight; and OMISSION — a
serious interaction or safety signal present in the child reports but missing from the brief.

Scrutinize the SCHEDULE section hardest. A schedule change to a PRESCRIPTION that contradicts its
label without flagging the conflict is FATAL — as is any timing claim that AUDIT.md or LOGIC.md
marked defective reappearing here as clean, or a timing recommendation that rests on one.

Quote every sentence you attack. Rate each defect `fatal` (the claim cannot stand), `serious`
(weaken before use), or `minor` (imprecision worth fixing), and say what it should say instead.
Judge the document against its own sources.

Do NOT block or halt — always complete. Flag each defect IN PLACE: edit {reports}/{brief} and,
in the same section the claim sits in, insert a line directly after the sentence it concerns —

    > **[review: fatal|serious|minor]** <the problem, one line> — should say: <the correction>

so the flag rides with the claim a reader is looking at. Leave the flagged claim in place; do not
delete it. A claim the audit discredited, or a recommendation built on one, is `fatal` — flag it,
do not remove it. "This brief is sound" is a valid verdict when it survives a real attempt to
break it.

Also write the full critique to {reports}/CRITIQUE.md.
Then kanban_complete with metadata: {{"fatal": N, "serious": N, "minor": N}}
"""


# ── card creation ──────────────────────────────────────────────────────────


TREND_INTRO = """Assess whether a TREND in one lab marker is clinically significant.

    MARKER:    {name}
    DIRECTION: {direction} across {points} draws
    SERIES:    {series}
    REFERENCE: {ref} {unit}
    STATUS:    {status}

Judge the DIRECTION, not the value: a marker inside its reference range at every draw can still
be the most informative thing in the panel."""


# The endnote contract, in ONE place. It lived only in SUBSTANCE; MARKER, INTERACTIONS and
# SCHEDULE asked for "numbered endnotes" with no format, and TREND asked for nothing at all.
# trend-rbc.md therefore invented its own: article title in quotes, claim as an unquoted
# paraphrase. The audit cannot verify a paraphrase, so two significant citations - a diagnostic
# haematocrit threshold and renal cell carcinoma as a cause of raised RBC - were reported
# absent for having nothing quotable in them.
# ── Sharding a research topic ────────────────────────────────────────────────────────────
#
# A research card used to ask its whole question list in one context, and that context did not
# fit. Measured on the run of 2026-07-31: peak contexts of 104.5k, 103.2k and 95.8k tokens
# against a compression threshold of 90k, reached after 20+ web_extracts each. Every one of
# them then tried to compact, and compaction failed - six 429s and five timeouts on one card,
# twelve attempts on another - until the 45-minute cap killed the run. Eight cards blocked.
#
# The token cost is the visible half. The other half is worse: a card that compacts mid-run
# answers from a SUMMARY of its sources, while the endnote rule below demands the verbatim
# sentence. A compacted answer looks exactly like a real one, which is the same failure the
# adversarial lenses were split to cure - here it is one layer up, in the layer that gathers
# the evidence rather than the layer that judges it.
#
# So the question list is the shard boundary. It was already written, already numbered, and
# already grouped by a human; inventing new boundaries would be worse. Independent questions
# become PARTS that run in parallel and never read each other; the questions that genuinely
# synthesise become the one card gated behind them. Each part peaks near 25-30k - a third of
# the threshold - so nothing compacts and no quote is ever summarised away.
PART_BODY = """{intro}

This is PART {n} of {total} on this topic. Answer ONLY the question(s) below; another card
assembles the final report.

{common}
{questions}

Build your findings in {reports}/{frag} incrementally: read it first and keep any findings
already recorded there, then append each new finding with its endnote as soon as you have its
citation.

Structure it as a short heading, your answer, then a `## Endnotes` section in the format below,
under ~500 words of prose.

Reporting "the evidence here is weak" is a successful result.

{endnote_rule}
Then kanban_complete with a <=120 word summary and metadata:
  {{"topic": "{name}", "part": {n}, "sources": N}}
"""

SYNTH_BODY = """Assemble the final report on {name}.

Read these part files — your inputs and the only place the cited evidence lives:

{frag_list}

Search only if the question below needs something the parts did not cover, and cite anything new
the same way.

{questions}
Write {reports}/{out}

Assemble one coherent report: the parts' findings in a sensible order, then the question above,
then ONE combined `## Endnotes` section. Renumber every part's endnotes continuously and repoint
each claim at its new number, copying each quoted sentence and URL exactly as the part wrote it.
If a part file is missing or empty, say so.

{endnote_rule}
Then kanban_complete with a <=200 word summary and metadata:
  {{"topic": "{name}", "parts_read": N, "sources": N}}
"""


# The groupings below are the ORIGINAL numbered questions, moved not rewritten. Independent
# questions are parts; the ones that reason over the others' answers are the synthesis. Keeping
# the wording identical matters: each line was written against a specific failure (see the
# comments on TREND and ENDNOTE_RULE), and paraphrasing while resharding would quietly discard
# that history.
PART_RUNTIME, SYNTH_RUNTIME = "25m", "30m"

# Intermediates live in reports/ beside the finished reports, and every consumer that globs
# that directory skips them by this prefix. Changing it means changing the skip lists in
# lenses.py and verify.py too - rx_test.py asserts they agree.
PART_PREFIX = "PART-"

MARKER_PARTS = [
    "1. What does {name} measure, and what does out-of-range in this direction generally\n"
    "   indicate?",
    "2. Which substances in the user's regimen are known to move {name}, in which direction,\n"
    "   and how strong is that evidence?",
    "3. What NON-regimen explanations are common (timing of draw, hydration, recent exercise,\n"
    "   fasting state, assay variation, intercurrent illness, normal biological variation)?",
]
MARKER_SYNTH = """4. What would distinguish those explanations from each other — what would a clinician
   typically check next?

Present possibilities with evidence, saying which are more or less likely and why.
"""

TREND_PARTS = [
    "1. Is a {direction} trend of this size, in this marker, over this interval clinically\n"
    "   meaningful — or is it within ordinary biological and analytical variation? Quantify the\n"
    "   variation you are comparing against; \"could be normal variation\" without a figure is not\n"
    "   an answer.",
    "2. What are the common causes of a {direction} {name}, and which are benign?",
    "3. Does anything in the user's regimen ({inputs}/regimen-final.md) plausibly drive this\n"
    "   direction — a supplement, a dose, an interaction, a timing effect?",
]
TREND_SYNTH = """4. At what value or rate would this stop being watchful-waiting and start warranting action?
5. What single follow-up test or repeat interval would settle it?

Say what the trend means, what would explain it, and what would resolve the question, leaving
the decision to a prescriber. When the trend is unremarkable, say so plainly — reporting a dull
trend as dull is a correct result.
"""

# Stage 6c splits the old three-part trend card into a TRIAGE that judges the trend and a
# deterministic DISPATCH that either writes a skip report or deepens it — see ARCHITECTURE.md
# "Stage 6c". The triage answers only TREND_PARTS[0] and reads no labs (the series is in the
# intro); it writes its prose to a PART- fragment (part 1, which the synthesis later reads) AND a
# two-line verdict the dispatch parses. The verdict file reuses the PART- skip prefix so no
# report-globbing consumer reads it as a finished report.
TREND_TRIAGE_BODY = """{intro}

Answer only the question below; two later cards handle the rest.

{common}
{question}

Write your answer to {reports}/{frag}: a short heading, your answer, then a `## Endnotes` section
in the format below, under ~500 words.

Then write {reports}/{verdict} as exactly two lines:

    MEANINGFUL: yes
    REASON: <one sentence with the numbers — the change, over how many draws, against the
    reference-change-value or reference range>

Write `MEANINGFUL: yes` when the change clears ordinary biological and analytical variation, when
the marker is out of range at any draw, or whenever you are unsure. Write `MEANINGFUL: no` only
when the change stays within ordinary variation and the marker is in range at every draw.

{endnote_rule}
Then kanban_complete with a <=120 word summary and metadata:
  {{"topic": "{name}", "part": 1, "meaningful": "yes|no", "sources": N}}
"""

# The dispatch is a thin "run this verb" card, like a substage Begin: the verb decides and
# self-completes it. Parented on the triage, so the verdict it reads is already written.
TREND_DISPATCH_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/hermes-skills/rx-review/scripts/rx.py trend-dispatch --slug {slug} --triage {triage}
"""

# Substance questions group by theme rather than one-per-card: eight questions would make eight
# cards whose combined overhead beats the tokens saved, and efficacy/safety/practical is how a
# monograph is organised anyway.
SUBSTANCE_PARTS = [
    "1. Approved / best-evidenced indications, and the quality of that evidence\n"
    "   (meta-analysis / single RCT / observational / animal / in-vitro / marketing).\n"
    "2. Where the evidence is thin or sources disagree.",
    "3. Common and serious adverse effects, and which are dose-dependent.\n"
    "4. Which lab markers this substance is known to move, and in which direction.\n"
    "5. Anything in the user's labs plausibly related — state as a HYPOTHESIS, not a conclusion.",
    "6. Absorption and timing: with or without food; what blocks or enhances uptake; whether\n"
    "   time of day matters for efficacy, tolerability, or side effects (and whether that is\n"
    "   established or merely conventional wisdom). If the label or monograph specifies timing,\n"
    "   quote it — a label instruction outranks a general finding.\n"
    "7. Timing relative to other things: what this must be separated from, and by how long."
    "{timing_q}",
]
SUBSTANCE_SYNTH = """Bring the parts together. Where two parts bear on the same point — an adverse effect that is
also a timing constraint, an indication whose evidence is thin — say so.
"""

EFFICACY_BODY = """Assess whether {name} is moving the lab markers it is known to affect.

The user's recorded start date for this substance is: {started}

1. Read {reports}/PART-research-{slug}-2.md (the part-2 fragment — it holds the answers to
   questions 4 and 5) and find its answer to QUESTION 4 — which lab markers {name} is known to
   move, and in which direction. That answer is the ONLY marker list you use; do not re-research
   the literature on this card.
2. For EACH lab marker named there, run:
       python3 ~/hermes-skills/rx-review/scripts/rx.py before-after --marker <marker> --since {started}
   The verb splits the user's confirmed dated lab series at the start date and prints the pre
   values, the post values, the delta, and the number of post-start draws. It is pure
   arithmetic — use its numbers as-is; do not recompute or adjust them. If a marker name is
   ambiguous (say, a blood and a urine protein share it), say so instead of picking one.
3. Compare the observed direction/magnitude against what question 4 said to expect.

Write {reports}/efficacy-{slug}.md:
- One entry per marker: the expected direction (from question 4), the observed pre→post values,
  the delta, and the post-start draw count.
- "Too early to tell" is a valid, first-class result — the verb says so when there are fewer
  than two post-start draws. ALWAYS report the post-start draw count, even when it is 0 or 1.
- Where the observed change is consistent with the expected effect, say so plainly. Where it is
  not, or the data are too thin, say that plainly. A single early post draw is not evidence of
  effect — do not over-read it.
- Do NOT recommend a dose, a change, or a stop. You supply the comparison; a clinician decides.
- Carry the part-2 citation for each "expected to move X" claim into the endnotes, and label
  the observed values as "from the user's labs" (no external citation for the arithmetic).
"""


# What shard() actually skipped, in the order it skipped it. Recorded rather than re-derived so
# coverage.md describes the cards that were NOT created, not a second opinion about what should
# have been - two implementations of "what was excluded" would eventually disagree, and the one
# the user reads would be the wrong one.
EXCLUDED = []


def _excluded(subject, name):
    """True when the user asked for this subject not to be researched.

    Both lists are read through rx.py rather than re-derived: "what is excluded" has one
    implementation, for the same reason "what is out of range" does.
    """
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "rx_pipeline_ex", os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx.py"))
        _rx = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_rx)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit(
            "Could not read the exclusion lists through rx.py (%s).\n"
            "Refusing to build research cards without them: a marker the user asked to skip,\n"
            "or an item they could not confirm, would be researched anyway. Fix and re-run."
            % exc)
    if subject == "marker":
        return _rx.is_ignored(name)
    if subject == "substance":
        return _rx.is_dropped(name)
    return False


def shard(args, label, name, slug, intro, parts, synth_q, out, fmt, priority, qfmt=None,
          labs_parts=None, subject=None):
    """One card per question group, plus a synthesis card gated on all of them.

    Returns the SYNTHESIS id, because that is the card that produces the report every
    downstream stage reads — the parts are scaffolding and nothing should wait on them
    individually. Returns None when the subject was excluded (see below).

    labs_parts is the 1-based set of parts whose questions actually consult the user's lab
    results; the rest are told not to read them. None means all of them, which is the safe
    default — a card that reads labs it does not need wastes tokens, but one that silently
    lacks labs it DOES need answers a different question than the one asked.

    EXCLUSIONS ARE APPLIED HERE, at the one point where anything becomes a research card, and
    not in each family's reader. `is_ignored` used to live in read_markers() only, so a marker
    the user asked not to research still got a Trend: card — most of the cost the exclusion was
    meant to avoid, under a title they had never named. `subject` says which list governs:

        "marker"     — ignored_markers(), from `labs-confirm --ignore`
        "substance"  — dropped_items(), from `regimen-confirm --unknown`
        None         — nothing is excluded; the subject is not user-nameable

    A family added later declares its subject and inherits the filter rather than having to
    remember it, which is the whole reason this is one place instead of three.
    """
    if subject and _excluded(subject, name):
        why = ("the user asked not to research it" if subject == "marker"
               else "the user could not confirm its dose")
        print("  (skipping %s: %s — %s)" % (label, name, why))
        EXCLUDED.append((label, name, why))
        return None
    qfmt = dict(qfmt or {}, name=name, inputs=INPUTS)
    # The part workers are PARENTLESS. The substage Begin (6a/6b/6c) is a starter — it creates
    # them, so an edge back to it is always already satisfied and only delays them; parentless,
    # they are eligible at once and run in parallel. Only a follow-on that consumes another
    # worker's output is parented on it — that is the synthesis below, on its parts.
    frags, ids = [], []
    for i, questions in enumerate(parts, 1):
        # PART- prefix, not a -part suffix. Four later stages glob reports/*.md flat and skip
        # intermediates by PREFIX (lenses.py's own LENS-* files do exactly this). Named
        # "marker-x-part1.md" these fragments were picked up as finished research reports by
        # the adversarial lenses, the citation audit AND the interactions card - so partial
        # answers would be judged as if complete, the same evidence reviewed twice, and the
        # lens corpus quadrupled.
        frag = "%s%s-%s-%d.md" % (PART_PREFIX, label.lower(), slug, i)
        frags.append(frag)
        pfmt = dict(fmt)
        if labs_parts is not None and i not in labs_parts:
            pfmt["common"] = COMMON.format(inputs=INPUTS, labs_line=NO_LABS_LINE)
        body = PART_BODY.format(intro=intro, n=i, total=len(parts), name=name,
                                questions=questions.format(**qfmt), frag=frag, **pfmt)
        ids.append(create(args, "%s: %s — part %d/%d" % (label, name, i, len(parts)),
                          "rx-research", body, runtime=PART_RUNTIME, priority=priority))
    frag_list = "\n".join("    %s/%s" % (REPORTS, f) for f in frags)
    body = SYNTH_BODY.format(name=name, total=len(parts), frag_list=frag_list,
                             questions=synth_q.format(**qfmt), out=out, **fmt)
    return create(args, "%s: %s — report" % (label, name), "rx-research", body,
                  parents=[i for i in ids if not rxkanban.is_dry(i)],
                  runtime=SYNTH_RUNTIME, priority=priority - 1)


ENDNOTE_RULE = """## Endnotes

Number every claim that rests on a source. Each endnote gives BOTH the URL and the sentence:

    [n] <source>, "<the verbatim sentence that carries the evidence>" <URL>

Quote the exact sentence that supports the claim, not the article or page title. If the source
supports the claim but no single sentence says so, write that in the endnote instead."""


# Work cards do NOT subscribe. A subscription fires on completion AND on block with no
# way to filter (notify-subscribe has no event selector), and a sharded run is ~150 cards -
# so per-card notification turns the run into narration of its own bookkeeping. The signals
# worth pushing are already sent by other means: the two human gates subscribe themselves in
# rx.py, and phase boundaries go through announce(). A blocked work card is visible in
# `rx.py status` and on the board.
def create(args, title, assignee, body, parents=(), runtime="45m", priority=0):
    """Graph card rooted in reports/, keyed with the rxfan- prefix. Mechanics in rxkanban."""
    tag = getattr(args, "tag", "")
    if tag:
        title = "%s [%s]" % (title, tag)
    if args.dry_run:
        print("  would create: %-58s <- %s  [%s]"
              % (title[:58], ", ".join(parents) or "no parents", assignee))
        return "DRY-" + rxkanban.slugify(title, 12)
    tid = rxkanban.create_card(title, assignee, body, REPORTS, parents=parents,
                               runtime=runtime, priority=priority,
                               key="rxfan-" + rxkanban.slugify(title), notify=False)
    print("  %s  %-58s <- %s  [%s]"
          % (tid, title[:58], ", ".join(parents) or "no parents", assignee))
    return tid


# ── substage shells (Stage 6a-6d) ───────────────────────────────────────────────────────────
# `Stage 6: Research Begin` (analyze-research, no --family) creates these four Begin+Barrier
# shells. Each substage Begin then re-invokes analyze-research for its own --family, which builds
# that family's worker cards and splices them in front of the substage Barrier. The four substage
# Barriers are spliced in front of the spine's `Stage 6: Research Complete`.
# The family builder (this verb) self-completes the substage Begin; the body just runs it.
SUBSTAGE_BEGIN_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/hermes-skills/rx-review/scripts/rx.py analyze-research --family {family}
"""

# A pure sync barrier: its worker cards are its kanban parents, so by the time it runs they are
# done. `settle` completes it. No read_file check, no kanban_complete for the model to do.
SUBSTAGE_BARRIER_BODY = """Run this and report what it printed. Do nothing else:

    python3 ~/hermes-skills/rx-review/scripts/rx.py settle
"""

SUBSTAGES = [
    dict(sub="6a", family="substances",
         begin="Stage 6a: Research Substances", barrier="Stage 6a: Substances Researched",
         begin_purpose="Research each regimen substance — evidence & efficacy, safety & marker "
                       "effects, and timing.",
         barrier_purpose="Confirm every substance report (substance-<slug>.md) is written."),
    dict(sub="6b", family="markers",
         begin="Stage 6b: Research Markers", barrier="Stage 6b: Markers Researched",
         begin_purpose="Research each out-of-range lab marker — what it measures, what moves it, "
                       "and the common benign explanations.",
         barrier_purpose="Confirm every out-of-range marker report (marker-<slug>.md) is "
                         "written."),
    dict(sub="6c", family="trends",
         begin="Stage 6c: Research Trends", barrier="Stage 6c: Trends Researched",
         begin_purpose="Research each marker trending over time — whether the trend is meaningful "
                       "and what drives it.",
         barrier_purpose="Confirm every trend report (trend-<slug>.md) is written."),
    dict(sub="6d", family="screens",
         begin="Stage 6d: Whole-regimen Screens", barrier="Stage 6d: Screens Complete",
         begin_purpose="Screen the whole regimen for interaction/timing conflicts, and review "
                       "the dosing schedule against the evidence.",
         barrier_purpose="Confirm interactions.md (and SCHEDULE.md when the regimen records dose "
                         "times) is written."),
]
_SUBSTAGE = {d["family"]: d for d in SUBSTAGES}


def _brief_name():
    """Canonical final-brief filename for the active run: <date>-rx-review.md, dated from the run
    dir (the day the review started) so a review concluding past midnight keeps one name."""
    try:
        stamp = os.path.basename(os.path.realpath(os.path.expanduser(REPORTS)))
        if len(stamp) >= 10 and stamp[4] == "-":
            return "%s-rx-review.md" % stamp[:10]
    except OSError:
        pass
    return "%s-rx-review.md" % time.strftime("%Y-%m-%d")


def _snapshot_inputs():
    """Copy the run's inputs — the regimen and the lab transcriptions — into <run>/inputs/, so each
    timestamped dir is a self-contained, reproducible record even after `reset` clears the inputs
    area and independent of the content-addressed caches. Best-effort; never fails the conclusion."""
    import glob as _glob                                        # noqa: PLC0415
    import shutil as _shutil                                    # noqa: PLC0415
    dst = os.path.join(os.path.expanduser(REPORTS), "inputs")
    try:
        os.makedirs(dst, exist_ok=True)
        srcs = [os.path.join(INPUTS, n) for n in ("regimen.txt", "regimen-final.md")]
        srcs += _glob.glob(os.path.join(INPUTS, "labs-*.md"))
        for p in srcs:
            if os.path.isfile(p):
                _shutil.copy2(p, os.path.join(dst, os.path.basename(p)))
    except OSError:
        pass


def _fmt():
    """The shared template fill for research card bodies."""
    return dict(inputs=INPUTS, reports=REPORTS, endnote_rule=ENDNOTE_RULE, brief=_brief_name(),
                common=COMMON.format(inputs=INPUTS,
                                     labs_line=LABS_LINE.format(inputs=INPUTS)))


def _my_parents():
    """The running Stage Begin card as a 1-element parent list — empty on a hand run.

    Hermes sets HERMES_KANBAN_TASK to the running card's id when a Begin card's agent runs this
    script, so cards created here hang off that Begin. Unset on a hand run, so they land ready.
    """
    me = os.environ.get("HERMES_KANBAN_TASK")
    return [me] if me else []


def _complete_self(summary="", dry=False):
    """Complete THIS Begin card from fanout — the terminal action the phase owns so the card body
    says only "run this" and the model never calls kanban_complete. No-op on a hand run."""
    me = os.environ.get("HERMES_KANBAN_TASK")
    if me and not dry:
        cmd = [rxkanban.HERMES, "kanban", "--board", rxkanban.BOARD, "complete", me]
        if summary:
            cmd += ["--summary", summary]
        subprocess.run(cmd, capture_output=True, text=True)


def _collect_excluded():
    """Every subject the user asked to leave out, across all three families, as (label, name, why).

    The shard() loops record exclusions into EXCLUDED as a side effect, but the shell-builder (no
    --family) does not run those loops — so it recomputes the same set the same way, applying the
    same `_excluded()` predicate shard() uses, so coverage.md is written once by the one phase that
    sees every family.
    """
    out = []
    for s in read_substances():
        if _excluded("substance", s["name"]):
            out.append(("Research", s["name"], "the user could not confirm its dose"))
    for m in read_markers():
        if _excluded("marker", m):
            out.append(("Marker", m, "the user asked not to research it"))
    for t in read_trends():
        if _excluded("marker", t["marker"]):
            out.append(("Trend", t["marker"], "the user asked not to research it"))
    return out


def phase_research_shells(args):
    """Stage 6: Research Begin — create the four substage (6a-6d) Begin+Barrier shells and wire
    them: 6a/6b/6c off the Research Begin card, 6d off 6a's Barrier, all four Barriers spliced in
    front of the spine's `Stage 6: Research Complete`. Writes coverage.md (the one phase that sees
    every family)."""
    subs = read_substances()
    if not subs:
        print("No substances found in %s (or regimen-final.md)." % REGIMEN)
        print("Fill in the regimen table first — nothing to fan out.")
        raise SystemExit(1)

    # coverage.md — written EVEN WHEN EMPTY so the brief can say "nothing was excluded" rather
    # than omit the section (a missing section and one saying nothing look identical to a reader).
    # PRESERVE the regimen drops Stage 3 already wrote here: a `<n> drop` at the Stage 3 review
    # appends a bullet to coverage.md, and this phase must ADD the marker/trend exclusions to those
    # rather than overwrite them, or a dropped item never reaches the brief's "did not cover".
    excluded = _collect_excluded()
    cov = os.path.join(INPUTS, "coverage.md")
    existing = []
    if os.path.exists(cov):
        existing = [l.rstrip("\n") for l in open(cov, encoding="utf-8", errors="replace")
                    if l.lstrip().startswith("- ")]
    new_lines = ["- **%s** (%s) — %s" % (name, label.lower(), why)
                 for label, name, why in excluded]
    seen, merged = set(), []
    for l in existing + new_lines:                     # Stage 3 drops first, then this phase's
        m = re.search(r"\*\*(.+?)\*\*", l)
        key = re.sub(r"[^a-z0-9]+", "", m.group(1).lower()) if m else l
        if key in seen:
            continue
        seen.add(key)
        merged.append(l)
    if not args.dry_run:
        with open(cov, "w", encoding="utf-8") as fh:
            fh.write("# Excluded from research at the user's request\n\n")
            if not merged:
                fh.write("Nothing was excluded.\n")
            for l in merged:
                fh.write(l + "\n")
    print("  coverage.md: %d subject(s) excluded" % len(merged))

    print("\nStage 6: Research Begin — creating the 6a-6d substage shells%s\n"
          % ("  (DRY RUN)" if args.dry_run else ""))
    begins = {}
    barriers = {}
    for d in SUBSTAGES:
        # 6a/6b/6c get NO parent: this card (the Research Begin) is what creates them, so an edge
        # back to it is always already satisfied and only delays them until this card reaches done.
        # Parentless, they are eligible the moment they are created and run in parallel. 6d is the
        # one real dependency — it needs every substance report, so its Begin waits on 6a's Barrier
        # (runs alongside 6b/6c, after the substance syntheses land).
        begin_parents = [barriers["substances"]] if d["family"] == "screens" else []
        b = create(args, d["begin"], "rx-intake",
                   SUBSTAGE_BEGIN_BODY.format(family=d["family"]),
                   parents=begin_parents, runtime="20m", priority=58)
        bar = create(args, d["barrier"], "rx-intake",
                     SUBSTAGE_BARRIER_BODY,
                     parents=[b] if not rxkanban.is_dry(b) else [],
                     runtime="15m", priority=57)
        begins[d["family"]] = b
        barriers[d["family"]] = bar

    # The four substage Barriers gate `Stage 6: Research Complete`. Splice, because that spine
    # Barrier was created up front by rx.py start and is sitting unstarted behind this card.
    rxkanban.splice(list(barriers.values()), "Stage 6: Research Complete")
    print("\nWired 6a-6d ahead of `Stage 6: Research Complete`. "
          "Watch with:  hermes kanban --board %s list" % BOARD)
    _complete_self("6a-6d substage shells created", dry=args.dry_run)


def phase_research_family(args, family):
    """A substage Begin (6a/6b/6c) — build that family's shard workers, then splice each synthesis
    in front of the substage Barrier. Empty family → creates nothing; its Barrier completes on its
    Begin alone."""
    if family == "screens":
        return phase_screens(args)
    fmt = _fmt()
    d = _SUBSTAGE[family]
    synth_ids = []
    if family == "substances":
        for s in read_substances():
            note = ("\nNOTE: %s." % s["note"]) if s["note"] else ""
            slug = rxkanban.slugify(s["name"], 48)
            started = (s.get("started") or "").strip()
            synth = shard(
                args, "Research", s["name"], slug,
                "Research {name} as taken by the user.{note}".format(name=s["name"], note=note),
                SUBSTANCE_PARTS, SUBSTANCE_SYNTH, "substance-%s.md" % slug, fmt, priority=50,
                subject="substance",
                qfmt={"timing_q": TIMING_Q.format(when=s["when"]) if s["when"] else ""},
                # Only part 2 asks about the user: which markers this moves, and what in their
                # labs looks related. Part 1 is indications and evidence quality, part 3 is
                # absorption, timing and cost — both about the SUBSTANCE. Part 3's one
                # user-specific question carries the answer inline, so it needs no file.
                labs_parts={2})
            # shard() returns None for an excluded subject (and "DRY-…" on a dry run): there is
            # then no synthesis to gate an Efficacy card on and nothing to splice, so the dated
            # row gets no efficacy card either — exclusions win over the start date.
            if not synth or rxkanban.is_dry(synth):
                continue
            synth_ids.append(synth)
            # One Efficacy card per DATED medication (Started non-blank; supplements never get
            # one). Gated on the synthesis — which completes only after part 2 has written
            # PART-research-<slug>-2.md, the q4 answer the card reads — and appended to
            # synth_ids so the splice below also places it in front of the 6a Barrier: Stage 6
            # cannot complete (and Stage 8 cannot assemble a brief) without the report.
            # The start date is IN the title, so the rxfan- key (derived from the title) treats
            # a corrected date as a new card rather than silently reusing the old one.
            if started:
                eff = create(args, "Efficacy: %s (started %s)" % (s["name"], started), "rx-research",
                             EFFICACY_BODY.format(name=s["name"], slug=slug, started=started,
                                                  reports=REPORTS),
                             parents=[synth], runtime=SYNTH_RUNTIME, priority=48)
                if eff:
                    synth_ids.append(eff)
    elif family == "markers":
        for m in read_markers():
            slug = rxkanban.slugify(m, 48)
            synth_ids.append(shard(
                args, "Marker", m, slug,
                "Investigate the user's out-of-range lab marker: %s" % m,
                MARKER_PARTS, MARKER_SYNTH, "marker-%s.md" % slug, fmt, priority=45,
                # Part 1 interprets the user's out-of-range direction, which only the labs file
                # carries (the marker intro is name-only). Parts 2 (regimen vs literature) and
                # 3 (non-regimen explanations) are answered without the user's values.
                subject="marker", labs_parts={1}))
    elif family == "trends":
        for t in read_trends():
            # 6c defers depth: a triage judges the trend and a deterministic dispatch either
            # writes a skip report or spawns the two deeper parts + synthesis (phase_trend_dispatch).
            # The dispatch — or, on the skip path, itself — is the trend's terminal card, so it, not
            # a synthesis, gates the barrier. Exclusions apply HERE, the one place a trend becomes a
            # card, exactly as shard()'s subject="marker" does for the other families.
            if _excluded("marker", t["marker"]):
                print("  (skipping Trend: %s — the user asked not to research it)" % t["marker"])
                EXCLUDED.append(("Trend", t["marker"], "the user asked not to research it"))
                continue
            synth_ids.append(_trend_cards(args, t))

    # shard() returns None for an excluded subject; drop those so a None never reaches splice.
    synth_ids = [i for i in synth_ids if i and not rxkanban.is_dry(i)]
    rxkanban.splice(synth_ids, d["barrier"])
    print("\n%s: %d synthesis card(s) linked in front of `%s`.%s"
          % (d["begin"], len(synth_ids), d["barrier"],
             "  (DRY RUN)" if args.dry_run else ""))
    _complete_self("%s: %d card(s)" % (d["begin"], len(synth_ids)), dry=args.dry_run)


def _trend_intro(t):
    """The shared TREND_INTRO fill for a trend row — used by both the triage and the dispatch."""
    return TREND_INTRO.format(
        name=t["marker"], direction=t["direction"], points=t["points"],
        series=" -> ".join("%s: %g" % (d, n) for d, n in t["series"]),
        ref=t.get("ref") or "(none given)", unit=t.get("unit") or "",
        status=("inside the reference range at every draw"
                if t["in_range_throughout"] else "flagged abnormal at one or more draws"))


def _trend_frag(slug, i):
    """The PART- fragment name for trend <slug>'s part <i> (1=triage, 2=causes, 3=regimen)."""
    return "%strend-%s-%d.md" % (PART_PREFIX, slug, i)


def _trend_cards(args, t):
    """6c per-trend head: a TRIAGE worker that judges the trend and writes a verdict, and a
    deterministic DISPATCH parented on it that either skips or deepens (phase_trend_dispatch).
    Returns the dispatch id — the card that gates the 6c Barrier until the trend is resolved. No
    trend card reads the labs file: the dated series is in the intro. Loading the 51KB labs into
    every part is what drove t_7c78c46c to a 140k-token peak (board median 26k), 6 compactions and
    a 3-timeout block on 2026-08-09."""
    slug = rxkanban.slugify(t["marker"], 48)
    nolabs = dict(_fmt(), common=COMMON.format(inputs=INPUTS, labs_line=NO_LABS_LINE))
    triage = create(args, "Trend: %s — triage" % t["marker"], "rx-research",
                    TREND_TRIAGE_BODY.format(
                        intro=_trend_intro(t), name=t["marker"],
                        question=TREND_PARTS[0].format(direction=t["direction"], name=t["marker"],
                                                       inputs=INPUTS),
                        frag=_trend_frag(slug, 1),
                        verdict="%strend-%s-verdict.md" % (PART_PREFIX, slug), **nolabs),
                    runtime=PART_RUNTIME, priority=48)
    return create(args, "Trend: %s — dispatch" % t["marker"], "rx-intake",
                  TREND_DISPATCH_BODY.format(slug=slug, triage=triage),
                  parents=[triage] if not rxkanban.is_dry(triage) else [],
                  runtime="15m", priority=47)


# A sustained transaminase rise is the liver's canary and the one trend the triage's "ordinary
# variation" band is most likely to dismiss - ALT/SGPT 26 -> 29 -> 35 over five months was judged
# within ordinary variation and NOT researched on 2026-08-14, so the regimen-driver question
# (a statin and a JAK inhibitor can both move these) never ran. Deterministic gate, not a prompt
# nudge: an LLM triage that already said "no" is the wrong second opinion. When the marker is a
# transaminase and the triage judged it ordinary, the dispatch OVERRIDES the verdict and deepens
# anyway (see phase_trend_dispatch). rx.py owns the classification - one _norm_marker, one answer.


def _rx_module():
    """rx.py as a module, through the same dynamic-load idiom read_trends() already uses.

    Cached at module level: the dispatch runs once per trend, but each exec_module would re-parse
    the whole of rx.py, and the trends substage can fan out many dispatches in one phase.
    """
    global _RX_CACHE
    if _RX_CACHE is None:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "rx_pipeline_disp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx.py"))
        _RX_CACHE = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_RX_CACHE)
    return _RX_CACHE


_RX_CACHE = None


def phase_trend_dispatch(args):
    """The `Trend: <m> — dispatch` card: read the triage's verdict and either skip or deepen.

    Deterministic — it re-judges nothing. An explicit `MEANINGFUL: no` writes the skip report and
    stops; anything else, INCLUDING an unreadable or absent verdict, DEEPENS: create the two deeper
    parts (parentless, parallel) and the synthesis (parented on the triage + those parts), and
    splice the synthesis in front of the 6c Barrier. Self-completes this dispatch card either way.
    Idempotent on retry: every create is keyed on its title, so re-running returns the same cards."""
    slug, triage = args.slug, args.triage
    t = next((r for r in read_trends() if rxkanban.slugify(r["marker"], 48) == slug), None)
    if t is None:
        print("No trend matches slug %r — nothing to dispatch." % slug)
        return _complete_self("trend-dispatch: no trend %s" % slug, dry=args.dry_run)

    reports_dir = os.path.expanduser(REPORTS)
    verdict_path = os.path.join(reports_dir, "%strend-%s-verdict.md" % (PART_PREFIX, slug))
    meaningful, reason = True, ""                          # conservative default: deepen
    try:
        vtext = open(verdict_path, encoding="utf-8").read()
        meaningful = not re.search(r"^\s*MEANINGFUL:\s*no\b", vtext, re.I | re.M)
        m = re.search(r"^\s*REASON:\s*(.+)$", vtext, re.I | re.M)
        reason = m.group(1).strip() if m else ""
    except OSError:
        print("  verdict file absent (%s) — deepening, per the conservative default." % verdict_path)

    # The transaminase gate. A triage "no" on a liver enzyme is exactly the dismissal this exists
    # to prevent, so for a transaminase an ordinary verdict does not skip - it deepens, and the
    # NOTE in the intro tells parts 2 and 3 the trend is being researched anyway. Only an
    # explicit, marker-qualified override: a triage "no" on creatinine still writes a skip report.
    override = False
    if not meaningful and _rx_module().is_transaminase(t["marker"]):
        override = True
        meaningful = True
        print("  %s is a transaminase and the triage judged it ordinary — overriding to deepen."
              % t["marker"])

    if not meaningful:
        out = os.path.join(reports_dir, "trend-%s.md" % slug)
        if not args.dry_run:
            os.makedirs(reports_dir, exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("# %s — trend\n\nJudged within ordinary variation at triage; the "
                         "common-causes and regimen-driver analyses were not performed.\n\n"
                         "**Reason (from triage):** %s\n"
                         % (t["marker"], reason or "within ordinary biological/analytical "
                            "variation, and in range at every draw."))
        print("Trend %s judged ordinary — %s skip report %s; no synthesis created."
              % (slug, "would write" if args.dry_run else "wrote", out))
        return _complete_self("trend-dispatch: %s ordinary" % slug, dry=args.dry_run)

    intro = _trend_intro(t)
    if override:
        intro += ("\n\n    NOTE:      the triage judged this trend within ordinary variation, but it is a "
                  "transaminase and the pipeline researches transaminase trends regardless - the "
                  "regimen-driver question (a statin or JAK inhibitor can move these) must run. "
                  "Treat the trend as worth researching; the dismissal is recorded in "
                  "PART-trend-%s-1.md." % slug)
    nolabs = dict(_fmt(), common=COMMON.format(inputs=INPUTS, labs_line=NO_LABS_LINE))
    part_ids = []
    for i in (2, 3):
        body = PART_BODY.format(intro=intro, n=i, total=3, name=t["marker"],
                                questions=TREND_PARTS[i - 1].format(direction=t["direction"],
                                                                    name=t["marker"], inputs=INPUTS),
                                frag=_trend_frag(slug, i), **nolabs)
        part_ids.append(create(args, "Trend: %s — part %d/3" % (t["marker"], i), "rx-research",
                               body, runtime=PART_RUNTIME, priority=48))
    frag_list = "\n".join("    %s/%s" % (REPORTS, _trend_frag(slug, i)) for i in (1, 2, 3))
    synth_parents = [p for p in ([triage] + part_ids) if p and not rxkanban.is_dry(p)]
    synth = create(args, "Trend: %s — report" % t["marker"], "rx-research",
                   SYNTH_BODY.format(name=t["marker"], total=3, frag_list=frag_list,
                                     questions=TREND_SYNTH.format(name=t["marker"], inputs=INPUTS),
                                     out="trend-%s.md" % slug, **_fmt()),
                   parents=synth_parents, runtime=SYNTH_RUNTIME, priority=47)
    rxkanban.splice([synth] if not rxkanban.is_dry(synth) else [], "Stage 6c: Trends Researched")
    print("Trend %s meaningful — created parts 2/3 and 3/3 and the synthesis "
          "(gated on triage + parts)." % slug)
    return _complete_self("trend-dispatch: %s deepened" % slug, dry=args.dry_run)


def phase_screens(args):
    """Stage 6d — the two whole-regimen screens. 6d's Begin was gated on 6a's Barrier, so every
    substance report exists on disk; the screens read them, so they need only hang off this Begin.
    The schedule review is created only when the regimen records dose times."""
    fmt = _fmt()
    me = _my_parents()
    inter = create(args, "Interaction and timing screen: full regimen", "rx-research",
                   INTERACTIONS.format(**fmt), parents=me, runtime="60m", priority=40)
    screens = [inter]
    if any(s["when"] for s in read_substances()):
        # Runs after interactions so it can consume them; only worth creating when a row records
        # a time.
        screens.append(create(args, "Schedule review: current vs evidence-based timing",
                              "rx-research", SCHEDULE.format(**fmt),
                              parents=[inter] if not rxkanban.is_dry(inter) else [],
                              runtime="60m", priority=35))
    else:
        print("  (no times recorded in the regimen — skipping the schedule review card)")
    rxkanban.splice([i for i in screens if not rxkanban.is_dry(i)], "Stage 6d: Screens Complete")
    print("\nStage 6d: %d screen(s) linked in front of `Stage 6d: Screens Complete`.%s"
          % (len(screens), "  (DRY RUN)" if args.dry_run else ""))
    _complete_self("Stage 6d: %d screen(s)" % len(screens), dry=args.dry_run)


def phase_adversarial(args):
    """Stage 7: Adversarial Review — chunk the Stage 6 reports and fan out the four lenses and the
    citation audit. The reports exist on disk (Stage 6 completed), so the chunking runs directly
    here rather than via a worker card. lenses.cmd_fanout and verify.cmd_fanout splice their merges
    in front of `Stage 7: Adversarial Complete`."""
    import lenses
    import verify

    class _A:
        def __init__(self, dry_run):
            self.dry_run = dry_run
            self.round = 1
    a = _A(args.dry_run)
    print("\nStage 7: Adversarial Review — lenses%s\n" % ("  (DRY RUN)" if args.dry_run else ""))
    lenses.cmd_fanout(a)
    print("\nStage 7: Adversarial Review — citation audit%s\n"
          % ("  (DRY RUN)" if args.dry_run else ""))
    verify.cmd_build(a)
    verify.cmd_fanout(a)
    print("\nDone. Watch with:  hermes kanban --board %s list" % BOARD)
    _complete_self("Stage 7 lenses + citation audit fanned out", dry=args.dry_run)


def phase_conclude(args):
    """Stage 8: Conclusion — the fixed Reconcile -> Assemble -> Adversarial-review-of-the-brief
    chain. The Stage 7 reports exist on disk (Stage 7 completed); Reconcile hangs off this Begin,
    the others chain behind it, and the final devil card is spliced ahead of `Stage 8: Conclusion
    Complete`."""
    fmt = _fmt()
    if not args.dry_run:
        _snapshot_inputs()                                     # self-contained record in the run dir
    me = _my_parents()
    rec = create(args, "Reconcile adversarial verdicts", "rx-verify",
                 RECONCILE.format(**fmt), parents=me, runtime="60m", priority=20)
    syn = create(args, "Assemble prescriber discussion brief", "rx-verify",
                 SYNTH.format(**fmt),
                 parents=[rec] if not rxkanban.is_dry(rec) else [], runtime="45m", priority=10)
    devil = create(args, "Adversarial review of the brief", "rx-devil",
                   DEVIL.format(**fmt),
                   parents=[syn] if not rxkanban.is_dry(syn) else [], runtime="45m", priority=5)
    # Subscribe the FINAL card so the run's conclusion reaches the user — the one place a
    # subscription is right on this board (work cards stay unsubscribed to avoid noise). The devil
    # never blocks now: it completes with {fatal, serious, minor} counts and writes each defect
    # into the brief (<date>-rx-review.md) in place, so this completion notice tells the user the brief is ready and how
    # many issues it carries. Previously its block on a flawed brief was silent (2026-08-13).
    if not rxkanban.is_dry(devil):
        rxkanban.subscribe(devil)
    rxkanban.splice([devil] if not rxkanban.is_dry(devil) else [],
                    "Stage 8: Conclusion Complete")
    print("\nStage 8: Conclusion — reconcile -> assemble -> devil created.%s"
          % ("  (DRY RUN)" if args.dry_run else ""))
    _complete_self("Stage 8: reconcile -> assemble -> devil created", dry=args.dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("research", "adversarial", "conclude", "trend-dispatch"),
                    default="research", help="which analyze stage to build")
    ap.add_argument("--family", choices=("substances", "markers", "trends", "screens"),
                    help="within --phase research: build one substage's (6a-6d) workers; omit to "
                         "create the four substage shells")
    ap.add_argument("--slug", help="trend-dispatch: the trend marker slug")
    ap.add_argument("--triage", help="trend-dispatch: the triage card id to parent the synthesis on")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, create nothing")
    args = ap.parse_args()

    if args.phase == "research":
        if args.family:
            return phase_research_family(args, args.family)
        return phase_research_shells(args)
    if args.phase == "trend-dispatch":
        return phase_trend_dispatch(args)
    if args.phase == "adversarial":
        return phase_adversarial(args)
    if args.phase == "conclude":
        return phase_conclude(args)


if __name__ == "__main__":
    main()
