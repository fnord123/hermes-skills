---
name: pre-ipo-investment-analysis
description: >
  Due diligence on ONE private-company round you have been offered — an SPV
  fund summary, private placement memorandum, Forge / EquityZen / AngelList
  secondary listing, or direct-deal pitch deck. Verifies the document's claims
  against independent sources, models exit math net of SPV fees and dilution,
  and returns an Invest / Pass / Watchlist verdict. PREFER THIS SKILL whenever
  there is a named private company, a specific round or secondary, and an
  allocation decision to make. Use `stock-investment-analysis` instead if the
  company is already publicly traded. Use `investment-hypothesis-investigation`
  instead for venture themes spanning several companies. Activate on any of:
  "analyze this fund summary", "evaluate this pre-IPO round", "is this Series
  A/B/C/D worth it", "should I invest in <private company>", "review this SPV
  offering", "size up this private deal", "should I take this allocation",
  "what do you think of this round", "here's a pitch deck".
version: 0.1.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Investing, Pre-IPO, Private-Markets, Venture, SPV]
    requires_toolsets: [web, file]
---

# Pre-IPO Investment Analysis

## When to Use

Activate this skill any time the user is evaluating a specific private-company investment opportunity at a specific round — SPV fund summaries, private placement memorandums, Forge / EquityZen / AngelList secondary listings, and direct-deal pitch decks. The defining signals are: a named private company, a specific round (Series A/B/C/etc., SAFE, convertible note, or secondary), and an actionable decision (invest / pass / watchlist) with a finite allocation window.

## When NOT to use

Do **not** activate for: publicly traded equities (use `stock-investment-analysis`), thematic or macro theses spanning multiple companies (use `investment-hypothesis-investigation`), individual municipal bonds identified by CUSIP (use `municipal-bond-analysis`), general venture-trend questions, or open-ended sector overviews.

If the company has IPO'd since the fund summary was written, stop and tell the user — recommend running `stock-investment-analysis` against the public ticker instead, since the public-equity framework supersedes the private-round one once a market price exists.

## Quick Reference

You are a senior private-markets analyst evaluating a single pre-IPO investment opportunity. You parse the offered source document, verify every material claim and footnote against independent sources, model the exit math net of SPV fees and dilution, weigh the bull and bear cases with equal rigor, and end with a clearly reasoned verdict.

User input format:
- **Source:** [path or URL to the fund summary / PPM / deck]
- **Optional context:** [e.g., "I'm overweight CleanTech already," "target 5x in 5 years," "considering the $5K minimum"]

Output: a structured markdown report (sections 1–11 below) saved to `~/.hermes/reports/private-company/{COMPANY-SLUG}.md`, with GitHub-flavored footnote citations.

## Operating Principles

1. **Treat the fund summary as one source, not ground truth.** Marketing language ("the single largest constraint," "industry-leading," "demonstrated traction") is signal of intent, not fact. Every numeric claim that drives the thesis — revenue, ARR, customer backlog, deployed units, market size, funding raised — must be independently verified or explicitly flagged `DATA UNAVAILABLE`. Never silently restate a marketing number as a verified fact.
2. **Verify every footnote.** Walk the document's footnote/reference list end-to-end. For each cited source, fetch it (or search for it if the URL is dead), confirm the number actually matches the claim, and note the source's independence (company press release vs. third-party reporting vs. primary filing). A footnote that points to the company's own press is corroboration of the company's claim, not verification of the underlying fact.
3. **Date-stamp everything.** Private valuations are stale fast. State as-of dates for the round, the fund summary, every traction figure, every comparable transaction, and every public-comp multiple. Flag any figure older than 12 months for private deals or older than the latest filed quarter for public comps.
4. **Compute the exit math explicitly.** A verdict without a return model is an opinion. Show: required exit valuation for the user's IRR target (or a 3x/5x/10x reference grid) over a stated hold period, net of SPV placement fee, offering costs, annual management fee, carry, and an explicit dilution assumption for future rounds. Compare against named comparable exits in the same sub-industry.
5. **Steel-man both sides.** Build the invest case and the pass case with equal effort. If one side has twice the supporting evidence after a first pass, search again for the other side before forming a view.
6. **Force adversarial search.** At least 3–5 of the 10–15 baseline external searches must actively seek disconfirming evidence: failed comps, litigation, founder controversies, customer churn, missed milestones, prior-round down-rounds, regulatory headwinds.
7. **Separate fact, inference, and opinion.** Tag inferences with `(inferred:)` and opinions with `(view:)`. Plain text is reserved for sourced, verified facts.
8. **Flag your uncertainty.** End with the top three things you do not know that would most change the verdict if learned, each with a concrete way to resolve it.

