# Analysis pipelines — best known methods

A multi-card Hermes pipeline that gathers evidence, judges it, and writes a conclusion is a
distinctive kind of program: most of its work is done by models in separate contexts that
cannot see each other, coordinated through a kanban board, against sources that fail in ways
HTTP does not describe. Almost none of what follows is guessable. Every rule here was paid for
by a run that failed, and the failures repeat across domains because they come from the shape
of the pipeline, not from medicine or municipal bonds.

**Read this before writing a new pipeline, and after any run that surprised you.**

Sources: the `rx-review` pipeline (`~/.hermes/rx-review/` — `rx.py`, `fanout.py`, `verify.py`,
`rxfetch.py`), and the retired `analysis-engine` extraction (in git history, `c6bfd7a^`).

> **Why a document and not a library.** A shared engine was extracted and abandoned. Two
> reasons. It could not run until every domain-neutral abstraction existed, so it sat at one
> phase of six while the working pipeline accumulated fixes it did not have. And the copy went
> stale in the direction that matters: three defects fixed in `rx-review` in a single week were
> already re-created in the engine, including one where `pipeline.py` stated *"Guards fail
> closed"* in its own docstring while failing open in three places. A document cannot silently
> diverge from an implementation it never claimed to be.

---

## 0. The meta-lesson: hardening is lost by rewriting, not by deleting

This has now happened three times in one pipeline. It is the single most expensive pattern
here, and it is invisible in code review because **the new module always looks reasonable.**

| When | What |
|---|---|
| 11:17 | Throttling + interstitial detection hardened in `citations.py` after NCBI rate-limited 41 URLs |
| 15:04 *(same day)* | `verify.py` written fresh — 591 new lines, a bare parallel `urllib` GET — and the pipeline pointed at it |
| later | An entire citation audit judged claims against *"Checking your browser before accessing pubmed.ncbi.nlm.nih.gov"* |

Nothing was deleted. `citations.py` still contains the fix today. The pipeline simply moved to
a module that never inherited it. The same thing then happened to `announce()` (copied without
the helper it calls, `NameError`, a completed run reported as failed), and again in
`analysis-engine` (three fixes pre-broken before it ever ran).

**Rules that follow from this:**

- **When you replace a module, diff its defences, not its features.** Grep the old one for
  `sleep`, `retry`, `lock`, `attempt`, `cap`, `guard`, `dedupe`, and for comments containing
  "which is how", "precisely how", "that is what happened". Each one is a bug someone already
  paid for.
- **Write the incident into the code, not the commit message.** Every defence in `rx-review`
  carries the failure that motivated it in a comment. That is why this document could be
  written at all, and why the losses above were recoverable. A defence whose reason is only in
  a commit message is a defence the next author will tidy away.
- **A second copy is the failure mode, not the drift.** Vendoring with a CI drift check makes
  divergence *visible after the fact*; it does not stop the new module from being written
  without the old one's scars.

---

## 1. Kanban mechanics (Hermes-specific, mostly undocumented)

These are properties of Hermes' kanban, verified against the source. Several contradict the
published docs.

**`blocked` means "waiting on a human", always.** Only `dependency` blocks auto-resume. A
worker that hits a network outage and calls `kanban_block` produces a card that will sit there
forever. `transient` is *"treated like a generic block for routing"* — the kind is advisory.

