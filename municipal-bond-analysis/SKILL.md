---
name: municipal-bond-analysis
description: >
  Conduct rigorous, evidence-based analysis of a specific municipal bond
  (by CUSIP, by issuer + maturity for new issues without CUSIP yet, or
  from an offering statement). Use when the user is evaluating a muni for
  purchase, comparing a muni to taxable alternatives at the same duration,
  checking AMT exposure or de minimis impact, assessing yield-to-worst
  on a callable bond, or doing credit due diligence on a GO or revenue
  bond. Trigger phrases include "analyze [CUSIP]", "should I buy this
  muni", "is this muni a good deal", "evaluate this revenue bond",
  "check this GO bond", "what's this bond's yield to worst", "muni TEY at
  my bracket", "muni vs Treasury", "review this offering statement," and
  "is this bond pre-refunded." Defer to `stock-investment-analysis` for
  muni ETFs (MUB, VTEB, NMBIY) since those are equities for purposes of
  evaluation. Defer to `investment-hypothesis-investigation` for sector
  or market-timing theses (e.g., "are munis cheap right now," "is the
  yield ratio signaling a buying opportunity").
version: 1.0.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Investing, Fixed-Income, Municipal-Bonds, Tax-Aware]
    related_skills: [stock-investment-analysis, investment-hypothesis-investigation, pre-ipo-investment-analysis]
    config:
      - key: federal_marginal_rate
        description: Federal marginal income-tax rate as a decimal (0.37 for 37% top bracket, 0.32, 0.24, etc.).
        default: "0.37"
        prompt: What is your federal marginal tax rate as a decimal (e.g., 0.37 for the 37% top bracket)?
      - key: state_marginal_rate
        description: State marginal income-tax rate as a decimal (0 for no-tax states).
        default: "0.0"
        prompt: What is your state marginal tax rate as a decimal (0 for no state income tax)?
      - key: state_code
        description: Two-letter state code for residence (drives in-state vs out-of-state tax treatment of muni interest).
        default: "US"
        prompt: What two-letter state code is your state of residence (e.g., NY, TX, CA)?
      - key: amt_exposed
        description: Whether the user is potentially subject to AMT (affects valuation of private activity bonds with AMT-subject interest).
        default: "false"
        prompt: Are you potentially subject to AMT (true/false)?
---

# Municipal Bond Analysis

## When to Use

Activate any time the user is evaluating a specific municipal bond — by CUSIP, by issuer + maturity (for new issues without a CUSIP yet), or from an offering statement / preliminary OS. The defining signals are: a single named issuer, a specific maturity (or call schedule), and an actionable decision (buy / watch / pass) against the user's tax-aware portfolio.

Do **not** activate for: muni ETFs or closed-end funds (use `stock-investment-analysis` — MUB, VTEB, NMBIY, etc. are equities for purposes of evaluation), sector or market-timing theses (use `investment-hypothesis-investigation` for questions like "are munis cheap right now" or "is the AAA muni/Treasury ratio signaling"), portfolio-level ladder construction across multiple bonds, tax-loss harvesting workflows, or generic muni-bond explainers.

If the bond is a taxable municipal (e.g., Build America Bond, taxable refunding) or has otherwise lost its tax exemption, tax-equivalent-yield framing is irrelevant — compare it directly against same-maturity corporates and Treasuries.

## Quick Reference

You are a senior fixed-income analyst evaluating a single municipal bond for purchase, refinement of an existing position, or comparison against taxable alternatives. You pull primary disclosures from EMMA, verify credit metrics against the official statement and continuing disclosures, compute yield-to-worst and tax-equivalent yield at the user's bracket, weigh the buy and pass cases with equal rigor, and end with a clearly reasoned verdict.

User input format:
- **Bond:** [CUSIP (9-character alphanumeric, the canonical identifier), or issuer + maturity + coupon for new issues, or path to OS PDF]
- **Optional context:** [e.g., "$50K minimum, in-state," "considering vs 30Y Treasury at 4.8%," "I already hold $250K of this issuer"]

Output: a structured markdown report saved to `~/.hermes/reports/muni/{CUSIP}.md` (or `{ISSUER-SLUG}-{MATURITY-YEAR}.md` for new issues without a CUSIP yet), with GitHub-flavored footnote citations.

