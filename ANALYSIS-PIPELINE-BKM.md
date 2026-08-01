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

**A worked example of everything below is `~/.hermes/rx-review/ARCHITECTURE.md`** — the same
ideas as concrete cards: what each of its 21 card types does, how they chain, where the human
gates sit, and which knobs were tuned to what. Read it when a rule here is too abstract to act
on. This document says *why* the shape is like that; that one says *what* the shape is.

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

**The per-profile config is the real config; the global one is decoration.**
`~/.hermes/config.yaml` and `~/.hermes/.env` do **not** reach `~/.hermes/profiles/<p>/`, and a
dispatched worker reads the profile. This cost a full day twice over in one session: research
cards died all afternoon against a context ceiling that had been raised globally hours earlier,
and later the entire research stage failed on a search backend that had been switched away from
weeks before — the global file said `searxng`, all ten profiles said `tavily`, and none of them
carried the endpoint variables. **Any claim of the form "we already changed that" is a claim
about `profiles/*/`, and must be checked there.** Verify what a worker actually resolves rather
than reading the global file:

```bash
HERMES_HOME=~/.hermes/profiles/<p> ~/.hermes/hermes-agent/venv/bin/python -c \
  "import os,sys; sys.path.insert(0,'/home/you/.hermes/hermes-agent'); \
   from hermes_cli.env_loader import load_hermes_dotenv; load_hermes_dotenv(); \
   print(os.environ.get('SEARXNG_URL'))"
```

Two corollaries. A search-and-replace across profiles silently skips any profile whose block
lacks the key entirely — **check the keys exist, not just their values**. And `profiles/*` is
gitignored, so every fix applied there is invisible to version control and dies on a profile
rebuild; if it matters, it needs a provisioning script, not an edit.

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

### 2c. Measuring how close a card actually came

**The agent's own token estimate is not evidence.** Hermes reported peaks of ~104k and ~96k
tokens for cards whose real prompts, per the provider, were 146k–153k — roughly 40% low, and
low in the direction that hides the problem. Sizing decisions taken from those logs put cards
at "58% of the window" when they were at 85%.

**Worker logs print token counts only inside WARNINGS**, so the only cards you can measure from
logs are the ones that already went wrong. Nothing durable records how close a *successful*
card came to the wall, which is exactly the number you need to size the next fan-out.

**Tag every request with the card id at the proxy.** One header makes per-card context exact,
from the provider's own accounting rather than the agent's estimate:

```yaml
model:
  default_headers:
    x-litellm-tags: "rxcard=${HERMES_KANBAN_TASK}"
```

Hermes expands `${VAR}` from the worker environment, where the dispatcher has already set the
task id; litellm stores it in `LiteLLM_SpendLogs.request_tags`. Without it, attribution by
timestamp collapses under concurrency — with six workers running, 75 of 77 runs overlapped
another and four different cards were each credited with the same 149,550-token peak.
Compactions can then be inferred from sharp drops in `prompt_tokens` within a card's sequence,
since context only falls when history is discarded. **Do this before you need it: nothing
recovers attribution for runs already finished.**

### 2d. Compaction fails because of your own concurrency

The cards that died were not merely oversized. They crossed the threshold, tried to compact,
and **compaction — itself a large model call — was rate-limited or timed out** because six
workers were saturating one backend. Twelve compaction attempts on one card, six 429s and five
timeouts, until the runtime cap killed it.

**The recovery mechanism competes for the resource that is already exhausted**, so it cannot
self-heal, and the failure presents as a timeout rather than as a context error. Size
`max_in_progress` against what the backend can actually serve, not against the card count, and
give the compression call a timeout appropriate to summarising ~100k tokens on a busy server —
not the default two minutes.

### 2e. Measure the document before designing the splitter

Every assumption worth making about a source is checkable in a minute, and the real one broke
three of them at once. On a 29-page lab panel: the text extracts **one cell per line** (marker
name, value and reference range arrive as three separate lines, so "count the rows" is
ill-defined); it is **two different reports bound into one file**, with the seam visible only in
the page footers; and the **largest font on all 16 pages of the first report is the patient's
name**, so heading detection by font size finds exactly nothing.

**Take cut points from the document's own identity strings** — `Page N of M`, an appendix
banner, a change in column geometry — never from styling. And note the corollary for any
frequency-based cleanup: page footers vary per page (`PAGE 1 OF 13`), so they escape exact-line
matching entirely; normalise digits before counting, and compute per **segment**, because a
footer on 13 of 29 pages is 45% of the file and 100% of its own report.

### 2f. Sizing the runtime

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