**There is no timer. `scheduled_at` does not exist.** The docs describe deferred dispatch with
`hermes kanban schedule <id> --at <ISO8601>`. Across all of upstream `main`, `scheduled_at`
appears in exactly one file: the documentation. It was implemented (#24429), recorded as merged
(#28384), and **the diff was dropped in the rebase** — the re-land PR (#45504) is still open and
conflicting. `scheduled` is an inert parking status; `kanban_db.py` says so plainly: *"scheduled
tasks are intentionally not dispatchable; an external cron, human action, or automation can
later call unblock_task."* **Design for no timer.**

**`BLOCK_RECURRENCE_LIMIT` (default 2) routes to `triage`.** Unblock a card twice and let it
re-block for the same reason and it leaves the graph entirely. Any automation that unblocks
must **health-gate first** — verify the underlying condition actually cleared. Blind retries do
not just fail, they permanently poison the card.

**Never resolve a human gate by unblocking it.** Unblock *re-runs* the card, so a card whose
job is to ask for confirmation asks again, blocks again, trips the loop detector, and lands in
triage where it satisfies nothing. **Write the answer down and `complete` the card** — completion
is what the dependency graph waits on.

**Block reasons are truncated to 160 characters** by `gateway/kanban_watchers.py` before the
Discord adapter (which chunks correctly at 2000) ever sees them. Anything longer is lost
mid-word. Put a fitted summary in the reason; put the detail in the card body or a file.

**Create barriers with the graph, never splice them in later.** Linking a new parent onto a
card that has already started does nothing — kanban does not un-start a running card. That is
how a reconciler once ran three hours ahead of its evidence, and how a brief was assembled from
an audit still in progress. Corollary: when you *do* splice, splice only into cards that have
not started, and check.

**An `archived` parent counts as satisfied, exactly like `done`.** A merge card that kept round
1's parents became ready when those were archived and merged 88 of 303 verdicts mid-run.

**The idempotency key is usually derived from the title, so titles must carry the round.**
Re-planning with the same title returns the *existing* card and **silently discards the new
`--parent` arguments**. Suffix round-dependent titles (`... (round 2)`), and keep the reason
next to the suffix or someone will tidy it away.

**Set `--workspace dir:<reports>`.** The default scratch workspace is deleted on completion and
no consumer looks there; one run lost ~120KB of finished work that way.

**Card bodies are capped at 8KB** (`_CTX_MAX_BODY_BYTES`) and `build_worker_context()` appends a
truncation marker rather than failing — so a 36KB body silently delivers its first two items.
**Refuse to create an oversized card** rather than let it be clipped. Enforce this in your
`create()` so it guards every card, not just the ones you remembered.

**Subscribe the cards that block for a human**, and pin `--notifier-profile`: the notifier skips
any subscription whose owner has no running gateway, which silently dropped every one of them.
Per-*card* notifications turn a run into narration of its own bookkeeping — subscribe gates and
phase boundaries, not everything.

**Know which card bodies are `.format()`ed and which are not.** A template passed straight to
`create()` reaches the model verbatim, so `{reports}/` stays a literal placeholder and the
doubled braces you wrote to escape a JSON example (`{{"lenses": 4}}`) arrive doubled. Both
render as instructions the model cannot follow. Render one body and read it before shipping.

**A worker that exits 0 without a terminal `kanban_complete`/`kanban_block` is a protocol
violation** and counts as failed regardless of what it accomplished. Say so in the card body.

---

## 2. Fitting the work into the context you actually have

Compaction is the quiet killer. A card handed more than its window does not fail — it
summarises, and then answers confidently from the summary. **Its verdicts look exactly like
real ones**, so nothing downstream can tell. This is why the whole pipeline is shaped around
never letting a worker see more than it can hold.

### 2a. One source that is too large

Never hand a worker a document. Hand it the smallest span that can answer the question, in this
cascade:

1. **Locate first, then extract the ENCLOSING SECTION.** For the quote in a 90-page drug label,
   the enclosing section is ~630 tokens against ~52,000 for the document. This is what makes a
   full re-audit affordable at all — and the heading is what catches scope errors, so it is more
   accurate *and* smaller.
2. **If the section itself is oversized, centre a window on the match** (`MAX_SECTION_CHARS`,
   5,000 in rx-review). Some documents have one enormous "Adverse Reactions" section, and
   *"one fat item drags a whole card past its budget."*
3. **If the document has no heading structure, fall back to a plain window** centred on the match
   (`CONTEXT_IF_NO_SECTION`, 3,000).
4. **Mark every truncation in the text itself** — rx-review prepends
   `[section truncated around the quote]`. A judge that cannot tell it is looking at a window
   will treat absence of context as absence of support.

Structure is worth protecting upstream of all this: convert block-level tags to newlines
**before** stripping tags, and re-extract PDFs with PyMuPDF rather than reusing flattened
markdown. Firecrawl markdown arrives with zero newlines and zero headings, which destroys
section detection and silently forces every item down to step 3. PyMuPDF keeps 5,915 line
breaks and 166 numbered sections on the same file.

### 2b. Many sources packed into cards

**Size to the window your WORKERS will get, and ask the layer they actually go through.**
The tempting move is to probe the inference backend for its real `n_ctx` — "a declared
context_length is a claim, not a measurement." That reasoning is wrong twice over here. The
operative number is what the client is *configured to send*: cards are executed by agent
workers through a proxy, and the agent will not send more than its own configured window
whatever the backend loaded. And pinning a backend address means the pipeline inherits every
migration — this one hardcoded `192.168.1.4:10400`, serving moved to another host, every probe
failed, and the pipeline quietly planned against a quarter of the real window with one warning
line as the only trace. If your infrastructure says *"clients never talk to a GPU host
directly; the proxy is the only front door"*, that applies to your pipeline too. Read the
configured window; keep a conservative floor for when even that is unreadable.

**Measure real sizes by fetching, once, per unique source.** A domain lookup table cannot know
that one PMC article is an abstract stub and the next is 40 pages, and it silently mis-sizes
every host nobody has added to it. Cache the measurement.

**`n_ctx` is in TOKENS; your corpus is in BYTES. Convert, and say which unit you are in.**
Conflating them is not a rounding error — it silently sized every chunk at ~2.5% of the window
and turned 26 reports into 104 cards. Multiply by ~4 chars/token, then take a fraction of
*that*. Print the arithmetic (`64000 tokens (~256000 chars) -> budget 64000 chars (25% of
window)`) so the mistake is visible in the log rather than only in the card count.

**Leave real headroom — a quarter of the window, not all of it.** The failure being prevented is
not "did not fit" but *"fit, and compacted anyway"*. The model needs room to reason over the
text, not merely room to hold it.

**Bound each card by BOTH a character budget and an item count, whichever binds first.**
rx-review uses `CARD_BUDGET_CHARS = 36_000` (~9k tokens of sections) and
`MAX_CITATIONS_PER_CARD = 10`. The character budget stops one fat item from blowing the card;
the item cap stops thirty tiny ones from blowing the wall clock.

**When something will not fit, flag it — never pack it silently.** rx-review's chunker records
`oversized: true` on the chunk rather than pretending. Silent truncation reads as "covered
everything" when it did not, which is indistinguishable from success at every later stage.

**Items go to a file; the card names the file.** A card that inlines its work list *"is one
incurious worker away from silently auditing 2 of 25 citations and reporting done."*

### 2c. Sizing the runtime

**Size from the median and the p90 — not the worst case, and never from the failures.** One
version took 10 min/item from *the single card that had failed*: sizing a fleet from its slowest
member.

**Set the runtime cap ABOVE the design target, not at it.** A cap equal to the expected duration
turns ordinary variance into a timeout; two cards burned four attempts that way. Target 20
minutes, cap at 30.

---

## 3. Fetching external sources

The richest source of silent failure in the whole pipeline. Sources do not fail like HTTP.

**A throttled failure is not a small document.** NCBI answers rate limiting with **HTTP 200** and
a ~133-character interstitial. `urlopen` raises nothing, so a sizer recorded it as a successful
measurement of a tiny page — 41 of 147 URLs — then packed 15 full-text articles into one card
believing they totalled 2KB. That card timed out twice, tripped the circuit breaker, and
stalled everything behind it. **The bug was that the failure was indistinguishable from
success.**

**Detect interstitials by phrase, at any length — not by size.** PubMed's JavaScript shell is
6–11KB of clipboard/search-history chrome carrying no abstract at all, so every length-gated
test passes it. This mistake has been made three times, including once in a fix written
*specifically for this problem* that then reported "8/8 USABLE" while returning zero abstracts.
**Read the text your fetcher returned before believing a size.**

**Never cache an unusable response.** Writing one interstitial makes it permanent: the read path
trusts any non-empty cache file, so every later retry replays it. A sweep reported *"retrying is
not helping"* for nine rounds against 41 cached bot walls. Leaving no file costs one re-fetch;
leaving a wall costs the citation.

**Rate-limit per host, across processes.** A `threading.Lock` is not enough — `build`, `sweep`
and the sizer run as separate processes, and a rate limit is enforced against the *client*.
`fcntl.flock` on a per-host file, and **write the timestamp even when the request raises**: a 429
consumed the quota exactly as a 200 did.

**Use the site's API when it has one.** PubMed and PMC HTML never yield an article to a
non-browser; `eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi` returns the real text. 52 of 56
sources recovered this way, averaging 12.5KB against 138-byte walls.

**Return WHY it failed, not `""`.** A bot wall, a 429 and a read timeout all reach the caller as
"no text", and a caller that cannot tell them apart writes *"the source does not support this
claim"* when the truth is *"we were throttled."* Downstream that becomes a finding. Distinguish
at minimum: **`ok` / `unreadable` (reached it, got chrome) / `unreachable` (never got a
response)**.

**Match cached sources by document identifier, never by host.** Matching `pmc.ncbi.nlm.nih.gov`
on host alone returned the largest cached article for every citation and checked 53 of 69
against the wrong paper. **Returning nothing beats returning the wrong source.**

---

## 4. Verdicts and evidence

**Separate "the source does not support this" from "we could not read the source."** These are
different findings and only one is about the evidence. Measured over a full run, the split was
**50 evidence findings and 78 unverified** out of 128 apparent failures — reported flat, that
read as 128 refutations. *Conflating them is how a bot wall becomes "the literature contradicts
this" in a document written for a professional.*

**Derive the distinction mechanically, not from the reason prose.** Cross the verdict with the
locator's match type. Verdicts like `misquoted`, `scope-mismatch`, `overstated` and
`context-reversed` are only reachable with the text in hand — measured over a full run they
never once appeared on an unreachable source. `absent` and `unsupported` carry no such
guarantee and must defer to whether the source was actually read.

**When the join is ambiguous, default to "could not verify."** Overstating your reach is the
more dangerous error: a false "the literature contradicts this" is worse than a false "unchecked".

**No verdict is not a pass.** Absence of a check is not evidence that a source checks out.
Say so explicitly wherever such a claim appears, so a reader can tell an unverified claim from
a verified one.

**Verdicts are sticky — decide deliberately whether that is what you want.** A sweep that
schedules only citations with *no* verdict will never revisit one judged `absent` against a bot
wall. A two-second network blip therefore permanently demotes a claim, **and the pipeline
reports itself clean.** Either make verdicts reached without text provisional, or accept that
transient failures are final.

**A judged-but-unfinished card looks exactly like a finished one on the board.** Sweep for items
with no verdict, loop until dry, and guard on no-progress so a genuinely unjudgeable item cannot
spin forever.

---

## 5. Fan-out topology

**Any card that must read "everything" is a bug.** One card handed 759KB of reports plus 162
URLs compacted three times, *"which makes its verdicts worthless."* Split by natural unit —
per source, per section, per claim — sized to the context the server actually serves.

**This applies to adversarial lenses too, not just the obvious stages.** `rx-review` learned it
for the citation audit and never carried it to the four lens cards sitting beside it, each of
which is still handed the entire corpus. **When you fix a scoping bug, grep for every other card
with the same shape.**

**Feed each critic only its slice, plus that slice's prior findings.** Section-scoped critique
with the section's failed citations injected beats a corpus-scoped critic that compacts.

**Judge the enclosing section, not a character window** (§2a). A ±200-char window cannot tell
you the sentence sits under *"6.1 Adverse Reactions in Atopic Dermatitis"* while the claim is
about a different indication — and the heading is exactly what catches scope errors.

**Filter junk headings.** Site chrome ("JOIN NOW", "PERMALINK") matches an ALL-CAPS heading
pattern perfectly, and a junk heading is worse than none because it actively misleads about
scope.

---

## 6. Human gates

**A gate is a card the pipeline waits on, created with the graph.** Never a prompt, never a
file someone has to notice.

**Verify before asking.** Ask the human to confirm something the pipeline has already checked
mechanically — showing 258 markers verified against their source PDFs makes "do these look
right?" answerable. An unverified question wastes the only human in the loop.

**Ask answerable questions.** *"Confirm the labs"* is not answerable; *"258 markers, 16 out of
range, here they are"* is.

**An answer given in chat is not a state change.** This is the most persistent bug in the whole
category: the pipeline asks, the human answers, everyone believes the gate is settled, and the
card sits blocked forever. Nobody notices, because the human has no reason to look at the board
again and the pipeline has no way to report that it is still waiting. Two halves are needed:

- **Give the agent an exact command to run**, in the card body and in the skill, with the answer
  as an argument (`rx.py labs-confirm`, `rx.py regimen-confirm --item ... --answer ...`).
  A model that has to invent the state transition will summarise the conversation instead and
  move on. Do not describe the outcome — name the command.
- **The command writes the answer down where the card can see it, and COMPLETES the card.**
  Completion is what the dependency graph waits on.

**Never resolve a gate by unblocking it.** Unblock *re-runs* the card, and the card's whole job
is to ask — so it asks again, blocks again, trips `block_loop_detected` (limit 2), and lands in
triage *"where it satisfies nothing and the research stage waits forever. That is exactly what
happened at 13:30."*

**Make the gate answerable from wherever the human actually is.** If they read the question in
Discord, answering in Discord has to clear it. A gate that can only be cleared from a terminal
is a gate that stays shut.

**Guards fail closed, and say what is outstanding.** Refusing to proceed is correct; refusing
without naming what is missing sends the human to read the code.

---

## 7. Adversarial review

**Separate the lenses.** Run independently so they cannot converge, and give each a distinct
question:

| Lens | Question |
|---|---|
| `logic` | Does the conclusion follow from the premises actually stated? |
| `counter` / refute | Is there stronger evidence this ignored or contradicts? |
| `overreach` | Is the claim stronger than the support carries? |
| `evidence` | Do the citations survive the audit? (fed from the audit, not re-derived) |
| `null hypothesis` | Steelman changing **nothing** |

**The null-hypothesis lens is not optional.** *"This pipeline is biased toward manufacturing
action items. You exist to push back."* Any fan-out that researches candidate changes will
produce candidate changes; something must argue for the status quo, or the bias is unopposed.

**Attack the support for each argument separately and with equal rigour** — not one argument
against another. A bull case and a bear case are both standard sections and both legitimately
supported; setting them against each other produces rhetoric, not a check.

**Use ONE severity scale across every lens, and make the survival rule consume all of it.**
Divergent vocabularies (`fatal`/`qualifying` in one lens, `fatal`/`minor` in another) mean the
middle grades are invisible to the gate that decides what ships. A workable scale:

| | |
|---|---|
| `fatal` | the claim cannot stand |
| `serious` | the claim must be weakened before it is used |
| `minor` | imprecision worth fixing; carried as a correction, not a reason to drop |
| `clean` | challenged under this lens and held |

**`clean` is a result, not padding — and should be common.** State that in the card body. A
critic that believes silence looks lazy will manufacture findings, which is the failure the
lens exists to catch in others.

**Reporting weakness is a success; manufacturing a finding is a failure.** Say this in the card
body, or a critic with nothing to report will invent something.

**The critic must not author what it judges.** Keep generation and judgement in different cards.

---

## 8. Failing closed

State it as a principle *and* check it, because it is easy to violate while believing you
haven't — `analysis-engine` asserted *"Guards fail closed. An unreadable input is an error,
never 'nothing found'"* in its docstring while failing open in three separate places.

- An unreadable input is an error, never "nothing found".
- Silence is never success. If a phase can produce zero output, distinguish "found nothing"
  from "did not run".
- When you cannot tell whether something applies, **keep it and flag it**. Dropping a real
  finding is the worst outcome available to a filter.
- A pipeline that fails quietly is worse than one that fails loudly.

---

## 9. Anti-patterns

- **Duplicating a helper across scripts.** Four implementations of "which markers are out of
  range" returned four different answers, and the one wired to the gate was the wrong one.
  *"Two answers to 'what is abnormal' means at least one is wrong, and the user is the one who
  has to notice."*
- **Two constants with one name.** `MAX_SECTION_CHARS` was 5,000 in one module and 12,000 in
  another.
- **Re-deriving a classification the upstream stage already made** by hand-parsing its output
  file. Consume the structure; do not re-infer it from prose.
- **Letting a cosmetic step fail the run.** An unguarded Discord announcement raised, the script
  exited 1, and a card blocked as though its work had failed — after all 22 of its cards had
  been created successfully. *Never fail a run over a message about the run.*
- **Copying inputs without content-hash dedupe.** Copying is not idempotent when each copy gets
  a fresh random prefix: one retry landed the whole set twice, five documents became ten, and
  the confirmed summary was drawn from doubled data.
- **Making yourself the recovery mechanism.** If the answer to "what happens when this fails at
  3am" is "I notice and re-run it", the pipeline is not finished.