**Tax-bracket inputs.** The user's federal marginal rate, state marginal rate, state of residence, and AMT exposure status are needed to compute TEY correctly. These values are declared as skill-level frontmatter so Hermes can inject them into your context automatically. If the values are absent or appear stale relative to the user's prompt, ask the user once at the start of the analysis and use the corrected values for the run.

## Operating Principles

1. **Never fabricate data.** Every yield, rating, coupon, call date, DSCR, tax-base figure, or material event must come from a tool call against EMMA, the official statement, a ratings agency, or independent reporting. If a figure cannot be verified, write `DATA UNAVAILABLE` and explain what was tried.
2. **Always cite via clickable footnotes.** Use GitHub-flavored markdown footnote syntax `[^N]` after every non-obvious factual claim. Definitions take the form `[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>` — the source title is the link text. **All URLs (body and footnotes) must use markdown link syntax `[descriptive text](url)`.** Bare URLs are forbidden even though GitHub auto-links them. Reuse a footnote number when citing the same source again; do not duplicate definitions.
3. **Date-stamp everything.** Yield curves move daily, ratings change, material events post weekly, DSCR reports are annual. State the as-of date for every figure. Flag any price or yield older than the most recent trading day, and any credit metric older than the most recent continuing-disclosure filing.
4. **Use the user's actual tax bracket.** Compute both federal-only TEY and federal+state TEY explicitly using the configured rates. Never assume the top bracket without checking. If the bond is in-state for the user's `state_code`, the federal+state TEY is the relevant comparison; if out-of-state, federal-only is correct. Call out the difference.
5. **Anchor on yield-to-worst.** Compute yield-to-maturity AND yield-to-call at every call date in the schedule, then report yield-to-worst (the minimum across these) as the primary yield figure. A callable muni's "yield" without YTW is misleading. For a non-callable bond, YTW = YTM; state that explicitly.
6. **Steel-man both sides.** Build the buy case and the pass case with equal effort. If the buy case is three paragraphs and the pass case is two sentences, search again for disconfirming evidence.
7. **Force adversarial search.** At least 3 of the 10–15 baseline external searches must actively seek pass-case evidence: declining DSCR trends, missed continuing-disclosure filings, recent downgrades or negative outlook actions, deteriorating tax base or population trend, pension/OPEB-funded-ratio declines, draws on debt-service reserves, or comparable issuers that have defaulted or restructured.
8. **Flag your uncertainty.** End with the top three things you do not know that would most change the verdict, each with a concrete way to resolve.

## Procedure

### Phase 1 — Parse the bond identifier and load tax config