**If you do make them provisional, gate revival on NEW EVIDENCE and cap it.** Re-judging
anything that merely *looks* unresolved loops forever on a genuinely unreachable source, and
re-blocking twice for the same reason escalates the card out of the graph entirely (§1). Two
constraints make it safe: revive only when the underlying condition has actually changed — the
source now returns usable text — and revive each item at most once, tracked in persisted state,
so an item whose source reads fine but whose quote is truly absent settles after one retry
instead of oscillating.

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
with the same shape.** The same applies to *conventions*, not just bugs: when a severity scale
was unified across four lenses, the fifth consumer — a final review card in a different file —
kept the old two-grade vocabulary and went on reporting in a language the survival rule no
longer read. **Unifying a convention means enumerating every consumer of it, including the ones
in other modules.**

**Every card in a fan-out writes its OWN output file.** N cards working the same lens and
appending to one shared file is a write race, and the interleaved result is not obviously
corrupt — it just quietly loses lines. Give each card `<stage>-part-NN.md` and merge them
afterwards. This is easy to get wrong even when the codebase already does it correctly
elsewhere: a fan-out rebuilt from scratch beside a working one reintroduced the race, because
the per-part convention lived in the old code and not in anyone's head.

**Feed each critic only its slice, plus that slice's prior findings.** Section-scoped critique
with the section's failed citations injected beats a corpus-scoped critic that compacts.

**Judge the enclosing section, not a character window** (§2a). A ±200-char window cannot tell
you the sentence sits under *"6.1 Adverse Reactions in Atopic Dermatitis"* while the claim is
about a different indication — and the heading is exactly what catches scope errors.

**Filter junk headings.** Site chrome ("JOIN NOW", "PERMALINK") matches an ALL-CAPS heading
pattern perfectly, and a junk heading is worse than none because it actively misleads about
scope.

**Shard on boundaries the artifact already has.** A research card asking four numbered
questions is already sharded — by whoever numbered them. Those boundaries are grouped by
meaning, and the wording of each was usually written against a specific past failure. Inventing
new ones (a discovery phase, a per-source fan-out) is more machinery for a worse split, and
rewording while resharding quietly discards the history each line was carrying. **Move the
questions; do not paraphrase them.** Where one question genuinely reasons over the others'
answers, that one is the synthesis card, gated on the rest.

**Overlap the shards so the split is checkable.** Give adjacent shards one shared unit — a page,
a section — transcribed twice by workers that never see each other, then compare. Agreement is
real evidence the split lost nothing, and it is far stronger than counting rows, which is
ill-defined the moment a layout puts one field per line. Two traps, both hit live:

- **Compare on identity plus unit, never on the name alone.** A comprehensive panel measures
  glucose in blood *and* in urine; keyed on name, two correct transcriptions look like one
  reading with two contradictory values. Do not put the reference range in the key either —
  two workers write the same range differently (`< or = 2` and `< or = 2 IU/mL`) and both are
  right.
- **An empty overlap is suspicion, not proof.** The shared unit may hold only narrative. Report
  it; never block on it. A false block on a heuristic is how a check stops being trusted — this
  one halted a live pipeline within an hour of shipping.

**When you add a file type to a shared directory, enumerate every glob over that directory.**
Sharded research fragments were written as `marker-x-part1.md` into the reports directory, which
four later stages read as "the research reports" — the adversarial lenses, the citation audit,
the interactions card and the status count. The corpus would have quadrupled, and deliberately
*partial* fragments would have been judged for gaps and overreach, producing findings that look
real but are artifacts of the split. The convention to follow already existed in the same file
(intermediates carry a `LENS-` prefix and every consumer skips it); the new writer used a
suffix and nothing skipped it. **A dry run and a green test suite both passed this** — only
reading the consumers found it.

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

**Never put a partial set to a human.** Inputs arrive in rounds, so a stage that plans over
"everything it can see" runs several times, and an early merge card completes over the subset
that existed then — advancing the pipeline and posting the gate. We asked for confirmation of
600 markers from 20 PDFs while two were still being transcribed. **A confirmation is the one
step that cannot be retracted:** "these match my results" does not become true for the files
nobody saw. Do not reason about which planning round fired; ask the inputs directory whether
every staged item has landed, and stay silent until it has.

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

**A gather must assert the count the fan-out planned.** Globbing what exists cannot distinguish
"part 7 has not finished" from "part 7 finished and wrote nothing" — both merges here errored
only at *zero* parts and otherwise published `N findings across 11 parts` when twelve were
planned, which reads as complete at every later stage. The number is known at fan-out time and
was only ever printed to stdout, where nothing consumed it. Write a manifest of expected part
files before the cards run, and refuse when any are missing. Store it as `.json` so no `*.md`
glob mistakes it for a report, accumulate it across rounds, and treat "no manifest" as "do not
refuse" so the upgrade is not a flag day. This also catches the archived-parent race for free.

