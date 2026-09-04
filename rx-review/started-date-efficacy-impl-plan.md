# Implementation Plan — medication start dates → before/after lab comparison

Status: **PLAN ONLY — no code has been changed.** This is step 3 of the agreed flow
(human-controlled doc ✓ committed as `3f959ad` → rest of document ✓ aligned → code pass,
pending your review of this plan).

Companion documents:
- `started-date-efficacy-spec.md` — WHAT to build (approved design)
- This file — HOW to sequence it, what was verified against today's code, what could break,
  and the open decisions needing your sign-off before work starts.

---

## 0. Verification of spec anchors (done, against current `main`)

Every line-number claim in the spec was checked against the code on 2026-08-22. All match:

| Spec says | Found | Note |
|---|---|---|
| `REGIMEN_BODY` ~820 | rx.py 820 | Stage 2 draft-worker prompt |
| `_draft_regimen_rows()` ~911 | rx.py 911 | |
| `REGIMEN_INTAKE_BODY` ~839 | rx.py 839 | Stage 3 intake prompt |
| `cmd_intake_regimen_items()` ~945 | rx.py 945 | |
| `_read_item_file()` ~4853 | rx.py 4853 | |
| `REGIMEN_FINAL_HEADER` ~504 | rx.py 504 (used at 4881) | |
| `_write/_read_regimen_final_rows` ~4875/4889 | rx.py 4875 / 4889 | |
| consumers of `_read_regimen_final_rows` | 6 call sites | see Step 1e |
| `_parse_since` home near `_norm_date` ~3130 | rx.py 3130 | |
| `before_after` home near `trends` | rx.py 3184 | `series_for` at 3064 behaves as spec assumes (ambiguous name ⇒ `[]`, never guesses); `marker_series()` at 3167 dedups by (marker, date) |
| `cmd_before_after` home near `cmd_trends` ~2076 | rx.py 2076 | |
| CLI table ~5662 | rx.py 5662 region | |
| `read_substances()` ~86 | fanout.py 86 | header-index lookup already column-order tolerant |
| `SUBSTANCE_PARTS` / q4∈part2 | fanout.py 581 | `labs_parts={2}` already correct |
| substances branch / `shard()` / `synth_ids` splice | fanout.py 928–974 / shard 632 / splice 970 | return contract unchanged by plan |
| `RECONCILE` ~313, `SYNTH` ~350 | fanout.py 313 / 350 | both prompt strings found |

No spec corrections needed. The spec is implementable as written.

---

## 1. Sequenced steps (each independently testable, commit-sized)

### Step 0 — Baseline (no behavior change)
- Confirm clean tree; create working branch `feat/started-efficacy`.
- Run whatever exists of the test suite + one full manual `rx.py start --dry-run` style smoke
  to capture the pre-change card graph shape for comparison.
- Record: `git rev-parse HEAD` the plan was written against = `3f959ad`.

### Step 1 — `Started` plumbing (spec Part 1; rx.py ×9 + fanout.py ×1)
a. **Stage 2 draft prompt** `REGIMEN_BODY` (820): draft row becomes
   `product | brand | quantity | schedule | started`; instruct: transcribe only what the user
   wrote, blank when unstated.
b. **`_draft_regimen_rows()`** (911): accept/pass the 5th field.
c. **Stage 3 prompt** `REGIMEN_INTAKE_BODY` (839): worker carries the whole row incl.
   `Started`; user-provided, never researched; supplements typically blank.
d. **`cmd_intake_regimen_items()`** (945) + **`_read_item_file()`** (4853): carry the field
   into `regimen-item-<slug>.md`.
e. **Settled-file format** `REGIMEN_FINAL_HEADER` (504), `_write_regimen_final_rows()`
   (4875), `_read_regimen_final_rows()` (4889): insert `Started` between Schedule and
   Confidence (6 data columns). Reader: missing trailing cell ⇒ blank `Started`
   (old-format tolerant during transition; no migration needed since each run rebuilds).
f. **Six consumer sites** (spec §1.9):
   - `_regimen_final_review()` (4916 loop): add `; started <date>` clause when non-blank
     → **expected consequence:** the stage-3 review fingerprint changes ⇒ the user sees one
     more review round after deploying. Intended, not a bug.
   - `cmd_confirm()` (2054): 7-tuple unpack + `"started"` in JSON.
   - `cmd_doctor()` (2443): 7-tuple unpack.
   - `cmd_gather_regimen_slugs()` (5014): parse + carry through.
   - `cmd_correct_item_slug_request()` (5074): comprehension/unpack → 7-tuple; printed
     `LINE:` template gains one `| %s |`.
   - `cmd_correct_item_slug_response()` (5140): `len(cells) != 5` → `!= 6`; keep
     Schedule-not-blank guard; do NOT require `started`.
   - `check_regimen()` (1538): truthiness only — untouched.
