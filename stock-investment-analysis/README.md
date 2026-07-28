# stock-investment-analysis — human notes

Equity research on one publicly traded company, producing a full investment memo
saved to `~/.hermes/reports/company/{TICKER}.md`.

## Routing

Four investment skills sit next to each other and were mutually confusable
before their descriptions were rewritten to lead with the discriminating noun:

| Subject | Skill |
|---|---|
| One listed security (incl. muni ETFs and closed-end funds) | `stock-investment-analysis` |
| A theme, sector, or macro claim spanning several companies | `investment-hypothesis-investigation` |
| One private company round | `pre-ipo-investment-analysis` |
| One municipal bond by CUSIP | `municipal-bond-analysis` |

The routing lives in the `description` frontmatter, because that is the only
field Hermes reads when deciding which skill to activate.

## Failure modes to watch for in the output

These used to live in SKILL.md as a `## Notes` section. They are recorded here
rather than in model context, because naming a failure mode to the model primes
it; each already has a positive counterpart in the SKILL.md Verification
checklist.

- **Hallucinated multiples.** If forward P/E or EV/EBITDA cannot be sourced, mark `DATA UNAVAILABLE`. Do not back-calculate from a guessed earnings figure.
- **Stale prices.** A "current price" pulled from training data is wrong. Always fetch live, and timestamp it.
- **One-sided pattern matching.** If the bull case is three paragraphs and the bear case is three sentences, you have not done the work. Search again with disconfirming queries.
- **Citation drift.** Every `[^N]` reference in the body must have a matching `[^N]: ...` definition, and every definition must be referenced at least once. Verify before delivering.
- **Reverse-DCF skipped.** If valuation tools are limited, you can simplify the reverse-DCF to revenue-growth-implied alone, but do not omit it — it is the single most useful section for sanity-checking sentiment.
- **Mixing GAAP and adjusted figures within a comparison.** Pick one basis and stick to it within Section 3 and Section 4.

## Requirements

`metadata.hermes.requires_toolsets: [web, file]` — the skill is wholly dependent
on web research and on writing the report file. Without those toolsets in the
active profile it would activate and then fail with nothing to show for it.
