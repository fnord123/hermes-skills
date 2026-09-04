# Municipal bond report template

Read on demand from Phase 14 of `SKILL.md`. Use this skeleton verbatim.

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
| De minimis threshold (par − 0.25% × years_to_maturity) | |
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
