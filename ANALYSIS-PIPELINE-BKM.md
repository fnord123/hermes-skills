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

**A worker that exits 0 without a terminal `kanban_complete`/`kanban_block` is a protocol
violation** and counts as failed regardless of what it accomplished. Say so in the card body.

---

## 2. Card sizing

**Measure the work; never size from a domain lookup table.** A table cannot know that one PMC
article is an abstract stub and the next is 40 pages, and it silently mis-sizes every host
nobody has added to it.

**Size from the median and the p90, not the worst case and not the failures.** One engine
version took 10 min/item from *the single card that had failed* — sizing a fleet from its
slowest member.

**Set the runtime cap ABOVE the design target, not at it.** A cap set exactly at the expected
duration turns ordinary variance into a timeout; two cards burned four attempts that way.
Target 20 minutes, cap at 30.

**Items go to a file; the card names the file.** A card that inlines its work list *"is one
incurious worker away from silently auditing 2 of 25 citations and reporting done."*

**One document per worker.** Beyond that Hermes compacts, and a compacted context produces
verdicts that look identical to real ones.

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

**Judge the enclosing section, not a character window.** A ±200-char window cannot tell you the
sentence sits under *"6.1 Adverse Reactions in Atopic Dermatitis"* while the claim is about a
different indication — and the heading is exactly what catches scope errors. It is also what
makes re-auditing affordable: ~630 tokens of section versus ~52,000 for the document.

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

**Record the answer, then complete.** See §1 — unblocking re-asks.

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
middle grades are invisible to the gate that decides what ships.

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