## Procedure

### Phase 1 — Parse the source document

Read the source file. Extract the canonical fund-summary sections by **section name match (case-insensitive)**, not by markdown heading level — PDF-to-markdown conversion routinely corrupts heading hierarchy at page breaks, splices bio paragraphs into `##` headings, and duplicates paragraphs across column boundaries. Strip per-page footers (running titles, page numbers) and stripped-image placeholder lines introduced by the PDF converter.

Content categories to locate and extract, regardless of the exact headings used in the source document. SPV summaries, PPMs, and decks each use their own naming conventions — match by content type, not by literal section name. Many documents combine or split these differently; some omit them entirely.

| Content category | What to extract |
|---|---|
| Cover / Highlights | company, sector, founders, lead and notable investors, top bullet highlights |
| Investment thesis (the "why now" narrative) | the pitch — treat as marketing, not fact |
| Executive summary / company snapshot | founding year, milestones, IP claims, partnerships, supply chain or operational footprint |
| Product / technology / roadmap | what they sell, how it works, near-term deliverables |
| Use of proceeds | how the round capital will be deployed |
| Business model | revenue model, unit economics, customer commitments |
| Traction | pipeline, customers, deployments, GTM partnerships |
| Market / industry overview | TAM/SAM, growth rates, structural drivers — every number a claim |
| Competitive landscape | named competitors (public tickers when listed) |
| Team / management | founder and key-exec bios |
| Prior financing | previous rounds, dates, sizes, lead investors |
| Vehicle terms (the SPV / fund itself) | minimum investment, placement fee, management fee, carry, offering costs |
| Round terms (the deal at the company) | security type, total round size, vehicle's allocation, pre-money valuation, price per share, primary vs. secondary |
| Press / media | linked articles — research starting points |
| Risk factors | mostly boilerplate; surface any non-boilerplate specifics |
| References / footnotes | numbered source list (any numbering scheme — arabic, roman, alphabetic) |

If a category is missing entirely from the source, note it explicitly in the report's Phase-1 output. Missing vehicle terms or round terms is a hard blocker for a verdict.

Then plan the external research: list the 10–15 baseline tool calls you intend to make (round announcement search, footnote verifications, founder-background lookups, private-comp searches, public-comp pulls, adversarial searches for disconfirming evidence). Execute them. Add more searches when footnote verification surfaces ambiguity — verifying claims is non-negotiable, the 10–15 is a floor, not a ceiling.

### Phase 2 — Verify deal mechanics

Independently confirm:

- **Round announcement** — recent press, the company's own press releases, Crunchbase / PitchBook coverage, regulatory filings (Form D on SEC EDGAR for US companies). Does the publicly reported round size, lead investor, and valuation match what the fund summary states?
- **Primary vs. secondary** — primary issues new shares (dilutes existing holders, capital goes to the company); secondary buys from existing holders (no dilution, no capital to the company). The verdict math differs materially. The source document usually states this in the round-terms section; verify against external reporting.
- **Pre-money / post-money / share price** — post-money = pre-money + round size; share price = pre-money / fully-diluted share count (including any option-pool expansion specified in the terms). Recompute and flag any inconsistency.
- **Cap-table snapshot** — surface what's publicly known: prior round sizes and lead investors, board composition, any visible option-pool size. PitchBook and Crunchbase usually surface enough for a rough table.
- **Preference stack** — Series B Preferred sits behind any earlier preferred. State the implied liquidation waterfall: in a sale at $X, what does Series B holder get? This matters more at exits below 2× post-money.
- **SPV fee load** — compute the all-in upfront cost (placement + offering costs as % of contribution) and the annual drag (management fee + carry on profits). State both nominally and as a haircut to gross multiple-on-money.

### Phase 3 — Verify company claims and footnotes

Walk the footnote list. For each citation:

