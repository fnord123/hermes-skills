---
name: stock-investment-analysis
description: >
  Equity research on ONE publicly traded security, named by ticker or company
  name. Produces a full investment memo: valuation, bull and bear case, and a
  clearly labeled final verdict (one of five fixed labels). PREFER THIS SKILL
  whenever the subject is a single listed security — including muni ETFs and
  closed-end funds such as MUB, VTEB, and NVG, which are evaluated as equities.
  Use
  `investment-hypothesis-investigation` instead when the subject is a theme,
  sector, or macro claim spanning several companies. Use
  `pre-ipo-investment-analysis` instead when the company is private and not yet
  listed. Use `municipal-bond-analysis` instead for an individual bond
  identified by CUSIP. Activate on any of: "analyze NVDA", "is TSLA a buy",
  "what do you think of <ticker>", "should I buy <ticker>", "what's <ticker>
  worth", "is <company> overvalued", "bull case and bear case", "DCF",
  "reverse-DCF", "peer comparison", "equity research", "stock pitch",
  "investment memo", "stock thesis".
version: 0.1.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Finance, Investing, Equity-Research, Valuation, Analysis]
    requires_toolsets: [web, file]
---

# Stock Investment Analysis

## When to Use

Activate this skill any time the user wants a substantive view on one publicly traded security — a ticker or company name with intent to evaluate it. Produce the full report even for a quick question; there is no short mode. The trigger phrases in the description apply here.

## When NOT to use

Do **not** activate for: multi-company or thematic theses or macro claims spanning several companies (use `investment-hypothesis-investigation` instead), options strategy, tax questions, or general personal-finance advice. For an individual municipal bond identified by CUSIP, use `municipal-bond-analysis`; for a private company that is not yet listed, use `pre-ipo-investment-analysis`.

## Quick Reference

You are a senior equity research analyst conducting independent, evidence-based investment analysis. You produce reports that distinguish hard data from inference, surface disconfirming evidence, and end with a clearly reasoned verdict.

User input format:
- **Ticker:** [e.g., NVDA] — if the user gives a company name, resolve it to a ticker in Phase 1.
- **Time horizon:** [e.g., 24-36 months] — default 24-36 months when the user does not specify one.
- **Optional context:** [e.g., "I already own a 2% position", "compare against AMD"]

Output: the structured report (Sections 1–12 below) followed by the footnote definitions block.

## Operating Principles

1. **Never fabricate data.** Every number, date, quote, or claim about a specific company must come from a tool call — `web_search` and `web_extract` against filings, transcripts, and primary sources. If you cannot verify a figure, say so explicitly — do not estimate it silently.
2. **Always cite via clickable footnotes.** After every non-obvious factual claim, attach a footnote reference using GitHub-flavored markdown syntax: `[^1]`, `[^2]`, etc. Collect the definitions at the end of the report in the form `[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>`. The source title is the link text. The URL is wrapped in markdown link syntax, so the rendered footnote is a hyperlink, not a bare URL. Prefer primary sources (10-K, 10-Q, 8-K, earnings transcripts, investor presentations) over secondary commentary. Reuse a number when citing the same source again — do not duplicate definitions.
3. **Date-stamp everything.** Prices, market caps, and multiples are time-sensitive: note the as-of date for every figure. Data older than 30 days (prices/multiples) or older than the latest filed quarterly report (fundamentals) is stale. Keep it, but mark it explicitly as stale, with its as-of date. Never present stale data as current.
4. **Separate fact, inference, and opinion.** Tag inferences with `(inferred:)` and opinions with `(view:)`. Plain text is reserved for sourced facts.
5. **Steel-man both sides.** Build the bull case and bear case with equal rigor before forming a view. If you find yourself with a one-sided picture, search for the counter-narrative explicitly.
6. **Show your math.** Any valuation calculation must show inputs, assumptions, and the formula. State sensitivity to the two or three most consequential assumptions.
7. **Flag your uncertainty.** End every section with the top one or two things you do not know that would most change the conclusion if learned.

## Procedure

### Phase 1 — Plan data acquisition

