---
name: investment-hypothesis-investigation
description: "Investigate a high-level investment hypothesis through structured, adversarial research. Use when the user states a thesis or claim they want stress-tested - e.g., the strait of Hormuz staying closed longer than expected, transformer makers undervalued vs hyperscalers, AI capex plateauing in 2027, antitrust breaking up Big Tech. Also activate for: test this thesis, research this hypothesis, is this view priced in, what's the edge here, build a case for or against, or any user statement of a directional investing view they want validated against current evidence and consensus. Produces a single living markdown report with linked endnote citations, saved to ~/.hermes/reports/research/."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [investment, investing, research, hypothesis, thesis-testing, macro, equity]
    category: research
    related_skills: [stock-investment-analysis, arxiv]
---

# Investment Hypothesis Investigation

Stress-test a high-level investment hypothesis through adversarial multi-angle research. Output is a single living markdown report saved to `~/.hermes/reports/research/` that decomposes the claim, establishes the consensus baseline, gathers evidence for and against with linked citations, estimates the probability vs market-implied, and constructs concrete trades that express the view.

## When to Use (and when NOT to)

Activate for thematic, macro, or multi-company investment theses — e.g., "is the AI infrastructure theme overvalued," "should I bet on nuclear energy," "are semiconductor equipment stocks a good entry point."

**Do NOT activate for single-stock analysis.** If the user names a specific ticker or asks to analyze/evaluate/research one company (e.g., "analyze Schneider Electric," "what do you think of SU," "is Eaton undervalued"), load `stock-investment-analysis` instead. This skill is for theses that span multiple companies, sectors, or macro factors — not individual equity research.

**When a thesis investigation identifies specific companies as candidates:** After completing the hypothesis-level analysis, offer to run `stock-investment-analysis` on any named tickers for deeper due diligence. Do not attempt single-stock valuation within this skill's framework.

- *Macro/event:* "The strait of Hormuz will be kept closed for months instead of weeks."
- *Relative valuation:* "Electrical-component makers (transformers, UPS, switchgear) are undervalued vs hyperscaler/AI-compute names."
- *Secular trend:* "AI training capex will plateau by 2027 as scaling laws hit diminishing returns."
- *Regulatory/structural:* "FTC will block at least one Big Tech acquisition in the next 12 months."
- *Cross-asset:* "The dollar weakens 10%+ against EM currencies over the next 18 months."

Also do **not** activate for: pure educational topic explainers, or open-ended sector overviews without a directional claim.

## Quick Reference

You are a research analyst stress-testing investment hypotheses through rigorous, evidence-based, adversarial analysis. You quantify market expectations before forming views, weigh confirming and disconfirming evidence equally, and produce calibrated probability estimates with stated edge against market-implied.

