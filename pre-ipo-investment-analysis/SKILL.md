---
name: pre-ipo-investment-analysis
description: >
  Due diligence on ONE private-company round you were offered. The source
  is one of four: an SPV fund summary, a private placement memorandum, a
  Forge / EquityZen / AngelList secondary listing, or a direct-deal pitch
  deck. Verifies the document's claims
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

Activate this skill when the user evaluates one specific private-company opportunity. The opportunity is at a specific round. Sources are SPV fund summaries, private placement memorandums, Forge / EquityZen / AngelList secondary listings, and direct-deal pitch decks. The defining signals are three. A named private company. A specific round. The round is a Series A/B/C/etc., SAFE, convertible note, or secondary. An actionable decision, invest / pass / watchlist, with a finite allocation window.

## When NOT to use

Do **not** activate for: publicly traded equities (use `stock-investment-analysis`). Do **not** activate for thematic or macro theses spanning multiple companies (use `investment-hypothesis-investigation`). Do **not** activate for individual municipal bonds identified by CUSIP (use `municipal-bond-analysis`). Do **not** activate for general venture-trend questions or open-ended sector overviews.

If the company IPO'd since the fund summary was written, stop and tell the user. If it went public, recommend running `stock-investment-analysis` against the public ticker instead. The public-equity framework supersedes the private-round one once a market price exists.

## Quick Reference

You are a senior private-markets analyst evaluating a single pre-IPO investment opportunity. You parse the offered source document. You verify every material claim and footnote against independent sources. You model the exit math net of SPV fees and dilution. You weigh the bull and bear cases with equal rigor. You end with a clearly reasoned verdict.

User input format:
- **Source:** [path or URL to the fund summary / PPM / deck]
- **Optional context:** [e.g., "I'm overweight CleanTech already," "target 5x in 5 years," "considering the $5K minimum"]

Output: a structured markdown report (sections 1–11 below) saved to `~/.hermes/reports/private-company/{COMPANY-SLUG}.md`, with GitHub-flavored footnote citations.

## Operating Principles

1. **Treat the fund summary as one source, not ground truth.** Marketing language ("the single largest constraint," "industry-leading," "demonstrated traction") is signal of intent, not fact. Every numeric claim that drives the thesis must be independently verified or explicitly flagged `DATA UNAVAILABLE`. The claims are revenue, ARR, customer backlog, deployed units, market size, and funding raised. Never silently restate a marketing number as a verified fact.
2. **Verify every footnote.** Walk the document's footnote/reference list end-to-end. For each cited source, fetch it. If the URL is dead, search for it. Confirm the number actually matches the claim. Note the source's independence. The independence is company press release vs. third-party reporting vs. primary filing. A footnote that points to the company's own press is corroboration of the company's claim, not verification of the underlying fact.
3. **Date-stamp everything.** Private valuations are stale fast. State as-of dates for the round, the fund summary, every traction figure, every comparable transaction, and every public-comp multiple. Flag any figure older than 12 months for private deals or older than the latest filed quarter for public comps. Every numeric figure in a report table (deal mechanics, ledger, comps, exit math) carries its as-of date in the same row. A number without a date is unfalsifiable. The report accumulates across addendum runs. An old entry must read as a snapshot, not as current.
4. **Compute the exit math explicitly.** A verdict without a return model is an opinion. Show the required exit valuation. Target the user's IRR, or use a 3x/5x/10x reference grid. State the hold period. The math is net of SPV placement fee, offering costs, annual management fee, carry, and an explicit dilution assumption for future rounds. Compare against named comparable exits in the same sub-industry.
5. **Steel-man both sides.** Build the invest case and the pass case with equal effort. If one side has twice the supporting evidence after a first pass, search again for the other side before forming a view.
6. **Force adversarial search.** At least 3–5 of the 10–15 baseline external searches must actively seek disconfirming evidence. Record what you searched and what the searches returned. Include empty results ("searched X, Y, Z; nothing found"). A clean adversarial search is evidence.
7. **Separate fact, inference, opinion, and computed.** Tag inferences with `(inferred:)`, opinions with `(view:)`, and derived figures with `(computed:)` plus the formula or the named inputs. Reserve plain text for sourced, verified facts. A computed number that reads as a plain fact cannot be re-derived or questioned.
8. **Flag your uncertainty.** End with the top three unknowns that would most change the verdict. Name a concrete way to resolve each.

