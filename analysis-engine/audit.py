#!/usr/bin/env python3
"""The citation-audit lifecycle: locate, size into cards, judge, merge, sweep.

Pairs with pipeline.py. That module knows how to find a quote inside a source; this one turns
that into a card graph a kanban board can execute, and closes the loop when the judging is
done.

The shape, and why each piece exists:

  build   - fetch every cited source, locate each quoted sentence, extract the ENCLOSING
            SECTION and the claim that cites it. Written to locations.json.
  fanout  - pack those into cards that fit BOTH the context window and a 20-minute wall clock,
            write each card's items to a FILE (the body names it), create a merge card gated
            on all of them, create the next sweep, and splice the merge in front of whatever
            barrier is waiting on the audit.
  merge   - concatenate the parts deterministically and tally the verdicts.
  sweep   - re-plan anything that never got judged; stop when a round makes no progress.

Judging is a section, not a character window, because the heading is what catches scope
errors: a sentence under "Adverse Reactions in Atopic Dermatitis" does not support a claim
about rheumatoid arthritis, and no +/-200 character window shows you that.
"""

import glob
import json
import os
import re
import subprocess

from pipeline import (Pipeline, CARD_BUDGET_CHARS, MAX_ITEMS_PER_CARD, CARD_RUNTIME_MINUTES,
                      CHARS_PER_TOKEN, MAX_MEASURE_WORKERS, HERMES)

MAX_SWEEP_ROUNDS = 4

VERDICTS = ("supported", "context-reversed", "scope-mismatch", "overstated",
            "misquoted", "unsupported", "absent")

JUDGE_BODY = """Judge whether each source supports the use the report made of it.

The quote has ALREADY been located in the source mechanically - you do not need to fetch
anything, and you must not. What you are judging is something a text search cannot: whether
the surrounding section actually means what the claim takes it to mean.

Your items are in {items_file}, in this working directory. Read that file first. It holds
{count} item(s) and nothing else. Do NOT open the reports.

Each item gives you:
  CLAIM    - what the report asserts, in its own words
  QUOTE    - the sentence it cites
  MATCH    - exact / fuzzy / absent (absent = the quote was not found in the source text)
  SECTION  - the heading the quote sits under, and that section's text

Verdict per item, choosing the FIRST that applies:

  supported        - the section says this, and the claim uses it for what it says
  context-reversed - the surrounding text negates, refutes or contradicts the quoted sentence;
                     it is a hypothesis, a straw man, a limitation, or a position the source
                     goes on to reject
  scope-mismatch   - the quote is real but the section covers a different subject, population,
                     period, or unit than the claim assumes. Check the HEADING against the
                     claim - that is usually where the mismatch shows
  overstated       - the section supports something weaker: an association reported as
                     causation, an estimate reported as a measurement, one case presented as a
                     pattern, a range reported as a point
  misquoted        - the section addresses the point but the quoted wording is not what it says
  unsupported      - the section does not address this claim at all
  absent           - the quote is not in the source. Say whether the claim is nonetheless
                     supported by the section you were given: a paraphrase is legitimate, an
                     invention is not

Be specific. A verdict of `supported` on something the section does not really carry is the
failure this pass exists to prevent, so where you are unsure, say so rather than passing it.

Append ONE line per item to {out} as you go, before starting the next - do not save them up:

  <verdict> | <report> | <endnote> | <heading> | <one-line reason, quoting the section if it
  contradicts the claim>

If {out} already exists, APPEND and skip any item that already has a line - a previous attempt
may have got part way. Never rewrite it from the top: those lines are finished work.

Write no other file. Create no cards.

When every item has a line, kanban_complete with metadata:
  {{"part": {n}, "judged": N}}
"""

MERGE_BODY = """Concatenate the judged parts into one file. Mechanical - do not re-judge anything and do
not fetch anything.

Run exactly this, using the terminal tool:

    python3 {runner} merge

It reads the part files and writes the consolidated audit. kanban_complete with the totals it
prints. If it exits non-zero, kanban_block with its output.
"""

SWEEP_BODY = """Check whether any citation was never judged, and if so schedule the work. Mechanical -
do not judge anything yourself.

Run exactly this, using the terminal tool:

    python3 {runner} sweep --round {n}

Then act on its FIRST line:

- `SWEEP: CLEAN` - every citation has a verdict. kanban_complete with the metadata printed.
- `SWEEP: SCHEDULED` - new cards were created. kanban_complete, declaring the ids it printed
  in created_cards=[...].
- `SWEEP: BLOCKED` - citations remain unjudged and retrying is not helping. kanban_block with
  the full output, so the gap is visible rather than silently absent from the conclusions.

If the command exits non-zero for any other reason, kanban_block with its output.
"""


