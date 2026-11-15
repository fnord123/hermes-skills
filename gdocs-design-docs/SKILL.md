---
name: gdocs-design-docs
description: >
  Write and revise Design Docs in Google Docs with the gdocs document tools:
  the problem, the options, the chosen solution, then the deep dives. The owner
  reviews by commenting in the document, and every edit arrives as a proposal
  that the owner accepts. The playbook enforces the document rules. Use
  embedded links for every reference, fixed-width text for code and paths,
  section headers, all bullet kinds, and tables for presentation. Put the
  revision-history table at the end. PREFER THIS SKILL when the task is
  authoring, restructuring, or
  reviewing a design document in Google Docs. For plain document reading and
  writing with no design-document shape, use `google-docs` instead. Activate
  on any of: "design doc", "design document", "write a design doc", "start a
  design doc", "system design doc", "RFC", "design review", "design review
  comments", "address the review comments", "handle the comments in the doc",
  "update the design doc", "mini design doc", "revision history", "add a
  revision row", "turn my notes into a design doc", "DD".
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [DesignDocs, GoogleDocs, Writing, Review, Documentation]
---

# Google Docs playbook: problem to decision

Write design documents (DDs) in Google Docs with the gdocs tools. A DD states a
problem, weighs the options, records the chosen solution with its trade-offs,
then explains the design in depth. The document must teach a new reader and
survive a hostile review. The document's language follows the ASD-STE100
standard. See "Writing rules".

Every write this skill makes is a proposal. The owner accepts or rejects each
proposal in Google Docs. The document never changes without the owner.

## When to use

Activate when the user wants to:

- **Write** a design document for a project or change ("write a DD for the
  ingestion rewrite").
- **Restructure or extend** an existing DD.
- **Act on review comments** the owner left inside a DD.
- **Turn notes, a spec, or a proposal** into DD shape.
- **Record a substantive change** in the revision-history table.

## When NOT to use

- **Plain document work with no DD shape.** Reading, writing, or formatting any
  Google Doc belongs to `google-docs`.
- **Slides, forms, or numeric records in another app.** Different surfaces.
- **A document the tools cannot reach.** The owner must first share it with the
  account that `whoami` names.
- **Copywriting or marketing pages.** This skill shapes engineering arguments.

## The tools

Call the gdocs tools below. Hermes may show each name with a server prefix,
like `mcp_gdocs_read_doc`. Match by the final part of the name.

| Tool | Purpose |
|---|---|
| `read_doc` | Reads the document text, one page at a time. |
| `pending_work` | Gets unread owner comments: the open work orders. |
| `list_comments` | Lists every comment thread, author, and resolve state. |
| `reply_comment` | Replies inside one comment thread. |
| `add_comment` | Adds a new comment, usually a question for the owner. |
| `suggest_insert` | Proposes new text at a position. |
| `suggest_replace` | Proposes replacing existing text. |
| `suggest_delete` | Proposes removing text. |
| `suggest_style` | Proposes formatting: headings, bold, italic, bullets, alignment. `code=true` sets fixed-width text. |
| `suggest_table` | Proposes a real table. |
| `publish_image` | Uploads an image and returns a private fetch URL. |
| `suggest_image` | Proposes inserting an image from that private URL. |
| `whoami` | Shows which account the tools act as. |

### Proposal rules

1. Every write is a proposal. The owner accepts it in Google Docs. Until then
   the document is unchanged. Report "proposed", not "done".
2. A style takes effect only after the owner accepts it. Google drops a style
   proposal whose target text is still pending. Order every styled block.
   Propose the text. Wait until the owner accepts it. Then propose the style.
3. Tables are real tables. Use `suggest_table`, never typed lines. You cannot
   propose column widths. Bold a header row only after the owner accepts the
   table.
4. The URL from `publish_image` is private and single-purpose. Pass it only as
   the `image_url` argument of `suggest_image`. Never paste it into chat, a
   comment, or the document.
5. Fixed-width text comes from `suggest_style` with `code=true`. Use it for
   code, file paths, binary names, JSON, literal URLs, and constants.
6. A markdown link inside suggested text becomes a real embedded link:
   `[the pricing chapter](the link target)`.
7. Verify before reporting. Re-read the edited region with `read_doc` after
   each editing round.

## Turning the user's words into calls

Resolve the request to calls BEFORE acting:

| User said | Call |
|---|---|
| "check the doc for comments for me" | `pending_work`, then `read_doc` on each anchored region |
| "what did reviewers say?" | `list_comments` |
| "start a DD for the ingestion rewrite" | the owner opens an empty doc; `read_doc` it; then `suggest_insert` section by section, in the order of "The DD flow" |
| "make 'Goals' a heading" | `suggest_style` with a heading level, after that text is accepted |
| "put the paths in monospace" | `suggest_style` with `code=true` on each exact span |
| "add the steps as a numbered list" | `suggest_insert` the lines now; `suggest_style` the list style after acceptance |
| "link the sharding chapter here" | `suggest_replace` the referring sentence with markdown link text |
| "add the architecture diagram" | `publish_image`, then `suggest_image` with the URL it returns |
| "record today's change" | `suggest_insert` one revision row: date first, change second |

## The DD flow

Propose the document in this order. Depth may vary. The order does not.

1. **Title block.** Short, distinctive title. Author and one named reviewer.
   Date and status.
2. **Overview.** At most three short paragraphs. A busy reader decides from
   these alone.
3. **Context and problem.** Objective background facts. Why this, why now.
   Link prior attempts instead of inlining them.
4. **Goals and non-goals.** State goals as measurable impact, never as
   implementation. A non-goal is a plausible goal you reject on purpose.
5. **Alternatives.** One entry per credible option. For each rejected option,
   name the trade-off that killed it in a few brief lines. Compare building
   against buying.
6. **The chosen solution, overview first.** Show the full shape in one short
   pass. Then explain the complex and subtle parts in detail. Record the
   trade-offs there. Walk one concrete user story through the finished system.
7. **Depth sections, as the project needs.** Pick from the menu: an API sketch
   with only the relevant surface, data storage, dependencies (name the
   hard-to-reverse ones), SLO numbers, monitoring, security, privacy, logging,
   ops impact, constraints, milestones with dates, a glossary.
8. **Open questions.** For each: the problem, the options, the next step. Keep
   resolved questions with their discussion.
9. **Revision-history table.** The last section, always. Two columns: the date,
   then the change made. Add one row per accepted substantive change. Newest
   row last.

## Writing rules

- **The document conforms to ASD-STE100.** Every design document this skill
  produces must follow the ASD-STE100 standard (Simplified Technical English).
  Write short declarative sentences. Give each step one instruction. Use no
  idioms. Leave no sentence open to two readings. The `asd-ste100` skill is
  the authoritative style source. Check every passage against it before you
  propose it.
- **Embedded links everywhere.** Every external reference (a pull request, a
  web page, a ticket) sits inside the referring text as an embedded link.
- **Chapter links too.** Reference another chapter with an embedded link. Link
  the chapter heading when the tools support an in-document target. Otherwise
  link the document URL.
- **Fixed-width for technical tokens.** Set code, paths, binary names, JSON,
  URLs, and constants with `code=true`.
- **Section headers organize everything.** No wall of text longer than one
  screen may sit under no header.
- **Bullets come in all kinds.** Plain, numbered, letter, and roman bullets
  each promise a different reading. Choose the kind the content earns.
- **Write arguments in prose.** When you reason or argue, write sentences.
  A bullet must not hide a weak step in an argument.
- **Tables where presentation gains.** Comparisons, option matrices,
  schedules, and API sketches read better as tables. Build them with
  `suggest_table`.
- **Concrete numbers and names.** "User Alice connects her bank account,
  then ..." beats an abstraction. State the scale of the problem. Scale often
  decides the solution.
- **Diagrams beat prose for structure.** Link the editable source file under
  each picture.
- **Paste code rarely.** Only genuinely novel algorithms belong in code. Link
  a working prototype instead of pasting interface definitions. "I tried it
  and it works" is the strongest argument.
- **Keep only expensive decisions.** Ask: what is the penalty for being wrong
  here? Implementation detail belongs in the ticket, not the document.
- **Length: 10 to 20 pages** for a big project. A 1 to 3 page mini document
  keeps this same shape for incremental work.
- **Run the two self-tests before sending for review.** Skeptic test: reread
  as a hostile reviewer and answer every doubt in the text. Vacation test:
  another engineer could build it from the document alone.

## The comment loop

The owner reviews inside the document. Every comment is a work order.

1. `pending_work` first, in every session. `list_comments` adds the resolved
   history.
2. Read the anchored text with `read_doc` before touching it. The owner edits
   while you work.
3. Implement each comment as proposals: `suggest_insert`, `suggest_replace`,
   `suggest_delete`, or `suggest_table`. Never answer a comment with chat when
   the fix is an edit.
4. Reply in the thread with `reply_comment`: what changed, and where. The
   owner resolves threads.
5. Leave no comment hanging. If the document cannot answer a question, ask one
   back with `add_comment`.
6. Add a revision-history row for every accepted substantive change.

## Common flows

### "Start a DD for the ingestion rewrite"

The owner creates an empty document and shares it. Work from its URL.

```
read_doc          → confirm the document is empty
suggest_insert    → title block, then each header and its text, in flow order
suggest_table     → revision-history table; first row: today, "Initial draft"
read_doc          → confirm the structure
```

Then report: "Proposed the full skeleton: title, overview, problem, goals,
alternatives, solution, open questions, revision table. Accept what you
approve, and comment on the rest."

### "Handle the review comments"

```
pending_work      → the open work orders
read_doc          → the live text around each comment
suggest_replace   → the fix for each comment, one proposal at a time
reply_comment     → in each thread: what changed, where
suggest_insert    → a revision row per substantive change
```

### "The table arrived as typed text, not a table"

```
suggest_delete    → the typed block
suggest_table     → the real table with the same rows
```

## When a step reports an error

- The gdocs tools are missing from your tool list: the document server is not
  connected for this profile. Tell the user. Do not write the document through
  other document tools. Those edit without proposals.
- `whoami` names an account the document is not shared with: ask the owner to
  share the document with that account.
- The text to edit is not where `read_doc` shows it: the document moved.
  Reread, find the anchor again, and propose from the fresh text. If the
  anchor is gone, `add_comment` a question instead of guessing.
- A style proposal took no effect: its target text was probably still
  pending. Propose the style again after the owner accepts the text.
- Any tool call fails, or the document state looks wrong: stop and report
  exactly what you saw.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Empty results

`pending_work` with no items means no unread owner comments. Say "no open
comments"; do not poll again. `list_comments` with every thread resolved means
the review discussion is complete. Report that, and add the revision row if an
accepted change still lacks one. `read_doc` on an empty document means it is
genuinely empty; start the DD flow above.