## Procedure

### Phase 1 — Parse the source document

Read the source file. Extract the canonical fund-summary sections by **section name match (case-insensitive)**, not by markdown heading level. PDF-to-markdown conversion routinely corrupts heading hierarchy at page breaks. It splices bio paragraphs into `##` headings. It duplicates paragraphs across column boundaries. Strip per-page footers (running titles, page numbers) and stripped-image placeholder lines introduced by the PDF converter.

Content categories to locate and extract, regardless of the exact headings used in the source document. SPV summaries, PPMs, and decks each use their own naming conventions — match by content type, not by literal section name. Many documents combine or split these differently. Some omit them entirely.

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
| Risk factors | mostly boilerplate. Surface any non-boilerplate specifics |
| References / footnotes | numbered source list (any numbering scheme — arabic, roman, alphabetic) |

If a category is missing entirely from the source, note it explicitly in the report's Phase-1 output. Missing vehicle terms or round terms is a hard blocker for a verdict.

Then plan the external research. List the 10–15 baseline tool calls you intend to make. The calls cover round announcement search, footnote verifications, founder-background lookups, private-comp searches, public-comp pulls, and adversarial searches for disconfirming evidence. Execute them. Add more searches when footnote verification surfaces ambiguity. Verifying claims is non-negotiable. The 10–15 is a floor, not a ceiling.

### Phase 2 — Verify deal mechanics

Independently confirm:

- **Round announcement** — recent press, the company's own press releases, Crunchbase / PitchBook coverage, regulatory filings (Form D on SEC EDGAR for US companies). Does the publicly reported round size, lead investor, and valuation match what the fund summary states?
- **Primary vs. secondary** — a primary issue creates new shares. It dilutes existing holders. The capital goes to the company. A secondary buys from existing holders. It brings no dilution and no capital to the company. The verdict math differs materially. The source document usually states this in the round-terms section. Verify it against external reporting. If you cannot verify it, mark the field `UNVERIFIED`. The dilution assumption in the exit math depends on it. Primary vs. secondary must never be guessed silently.
- **Pre-money / post-money / share price** — post-money = pre-money + round size. Share price = pre-money / fully-diluted share count (including any option-pool expansion specified in the terms). Recompute and flag any inconsistency.
- **Cap-table snapshot** — surface what's publicly known: prior round sizes and lead investors, board composition, any visible option-pool size. PitchBook and Crunchbase usually surface enough for a rough table.
- **Preference stack** — Series B Preferred sits behind any earlier preferred. State the implied liquidation waterfall: in a sale at $X, what does Series B holder get? This matters more at exits below 2× post-money.
- **SPV fee load** — compute the all-in upfront cost (placement + offering costs as % of contribution). Compute the annual drag (management fee + carry on profits). State both nominally and as a haircut to gross multiple-on-money.

### Phase 3 — Verify company claims and footnotes

Walk the footnote list. For each citation:

1. Fetch the cited URL (or search for an archived/canonical copy if dead).
2. Confirm the number/claim in the body matches what the source actually says.
3. Classify the source. `primary`: a filing, regulator, or court doc. `independent`: a third-party publisher with editorial standards. `corroborating`: the company's own press release or an executive interview. `weak`: a forum, an anonymous blog, or a paywalled headline only.
4. If the underlying claim cannot be reverified, mark it `UNVERIFIED — source [N] is [classification]; the claim depends on the company's own assertion.`

In parallel, verify every material **non-footnoted** number in the doc — these are the most likely to be unverified marketing claims. Common examples: pipeline / backlog figures, deployed-unit counts, ARR or revenue run-rate, headcount, runway, patent count.

Produce a verification ledger as part of the report's "Verification" section. The columns are claim → source → verification outcome (verified / corroborated / unverified / contradicted). The cited source's as-of date sits on every row. Write a claim verified against a source older than the freshness thresholds above as `verified (stale, as of <date>)`. Never write bare `verified`. A verified claim from a stale source is not a current fact.

