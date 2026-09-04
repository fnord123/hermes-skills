# rx-review — architecture

_This section is under user control - do not change without explicit permission_

This is an analysis pipeline to produce a prescriber discussion brief, hereafter referred to as rx-review. It is based on `hermes-skills/ANALYSIS-PIPELINE-BKM.md`, which explains why fan-out looks like this, why guards fail closed, how to size a card, etc. The BKM document is deliberately domain-neutral and never names a card. Read that before building a new pipeline; read this before changing this one.

The rx-review pipeline reviews the **patient**. Its input is the patient's records. The records are provided by the patient as a single input document carrying the patient's identifying information and their substance regimen (the supplements and medications they take). The patient also provides a set of lab readings they have had over time.

The document — not the regimen alone — is the record of the patient. The lab readings arrive separately, as PDFs, and do not live in it. The **regimen** is one part of the document, not the whole: the document is the patient, and the regimen is what they take.

The document's regimen entries are written to `inputs/regimen.txt`. A subset of the patient's identifying information (`Name:`, `DOB:`, and `Sex:`) are materialized into `inputs/patient.md`. These two output .txt files (along with the labs, described below) are the input to the remainder of the pipeline.

The next step of the pipeline is transcription of `inputs/regimen.txt` to a machine-readable format (markdown), resolution of any ambiguities in it around dosage and ingredients, and transcription of the lab readings.

The labs are then evaluated to determine the subset of lab test values that are out-of-range. The regimen and out-of-range labs are then evaluated to determine:
* Trends that may merit medical attention.
* Which substances in the regimen may be interacting (exacerbating, improving) those trends.
* What evidence there is for the efficacy of the items in the regimen.
* For substances with a recorded start date, a before/after comparison of the lab markers each substance is known to move — computed from the user's own dated lab series, split at the start date.
All of the above is then assembled in a report. The general approach is to use adversarial reviewers and ensure every claim is supported, citation by citation.

This report is taken by the person who started it to a doctor for further review. It MAY recommend a dose, a change, or a stop where the surviving evidence supports it (a 2026-08-13 change — the earlier design recommended nothing); every recommendation, and every claim, traces to a source fetched during the run and survives adversarial review, and nothing rests on a claim the audit discredited.

Stage 1's ingestion materialises the patient input document into the files the pipeline reads from, using two scripts — one for the regimen, one for the patient.
* `rx.py regimen` creates the user's regimen in `inputs/regimen.txt` (the regimen part) — nothing else.
* `rx.py patient` takes the same input document and extracts recognised fact lines subset — `Name:`, `DOB:`, `Age:` and creates the user's medical facts in `inputs/patient.md`
The two documents (`inputs/regimen.txt`, `inputs/patient.txt` are recreated fresh on every run.

Of the patient information, the pipeline requires today: `Name:` for the record, `Sex:`, and `DOB:` — date of birth is the first fact the pipeline needs for its own computation (FIB-4 is age-weighted, and the age is computed from the DOB at read time, so it stays correct on the next birthday). Other fields potentially present in the user-provided document such as `Race:`, `Sex:`, and **Medical conditions** are ignored by the pipeline at this time.

---

## Pipeline Stages

_This section is under user control - do not change without explicit permission_

A rx-review consists of a skill used by the Hermes agent, a set of Hermes kanban cards, and a set of scripts the skill and cards use to offload as much functionality as possible away from the agent LLM and onto deterministic code.

Functionally the review runs as **eight** stages in a fixed order. Stages **1–5** run first. The Stage 6 research phase then fans out into four substages — **6a Research Substances**, **6b Research out-of-range markers**, and **6c Research marker trends** (independent, running in parallel), plus **6d Whole-regimen screens** (waits on 6a only, so it runs alongside 6b/6c). Finally **7 Adversarial review** and **8 Conclusion**. Each substage has its own Begin/Barrier.

Additional details for Stages 2-6 are provided in later sections.

### Stage 1 — Set up the board and stage the labs
- **Purpose:** create the Stage Begin and Barrier cards for every later stage, stage the uploaded lab PDFs, and establish that the input set is complete. The labs may arrive as individual PDF attachments **or** as a single `.zip` archive containing them; staging accepts either shape.
- **Starts when:** a person initiates an rx-review, providing their patient's document and uploading labs. The agent first handles the uploaded labs, calling `rx.py stage` after every round of lab uploads by the user, asking whether more labs are coming. Once the user signals they have completed uploading labs the agent records it (`rx.py uploads-done`). If that succeeds, the agent proceeds to ingest the patient document with `rx.py regimen` (writing the document's regimen entries to `inputs/regimen.txt`) and `rx.py patient` (materialising `inputs/patient.md` from the fact lines). Once these are complete, the agent calls `rx.py start`, which refuses without the ingested regimen (`NO REGIMEN`) or without the ingested patient information (`NO PATIENT`), or if a lab arrived after the user confirmed they had uploaded all labs.
- **Does:** creates the stage level Begin and Barrier cards for the following stages. The spine is a **DAG, not one chain**: the regimen branch and the labs branch both start from Stage 1 and run **in parallel**, and Stage 6 **joins** them (it waits on both the Stage 3 and Stage 5 Barriers):
  - regimen branch: `Stage 2: Read Regimen` (Begin) → `Stage 2: Regimen Read` (Barrier) → `Stage 3: Settle the Regimen` (Begin) → `Stage 3: Finalize Regimen` (Barrier)
  - labs branch (parallel): `Stage 4: Transcribe Labs` (Begin) → `Stage 4: Labs Transcribed` (Barrier) → `Stage 5: Review Labs` (Begin, also waits on the **Stage 3** Barrier) → `Stage 5: Labs Complete` (Barrier)
  - join + tail: `Stage 6: Research Begin` (Begin, parented on **both** the Stage 3 and Stage 5 Barriers) → … → `Stage 6: Research Complete` (Barrier) → `Stage 7: Adversarial Review` (Begin) → `Stage 7: Adversarial Complete` (Barrier) → `Stage 8: Conclusion` (Begin) → `Stage 8: Conclusion Complete` (Barrier).
Additional Begin, Worker, and Barrier cards needed within a stage are created within that stage, not by Stage 1.

- **Completion:** the person has said the lab set is complete and that confirmation is recorded (`rx.py uploads-done`), the PDFs have been verified as copied, and all of the above cards created.
- **Exit:** BOTH branch heads are released — `Stage 2: Read Regimen` and `Stage 4: Transcribe Labs` — and they run in parallel from here; Stage 6 is where the two branches meet again.
- **Note:** this stage is entirely mechanistic; no worker cards run within it.

### Stage 2 — Read the regimen
- **Purpose:** convert the regimen to draft markdown.
- **Starts when:** the `Stage 2: Read Regimen` Stage Begin card is scheduled.
- **Does:** invokes `rx.py intake-regimen`, which creates one `Worker: Read regimen` card with the regimen text carried inline in its body, and sets it as a parent of the `Stage 2: Regimen Read` Barrier.
- **Completion:** `regimen-draft.txt` created from the user's regimen.
- **Exit:** the `Stage 2: Regimen Read` Barrier completes, releasing `Stage 3: Settle the Regimen`.

### Stage 3 — Settle the regimen
- **Purpose:** take `regimen-draft.txt`, remove ambiguities, and produce `regimen-final.md`.
- **Starts when:** the `Stage 2: Regimen Read` Barrier completes, letting `Stage 3: Settle the Regimen` run.
- **Does:** invokes `rx.py intake-regimen-items`, which creates one `Regimen Intake: <name>` worker per product row, each a parent of the `Stage 3: Finalize Regimen` Barrier. Each worker looks up its item's label and writes `regimen-item-<slug>.md` (Name · Ingredients · Quantity · Schedule · Started · Confidence), marking confidence `low` when a lookup fails rather than asking anyone. No Worker card blocks on the user — the Barrier is the one place the pipeline waits for the whole-regimen review.

- **Completion:** the `Stage 3: Finalize Regimen` Barrier writes the **whole regimen as one numbered review** into `regimen-final.md` and blocks its own card `needs_input`; corrections are routed number-by-number through the script (`correct-item-slug-request` / `-response`, detailed under *Stage 3 in detail*), and on `approved` completes, satisfying one of the two Barriers that gate Stage 5: Review Labs, and feeding the Stage 6 join.

### Stage 4 — Transcribe the labs
- **Purpose:** transcribe all lab documents into a unified `labs-draft.md`.
- **Starts when:** Stage 1 completes — the labs branch runs IN PARALLEL with the regimen branch (Stages 2-3), not after it. `Stage 4: Transcribe Labs` is a parentless branch head.
- **Does:** invokes `rx.py intake-labs`, which creates one parentless `Lab: <file>` card per staged lab PDF, each a parent of the `Stage 4: Labs Transcribed` Barrier; `rx.py plan-lab` splits each document into overlapping windows and creates one leak-free `Transcribe Lab <file>` child per window, also parents of the Barrier; when a child has written its table, `rx.py check-transcription <token>` verifies every row against the source and completes the card — a fabricated row is retried in the same turn, not blocked. The mechanics — document-type detection and OCR, window sizing, the token manifest, the check log — are detailed under *Stage 4 in detail*.

- **Completion:** all children transcribed; the `Stage 4: Labs Transcribed` Barrier merges them into `labs-draft.md`.
- **Exit:** the `Stage 4: Labs Transcribed` Barrier completes, satisfying one of the two Barriers that gate `Stage 5: Review Labs`.

### Stage 5 — Review the markers
- **Purpose:** detect and review with the user any out-of-range markers in `labs-draft.md`, producing `labs-complete.md` and `labs-succinct.md`.
- **Starts when:** the `Stage 4: Labs Transcribed` Barrier completes **and** the `Stage 3: Finalize Regimen` Barrier has completed. This ensures the regimen is settled before the marker review is presented to the user so the user is only asked one thing at a time.
- **Does:** `Stage 5: Review Labs` invokes `rx.py review_labs`, which seeds `labs-complete.md` from `labs-draft.md` and, for each marker that is **out-of-range** _as of its most recent reading_, writes `marker-question-<slug>.md`. A marker that is only trending is not questioned — it proceeds into research normally. No per-marker card or message is created here; the `Stage 5: Labs Complete` Barrier asks the batch.
- **Completion:** the `Stage 5: Labs Complete` Barrier posts the out-of-range markers as one **numbered** review; the user keeps all ('looks good', `labs-accept`, review completed), ignores some by number (`marker-review`, user review continues), or rejects the reading outright (`labs-reject`, ends the entire review). Decisions are recorded in `labs-complete.md` and the significant markers copied into `labs-succinct.md`.
- **Exit:** the `Stage 5: Labs Complete` Barrier completes, satisfying one of the two Barriers that gate `Stage 6: Research`.

### Stage 6 — Research
- **Purpose:** an in-depth, cited research analysis of the regimen and labs — every regimen substance, every out-of-range marker, and every marker trend, plus whole-regimen screens (interactions/timing; dosing schedule when dose times are recorded).
- **Starts when:** BOTH the `Stage 3: Finalize Regimen` and `Stage 5: Labs Complete` Barriers complete — Stage 6 is the JOIN of the regimen and labs branches, so it never sees labs before the regimen is settled.
- **Does:** `Stage 6: Research Begin` creates the four research substages below — **6a** (substances), **6b** (out-of-range markers), **6c** (marker trends), and **6d** (whole-regimen screens) — each with its own Begin and Barrier; it wires 6a's Barrier as the parent of 6d's Begin, and sets the four substage Barriers as parents of the `Stage 6: Research Complete` Barrier. The 6a/6b/6c Begins are created **parentless** — the Research Begin is what creates them, so an edge back to it would only delay them; parentless, they are eligible the moment they exist and run in parallel. 6d follows 6a.
- **Backstop with teeth:** Before creating anything, analyze-research re-verifies the labs and regimen (only on the main Begin, not the substages). If a lab value cannot be verified against its source PDF, or a regimen row fails re-verification, it halts the pipeline and reports the error to chat rather than completing.

- **Completion:** every substage has written its reports to `reports/` and its Barrier has completed. A substage with nothing to research — no out-of-range markers (6b) or no trends (6c) — creates no card-sets, and its Barrier completes immediately on the empty set, so it never holds up `Stage 6: Research Complete`.
- **Exit:** the `Stage 6: Research Complete` Barrier completes, releasing `Stage 7: Adversarial Review`.

#### Stage 6a — Research the substances
- **Purpose:** research each regimen substance — evidence & efficacy, safety & marker effects, and timing — and, for every substance with a recorded start date, a before/after comparison of the lab markers the research says it moves.
- **Starts when:** `Stage 6: Research Begin` creates it — parentless, so it is eligible immediately (in parallel with 6b and 6c).
- **Does:** invokes `rx.py analyze-research` for the substance family; it creates, per substance, three Worker part-cards (evidence & efficacy · safety & marker effects · timing) that are parents of one per-substance **synthesis** Worker card that writes that substance's report — and, for every substance whose `Started` field in `regimen-final.md` is non-blank, one **Efficacy** Worker parented on that synthesis (the before/after comparison, detailed under *Stage 6 in detail*). Every substance runs in parallel; none depends on 6b or 6c. Each per-substance synthesis card — and, where one was created, its Efficacy card — is in turn a parent of the `Stage 6a: Substances Researched` Barrier.
- **Completion / Exit:** all per-substance synthesis Workers, and all Efficacy Workers where created, written and completed; the `Stage 6a: Substances Researched` Barrier completes, releasing the 6d Begin — and, like all four substage Barriers, it is a parent of `Stage 6: Research Complete`.

#### Stage 6b — Research the out-of-range markers
- **Purpose:** research each out-of-range lab marker — what it measures, what moves it, and the common benign explanations.
- **Starts when:** `Stage 6: Research Begin` creates it — parentless, so it is eligible immediately (in parallel with 6a and 6c; independent of both).
- **Does:** `rx.py analyze-research` creates one card-set per out-of-range marker consisting of **three Worker part-cards**, one question each, that are parents of a **synthesis** Worker card: (1) what the marker measures and what out-of-range *in this direction* generally indicates; (2) which regimen substances are known to move it, in which direction, and how strong that evidence is; (3) common **non-regimen** explanations (draw timing, hydration, recent exercise, fasting state, assay variation, intercurrent illness, normal biological variation). The synthesis card asks what would distinguish those explanations — what a clinician would check next (no diagnosis) — and writes the marker's report. Each marker's synthesis is a parent of the `Stage 6b: Markers Researched` Barrier.
- **Completion / Exit:** every marker synthesis written; the `Stage 6b: Markers Researched` Barrier completes. Like all four substage Barriers, it is a parent of `Stage 6: Research Complete`.

#### Stage 6c — Research the marker trends
- **Purpose:** research each marker moving consistently over time — whether the trend is meaningful, and what drives it.
- **Starts when:** `Stage 6: Research Begin` creates it — parentless, so it is eligible immediately (in parallel with 6a and 6b; independent of both).
- **Does:** `rx.py analyze-research` creates, per trending marker, a **triage** part-card and a **dispatch** gate. Two deeper parts and a synthesis are created only if the triage judges necessary. The **triage** (part 1) runs first, parentless: it quantifies the ordinary biological/analytical variation for the marker and judges whether a trend of this size over this interval clears it, writing its prose answer to its `PART-` fragment **and** a machine-readable verdict — meaningful yes/no plus the quantified reason — to a dedicated per-trend verdict file it alone writes. The **dispatch** is a deterministic gate **analyze-research** creates and parents on the triage. **The dispatch card** reads the verdict already written, and, depending on the result:
     - **ordinary** (trend was not significant) → it writes "trend within normal variation; causes and regimen-driver not researched — «the triage's reason»" as that trend's `trend-<slug>.md` and is itself the trend's terminal card. **No synthesis is created** — there is nothing to synthesize.
     - **meaningful** → it creates the two deeper part-cards — (2) common causes and which are benign, (3) whether the regimen plausibly drives it — parentless so they run in parallel, then creates the **synthesis** card parented on **[triage, (2), (3)]**. The synthesis asks at what value or rate the trend would stop being watchful waiting, and what one follow-up test or repeat interval would settle it (no recommendation), and writes `trend-<slug>.md`.
     The `Stage 6c: Trends Researched` Barrier is parented on every trend's **dispatch** up front; on the meaningful path the dispatch also splices the **synthesis** onto the Barrier. The Barrier waits for *all* its parents, so it releases on whichever card actually produced the report — the dispatch alone when ordinary, the synthesis when meaningful — and the early-completing dispatch never releases it prematurely, because on the meaningful path the still-running synthesis is also a parent. **The gate is deliberately conservative:** a trend is deepened unless the triage is confident it is within ordinary variation *and* still in range, so an uncertain call researches rather than skips; and every skip is named with its reason in the report that reaches the brief — a dull trend reported as dull, never a silent omission.