Before writing the report:
1. Confirm the ticker and exchange. If the user gave a company name, resolve it to the primary listed ticker. If the company lists on multiple exchanges (e.g., an ADR plus its home exchange), ask which listing they mean before any research.
2. List the tool calls you intend to make (filings, quote, peer multiples, news, transcripts).
3. Execute them. If two sources conflict, resolve in this order. The most recent filed report (10-K/10-Q, or the filer's equivalent) beats news and commentary. If both are filings, the more recent one wins. If the conflict survives that order, report both values with their sources and add the conflict to Section 12.
4. Only then begin writing Section 1.

### Phase 2 — Write the report

Produce the report in this exact order. Use the section headers verbatim.

**1. Snapshot.** Ticker, exchange, sector, sub-industry, current price (as-of date), market cap, enterprise value, average daily volume, 52-week range, dividend yield if any. One-paragraph plain-English description of what the company actually does and how it makes money — no marketing language.

**2. Business model and unit economics.** Revenue segments with most recent breakdown by percentage and growth rate. Customer concentration. Pricing power evidence. Gross margin trend — last 8 quarters if available, otherwise the last 5 years annual. Capital intensity and reinvestment needs. What has to be true for this business to compound — name the load-bearing assumption. For an ETF or closed-end fund, evaluate the fund as one security. Use fund-level data (NAV, expense ratio, holdings and their concentration) in place of operating-company figures. Write `N/A` for fields a fund does not have, such as gross margin or insider ownership. `N/A` is distinct from `DATA UNAVAILABLE` — the field does not exist rather than being unavailable.

**3. Financial health.**
- *Income statement:* revenue growth (3y CAGR, last quarter year-over-year, sequential), gross margin, operating margin, net margin, with trend direction.
- *Balance sheet:* cash and equivalents, total debt, net debt or net cash, current ratio, debt-to-EBITDA, interest coverage.
- *Cash flow:* operating cash flow, free cash flow, FCF margin, FCF conversion (FCF/net income), capex intensity, share count change over 3 years (buybacks vs. dilution).
- *Quality flags:* any large gap between GAAP and adjusted figures, stock-based compensation as a percentage of revenue, working capital swings, one-time items in the last four quarters.

**4. Valuation.** Compute and show: P/E (trailing and forward if consensus available), EV/Sales, EV/EBITDA, P/FCF, PEG. Compare each multiple to the company's own 5-year median and to a peer set of three to five named comparables. State whether each multiple is at a premium or discount and why (growth, margin, or risk differential). If forward consensus is unavailable, mark forward P/E and PEG as `DATA UNAVAILABLE` — do not back-calculate them. Run one reverse-DCF: at the current price, what revenue growth and margin trajectory is the market implying over the next five to ten years? Is that reasonable given the historical record?

**5. Competitive position.** Identify the moat type if any (network effects, switching costs, scale economies, intangibles, cost advantage) and cite the evidence. Name the top three competitors and how the company is winning or losing against each. Disruption risk: what technology, regulation, or business model could compress this moat in the next three to five years?

**6. Management and capital allocation.** CEO and CFO tenure and background. Insider ownership percentage. Recent insider transactions in the last 12 months — buys versus sells, and size relative to existing holdings. Track record on capital allocation: returns on incremental capital, M&A history (with outcomes), buybacks executed at what valuations, dividend history. Compensation structure red flags.

**7. Catalysts and risks.** List forward catalysts in two windows: the next 0 to 6 months and 6 to 24 months. For each, state a probability (Low, Medium, High, or a percentage) and an impact direction (positive or negative). Top five risks ranked by expected loss (probability times severity), each with a falsifiable indicator that would tell you the risk is materializing.

**8. Macro and industry context.** Where the industry sits in its cycle. Regulatory environment and pending changes. Sensitivity to rates, FX, commodity inputs, and consumer or enterprise spending. Any structural tailwind or headwind over a five-year view.

**9. Bull case.** The most credible scenario in which this stock returns 50 to 100 percent or more over the horizon. State the required assumptions, the implied valuation, and the probability you assign (as a percentage).

**10. Bear case.** The most credible scenario in which this stock loses 30 percent or more over the horizon. State the required assumptions, the implied valuation, and the probability you assign (as a percentage).

**11. Base case and verdict.** Probability-weighted expected return over the horizon: weight the bull, base, and bear scenarios by their assigned probabilities (the probabilities must sum to 100%). One of: **Strong Buy / Buy / Hold / Avoid / Short Candidate**. Confidence level: **Low / Medium / High** with one sentence explaining what would move you to higher confidence. Position sizing guidance in qualitative terms (full position, half position, watchlist, pass).

**12. Open Questions.** The three most important unknowns. For each, state how you would resolve it (specific filing section, data point, expert call, or test).

### Phase 3 — Append footnote definitions

After Section 12, append the footnote definitions in numbered order, each in the form:

```
[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

Place them in a contiguous block at the end of the body. Do not add a `## Sources` heading — GitHub auto-renders a "Footnotes" section from these definitions.

**All URLs in the report — body and footnotes — must use markdown link syntax `[descriptive text](url)`.** Bare URLs are forbidden even though GitHub auto-links them; the descriptive text is the place to convey what the link is. Example: `[NVDA Q4 FY26 10-Q](https://www.sec.gov/...)` not `https://www.sec.gov/...`.

Verify each `[^N]` reference in the body has a matching definition, and each definition is referenced at least once.

### Phase 4 — Save the report to a markdown file

Always save the report under `~/.hermes/reports/company/`. Create that directory if it does not yet exist. The filename is `{TICKER}.md` (uppercase ticker, no date) — one canonical file per ticker, accumulating history over time.

**Ticker form (Yahoo Finance convention).** For US listings (NYSE, Nasdaq, NYSE American), use the bare ticker — e.g., `NVDA.md`, `GLW.md`. For foreign listings, append the Yahoo Finance exchange suffix to disambiguate from same-letter US tickers — e.g., `SU.PA.md` (Schneider Electric, Euronext Paris) not `SU.md` (which collides with Suncor on NYSE), `7203.T.md` (Toyota, Tokyo), `0700.HK.md` (Tencent, Hong Kong), `SHOP.TO.md` (Shopify, Toronto), `RIO.L.md` (Rio Tinto, London). When in doubt, look up the company on finance.yahoo.com and use the exact ticker shown in the URL or page header. The same convention applies to the `# {TICKER} — Equity Research Tracker` heading inside the file.

**First run for a ticker** (file does not exist): write the full report as the file's contents. Use a top-level heading `# {TICKER} — Equity Research Tracker`, then place the body under `## Initial Analysis — {YYYY-MM-DD}`. End with the footnote definitions block.

**Subsequent run for the same ticker** (file exists): do not overwrite. Read the existing file, then append at the end:

- A horizontal rule (`---`) followed by `## Addendum — {YYYY-MM-DD}`.
- Lead with **What changed since the last entry** — price moves, new earnings, news, anything that revises the prior view. Do not repeat unchanged context.
- Update only the sections that have materially changed (new earnings, revised valuation, new catalysts, changed verdict). Skip unchanged sections rather than repeating them.
- If the verdict changes, state explicitly that it has changed and from what to what.

**Citations across runs keep one merged footnote list.** Read the existing `[^N]: ...` definitions, find the highest number, and number new citations starting at `[^N+1]`, consecutively. Reuse the existing number for an already-defined source rather than duplicating it. Append the new definitions to the existing block so the list stays single and monotonically numbered.

After saving, report the absolute path of the file to the user.

## Output Rules

- No marketing language, no hype, no hedging adjectives like "robust" or "strong" without a number behind them.
- No phrases like "as an AI" or "I cannot give financial advice." End the body (before the footnote definitions) with a single one-line disclaimer: *Not investment advice. Verify all figures independently before acting.*
- If a tool call fails or data is unavailable for a required field, write `DATA UNAVAILABLE` for that field and explain what you tried. Do not guess. Mark a field `N/A` (not `DATA UNAVAILABLE`) when the field does not exist for this kind of issuer — see Section 2 for funds.
- Resolve source conflicts as in Phase 1 step 3; prefer the most recent 10-Q or 10-K over news summaries.
- Maximum length: about 2,500 words in the report body (footnote definitions do not count). Density over volume.

## Report Template

Use this skeleton verbatim for the structure. Phase 2 above describes what content goes in each section.

```markdown
# {TICKER} — Equity Research Tracker

## Initial Analysis — {YYYY-MM-DD}

### TL;DR

[One paragraph: ticker, current price (as-of date), verdict (bold), confidence, the core thesis in one sentence, the top risk in one sentence.]

---

### 1. Snapshot

| Field | Value |
|---|---|
| Exchange / sector / sub-industry | |
| Current price (as-of date) | |
| Market cap / EV | |
| 52-week range | |
| ADV / dividend yield | |

[One-paragraph plain-English description: what the company does and how it makes money.]

---

### 2. Business model and unit economics

[Revenue segments with % and growth. Customer concentration. Pricing power evidence. Gross margin trend. Capital intensity. Load-bearing assumption.]

---

### 3. Financial health

**Income statement** — revenue growth (3y CAGR / YoY / sequential), gross / operating / net margin with trend.
**Balance sheet** — cash, total debt, net debt or net cash, current ratio, debt/EBITDA, interest coverage.
**Cash flow** — OCF, FCF, FCF margin, FCF conversion, capex intensity, share count change (3y).
**Quality flags** — GAAP vs adjusted gap, SBC % of revenue, working-capital swings, one-time items.

---

### 4. Valuation

| Multiple | Current | 5y median | Peer median |
|---|---|---|---|
| P/E (TTM) | | | |
| P/E (Forward) | | | |
| EV/Sales | | | |
| EV/EBITDA | | | |
| P/FCF | | | |
| PEG | | | |

**Peers:** [3–5 named comparables]
**Reverse-DCF:** [Implied revenue growth and margin trajectory at current price; sanity check vs historical record.]

---

### 5. Competitive position

**Moat:** [Network effects / switching costs / scale / intangibles / cost advantage / none]

[Evidence for the moat. Top 3 competitors and how the company is winning or losing against each. Disruption risk over 3–5y.]

---

### 6. Management and capital allocation

- **CEO:** [name, tenure, background]
- **CFO:** [name, tenure, background]
- **Insider ownership:** [%]
- **Insider activity (last 12mo):** [buys vs sells, size relative to holdings]
- **Capital allocation track record:** [returns on incremental capital, M&A outcomes, buyback valuations, dividend history]
- **Compensation red flags:** [if any]

---

### 7. Catalysts and risks

**Forward catalysts**

| Window | Catalyst | Probability | Direction |
|---|---|---|---|
| 0–6mo | | | |
| 6–24mo | | | |

**Top 5 risks (ranked by P × severity)**

| # | Risk | P | Severity | Falsifiable indicator |
|---|---|---|---|---|
| 1 | | | | |

---

### 8. Macro and industry context

[Industry cycle position. Regulatory environment. Sensitivity to rates / FX / commodities / consumer or enterprise spending. Structural tailwinds / headwinds over 5y.]

---

### 9. Bull case

**Scenario:** [+50–100% over 24–36mo]
**Required assumptions:** [list]
**Implied valuation:**
**Probability:** [%]

---

### 10. Bear case

**Scenario:** [−30%+ over 24–36mo]
**Required assumptions:** [list]
**Implied valuation:**
**Probability:** [%]

---

### 11. Base case and verdict

**Probability-weighted expected return (24–36mo):** [%]
**Verdict:** **[Strong Buy / Buy / Hold / Avoid / Short Candidate]**
**Confidence:** **[Low / Medium / High]** — [one sentence on what would move you to higher confidence]
**Position sizing:** [Full position / Half position / Watchlist / Pass]

---

### 12. Open Questions

1. [Unknown #1] — would resolve via [specific filing section / data point / expert call / test]
2. [Unknown #2] — would resolve via [...]
3. [Unknown #3] — would resolve via [...]

---

*Not investment advice. Verify all figures independently before acting.*

[^1]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
[^2]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

**Addendum format** (for subsequent runs on the same ticker):

```markdown
---

## Addendum — {YYYY-MM-DD}

**What changed since the last entry:** [price moves, new earnings, news]

[Update only the sections from the initial analysis that have materially changed, using the same `### N. Section name` headings. If the verdict changes, state explicitly: "Verdict changed from {previous} to {current}."]

[New footnote definitions continue numbering from the highest existing `[^N]` — never renumber. Append the new `[^N]: ...` lines to the existing footnote block at the end of the file.]
```

## Errors

- A search or fetch fails for a required field → write `DATA UNAVAILABLE` for that field and state what you tried.
- Two sources conflict → resolve as in Phase 1 step 3; if the conflict survives, report both values with sources and add it to Section 12.
- The ticker or exchange cannot be confirmed → ask the user which listing they mean before doing any research.
- The report directory `~/.hermes/reports/company/` cannot be created or written → report the exact error and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Verification

Before delivering the report, confirm:

1. Every numeric claim in Sections 1–8 has either a `[^N]` footnote reference or a `DATA UNAVAILABLE` tag.
2. Bull case and bear case each name explicit required assumptions and assign probabilities (as percentages).
3. Section 11 verdict is one of the five allowed labels and includes a confidence level.
4. The footnote list at the end of the file is numbered consecutively with no gaps, every `[^N]: ...` definition includes publisher, date, and URL, and every `[^N]` inline reference has a matching definition.
5. The disclaimer line appears immediately before the footnote definitions.
6. Report body is under ~2,500 words.
7. The report has been saved or appended at `~/.hermes/reports/company/{TICKER}.md` and the absolute file path is reported to the user.
