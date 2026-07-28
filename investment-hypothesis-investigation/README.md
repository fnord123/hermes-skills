# investment-hypothesis-investigation — human notes

Stress-tests one directional investment thesis spanning multiple companies, a
sector, or a macro variable, and saves a living report to
`~/.hermes/reports/research/YYYY-MM-DD_<slug>.md`.

## Routing

Four investment skills sit next to each other and were mutually confusable
before their descriptions were rewritten to lead with the discriminating noun:

| Subject | Skill |
|---|---|
| A theme, sector, or macro claim spanning several companies | `investment-hypothesis-investigation` |
| One listed security (incl. muni ETFs and closed-end funds) | `stock-investment-analysis` |
| One private company round | `pre-ipo-investment-analysis` |
| One municipal bond by CUSIP | `municipal-bond-analysis` |

The routing lives in the `description` frontmatter, because that is the only
field Hermes reads when deciding which skill to activate. A `related_skills`
key used to carry this intent; it is not a Hermes frontmatter key, so nothing
read it.

## Failure modes to watch for in the output

These used to live in SKILL.md as a `## Notes` section. They are recorded here
rather than in model context, because naming a failure mode to the model primes
it; each already has a positive counterpart in the SKILL.md Verification
checklist.

1. **Skipping Phase 2.** Without a quantified consensus, "the evidence supports the hypothesis" is meaningless — the question is whether evidence supports it *more than the market already believes*. Always quantify the baseline before gathering supporting evidence.
2. **Confirmation cascade.** If your evidence-for section is twice as long as evidence-against, you have not done the work. Force-search for the strongest counter-argument from a credible source.
3. **Vague probability.** "Likely" is not an estimate. "55–65% with medium confidence" is.
4. **No null-edge finding.** If the market is already pricing the hypothesis correctly, that is the right answer. Say so. Do not manufacture edge.
5. **Trade list without specifics.** Every named instrument in Section 7 must include all five fields: (a) ticker, (b) current price with explicit as-of date, (c) market cap or notional size, (d) one-line rationale for why it expresses the view, (e) the specific risk that breaks the trade even if the hypothesis is right. "Long electrical-component names" is useless. "GEV at $X (as-of YYYY-MM-DD), 12-month target $Y based on 18× forward EPS, breaks if data-center capex guidance cuts >15% in next two earnings cycles" is a trade. **If you cannot fill all five fields for an instrument, strike it from the list rather than including a half-formed entry.** Generic ETF baskets ("dry bulk ETFs", "shipping stocks") without named tickers count as half-formed.
6. **Footnote drift.** Every `[^N]` reference in the body must have a matching `[^N]: ...` definition at the end of the report, and every definition must be referenced at least once. No gaps in numbering. Verify before saving.
7. **Saving to the wrong directory.** Reports go to `~/.hermes/reports/research/`, not `cwd`, not `/tmp`. Create the directory if missing.
8. **Renumbering on edit.** When extending the report, append new footnotes with the next available number. Never renumber existing ones — the user may have linked to them.
9. **Stale prices.** Any quoted price, multiple, or yield must have an as-of date. Pull live, do not rely on training data.
10. **Skipping stock-investment-analysis when a specific ticker is named.** If the user says "analyze [company]" or names a ticker, that's a single-stock request — delegate to `stock-investment-analysis`. Do not try to do valuation, bull/bear cases, or financial deep dives within this skill.
11. **Overclaiming certainty on market timing.** Even with strong evidence, assign probabilities honestly. A 70% conviction thesis can still lose money if the catalyst is priced in.
12. **Citing blogs and social media as primary evidence.** Footnotes whose URLs point to Substack, Medium, X/Twitter, personal blogs, or SaaS-company marketing pages are secondary at best. Replace with the underlying primary source — the SEC filing, government data (EIA, BLS, IMF, Treasury, central bank), regulatory release, court document, peer-reviewed paper, or major-publication article they are paraphrasing. If you cannot find the primary source, demote that claim's weight rather than treating the blog as canonical. Target ≥75% of footnotes pointing to primary sources.
13. **Stale data without flagging.** When citing a report, filing, or assessment whose date is older than the most recently filed quarter (for fundamentals) or older than 30 days (for prices, prediction-market odds, multiples), explicitly note the as-of date in the body and flag that the figure may be stale. Do not silently treat year-old assessments as current.

## Requirements

`metadata.hermes.requires_toolsets: [web, file]` — the skill is wholly dependent
on web research and on writing the report file. Without those toolsets in the
active profile it would activate and then fail with nothing to show for it.