- **Completion / Exit:** for a meaningful trend its synthesis card completed; for an ordinary one its dispatch card completed, having written the skip report itself with no synthesis created. Either way the trend's terminal card is done and the `Stage 6c: Trends Researched` Barrier completes. Like all four substage Barriers, it is a parent of `Stage 6: Research Complete`. An ordinary trend finishes after two cards (triage → dispatch) and does its web work once; only a meaningful trend spawns the causes and regimen-driver parts and a synthesis, so the fan-out — and its fetch cost — scales with the trends that actually merit it.

#### Stage 6d — Whole-regimen screens
- **Purpose:** screen the whole regimen for interaction/timing conflicts, and, when dose times are recorded, review current vs evidence-based dosing schedule.
- **Starts when:** the **`Stage 6a: Substances Researched` Barrier** completes — 6d depends on the substance syntheses ONLY, not on 6b or 6c, so it runs alongside them.
- **Does:** its Begin — already gated on the `Stage 6a: Substances Researched` Barrier, so all substance research is done — creates the **interaction & timing screen** (a Worker parented on the 6d Begin) and, only when the regimen records any dose times, the **schedule review** (parented on the interaction & timing screen, whose output it consumes). Each screen is a parent of the `Stage 6d` Barrier.
- **Completion / Exit:** all screens written; the `Stage 6d: Screens Complete` Barrier completes. Like all four substage Barriers, it is a parent of `Stage 6: Research Complete`.

### Stage 7 — Adversarial review
- **Purpose:** attack every research claim and audit its citations before anything is concluded.
- **Starts when:** the `Stage 6: Research Complete` Barrier completes.
- **Does:** `Stage 7: Adversarial Review` (invoking `rx.py analyze-adversarial`) first **chunks** the Stage 6 output — packing `reports/*.md` into chunks that fit the model's context window — then fans out two independent tracks, each reading only the reports, never each other:
  - **chunk × lens** — every chunk is reviewed by each of **four lenses**, one Worker card per (chunk × lens): **Logic** (`rx-logic` → `LOGIC.md`) — does the reasoning hold (unsupported premises, correlation-as-causation, over-generalisation); **Counter-evidence** (`rx-redteam` → `REFUTATION.md`) — what newer, larger, or contradicting evidence the reports ignore; **Overreach** (`rx-logic` → `OVERREACH.md`) — whether more is claimed than the support licenses; and **Status-quo** (`rx-nullhyp` → `NULLHYP.md`) — the case for changing nothing. Every finding a lens records carries a severity on **one shared scale** — `fatal` (the claim cannot stand), `serious` (must be weakened before it is used), or `minor` (imprecision worth fixing) — and a lens that finds nothing in a chunk writes `clean`. These are the grades Stage 8's reconcile consumes (it drops any claim a lens marked `fatal`, and narrows or drops an un-narrowed `serious`). Each lens then has one **merge** Worker, parented by that lens's own chunk cards, that concatenates its per-chunk findings into that lens's single report.
  - **citation audit** — per-chunk Workers trace every citation in every report and confirm that the cited text exists and supports the report's claim that cited it. A **citation-audit merge** Worker, gated on all the per-chunk audit cards (the audit track's counterpart to each lens's merge), concatenates their results into `CONTEXT-AUDIT.md`.
  Dependencies: the chunk/lens/audit Workers are created by the Stage 7 Begin once the Stage 6 reports exist; each lens **merge** is parented by that lens's own chunk cards; and the four lens merges plus the **citation-audit merge** are the parents of the `Stage 7: Adversarial Complete` Barrier. The lenses never read one another, so a lens cannot launder another's miss.
- **Completion:** the four lens reports (`LOGIC.md`, `REFUTATION.md`, `OVERREACH.md`, `NULLHYP.md`) and `CONTEXT-AUDIT.md` are written — the verdicts Stage 8's reconciler uses to keep, narrow, or drop each claim — and the `Stage 7: Adversarial Complete` Barrier completes.
- **Exit:** the `Stage 7: Adversarial Complete` Barrier completes, releasing `Stage 8: Conclusion`.

### Stage 8 — Conclusion
- **Purpose:** reconcile the reviewed findings and assemble the final brief.
- **Starts when:** the `Stage 7: Adversarial Complete` Barrier completes.
- **Does:** `Stage 8: Conclusion` (invoking `rx.py analyze-conclude`) creates three Worker cards in fixed order — no data-dependent fan-out:
  - **Reconcile adversarial verdicts** (`rx-verify`) — reads the Stage 6 research reports (substance, marker, trend, screens, **and `efficacy-*.md`**) and Stage 7's four lens reports plus `CONTEXT-AUDIT.md`, resolves disagreements between lenses, and decides each claim's fate: a claim survives only if its citation passed the audit **and** no lens left a `fatal` (or an un-narrowed `serious`) finding against it; the rest are dropped or narrowed. An efficacy report's "expected to move X" claims are audited like any claim; its observed pre/post values are arithmetic over the user's confirmed labs and stand or fall on the lab confirmation, not on a citation.
  - **Assemble prescriber discussion brief** (`rx-verify`, parented on Reconcile) — writes the surviving, cited claims into `<date>-rx-review.md`, including a "what this review did not cover" section for anything excluded (from `coverage.md`) and a **Medication/Supplement efficacy** section from the surviving efficacy findings: for each dated substance, the before/after comparison of the markers its research says it moves, with the post-start draw count; "too early to tell" carried through verbatim.
  - **Adversarial review of the brief** (`rx-devil`, parented on Assemble) — a final adversarial pass over the assembled brief. It **never blocks**: it flags each defect (`fatal`/`serious`/`minor`) **in place** in `<date>-rx-review.md` — a `> **[review: …]**` line inserted directly after the sentence it concerns — writes the full critique to `CRITIQUE.md`, and completes with the counts. The card is subscribed, so its completion and issue counts reach the user; the analysis always finishes.
  The three run in sequence (each parented on the previous), and the last is a parent of the `Stage 8: Conclusion Complete` Barrier. The Stage 8: Conclusion Complete Barrier runs `check-output --stage 8` (the dated brief in reports/ satisfies it).
- **Completion:** `<date>-rx-review.md` produced, containing all findings, evidence, and citations.
- **Exit:** none — the pipeline is complete.

---

## Flow Control Mechanics

_This section is under user control - do not change without explicit permission_

The Hermes Kanban scheduler is responsible for implementing flow control. However, it is the dependencies
and requirements the Kanban cards express that dictate the execution shape that manifests as part of
that flow control. Three card types are used in this pipeline to dictate the shape and ordering of
execution of this pipeline:

- **Stage Begin Cards** — Used at the start of each stage except Stage 1. Responsible for creating the stage's initial cards - Worker cards, or none (Stage 5), or the substage Begin/Barrier pairs (Stage 6) - and marking them as parents of the stage's Barrier card where they gate it.
- **Worker Cards** — created parentless (if they do not depend on any other card), or with a `parents=[…]` list naming what must finish before they run. Dependencies that cards may have include the Stage Begin card that created them, a Barrier card, or other Worker cards in the same stage. That last case lets a stage's workers form a dependency chain — a DAG, not just a flat set — so a worker that consumes another's output is parented by it and runs only after it completes. In Stage 6, for example, each **synthesis** worker is parented by its own part workers, and 6d's **schedule review** worker is parented by the **interaction & timing screen** worker whose output it consumes (the interaction & timing screen is itself parented on 6d's Begin, which is gated on 6a's Barrier).
Worker cards do the majority of the actual work within a stage and run without the user — they never block on or wait for a human. A worker may report its completion to the user (a subscribed card posts its result and counts on finishing — Stage 8's brief review does exactly this), but a completion notification is not a question: nothing waits for a reply. Anything a stage needs from the user is raised by the stage's Barrier — i.e. the two gates: the Stage 3 Finalize review and the Stage 5 marker review.

- **Barrier cards** — Parented by Stage Begin or Worker cards and used to verify that a set of Worker cards has completed. These cards implement any needed checks on whether work is done or if new Worker cards (along with an associated required Barrier card) need to be created. These may be created at the start of an rx-review run or dynamically, e.g. when additional workers / passes are needed. When its checks pass a Barrier card completes, which releases the next stage's Stage Begin card gated behind it. A Barrier that guards dynamically-created workers must itself be set as a parent of that next Stage Begin card, or the next stage starts before the new work finishes.

- **Script-owned completion (leak-free bodies).** A **script-settled card's** body (Begin, Barrier, or the Lab: per-PDF card) says only "run this and report what it printed." The verb it runs is what settles the card:
  - a deterministic stage verb (`intake-*`, `review_labs`, `merge-labs`, `plan-lab`) — **completed by the dispatcher on success**;
  - `check-output`, `check-transcription`, and `settle` — settle themselves;
  - the research phases, and each 6c `Trend: <marker> — dispatch` gate (`trend-dispatch`) — self-complete from `fanout.py`;
  - the two gate Barriers — post their numbered review, block their own card `needs_input`, and complete on the user's answer.

  So no dispatch card body ever tells the model to `kanban_complete`, and a stage that could not proceed **surfaces to the user instead of silently completing**. The exception is the domain **Worker** cards — transcribe, research, and the 6a **Efficacy** card (synthesis-gated, reads a part file, runs the arithmetic verb, completes with its report) — which complete themselves with their output.

**A failure is not automatically a hold.** Only the two gate Barriers block. A non-zero exit means one of two different things, and treating them alike is what stalls a run:

* **The caller can fix it in this turn** — a mistyped argument, an unknown token, a fabricated row to delete. The verb returns non-zero, prints the one thing to do about it, and does **not** block; the worker corrects itself and re-runs the same command in the same turn.
* **The caller cannot fix it** — the OCR service is unreachable, a lab value cannot be verified, or a regimen row fails re-verification. The verb halts the pipeline and reports the error to chat rather than completing.

So a gate is **opt-in and explicit**, and a bare non-zero is not one. A card is NEVER blocked for a condition its own retry could clear, because **a block is terminal**: Hermes does not clear a card that blocked itself (upstream NousResearch/hermes-agent#40312), so such a card is stranded even after the work succeeds. That is not hypothetical — on 2026-08-10 a worker mistyped the path in a `Lab:` card body, `plan-lab` correctly reported no such PDF, the dispatcher blocked the card, the worker's own retry two minutes later completed the work, and the card stayed blocked with the entire lab branch and every stage behind it waiting on it. A verb that genuinely keeps failing still halts the pipeline, by the retry limit rather than on the first attempt, which is the difference between a backstop and a hair trigger.

Every Stage Begin card has 0..N sets of associated Worker cards, and a single corresponding Barrier card. That Barrier is parented by those workers — or by the substage Barriers in Stage 6, or by the Begin itself when a stage creates no workers (Stage 5).

**Parentless means concurrent, and that constrains what those cards may write.** Cards with no dependencies are created parentless in many places in this pipeline. Examples include the `Stage 4: Transcribe Labs` branch head, every `Lab:` card, the 6a/6b/6c substage Begins, every research part — because the card that creates them is what releases them, so an edge back to it would only delay them. The consequence is easy to miss: parentless cards are eligible the moment they exist, and the dispatcher runs `kanban.max_in_progress` of them AT ONCE. Any state such a card writes is therefore written concurrently with its siblings, and must be safe that way — **one writer per path, never a shared mutable file**. Read-modify-write over one shared file is the shape to avoid: it looks safe because the publish is atomic, while the read-modify-write around it is not, and the entry a sibling wrote in between is simply lost.

---

## Stage 2 in detail — reading the regimen

_This section is under user control - do not change without explicit permission_

Stage 2 turns the user's regimen into `regimen-draft.txt`, which is the input to Stage 3. The document is always text and always ONE file — a Google Doc (fetched by `rx.py regimen --from-gdoc`), an attached text file (`--from`), or the message itself (`--stdin`); `rx.py regimen` writes its regimen part to `inputs/regimen.txt`. There are never images or PDFs here, so Stage 2 needs no classification, no OCR, and no vision.

### Stage 2: Read Regimen Begin Card functionality

The Stage 2 Begin card invokes `rx.py intake-regimen`, which reads `inputs/regimen.txt`, creates a single `Worker: Read regimen` worker **with that text carried inline in its body**, marks it a parent of the `Stage 2: Regimen Read` Barrier, and completes.

**The card is keyed on the regimen's CONTENT.** `stable_key("rx-read-regimen", <digest of the text>)`, not a constant. Keyed on content, a corrected regimen is a different card and an unchanged one is a free no-op, which is what *Re-run semantics* already promises for this stage.

**A regimen too large to inline is REFUSED.** Card bodies are capped at 8KB (`KANBAN_BODY_CAP`); a typical regimen is about 1KB and a hundred-item one about 6KB, so a regimen that does not fit is far more likely to be the wrong document than a real one. It is refused before the worker card is created, in the same manner as the other Stage 1 and 2 refusals, rather than falling back to a file read — a fallback would restore the second code path that inlining exists to remove.

### Stage 2: Worker Card functionality

The worker is HANDED the regimen text in its card body — it opens no file and names no path — and writes `regimen-draft.txt` as one row per product, `product | brand | quantity | schedule | started`, filling each row from what the user wrote: `product` and `brand` as written, `quantity` as the user provided it (`1 capsule`, `1 shot`, `1 pill`, `5g scoop`, or blank), `schedule` the timing (`morning` / `noon` / `evening` / `weekly` / `as needed`), and `started` when the user STARTED taking the item — a month or a date as written (`2026-04` or `2026-04-01`) — left blank when the user did not state it. It completes with metadata `{"products": N}`.

### Stage 2: Regimen Read Barrier Card functionality

`Stage 2: Regimen Read` is a plain barrier: Stage 2 raises nothing with the user, so it holds no review. It runs `check-output --stage 2`, which confirms `regimen-draft.txt` exists and completes the card on success — releasing Stage 3.

---

## Stage 3 in detail — settling the regimen

_This section is under user control - do not change without explicit permission_

Stage 3 turns `regimen-draft.txt` — Stage 2's best machine reading of what the user wrote — into `regimen-final.md`, the single settled table every Stage 6 research family reads. Every row can pass through any combination of machine reading, manufacturer lookup, and human correction, and all of it must land on the same row for the same item without cross-contaminating the other regimen items.

### Stage 3: Settle the Regimen Begin Card functionality

The Stage 3 begin card invokes `rx.py intake-regimen-items`, which creates a `Regimen Intake: <name>` worker for each of the supplement and medication rows in the `regimen-draft.txt`, providing the worker with the complete set of information from the row. All of those workers are marked as parents of the `Stage 3: Finalize Regimen` Barrier and are eligible to execute as soon as they are created. Once the Stage Begin card has created a worker for every row, it completes.

### Stage 3: Worker Card functionality

Each worker's job is to take the provided information for the supplement/medication and produce a `regimen-item-<slug>.md` with
the following information fields:
- Name — The name for the item. Typically this will be <brand> <product name>. When a brand has multiple items with
  the same product name (e.g. Thorne Super EPA has two different versions), then additional information will be added to distinguish which it is.
  At least some portion of this information will be provided to the worker card as it originates in the user's patient document.
- Ingredients — the active ingredients (e.g. 200mg ascorbic acid) and serving size/dosage (e.g. `one 5g scoop` or `3 pills`) as printed on the product label. Finding it is the worker LLM's main job and takes a web search and often a fetch, and a fetch of a bot-walled page escalates to a slow browser render. Each search and each fetch is therefore allowed 60s, and the whole card is capped at 4 minutes of wall-clock. The 60s timeout manifests as the `command_timeout` used with the terminal command when invoking the `web-access` skill, and also manifests in the call to web-access itself using `--timeout 60`.
- Quantity — what the user actually takes, as written (1 capsule, 1 shot, 5g scoop). This is typically already present in the provided information.
- Schedule — For items taken daily, this will be one or more of "morning", "noon", "evening". For weekly items this will be "weekly". For as-needed items this will be "as needed". This is always provided by the user's patient document.
- Started — when the user STARTED taking the item: a month or a date (`2026-04` / `2026-04-01`), copied from the row Stage 2 transcribed. Blank when the user did not state it — supplements are usually blank, and a blank `Started` changes nothing downstream (no Efficacy card, no before/after comparison). It is user-provided, not researched: the worker does not look it up or infer it, it carries it through.
- Confidence — the worker LLM's estimate of how accurately `regimen-item-<slug>.md` captures the user's intended item, `low` or `high`. It is informational.

Stage 3 worker cards never message the user nor mark themselves `needs_input`; their only two outcomes are: wrote their `regimen-item-<slug>.md` and complete, or a lookup failed, so the worker fills whichever fields it could, marks confidence `low`, and completes anyway.

### Stage 3: Finalize Barrier Card functionality

`Stage 3: Finalize Regimen` runs `rx.py gather-regimen-slugs`, which combines the per-item files into a single `regimen-final.md`, consisting of a row (row number · Name · Ingredients · Quantity · Schedule · Started · Confidence) for each regimen slug. Once that is done, it posts the completed `regimen-final.md` to chat, and blocks its OWN card `needs_input` — the Barrier is the one card in the stage allowed to wait on a human. The user replies with `<n> correction` to fix an item, `<n> drop` to exclude an item they cannot confirm (kept out of Stage 6 research and listed as not covered in the brief), or `approved` to finish.

Any user input except `approved` is passed by the LLM VERBATIM to `rx.py correct-item-slug-request <user response>`.
- For `drop` operations, the script removes the line in question and renumbers the remaining items, then returns the updated table to the user for another review pass.

- For correction operations, the SCRIPT reads the leading number — so the number the user wrote picks the line and no correction can land on another item — and returns that one line plus the correction text. The LLM merges the correction into that single line and returns it to the
script via `rx.py correct-item-slug-response <llm-updated line>`; the script VALIDATES the returned line (same field count, Schedule not blanked) before replacing it. The script then returns the updated `regimen-final.md` for re-review by the user. The loop ends when the user replies `approved`, at which point the Barrier completes — releasing `Stage 5: Review Labs` jointly with the `Stage 4: Labs Transcribed` Barrier, and feeding the Stage 6 join.

Note that `correct-item-slug-request` always checks for a leading number; input with none (a comment, or a mistyped finish word) is returned to the user as a re-prompt that names the `<n> <correction>` format, never merged into a line. The request RECORDS which line number it handed out, and `correct-item-slug-response` updates THAT line — so a response cannot land on a different item even if the LLM omits the number, and a stale response with no pending request is refused.

---

## Stage 4 in detail — transcribing the labs

_This section is under user control - do not change without explicit permission_

Stage 4 transcribes every staged lab PDF into `labs-draft.md`, the single table Stage 5 reviews. Lab
documents are always PDFs. The work for each PDF is owned by its own dedicated card, so one large or
unreadable document never blocks the others, and the mechanical work — detecting the document type,
OCR'ing a scan, and planning the split — is done by a script, not the agent. Each PDF is
auto-detected and auto-converted to text: a PDF with a real text layer is extracted directly; a
scanned/bitmap PDF (no text layer) is OCR'd to a searchable PDF first, then extracted the same way.
Values are copied exactly as printed; deciding which are out-of-range is Stage 5's job, not this
one's.

### Stage 4: Transcribe Labs Begin Card functionality

The Stage 4 Begin card invokes `rx.py intake-labs`. It refuses up front on an unstaged or empty set of PDFs. For each staged PDF in `inputs/raw/`, it creates one **per-PDF card** — `Lab: <file>` — created parentless (eligible at once) and set as a parent of the `Stage 4: Labs Transcribed` Barrier, then completes. It transcribes nothing itself; each per-PDF card owns its one document.

**The document binding is recorded here, not passed later.** Before creating each card, `intake-labs` writes that card's binding as its own record in the stage manifest — `inputs/.xcribe/<token>.json`, an opaque 12-character token naming the staged PDF it stands for. The card body then names only the token (`rx.py plan-lab <token>`), never a path. This is the one place the binding can be recorded without anyone re-deriving it: `intake-labs` is holding the document when it creates the card, and every later step is downstream of that fact. The token is derived deterministically from the document, so a re-run of this idempotent stage writes the same record and produces the same card body.

**The manifest is a DIRECTORY of one record per token, never one shared file.** Every writer creates only its own record, so there is no read-modify-write for a concurrent writer to lose. This is not a stylistic choice: `Lab:` cards are deliberately parentless and therefore all eligible at once, the dispatcher runs four at a time, and a single `manifest.json` updated by read-modify-write lost entries under exactly that load — six times in the run of 2026-08-10, each surfacing later as `check-transcription` reporting a token it could not find. A shared file also shared one temp name, so two writers could publish malformed JSON, and a writer that then read it silently restarted from an empty map and erased every prior entry. One file per token removes the whole class rather than serialising around it, and matches the per-token scratch files (`<token>.src.txt`, `<token>.tbl.md`, `<token>.check.log`) already written beside it.

Why a token rather than the path: a path in a card body is a ~110-character literal the worker must copy verbatim, and on 2026-08-10 a worker corrupted one (splicing `kanban/rx-review/` into the middle), `plan-lab` correctly reported no such PDF, the dispatcher blocked the card, and the whole lab branch stalled behind a card whose work a retry had already completed. A token is short, opaque and verifiable: a corrupted one matches nothing and says so, where a corrupted path merely looks plausible.

### Stage 4: Per-PDF Card functionality

The LLM working a `Lab: <file>` card does exactly one thing: it runs `rx.py plan-lab <token>`, reports what that command prints — **the verb completes the card**.

**`plan-lab` READS the manifest first.** The token it was given resolves to the staged PDF `intake-labs` bound to this card; that lookup is the only way the script learns which document it owns, and the only way the model can be wrong about it is by mistyping a token that then matches nothing. A token with no entry is **not a hold**: it is the worker's to fix, so the script returns non-zero saying the token is unknown and the worker re-runs it from its own card body in the same turn — the same rule `check-transcription` follows for a fabricated row, and for the same reason, since blocking would strand a card whose work a retry can complete. A genuine failure of the document — the OCR service unreachable, no text recovered — halts the pipeline.

`--pdf <file>` remains as a hand-run escape hatch for an operator debugging one document, and is **hidden from `--help`**: a flag the worker can discover is a flag the worker can pass a hallucinated path to, which is the failure the token removes. Run with no argument at all, the script asks for a **token** and nothing else — the caller in that case is almost always a worker that dropped one, and telling it a document is missing would hand back the one concept the token exists to keep out of its reach. Documents are named only to a caller who has already named one: `--pdf` given without a filename is a person's mistake, and answers in a person's terms.

`rx.py plan-lab` handles the one document end to end:
- The script reads the PDF's text layer. When that layer is missing — i.e. the PDF is a scan — the
  script POSTs the PDF to the OCR service (`ocrmypdf-web` on the docker host; `RX_OCR_URL`, default
  `http://192.168.1.226:8093/ocr`), which returns a searchable PDF the script writes to
  `inputs/raw-ocr/` and reads instead. Deterministic OCR (tesseract under the hood), no model and no
  vision. If the service is unreachable or recovers no text, the script reports the error and halts.
- From the extracted text the script strips the page furniture (headers, footers, and the
  identity header that carries the source filename), flattens the document to a single list of result
  lines, and splits it into **overlapping line-windows** — each sized to inline whole within the 8KB
  card body, overlapping its neighbour by enough lines that a reading on a boundary sits wholly inside
  at least one window. It creates **one `Transcribe Lab <file>` child per window**, the window's lines
  carried inline in the card body; a reading in an overlap is transcribed twice, by two children that
  never see each other, as a cross-check.
- The script WRITES one manifest record per child as it creates it — its own `<token>.json`, holding `{pdf, first, last, out}`: the document, the window's first and last line, and the `labs-<slug>[-<window>].md` the verified rows will be written to. This is what lets a `Transcribe Lab` card be leak-free: the child is handed its window's lines inline and its token, and the record holds everything about the document it must not see. The manifest therefore carries two kinds of record — one document binding per `Lab:` card (written by `intake-labs`), one window record per transcription child (written here) — each in its own file, so the several `plan-lab` processes the dispatcher runs at once never write the same path.
- The script sets every child it creates as a **parent of the `Stage 4: Labs Transcribed` Barrier** before it exits, so the Barrier waits for children that did not exist when it was created.

### Stage 4: Transcription Card functionality

Each transcription child is handed one overlapping window of the PDF's flattened, furniture-free result lines, inline in its card body — it never opens the PDF or a text file, so no child handles an image, a filename, or a line range — and writes `| marker | value | unit | reference range | specimen | date |`, one row per printed result, copying each value exactly and keeping the lab's own flag.

`check-transcription <token>` then READS that token's window record from the manifest — the only thing that knows which document and which lines the child was transcribing — verifies every row against the stored window text, stamps the `source file` column the child never saw, and writes `labs-<slug>.md` (or `labs-<slug>-<window>.md` for a window). The child holds only its inline lines and its token, so the record is what keeps the filename out of the card without losing the provenance the merge and the Stage 6 backstop later depend on. Children never message the user.

**Manifest lifecycle, end to end.** `inputs/.xcribe/` holds one `<token>.json` per token: written by `intake-labs` (one document binding per `Lab:` card) and by `plan-lab` (one window record per transcription child), and read by exactly two verbs — `plan-lab`, to learn its own document, and `check-transcription`, to learn a window's source and destination. Every record has exactly ONE writer, which is what makes it safe under a dispatcher running four cards at once; no verb ever rewrites another's record, and none needs to read the set as a whole. Nothing else reads it — not the model, which only ever handles opaque tokens; not the `Transcribe Lab` card, which carries its window inline; not `merge-labs`, which reads the written `labs-*.md` files. The directory is dotted, so `reset` removes it wholesale and every review builds its own: no token outlives the run that issued it, and a token from a cleared run resolves to nothing rather than to a stale document.

### Stage 4: Labs Transcribed Barrier Card functionality

`Stage 4: Labs Transcribed` waits on every per-PDF extraction card and every transcription child. Once they have all completed it runs `rx.py merge-labs`, which combines the per-window transcriptions into `labs-draft.md`, collapses readings the overlap transcribed twice (keyed on analyte + specimen + units, so blood and urine glucose stay distinct), reports any disagreement between the two transcriptions of an overlapping window, and flags an overlap that names no reading in common as a possible missed marker, then completes — releasing Stage 5.

A window that could not reach an analyte's value writes `UNREADABLE` for it while the neighbouring window reads it. Those rows are **subsumed**: an `UNREADABLE` row is dropped when a readable reading of the same analyte, date and document exists — keyed WITHOUT specimen, because the specimen cell of an unread row is unread too. The drop is listed under *Unreadable rows superseded* rather than made silently, and an analyte that is unreadable everywhere keeps its row, because that is a real gap the Stage 6 backstop must still see.

**A row with no value, no unit and no reference range was never a measurement, and is dropped.** A lab prints footnotes in the shape of results: the Function urinalysis panel prints a line labelled `NOTE`, carrying the lab's flag in the flag column with prose beneath, directly under `NONE SEEN /LPF`. To a transcriber walking a flattened list of result lines the two are indistinguishable, so it emits a row and writes `UNREADABLE` for a value that was never there — exactly as its card instructs. Nothing downstream could tell that row from a real gap: no window reads it, because there is nothing to read, and the fabrication check passes it because `NOTE` IS printed on the page.

---

## Stage 5 in detail — reviewing the markers

_This section is under user control - do not change without explicit permission_

Stage 5 turns `labs-draft.md` — the merged transcription Stage 4 produced — into
`labs-complete.md`, the annotated full record every script reads, and `labs-succinct.md`, the
significant-marker view the research cards read. A marker is flagged when its most recent reading
is out of range; a marker that is only trending (still in range) is never questioned — it
proceeds into Stage 6 normally. The stage runs no Worker cards at all: a Begin that derives the
questions and a Barrier that asks them. The Begin waits on BOTH the `Stage 4: Labs Transcribed`
and `Stage 3: Finalize Regimen` Barriers, so the marker review is never posted while the regimen
review is open.

### Stage 5: Review Labs Begin Card functionality

The Stage 5 Begin card invokes `rx.py review_labs`, which seeds `labs-complete.md` by copying
`labs-draft.md`, derives its `## Out of range` section, and writes one `marker-question-<slug>.md`
for every marker out of range as of its most recent reading. It creates no cards and posts no
message — the Barrier asks the whole batch at once. With nothing out of range it writes no
question files, and the Barrier below completes on its own.

### Stage 5: Labs Complete Barrier Card functionality

`Stage 5: Labs Complete` runs `rx.py labs-brief`, which gathers the `marker-question-*.md` files
into one numbered list (the number → marker map is recorded in `marker-batch-index.md`), posts it
as ONE chat message, and blocks its OWN card `needs_input` — the one card in the stage allowed to
wait on a human. When stage 5 flagged nothing, `labs-brief` writes `labs-succinct.md` and the
Barrier completes without asking anything.

The user's replies become verbs:
- **Ignore** — `rx.py marker-review` records a decision by `--number` (from the batch index) or
  `--marker` (matched against exactly the names the message listed); `--ignore` and `--confirm`
  are additive, and `--drop` clears a recorded decision. The decision lands in `labs-complete.md`
  and the marker's question file is deleted. An ignored marker keeps its value in
  `labs-complete.md` and in the brief; what it loses is its research cards.
- **Accept** — "looks good" (`rx.py labs-accept`) keeps every remaining flagged marker
  significant, deletes their question files, writes `labs-succinct.md`, and completes the
  Barrier — feeding the Stage 6 join. A flagged marker carries no *missing* information — the
  value and its flag are already in hand — so accepting all-as-significant is a valid safe
  default. 
- **Reject** — `labs-reject` halts the review. Rejection says the READING is wrong, where
  `--ignore` says "the value is right, do not research it" — excluding a wrong value would
  publish it. Re-transcribing is not offered: re-running the same cards over the same documents
  reproduces the same reading, and what has to change — the input or the method — is a human
  decision. `labs-draft.md`, `labs-complete.md` and the per-document transcriptions move to
  `salvage/` and their transcription-cache entries are dropped; see *What a halt does*, below.

Where the same marker name appears under two specimens (blood and urine GLUCOSE), the list shows
the two distinguishably and each is answerable on its own.

---

## Stage 6 in detail — research

_This section is under user control - do not change without explicit permission_

Stage 6 turns the settled regimen and the reviewed labs into cited research reports under
`reports/` — one per substance, per out-of-range marker, and per marker trend, plus the two
whole-regimen screens. It is the join of the two branches: its Begin waits on the Stage 3 AND
Stage 5 Barriers. Everything in it is sharded — part cards that never read each other, one
synthesis per subject — and every exclusion the user made upstream takes effect at one point.

### Stage 6: Research Begin Card functionality

The Begin card invokes `rx.py analyze-research` (no `--family`). Before creating anything the script re-verifies the inputs — `check_labs` confirms every lab value against its source, `check_regimen` that every item is settled — and on a failure it errors out: the card fails and the pipeline halts, rather than completing (`--force` overrides). `check_labs` applies the same `UNREADABLE` subsumption the merge does, so a value one window could not read but another did is not a finding here; without that the backstop and the merge could disagree about the same table, and the backstop would hold the research phase over a value the pipeline already had. The check is a backstop for a card reached out of order, never the mechanism: the Barriers in front of Stage 6 are what guarantee the regimen is finalized and the markers reviewed by the time this card runs. On a
clean check it execs `fanout.py --phase research`, which creates the four substage shells
(6a–6d, each a Begin + Barrier), creates the 6a/6b/6c Begins parentless, wires the
`Stage 6a: Substances Researched` Barrier ahead of the 6d Begin and all four substage Barriers
ahead of `Stage 6: Research Complete`, writes `inputs/coverage.md` — every subject excluded
across all families, or "Nothing was excluded." — and self-completes.

### Stage 6a/6b/6c: Substage Begin Card functionality

Each substage Begin invokes `rx.py analyze-research --family <substances|markers|trends|screens>`; a
substage skips the backstop, since the same data was already verified at the main Begin.
`fanout.py` reads the family's subjects from the single authoritative sources — the rows of
`regimen-final.md`, rx.py's out-of-range list, rx.py's trend detector — and builds one card-set
per subject with `shard()`: three part Workers, created parentless, and one synthesis Worker
parented on its three parts; every synthesis is spliced as a parent of the substage's Barrier
before the Begin completes.

`shard()` is the single point where an exclusion takes effect: subject `"substance"` consults
the items dropped at the regimen review, subject `"marker"` the markers the marker review
ignored, and an excluded subject returns None and is dropped before any `parents=` list is
built. An ignored marker is thereby excluded from BOTH 6b and 6c — a trend card is research on
that value under another title. A family added later inherits the filter by declaring its
subject, which is why the filter lives in one place.

### Stage 6: Part and Synthesis Worker Card functionality

A part card carries only its own question group and never reads another part's. Which parts load
the labs is per-family (`labs_parts` in `shard()`): substances `{2}`, markers `{1}` (only part 1
interprets the user's out-of-range direction), trends `set()` — the trend intro interpolates the
dated series, so no trend part opens the labs file. For substances the three groups are:
(1) **evidence & efficacy** — indications and the QUALITY of the evidence (meta-analysis / RCT /
observational / animal / in-vitro / marketing), pure literature; (2) **safety & marker
effects** — adverse effects and dose-dependence, which markers the substance moves, and anything
in the user's labs plausibly related, stated as a hypothesis; (3) **timing** — absorption, food,
what it must be separated from, cost tier, checked against what the user actually does, its one
user-specific fact interpolated into the body.

Every part builds its fragment (`PART-<family>-<slug>-<n>.md`) incrementally — read it first,
keep what is recorded, append each finding as its citation lands — so a compaction or a
runtime-limit retry resumes from the fragment instead of re-fetching its sources. The synthesis
is the only card that reads its three fragments; it writes the subject's report
(`substance-`/`marker-`/`trend-<slug>.md`) and, as a domain Worker, completes via
`kanban_complete` with its summary. Parts run on a 25-minute clock, syntheses 30. Every
report-producing card carries the endnote contract.

**The Efficacy card (6a only, dated substances only).** After `shard()` returns a substance's synthesis id, the family loop creates — only when that substance's `Started` field in `regimen-final.md` is non-blank — one `Efficacy: <substance>` Worker, profile `rx-research`, runtime 30m, parented on that substance's synthesis, and spliced in front of the `Stage 6a: Substances Researched` Barrier like a synthesis (its id is appended to the same list the Barrier is spliced from). It answers one question: is this medication moving the markers its research says it moves? It reads `PART-research-<slug>-2.md` — the part-2 fragment, which holds the answer to question 4 (which lab markers the substance is known to move, in which direction) — takes that answer as the ONLY marker list (no re-research), runs, per marker,
`python3 ~/.hermes/rx-review/rx.py before-after --marker <marker> --since <started>`,
and writes `efficacy-<slug>.md` to `reports/`: for each marker the expected direction, the observed pre→post values, the delta, and the post-start draw count; the part-2 citation carried into the endnotes; observed values labelled "from the user's labs". `before-after` is pure arithmetic — it splits the confirmed dated series at the start date and prints pre values, post values, delta, and the post-start draw count — so the drug↔marker knowledge stays in the research (the LLM's question-4 answer) and the script never learns what a statin is. "Too early to tell" is a first-class result, reported with the post-start draw count (even 0 or 1); a single early post draw is not evidence of effect. It recommends nothing. Blank `Started` ⇒ no Efficacy card ⇒ the supplement's graph is byte-identical to today.

For **6a and 6b** this is the whole story; **6c** defers it. A trend first gets a single **triage**
part-card (the "is this meaningful" question, part 1) plus a deterministic **dispatch** gate; only
when the triage's verdict is meaningful does the dispatch create parts 2 and 3 and a synthesis
parented on `[triage, part 2, part 3]` — the same three-fragment gate. An ordinary trend has no
synthesis: the dispatch writes `trend-<slug>.md` itself from the triage's reason. The triage's
verdict lives in a `PART-trend-<slug>-verdict.md` — the `PART-` prefix keeps every report-globbing
consumer from reading it as a finished report. See *Stage 6c* under *## Pipeline Stages*.

### Stage 6d: Whole-regimen Screens Card functionality

The 6d Begin — gated on the `Stage 6a: Substances Researched` Barrier, so every substance report
exists — creates the **interaction & timing screen** (parented on the 6d Begin), writing
`interactions.md`, and, only when the regimen records any dose times, the **schedule review**
(parented on the interaction screen, whose output it consumes), writing `SCHEDULE.md`. Both are
parents of the `Stage 6d: Screens Complete` Barrier.

### Stage 6: Barrier Card functionality

Each substage Barrier completes when its syntheses (6a/6b/6c — and in 6a, the Efficacy cards where created) or screens (6d) have completed; a
family with no subjects — no out-of-range markers, no trends — creates no card-sets and its
Barrier completes on the empty set. `Stage 6: Research Complete`, parented on all four substage
Barriers, completes last and releases `Stage 7: Adversarial Review`.

---

## The shape of a run

```
  UPLOAD                    lab PDFs; regimen as text (Google Doc / file / message)
     |
  STAGE 1  rx.py stage           NO CARD — run after EVERY upload round. Copies every PDF
     |                           Hermes received into inputs/raw/ - if Hermes received a zip
     |                           file containing PDFs, unzips it into inputs/raw - and
     |                           re-scans to prove none was missed. Creates nothing.
     |
     |       rx.py uploads-done  NO CARD — the person says that is all the labs; the set they
     |                           confirmed is recorded. `start` refuses without it.
     |
     |       rx.py start           Begins the review, ONCE. Refuses on anything unstaged, on
     |                           zero PDFs, on an unresolved regimen, and until the labs are
     |                           confirmed complete (or one arrived since). Creates the WHOLE

REPLACEMENT TEXT:
     |       rx.py start           Begins the review, ONCE. Refuses on anything unstaged, on
     |                           zero PDFs, on an unresolved regimen, on no ingested patient
     |                           information (NO PATIENT), and until the labs are confirmed
     |                           complete (or one arrived since). Creates the WHOLE
     |                           Begin→Barrier spine below in one shot — a DAG: the regimen
     |                           and labs branches run in parallel and Stage 6 joins them —
     |                           so no stage can start before what it depends on finishes.
     |
  STAGE 2  Stage 2: Read Regimen (Begin)         `rx.py intake-regimen`
     |       └─ Worker: Read regimen                    → regimen-draft.txt
     |     Stage 2: Regimen Read (Barrier)        completes → releases Stage 3
     |
  STAGE 3  Stage 3: Settle the Regimen (Begin)   `rx.py intake-regimen-items`
     |       └─ Regimen Intake: <name>   one per regimen item → regimen-item-<slug>.md (parallel)
     |                                    Name · Ingredients · Quantity · Schedule · Started · Confidence; never asks, low on a failed lookup
     |     Stage 3: Finalize Regimen (Barrier)    `rx.py gather-regimen-slugs` → regimen-final.md, posts ONE
     |                                            numbered whole-regimen review → blocks until
     |                                            `<n> <correction>` / `<n> drop` / `approved` → feeds the Stage 6 join   ← HUMAN
     |
  STAGE 4  Stage 4: Transcribe Labs (Begin)      `rx.py intake-labs`   (PARALLEL branch: starts after Stage 1, alongside Stages 2-3)
     |       └─ Lab: <file>              one per PDF (parentless); runs `plan-lab <token>` → OCR-detect, flatten, window
     |            └─ Transcribe Lab <file>   one child per overlapping line-window, results inline
     |     Stage 4: Labs Transcribed (Barrier)    `rx.py merge-labs` → labs-draft.md → releases Stage 5
     |
  STAGE 5  Stage 5: Review Labs (Begin)          `rx.py review_labs`   (also waits on the Stage 3 Barrier)
     |       └─ (out-of-range marker)    → marker-question-<slug>.md (no worker; trends not questioned)
     |     Stage 5: Labs Complete (Barrier)       posts ONE numbered marker review → blocks until
     |                                            `labs-accept` → labs-complete.md, labs-succinct.md → feeds the Stage 6 join   ← HUMAN
     |
  STAGE 6  Stage 6: Research Begin (Begin)       `rx.py analyze-research` → execs fanout.py   (JOIN: waits on the Stage 3 AND Stage 5 Barriers)
     |       creates the four substage Begin/Barrier shells, wires 6a's Barrier ahead of the
     |       6d Begin, sets all four substage Barriers ahead of Stage 6: Research Complete
     |
     ├─ 6a  Stage 6a: Research Substances (Begin)       per substance (parallel; rx-research)
     |  │      part-cards: evidence & efficacy · safety & marker effects · timing  (never read each other)
     |  │      └─ synthesis (gated on the three parts)      → substance-<slug>.md
     |  └─ Stage 6a: Substances Researched (Barrier)   releases the 6d Begin
     |
     ├─ 6b  Stage 6b: Research Markers (Begin)          per out-of-range marker (parallel; rx-research)
     |  │      part-cards: what it measures · what moves it · non-regimen causes
     |  │      └─ synthesis (what a clinician checks next; no diagnosis)   → marker-<slug>.md
     |  └─ Stage 6b: Markers Researched (Barrier)
     |
     ├─ 6c  Stage 6c: Research Trends (Begin)           per trending marker (parallel; rx-research)
     |  │      triage: meaningful vs ordinary variation (quantified) → verdict file
     |  │      └─ dispatch (deterministic; reads the verdict)
     |  │           ├─ ordinary   → writes trend-<slug>.md itself (terminal); no synthesis
     |  │           └─ meaningful → parts: common causes · does the regimen drive it
     |  │                            └─ synthesis [triage, part2, part3] (action threshold + one follow-up) → trend-<slug>.md
     |  └─ Stage 6c: Trends Researched (Barrier)   waits on the dispatch (+ synthesis when meaningful)
     |
     └─ 6d  Stage 6d: Whole-regimen Screens (Begin)     after 6a ONLY (alongside 6b/6c; rx-research)
        │      Interaction and timing screen: full regimen              → interactions.md
        │      Schedule review (only if the regimen records dose times) → SCHEDULE.md
        └─ Stage 6d: Screens Complete (Barrier)
     |
  Stage 6: Research Complete (Barrier)           6a+6b+6c+6d done → releases Stage 7
     |
  STAGE 7  Stage 7: Adversarial Review (Begin)   `rx.py analyze-adversarial`
     |     reports packed into window-sized chunks, then two tracks that never read each other:
     |       chunk × lens — logic (LOGIC.md) · counter-evidence (REFUTATION.md) ·
     |                      overreach (OVERREACH.md) · status-quo (NULLHYP.md);
     |                      one card per chunk per lens (each finding graded fatal/serious/minor,
     |                      else clean), a merge per lens
     |       citation audit — per chunk: does the cited text exist, and does it support the
     |                        claim that cited it? then a citation-audit merge → CONTEXT-AUDIT.md
     |     Stage 7: Adversarial Complete (Barrier)   four lens merges + citation-audit merge → releases Stage 8
     |
  STAGE 8  Stage 8: Conclusion (Begin)           `rx.py analyze-conclude`
     |       Reconcile adversarial verdicts   (rx-verify)   keep / narrow / drop each claim
     |       Assemble prescriber discussion brief   (rx-verify)   <date>-rx-review.md
     |       Adversarial review of the brief   (rx-devil)    hostile review of the finished brief
     |     Stage 8: Conclusion Complete (Barrier)      the run is done
```

Stage 1 creates the numbered spine — every later stage's Stage Begin and Barrier cards — in one
pass, each Barrier parented in front of the next stage's Begin. The one nesting is Stage 6:
`Stage 6: Research Begin` creates the four substage (6a–6d) Begin/Barrier cards and links them the
same way — each substage Barrier ahead of what depends on it, 6a's ahead of the 6d Begin, all four
ahead of `Stage 6: Research Complete`. Otherwise nothing creates a stage boundary as it goes: a
stage runs, its workers finish, its Barrier completes, and completing RELEASES the Begin card
already sitting behind it. The order is edges in a graph, not a chain each stage extends as it
goes.

**A stage is not one card.** Stage 1 usually runs as no card at all — a person runs `rx.py stage`
and `rx.py start` by hand — and every later stage runs as a single Stage Begin card that fans out
into workers and then completes: stages 2–5, then `Stage 6: Research Begin`, each of the four
research substages 6a–6d, `Stage 7: Adversarial Review` and `Stage 8: Conclusion`. What is
one-per-stage is the Begin card and its *command*, not the work: a stage's real size is its Worker
cards, and `Stage 3: Settle the Regimen` alone can create one per supplement and medication.

**Created is not released.** Because the whole card set exists from stage 1, a card's turn comes
when its parents finish, not when some upstream stage remembers to create it. A card created with
no parents is `ready` the instant it exists — on 2026-08-01 an accidentally empty parent list put
28 `Transcribe Lab` cards on the board at once — so a Stage Begin card is created with the
Barrier(s) it waits on among its parents, and parentless is reserved for the deliberate cases
whose creator's completion IS their release: the `Stage 4: Transcribe Labs` branch head, `Lab:`
cards, and the 6a/6b/6c substage Begins. Deliberate or not, those cards then run
`max_in_progress` at a time, which is a constraint on what they may write as well as a latency
win — see *Parentless means concurrent* in Flow Control. Worker
cards created inside a stage are set as parents of that stage's Barrier before their creator
completes, and a Barrier that guards dynamically-created workers is itself linked as a parent of
the next stage's Begin, so the next stage cannot start while new work is still outstanding.
Idempotency keys make re-creating any of these free: the same inputs return the same card.

**Stage 1 is the one boundary not expressed as an edge**, and it is a deliberate exception rather
than an oversight. `rx.py start` refuses unless staging is clean, so entry into the pipeline is
enforced by WITHHOLDING CREATION — the mechanism this document relies on edges for everywhere
else. It is sound here for a reason that does not generalise: staging is the only place that can
still see what Hermes received, so "is the input set complete" is answerable there and nowhere
downstream, and there is no earlier card to hang an edge from.

Staging answers only half of "complete", though. It can prove that everything Hermes RECEIVED is
staged; it cannot know whether the user has finished SENDING, and that answer exists nowhere but
with the user. That half was prose in the skill — *ask whether more are coming, and wait* — which
is advice a small model can race: on 2026-08-10 a review was started 23 seconds after the first
of twelve attachments and 23 seconds before the other eleven arrived. It is now the same kind of
refusal as the others: `rx.py uploads-done` records the user's confirmation of the staged SET, and
`start` refuses without it, or when a document has arrived since. Being a confirmation of a set
rather than a flag is what catches the realistic mistake — "that's all", then one more remembered.

Three properties hold throughout and are load-bearing:

**A Barrier releases; it does not gate a human.** What must not run until a stage's work is done
is held by a dependency edge — the next Begin card sits behind the Barrier — not by code inside a
card that would be running. Human input is never a separate gate mechanism: the stage's Barrier
gathers the questions the stage raised, posts ONE numbered batched review to chat, and blocks its
own card `needs_input` until the user accepts (stage 3, `approved`; stage 5, `labs-accept`).
The block parks the Barrier and holds the next Begin behind it; accepting completes the Barrier,
which is what releases the next stage. A worker-blocked card is not auto-promoted when the answer
arrives — the answer verb completes it.

**Parts never read each other.** A sharded card's parts run concurrently and are told to answer
only their own questions. Only the synthesis card sees all the fragments. This is what keeps a
card's context bounded — and why fragments are named `PART-*` and skipped by every stage that
globs `reports/*.md`.

**Lenses never read each other.** Four adversarial reviewers examine the same reports
independently. If they could see each other's output they would converge, and the point is
disagreement.

---

## Every card and script

Execution order. `↳` marks a script invoked by the card above it; unindented rows are scripts
that run outside any card.

| Name | What it does | Triggered by | Cards it creates, and why |
|---|---|---|---|
| **`rx.py regimen`** | Writes the document's regimen entries (a Google Doc via `--from-gdoc <id>`, a local file via `--from`, or text via `--stdin`) to `inputs/regimen.txt` — the regimen part, nothing else | A person when they ask for a rx-review | **nothing** — it lands the input; later cards read from it |
| **`rx.py patient`** | Takes the same document and writes the recognised fact lines (`Name:`, `DOB:`, `Age:`) to `inputs/patient.md` — the patient part, nothing else; a document with no fact lines at all leaves no `patient.md` at all | A person when they ask for a rx-review |  **nothing** — it lands the input; later cards read from it |
| **`rx.py fib4`** | The FIB-4 liver-fibrosis risk score from the newest draw that reports AST, ALT and a platelet count together, with the age from `inputs/patient.md` (computed from the `DOB:` at read time, so it stays correct on the next birthday). Refuses — and names what is missing — when no age is recorded or no draw carries all three inputs | A person, on demand | **nothing** — a read, not a stage |
| **`rx.py stage`** | Copies every PDF Hermes received into `inputs/raw/`. If Hermes received a zip file containing PDFs, unzips it into `inputs/raw`, then re-scans to prove nothing was missed. Run after **every** upload round; idempotent | A person, by hand, each time attachments arrive | **nothing** — copying what arrived and beginning the work are different decisions |
| **`rx.py start`** | Stage 1. Refuses on anything unstaged, on zero staged PDFs, on an unresolved regimen — which arrives as a doc or a file, so nothing upstream could have staged it — and, last, until `uploads-done` records that the user called the lab set complete (and again if one arrived since) | A person, once the user says the labs are complete | The **whole** Begin→Barrier chain for the numbered spine — stages 2–5, `Stage 6: Research Begin`/`Complete`, Stage 7 and Stage 8 — each Barrier parented in front of the next stage's Begin, so the order is fixed from the first minute. The 6a–6d substage shells are created dynamically by `Stage 6: Research Begin` |

REPLACEMENT TEXT:
| **`rx.py start`** | Stage 1. Refuses on anything unstaged, on zero staged PDFs, on an unresolved regimen — which arrives as a doc or a file, so nothing upstream could have staged it — on no ingested patient information (no `patient.md`, `NO PATIENT`), and, last, until `uploads-done` records that the user called the lab set complete (and again if one arrived since) | A person, once the user says the labs are complete | The **whole** Begin→Barrier chain for the numbered spine — stages 2–5, `Stage 6: Research Begin`/`Complete`, Stage 7 and Stage 8 — each Barrier parented in front of the next stage's Begin, so the order is fixed from the first minute. The 6a–6d substage shells are created dynamically by `Stage 6: Research Begin` |
| **`Stage 2: Read Regimen`** (Begin) | Stage 2 spine. **Refuses when there is no `regimen.txt`** — before creating anything | Released when its parent Barrier — `rx.py start` itself, at the head of the chain — is clear | `Worker: Read regimen`, set as a parent of the `Stage 2: Regimen Read` Barrier |
| ↳ `rx.py intake-regimen` | Reads `regimen.txt` and creates the read-regimen worker with that text inline in its body, keyed on the text's digest so a corrected regimen is a new card; holds if it exceeds the 8KB body cap | — | `Worker: Read regimen` |
| **`Worker: Read regimen`** | Transcribes the regimen text carried in its own body → `regimen-draft.txt`, one pipe-delimited line per product (`product | brand | quantity | schedule | started`). It opens no file and looks nothing up — stage 3's `Regimen Intake:` workers do the manufacturer lookup | The `Stage 2: Read Regimen` Begin card | — |
| **`Stage 2: Regimen Read`** (Barrier) | Confirms `regimen-draft.txt` exists, then completes — releasing `Stage 3: Settle the Regimen` | `Worker: Read regimen` done | — |
| **`Stage 3: Settle the Regimen`** (Begin) | Stage 3 spine. Creates one `Regimen Intake: <name>` worker per product row in `regimen-draft.txt`, each a parent of the `Stage 3: Finalize Regimen` Barrier and eligible at once | Released when `Stage 2: Regimen Read` completes | one `Regimen Intake:` per regimen item |
| ↳ `rx.py intake-regimen-items` | Creates one `Regimen Intake: <name>` worker per draft row — every item, no split — each a parent of the `Stage 3: Finalize Regimen` Barrier, handing the worker the whole row | — | `Regimen Intake:` ×(items) |
| **`Regimen Intake: <name>`** | Resolves one supplement or medication — fetching the manufacturer's Supplement Facts panel for the label ingredients and serving size — and writes **Name · Ingredients · Quantity · Schedule · Started · Confidence** to **its own** `regimen-item-<slug>.md`, never a shared file. The workers run in parallel, so per-item files avoid the parallel-write clobber a shared `regimen-final.md` caused. It never asks and never blocks: on a failed lookup it fills what it can, marks confidence `low`, and completes anyway. The one intake card that touches the network | Created by the stage-3 Begin card | — |
| ↳ `web_access.py` | Search and fetch, via the `web-access` skill | — | — |
| **`Stage 3: Finalize Regimen`** (Barrier) | Runs `rx.py gather-regimen-slugs`, which combines every `regimen-item-<slug>.md` into one numbered `regimen-final.md` (row number · Name · Ingredients · Quantity · Schedule · Started · Confidence), posts it with ONE chat message, and blocks its OWN card `needs_input`. The user replies `<n> <correction>`, `<n> drop`, or `approved`; corrections route by number through the correction verbs and replace the named line in place. On `approved` the card completes — releasing `Stage 5: Review Labs` jointly with the Stage 4 Barrier, and feeding the Stage 6 join | Every `Regimen Intake:` done, then the user's `approved` | — |
| ↳ `rx.py gather-regimen-slugs` | Combines the per-item files into `regimen-final.md`, posts the batched whole-regimen review, and blocks the barrier | — | — |
| ↳ `rx.py correct-item-slug-request <user response>` | Reads the leading number the user wrote — so a correction can never land on another item — and returns that one line plus the correction text; `<n> drop` removes the line and renumbers. Refuses input with no leading number, re-prompting the `<n> <correction>` format | — | — |
| ↳ `rx.py correct-item-slug-response <llm-updated line>` | Takes the LLM's merged line for the number a prior request handed out, validates it (same field count, Schedule not blanked), and replaces that line in `regimen-final.md`; a stale response with no pending request is refused. `approved` completes the barrier | — | — |
| **`Stage 4: Transcribe Labs`** (Begin) | Stage 4 spine | Parentless branch head — eligible as soon as `rx.py start` creates it, in parallel with Stages 2–3 | one `Lab: <file>` card per staged PDF |
| ↳ `rx.py intake-labs` | Records each document's binding as its own `.xcribe/<token>.json`, then creates one `Lab: <file>` card per staged PDF — body naming only the token — each a parent of the `Stage 4: Labs Transcribed` Barrier. Two refusals first: HOLDS on an unstaged document (`--force` overrides), ERRORS on zero staged PDFs | — | `Lab: <file>` ×(PDFs) |
| **`Lab: <file>`** | One per staged PDF. Runs `plan-lab <token>`, which OCR-detects, flattens, windows, and creates that PDF's transcription child card(s) — each a parent of the Barrier before it completes, so one large or unreadable document never blocks the others | The `Stage 4: Transcribe Labs` Begin card | its `Transcribe Lab` child(ren) |
| ↳ `rx.py plan-lab <token>` | Resolves the token to its document, then: extract the text layer (OCR a scan to a searchable PDF first), flatten it to furniture-free result lines, split those into overlapping line-windows, and create one `Transcribe Lab` child per window — the window's lines inline in the card body, a `<token>.json` record each, and each child a parent of the Barrier. An unknown token returns non-zero WITHOUT blocking, for the worker to re-run; only a document that cannot be read holds. `--pdf <file>` is a hand-run escape hatch, hidden from `--help` | — | `Transcribe Lab` ×(windows) |
| **`Transcribe Lab <file>`** | One inline line-window → one markdown table; settled by `check-transcription`, never by the model | Created by its `Lab: <file>` card | — |
| ↳ `rx.py check-transcription <token>` | Verifies every row against the source window, stamps the `source file` column, and completes the card; a row not in the source returns non-zero for the worker to delete and re-run, with per-row verdicts in `.xcribe/<token>.check.log` | — | — |
| ↳ `rxsplit.py` | PDF → text; furniture stripping, flattening, and overlapping line-windows; OCR of a scanned PDF via the OCR service | — | — |
| ↳ `rxcache.py` | Content-addressed cache of verified transcriptions | — | — |
| **`Stage 4: Labs Transcribed`** (Barrier) | Runs `rx.py merge-labs`, which combines the per-window tables, collapses readings the overlap transcribed twice (keyed on analyte + specimen + scale), keeps and lists any that DISAGREE, drops an `UNREADABLE` row when another window read that analyte on that date from that document (listed under *Unreadable rows superseded*, never silently), flags an overlap with no reading in common, rebuilds `## Out of range` → `labs-draft.md` (deterministic); then completes, releasing `Stage 5: Review Labs` | Every `Lab:` card and every transcription done | — |
| **`Stage 5: Review Labs`** (Begin) | Stage 5 spine | Released when `Stage 4: Labs Transcribed` **and** `Stage 3: Finalize Regimen` complete | a `marker-question-<slug>.md` (no card) per out-of-range marker |
| ↳ `rx.py review_labs` | Seeds `labs-complete.md` by copying `labs-draft.md`, derives `## Out of range`, then writes a `marker-question-<slug>.md` for every OUT-OF-RANGE marker. Trends stay in `labs-complete.md` and are analysed in stage 6 — they are NOT questioned. No per-marker cards | — | — |
| **`Stage 5: Labs Complete`** (Barrier) | Runs `rx.py labs-brief`, which gathers the `marker-question-*.md` into one numbered list, writes `marker-batch-index.md`, posts it with ONE chat message, and blocks its OWN card `needs_input`. The user ignores any by number (`marker-review`) and accepts (`labs-accept`, which keeps every remaining marker significant, writes `labs-succinct.md`, and completes this card, releasing `Stage 6`). When stage 5 flagged nothing, `labs-brief` just writes `labs-succinct.md` and the barrier completes on its own | Every out-of-range marker reviewed, then the user's acceptance | — |
| ↳ `rx.py labs-brief` | Posts the batched marker review and blocks the barrier; or, when no marker is flagged, writes `labs-succinct.md` | — | — |
| ↳ `rx.py marker-review` | Records a decision (by `--number` or `--marker`; `--ignore`/`--confirm`, additive; `--drop` clears) into `labs-complete.md` and deletes that marker's `marker-question-<slug>.md` | — | — |
| ↳ `rx.py labs-accept` | 'looks good' — keeps every remaining flagged marker significant, deletes their question files, writes `labs-succinct.md`, and completes the barrier | — | — |
| **`Stage 6: Research Begin`** (Begin) | Stage 6 spine. Creates the four research substage shells — 6a/6b/6c/6d, each a Begin+Barrier — wires `Stage 6a: Substances Researched` ahead of the 6d Begin, and sets all four substage Barriers ahead of `Stage 6: Research Complete` | Released when **both** the `Stage 3: Finalize Regimen` and `Stage 5: Labs Complete` Barriers complete — the join | the 6a/6b/6c/6d Begin/Barrier shells |
| ↳ `rx.py analyze-research` | Execs `fanout.py --phase research`. With **no** `--family` (the Stage 6 Begin) it creates the four substage shells; with `--family <substances\|markers\|trends\|screens>` (a substage Begin) it builds that family's workers | — | the substage shells, or one family's workers |
| ↳ `fanout.py --phase {research,adversarial,conclude}` | Builds one stage-phase's cards; card bodies are templates here. Consults the ignore decisions `marker-review` recorded, at the single point where a marker becomes a card, and refuses to create one for a marker the user asked to ignore | — | the phase's cards (a substage's workers, or the shells, or Stage 7/8) |
| **`Stage 6a: Research Substances`** (Begin) | 6a spine — one card-set per regimen substance | Released by `Stage 6: Research Begin`, in parallel with 6b/6c | per substance, three part-cards + a synthesis; each synthesis a parent of `Stage 6a: Substances Researched` |
| **`Research: <substance> — part N/3`** | Sharded substance research: the 7 questions grouped into three cards — evidence & efficacy · safety & marker effects · timing. Parts never read each other. An item marked unknown gets no card | Created by the 6a Begin | — |
| **`Research: <substance> — report`** | Synthesis; the only card that sees all three fragments → `substance-<slug>.md` | Its own three parts | — |
| **`Efficacy: <substance>`** | One per substance whose settled regimen row carries a non-blank `Started` — supplements never get one. Created when its substance's research synthesis completes; reads that synthesis's marker list (the part-2 q4 answer — the script learns no drug knowledge) and runs `rx.py before-after --marker <marker> --since <started>` for each, writing pre values, post values, delta, and the post-start draw count; fewer than two post-start draws ⇒ **"TOO EARLY TO TELL"**, carried through verbatim → `efficacy-<slug>.md`. Spliced in front of the 6a Barrier like a synthesis | Its substance's `Research: <substance> — report` card | — |

REPLACEMENT TEXT:
| **`Efficacy: <substance>`** | One per substance whose settled regimen row carries a non-blank `Started`. Created when its substance's research synthesis completes; reads that synthesis's marker list (the part-2 q4 answer — the script learns no drug knowledge) and runs `rx.py before-after --marker <marker> --since <started>` for each, writing pre values, post values, delta, and the post-start draw count; fewer than two post-start draws ⇒ **"TOO EARLY TO TELL"**, carried through verbatim → `efficacy-<slug>.md`. Spliced in front of the 6a Barrier like a synthesis | Its substance's `Research: <substance> — report` card | — |
| **`Stage 6a: Substances Researched`** (Barrier) | Confirms every substance report written, then completes — releasing the 6d Begin; like all four substage Barriers, it is a parent of `Stage 6: Research Complete` | Every substance synthesis done | — |
| **`Stage 6b: Research Markers`** (Begin) | 6b spine — one card-set per out-of-range marker | Released by `Stage 6: Research Begin`, in parallel with 6a/6c | per marker, three part-cards + a synthesis; each synthesis a parent of `Stage 6b: Markers Researched` |
| **`Marker: <marker> — part N/3`** | Sharded marker research: what it measures & what OOR *in this direction* indicates · which substances move it · non-regimen explanations. Parts never read each other. An excluded marker gets no card | Created by the 6b Begin | — |
| **`Marker: <marker> — report`** | Synthesis — what distinguishes those explanations, what a clinician would check next (no diagnosis) → `marker-<slug>.md` | Its own three parts | — |
| **`Stage 6b: Markers Researched`** (Barrier) | Confirms every marker report written, then completes | Every marker synthesis done | — |
| **`Stage 6c: Research Trends`** (Begin) | 6c spine — one card-set per trending marker | Released by `Stage 6: Research Begin`, in parallel with 6a/6b | per trend, a **triage** part-card + a deterministic **dispatch**; the deeper parts and synthesis are created only when the trend is meaningful |
| **`Trend: <marker> — triage`** | Quantifies the marker's ordinary variation and judges meaningful-vs-not; writes its `PART-` fragment (part 1) **and** a two-line verdict to `PART-trend-<slug>-verdict.md`. Reads no labs. An excluded marker gets no card | Created by the 6c Begin | its verdict + fragment |
| **`Trend: <marker> — dispatch`** (`trend-dispatch`) | Deterministic gate: reads the verdict. **Ordinary** (explicit `MEANINGFUL: no`) → writes `trend-<slug>.md` (skip + the triage's reason); terminal, no synthesis. **Anything else** (incl. absent/garbled verdict) → creates parts 2/3 and the synthesis parented on `[triage, 2, 3]`, and splices the synthesis onto the Barrier | Its triage | the two parts + synthesis, or the skip report |
| **`Trend: <marker> — part 2/3`, `3/3`** | (2) common causes & which benign · (3) does the regimen drive it. Created only for a meaningful trend. Parts never read each other | Created by the dispatch | — |
| **`Trend: <marker> — report`** | Synthesis — action threshold + one settling follow-up (no recommendation) → `trend-<slug>.md`. Created only for a meaningful trend | Its triage + two parts | — |
| **`Stage 6c: Trends Researched`** (Barrier) | Confirms each trend's terminal card (dispatch when ordinary, synthesis when meaningful) completed, then completes | Every trend's terminal card done | — |
| ↳ `rxfetch.py` | Binding to the `web-access` fetcher — not a copy, copies diverged | — | — |
| **`Stage 6d: Whole-regimen Screens`** (Begin) | 6d spine — gated on 6a's Barrier, so all substance research is done; runs alongside 6b/6c | Released when `Stage 6a: Substances Researched` completes | the interaction & timing screen, plus the schedule review only when the regimen records dose times |
| **`Interaction and timing screen: full regimen`** | Cross-regimen interaction & timing screen → `interactions.md` | The 6d Begin (all substance syntheses already done) | — |
| **`Schedule review: current vs evidence-based timing`** | Current timing against the evidence → `SCHEDULE.md`; created only when the regimen records dose times | The interaction & timing screen | — |
| **`Stage 6d: Screens Complete`** (Barrier) | Confirms the screens written, then completes | The screens | — |
| **`Stage 6: Research Complete`** (Barrier) | Confirms all four substages done, then completes — releasing `Stage 7: Adversarial Review` | The four substage Barriers | — |
| **`Stage 7: Adversarial Review`** (Begin) | Stage 7 spine. Packs the reports into window-sized chunks, then fans out chunk × lens and the citation audit — the chunk count is not knowable until the reports exist, so this cannot be static | Released when `Stage 6: Research Complete` completes | one card per chunk × 4 lenses, a merge per lens, per-chunk audit cards, a citation-audit merge |
| ↳ `rx.py analyze-adversarial` | Execs `fanout.py` to chunk the reports and build the lens + audit fan-out | — | the chunk/lens/audit workers, the lens merges, and the citation-audit merge |
| ↳ `lenses.py` | Chunking, and the four lens definitions | — | — |
| **Per-chunk lens cards** (4 lenses) | `logic` (attack the reasoning), `counter` (evidence ignored or contradicted), `overreach` (claim strength vs support), `nullhyp` (steelman changing nothing). Lenses never read each other | Created by the Stage 7 Begin | — |
| **Lens merges** | Concatenate a lens's per-chunk findings → `LOGIC.md`, `REFUTATION.md`, `OVERREACH.md`, `NULLHYP.md`; each a parent of `Stage 7: Adversarial Complete` | That lens's chunk cards | — |
| **Citation audit** (per-chunk) | Confirms each cited text exists in its source and supports the report's claim that cited it (a source that cannot be read is `dead-link`, never `unsupported`); each is a parent of the citation-audit merge | Created by the Stage 7 Begin | — |
| **Citation-audit merge** | Concatenates the per-chunk audit findings → `CONTEXT-AUDIT.md`; the audit track's counterpart to each lens's merge, and a parent of `Stage 7: Adversarial Complete` | That track's per-chunk audit cards | — |
| ↳ `verify.py` | Locates each quoted sentence in its source (fetched through the shared fetcher), so the audit cards judge support, not retrieval. An unreadable source is `dead-link`, never `unsupported` | — | — |
| **`Stage 7: Adversarial Complete`** (Barrier) | Confirms the four lens reports + `CONTEXT-AUDIT.md` written, then completes — releasing `Stage 8: Conclusion` | The four lens merges + the citation-audit merge | — |
| **`Stage 8: Conclusion`** (Begin) | Stage 8 spine. Creates the three conclusion cards in fixed order — no data-dependent fan-out | Released when `Stage 7: Adversarial Complete` completes | Reconcile → Assemble → Adversarial review of the brief |
| ↳ `rx.py analyze-conclude` | Execs `fanout.py` to create the reconcile → assemble → devil chain | — | the three conclusion cards |
| **`Reconcile adversarial verdicts`** (rx-verify) | Resolves disagreements between the lenses; a claim survives only if its citation passed the audit **and** no lens left a `fatal` (or un-narrowed `serious`) finding. Ingests `efficacy-*.md` alongside the research reports — any "expected to move X" claim is audited like any other, while observed values stand on lab confirmation, not citation | The Stage 8 Begin | — |
| **`Assemble prescriber discussion brief`** (rx-verify) | Writes `<date>-rx-review.md`, including what the review did **not** cover (from `coverage.md`): markers excluded by `--ignore`, items the user dropped at the regimen review, and a **Medication efficacy** section carrying each dated substance's before/after findings — with any **"TOO EARLY TO TELL"** verdict verbatim | The reconciler | — |
| **`Adversarial review of the brief`** (rx-devil) | Final hostile pass over the finished product; a parent of `Stage 8: Conclusion Complete` | The assembler | — |
| **`Stage 8: Conclusion Complete`** (Barrier) | Confirms `<date>-rx-review.md` produced, then completes — the run is done | The brief's adversarial review | — |
| **`rxkanban.py`** | Kanban mechanics: create, announce, subscribe. Library for `rx.py` and `fanout.py`. Every card is a separate `hermes kanban create` subprocess, so creations are PACED — `CREATE_DELAY_S`, 1s between them (`RX_CARD_CREATE_DELAY`, 0 disables). A burst of 86 unpaced creations tore the board's SQLite one page short of its own header on 2026-08-11; the cost is (N-1)x1s per fan-out | Imported | — |
| **`terminal-pipeline-only.sh`** | Hook. Holds this board's `terminal` to an allowlist, scoped by `HERMES_KANBAN_DB` | Every terminal call on this board | — |
| **`rx.py status` / `doctor` / `labs-report`** | `status` answers "what is happening, and what happens next" in ONE ranked headline from `pipeline_state()` — a card held for the user outranks everything and is never truncated away, and the headline names the verb that routes their reply; `--detail` adds the old inputs/cache/board/reports dump. `doctor` explains a held card by asking the board what is `blocked`, not by matching card titles. `labs-report` is the readable out-of-range list | the model on every "how is it going", and a person | — |
| **`rx.py reset`** | Empties the board and inputs; keeps the board, `salvage/`, `archive-*/`, the transcription cache unless `--clear-cache`, and the web-access fetch cache unless `--clear-web-cache`. Also the recovery from a halt | A person | — |
| **`repair_db.py`, `rx_repair.py`** | **Empty tombstones, kept on purpose.** Both held scripts that opened the board's SQLite read-write to rebuild it. The board is live — the dispatcher and both gateways hold handles while a review runs — so rebuilding it over them discards their writes, which is how the board was corrupted repeatedly. A corrupt board is REPLACED (`hermes kanban boards rm` archives it, `boards create` remakes it, then restart the gateways), never patched. Deleting the files was tried; the script was rewritten twice more, under two names. `rx_test.py` now fails the build if any module here opens that database read-write | nothing runs them | — |
| **`cardmap.py`** | Generates the card map below; `--check` fails when it is stale | CI and commit | — |
| **`cardstats.py`** | Per-card peak context and inferred compactions, from litellm's logs | A person | — |
| **`provision-profiles.py`** | Builds the nine `rx-*` profiles. Profile directories are gitignored, so this is the only durable record | A person | — |
| **`rx_test.py`, `card_command_test.py`, `test-terminal-pipeline-only.sh`, `provision_profiles_test.py`** | Parser, regimen-intake/batched-review and sharding tests; every card command against the allowlist; the hook's 19 escape cases; profile config | CI and commit | — |

The per-chunk lens and audit cards are generated at runtime — how many there are depends on how
many chunks the reports pack into — so they carry no fixed titles and do not appear in the
generated card map. The role names above are the only names they have.

---

## Stage by stage

### Intake

Stages 2, 3 and 4 — `rx.py intake-regimen`, `rx.py intake-regimen-items`, `rx.py intake-labs` —
behind stage 1's `rx.py stage` / `rx.py start`, and stage 5's `rx.py review_labs` after them. Each
stage's Stage Begin card was created up front by stage 1 and is released when the Barrier ahead of
it completes (`Stage 4: Transcribe Labs`, the labs branch head, has none and is eligible at once),
so the order is an edge in the graph and nothing has to check whether another branch has finished. Each command is **idempotent**: it creates its full worker set every run, and the
idempotency keys mean the same inputs return the same cards, so re-running one is how the pipeline
recovers rather than something to avoid.

Per-card detail is in **Every card and script** and the canonical per-stage sections above. What
follows is what neither covers: how intake behaves when an input is thin, and when thin becomes
absent.

| Stage | Refuses when | `--force`? |
|---|---|---|
| 1 `stage` | anything is still unstaged after the copy | no — the re-scan is the whole point |
| 1 `stage` | nothing at all was received | no — an empty inputs set is a failed upload |
| 1 `start` | anything unstaged, or zero staged PDFs | no |
| 1 `start` | no regimen resolved — no `regimen.txt` | no |
| 1 `start` | no ingested patient information — no `patient.md` (`NO PATIENT`) | no |
| 1 `start` | the user has not run `uploads-done` — nobody has said the labs are complete | no |
| 1 `start` | a lab arrived AFTER that confirmation, so it no longer covers the staged set | no |
| 2 `intake-regimen` | no `regimen.txt` | no — the backstop for a card reached out of order |
| 2 `intake-regimen` | the regimen does not fit in a card body (8KB) | no — a regimen that large is the wrong document |
| 4 `intake-labs` | a received document is unstaged | **yes** |
| 4 `intake-labs` | zero staged lab PDFs | no — nothing to force |

Stage 3 has no such refusal: it works on whatever stage 2 produced, and stage 2 already refused
if there was nothing. Stage 5 reviews whatever markers stage 4 transcribed, and the research
stage works from the finalized regimen and reviewed labs its predecessors already guaranteed.

**Why an absent input is an error and not a shorter review.** This pipeline exists to reason
about substances *against* lab markers. Two of the three research families are keyed on markers,
the safety part of every substance card reads `labs-succinct.md`, and both whole-regimen screens
come from the regimen — so the brief's entire claim to usefulness is that it connects the two
halves. A run missing either half would complete, produce a document that looks exactly like the
real output, and silently be missing the thing that justified it.

It is also almost never what the user meant. The overwhelmingly likely cause of an empty lab set
is that staging did not pick the documents up, which is precisely the failure stage 1 was split
out to catch. Failing loudly sends them back to re-upload; completing quietly hands them a brief
they have no reason to distrust.

These refusals are a count taken before anything is created, not a rescue attempted afterwards:
the stage returns non-zero naming what is missing and where it looked. No transcribed labs means
the `Stage 4: Labs Transcribed` Barrier never completes, so nothing downstream is released, and
the run stops at the stage that found the problem rather than somewhere further on that would have
to explain it.

**Why the refusals live at the top of a stage and not in a card.** A stage that refuses before
creating anything leaves a board with no cards below it, which is a state the graph itself
expresses. A stage that created its cards and let each one discover the problem would produce N
cards all reporting the same missing input, and the run would look like it was working.

**Why staging is its own stage.** It used to be a per-attachment `cp` run by the assistant, so a
missed attachment silently halved the labs — and a PDF that arrives after transcription has been
planned is a panel the report quietly omits. Why stage 1 alone can answer "is this everything" is
the stage-1 exception, above; `intake-labs`' unstaged check is the backstop for a stage 1 that was
skipped or run out of order.

**Why the merge is deterministic.** It was a model holding 140KB of tables in context, peaking at
98k tokens. `rx.py merge-labs` does the same work as code.

#### Building the regimen (stage 2) and settling it (stage 3)

The mechanics are canonical — *Stage 2 in detail* and *Stage 3 in detail*, above. What belongs
here is the why:

**Why two stages rather than one.** Stage 2 turns what the user supplied into a structured draft;
stage 3 resolves what that draft could not pin down. Resolving requires the network —
`Regimen Intake:` is the intake card on `rx-research` — and reading and settling are separated by
per-item fan-out, so they are separated by a stage.

**Why stage 2 refuses an empty regimen** before creating its worker: the same argument as the labs
refusal, in the other direction — see *Why an absent input is an error*, above. Both halves have
to be there, and the cheapest place to say so is before any card exists.

**Why per-item files.** Stage 3's workers run in parallel, and a shared `regimen-final.md` they
all appended to used to clobber each other's writes and stall the barrier; one file per item, each
worker its sole writer, removes the contention.

**Why every item gets a lookup, and none blocks.** Given a brand and product the LLM can read a
missing dose off the manufacturer's Supplement Facts panel — so a blank dose is a lookup, not a
question. That the worker can never message the user is what stops an interrupted lookup from
stalling the barrier invisibly.

**Why the whole regimen is reviewed, not just the doubtful items.** A lookup can go wrong without
lowering its own confidence; showing every Name / Ingredients / Schedule is what lets the user
catch that.

### Human input: the batched barrier review

The pipeline asks the user exactly two batched questions: the `Stage 3: Finalize Regimen` review
and the `Stage 5: Labs Complete` review. The mechanics — one numbered message, the barrier
blocking its own card `needs_input`, the finish verb completing it — are canonical (*Pipeline
Stages* and *A Barrier releases; it does not gate a human*, above).

**`blocked` is reserved for a card genuinely waiting on a person.** A card merely waiting on
another card expresses the dependency as an edge and steps back into `todo`, never blocks. Hermes
will not auto-promote a card a worker blocked itself (upstream NousResearch/hermes-agent#40312:
sticky blocks are not cleared when parents complete), so a card that blocks to wait stays blocked
until it is cleared. The cards that reach `blocked` on purpose are the stage-3 and stage-5
barriers — each cleared by its finish verb (`approved` / `labs-accept`), never by unblocking it —
and the Stage 6 backstop when its re-verification fails.

**Every hold posts to chat.** `needs_input` reaches nobody by itself: cards on this board are not
subscribed, so a hold used to sit on the board until somebody happened to look, and a stopped
pipeline was indistinguishable from a slow one. `_hold()` therefore posts one message naming the
stage, the reason and the repair. It REPORTS; it does not ask — the repair is an operator action,
and the two batched barrier reviews remain the only questions the pipeline puts to a human.

REPLACEMENT TEXT:
It REPORTS; it does not ask. A hold holds its card and tells the user what is needed — the repair is an operator action to perform, not a decision to make — so the two batched barrier reviews remain the only questions the pipeline puts to a human.

**Model-facing output never says "you" for the human.** Every script's stdout has exactly one
reader — the model — so it addresses that reader in the imperative and calls the human *the
user*, in the third person. This is not style. A hold that reads "BLOCKED for you to fix" tells
the model to fix what only a person can, and the dangerous resolution is not a stall: a model
that answers a regimen or a marker review on the user's behalf puts an approval nobody gave into
the brief. The rule is enforced mechanically — `rx_test.py` fails the build on a bare `you` in
any printed string — because it is the kind of thing that reads fine to whoever writes it.

**The two reviews are not symmetric.** The regimen review ALWAYS runs — the whole regimen is shown
for a final check even when every item came back high-confidence — because a lookup can go wrong
without lowering its own confidence, and only the user can catch that. The marker review only runs
when stage 5 flagged something: with clean labs it raises no questions and the barrier writes
`labs-succinct.md` and completes on its own. A run whose regimen is entirely uncontroversial still
gets its review; a run whose labs are entirely in range does not.

#### How a question is presented and answered

Both reviews work the same way; only the content differs.

**The reason is a summary, the batch is the evidence.** Block reasons are truncated to 160
characters before the chat adapter ever sees them, so the numbered review is posted as a normal
chat message (`send_detail`), not carried in the block reason. The regimen review lists every item
with its Name, Ingredients (the active ingredients and their dose) and Schedule, plus its
confidence; the marker review lists every out-of-range marker with its value and reference range.
Each review is numbered — the regimen from `regimen-final.md`'s own row numbers, the markers from
`marker-batch-index.md` — so an answer by number is deterministic.

**Every answer is a command, and the command completes the card.** The user answers in chat and the
worker turns that into the verb. A model left to invent the state transition summarises the
conversation and moves on, and the card sits blocked forever with everyone believing it was
settled. Completion is what the dependency graph waits on.

**Names are matched against exactly the strings the body listed**, case- and
whitespace-insensitively. Nobody should have to know how something is spelled in `regimen-draft.txt`
or `labs-draft.md`, or what slug it maps to. A name matching nothing refuses the whole command,
with the closest entries named; nothing is recorded and the card stays open. Recording the part
that matched would leave the user believing a multi-item answer landed when one item of it went
nowhere. An answer may be more specific than the item; it may never be a guess at which item was
meant.

> **Never unblock one of these cards to resolve it.** Unblocking re-runs the card, which asks the
> same question, blocks again, and lands in triage. Use the verb. `rx.py doctor` explains why a
> card is still waiting — which answers are on record and how each was matched.

#### What a halt does

Either question can be rejected, and a rejection ends the review rather than changing it. The
reject verbs do the same four things:

- **Write the record** — with the user's reason and, where there is one, a fingerprint of the
  artifact being rejected, so it is tied to exactly what was refused.
- **Archive every open card, including the one being answered**, so nothing dispatches. Halting has
  to remove the cards. A flag that each card checks is a halt that runs for as long as it takes
  every in-flight card to notice.
- **Make the rejected reading unrepeatable.** The derived artifacts move to `salvage/` — moved, not
  deleted, so the evidence of what went wrong survives while nothing downstream can pick them up as
  current. A rejected transcription additionally drops its transcription-cache entries: the cache
  is content-addressed, so it would otherwise replay the rejected reading no matter where the file
  went. Its entries are admitted only after verbatim verification, and a rejection withdraws it.
- **Keep what the pipeline did not produce** — `regimen.txt` and the lab PDFs the user supplied,
  and any manufacturer panel, which came from a source other than the reading being rejected.

The review does not resume. `rx.py status` reports the halt, so a halted board is never mistaken
for a finished or a hung one; `reset` clears it, and a corrected input starts a new run at stage
1.

#### Answering the regimen review

The `Stage 3: Finalize Regimen` barrier shows the WHOLE regimen as one numbered list and blocks
until the user replies `approved`. Every item is on it — Name, Ingredients (the active ingredients
and their dose), Schedule, and the worker's confidence:

```
1. Thorne Super EPA — EPA 425mg, DHA 270mg (per 2 gelcaps) — morning — high
2. Vitamin C — ascorbic acid 1000mg — morning — low
```

**Correct by number.** `<n> <correction>` — e.g. `2 Now Foods Vitamin C, 1000mg, evening`. The
routing — the script reads the leading number, the LLM merges one line, the script validates and
replaces it — is canonical: *Stage 3 in detail*, above. A low-confidence line and a
high-confidence one are corrected the same way — confidence only tells the user where to look
hardest. **Drop** with `<n> drop` for a bottle thrown out or an item the user cannot confirm: the
dropped item is kept out of Stage 6 research — no `Research: <substance>` card — and listed as not
covered in the brief. A substance with no settled dose cannot be reasoned about: every research
part asks dose-dependent questions, and answering them against a guess produces a confident brief
about a regimen the user does not have.

**Finish** with `approved` to complete the barrier — releasing Stage 5's review step and feeding
the Stage 6 join. There is no separate verification gate: confidence does not block acceptance,
and an item the user is unsure of is either corrected or dropped, not held. `regimen-final.md`
already exists — `gather-regimen-slugs` wrote it when it posted the review — so `approved` only
has to complete the card.

Dropping it must not be silent. `fanout.py` records what it skipped as it skips it and writes
`inputs/coverage.md`; the assembler reproduces that as a section of `<date>-rx-review.md` headed
*what this review did not cover*, and writes "Nothing was excluded" when the list is empty. A
missing section and a section saying nothing look identical to a reader, and only one of them is
true. The record is what was actually skipped, not a second derivation of what should have been —
two implementations of "what was excluded" would eventually disagree, and no adversarial lens can
catch it: an excluded subject has no child report for the omission check to find missing.

**Reject** when the draft is wrong in a way answering cannot fix — the reading captured the wrong
products, a substance is in there that the user does not take, half the regimen is missing.
`regimen-draft.txt` is what moves to `salvage/`; see *What a halt does* above.

#### One question at a time

The graph guarantees there is only ever one batched review outstanding: `Stage 5: Review Labs`
waits on the Stage 3 Barrier as well as Stage 4's, so the marker review cannot be posted while the
regimen review is open — transcription runs in parallel with the regimen review, but the next
question does not. And the marker review is posted only after transcription, so the user is never
asked about a value before it exists. Within a stage the whole list is answered at once — one
numbered message, one accept — rather than one card per item.

The regimen review in particular is posted only after the product panels have landed — the
mechanics are in *Stage 3 in detail*, above — so the user is
never asked for a number a `Regimen Intake:` worker is about to read off a manufacturer's label.
One review is posted, once, at the point where it is genuinely the user's to answer.

#### Two variables, four fan-outs

Two things vary between runs: whether any regimen item came back low-confidence, and whether any
marker is out of range. They change what the user is likely to correct and what the research stage
contains, never the order the stages run in. Unlike the old design, the confidence one no longer
changes the regimen review itself: the whole regimen is shown for correction either way, confidence
is informational, and the user corrects, drops, or `approved`s regardless.

| | low-confidence items | markers out of range | regimen review | marker review | research fan-out |
|---|---|---|---|---|---|
| **A** | none | none | whole regimen; correct / drop / `approved` | none — barrier self-completes | substances + any trends |
| **B** | none | some | whole regimen; correct / drop / `approved` | numbered out-of-range list | substances + markers + trends |
| **C** | some | none | whole regimen; correct / drop / `approved` | none — barrier self-completes | substances + any trends |
| **D** | some | some | whole regimen; correct / drop / `approved` | numbered out-of-range list | substances + markers + trends |

`Trend:` cards are keyed on a marker moving consistently in one direction, not on being out of
range, so they are possible in all four — a trend is analysed in stage 6 whether or not any reading
crossed the line, and it is never questioned in the marker review. The out-of-range count changes
one thing — whether `fanout.py` creates a `Marker:` family — and that decision is made after the
marker review has been accepted.

**What is the same in all four.** No card ever blocks to wait for another card — in these four
scenarios the only cards that reach `blocked` are the stage-3 and stage-5 barriers, each cleared
by its finish verb (`approved` / `labs-accept`) rather than by unblocking. The regimen review is identical in every
row — the whole regimen, corrected or dropped by number, then `approved`. The order in which the
user answers is never a variable: they cannot answer the marker review before stage 4 has
transcribed the labs.

**Any of the four can end early.** A rejection at either question halts the review where it stands,
archiving every open card. That is not a fifth ordering — it is one of the four above, truncated.

**And none of the four is reachable without both halves of the input.** A run with no lab PDFs is
not a fifth scenario, it is an error refused in stage 4; a run with no regimen source is refused
in stage 2. Neither produces a shorter review. The refusals and the reasoning behind them are in
*An empty case is not the same as an empty run*, above; what matters here is that "the labs were
empty" and "the regimen was empty" never appear as branches in this table, because the pipeline

REPLACEMENT TEXT:
**And none of the four is reachable without both halves of the input.** A run with no lab PDFs is
not a fifth scenario, it is an error refused in stage 1; a run with no regimen source is refused
in stage 1; a document with no fact lines at all is refused in stage 1 (`NO PATIENT`). None
produces a shorter review. The refusals and the reasoning behind them are in
*Why an absent input is an error and not a shorter review*, above; what matters here is that "the labs were
empty" and "the regimen was empty" never appear as branches in this table, because the pipeline
does not get that far.

### Adversarial review

`Stage 7: Adversarial Review`, released by `Stage 6: Research Complete`, runs
`rx.py analyze-adversarial`. It packs the reports into chunks that fit the model window
(`lenses.py`, capped at `LENS_BUDGET_CHARS`), then fans out two tracks that never read each other.
The first is **chunk × lens** — every chunk is examined by every lens, one card per (chunk × lens),
and each lens has one merge card, parented by that lens's own chunk cards, that concatenates its
per-chunk findings into that lens's single report:

| Lens | Profile | Asks | Writes |
|---|---|---|---|
| `logic` | rx-logic | Attack the reasoning — not the conclusion | `LOGIC.md` |
| `counter` | rx-redteam | Find evidence the reports ignored or contradict | `REFUTATION.md` |
| `overreach` | rx-logic | Compare each claim's strength against its support | `OVERREACH.md` |
| `nullhyp` | rx-nullhyp | Argue for changing nothing; steelman the status quo | `NULLHYP.md` |

`nullhyp` exists because the pipeline is structurally biased toward finding something to say.

The second track is a **citation audit**, asking two things of every endnote: does the cited
text exist in its source, and does it support the use the report made of it. `verify.cmd_build`
resolves each source through the web-access fetcher and locates the quoted sentence; the
per-chunk workers then judge the located text against the claim — `supported`,
`context-reversed`, `scope-mismatch`, `overstated`, `misquoted`, `unsupported`, or `absent` —
and a source that could not be read is `dead-link`, never `unsupported`, because calling an
unread page unsupported accuses the report of inventing a citation on the strength of our own
network trouble. A **citation-audit merge** — the audit track's counterpart to each lens's
merge, gated on all the per-chunk audit cards — concatenates their results into `CONTEXT-AUDIT.md`.

The severity scale, the barrier wiring, and Stage 8's reconcile → assemble → devil chain carry no
detail beyond their canonical sections (*Pipeline Stages*, Stages 7–8).

---

## Artifacts

Everything under `inputs/` is the user's data or derived from it; everything a run produces goes to
its own timestamped output directory.

**Each invocation gets `~/.hermes/reports/rx-review/<YYYY-MM-DD-HHMMSS>/`.** `rx.py start`
(`start_run()`) creates it at Stage 1 — the single writer, before any parallel card exists — and
points a `current` symlink in the parent at it, swapped atomically. Every stage resolves its output
dir (`REPORTS`) through `current`, so all eight stages' worker processes write into the SAME run
dir; the `reports/…` paths in the table below are relative to it. Past run dirs are the deliverables
and are KEPT across `reset` (which only drops the `current` pointer); only `reset --clear-reports`
purges the history. At Stage 8 the conclusion snapshots the run's inputs (the regimen and the lab
transcriptions) into `<run>/inputs/`, so each timestamped dir is a self-contained record — the caches
(transcriptions, verdicts, fetched pages) are content-addressed and never copied in.

| File | Written by | Read by | Authoritative? |
|---|---|---|---|
| Patient document | the user | Stage 1 | **yes** - everything in it is authoritative |
| `inputs/regimen.txt` | `rx.py regimen`, at ingest - materialized from medication and supplementation lines | stage 2 | no |
| `inputs/patient.md` | `rx.py patient`, at ingest — materialised from the fact lines (`Name:`, `DOB:` and `Age:` — `Race:`, `Sex:`, and medical conditions are not yet extracted) of the patient's single input document; the document is the surface, this file is what the pipeline reads from | the age-weighted scores (`rx.py fib4` today; any later score that needs the patient) | **yes** — the patient facts as recorded in the document: they re-derive from it on every ingest, so a fact the document no longer carries is gone from this file after the next ingest, and a document with no fact lines at all leaves no file at all |
| `inputs/*.pdf` (labs) | the user | `rx.py plan-lab` / `check-transcription` — transcription children never open a PDF | **yes** |
| `inputs/product-<slug>.md` | `Regimen Intake:` | regimen settling, `doctor` diagnosis | no |

REPLACEMENT TEXT:
| `inputs/regimen-draft.txt` | `Worker: Read regimen` (stage 2) | stage 3's `Regimen Intake:` workers | working draft (pipe-delimited `product | brand | quantity | schedule | started`) |
| `inputs/regimen-item-<slug>.md` | `Regimen Intake:` (one per item, its sole writer) | `gather-regimen-slugs`, which combines them into `regimen-final.md` | per-item settled row (Name · Ingredients · Quantity · Schedule · Started · Confidence) — avoids the parallel-write clobber a shared file caused |
| `inputs/regimen-final.md` | `gather-regimen-slugs` (the barrier); corrections replace lines in place | every research card | **yes** — the settled regimen |
| `inputs/LABS-REJECTED.txt` | a halt (marker reject) | `rx.py status`, `doctor` | **yes** — why the review was halted, and against which `labs-draft.md` |
| `inputs/REGIMEN-REJECTED.txt` | a halt (regimen reject) | `rx.py status`, `doctor` | **yes** — why the review was halted |
| `salvage/` | a halt | a person, diagnosing | the rejected reading, kept as evidence — survives `reset` |
| `inputs/labs-<slug>[-<window>].md` | `rx.py check-transcription`, from a `Transcribe Lab` card's verified rows | the barrier's merge | no |
| `.xcribe/<token>.check.log` | `check-transcription`, one per window | a person, inspecting a rejected row | no — per-row verdicts |
| `inputs/.xcribe/<token>.json` | `intake-labs` (one per document) and `plan-lab` (one per window) — ONE writer each, never a shared file | `plan-lab`, to learn its document; `check-transcription`, for a window's source and destination | working — the run's token bindings; swept by `reset` |
| `inputs/.uploads-done.json` | `rx.py uploads-done` — the set of documents the user called complete | `rx.py start`, which refuses without it or if a document arrived since | **yes** — the person's "that is all the labs" |
| `inputs/labs-draft.md` | the `Stage 4: Labs Transcribed` barrier (`merge-labs`) | `review_labs`, `labs-report` | **yes** — full transcription with provenance |
| `inputs/labs-complete.md` | `review_labs` (seeded from `labs-draft.md`), annotated by `marker-review` | **all scripts** — `check_labs`, `out_of_range_entries`, `trends`, `labs-report` | **yes** — full provenance plus review decisions |
| `inputs/marker-question-<slug>.md` | `review_labs` (one per out-of-range marker; deleted by `marker-review` / `labs-accept`) | the `Stage 5: Labs Complete` barrier | working — the still-unreviewed markers |
| `inputs/marker-batch-index.md` | the `Stage 5: Labs Complete` barrier (`rx.py labs-brief`) | `marker-review --number` | the stable number → marker map for the marker review |
| `inputs/labs-succinct.md` | the `Stage 5: Labs Complete` Barrier (`labs-brief` / `labs-accept`) | **cards only** | no — significant-marker view |
| `inputs/coverage.md` | `fanout.py`, as it skips | the assembler | **yes** — what the review did not cover |
| `reports/PART-*.md` | research parts | only that topic's synthesis card | no |
| `reports/substance-*.md` (6a), `efficacy-*.md` (6a, dated substances only), `marker-*.md` (6b), `trend-*.md` (6c) | per-subject synthesis cards; the efficacy card-set runs the mechanical before/after comparison for substances with a start date | 6d screens, lenses, audit, reconciler | yes |
| `reports/interactions.md`, `reports/SCHEDULE.md` | the 6d whole-regimen screens (`SCHEDULE.md` only when the regimen records dose times) | lenses, audit, assembler | yes |
| `reports/LOGIC.md`, `REFUTATION.md`, `OVERREACH.md`, `NULLHYP.md` | lens merges (Stage 7) | reconciler | yes |
| `reports/CONTEXT-AUDIT.md` | the citation-audit merge (Stage 7) | reconciler | yes |
| `reports/<date>-rx-review.md` | assembler | rx-devil, the user | **the output** — including what it did NOT cover: markers the user asked to ignore and items dropped at the regimen review |
| `reports/inputs/` (regimen + transcriptions) | Stage 8 conclusion (`_snapshot_inputs`) | a person reading the run later | the run's input snapshot — makes the timestamped dir a self-contained record |

### labs-complete.md vs labs-succinct.md

`labs-complete.md` carries a `source file` column — the PDF each value came from — and every
marker the labs contained, annotated with what the marker review decided. No card reasons
about the source column, but three things do: the marker review checks a value against the real
PDF, `review_labs` uses the column to tell two draws apart, and the analysis method is sometimes
only visible in the filename. That provenance is why every script reads `labs-complete.md`.

`labs-succinct.md` is the subset the `Stage 5: Labs Complete` Barrier copies out — the markers the
reviews kept as significant, without the `source file` and `confidence` columns — small enough to
fit `file_read_max_chars` where the full file did not. It is what the research cards read, because
a card reasoning about a substance needs the markers that matter, not the whole panel history.

`specimen` is **kept**, though it looks like panel-name noise. GLUCOSE on one date is `87` in the
metabolic panel and `NEGATIVE` in the urinalysis. Dropping it would merge a blood value with a
urine one under a single marker name.

---

## Profiles

Nine profiles, each a separate Hermes install with its own model config, toolsets and hooks.
Provisioned by `~/.hermes/provision-profiles.py` — profile directories are gitignored, so that
script is the only durable record.

| Profile | Toolsets | Runs |
|---|---|---|
| `rx-intake` | file, terminal | all Stage Begin cards, intake and review workers, merges, barriers, splits |
| `rx-intake-vision` | file, terminal, vision | **unused** — the regimen is text-only now; the profile is retained but no card runs on it |
| `rx-research` | file, terminal | regimen item lookups, all research parts and syntheses |
| `rx-audit` | file, terminal | citation audit |
| `rx-verify` | file, terminal | reconcile, assemble |
| `rx-nullhyp` | file, terminal | the null-hypothesis lens |
| `rx-redteam` | file, terminal | the counter-evidence lens |
| `rx-logic` | **file** | the logic and overreach lenses |
| `rx-devil` | **file** | final hostile review |

**No profile has a network toolset.** `web`, `search`, `browser` and `x_search` are removed —
the built-in backend auto-selects from whatever API keys are in the environment and ranks a paid
provider first, which took out an entire research stage when a stale key outranked the local
stack. Network access is the `web-access` skill, run through `terminal`.

`rx-logic` and `rx-devil` get **no shell at all**: they reason over reports already written and
have nothing to fetch.

Both `toolsets:` and `platform_toolsets.cli:` must be set. A worker resolves its tools from the
latter; setting only the obvious one produces a config that reads correctly and behaves
otherwise.

### Why `terminal` is safe here

`~/.hermes/hooks/terminal-pipeline-only.sh` holds this board's `terminal` to an allowlist:

```
python3 ~/.hermes/rx-review/{rx,rxsplit,fanout,lenses,verify}.py ...
python3 ~/hermes-skills/web-access/scripts/web_access.py search|fetch ...
python3 ~/hermes-skills/browse-task/scripts/browse_task.py ...
```

It scopes itself by reading `HERMES_KANBAN_DB`, so it restricts this board and nothing else on
the machine. It must be registered in each **profile** config — the global one is not what a
worker reads. `hooks/test-terminal-pipeline-only.sh` covers 19 cases including the escapes that
defeated earlier versions; `card_command_test.py` checks the other direction, that every command
a card instructs is one the allowlist permits.

---

## Card map

<!-- BEGIN GENERATED CARD MAP -->

| card | runs as | waits on | runtime | defined in |
|---|---|---|---|---|
| `%s: %s — part %d/%d` | rx-research | — | {PART_RUNTIME} | `fanout.py` |
| `%s: %s — report` | rx-research | (computed) | {SYNTH_RUNTIME} | `fanout.py` |
| `(computed)` | rx-intake | {begin_parents} | 20m | `fanout.py` |
| `(computed)` | rx-intake | (computed) | 15m | `fanout.py` |
| `Adversarial review of the brief` | rx-devil | (computed) | 45m | `fanout.py` |
| `Assemble prescriber discussion brief` | rx-verify | (computed) | 45m | `fanout.py` |
| `Efficacy: %s (started %s)` | rx-research | (computed) | {SYNTH_RUNTIME} | `fanout.py` |
| `Interaction and timing screen: full regimen` | rx-research | {me} | 60m | `fanout.py` |
| `Reconcile adversarial verdicts` | rx-verify | {me} | 60m | `fanout.py` |
| `Schedule review: current vs evidence-based timing` | rx-research | (computed) | 60m | `fanout.py` |
| `Trend: %s — dispatch` | rx-intake | (computed) | 15m | `fanout.py` |
| `Trend: %s — part %d/3` | rx-research | — | {PART_RUNTIME} | `fanout.py` |
| `Trend: %s — report` | rx-research | {synth_parents} | {SYNTH_RUNTIME} | `fanout.py` |
| `Trend: %s — triage` | rx-research | — | {PART_RUNTIME} | `fanout.py` |
| `(computed)` | rx-intake | {bparents} | 20m | `rx.py` |
| `(computed)` | rx-intake | (computed) | 15m | `rx.py` |
| `Lab: %s` | rx-intake | — | 20m | `rx.py` |
| `Regimen Intake: %s` | rx-research | — | 4m | `rx.py` |
| `Transcribe Lab %s` | rx-intake | (computed) | {_mn}m | `rx.py` |
| `Transcribe Lab %s (part %d)` | rx-intake | (computed) | {_mn}m | `rx.py` |
| `Worker: Read regimen` | rx-intake | (computed) | 90m | `rx.py` |

_21 card types. Generated by `cardmap.py`; do not edit by hand._
<!-- END GENERATED CARD MAP -->

---

## Re-run semantics

An intake stage can run more than once per review — a later upload or a corrected input sends the
pipeline back through one. What stops it duplicating work:

**Idempotency keys.** Every card carries one; creating a card with a key that already exists is a
no-op. Keys are `stable_key(prefix, *parts)` — a SHA-1 of the inputs, never Python's `hash()`,
which is salted per interpreter run and therefore produced a new key, and a duplicate card, on
every invocation.

**Staleness.** A card is created when its output is missing or older than its inputs. A corrected
regimen legitimately rebuilds the draft — the correction belongs in it. The `Worker: Read regimen`
card carries the regimen text in its body, so it is keyed on a digest of that TEXT rather than on
a file's mtime: a corrected regimen is a different card, an unchanged one is a free no-op, and a
card whose body disagrees with `regimen.txt` cannot occur. The lab-side artifacts are deliberately
excluded from that set: reviewing lab results should not rebuild the regimen.

A corrected or extended patient document is re-ingested the same way: `rx.py regimen` generates the regimen document, and `inputs/patient.md` is regenerated based on the current document — the document remains the single source of truth for the facts. A document that drops its fact lines results in a `patient.md` missing those facts.

**Phases.** `.phase.json` records which stage has announced itself, so re-running a stage does
not re-announce. `phase_start()` is idempotent by design.

### A halted review is the exception

Everything above is about a review that is still running. A rejection ends one — *What a halt
does* covers the mechanics; what matters here is that re-running a stage is **not** the recovery.
There is no graph left to re-enter, so running one by hand builds a second one beside the
wreckage.

The recovery is: read the reason, fix the input it names, `reset`, start again at stage 1. That
`reset` clears `inputs/` is what removes the rejection record, and it has to: a rejection
surviving into the next review would report a halt that had already been dealt with, and
`status` would describe a healthy board as dead.

`salvage/` survives `reset` on purpose. It is where a halt puts the artifacts of the reading that
was rejected, and the reason for the rejection usually has to be found in them — after the board
and the inputs that produced them are gone.

---

## Scripts

| Script | Does |
|---|---|
| `rx.py` | The driver. The intake and review stages — `intake-regimen`, `intake-regimen-items`, `intake-labs` / `plan-lab`, `review_labs` — the regimen and marker review answers, the three analysis phases (`analyze-research` / `analyze-adversarial` / `analyze-conclude`), halt, reset — `rx.py --help`. |
| `fanout.py` | Builds the research + adversarial graph. Card bodies are templates here. |
| `lenses.py` | Packs reports into window-sized chunks and defines the four lenses. |
| `verify.py` | Citation verification: locates quoted sentences in fetched sources. |
| `rxsplit.py` | PDF → text: furniture stripping, flattening to result lines, overlapping line-windows. |
| `rxfetch.py` | Binding to the `web-access` skill's fetcher. Not a copy — copies diverged. |
| `rxkanban.py` | Kanban mechanics: create, announce, subscribe. |
| `rxcache.py` | Verified transcription cache, content-addressed. |
| `cardstats.py` | Per-card peak context and inferred compactions, from litellm's own logs. |
| `cardmap.py` | Generates the card map above. `--check` fails when it is stale. |

---

## Knobs that matter

| Setting | Value | Where | Why |
|---|---|---|---|
| `rxkanban.CREATE_DELAY_S` | 1s | `RX_CARD_CREATE_DELAY` | Seconds between consecutive card creations. Each card is its own `hermes kanban create` subprocess — open the board, write, close. 86 of them back to back, while the dispatcher spawned workers and the dashboard polled, left the board one page shorter than its header claimed (a torn extend) and the burst died with `could not parse a task id from:`. Measured 2026-08-12: a create is ~0.26s end to end, of which only ~0.08s is the write and ~0.18s is interpreter start — so 1s is ~12x the write it separates. Costs (N-1)x1s per fan-out (an 86-card audit ~1.5 min, not 7); 0 disables, which is what the test suite uses. |
| `kanban.max_in_progress` | 4 | archivist profile + global | The backend serializes: latency scales ~linearly with in-flight requests. Six concurrent cards ran ~8x slower each and timed out; four is measured-better throughput. Read once at gateway start — **needs a restart**. |
| `model.context_length` | 200,000 | every rx profile | The host serves 240k (verified via /v1/models 2026-08-12). The estimator undercounts tool-heavy history by ~30%, so what matters is where compression fires: 0.6x200k = 120k estimated is ~171k actual, +24,576 output = ~196k against 240k. 44k headroom (was 61k at 180k; raised 2026-08-12). |
| `model.max_tokens` | 24,576 | every rx profile | Unset, workers request the model's 65,536 ceiling and blow the window. |
| `compression.threshold` | 0.6 | every rx profile | Compaction fires at `0.6 x context_length` = 120k estimated (was 108k at 180k). Every card that timed out had crossed it. Do not raise it without re-reading the undercount note above. |
| `file_read_max_chars` | 100,000 | global | `labs-complete.md` exceeded this and was silently truncated; `labs-succinct.md` fits. |
| Card runtimes | 25m parts, 30m synthesis | `fanout.py` | A card killed at its limit is retried, then blocked. |
| `dispatch_in_gateway` | true | archivist profile + global | With this false the board sits at `ready` and spawns nothing — indistinguishable from a hang. |

---

## Operating it

```
python3 rx.py stage                # copy received PDFs — after every upload round
python3 rx.py uploads-done         # the user says that is all the labs — `start` waits on this
python3 rx.py start                # stage 1: begin, once the labs are complete
python3 rx.py status               # one line: held / running / finished / halted, and what is next
python3 rx.py status --detail      # ...plus inputs, cache, board and reports
python3 rx.py intake-regimen       # stage 2, by hand: rebuild the regimen draft
python3 rx.py intake-regimen-items # stage 3, by hand: re-settle the regimen
python3 rx.py review_labs          # stage 5, by hand: re-review the markers
python3 rx.py doctor               # why is a barrier review still waiting, or why did the review halt?
python3 rx.py labs-report          # readable out-of-range list
python3 cardstats.py               # per-card peak context and compactions
python3 rx.py reset --confirm --clear-cache --clear-documents
python3 rx.py regimen --from <patient document>   # or --stdin / --from-gdoc <id>: records the
                                                  # patient's single document; the fact lines it
                                                  # carries (Name/DOB/Age) materialise to inputs/patient.md

REPLACEMENT TEXT:
python3 rx.py regimen --from <patient document>   # or --stdin / --from-gdoc <id>: records the
                                                  # patient's single document; the regimen part
                                                  # goes to inputs/regimen.txt
python3 rx.py patient --from <patient document>   # or --stdin / --from-gdoc <id>: materialises the
                                                  # fact lines it carries (Name/DOB/Age) to
                                                  # inputs/patient.md
python3 rx.py fib4                                 # the FIB-4 score, on demand: newest draw with
```

The regimen and marker reviews are answered in chat — the assistant turns the user's reply into
the answer verbs. The regimen review takes a correction per item (`<n> <correction>`, by number),
`<n> drop` to exclude one, and `approved` to finish; the marker review takes a request to ignore
markers by number (`marker-review`) and "looks good" to keep the rest significant (`labs-accept`);
either stage can be rejected to halt the review. Finishing completes the barrier, which releases
the stage waiting behind it.

`reset` empties the board and inputs but keeps the board itself, `salvage/` and `archive-*/`.
`--clear-documents` also discards what Hermes received, so the next run stages from nothing —
without it, a re-run re-stages the same uploads. The transcription cache survives unless
`--clear-cache` is given: its entries are content-addressed and were each admitted only after
verbatim verification, so re-deriving them costs cards and risks a different answer from the same
document.

`reset` is also how a halted review is cleared — see *A halted review is the exception* above.
Clearing `inputs/` is what removes the rejection record, and that record must not outlive the
review it describes.

---

## Tests

```
python3 rx_test.py                          # the parser, regimen intake/batched review, sharding
python3 card_command_test.py                # every card command passes the terminal allowlist
bash ~/.hermes/hooks/test-terminal-pipeline-only.sh
python3 ~/.hermes/provision_profiles_test.py
python3 cardmap.py --check                  # the card map above is current
```

All of these run in CI (`.github/workflows/test.yml`) and on commit.

**The chain tests assert ORDERING, not creation.** Asking whether a stage creates its successor
is what let two ordering defects reach production: both stages created their cards correctly and
gave them the wrong parents. `rx_test.py` therefore runs each stage command as a real worker —
`HERMES_KANBAN_TASK` set, `rxkanban.create_card` replaced by a stand-in board that dedupes on
the idempotency key exactly as kanban does — and reads back the `parents` of what came out. Four
things are asserted that source inspection cannot see: every boundary card names its creator as
a parent; nothing on the spine is created with an empty parent list; stage 3 creates one
`Regimen Intake:` worker per draft row and each stage's barrier posts one batched review and blocks
until it is finished; and stage 4 fans out one `Lab:` card per PDF, each of which creates its own
transcription child.

Note the scope: those runs all have `HERMES_KANBAN_TASK` set, so "nothing on the spine is created
parentless" is asserted **of a stage running as a card**. A hand run of `rx.py start` legitimately
creates `Stage 2: Read Regimen` with no parents — see the stage-1 exception above.

**This document is the specification; the tests are how it is held to it.** A behaviour described
here that no test asserts is a behaviour the next change can remove silently — a described
defence is not evidence of a defence. When a rule here is added or changed, its test belongs in
the same commit.
