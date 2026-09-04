# rx-review — session handoff

Written 2026-08-02 at the end of a long session. Everything a fresh Claude Code instance needs to
pick this up. Read `ARCHITECTURE.md` first — it is the specification — then this for what is in
flight and why.

---

## 1. Do this first

**`ARCHITECTURE.md` has uncommitted changes that the USER authored** (sections 1–3). They are not
mine and I did not commit them. Confirm with the user before touching or committing that file. If
the working tree has been lost, those three sections are gone and will need rewriting from the
user's description.

```
cd ~/.hermes && git status --short rx-review/ARCHITECTURE.md
```

**The document currently argues with itself.** The user rewrote sections 1–3 (intro, *Pipeline
stages*, *Flow control mechanics*) to a new model. Sections 4 onward still describe the old one.
Aligning them is the main open task — see §5.

---

## 2. Where the code lives

The pipeline is a single skill: `~/hermes-skills/rx-review/` (code in `scripts/`, docs at the
root), in the `fnord123/hermes-skills` repo. `~/.hermes` (`fnord123/Hermes`) keeps the nine
`rx-*` profiles, `provision-profiles.py`, and `hooks/terminal-pipeline-only.sh` (now a shim to
the skill's copy). `~/.hermes/rx-review` is a local symlink to `~/hermes-skills/rx-review/scripts/`
— transitional; it is removed after one clean run.

| Repo | Path | Contents |
|---|---|---|
| `fnord123/hermes-skills` | `~/hermes-skills/rx-review/` | the pipeline — `scripts/` (code, tests, allowlist, card map) + docs |
| `fnord123/Hermes` | `~/.hermes` | the `rx-*` profiles, `provision-profiles.py`, the allowlist shim in `hooks/` |

`~/.hermes/profiles/*` is gitignored; `provision-profiles.py` is the only durable record of the
nine `rx-*` profiles.

**Checks — all five must pass; the hermes-skills repo CI runs the first four.**

```
cd ~/hermes-skills/rx-review/scripts
python3 rx_test.py                          # 434 checks
python3 card_command_test.py                # 32 card commands vs the terminal allowlist
python3 cardmap.py --check                  # ARCHITECTURE.md's generated card map is current
bash test-terminal-pipeline-only.sh         # the allowlist's own block/allow battery
```
(Plus, in the Hermes repo: `python3 provision_profiles_test.py`.)

**23 verbs**: `stage start intake-regimen intake-supplements intake-labs analyze merge-labs
labs-brief verify-labs labs-report labs-confirm labs-reject regimen regimen-confirm
regimen-reject confirm staged status doctor trends check-reports prune-unsourced reset`

---

## 3. How this session worked, and the conventions to keep

**`ARCHITECTURE.md` is the specification, not a description of the code.** It states what the
pipeline should do; the code is brought to it. That inversion is deliberate and the user
confirmed it explicitly ("The doc needs to explain what we want and how we will build it"). Do
not "fix" the doc to match the code.

**Every rule in the doc wants a test in the same commit.** A behaviour described but unasserted
is one the next change removes silently.

**The card body and the parser that reads it must be tested against each other.** The worst bug
found this session was exactly that pair drifting — see §4.

**Commit messages carry the incident, not just the change.** That is why this codebase could be
audited at all. Keep it.

**The user's standing preferences:** push after commit without re-asking; never leave CI broken;
during a planned migration do not tweak the old version, fold changes into the cutover; always
open the kanban DB read-only (`sqlite3.connect("file:...?mode=ro", uri=True)`); deliver systems
that run without a human in the loop.

---

## 4. What was done, and the bugs that motivated it

Seven phases, tracked in `ALIGNMENT-PLAN.md` (all marked done). The defects worth remembering:

**The regimen gate never fired at all.** `check_regimen()` stripped bullets with
`lstrip("-*• ")` — a character *set*, so the `**` of a bold name went with the bullet.
`REGIMEN_BODY` instructs the model to write exactly `- **Name** — why`, which arrived as
`Name** — why`, matched no pattern, and was dropped in silence. Every run behaved as though the
regimen were unambiguous. Fixed; `needs_confirmation_item()` is now its own function and the test
runs `REGIMEN_BODY`'s own examples through it.

**Staging twice started two reviews.** `rx.py stage` both copied files and created the stage-2
card, keyed on the set of staged filenames — so 10 labs then 5 more gave two keys and two
independent chains. Split into `stage` (repeatable, creates nothing) and `start` (once, constant
key `rx-stage2-singleton`). **This is the split the user's rewritten stage-1 row currently
contradicts** — see §5.

**A gate closed on any answer.** `regimen-confirm` appended `--item` verbatim with no validation
and completed the card regardless, so a typo recorded an answer against nothing and removed the
card that would have re-asked. Now validates against the outstanding set, refuses the whole
command on a miss with close matches, and completes only when nothing is outstanding.

**Neither gate could be rejected.** The lab gate exists to catch a misread transcription and had
no path for that outcome. `labs-reject` / `regimen-reject` now halt: write the record, archive
every open card, move derived artifacts to `salvage/`, drop the transcription-cache entries (labs
only — a content-addressed cache replays regardless of where the file went), keep what the
pipeline did not produce.

**Exclusions applied to one family only.** A marker excluded with `--ignore` still got a `Trend:`
card. Now applied in `shard()`, the single point where anything becomes a research card, via a
`subject` parameter.

**Silent truncation at the lab gate.** The card body printed the true out-of-range count and
listed at most 25, saying nothing about the rest.

**Dead dry-run guards.** `rxkanban.create_card` returns bare `"DRY"`; `fanout`'s own wrapper
returns `"DRY-<slug>"`. Every caller compared `!= "DRY"`, so the filters in `fanout.py` and
`lenses.py` never fired. One predicate now: `rxkanban.is_dry`.

---

## 5. The open task: align sections 4+ with the user's new model

The user's sections 1–3 define three card types and a different flow. Sections 4 onward, and the
code, still implement the old one. **Do not start implementing without confirming the deltas with
the user** — several are genuine design changes, not corrections.

### What the user's model changes

| Their model | What exists today |
|---|---|
| Worker cards are created with `parents=[creator]` | Leaf cards are created with no parents at all — ordered only by not existing until their stage runs |
| A **barrier card** creates the next Stage Begin card | The stage card creates its own successor and parents it on itself |
| Gates are worker cards with human interaction; barriers wait on them | Gates are a fourth kind, *linked in front of* an already-created next-stage card |
| Barriers may spawn further worker+barrier rounds | Stage 3's two passes, terminated by idempotency key |
| Lab confirmation is inside stage 4's completion condition | The lab gate holds the stage-5 card |
| `regimen.md`, `labs-complete.md`, `labs-succinct.md`, `<date>-rx-review.md` | `supplements-draft.md`, `labs.md`, `labs-brief.md`, `BRIEF.md` |

### Feedback given, not yet acted on

1. **The stage-1 row says `rx.py stage` "in turn calls `rx.py start`".** It does not, and it must
   not — that is the two-chains bug. I suggested a replacement row; the user has not applied it.
2. **What terminates a barrier loop?** Today termination is structural (the key). Barrier-decides
   needs a no-progress guard or a round cap.
3. **Stage 1 has no Stage Begin card**, but the section says every stage has one.
4. **Zero worker cards ⇒ zero barriers ⇒ nobody creates the next Stage Begin card.**
5. **Stage 5's exit is "None, pipeline is complete"**, but `analyze` today can queue a stage-2
   rebuild. If stage 3 makes the regimen definitive that back-edge may be dead — worth making
   explicit rather than implied.
6. Typos: "regiment" → "regimen"; "assembed" → "assembled"; "These cards to the majority" → "do
   the majority"; a stray `` ` `` and card name in the stage-5 *What It Does* cell.

Sections 1–3 are marked `This section authored by person - do not change without explicit
permission`. Respect that.

---

## 6. Known open items, none blocking

- **Nothing has been run end to end.** Every phase has unit and chain tests; no actual review has
  gone through the `stage`/`start` split, the halt paths, or coverage reporting. A dry run on the
  board before real labs would be sensible.
- **Dropped substances still reach the interaction screen.** `--unknown` suppresses the monograph,
  but `Interaction and timing screen: full regimen` reads `supplements.md` directly. Arguably
  correct — an interaction risk does not vanish because the dose is unknown — but not what the doc
  implies. Undecided.
- **`upstream = None` in `fanout.py`** is a latent `parents=None`. Safe today because the two
  conditions are exact complements, but the safety is coincidental and ~60 lines apart.
- **`~/rx-selfedge-wip.patch`** is discarded work from Phase 0. Everything worth keeping was
  re-landed with tests. Safe to delete.
- **`fix_db.py` and `repair.py`** sit untracked in `rx-review/`. The user never decided their fate.
- **Node 20 deprecation** in the Hermes CI actions.

---

## 7. Traps this session actually hit

- **`cardmap.py --check` compares the doc against the code.** Committing code without
  `ARCHITECTURE.md` passes locally and fails CI — the local check reads the uncommitted doc. Commit
  them together.
- **`fitz` (PyMuPDF) is not installed in CI.** Anything a test reaches must not sit behind that
  import. `cmd_intake_labs` imports it *after* its refusals for this reason.
- **Source-inspection tests match comments and docstrings.** Two tests false-positived on prose
  explaining the very thing they asserted was gone. `rx_test.py` has `_code_only()` and
  `_executable_source()` helpers; use them.
- **Batch-editing by anchoring on the first regex match is dangerous.** One such edit deleted
  `read_markers()` entirely because the anchor matched in `read_trends()`. Assert the anchor is
  inside the intended function before replacing.
- **Two different `"DRY"` sentinels exist.** See §4.
- **A subagent's work is not trustworthy because it looks competent.** An earlier agent's 480-line
  patch implemented self-edges correctly *and* silently removed the regimen gate; three tests
  caught it. Run the suite before building on delegated work.

---

## 8. The user's actual entry point

```
/rx-review I want to review my meds and supplements against my labs.
           See my google doc for meds and supplements.
```
…with ~10 lab PDFs attached, and more uploaded afterwards.

So: the regimen comes from a **Google Doc** (`rx.py regimen --from-gdoc <doc-id>`, which shells out
to the google-docs skill), labs arrive over **several rounds**, and the agent must ask whether more
are coming before running `start`. `SKILL.md` in the other repo is what drives this; it was updated
this session to document `stage`/`start`, both reject verbs, `--unknown` and `--drop`.