User input format:
- **Hypothesis:** [the user's claim, as stated]
- **Time horizon:** [e.g., 6 months, 12 months, 2 years]
- **Optional context:** [e.g., "I'm long energy already", "what positions could express this"]

Output: a structured report (Sections 1–11 of the template below) saved to `~/.hermes/reports/research/`.

**Hypothesis types and their research playbooks:**

| Type | Investigate | Where to find consensus |
|---|---|---|
| Macro/event | Actors, capabilities, incentives, historical analogs, base rates | Prediction markets, futures curves, options skew, analyst notes |
| Relative valuation | Segment multiples, historical spreads, sub-industry composition, name-level screens | Sector ETF P/E, peer EV/EBITDA medians, sell-side targets |
| Secular trend | Demand drivers, capacity build-out, technology constraints, S-curve fit | Long-dated futures, analyst LT estimates, IEA/EIA forecasts |
| Regulatory/structural | Legal precedent, political alignment, agency posture, timeline analogs | Polymarket, expert commentary, lobbying disclosures |
| Cross-asset | Carry, real-rate differentials, positioning, flow data | DXY/forward curves, CFTC COT, BIS positioning |

**Output location:** `~/.hermes/reports/research/YYYY-MM-DD_<slug>.md`

**Footnote format (GitHub-flavored markdown):**

- In body: `[^N]` after the claim — e.g. `Henry Hub futures imply $4.20/MMBtu through 2027 [^3].`
- Definition at the end of the report: `[^N]: <publisher/filing>, <date>, <URL>`
- Do **not** add a manual `## Sources` heading — GitHub auto-renders a "Footnotes" section from the definitions, with bidirectional click-jumps.
- Reuse a number when citing the same source again; do not duplicate definitions.

## Operating Principles

1. **Never fabricate data.** Every number, date, or claim must come from a tool call. If you cannot verify a figure, write `DATA UNAVAILABLE` and explain what you tried — do not estimate silently.
2. **Always cite via clickable footnotes** (`[^N]` syntax). Prefer primary sources: filings, transcripts, government data (EIA, BLS, IMF, central banks), peer-reviewed research, regulatory filings, court documents. Demote: forum posts, X/Twitter without primary linkage, paywalled headlines.
3. **Date-stamp everything.** Prices, multiples, prediction-market odds, futures-curve levels, options-implied moves are all time-sensitive. State the as-of date for every figure.
4. **Quantify the consensus baseline before evaluating evidence.** "Longer than expected" and "undervalued" are meaningless without a number for what's currently expected/priced. Phase 2 is non-skippable.
5. **Force adversarial search.** At least 30% of queries seek disconfirming evidence. If evidence-for is twice as long as evidence-against, search again.
6. **Numeric probabilities, not qualitative ones.** "55–65% with medium confidence" beats "likely." State both your estimate and the market-implied probability.
7. **Null edge is a valid finding.** If the market is already pricing the hypothesis correctly, say so plainly. Do not manufacture edge.

## Procedure

### Phase 1: Frame the hypothesis

Before any web search, restate the user's hypothesis in precise, falsifiable terms. Produce in this order:

1. **Restated hypothesis** — one sentence, no hedges, with explicit time horizon.
2. **Hypothesis type** — pick from the table above. State which playbook applies.
3. **Implicit baseline** — "longer than expected" → expected by whom, currently? "Undervalued" → vs what multiple, what peer, what historical median? Make the comparison explicit.
4. **Falsification criteria** — three to five specific events or data points that would prove the hypothesis wrong.
5. **Sub-claim decomposition** — break the hypothesis into 3–7 independently testable sub-claims. Each sub-claim should be small enough that a focused search can produce evidence for or against it.

If any of the above is ambiguous from the user's input, ask **one** clarifying question before proceeding. Do not ask more than one.

### Phase 2: Establish the consensus baseline

This is the step LLMs most often skip. You cannot evaluate "longer than expected" without knowing the current expectation, and you cannot evaluate "undervalued" without knowing the current valuation.

For the hypothesis as stated, identify and quantify:

- **What the market currently expects** — analyst consensus, prediction-market odds, options-implied probabilities, futures-curve shape, forward multiples, whatever is measurable for this hypothesis type.
- **What's already priced in** — has the move started? How much of the thesis is consensus already?
- **Implied probability or implied edge** — if measurable (e.g., Polymarket has a contract, options imply an X% move), state it numerically.

Cite each consensus data point with a footnote.

### Phase 3: Adversarial multi-angle research

Run at least 5–10 searches covering the playbook for this hypothesis type. **Critically: at least 30% of searches must be adversarial** — actively seeking the strongest counter-evidence and disconfirming data. If your supporting evidence is three pages of bullet points and your counter-evidence is two sentences, search again with disconfirming queries.

For each angle, capture:
- The specific data point or claim
- Whether it supports or undermines the hypothesis
- A footnote citation to the primary source
- A confidence note if the source is weak (forum, opinion, secondary commentary)

Prefer primary sources: filings, transcripts, government data (EIA, BLS, IMF, central banks), peer-reviewed research, regulatory filings, court documents. Demote: anonymous forum posts, X/Twitter threads without primary linkage, paywalled articles you can only see the headline of.

### Phase 4: Probability assessment

Synthesize evidence into a calibrated estimate. Produce:

- **Strongest evidence for** — top 3 points, ranked by weight.
- **Strongest evidence against** — top 3 points, ranked by weight.
- **Historical base rate** — how often have analogous hypotheses played out? Cite at least one analog with outcome.
- **Our probability estimate** — a number or tight range (e.g., 35–45%), not a vague qualitative judgment.
- **Market-implied probability** — from Phase 2.
- **Edge** — difference between our estimate and market-implied. State whether positive, negative, or null. A null-edge result is a valid and important finding — say so plainly.
- **Confidence** — Low / Medium / High, with a one-sentence reason. State explicitly what new information would move you to higher confidence.

### Phase 5: Trade construction

Only valuable if Phase 4 found positive edge. Otherwise, this section says "no trade — hypothesis appears fairly priced" and explains why.

If there is edge, structure trades into four categories:

1. **Direct expressions** — the most obvious way to express the view (long the asset, short the asset, etc.). Specific tickers, instruments, or contracts.
2. **Hedged expressions** — pair trades, spreads, basis trades that isolate the hypothesis from broader factors.
3. **Asymmetric/optionality** — options structures that give convex payoff if the hypothesis hits hard. State strike, tenor, breakeven where applicable.
4. **What looks attractive but isn't** — names or trades that *seem* to express the view but actually have offsetting exposures. This section catches the most expensive mistakes.

For each named instrument: provide ticker, current price (with as-of date), market cap or notional size, why it expresses the view, and the specific risk that breaks the trade even if the hypothesis is correct.

### Phase 6: Indicators and exits

Concrete, time-bound, observable:

- **Confirming indicators** — data points that, if observed, would strengthen the thesis. Each with a threshold (e.g., "Henry Hub sustained above $6/MMBtu for 2+ quarters").
- **Disconfirming indicators** — data points that would weaken it.
- **Exit triggers** — specific conditions that close the position. Vague sentiment shifts are not exit triggers; numeric thresholds with timeframes are.
- **Risk matrix** — table of `Risk | Probability (H/M/L) | Impact (H/M/L) | Mitigation`.

### Phase 7: Save the report

Write the full report to `~/.hermes/reports/research/`. The directory may not exist — create it.

```bash
mkdir -p ~/.hermes/reports/research
```

Filename convention: `YYYY-MM-DD_<slug>.md` where `<slug>` is the hypothesis lowercased, alphanumeric + hyphens only, capped at 60 characters. Example: `2026-05-03_hormuz-strait-closed-months-not-weeks.md`.

Use the template below verbatim for structure. After writing, confirm the file path back to the user and offer to extend any section.

### Phase 8: Iterative augmentation (when the user asks to extend)

When the user requests additions or refinements:

1. Read the existing file with `read_file`.
2. Run targeted searches for the new angle.
3. Use `patch` (mode=replace) to integrate new content into the existing document — never create a parallel file unless the user explicitly asks.
4. Update cross-references: a new evidence item means revisiting Phases 4, 5, and 6. Don't leave the probability estimate inconsistent with newly added evidence.
5. Append new footnotes with the next available number; never renumber existing ones (the user may have linked to them).
6. Confirm what changed and which downstream sections were updated.

## Report Template

```markdown
# Hypothesis Investigation: [Restated hypothesis]

**Date:** YYYY-MM-DD | **Status:** Draft v1 | **Time horizon:** [window] | **Type:** [Macro/Event | Relative Valuation | Secular Trend | Regulatory | Cross-asset]

## TL;DR

[One paragraph: the hypothesis, our probability estimate, market-implied probability, edge, recommended action. Bold the punchline.]

---

## 1. Hypothesis Framing

### 1.1 Restated Hypothesis
### 1.2 Implicit Baseline
### 1.3 Falsification Criteria
### 1.4 Sub-Claim Decomposition

| # | Sub-claim | Testable via |
|---|---|---|
| 1 | ... | ... |

---

## 2. The Consensus Baseline

### 2.1 What the Market Currently Expects
### 2.2 What's Already Priced In
### 2.3 Implied Probability or Implied Move

---

## 3. Evidence For

[Numbered points with footnote citations. Each: claim, source quality note, weight.]

---

## 4. Evidence Against

[Equally rigorous. If thinner than Section 3, do another adversarial search pass.]

---

## 5. Historical Analogs and Base Rates

| Analog | Year | Setup similarity | Outcome | Source |
|---|---|---|---|---|

---

## 6. Probability Assessment

### 6.1 Top Evidence For (ranked)
### 6.2 Top Evidence Against (ranked)
### 6.3 Our Estimate: **X%** (range Y–Z%)
### 6.4 Market-Implied: **A%**
### 6.5 Edge: **+/− N percentage points**
### 6.6 Confidence: **Low / Medium / High**

---

## 7. Trade Construction

### 7.1 Direct Expressions
### 7.2 Hedged / Pair Expressions
### 7.3 Asymmetric / Optionality
### 7.4 What Looks Attractive but Isn't

---

## 8. Indicators to Monitor

**Confirming:** [list with thresholds]
**Disconfirming:** [list with thresholds]
**Exit triggers:** [specific, time-bound]

---

## 9. Risk Matrix

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|

---

## 10. Open Questions

[Numbered. Each one specifies how to resolve it: filing section, data point, expert call.]

---

## 11. Recommendation

### Thesis in One Sentence
### Position Sizing Guidance
### Time Horizon for Re-evaluation

---

*Not investment advice. Verify all figures independently before acting.*

[^1]: Publisher, Date, https://url
[^2]: Publisher, Date, https://url
```

## Pitfalls

1. **Skipping Phase 2.** Without a quantified consensus, "the evidence supports the hypothesis" is meaningless — the question is whether evidence supports it *more than the market already believes*. Always quantify the baseline before gathering supporting evidence.
2. **Confirmation cascade.** If your evidence-for section is twice as long as evidence-against, you have not done the work. Force-search for the strongest counter-argument from a credible source.
3. **Vague probability.** "Likely" is not an estimate. "55–65% with medium confidence" is.
4. **No null-edge finding.** If the market is already pricing the hypothesis correctly, that is the right answer. Say so. Do not manufacture edge.
5. **Trade list without specifics.** "Long electrical-component names" is useless. "GEV at $X (as-of date), 12-month target $Y based on 18× forward EPS, breaks if data-center capex guidance cuts >15% in next two earnings cycles" is a trade.
6. **Footnote drift.** Every `[^N]` reference in the body must have a matching `[^N]: ...` definition at the end of the report, and every definition must be referenced at least once. No gaps in numbering. Verify before saving.
7. **Saving to the wrong directory.** Reports go to `~/.hermes/reports/research/`, not `cwd`, not `/tmp`. Create the directory if missing.
8. **Renumbering on edit.** When extending the report, append new footnotes with the next available number. Never renumber existing ones — the user may have linked to them.
9. **Stale prices.** Any quoted price, multiple, or yield must have an as-of date. Pull live, do not rely on training data.
10. **Skipping stock-investment-analysis when a specific ticker is named.** If the user says "analyze [company]" or names a ticker, that's a single-stock request — delegate to `stock-investment-analysis`. Do not try to do valuation, bull/bear cases, or financial deep dives within this skill.
11. **Overclaiming certainty on market timing.** Even with strong evidence, assign probabilities honestly. A 70% conviction thesis can still lose money if the catalyst is priced in.

## Verification

Before reporting completion to the user, confirm:

1. The report file exists at `~/.hermes/reports/research/YYYY-MM-DD_<slug>.md` (verify with `ls -la ~/.hermes/reports/research/ | tail -5`).
2. Phase 1 produced a precise restated hypothesis with explicit time horizon and falsification criteria.
3. Phase 2 quantifies the market-implied baseline with at least one cited number.
4. Sections 3 and 4 have comparable rigor — count the citations; if Section 4 has fewer than 60% of Section 3's citations, do another adversarial pass.
5. Section 6 has a numeric probability estimate and a numeric market-implied probability, with a stated edge.
6. Every `[^N]` reference in the body has a matching `[^N]: ...` definition at the end of the file, and every definition is referenced at least once. Numbering is consecutive with no gaps.
7. The disclaimer line appears immediately before the footnote definitions block.
8. Tell the user the exact path to the report and offer to extend it.
