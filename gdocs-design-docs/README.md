# gdocs-design-docs

Doctrine skill: how an agent writes and revises Design Docs (DDs) in Google
Docs through the gdocs MCP server. There are no scripts on purpose. The
machinery is the server; this skill is the playbook. `SKILL.md` holds what the
agent does; this README holds why.

## What this is for

- The user asks the agent to draft a design document, extend one, or act on
  the review comments the owner left in one.
- The gdocs MCP server turns every agent write into a change suggestion the
  owner accepts (or rejects) in the Google Docs UI. Nothing lands silently.
  That single fact shapes every rule in `SKILL.md`.

## Wiring

Operator task: register the gdocs MCP server with the profile. Once connected,
`whoami` names the account the tools act as, and the owner shares documents
with that account. If the gdocs tools are absent, the skill cannot run and says
so rather than writing through another tool.

## The user's contract

The intake request listed the document rules the skill must cover, and
`SKILL.md` satisfies each visibly:

| Requested rule | Where it lives in SKILL.md |
|---|---|
| Comments are work orders; implement as proposed text | "The comment loop", "Proposal rules" |
| Flow: problem → options → chosen solution → overview → deep dives | "The DD flow" |
| External references as embedded links | "Writing rules" |
| Chapter references as embedded links | "Writing rules" |
| Code, paths, binaries, JSON, URLs, constants in fixed width | "Proposal rules" 5, "Writing rules" |
| Section headers organize | "Writing rules" |
| All bullet kinds where appropriate | "Writing rules" |
| Revision-history table at the end, date + change | "The DD flow" step 9 |
| Tables whenever they help | "Writing rules", Proposal rule 3 |
| Document language conforms to ASD-STE100 controlled English | "Writing rules" |

## Why these sources shaped the playbook

- [Design Docs at Google](https://www.industrialempathy.com/posts/design-docs-at-google/)
  (Malte Ubl) — the informal standard: alternatives as the section that earns
  its keep, overview-then-detail, link-don't-inline, the as-applicable menu.
- [How to write a good software design doc](https://www.freecodecamp.org/news/how-to-write-a-good-software-design-document-66fcf019569c/)
  (Angela Zhang) — named reviewer, measurable goals, concrete user stories,
  real numbers and scale, the Skeptic and Vacation tests.
- [How to Write an Effective Software Design Document](https://refactoringenglish.com/excerpts/write-an-effective-design-doc/)
  (Michael Lynch) — the opening that needs no outside context, open questions
  with next steps, "what's the penalty for being wrong?" as the keep-or-cut
  filter.
- Amazon's narrative-memo tradition is the counterweight on bullets: bullets
  for list-shaped content, full-sentence prose for anything carrying an
  argument. That is why "Write arguments in prose" sits next to "Bullets come
  in all kinds".
- **ASD-STE100 (Simplified Technical English)** is the binding language
  standard for every document this skill produces. The owner set conformance
  as a requirement: short declarative sentences, one instruction per step, no
  idioms, no ambiguity. The `asd-ste100` skill is the authoritative style
  source, and the pipeline's STE100 gate reviews output against it. The old
  "Short sentences, simple words" bullet became this binding rule, so it is
  not a separate line.

## Judgment calls recorded here

- **New skill, not an extension of `google-docs`.** `google-docs` is a
  script-driven CRUD surface whose edits apply directly. This skill is doctrine
  over a proposal server where nothing is final until a human accepts it.
  Merging the two would blur exactly one semantic, and it is the load-bearing
  one. The skills cross-point in their "When NOT to use" sections.
- **"Newest row last"** for the revision-history table is a house pick. The
  rule that matters is: pick one order and state it. `SKILL.md` states it.
- **No scripts.** There is nothing to script: the verbs are MCP tools and the
  skill teaches sequencing and document shape, not I/O.
- **DD as a trigger and a heading shorthand.** The intake request uses "DD" as
  the user's own abbreviation, so the skill defines it in the first line and
  uses it where headings need to stay short.
- **STE-100 conformance is a writing rule, not a routing change.** The
  requirement binds the language of produced documents, so it belongs in
  "Writing rules"; the description and trigger list stay untouched. The rule
  states the core constraints inline and points to `asd-ste100` for the full
  standard, so it stays executable on profiles that do not carry that skill.

## Server semantics the playbook depends on

Observed on the gdocs server; kept as current facts in `SKILL.md`:

- Google drops style suggestions whose target text is itself still pending.
  Hence the strict sequence: content, acceptance, then style.
- Tables: column widths cannot be proposed, and header bolding lands only
  after the table itself is accepted.
- `publish_image` hands back a private fetch URL that exists only to feed
  `suggest_image`. It is secret material; the no-paste rule is absolute.
- Markdown link syntax inside suggested text becomes a real embedded link.