### Phase 4 — Verify the team

For each founder and named executive:

- **Track record** — LinkedIn for tenure, news coverage and acquisition databases (Crunchbase, news search) for prior-company outcomes. If a bio claims an exit ("X acquired by Y for $Z"), verify the acquirer, year, and price. A vague "later acquired" is a red flag worth surfacing.
- **Operating history** — did they previously scale comparable hardware/software/biotech (whatever the venture requires)? A consumer-software founder pivoting to hardware-heavy infrastructure is a different bet than a hardware veteran doing more hardware.
- **Reputation signal** — recent news, controversies, employee reviews when material. Surface specifics, not vibes.
- **Insider commitment** — for direct-deal decks, any disclosed founder secondary in the round (founders sell their own shares) is a meaningful negative signal. Surface it if present.

### Phase 5 — Market and comparable transactions

Verify or restate the market sizing with independent sources (IEA, BLS, government statistics, top-tier industry research firms — not the company's own white papers).

Build a comparables table covering both sides of an eventual exit:

- **Private comps** — recent (last 18 months) private rounds for companies doing comparable work at comparable stage. Round size, lead, pre-money, traction snapshot. Sources: PitchBook, Crunchbase, TechCrunch / The Information.
- **Public comps** — companies from the source document's competitive-landscape section. They often have tickers. For each, report current EV/Sales, EV/EBITDA where positive, revenue growth, and the implied value. The implied value assumes the target trades like the comp at current revenue. When public comps are useful enough to drive a verdict, consider delegating depth to `stock-investment-analysis`. The comp is the single one the user wants to investigate in depth.
- **Recent IPO outcomes** — for adjacent companies that IPO'd in the last 24 months. Report the IPO valuation, the current valuation, and the % move. This is the most concrete signal for what the exit looks like for the user's target.
- **Recent M&A** — strategic acquirers and recent buy prices in the space.

### Phase 6 — Exit math

Compute — and show the work:

- **Inputs** — post-money, vehicle allocation, price per share, and every fee rate (placement, offering costs, management, carry). Each input carries its as-of date and source. The source is the deal-mechanics table or a footnote. Every number that follows must be re-derivable from these inputs.
- **Required exit valuation** for 2x / 3x / 5x / 10x gross multiple-on-money over a 5-year and 7-year hold, holding the round's post-money flat-to-up. Show the formula for each. Required exit valuation = required gross multiple × (investment ÷ current ownership fraction). The ownership fraction is itself tagged `(computed:)` from the inputs above.
- **Dilution assumption** — assume one to two more priced rounds before exit at realistic dilution per round (15–25% typical), and a final option-pool top-up. State assumptions, vary by ±5 points as sensitivity.
- **Net of SPV economics** — apply placement fee, offering costs, management fee × years held, and carry on gains. Show the fee math for each target. The math is gross × (1 − upfront haircut) × (1 − annual drag × years) − carry on gains, or the actual computation used. State the net multiple separately from gross.
- **Comparable-exit reality check** — what % of named adjacent companies actually reached the required exit valuation? If the math requires a 10x multiple in a space where the median IPO settles 2–3x, flag the asymmetry.

### Phase 7 — Bull case, bear case, base case

**Bull case** — the most credible scenario where this returns the target multiple (3x+ for venture risk-adjusted). State the required assumptions. Name the milestones that must get hit and the dates by when. Assign a rough probability.

**Bear case** — most credible scenario where this returns less than capital. Be specific: down-round next, IPO at flat, secondary at discount, write-off via failed pivot. Assign a rough probability.

**Base case** — most likely outcome at probability-weighted expectation. State the expected net-of-fees multiple-on-money.

### Phase 8 — Verdict

End with one of: **Invest / Pass / Watchlist**.

- **Invest** — base-case net return clears the user's threshold for venture-risk capital. The default reference is 3x net-of-fees over 5–7 years, if no user-specified target. Also require no major red flags in verification.
- **Pass** — the base case fails to clear the threshold. OR a material claim is unverified-and-load-bearing. OR a non-trivial red flag (founder, terms, comp landscape) exists.
- **Watchlist** — an interesting story, but premature. A near-term milestone (named, dated) would resolve the load-bearing uncertainty. It would justify a future entry, often at the next round.

Confidence: **Low / Medium / High** with one sentence explaining what would move you higher.

Sizing guidance (qualitative): full intended check / minimum check / pass entirely.

### Phase 9 — Open Questions

The three most important unknowns. For each, state how the user could resolve it. You can resolve it on a follow-up too. The method is a specific filing, a named source, a milestone to watch, or a question to ask the GP.

### Phase 10 — Append footnote definitions and disclaimer

After Phase 9, append footnote definitions in numbered order using GitHub-flavored markdown footnote syntax. Each definition takes the form:

```
[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

The source title is the clickable link text. Wrap the URL in markdown link syntax. So the rendered footnote shows an actual hyperlink rather than a bare URL. GitHub auto-renders the `[^N]` references in the body as superscripts. They click-jump to the matching definition and back. Do not add a manual `## Sources` heading.

**All URLs in the report — body and footnotes — must use markdown link syntax `[descriptive text](url)`.** GitHub auto-links bare URLs. Wrap every URL in markdown link syntax. The rendered output reads better. The descriptive text is where you convey what the link is. Examples:

- Good: `According to [the company's Form D filing](https://www.sec.gov/cgi-bin/browse-edgar?...), the round closed on 2026-04-15 [^3].`
- Bad: `According to https://www.sec.gov/cgi-bin/browse-edgar?... the round closed on 2026-04-15.`

Reuse a footnote number when citing the same source again. Do not duplicate definitions. Verify every `[^N]` in the body has a matching definition and vice versa.

Immediately before the footnote definitions, include the one-line disclaimer: *Not investment advice. Verify all figures independently before acting.*

### Phase 11 — Save the report

Always save under `~/.hermes/reports/private-company/`. Create the directory if it does not exist.

The filename is `{COMPANY-SLUG}.md`. Lowercase the slug. Keep alphanumeric characters and hyphens only. Cap it at 50 characters (e.g., `acme-robotics.md`, `northstar-bio.md`). One canonical file per company, accumulating history across rounds.

**First run for a company** (file does not exist): write the full report. Use a top-level heading `# {COMPANY} — Pre-IPO Investment Tracker`. Then place the body under `## Initial Analysis — {YYYY-MM-DD}, {ROUND}`. For example, `## Initial Analysis — 2026-05-12, Series B`. End with the disclaimer and footnote definitions block.

**Subsequent run for the same company** (file exists, new round or material update): do not overwrite. Read the existing file, then append:

- A horizontal rule (`---`) followed by `## Addendum — {YYYY-MM-DD}, {NEW ROUND}` (e.g., `## Addendum — 2027-03-10, Series C`).
- Lead with **What changed since the last entry**. The changes are new round terms, new milestones hit or missed, market shifts, and comp moves. Note anything that revises the prior view. Do not repeat unchanged context.
- Update only the sections from Phase 5–8 that changed meaningfully. Skip sections that are unchanged.
- If the verdict changes, state explicitly that it changed. State from what to what.

**Citations across runs use a single merged footnote list.** Find the highest existing `[^N]` number at the end of the file. Number new citations starting at `[^N+1]` and continuing consecutively. Reuse existing numbers when citing already-defined sources. After writing the addendum body, append new `[^N]: ...` definitions to the existing footnote block so the list remains a single monotonically-numbered series.

**If the company IPO'd** between runs: do not write an addendum. Instead, tell the user the company is public. Recommend running `stock-investment-analysis` against the public ticker. Leave the existing private-company file as a frozen historical record.

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

[One paragraph: company, round, post-money valuation (as-of date), verdict (bold), confidence, the base-case net multiple-on-money from Section 10, the core thesis in one sentence, the top risk in one sentence.]

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

| Claim | Source | Footnote | As of | Classification | Outcome |
|---|---|---|---|---|---|
| [e.g., "90 GWh signed customer demand"] | [URL] | [^N] | [YYYY-MM-DD] | corroborating / independent / primary / weak | verified / corroborated / unverified / contradicted / verified (stale, as of <date>) |

[Coverage statement: of N footnoted claims, X verified, Y corroborated, Z unverified, S stale. Of M material non-footnoted claims, ...]

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

| Company | Round | Date | Pre-money | Lead | As of | Notes |
|---|---|---|---|---|---|---|

**Public comps:**

| Ticker | EV/Sales | EV/EBITDA | Rev growth | As of | Notes |
|---|---|---|---|---|---|

**Recent IPO outcomes in adjacent space:**

| Company | IPO date | IPO valuation | Current valuation | % move | As of |
|---|---|---|---|---|---|

**Recent M&A in space:**

| Acquirer | Target | Date | Price | Notes |
|---|---|---|---|---|

---

### 7. Exit math

**Inputs (each with as-of date and source):** [post-money, vehicle allocation, price per share, fee rates — all tagged (computed:) where derived]

**Assumptions:** [years to exit, future dilution per round, # additional rounds, terminal option-pool top-up]

| Required gross multiple | Required exit valuation (computed: show formula) | Implied multiple of current revenue (if any) | Plausibility vs. comps |
|---|---|---|---|
| 2× | | | |
| 3× | | | |
| 5× | | | |
| 10× | | | |

**Net multiple after SPV economics:** [for each target above, the gross-to-net conversion with the fee math shown]

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

- A cited URL in the source document is dead → search for an archived or canonical copy. If none exists, mark the claim `UNVERIFIED`. Name the classification of the source.
- A search or fetch fails for a required field → write `DATA UNAVAILABLE` for that field and state what you tried.
- The source document is missing vehicle terms or round terms → that is a hard blocker for a verdict. Say so. Ask the user for the missing terms.
- The report directory `~/.hermes/reports/private-company/` cannot be created or written → report the exact error and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Verification

Before reporting completion to the user, confirm:

1. The report file exists at `~/.hermes/reports/private-company/{COMPANY-SLUG}.md` (verify with `ls -la ~/.hermes/reports/private-company/ | tail -5`).
2. Every numeric claim in Sections 1–8 carries either a `[^N]` footnote reference or a `DATA UNAVAILABLE` / `UNVERIFIED` tag. No silent restating of marketing figures. Every derived figure (ownership fraction, required exit valuation, net multiple) is tagged `(computed:)` with its formula or named inputs.
3. The verification ledger in Section 2 covers every footnote in the source document. It also covers every material non-footnoted number. Each row is classified as primary, independent, corroborating, or weak. Each row has a verification outcome and the cited source's as-of date. If you verify a claim only against a stale source, write `verified (stale, as of <date>)`.
4. The deal-mechanics table in Section 1 has no blank cells (use `DATA UNAVAILABLE` if a field cannot be sourced).
5. Report the SPV fee load both as a percentage and as a drag on the gross multiple (the all-in net haircut row).
6. The exit-math table in Section 7 shows required exit valuations for at least 2×/3×/5× targets. Every number is re-derivable from the stated inputs. The inputs are post-money, vehicle allocation, price per share, and fee rates. The table shows the net-of-fees conversion separately from gross. The fee math is visible.
7. Bull case (Section 8) and bear case (Section 9) each name explicit required assumptions. Each assigns rough probabilities. The two sections have comparable rigor.
8. Section 10 verdict is one of **Invest / Pass / Watchlist** and includes a confidence level (**Low / Medium / High**) and qualitative sizing guidance.
9. Section 11 lists at least three open questions with a concrete way to resolve each.
10. The footnote list at the end of the file is numbered consecutively with no gaps. Every `[^N]: ...` definition uses markdown link form `[<source title>](<URL>), <publisher>, <YYYY-MM-DD>`. Every `[^N]` inline reference has a matching definition. Reference every definition at least once.
11. No bare URLs anywhere in the report body, tables, or footnotes — all wrapped in `[text](url)` form.
12. The disclaimer line appears immediately before the footnote definitions.
13. Report body is under ~3,000 words (footnote definitions excluded).
14. You reported the absolute file path to the user.
15. Every numeric figure in a report table (deal mechanics, ledger, comps, exit math) carries its as-of date in the same row. The TL;DR names the base-case net multiple-on-money from Section 10.
