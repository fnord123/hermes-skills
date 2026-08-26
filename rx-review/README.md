# rx-review — human notes

Reviews the user's medications and supplements against their blood tests and
produces a discussion brief for their prescriber. The work is done by a kanban
pipeline at `~/.hermes/rx-review/`; the skill is the human interface to it.

## Verb scope

SKILL.md used to open by listing all seven verbs the script accepts and then
saying "You only ever need `regimen`, `intake`, and `status`. The rest are run
BY the pipeline" — while two later sections told the model to run `verify-labs`
and `confirm`. A small model resolves a contradiction like that by concluding
every verb is fair game, which is the opposite of the intent.

The tools table in SKILL.md now lists exactly the five verbs the model may run
(`regimen`, `intake`, `status`, `verify-labs`, `confirm`) and says the rest
belong to the pipeline, without enumerating them.

For the same reason the `--force` mention was removed from the "Start the
research stage is blocked" section. Naming a dangerous flag, even to forbid it,
is how it gets used; the positive instruction (deal with the block reason, then
unblock — it retries itself) is what remains.

## Output

Reports land in `~/.hermes/reports/rx-review/`. The run is done when both
`BRIEF.md` and `CRITIQUE.md` exist.

The brief is evidence and questions for a prescriber or pharmacist to confirm.
It is not medical advice and nothing in it recommends a dose.

## FIB-4 (liver fibrosis risk)

`rx.py fib4` computes `(age * AST) / (platelets * sqrt(ALT))` and `labs-report`
surfaces it under **Derived scores**. Implementation notes:

- **Age is a property of the person, carried in the one input document.** The user
  keeps a single document — regimen lines plus a `Name:` / `Age:` / `DOB:` line.
  `_write_patient_facts()` runs at `rx.py regimen` ingest (all three routes, including
  the house JSON envelope) and materialises those fact lines into
  `inputs/patient.md`; `patient_age()` reads that file and returns `0` when absent.
  The document is the surface, the file is what the pipeline reads from — the same
  split the regimen itself has (`regimen.txt`). A re-ingest *replaces* the file, and
  it never *deletes*: a document that drops its fact lines leaves the last recorded
  age in place, because a stale, visible age beats a silent score. FIB-4 refuses to
  compute without an age rather than guess one — an invented age in a clinical score
  is worse than no score. The stage-2 worker prompt marks fact lines as NOT products
  so the transcriber cannot turn a `DOB:` line into a supplement row. The SKILL.md
  "If something fails" guard whitelists `inputs/patient.md` alongside
  `regimen.txt`/`CONFIRMED.txt` because a re-ingest of the document is the supported
  way to change it.
- **One draw only.** The score is computed from the newest draw that reports
  AST, ALT and a platelet count *together*; it is never stitched across draws.
  This is deliberately stricter than `DERIVED_MARKERS` (non-HDL), whose inputs
  may come from separate draws: non-HDL is an exact identity, whereas FIB-4 is a
  validated single-time-point ratio, and cross-draw inputs are a value the
  formula was never validated on. When no draw has all three, it reports which
  inputs are missing — the score is not invented.
- **Band boundaries** follow the conventional cut points: `<1.30` low,
  `1.30–3.27` indeterminate, `>3.27` high.

## Transaminase override in trend dispatch

Stage 6c triages each trend and may judge it "ordinary variation", in which case
the dispatch writes a skip report and stops. That is the exact dismissal that
let ALT/SGPT 26 → 29 → 35 over five months go un-researched, so the regimen-driver
question (a statin and a JAK inhibitor both move these) never ran.

`phase_trend_dispatch` now carries a deterministic gate: when the marker is a
transaminase (`rx.is_transaminase`) and the triage verdict is `MEANINGFUL: no`,
the dispatch **overrides** the verdict and deepens anyway — parts 2/3 and the
synthesis are created as for a meaningful trend, and the intro carries a NOTE
explaining the override so the cards don't re-justify a dismissal. Two invariants:

- The gate is **marker-qualified**. A `no` on anything else (creatinine, sodium)
  still writes the skip report and stops; only liver enzymes are force-deepened.
- It is a **deterministic gate, not a prompt nudge**: an LLM triage that already
  said "no" is the wrong second opinion, so the override is code, and it fires
  on the parsed verdict file, not on re-reading the trend.

`is_transaminase()` lives in `rx.py` next to `_norm_marker` — one normaliser, one
answer — so fanout never re-derives the classification. It is tolerant of
`ALT`/`AST`/`ALT/SGPT`/`AST/SGOT` vendor spellings; the bare `SGPT`/`SGOT` are
*not* treated as transaminases on their own.
