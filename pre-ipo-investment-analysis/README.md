# pre-ipo-investment-analysis — human notes

Due diligence on one private-company round, producing a report saved to
`~/.hermes/reports/private-company/{COMPANY-SLUG}.md`.

## Routing

Four investment skills sit next to each other and were mutually confusable
before their descriptions were rewritten to lead with the discriminating noun:

| Subject | Skill |
|---|---|
| One private company round or secondary | `pre-ipo-investment-analysis` |
| One listed security (incl. muni ETFs and closed-end funds) | `stock-investment-analysis` |
| A theme, sector, or macro claim spanning several companies | `investment-hypothesis-investigation` |
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

- **Marketing-as-fact.** The fund summary is sales material. Numbers that drive the investment thesis — backlog, ARR, deployed units, market size, claimed customer commitments — must be independently verified or labeled `UNVERIFIED`.
- **Footnote-verification skip.** A footnote in the source doc is not verification of the underlying claim. Walk every footnote, fetch the cited source, and confirm the number in the source actually matches the body. A footnote pointing to the company's own press release is corroboration of the company's claim, not independent verification.
- **Exit math without fees.** A 3× gross multiple after placement fee (5%), offering costs (1.5%), annual management fee (0.5%/yr × hold years), and 10% carry on profit is closer to a 2.3× net multiple. Always compute and report the net-of-fees figure separately from gross.
- **Preference-stack blindness.** Series B Preferred sits behind Series A Preferred in the waterfall. At exits below ~2× post-money the preference stack materially eats into the new round's economics. Surface the waterfall at low-end exits.
- **One-sided invest case.** If the bull case is three paragraphs and the bear case is three sentences, you have not done the work. Force-search for down-rounds, missed milestones, founder controversies, customer churn, and prior-round terms.
- **Company-press-as-independent.** A claim cited to the company's own newsroom or X account is the company's claim, not independent verification. Demote.
- **Founder-exit claim drift.** "X was acquired by a defense contractor" without buyer name, year, and price is a red flag. Verify acquirer identity, transaction year, and reported price; flag if the outcome was an acquihire or fire sale.
- **Missing IPO check.** Run an explicit check for whether the company has IPO'd since the fund summary was written. If it has, stop and tell the user to use `stock-investment-analysis` against the public ticker.
- **Citation drift.** Every `[^N]` reference in the body must have a matching `[^N]: ...` definition, and every definition must be referenced at least once. No gaps in numbering.
- **Bare URLs.** All URLs use markdown link syntax `[descriptive text](url)` — body and footnotes alike. Verify before delivering.

(The original Marketing-as-fact bullet ended with a sentence nominating that
behaviour as "the single most likely failure mode of this skill". It was dropped
outright rather than relocated: Verification item 2 already enforces the same
rule positively, so the superlative added no enforcement and only raised the
salience of the mistake.)

## Requirements

`metadata.hermes.requires_toolsets: [web, file]` — the skill is wholly dependent
on web research and on writing the report file. Without those toolsets in the
active profile it would activate and then fail with nothing to show for it.
