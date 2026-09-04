# Bringing rx-review into alignment with ARCHITECTURE.md

`ARCHITECTURE.md` is the specification. This is the ordered work to make the code match it.

**This plan supersedes the 2026-08-01 plan**, which targeted the old *five-stage, gated* design.
That design is gone: the doc now specifies **six stages, a pre-created Begin/Barrier chain, and
human input via `Regimen clarify:` / `Marker review:` worker cards — no gates.** The code today
still implements the old design in full (the 2026-08-01 plan's phases are done), so this is a
re-architecture, not a touch-up.

Every item names the function it touches and the assertion that proves it. A phase is done when
its tests are in `rx_test.py` and the suite is green — **except** `cardmap.py --check`, which
stays red until Phase 7's `cardmap.py --write`. Do not "fix" it earlier by regenerating the map;
that would overwrite the spec.

## Decisions on record

| Question | Decision |
|---|---|
| The gate mechanism | **Deleted.** `write_gate`, `write_lab_gate`, `cmd_labs_confirm`, `cmd_verify_labs`, `CONFIRMED.txt`/`LABS-CONFIRMED.txt`, receipt/fingerprint. Human input is worker cards. |
| Halts | **Kept**, re-attached to the clarify/review worker cards. `labs-reject`/`regimen-reject` stay; only their derived-file lists change. |
| `--ignore` on a marker re-review | **Accumulates**; `--drop` clears. (Logic moves from `cmd_labs_confirm` onto the review-answer verb.) |
| A name that matches nothing | **Reject the whole command** — record nothing, leave the worker card open, name the unmatched entries with close matches. (`difflib`, kept.) |
| Card creation | `start` creates the **entire** ten-card Begin/Barrier spine up front. Stages no longer create their successor. |

## File-name mapping (the whole migration turns on this)

| current | target | current writer |
|---|---|---|
| `supplements-draft.md` | `regimen-draft.md` | stage-2 worker (REGIMEN_BODY) |
| *(settled in place)* | `regimen-final.md` | **new** stage-3 workers |
| `labs.md` | `labs-draft.md` | `merge_labs` / `cmd_merge_labs` |
| *(none)* | `labs-complete.md` | **new** `review_labs` |
| `labs-brief.md` | `labs-succinct.md` | `cmd_labs_brief` (`Stage 5: Labs Complete` barrier) |
| `LABS-CONFIRMED.txt`, `CONFIRMED.txt` | **deleted** | `cmd_labs_confirm` / `cmd_regimen_confirm` |

Line numbers below are current as of the 2026-08-04 audit; verify before editing.

---

## Phase 1 — command table + help text (mechanical; suite stays green)

`main()` dispatch table, `rx.py:5147`.
- Rename verb `intake-supplements` → `intake-regimen-items` (5178); help "stage 3 of 6".
- Add `("review_labs", cmd_review_labs, "stage 5 of 6 …")` (stub handler until Phase 5).
- Remove `verify-labs` (5148), `labs-confirm` (5156). Keep `labs-reject`/`regimen-reject` (halts).
- `"of 5"` → `"of 6"` (17 sites in rx.py: docstrings 1296/1364/1476, banners, module docstring; **0 in rx_test.py**).
- Move the `--ignore`/`--drop` flag block (5220-5227) onto the review-answer verb (Phase 5).

**Test:** `_verb_registered`, `rx_test.py:1837` — update the registered-verb set (`intake-supplements`→`intake-regimen-items`, add `review_labs`).

## Phase 2 — `cmd_start` builds the whole Begin/Barrier chain (largest new work)

`cmd_start`, `rx.py:4830`. Today creates one card (4876, `Intake: read the regimen`, key `rx-stage2-singleton`). Keep the three refusals (4844/4850/4862).

**Target:** create all ten spine cards in one pass, each Barrier parented in front of the next
Begin: `Stage 2: Read Regimen`→`Stage 2: Regimen Read`→`Stage 3: Settle the Regimen`→`Stage 3:
Finalize Regimen`→`Stage 4: Transcribe Labs`→`Stage 4: Labs Transcribed`→`Stage 5: Review Labs`→
`Stage 5: Labs Complete`→`Stage 6: Research Begin`→`Stage 6: Research Complete`. Each Begin invokes
its command and is created with the prior Barrier in `parents`. Use `create()` (`rx.py:839`) with
an idempotency `key` per card. The head Begin is created parentless on a hand run (`_my_card_id()`
= None normalises away, 846) — the deliberate stage-1 exception.

**Test:** rebuild the chain block `rx_test.py:1779-1806` onto the worker-run harness ARCHITECTURE
§Tests (901-913) mandates: run `start`, read back the ten cards' `parents`, assert each Barrier is
a parent of the next Begin and nothing on the spine is parentless except the head. New test.

## Phase 3 — stage 2 read + stage 3 settle

**Stage 2** `cmd_intake_regimen`, `rx.py:1295`. Currently creates `Read supplement bottle photos`,
`Intake: build regimen inventory` (→`supplements-draft.md`), and the stage-3 successor. Target:
one `Worker: Read regimen` (photo-reading folded in) → **`regimen-draft.md`**, parented to `Stage
2: Regimen Read`; no successor creation. `REGIMEN_BODY:1219` output line → `regimen-draft.md`.

**Stage 3** `cmd_intake_supplements` → `cmd_intake_regimen_items`, `rx.py:1363`. Delete the
two-pass / product-lookup / `write_gate(1470)` flow. Target: one `Regimen Intake: <name>` worker
per supplement+medication (assignee `rx-research`), each a parent of `Stage 3: Finalize Regimen`,
each fetching the panel and writing to **`regimen-final.md`**; when it can't settle, it spawns
`Regimen clarify: <name>` (also a barrier parent). `INTAKE_SUPPLEMENTS_BODY:1153` rewritten.
`cmd_regimen_confirm:657` → the clarify-answer verb: keep name-validation (684-701) and `--unknown`
drop (710-716); delete the gate-close block (720-747); complete the specific `Regimen clarify:`
card and append to `regimen-final.md`.

**Tests:** replace the gate-behavior block `rx_test.py:1935-2034` (asserts `write_gate` fired) with:
a `Regimen Intake:` that can't settle spawns a `Regimen clarify:` barrier-parent, and the answer
verb completes it. Retitle expectations at 2169-2210 (`ACTION REQUIRED`→ clarify card). Keep the
`check_regimen` bullet-parser tests; retarget `CONFIRMED.txt` (15 refs).

## Phase 4 — stage 4 transcribe-only

`cmd_intake_labs`, `rx.py:1475`. Currently transcribe + merge + condense + verify-gate + successor.
Target: create only `Transcribe Lab <date>` / `… pages A-B` workers parented to `Stage 4: Labs
Transcribed`, each → **`labs-draft.md`**. Delete the condense card (1646), the `Verify the labs`
card (1659, delete VERIFY_BODY:216), and the `Start the research stage` creation (1680). Keep
refusals (1489/1506) and `--force`. `merge_labs:4660` stays as the deterministic step invoked by
the `Stage 4: Labs Transcribed` barrier; dest `labs.md`→`labs-draft.md` (4953); the "## Out of
range" rebuild (4911-4945) **moves to `review_labs`** (stage 5 owns out-of-range derivation).
`INTAKE_LABS_BODY:1181` rewritten ("stage 4 of 6", output `labs-draft.md`, no merge/condense).

**Tests:** `labs.md` (18 refs) → the three new files; retitle `Intake: transcribe the labs` (13
refs) → `Stage 4: Transcribe Labs`/`Transcribe Lab`; chain-test fixtures 1873-1878.

## Phase 5 — new `cmd_review_labs` (stage 5)

New command. Seeds `labs-complete.md` by copying `labs-draft.md`, derives the "## Out of range"
section (moved from `cmd_merge_labs`), then creates a `Marker review: <name>` worker (parent of
`Stage 5: Labs Complete`) for each marker `out_of_range_entries():3965` or `trends():3763` flags.
Retarget those readers' source `labs.md`→`labs-complete.md` (3975). `is_ignored():340` /
`ignored_markers():323` retarget from the deleted receipt to the per-review "ignore" decisions
recorded in `labs-complete.md`; `--ignore` accumulate / `--drop` clear (from `cmd_labs_confirm:602`)
move onto the review-answer verb. The `Stage 5: Labs Complete` barrier copies significant markers
to `labs-succinct.md` (repurposed `labs_brief:4584`, input `labs-complete.md`, dest
`labs-succinct.md`).

**Tests (all new — 0 refs today):** `review_labs` seeds `labs-complete.md`; raises one `Marker
review:` per flagged marker; each a parent of `Stage 5: Labs Complete`; confirm/ignore updates the
entry; barrier writes `labs-succinct.md`. Re-base `ignored_markers` accumulate/drop tests
(1689-1761) onto the new verb.

## Phase 6 — stage 6 analyze/fanout + fanout gate removal

`cmd_analyze`, `rx.py:5034`. Keep the `execv` to fanout (5139). Delete the
`_gate_outstanding("CONFIRM YOUR LABS")` block (5107-5121) and the stale-inventory "NOT YET"
self-edge block (5058-5105). Card is `Stage 6: Research Begin`; `ANALYZE_BODY:1194` rewritten (drop
gate/block/retry language).

`fanout.py`: `LABS:55`→`labs-succinct.md` (reads) / `labs-complete.md` (derivations);
`REGIMEN/DRAFT:53-54`→`regimen-final.md`; `read_substances():87` reads `regimen-final.md`;
`LABS_LINE:211`→`labs-succinct.md`. **Delete** the `labs_confirmed()` gate in `read_markers():161`;
keep the fail-closed rationale keyed on file existence. Keep the single filter point `shard():696`
/ `_excluded():671` and the `coverage.md` / `SYNTH` "what this review did not cover" section.

**Tests:** delete `labs_confirmed` (6 refs); retitle `Start the research stage` (5) → `Stage 6:
Research Begin`; keep the single-filter test 879-896.

## Phase 7 — gate-deletion sweep + reject re-target + cardmap regen

**Delete (rx.py):** `write_gate:2663`, `write_lab_gate:2528`, `cmd_labs_confirm:549`,
`cmd_verify_labs:4992`, `_receipt:263`, `_labs_fingerprint:255`, `labs_confirmed:329`,
`admit_confirmed_transcriptions:375`, `_gate_outstanding:770`, `LABS_OK`/`LABS-CONFIRMED.txt`:249,
VERIFY_BODY:216. Re-point `dropped_items:295`/`CONTROL_TXT:360` from `CONFIRMED.txt` to the
clarify decisions.

**Keep + re-attach (halt/salvage):** `_halt:421`, `cmd_labs_reject:493`, `cmd_regimen_reject:517`,
`halted:536`, `clear_board:3338`, `salvage/`. Update the derived-file lists: `cmd_labs_reject:511`
`["labs.md","labs-brief.md",…]`→`labs-draft/complete/succinct.md`; `cmd_regimen_reject:531`
`["photo-inventory.md","supplements-draft.md"]`→`regimen-draft/final.md`. Reject now halts from the
worker cards, not gate cards. Retune `cmd_doctor:3063` "regimen gate" language → "why a `Regimen
clarify:`/`Marker review:` card is waiting."

**cardmap:** last step is `python3 cardmap.py --write` to regenerate the map from the new `create()`
titles; CI green here. Do not hand-edit the block.

**Tests:** delete `write_gate`(6)/`write_lab_gate`(4)/`CONFIRM YOUR LABS`(4)/`ACTION REQUIRED`(1)/
receipt tests (1689-1761); keep + retarget the reject-verb table (2240+) file lists.

---

## Test-rewrite size

~120 assertion sites reference renamed/deleted names; the chain-test block (`rx_test.py:1770-2035`,
~265 lines) is literal-title source-inspection and should be rebuilt onto the worker-run harness
the doc now mandates. Expect to rewrite/delete ~40% of `rx_test.py` and add new stage-1-chain,
stage-5-review, and clarify/review worker-completion tests. `verify.py` needs no change.