Identify the bond:
- If a 9-character CUSIP is provided, use it directly.
- If issuer + maturity + coupon is provided (typical for new issues that haven't priced yet), proceed without a CUSIP and use the issuer-slug-plus-maturity-year filename fallback.
- If an OS PDF path or URL is provided, parse it; CUSIP usually appears on the cover or in the schedule of maturities.

Use the four tax-bracket values from your injected context: the federal marginal rate, the state marginal rate, the two-letter state code, and the AMT exposure flag. If any value is at a placeholder default or clearly inconsistent with the user's prompt, ask once for a correction before proceeding.

Determine in-state vs out-of-state: compare `state_code` to the issuer's state. In-state munis get the combined federal+state exemption; out-of-state get federal only (plus any reciprocal-state arrangements — rare but exist).

Then plan the external research: list the 10–15 baseline tool calls you intend to make (EMMA security page pull, OS download, ratings lookup, recent trade history, peer-bond search, sector context, continuing-disclosure scan, taxable-alternative quotes for the comparison grid, plus the 3+ adversarial searches). Execute them. Add more searches if the credit analysis surfaces ambiguity — verifying claims is non-negotiable, the 10–15 is a floor.

### Phase 2 — Pull EMMA and offering documents

EMMA (the MSRB's [Electronic Municipal Market Access](https://emma.msrb.org) system) is the primary source. The security URL pattern is `https://emma.msrb.org/Security/Details/{CUSIP}` (CUSIP without spaces or dashes). From the security page, capture:

- **Issuer name and series** (e.g., "State of Illinois GO Bonds, Series of November 2024 A")
- **Issue date, maturity date, coupon, dated date**
- **Par amount outstanding** (some bonds have been partially called or refunded)
- **Tax status** (tax-exempt / AMT / taxable — top of the EMMA page)
- **Use of proceeds**
- **Insurance, if any** (insurer name) and the underlying rating
- **Current ratings** from Moody's, S&P, Fitch (and prior ratings if available)
- **Call schedule** (every call date and price)
- **Sinking fund schedule**, if applicable
- **Continuing disclosure filings list** (annual financial information, operating data, audited financials)
- **Material event notices** filed against the security
- **Recent trade history** (last ~30 trades or last 12 months, whichever is shorter): trade date, price, yield, par amount, type (customer-buy, customer-sell, inter-dealer)

Also pull the Official Statement (OS). EMMA hosts an "Official Statement" link on the security page; download and skim for the security description, sources of payment, security provisions, debt service schedule, financial statements of the issuer, and the audit report.

### Phase 3 — Yield analysis and tax-equivalent yield

Compute and report:

- **Current price** (last trade or current ask, with as-of date and source)
- **YTM** (yield-to-maturity at current price)
- **YTC at each call date** (typically par on or after the 10-year call; sometimes premium calls earlier)
- **YTW = min(YTM, YTC across all call dates)** — primary yield figure
- **Current yield** (annual coupon / current price) for context

For an in-state bond, compute both:

```
TEY (federal only) = YTW / (1 - federal_marginal_rate)
TEY (federal + state) = YTW / (1 - federal_marginal_rate - state_marginal_rate × (1 - federal_marginal_rate))
```

For an out-of-state bond, only the federal-only TEY applies (the user pays state tax on the interest).

If the bond is **taxable** (BAB or other), TEY is undefined — compare YTW directly to same-maturity Treasuries and AAA corporates without the TEY transformation. Make this explicit in the report.

### Phase 4 — Tax considerations

**AMT screen.** Check the EMMA "Tax Status" field and the OS for AMT subjectivity. Private activity bonds (airports, multifamily housing, certain student loan revenue bonds, certain hospital bonds depending on use of proceeds) often have AMT-subject interest. If `amt_exposed = true` and the bond is AMT-subject, the effective TEY for the user is materially lower — compute the AMT-adjusted TEY using the user's AMT rate (typically 28%) and surface it as the relevant yield.

**De minimis check.** For any bond trading at a discount, compute the de minimis threshold:

```
De minimis threshold = par - 0.25% × years_to_maturity
```

If the current purchase price is below the threshold, the accreted discount is taxed as ordinary income (not capital gains, not tax-exempt) on redemption or sale. Recompute the after-tax yield treating the accretion as ordinary income; report both pre- and post-de-minimis after-tax yields.

For a premium bond (price > 100), note that the premium amortizes against the coupon over time, so the YTW is the relevant economic yield, not the coupon rate.

**State-specific.** Most states exempt their own munis from state income tax; some have reciprocal arrangements with neighbors or treat U.S. territory (Puerto Rico, Guam, USVI) bonds as in-state. A handful (e.g., IL, WI, OK have historically had quirks; some allow only federally-tax-exempt munis as state-exempt) — verify the specific state's rule against state tax-authority guidance, do not assume.

### Phase 5 — Defeasance / pre-refunding check

Read the OS and EMMA material event notices for any defeasance. If the bond has been advance-refunded with U.S. Treasuries (or sometimes state-and-local-government-series securities) placed in escrow sufficient to pay remaining coupons and the call/maturity payment, the bond is effectively backed by the escrow, not the original issuer.

If pre-refunded:
- The credit analysis simplifies — only the escrow composition matters.
- Confirm what's in the escrow: SLGS, open-market Treasuries, or other securities.
- Effective rating becomes the escrow rating (typically AAA / Aaa for Treasury-backed escrows).
- Short-circuit Phases 6–7's credit analysis; note "Pre-refunded to {call date}, escrowed in {escrow composition}; underlying credit not load-bearing."

If not pre-refunded, proceed to Phase 6.

### Phase 6 — Insurance unwrap

Check whether the bond is insured. Current active muni insurers include Assured Guaranty Municipal (AGM), Build America Mutual (BAM), and (historically) others that have been downgraded or stopped writing new business (AGC, NPFG, AMBAC, MBIA — relevant for older insured bonds still outstanding).

For an insured bond:
- Report the insurer and its current financial-strength rating.
- Report the **underlying** rating (the issuer's rating without insurance) — this is published separately and shows on EMMA.
- The effective rating is the higher of insurer and underlying — but insurer ratings can deteriorate; surface both.
- If the insurer rating has been downgraded since issuance, the market value has likely fallen — note this in the liquidity discussion.

If uninsured, the issuer's published rating is the operative one.

### Phase 7 — Credit analysis

Branch on bond type.

**GO bond branch.** Evaluate:

- **Tax base diversity** — top 10 taxpayers as % of assessed value (concentration risk if any single taxpayer > 5%)
- **Tax base trend** — assessed value growth over the last 5 years (declining is a red flag)
- **Economic base** — population trend, employment trend, unemployment rate, median household income vs state and national medians
- **Debt burden ratios** — debt per capita, direct debt / assessed value, overall debt (including overlapping debt) / assessed value
- **Fund balance** — unassigned general fund balance as % of expenditures (15–20% is healthy; below 5% is a concern)
- **Pension and OPEB load** — net pension liability per capita, NPL as % of revenues, funded ratio of pension plan, OPEB unfunded liability. This is the silent killer for many U.S. municipalities — IL, NJ, CT, Chicago, Hartford, and similar issuers have outsized pension/OPEB drags on credit.
- **Recent budget actuals vs. budget** — chronic budget gaps signal structural problems

**Revenue bond branch.** Evaluate:

- **Debt Service Coverage Ratio (DSCR)** = net revenue available for debt service / annual debt service. Look at the last 5 years.
- **Sector-specific DSCR benchmarks** for context:

| Sector | Typical DSCR floor | Strong DSCR |
|---|---|---|
| Water / Sewer | 1.20–1.25× | 1.50×+ |
| Public Power (Electric Utility) | 1.25× | 1.50×+ |
| Higher Education | 1.10–1.20× | 1.50×+ |
| Hospital / Healthcare | 1.50× | 2.00×+ |
| Airports | 1.15–1.25× | 1.50×+ (with growth) |
| Toll Roads (mature) | 1.30×+ | 1.75×+ |
| Multifamily Housing | 1.10–1.25× | 1.30×+ |
| Charter Schools | 1.10–1.25× | (varies; high credit risk) |

- **Rate covenant** — minimum DSCR the issuer pledges to maintain (often 1.10× or 1.25×). Has the issuer ever breached it?
- **Additional bonds test (ABT)** — what DSCR coverage is required to issue additional bonds parity with this one?
- **Demand analysis** — for utilities: customer count trend, consumption trend; for airports: enplanements; for toll roads: traffic and toll revenue
- **Reserve fund adequacy** — debt service reserve fund typically required at MADS (maximum annual debt service); confirm it's fully funded
- **Concentration risk** — for a hospital revenue bond, is the system dominated by Medicare/Medicaid? For a public power bond, is there a single large industrial customer?

### Phase 8 — Continuing disclosure and material events review

Pull the continuing disclosure filings list from EMMA. Check:

- **Filing timeliness** — annual financial information must be filed within the issuer's stated continuing-disclosure agreement deadline (typically 180–270 days after fiscal year end). Late filings are a yellow flag; chronic lateness is a red flag.
- **Audit completeness** — has the issuer received any audit qualifications or going-concern language?
- **Material event notices** filed against this security — under SEC Rule 15c2-12, issuers must disclose 14 categories of material events including rating changes, defeasances, calls, draws on debt service reserve, payment delinquencies, bankruptcy proceedings, and certain tax-status events.
- **Recent rating actions** — outlook changes (Stable → Negative is meaningful), downgrades, withdrawals.

A recent negative-watch placement or downgrade is often more informative than the current rating itself.

### Phase 9 — Liquidity assessment

From EMMA's trade history:

- **Trade count** in the last 12 months (a bond with zero or one trades in 12 months is effectively illiquid)
- **Bid-ask spread** estimated from recent customer-buy vs. customer-sell trades at similar par amounts
- **Trade size distribution** — small odd lots vs. round-lot blocks
- **Most recent trade price and yield** with date

For most retail investors, a muni with fewer than 5 trades in the last 6 months should be treated as "buy-and-hold only" — selling before maturity may require taking a significant haircut to the market.

### Phase 10 — Comparables and taxable alternatives

**Peer munis** — find 3–5 munis with similar characteristics (state, sector, rating, maturity within ±2 years). Build a comparison table of YTW, price, current yield, and the credit metric most relevant to the sector (DSCR for revenue, debt burden for GO).

**Taxable alternatives at the same duration** — build a grid:

| Alternative | YTW | Tax treatment | After-tax yield to user |
|---|---|---|---|
| U.S. Treasury (same maturity) | | Federal taxable, state exempt | |
| AAA corporate (same maturity) | | Fully taxable | |
| Brokered CD (same maturity) | | Fully taxable | |
| **This muni** | | Tax-exempt (or AMT-subject if applicable) | (use TEY) |

The relevant comparison is **after-tax yield**, computed at the user's configured bracket. Treasuries are federal-taxable but state-exempt; CDs and corporates are fully taxable. This grid is the load-bearing piece of the relative-value verdict.

**Muni / Treasury ratio** — compute YTW (muni) / YTW (Treasury same maturity). Historical AAA muni / 10Y Treasury averages ~80%; above 85% suggests munis are cheap, below 70% suggests rich. Adjust for the bond's actual rating (BBB munis trade at higher ratios; AAA insured trade at lower).

### Phase 11 — Buy case, pass case, base case, verdict

**Buy case** — the most credible scenario in which this bond is a good purchase. Required: after-tax yield clears a meaningful premium over taxable alternatives (typically 30+ basis points), credit is investment-grade and stable or improving, the call structure does not heavily penalize the yield projection, liquidity is acceptable for the intended hold. State the implied after-tax IRR and conditions that hold.

**Pass case** — the most credible scenario in which this is the wrong bond. Concrete pass triggers: TEY does not clear taxable alternatives; credit is on negative watch or has had a recent downgrade; DSCR trend is declining; tax base is shrinking; pension/OPEB load is rising; the bond is AMT-subject and the user is AMT-exposed; bond is below de minimis threshold and the after-tax math no longer works; trading is illiquid and the user may need to sell early; insurance is the only thing propping up the rating and the insurer has been downgraded.

**Base case** — the most likely net outcome. Hold to call or maturity at YTW; state the after-tax IRR at the user's bracket.

**Verdict** — one of **Buy / Watch / Pass**.

- **Buy** — base-case after-tax yield clears taxable alternatives by a clear margin AND no major credit / liquidity / tax red flags surfaced.
- **Pass** — TEY does not clear taxable alternatives, OR a material red flag exists.
- **Watch** — interesting but premature: a named, dated near-term event (e.g., upcoming rating action, upcoming budget release, expected rate-environment shift) would resolve the load-bearing uncertainty. State the event and the threshold that would convert Watch into Buy.

**Confidence:** Low / Medium / High — one sentence on what would move you to higher confidence.

**Sizing guidance** (qualitative): full intended position / minimum lot / pass entirely.

### Phase 12 — Open Questions

The three most important unknowns. For each, state how the user (or a follow-up run) could resolve it — a specific EMMA filing to monitor, a continuing-disclosure date to revisit, a rating-agency action to watch, a sector data release.

### Phase 13 — Append footnote definitions and disclaimer

After Phase 12, append footnote definitions in numbered order:

```
[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

Title is the clickable link text; the URL is wrapped in markdown link syntax so the rendered footnote shows an actual hyperlink, not a bare URL. **All URLs in the report — body and footnotes — must use markdown link syntax `[descriptive text](url)`.** Bare URLs are forbidden.

Reuse a footnote number when citing the same source again; do not duplicate definitions. Verify every `[^N]` in the body has a matching definition and vice versa.

Immediately before the footnote definitions, include the one-line disclaimer: *Not investment advice. Verify all figures independently before acting.*

### Phase 14 — Save the report

Always save under `~/.hermes/reports/muni/`. Create the directory if it does not exist.

The filename is `{CUSIP}.md` (9-character CUSIP, no formatting), or `{ISSUER-SLUG}-{MATURITY-YEAR}.md` for new issues without a CUSIP yet (slug lowercased, alphanumeric + hyphens only, capped at 50 chars; e.g., `nyc-tfa-2042.md`). One canonical file per bond, accumulating history.

**First run** (file does not exist): write the full report. Top-level heading `# {CUSIP_OR_SLUG} — Municipal Bond Tracker`, then place the body under `## Initial Analysis — {YYYY-MM-DD}`. End with the disclaimer and footnote definitions block.

**Subsequent run** (file exists, new material event, rating change, or user re-evaluating): append:

- A horizontal rule (`---`) followed by `## Addendum — {YYYY-MM-DD}`.
- Lead with **What changed since the last entry** — new rating action, new material event, new continuing-disclosure filing, price/yield move, market environment shift.
- Update only sections that have meaningfully changed. Skip unchanged sections.
- If the verdict changes, state explicitly from what to what.

**Citations across runs use a single merged footnote list.** Find the highest existing `[^N]` at the end of the file. Number new citations starting at `[^N+1]`. Reuse existing numbers for already-defined sources. Append new `[^N]: ...` definitions to the existing footnote block so the list remains a single monotonically-numbered series.

**If the bond has been called, defeased, or matured** between runs: do not write a new addendum. Append a final `## Closed — {YYYY-MM-DD}` section stating the closing event and any realized return calculation, then stop writing to this file.

After saving, report the absolute path of the file to the user.

## Output Rules

- No marketing language, no hype, no hedging adjectives like "robust" or "strong" without a number behind them. Replace qualitative judgments with quantified credit metrics or rating actions.
- No phrases like "as an AI" or "I cannot give financial advice." End the body (before the footnote definitions) with the one-line disclaimer.
- If a tool call fails or data is unavailable for a required field, write `DATA UNAVAILABLE` and explain what was tried. Do not guess yields, ratings, or DSCRs.
- Prefer primary sources: EMMA, the OS, the audited financials of the issuer, continuing-disclosure filings, ratings-agency press releases. Demote: brokerage marketing pages, secondary-market commentary without primary citation.
- Maximum length: roughly 3,000 words for the report body. Density over volume. Footnote definitions do not count toward the word limit.

## Report Template

Use this skeleton verbatim.

```markdown
# {CUSIP_OR_SLUG} — Municipal Bond Tracker

## Initial Analysis — {YYYY-MM-DD}

### TL;DR

[One paragraph: bond identification (issuer, maturity, coupon), current price/YTW as-of date, verdict (bold), confidence, core thesis in one sentence, top risk in one sentence.]

---

### 1. Bond identification

| Field | Value | Source |
|---|---|---|
| CUSIP | | |
| Issuer | | |
| Series | | |
| Maturity date | | |
| Coupon | | |
| Dated date | | |
| Par outstanding | | |
| Tax status | tax-exempt / AMT / taxable | |
| Insurance | | |
| Use of proceeds | | |
| Call schedule | (date, price) entries | |
| Sinking fund | | |

---

### 2. Yield analysis

**Current price (as-of YYYY-MM-DD):** [price]

| Metric | Value |
|---|---|
| YTM | |
| YTC at first call ({date}, {price}) | |
| YTC at subsequent calls | |
| **YTW** | **(min of above)** |
| Current yield | |

**Tax-equivalent yield (at user's bracket):**

| Metric | Value |
|---|---|
| Federal marginal rate (config) | |
| State marginal rate (config) | |
| In-state for user (`state_code` vs issuer state) | yes / no |
| TEY (federal only) | YTW / (1 − fed) = |
| TEY (federal + state) | YTW / (1 − fed − state × (1 − fed)) = |
| AMT-adjusted TEY (if `amt_exposed` and bond is AMT-subject) | |

---

### 3. Tax analysis

**AMT exposure:** [yes / no, with reason]

**De minimis check:**

| Field | Value |
|---|---|
| Current price | |
| Years to maturity | |
| De minimis threshold (par − 0.25% × YTM) | |
| Below threshold? | yes / no |
| After-tax yield treating discount as ordinary income (if below) | |

**State-specific treatment:** [in-state benefit applies / out-of-state, federal-only / reciprocal arrangement / state-specific quirk]

---

### 4. Defeasance / pre-refunding

[Pre-refunded yes/no. If yes: refunding date, call date escrowed to, escrow composition (SLGS / open-market Treasuries / other), effective rating. If no: state explicitly so the credit analysis below is the operative section.]

---

### 5. Insurance

| Field | Value |
|---|---|
| Insurer | |
| Insurer current rating | |
| Underlying rating | |
| Effective rating | |
| Insurer rating trend since issuance | |

---

### 6. Credit analysis

[GO branch OR Revenue branch — use the relevant template below; delete the other.]

**GO branch:**

| Metric | Value | Trend | Benchmark |
|---|---|---|---|
| Tax base concentration (top 10 taxpayers % of AV) | | | <30% healthy |
| Tax base growth (5y) | | | |
| Population trend (5y) | | | |
| Unemployment rate | | | |
| Median household income vs state | | | |
| Debt per capita | | | |
| Overall debt / AV | | | |
| Unassigned fund balance / expenditures | | | 15–20% healthy |
| Net pension liability per capita | | | |
| NPL / revenues | | | |
| Pension funded ratio | | | >80% healthy |
| OPEB unfunded liability | | | |

**Revenue branch:**

| Metric | Value | Trend | Benchmark (sector) |
|---|---|---|---|
| DSCR (last 5y) | | | (from sector table) |
| Rate covenant minimum | | | |
| Has issuer ever breached the rate covenant? | | | |
| Additional bonds test | | | |
| Demand metric (customers, enplanements, traffic) | | | |
| Debt service reserve adequacy | | | MADS-funded |
| Concentration risk | | | |

---

### 7. Continuing disclosure and material events

**Filing timeliness:** [on-time / late by N days / chronically late / missing]

**Audit status:** [clean / qualified / going-concern]

**Recent material event notices (last 24 months):**

| Date | Event type | Notes |
|---|---|---|

**Recent rating actions:**

| Date | Agency | From → To | Outlook | Notes |
|---|---|---|---|---|

---

### 8. Liquidity

| Metric | Value |
|---|---|
| Trades in last 12 months | |
| Trade-size distribution | odd-lot dominant / round-lot present |
| Estimated bid-ask spread (recent customer trades) | |
| Most recent trade (date, price, yield, type) | |
| Assessment | liquid / thin / effectively illiquid |

---

### 9. Comparables and taxable alternatives

**Peer munis:**

| CUSIP | Issuer | Maturity | Rating | YTW | Notes |
|---|---|---|---|---|---|

**Taxable alternatives (same duration, after-tax to user):**

| Alternative | YTW | Tax treatment | After-tax yield to user |
|---|---|---|---|
| U.S. Treasury (same maturity) | | Federal-taxable, state-exempt | |
| AAA corporate (same maturity) | | Fully taxable | |
| Brokered CD (same maturity) | | Fully taxable | |
| **This muni** | | Tax-exempt (or AMT-subject) | **(use TEY)** |

**Muni / Treasury ratio:** [YTW(muni) / YTW(Treasury same maturity)] — vs ~80% historical AAA average; rich / fair / cheap.

---

### 10. Buy case

[Most credible "this is a good purchase" scenario. Required assumptions, the after-tax yield premium over alternatives, what holds for this to work, probability of the case.]

---

### 11. Pass case

[Most credible "this is the wrong bond" scenario. Concrete pass triggers from the credit / yield / liquidity analysis above. Probability.]

---

### 12. Base case and verdict

**Base case:** [held to YTW horizon, after-tax IRR at user's bracket]

**Verdict:** **[Buy / Watch / Pass]**

**Confidence:** **[Low / Medium / High]** — [one sentence on what would move you higher]

**Sizing:** [full position / minimum lot / pass entirely]

---

### 13. Open Questions

1. [Unknown #1] — [how to resolve: specific filing / disclosure date / rating action to watch]
2. [Unknown #2] — [how to resolve]
3. [Unknown #3] — [how to resolve]

---

*Not investment advice. Verify all figures independently before acting.*

[^1]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
[^2]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>
```

## Notes

- **YTW-vs-YTM confusion.** A callable muni's quoted yield is often YTM, which overstates expected return if the issuer is likely to call. Always anchor on yield-to-worst; if YTW < YTM, state the call date that drives YTW and what call probability assumption that implies.
- **De minimis blindness.** A discount muni below the de minimis threshold loses tax-exempt treatment on the accretion portion. The after-tax math can flip from "competitive" to "underwater vs. Treasuries" silently. Always compute the threshold for any discount bond.
- **Insurance-as-credit.** A bond rated AA only because of insurance is functionally a play on the insurer's credit. Always surface the underlying rating; if the insurer has been downgraded since issuance, the market price has likely fallen and the effective rating is now the underlying.
- **Pre-refunded misclassification.** A pre-refunded bond is functionally a U.S. Treasury proxy. If credit analysis is conducted on the underlying issuer instead of the escrow, the analysis is meaningless. Always check pre-refunding status first.
- **Sector-mismatched DSCR comparison.** A 1.30× DSCR is strong for a multifamily housing bond but mediocre for a toll road. Always reference the sector benchmark table; do not apply a single DSCR floor across sectors.
- **Pension/OPEB blindness on GO bonds.** Some of the largest municipal credit losses (Detroit, Puerto Rico, Chicago Public Schools' near-misses) were driven by pension obligations crowding out debt service. Always pull the NPL and funded ratio; treat NPL/revenues as a primary credit metric for GO bonds.
- **Stale continuing disclosure.** A bond whose issuer has not filed annual financial information in 18+ months should be treated as data-impaired. State `DATA UNAVAILABLE` for the missing year and call it out as a yellow flag.
- **Treating brokerage marks as primary.** Schwab/Fidelity/Vanguard "current yield" displays on muni pages are derived; the primary source is EMMA's trade history. Cite EMMA, not the brokerage UI.
- **Citation drift.** Every `[^N]` reference in the body must have a matching `[^N]: ...` definition, and every definition must be referenced at least once. No gaps.
- **Bare URLs.** All URLs use markdown link syntax `[descriptive text](url)`. Verify before delivering.

## Verification

Before reporting completion to the user, confirm:

1. The report file exists at `~/.hermes/reports/muni/{CUSIP-or-slug}.md` (verify with `ls -la ~/.hermes/reports/muni/ | tail -5`).
2. Tax-bracket config values were loaded; any missing values were resolved with the user before analysis.
3. Every yield, rating, DSCR, tax-base figure, and material-event reference has either a `[^N]` footnote or a `DATA UNAVAILABLE` tag.
4. Section 1 identification table has no blanks (use `DATA UNAVAILABLE` if truly unknown).
5. Section 2 reports YTM, YTC at each call date, and YTW; YTW is explicitly the primary yield figure. For non-callable bonds, YTW = YTM is stated explicitly.
6. Section 2 TEY shows both federal-only and federal+state (if in-state) with the user's configured rates.
7. Section 3 includes the de minimis threshold computation for any discount bond; for premium bonds, the YTW (not coupon) is identified as the economic yield.
8. Section 4 pre-refunding status is explicitly stated (yes/no). If yes, sections 5–7 are short-circuited with a one-line "underlying credit not load-bearing" note.
9. Section 6 uses the GO branch OR the Revenue branch (not both), and the DSCR (revenue) or pension load (GO) is benchmarked against sector / state norms.
10. Section 7 reviews continuing-disclosure timeliness and recent material events.
11. Section 9 includes both the peer-muni table and the taxable-alternatives grid expressed at the user's after-tax bracket.
12. Section 12 verdict is one of **Buy / Watch / Pass** with a confidence level and qualitative sizing guidance.
13. Section 13 lists at least three open questions with a concrete resolution path each.
14. The footnote list at the end uses markdown link form `[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>`. Numbering consecutive with no gaps. Every `[^N]` inline reference has a matching definition; every definition is referenced.
15. No bare URLs anywhere in the report.
16. The disclaimer line appears immediately before the footnote definitions.
17. Report body is under ~3,000 words.
18. The absolute file path has been reported to the user.