1. Fetch the cited URL (or search for an archived/canonical copy if dead).
2. Confirm the number/claim in the body matches what the source actually says.
3. Classify the source: `primary` (filing, regulator, court doc), `independent` (third-party publisher with editorial standards), `corroborating` (company's own press release or executive interview), or `weak` (forum, anonymous blog, paywalled headline only).
4. If the underlying claim cannot be reverified, mark it `UNVERIFIED — source [N] is [classification]; the claim depends on the company's own assertion.`

In parallel, verify every material **non-footnoted** number in the doc — these are the most likely to be unverified marketing claims. Common examples: pipeline / backlog figures, deployed-unit counts, ARR or revenue run-rate, headcount, runway, patent count.

Produce a verification ledger as part of the report's "Verification" section: claim → source → verification outcome (verified / corroborated / unverified / contradicted).

### Phase 4 — Verify the team

For each founder and named executive:

- **Track record** — LinkedIn for tenure, news coverage and acquisition databases (Crunchbase, news search) for prior-company outcomes. If a bio claims an exit ("X acquired by Y for $Z"), verify the acquirer, year, and price. A vague "later acquired" is a red flag worth surfacing.
- **Operating history** — did they previously scale comparable hardware/software/biotech (whatever the venture requires)? A consumer-software founder pivoting to hardware-heavy infrastructure is a different bet than a hardware veteran doing more hardware.
- **Reputation signal** — recent news, controversies, employee reviews when material. Surface specifics, not vibes.
- **Insider commitment** — for direct-deal decks, any disclosed founder secondary in the round (founders cashing out) is a meaningful negative signal; surface if present.

### Phase 5 — Market and comparable transactions

Verify or restate the market sizing with independent sources (IEA, BLS, government statistics, top-tier industry research firms — not the company's own white papers).

Build a comparables table covering both sides of an eventual exit:

- **Private comps** — recent (last 18 months) private rounds for companies doing comparable work at comparable stage. Round size, lead, pre-money, traction snapshot. Sources: PitchBook, Crunchbase, TechCrunch / The Information.
- **Public comps** — companies the source document's competitive-landscape section names (often with tickers). For each: current EV/Sales, EV/EBITDA where positive, revenue growth, and the implied "if our target company traded like comp X, what would it be worth at current revenue projections." When public comps are useful enough to drive a verdict, consider delegating depth to `stock-investment-analysis` for any single comp the user wants to dig into.
- **Recent IPO outcomes** — for adjacent companies that have IPO'd in the last 24 months, the IPO valuation, the current valuation, and the % move. This is the most concrete signal for what the exit looks like for the user's target.
- **Recent M&A** — strategic acquirers and recent buy prices in the space.

### Phase 6 — Exit math

Compute:

- **Required exit valuation** for 2x / 3x / 5x / 10x gross multiple-on-money over a 5-year and 7-year hold, holding the round's post-money flat-to-up. Show the formula.
- **Dilution assumption** — assume one to two more priced rounds before exit at realistic dilution per round (15–25% typical), and a final option-pool top-up. State assumptions, vary by ±5 points as sensitivity.
- **Net of SPV economics** — apply placement fee, offering costs, management fee × years held, and carry on gains. State the net multiple separately from gross.
- **Comparable-exit reality check** — what % of named adjacent companies actually reached the required exit valuation? If the math requires a 10x multiple in a space where the median IPO settles 2–3x, flag the asymmetry.

### Phase 7 — Bull case, bear case, base case

**Bull case** — most credible scenario where this returns the target multiple (3x+ for venture risk-adjusted). State the required assumptions, what milestones get hit and by when, and assign a rough probability.

**Bear case** — most credible scenario where this returns less than capital. Be specific: down-round next, IPO at flat, secondary at discount, write-off via failed pivot. Assign a rough probability.

**Base case** — most likely outcome at probability-weighted expectation. State the expected net-of-fees multiple-on-money.

### Phase 8 — Verdict

End with one of: **Invest / Pass / Watchlist**.

- **Invest** — base-case net return clears the user's threshold for venture-risk capital (default reference: 3x net-of-fees over 5–7 years if no user-specified target) AND no major red flags surfaced in verification.
- **Pass** — base case fails to clear the threshold, OR a material claim is unverified-and-load-bearing, OR a non-trivial red flag (founder, terms, comp landscape) exists.
- **Watchlist** — interesting story but premature: a near-term milestone (named, dated) would resolve the load-bearing uncertainty and justify a future entry, often at the next round.

Confidence: **Low / Medium / High** with one sentence explaining what would move you higher.

Sizing guidance (qualitative): full intended check / minimum check / pass entirely.

### Phase 9 — Open Questions

The three most important unknowns. For each, state how the user (or you on a follow-up) could resolve it — a specific filing, a named source, a milestone to watch, a question to ask the GP.

### Phase 10 — Append footnote definitions and disclaimer

After Phase 9, append footnote definitions in numbered order using GitHub-flavored markdown footnote syntax. Each definition takes the form:

```
[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

The source title is the clickable link text; the URL is wrapped in markdown link syntax so the rendered footnote shows an actual hyperlink rather than a bare URL. GitHub auto-renders the `[^N]` references in the body as superscripts that click-jump to the matching definition (and back) — do not add a manual `## Sources` heading.

**All URLs in the report — body and footnotes — must use markdown link syntax `[descriptive text](url)`.** Bare URLs are forbidden even though GitHub auto-links them; the rendered output is less readable and the descriptive text is the place to convey what the link is. Examples:

- Good: `According to [the company's Form D filing](https://www.sec.gov/cgi-bin/browse-edgar?...), the round closed on 2026-04-15 [^3].`
- Bad: `According to https://www.sec.gov/cgi-bin/browse-edgar?... the round closed on 2026-04-15.`

Reuse a footnote number when citing the same source again; do not duplicate definitions. Verify every `[^N]` in the body has a matching definition and vice versa.

Immediately before the footnote definitions, include the one-line disclaimer: *Not investment advice. Verify all figures independently before acting.*

### Phase 11 — Save the report

Always save under `~/.hermes/reports/private-company/`. Create the directory if it does not exist.

The filename is `{COMPANY-SLUG}.md` — slug is lowercased, alphanumeric and hyphens only, capped at 50 characters (e.g., `acme-robotics.md`, `northstar-bio.md`). One canonical file per company, accumulating history across rounds.

**First run for a company** (file does not exist): write the full report. Use a top-level heading `# {COMPANY} — Pre-IPO Investment Tracker`, then place the body under `## Initial Analysis — {YYYY-MM-DD}, {ROUND}` (e.g., `## Initial Analysis — 2026-05-12, Series B`). End with the disclaimer and footnote definitions block.

**Subsequent run for the same company** (file exists, new round or material update): do not overwrite. Read the existing file, then append:

- A horizontal rule (`---`) followed by `## Addendum — {YYYY-MM-DD}, {NEW ROUND}` (e.g., `## Addendum — 2027-03-10, Series C`).
- Lead with **What changed since the last entry** — new round terms, new milestones hit or missed, market shifts, comp moves, anything that revises the prior view. Do not repeat unchanged context.
- Update only the sections from Phase 5–8 that have meaningfully changed. Skip sections that are unchanged.
- If the verdict changes, state explicitly that it has changed and from what to what.

**Citations across runs use a single merged footnote list.** Find the highest existing `[^N]` number at the end of the file. Number new citations starting at `[^N+1]` and continuing consecutively. Reuse existing numbers when citing already-defined sources. After writing the addendum body, append new `[^N]: ...` definitions to the existing footnote block so the list remains a single monotonically-numbered series.

**If the company has IPO'd** between runs: do not write an addendum. Instead, tell the user the company is public and recommend running `stock-investment-analysis` against the public ticker. Leave the existing private-company file as a frozen historical record.

After saving, report the absolute path of the file to the user.

## Output Rules

- No marketing language, no hype, no hedging adjectives like "robust" or "strong" without a number behind them.
- No phrases like "as an AI" or "I cannot give financial advice." End the body (before the footnote definitions) with the one-line disclaimer.
- If a tool call fails or data is unavailable for a required field, write `DATA UNAVAILABLE` and explain what you tried. Do not guess.
- Prefer primary and independent sources over the company's own press releases when they conflict.
- Maximum length: roughly 3,000 words for the report body. The footnote definitions do not count toward the word limit.

## Report Template

Use this skeleton verbatim. The Phase definitions above describe what content goes in each section.

```markdown
# {COMPANY} — Pre-IPO Investment Tracker

## Initial Analysis — {YYYY-MM-DD}, {ROUND}

### TL;DR

[One paragraph: company, round, post-money valuation (as-of date), verdict (bold), confidence, the core thesis in one sentence, the top risk in one sentence.]

---

### 1. Deal mechanics

| Field | Value | Verified |
|---|---|---|
| Security type | | |
| Round size | | |
| Lead investor | | |
| Pre-money / post-money | | |
| Price per share | | |
| Primary or secondary | | |
| Vehicle allocation | | |
| Effective ownership at minimum check | | |

**SPV fee load:**

| Fee | Rate | Drag on gross multiple |
|---|---|---|
| Placement fee | | |
| Offering costs | | |
| Management fee (annual) | | |
| Carry | | |
| **All-in net haircut at 3× gross over 5y** | | |

**Preference stack:** [waterfall summary; impact at low-end exits]

---

### 2. Verification ledger

| Claim | Source | Footnote | Classification | Outcome |
|---|---|---|---|---|
| [e.g., "90 GWh signed customer demand"] | [URL] | [^N] | corroborating / independent / primary / weak | verified / corroborated / unverified / contradicted |

[Coverage statement: of N footnoted claims, X verified, Y corroborated, Z unverified. Of M material non-footnoted claims, ...]

---

### 3. Business model and product

[What they sell, how they make money, unit economics where stated, capital intensity, the load-bearing assumption.]

---

### 4. Traction

[Verified pipeline / revenue / deployment numbers. Marketing-only figures explicitly labeled. Customer concentration if known.]

---

### 5. Team

| Role | Name | Background | Prior outcome | Verified |
|---|---|---|---|---|
| CEO | | | | |
| Co-founder | | | | |
| Key exec | | | | |

[Insider-commitment notes, any founder secondary in the round, board composition.]

---

### 6. Market and comparables

**Market size (verified):** [number, source, as-of date]

**Private comps (last 18 months):**

| Company | Round | Date | Pre-money | Lead | Notes |
|---|---|---|---|---|---|

**Public comps:**

| Ticker | EV/Sales | EV/EBITDA | Rev growth | Notes |
|---|---|---|---|---|

**Recent IPO outcomes in adjacent space:**

| Company | IPO date | IPO valuation | Current valuation | % move |
|---|---|---|---|---|

**Recent M&A in space:**

| Acquirer | Target | Date | Price | Notes |
|---|---|---|---|---|

---

### 7. Exit math

**Assumptions:** [years to exit, future dilution per round, # additional rounds, terminal option-pool top-up]

| Required gross multiple | Required exit valuation | Implied multiple of current revenue (if any) | Plausibility vs. comps |
|---|---|---|---|
| 2× | | | |
| 3× | | | |
| 5× | | | |
| 10× | | | |

**Net multiple after SPV economics:** [for each target above, the gross-to-net conversion]

**Sensitivity:** [±5% dilution per round, +2y hold, etc.]

---

### 8. Bull case

[Most credible 3×+ scenario. Required assumptions, named milestones with dates, probability.]

---

### 9. Bear case

[Most credible <1× scenario. Down-round / flat IPO / write-off paths. Required assumptions, probability.]

---

### 10. Base case and verdict

**Probability-weighted base case:** [expected net-of-fees MoM and IRR]

**Verdict: Invest / Pass / Watchlist**

**Confidence:** Low / Medium / High — [one sentence]

**Sizing:** [full check / minimum / pass]

---

### 11. Open Questions

1. [Unknown #1] — [how to resolve]
2. [Unknown #2] — [how to resolve]
3. [Unknown #3] — [how to resolve]

---

*Not investment advice. Verify all figures independently before acting.*

[^1]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
[^2]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

## Errors

- A cited URL in the source document is dead → search for an archived or canonical copy; if none exists, mark the claim `UNVERIFIED` and name the classification of the source.
- A search or fetch fails for a required field → write `DATA UNAVAILABLE` for that field and state what you tried.
- The source document is missing vehicle terms or round terms → that is a hard blocker for a verdict; say so and ask the user for the missing terms.
- The report directory `~/.hermes/reports/private-company/` cannot be created or written → report the exact error and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Verification

Before reporting completion to the user, confirm:

1. The report file exists at `~/.hermes/reports/private-company/{COMPANY-SLUG}.md` (verify with `ls -la ~/.hermes/reports/private-company/ | tail -5`).
2. Every numeric claim in Sections 1–8 has either a `[^N]` footnote reference or a `DATA UNAVAILABLE` / `UNVERIFIED` tag. No silent restating of marketing figures.
3. The verification ledger in Section 2 covers every footnote in the source document and every material non-footnoted number, each classified as primary / independent / corroborating / weak, with a verification outcome.
4. The deal-mechanics table in Section 1 has no blank cells (use `DATA UNAVAILABLE` if a field cannot be sourced).
5. The SPV fee load is reported both as a percentage and as a drag on the gross multiple (the all-in net haircut row).
6. The exit-math table in Section 7 shows required exit valuations for at least 2×/3×/5× targets, with the net-of-fees conversion shown separately from gross.
7. Bull case (Section 8) and bear case (Section 9) each name explicit required assumptions and assign rough probabilities; the two sections have comparable rigor.
8. Section 10 verdict is one of **Invest / Pass / Watchlist** and includes a confidence level (**Low / Medium / High**) and qualitative sizing guidance.
9. Section 11 lists at least three open questions with a concrete way to resolve each.
10. The footnote list at the end of the file is numbered consecutively with no gaps. Every `[^N]: ...` definition uses markdown link form `[<source title>](<URL>), <publisher>, <YYYY-MM-DD>`. Every `[^N]` inline reference has a matching definition; every definition is referenced at least once.
11. No bare URLs anywhere in the report body, tables, or footnotes — all wrapped in `[text](url)` form.
12. The disclaimer line appears immediately before the footnote definitions.
13. Report body is under ~3,000 words (footnote definitions excluded).
14. The absolute file path has been reported to the user.
