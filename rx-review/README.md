# rx-review

Reviews a patient's medications and supplements against their blood-test history and
produces a cited discussion brief for their prescriber. It is not a diagnosis and it is
not a directive: it gathers evidence, adversarially verifies it, and hands it to the
prescriber — with a recommendation where the surviving evidence supports one, for the
prescriber to confirm.

## Inputs

Two things, both provided by the patient:

- **Patient profile** carrying the patient's identifying information and their full
  substance regimen (medications and supplements, with doses, schedules, and optionally
  date initiated - useful for detecting if they are having the desired effect).  The profile
  can be typed in chat or read from a source the agent resolves first (a Google Doc, a local file).
  This document is the single source of truth for the patient: everything the pipeline
  records about what the patient takes comes from it.  Currently the pipeline is run as a 
  single pass analysis, so a change to the profile means a re-run.  Future versions may
  add long term durable storage and incremental runs.
- **A set of lab PDFs** — the patient's blood tests across time. The pipeline transcribes
  every marker, value, and draw date out of them, which is what makes the before/after
  and trend work below possible.  Labs may be uploaded all at once or in a series of uploads.
  In either case the pipeline waits for the patient to confirm all labs are uploaded before
  proceeding with transcription and analysis.

## What the pipeline does

Eight stages. The regimen branch (stages 2–3) and the labs branch (stages 4–5) run in
parallel; each requires human review upon completion. Once both are complete, the 
research, adversarial review, and conclusion stages execute in sequence.

1. **Ingest.** Stage the lab PDFs; split the patient document into two parts — the
   regimen (what is taken) and the patient's identifying facts.
2. **Regimen read.** Read the regimen and look up each substance's product label, so
   doses and ingredients are pinned to a source rather than trusted from the document.
3. **Regimen review** — the patient reviews the settled regimen and corrects it.
   *Gate 1.* Nothing downstream may rely on a regimen the patient has not confirmed.
4. **Lab transcription.** Extract every value, marker, and date from every PDF and check
   the transcript against the source document, so a scan cannot quietly feed a wrong
   number into the analysis.
5. **Labs review** — the out-of-range values across the whole history are flagged in a
   batch for the patient's review. *Gate 2.* Nothing downstream relies on an out of range
   value until the patient confirms it.
6. **Research.** Per substance, per marker, and per trend: what the literature says,
   whether the substance plausibly moves the marker,
   and what the patient's own before/after data shows around its start date. Whole-
   regimen screens for interactions and schedule conflicts run alongside.
7. **Adversarial verification.** Independent hostile reviewers — logic, counter-
   evidence, overreach, status-quo — plus a citation audit that re-checks every quote
   against its source. Each claim survives with its citation, or is narrowed or dropped.
   Performed by agent instances that have entirely separate context and memory backends
   so as to avoid self-dealing / confirmation bias.
8. **Conclusion.** The surviving claims are reconciled into the dated brief, and a final
   hostile reviewer attacks the finished brief and records what it finds.

## Outputs

Everything lands in a per-run directory under `~/.hermes/reports/rx-review/<YYYY-MM-DD-HHMMSS>-<patient>`:

- **`<date>-<patient>-rx-review.md`** — the brief: the regimen as settled, per-substance evidence,
  interaction flags ranked by severity, the schedule as recorded with its conflicts,
  the before/after efficacy comparison from the patient's own labs, lab observations
  framed as hypotheses, and prioritized questions for the prescriber. Where the surviving
  evidence supports it, it recommends a dose change, a new drug, or a stop — each traced
  to a source that survived adversarial review. The patient takes the brief to their
  prescriber; the prescriber decides.
- **`<date>-<patient>-critique.md`** — the adversarial reviewers findings on the brief, so the 
  patient can see what was challenged and what held.

## Architecture

The pipeline is a kanban card DAG.  Wherever possible scripts are used to control and
check execution, with LLM agents only being used where necessary.  Each card, on completing,
creates the cards its result makes possible; barriers wait on their parents and release
the next stage only when that stage's output is actually on disk. It advances itself —
no step-running, no polling, no nudging — and it stops for a human at exactly the two
gates above. Everything else either completes or errors out and says why; nothing waits
silently.

The work is split between scripts and language-model workers:

- **Scripts** do the deterministic part — staging, extraction, transcription checks,
  date and trend arithmetic, the gate wiring. Their output is reproducible and tested.
- **Worker cards** do the research and review part — literature search, fetch, and
  report writing. Every factual claim in a worker's report must cite a page fetched
  during that run; a claim without a citation is dropped at the audit, so the brief can
  only rest on what was actually read.
- **Barriers** are the trust model: a stage is released by evidence of its outputs, not
  by a card's word that it finished.

The full specification — stages, cards, wiring, and the error model — is in
`ARCHITECTURE.md`. The agent-facing operating manual is `SKILL.md`. The code lives in
`scripts/`.