**Never silently drop a record you cannot parse — count it and refuse.** Both merges discarded
any line whose first field was not in their vocabulary, in silence. The prompt says "append ONE
line per finding" and a model bullets by reflex, so `- fatal | …` lost the finding entirely
while counts, totals and body all agreed with each other and were all wrong. A dropped `fatal`
is an unsound claim reaching the final brief with nothing marking it.

**If two code paths decide "is this record valid", they must share the predicate.** The audit's
sweep accepted any line with three fields, a filename and a number; its merge required the
verdict vocabulary. So `context reversed` — a space instead of a hyphen — counted as *judged*
by the sweep, which reported `SWEEP: CLEAN`, and was *discarded* by the merge. The citation was
announced as fully audited and appeared nowhere in the audit. Two validity tests for one record
type is the same defect as two implementations of one question (§9), and it fails the same way:
silently, in the direction of false completeness.

---

## 9. Anti-patterns

- **Duplicating a helper across scripts.** Four implementations of "which markers are out of
  range" returned four different answers, and the one wired to the gate was the wrong one.
  *"Two answers to 'what is abnormal' means at least one is wrong, and the user is the one who
  has to notice."*
- **Consolidating a helper whose output feeds an IDENTIFIER.** Merging two near-identical slug
  functions is obviously right — until you notice one truncated at 48 characters and the other
  at 60, and that the output becomes the idempotency key. A different key is a different card:
  a re-plan stops matching the existing graph and silently builds a second one. The same
  applies to anything deriving a filename, a cache path or a dedupe key. Before merging, ask
  what consumes the output, and diff the output over real inputs rather than reading the two
  implementations.

  **Then keep going, because the divergence is usually hiding a bug rather than causing one.**
  Those three slug lengths were all arbitrary — the key column was unbounded text, so
  truncating bought nothing and risked collision, and a colliding idempotency key does not
  error: `create` returns the EXISTING card and discards the new parent arguments, wiring the
  graph to the wrong node in silence. Three copies had disagreed about a limit that should
  never have existed, and each looked defensible on its own. **Consolidation's real value is
  not that the copies stop drifting; it is that putting them side by side forces the question
  none of them had to answer alone.**
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

  **Content-hash dedupe is only half of it — you must also decide which copy SURVIVES.**
  Keeping "the first in sorted order" is a coin flip when the filenames carry random prefixes.
  Re-uploading 21 already-transcribed documents, 12 of them drew a lower prefix than their
  original, displaced it as canonical, and were reported as still needing work; every one of
  those then entered the merged output twice. The rule that holds: **once content has been
  processed, its identity is frozen** — prefer the copy that already has output, then the
  oldest, never the one that happens to sort first. And quarantine the losers (move, do not
  delete) rather than leaving them beside the survivor for the next stage to rediscover.
- **A membership test keyed on the entity instead of the row.** "Is this *marker* out of range"
  rather than "is this *reading* out of range" meant one abnormal cholesterol in December
  flagged every later cholesterol as well — so a single message told the user a value was
  flagged out of range *and* was no longer out of range, and 10 of 14 findings on the newest
  draw were normal values. A finding is a property of a reading: key on (date, entity, value).
  Partition in a second pass, too — deciding "resolved" needs to know whether the *newest*
  reading is itself flagged, which is unknowable while still walking the rows.
- **Flagging to stdout is not flagging.** A constant was declared with a comment promising that
  an over-budget report "is split at its own headings instead"; it was referenced nowhere and
  the split was never written. Oversized reports were passed whole to cards that could not hold
  them — the exact failure the module existed to prevent — and the only trace was a print
  statement no caller consumed, while the function's own docstring asserted the opposite of what
  it did. **A guard whose only output is a log line is not a guard**, and a docstring describing
  a defence is not evidence the defence exists (§0). Grep for every constant that a comment
  says is enforced.
- **A heuristic counter that blocks.** An approximate completeness check must warn, never hold
  up the review: one that counted a marker's own per-demographic reference brackets as separate
  markers declared a correct two-row transcription "short". Advisory was the right call — the
  same check, made blocking, would have stalled the pipeline over an approximation.
- **Making yourself the recovery mechanism.** If the answer to "what happens when this fails at
  3am" is "I notice and re-run it", the pipeline is not finished.
