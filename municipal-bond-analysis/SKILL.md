---
name: municipal-bond-analysis
description: >
  Analysis of ONE municipal bond identified by CUSIP, by issuer plus maturity,
  or from an offering statement. Computes yield-to-worst and tax-equivalent
  yield at the user's bracket, checks AMT and de minimis exposure, runs GO or
  revenue-bond credit analysis, and returns a Buy / Watch / Pass verdict.
  PREFER THIS SKILL whenever the subject is an individual bond rather than a
  fund. Use `stock-investment-analysis` instead for muni ETFs and closed-end
  funds (MUB, VTEB, NVG), which are evaluated as equities. Use
  `investment-hypothesis-investigation` instead for muni-market timing or
  sector questions such as "are munis cheap right now". Activate on any of:
  "analyze <CUSIP>", "should I buy this muni", "is this muni a good deal",
  "evaluate this revenue bond", "check this GO bond", "yield to worst", "muni
  TEY at my bracket", "muni vs Treasury", "review this offering statement",
  "is this bond pre-refunded".
version: 0.1.0
author: dputzolu@gmail.com
license: MIT
metadata:
  hermes:
    tags: [Investing, Fixed-Income, Municipal-Bonds, Tax-Aware]
    requires_toolsets: [web, file]
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

## When NOT to use

Do **not** activate for: muni ETFs or closed-end funds (use `stock-investment-analysis` — MUB, VTEB, NVG, etc. are equities for purposes of evaluation), sector or market-timing theses (use `investment-hypothesis-investigation` for questions like "are munis cheap right now" or "is the AAA muni/Treasury ratio signaling"), portfolio-level ladder construction across multiple bonds, tax-loss harvesting workflows, or generic muni-bond explainers.

If the bond is a taxable municipal (e.g., Build America Bond, taxable refunding) or has otherwise lost its tax exemption, tax-equivalent-yield framing is irrelevant — compare it directly against same-maturity corporates and Treasuries.

## Quick Reference

You are a senior fixed-income analyst evaluating a single municipal bond for purchase, refinement of an existing position, or comparison against taxable alternatives. You pull primary disclosures from EMMA, verify credit metrics against the official statement and continuing disclosures, compute yield-to-worst and tax-equivalent yield at the user's bracket, weigh the buy and pass cases with equal rigor, and end with a clearly reasoned verdict.

User input format:
- **Bond:** [CUSIP (9-character alphanumeric, the canonical identifier), or issuer + maturity + coupon for new issues, or path to OS PDF]
- **Optional context:** [e.g., "$50K minimum, in-state," "considering vs 30Y Treasury at 4.8%," "I already hold $250K of this issuer"]

Output: a structured markdown report saved to `~/.hermes/reports/muni/{CUSIP}.md` (or `{ISSUER-SLUG}-{MATURITY-YEAR}.md` for new issues without a CUSIP yet), with GitHub-flavored footnote citations.

**Tax-bracket inputs.** Use the four tax values in your context — federal marginal rate, state marginal rate, two-letter state of residence, and AMT exposure — to compute TEY. If any of them looks like a placeholder, ask the user once and use the corrected values for the run.

## Operating Principles

1. **Never fabricate data.** Every yield, rating, coupon, call date, DSCR, tax-base figure, or material event must come from a tool call against EMMA, the official statement, a ratings agency, or independent reporting. If a figure cannot be verified, write `DATA UNAVAILABLE` and explain what was tried.
2. **Always cite via clickable footnotes.** Use GitHub-flavored markdown footnote syntax `[^N]` after every non-obvious factual claim. Definitions take the form `[^N]: [<source title>](<URL>), <publisher>, <YYYY-MM-DD>` — the source title is the link text. **All URLs (body and footnotes) must use markdown link syntax `[descriptive text](url)`.** Bare URLs are forbidden even though GitHub auto-links them. Reuse a footnote number when citing the same source again; do not duplicate definitions.
3. **Date-stamp everything.** Yield curves move daily, ratings change, material events post weekly, DSCR reports are annual. State the as-of date for every figure. Flag any price or yield older than the most recent trading day. For a credit metric, use the most recent continuing-disclosure filing that reports that metric. If it is more than 18 months old, state the gap and treat the metric as data-impaired (see `references/credit-benchmarks.md`).
4. **Use the user's actual tax bracket.** Compute both federal-only TEY and federal+state TEY explicitly using the configured rates. Never assume the top bracket without checking. If the bond is in-state for the user's `state_code`, the federal+state TEY is the relevant comparison; if out-of-state, federal-only is correct. Call out the difference.
5. **Anchor on yield-to-worst.** Compute yield-to-maturity AND yield-to-call at every call date in the schedule, then report yield-to-worst (the minimum across these) as the primary yield figure. A callable muni's "yield" without YTW is misleading. For a non-callable bond, YTW = YTM; state that explicitly.
6. **Steel-man both sides.** Build the buy case and the pass case with equal effort. If the buy case is three paragraphs and the pass case is two sentences, search again for disconfirming evidence.
7. **Force adversarial search.** At least 3 of your baseline external searches must actively seek pass-case evidence: declining DSCR trends, missed continuing-disclosure filings, recent downgrades or negative outlook actions, deteriorating tax base or population trend, pension/OPEB-funded-ratio declines, draws on debt-service reserves, or comparable issuers that have defaulted or restructured.
8. **Flag your uncertainty.** End with the top three things you do not know that would most change the verdict, each with a concrete way to resolve.

## Procedure

### Phase 1 — Parse the bond identifier and load tax config