class Audit:
    """Citation audit over one Pipeline's reports."""

    def __init__(self, pipe, runner, barrier_title="Evidence audit complete"):
        self.p = pipe
        # How a card should invoke this pipeline. Skill-local by design: a card body naming a
        # shared engine path would leak the engine into model context.
        self.runner = runner
        self.barrier_title = barrier_title

    # ── build ───────────────────────────────────────────────────────────────

    def build(self):
        p = self.p
        notes = p.endnotes()
        urls = sorted({u for _, _, _, u in notes})
        print("  endnotes            : %d" % len(notes))
        print("  unique sources      : %d" % len(urls))

        texts, index = {}, {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=MAX_MEASURE_WORKERS) as ex:
            for u, t in zip(urls, ex.map(p.fetch_text, urls)):
                texts[u] = t
                index[u] = p.section_index(t) if t else []
        print("  sources fetched     : %d of %d"
              % (sum(1 for t in texts.values() if t), len(urls)))

        rows, kinds = [], {"exact": 0, "fuzzy": 0, "absent": 0, "unfetched": 0}
        for report, n, quotes, url in notes:
            text = texts.get(url) or ""
            claim = p.claim_for(report, n)
            if not text:
                kinds["unfetched"] += 1
                rows.append({"report": report, "n": n, "url": url,
                             "quote": max(quotes, key=len) if quotes else "",
                             "claim": claim, "match": "unfetched", "heading": "", "section": ""})
                continue
            kind, pos, quote = self._best(text, quotes)
            kinds[kind] += 1
            if pos is None:
                heading, section = "(quote not located)", text[:3000]
            else:
                heading, section = p.enclosing_section(text, pos, index[url])
            rows.append({"report": report, "n": n, "url": url, "quote": quote, "claim": claim,
                         "match": kind, "heading": heading,
                         "section": " ".join(section.split())})

        json.dump({"rows": rows}, open(p.locations, "w"), indent=1)
        print("  located             : exact=%d fuzzy=%d absent=%d unfetched=%d"
              % (kinds["exact"], kinds["fuzzy"], kinds["absent"], kinds["unfetched"]))
        sec = [len(r["section"]) for r in rows if r["section"]]
        if sec:
            sec.sort()
            print("  section size        : median %d chars (~%d tokens)"
                  % (sec[len(sec) // 2], sec[len(sec) // 2] // CHARS_PER_TOKEN))
        return rows

    def _best(self, text, quotes):
        """Best match across every quoted run in the endnote.

        An endnote usually carries the article TITLE in quotes as well as the claim it is cited
        for; picking the longest string grabs whichever happens to be longer.
        """
        best = ("absent", None, "")
        for q in quotes or []:
            kind, pos = self.p.find_quote(text, q)
            if kind == "exact":
                return kind, pos, q
            if kind == "fuzzy" and best[0] != "fuzzy":
                best = (kind, pos, q)
        if best[0] == "absent" and quotes:
            best = ("absent", None, max(quotes, key=len))
        return best

    # ── judged-so-far ───────────────────────────────────────────────────────

    def part_files(self):
        return sorted(glob.glob(os.path.join(self.p.reports, "AUDIT-part-*.md")))

    def already_judged(self):
        """(report, endnote) pairs that already carry a verdict line.

        Makes a retry cost one in-flight item instead of a whole card, and lets fanout be
        re-planned without redoing finished work.
        """
        done = set()
        for f in self.part_files():
            for line in open(f, encoding="utf-8", errors="ignore"):
                parts = [x.strip() for x in line.split("|")]
                if len(parts) < 3:
                    continue
                m = re.match(r"^\[?\^?(\d+)\]?$", parts[2])
                if m and parts[1].endswith(".md"):
                    done.add((parts[1], int(m.group(1))))
        return done

    def outstanding(self):
        if not os.path.exists(self.p.locations):
            return []
        rows = json.load(open(self.p.locations))["rows"]
        done = self.already_judged()
        return [r for r in rows if (r["report"], r["n"]) not in done]

    # ── fanout ──────────────────────────────────────────────────────────────

    @staticmethod
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

    def fanout(self, rnd=1, dry_run=False):
        p = self.p
        rows = self.outstanding()
        if not rows:
            print("  nothing left to judge.")
            return []

        parts, cur, size = [], [], 0
        for r in rows:
            w = len(r["section"]) + len(r["claim"]) + len(r["quote"]) + 200
            if cur and (size + w > CARD_BUDGET_CHARS or len(cur) >= MAX_ITEMS_PER_CARD):
                parts.append(cur)
                cur, size = [], 0
            cur.append(r)
            size += w
        if cur:
            parts.append(cur)
        print("  %d citation(s) -> %d card(s)" % (len(rows), len(parts)))

        suffix = "" if rnd == 1 else " r%d" % rnd
        ids = []
        for i, part in enumerate(parts, 1):
            items_file = "AUDIT-items%s-%02d.md" % (suffix.replace(" ", "-"), i)
            out_file = "AUDIT-part%s-%02d.md" % (suffix.replace(" ", "-"), i)
            if not dry_run:
                with open(os.path.join(p.reports, items_file), "w", encoding="utf-8") as fh:
                    fh.write("# Items for audit part %d\n\n" % i)
                    fh.write("\n".join(self._render(r) for r in part))
            body = JUDGE_BODY.format(items_file=items_file, out=out_file, n=i, count=len(part))
            tid = p.create("Evidence audit %02d/%02d%s" % (i, len(parts), suffix),
                           "audit", body, runtime_min=CARD_RUNTIME_MINUTES,
                           priority=33, dry_run=dry_run)
            ids.append(tid)
            print("  %s  part %02d  %2d items" % (tid, i, len(part)))

        real = [i for i in ids if i != "DRY"]
        if not dry_run:
            p.phase_start("audit", "**Evidence audit started** %d citation(s) across %d card(s)."
                          % (len(rows), len(parts)))

        merge = p.create("Consolidate the evidence audit%s" % suffix, "clerk",
                         MERGE_BODY.format(runner=self.runner), parents=real,
                         runtime_min=15, priority=32, dry_run=dry_run)
        sweep = None
        if rnd < MAX_SWEEP_ROUNDS:
            sweep = p.create("Sweep: re-judge citations with no verdict (round %d)" % (rnd + 1),
                             "clerk", SWEEP_BODY.format(runner=self.runner, n=rnd + 1),
                             parents=[merge] if merge != "DRY" else [],
                             runtime_min=15, priority=32, dry_run=dry_run)

        # Splice in front of the barrier, never the consumer directly: a parent added to a card
        # that has already started does nothing, and the barrier is created with the graph so it
        # cannot have started while work remains.
        if not dry_run:
            for cid in p.open_cards(self.barrier_title):
                for upstream in [x for x in (merge, sweep) if x and x != "DRY"]:
                    p.link(upstream, cid)
                    print("  linked %s -> %s" % (upstream, cid))
        return real

    # ── merge ───────────────────────────────────────────────────────────────

    def merge(self, out_name="EVIDENCE-AUDIT.md"):
        p = self.p
        parts = self.part_files()
        if not parts:
            print("no audit parts found")
            raise SystemExit(1)
        counts = {v: 0 for v in VERDICTS}
        rows, seen = [], set()
        for f in parts:
            for line in open(f, encoding="utf-8", errors="ignore"):
                t = line.strip()
                if not t or t.startswith("#") or "|" not in t:
                    continue
                v = t.split("|")[0].strip().lower()
                if v not in counts or t in seen:
                    continue
                seen.add(t)
                counts[v] += 1
                rows.append(t)
        total = sum(counts.values())
        unjudged = self.outstanding()
        with open(os.path.join(p.reports, out_name), "w", encoding="utf-8") as fh:
            fh.write("# Evidence audit\n\n")
            fh.write("Each citation was located in its source mechanically; a reviewer then "
                     "judged the ENCLOSING SECTION, not just the sentence. %d judged across "
                     "%d part(s).\n\n" % (total, len(parts)))
            for v in VERDICTS:
                fh.write("- **%s**: %d\n" % (v, counts[v]))
            fh.write("\nAnything not `supported` fails the citation test.\n")
            if unjudged:
                fh.write("\n## Unjudged citations (%d)\n\n" % len(unjudged))
                fh.write("No verdict was obtained for these. Treat any claim resting on them "
                         "as UNSUPPORTED - absence of a check is not evidence that the source "
                         "checks out.\n\n")
                for r in unjudged:
                    fh.write("- %s [%s] %s\n" % (r["report"], r["n"], r["url"]))
            fh.write("\n`verdict | report | endnote | heading | reason`\n\n")
            for r in sorted(rows):
                fh.write("%s\n" % r)
        clean = counts["supported"]
        p.phase_end("audit", "**Evidence audit complete**",
                    ["%d citation(s) judged - %d supported, %d not." % (total, clean, total - clean),
                     ("Problems: " + ", ".join("%s %d" % (k, v) for k, v in counts.items()
                                               if v and k != "supported") + ".")
                     if total - clean else "No citation problems found."])
        print(json.dumps({"parts": len(parts), "judged": total,
                          "unjudged": len(unjudged), **counts}))

    # ── sweep ───────────────────────────────────────────────────────────────

    def sweep(self, rnd=1, dry_run=False):
        p = self.p
        out = self.outstanding()
        st = p._phase_state()
        prev = st.get("audit_outstanding")

        if not out:
            print("SWEEP: CLEAN")
            print(json.dumps({"round": rnd, "outstanding": 0}))
            return 0
        stalled = prev is not None and len(out) >= prev
        if rnd >= MAX_SWEEP_ROUNDS or stalled:
            print("SWEEP: BLOCKED")
            print("%s. These citations have no verdict:"
                  % ("round %d made no progress (%d outstanding, was %s)" % (rnd, len(out), prev)
                     if stalled else "reached the %d-round limit with %d outstanding"
                     % (MAX_SWEEP_ROUNDS, len(out))))
            for r in out[:40]:
                print("  - %s [%s]  %s" % (r["report"], r["n"], r["url"][:80]))
            if len(out) > 40:
                print("  ... and %d more" % (len(out) - 40))
            print(json.dumps({"round": rnd, "outstanding": len(out), "blocked": True}))
            return 0
        print("SWEEP: SCHEDULED")
        print("  %d citation(s) unjudged; planning round %d" % (len(out), rnd + 1))
        if not dry_run:
            st["audit_outstanding"] = len(out)
            p._phase_state(st)
        self.fanout(rnd=rnd + 1, dry_run=dry_run)
        return 0