g. **`read_substances()`** (fanout.py 86): add `"started"` to the row dict via header index
   (`_header_index(cells, "started")`), same pattern as `when`. Blank stays blank.

### Step 2 — `before-after` verb (spec Part 2; rx.py ×4)
a. `_parse_since()` near 3138 (code given verbatim in spec §2.1).
b. `before_after()` near 3184 (spec §2.2). Pure arithmetic over `series_for(marker_series(), m)`;
   ambiguous/absent marker ⇒ `found=False`, never guessed; `post_n < 2` ⇒ `too_early=True`.
c. `cmd_before_after()` near 2076 (spec §2.3; human + `--json` output).
d. Register in `main()` verb table + argparse block (spec §2.4).

### Step 3 — Efficacy card (spec Part 3; fanout.py ×2)
a. New `EFFICACY_BODY` card body next to `SUBSTANCE_SYNTH` (spec §3.1 verbatim): reads ONLY
   `PART-research-<slug>-2.md` for the marker list; runs `rx.py before-after --marker M
   --since S` per marker; writes `efficacy-<slug>.md`; dull-result rule ("TOO EARLY TO TELL"
   with post-start draw count) first-class.
b. Substances branch of `phase_research_family()` (929–946): for each substance with
   non-blank `started`, create the card parented on that substance's synthesis id, append its
   id to `synth_ids` (existing splice at 970 places it before the 6a Barrier). Title/key
   includes the start date (`Efficacy: <name> (<started>)`) so a corrected date creates a new
   card, not a silent reuse.

### Step 4 — Downstream (spec Part 4; fanout.py ×2 prompt edits)
1. `RECONCILE` (313): input list += "and EFFICACY reports".
2. `SYNTH` (350): brief section for medication efficacy (+renumber or fold into §6 — **your
   call, Decision D1 below**).

### Step 5 — Tests (spec "Testing / verification" §)
- Seeded statin scenario: Feb/Mar draws (pre), May/Jun/Jul (post), started `2026-04` →
  delta + direction + post_n=3.
- Same seed with started `2026-04-20` → April draw correctly lands PRE.
- Blank-started supplement ⇒ byte-identical card graph vs pre-feature run (the key no-op
  guarantee).
- Too-early case (1 post draw) ⇒ TOO EARLY TO TELL.
- No pre-start baseline ⇒ post series reported, baseline noted absent.
- Unmeasured marker & ambiguous blood/urine marker ⇒ refused, not guessed.
- Correction verbs: 6-field line round-trips through request/response; Schedule-blank still
  refused; Started-blank accepted.
- Month-granularity caveat honored by card body wording.

---

## 2. Risk register

| Risk | Mitigation |
|---|---|
| Stage-3 fingerprint change forces one extra user review after deploy | Expected; called out here and in the canonical doc. Ship between runs. |
| Correction verbs are the most format-sensitive code (field counts hardcoded) | Covered by dedicated test above; they're also the only places `!= 5` appears. |
| `read_substances()` header detection with the new column | Lookup is by header name and already order-tolerant; test with shuffled columns anyway. |
| Efficacy card created but research excluded/short-circuited | Card exists only when its synthesis id exists (`shard()` may return None on exclusion) — creation sits inside the same guard. |
| Brief bloat when many meds are dated | One short section per dated med; acceptable; can cap later. |
| Old `regimen-final.md` from a previous run lacks the column | Each run regenerates it at stage 3; reader tolerates short rows anyway. |

## 3. Open decisions (need your answer before Step 4)

- **D1 — Brief layout:** new numbered section "Medication efficacy" (renumber 6–8),
  or fold into existing §6 "Lab observations" as a sub-bullet (numbers stay stable)?
  Spec default: new section.
- **D2 — Commit granularity:** five commits matching Steps 1–5 (recommended; bisectable),
  or one commit at the end?

## 4. Touch summary

- `rx.py`: ~10 edit regions (Steps 1a–f, 2a–d)
- `fanout.py`: 4 regions (1g, 3a, 3b, 4×2)
- No changes to `rxkanban.py`, `rxcache.py`, `verify.py` (glob picks efficacy reports up
  automatically), or any profile/skill files.