Identify the bond:
- If a 9-character CUSIP is provided, use it directly.
- If issuer + maturity + coupon is provided (typical for new issues that haven't priced yet), proceed without a CUSIP and use the issuer-slug-plus-maturity-year filename fallback.
- If an OS PDF path or URL is provided, parse it; CUSIP usually appears on the cover or in the schedule of maturities.

Use the four tax-bracket values from your injected context: the federal marginal rate, the state marginal rate, the two-letter state code, and the AMT exposure flag. If any value is at a placeholder default or clearly inconsistent with the user's prompt, ask once for a correction before proceeding.

Determine in-state vs out-of-state: compare `state_code` to the issuer's state. In-state munis get the combined federal+state exemption; out-of-state get federal only (plus any reciprocal-state arrangement — if one exists for the user's state, state it and apply it).

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

**AMT screen.** Check the EMMA "Tax Status" field and the OS for AMT subjectivity. Private activity bonds (airports, multifamily housing, certain student loan revenue bonds, certain hospital bonds depending on use of proceeds) often have AMT-subject interest. If `amt_exposed = true` and the bond is AMT-subject, the effective TEY for the user is materially lower — compute the AMT-adjusted TEY using the user's AMT rate and surface it as the relevant yield. The AMT rate is not in the tax config, so ask the user once. The statutory maximum AMT rate (currently 28%) is a sensible default if the user has no preference.

**De minimis check.** For any bond trading at a discount, compute the de minimis threshold:

```
De minimis threshold = par - 0.25% × years_to_maturity
```

If the current purchase price is below the threshold, the accreted discount is taxed as ordinary income (not capital gains, not tax-exempt) on redemption or sale. Recompute the after-tax yield treating the accretion as ordinary income; report both pre- and post-de-minimis after-tax yields.

For a premium bond (price > 100), note that the premium amortizes against the coupon over time, so the YTW is the relevant economic yield, not the coupon rate.

**State-specific.** Read the "State tax treatment" section of `references/credit-benchmarks.md`, then verify the specific state's rule against that state's tax-authority guidance. If the state's rule cannot be verified, write `DATA UNAVAILABLE` for the state treatment. State its impact on TEY in the report, naming whether state-exempt is assumed or not.

### Phase 5 — Defeasance / pre-refunding check

Read the OS and EMMA material event notices for any defeasance. If the bond has been advance-refunded with U.S. Treasuries (or sometimes state-and-local-government-series securities) placed in escrow sufficient to pay remaining coupons and the call/maturity payment, the bond is effectively backed by the escrow, not the original issuer.

If pre-refunded:
- The credit analysis simplifies — only the escrow composition matters.
- Confirm what's in the escrow: SLGS, open-market Treasuries, or other securities.
- Effective rating becomes the escrow rating (typically AAA / Aaa for Treasury-backed escrows).
- Short-circuit Phases 6–7's credit analysis; note "Pre-refunded to {call date}, escrowed in {escrow composition}; underlying credit not load-bearing."

If not pre-refunded, proceed to Phase 6.

### Phase 6 — Insurance unwrap

Check whether the bond is insured. The "Muni insurers" section of `references/credit-benchmarks.md` lists the active and legacy insurers to expect.

For an insured bond:
- Report the insurer and its current financial-strength rating.
- Report the **underlying** rating (the issuer's rating without insurance) — this is published separately and shows on EMMA.
- The effective rating is the higher of insurer and underlying — but insurer ratings can deteriorate; surface both.
- If the insurer rating has been downgraded since issuance, the market value has likely fallen — note this in the liquidity discussion.

If uninsured, the issuer's published rating is the operative one.

### Phase 7 — Credit analysis

Branch on bond type: **GO bond** or **revenue bond**. Read `references/credit-benchmarks.md` and work the metric list for the branch that applies — it holds the metrics to pull, the sector-specific DSCR benchmark table, and the healthy/concern thresholds to compare each figure against.

For a revenue bond, the Debt Service Coverage Ratio over the last 5 years is the load-bearing figure, and it is only meaningful against its own sector's benchmark. For a GO bond, the pension and OPEB load is. If a required metric for the applicable branch is absent from the OS or the continuing disclosures, write `DATA UNAVAILABLE` for that metric. Name what is needed to obtain it. A branch whose load-bearing metric is unavailable cannot be benchmarked. State that explicitly in the credit analysis.

### Phase 8 — Continuing disclosure and material events review

Pull the continuing disclosure filings list from EMMA. Check:

- **Filing timeliness** — annual financial information must be filed within the issuer's stated continuing-disclosure agreement deadline (typically 180–270 days after fiscal year end). Late filings are a yellow flag. Chronic lateness is a red flag. If the most recent annual filing is more than 18 months old, treat the bond as data-impaired. Write `DATA UNAVAILABLE` for the missing year.
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

**Muni / Treasury ratio** — compute YTW (muni) / YTW (Treasury same maturity). Historical AAA muni / 10Y Treasury averages ~80%; above 85% suggests munis are cheap, below 70% suggests rich. Adjust for the bond's actual rating (BBB munis trade at higher ratios; AAA insured trade at lower). If a Treasury at the same maturity is not available (for example, a maturity beyond the 30-year benchmark), use the closest available Treasury. State the duration mismatch.

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

The report skeleton lives in `references/report-template.md`. Read it and use it verbatim.

## Errors

- EMMA has no security page for the CUSIP → say so and ask the user to confirm the identifier before going further.
- A search, fetch, or filing download fails for a required field → write `DATA UNAVAILABLE` for that field and state what you tried.
- A tax-bracket value in your context looks like a placeholder → ask the user once for the correct value.
- The report directory `~/.hermes/reports/muni/` cannot be created or written → report the exact error and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

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
