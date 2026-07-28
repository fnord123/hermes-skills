# municipal-bond-analysis — human notes

Analysis of one municipal bond, producing a report saved to
`~/.hermes/reports/muni/{CUSIP-or-slug}.md`.

## Routing

Four investment skills sit next to each other and were mutually confusable
before their descriptions were rewritten to lead with the discriminating noun:

| Subject | Skill |
|---|---|
| One municipal bond by CUSIP | `municipal-bond-analysis` |
| One listed security, **including muni ETFs and closed-end funds** (MUB, VTEB, NVG) | `stock-investment-analysis` |
| A theme, sector, or macro claim spanning several companies (incl. "are munis cheap") | `investment-hypothesis-investigation` |
| One private company round | `pre-ipo-investment-analysis` |

The routing lives in the `description` frontmatter, because that is the only
field Hermes reads when deciding which skill to activate. A `related_skills`
key used to carry this intent; it is not a Hermes frontmatter key, so nothing
read it.

The description and body previously cited `NMBIY` as an example muni ETF
alongside MUB and VTEB. No such ticker exists — it was replaced with VTEB in
the description and with NVG (Nuveen AMT-Free Municipal Credit Income Fund, a
closed-end fund) where a third example was needed.

## Size

SKILL.md is injected in full on every activation, so on-demand material was
moved to `references/`:

- `references/credit-benchmarks.md` — state tax treatment, the muni-insurer
  roster, and the GO / revenue-bond credit metrics including the sector-specific
  DSCR benchmark table. Read from Phases 4, 6 and 7.
- `references/report-template.md` — the report skeleton. Read from Phase 14.

`metadata.hermes.requires_toolsets: [web, file]` was added: the skill is wholly
dependent on web research (EMMA, offering statements, ratings) and on writing
the report file.

## Failure modes to watch for in the output

These used to live in SKILL.md as a `## Notes` section. They are recorded here
rather than in model context, because naming a failure mode to the model primes
it; each already has a positive counterpart in the SKILL.md Verification
checklist.

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
