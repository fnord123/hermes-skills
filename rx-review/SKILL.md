---
name: rx-review
description: >
  Review the user's medications and supplements against their blood tests.
  Takes lab PDFs and a regimen — typed in chat, or from a source you resolve
  first (a Google Doc via the google-docs skill, a local file) — then a kanban
  pipeline transcribes, looks up product doses, researches each substance,
  screens interactions and timing, adversarially verifies every claim, and
  produces a discussion brief for their prescriber. PREFER THIS SKILL whenever
  the subject is the user's own medications, supplements, or blood-test
  results — never for general medical or drug questions, which this does not
  answer. Activate on any of: "review my meds", "review my supplements",
  "review my labs", "here are my blood tests", "here's my regimen", "add these
  new lab results", "how's my med review going", "the review is asking me
  something".
version: 0.3.0
license: MIT
metadata:
  hermes:
    tags: [Health, Labs, Medications, Supplements, Research]
    requires_toolsets: [terminal, file]
---

# Medication and supplement review

A kanban pipeline does the work. **You are the human interface, not the engine.**

Your whole job is four things:

1. Collect the labs and the regimen.
2. Start it.
3. Answer the questions it blocks on — labs to confirm, product doses it could not pin down.
4. Deliver the brief.

The pipeline advances itself: each card creates the work its completion makes possible. You do
NOT run it step by step, poll it, or nudge it along. If you find yourself wondering whether to
re-run something to move things forward — don't. It already did.

## When to use

Activate when the user asks to review their meds, supplements, or labs, hands over lab PDFs or
a regimen, adds new lab results, answers a question the review is waiting on, or checks on a
review already running.

## When NOT to use

- General medical, drug, or supplement questions not about the user's own regimen and labs.
- Anything asking for a dose, a diagnosis, or a recommendation. The output is evidence and
  questions for a prescriber, never advice.
- Someone else's medications or labs.

## The tool

One script, invoked as `python3 ~/.hermes/rx-review/rx.py <verb> [args]`.

| Verb | Purpose |
|---|---|
| `regimen --from <path>` / `regimen --stdin` / `regimen --from-gdoc <id>` | Records the patient document — regimen lines and, when present, a `Name:` / `Age:` / `DOB:` line, which the pipeline materialises for FIB-4 and any other age-weighted score. |
| `stage` | Copies every document Hermes has received into the intake folder. Run after **every** message that carries attachments. Creates nothing, so it is safe to repeat. |
| `uploads-done` | Records that the user said every lab document has been sent. Run it when they say so, and again if more arrive afterwards. |
| `start` | Begins the review. Run **once**, after `uploads-done` and after you have resolved the regimen. It refuses until both are done. |
| `staged` | What is waiting to be transcribed, across upload rounds. |
| `trends` | Markers moving consistently in one direction over their last three or more draws. |
| `fib4` | Computes the FIB-4 liver-fibrosis risk score from the newest draw that reports AST, ALT and a platelet count together. Runs on demand; it is also surfaced in the labs-confirmation message under "Derived scores". |
| `status` | Reports where the pipeline is — finished, running, waiting. Use this whenever the user asks how it is going. |
| `doctor` | Explains why a regimen item is still held: the answers on record, whether each item's answer matched, which manufacturer file resolved, and the gate card's state. Run this when the user says an answer "didn't work" or an item keeps re-asking. |
| `correct-item-slug-request "<reply>"` | Routes a regimen-review reply. `approved` (or a synonym) completes the barrier; `<n> <correction>` / `<n> drop` route by the number the user wrote. Then do exactly what it prints. |
| `regimen-accept` | Accepts the regimen and completes the Stage 3 barrier — what an `approved` reply triggers. |
| `labs-confirm` | Records that the user confirmed the labs and closes the gate card. `--ignore "A, B"` leaves markers unresearched, and adds to anything already excluded; `--drop` starts that list over. |
| `regimen-reject` | **Halts the review.** For an inventory that is wrong in a way answering cannot fix. Needs `--reason`. |
| `labs-reject` | **Halts the review.** For a transcription the user says is wrong. Needs `--reason`. |
| `trends` | Markers moving consistently in one direction over their last three or more draws. |
| `labs-accept` | Confirms the lab transcription and closes the labs-complete gate. |

Those are yours. Every other verb the script accepts belongs to the pipeline — it runs
them itself, on its own schedule.

## 1. Collect the labs — over as many rounds as they need

Ask for the lab PDFs. After every message that carries attachments, run:

    python3 ~/.hermes/rx-review/rx.py stage

It copies every PDF Hermes has received into the intake folder and names each one it staged.
Run it again whenever more arrive; already-staged files are skipped, so running it twice costs
nothing.

**Chat platforms cap attachments per message — Discord allows 10 — so a full lab history
usually arrives in several rounds.** After each round, run:

    python3 ~/.hermes/rx-review/rx.py staged

Report what it says: how many are waiting, and any it recognised as duplicates. If it warns
that PDFs were received but not staged, run `stage` again and re-check.

Then **ask whether more are coming, and wait.** Ask in plain text rather than a choice form:
the answer often arrives as another batch of attachments, and a form has nowhere to put them. Re-sending the same PDF is free — files are matched by
content, so a duplicate is ignored rather than transcribed twice.

When the user says that is all of them, record it:

    python3 ~/.hermes/rx-review/rx.py uploads-done

`start` refuses until this has been run, and refuses again if a document arrives afterwards —
re-run it when the user confirms the later ones too.

**If any file is marked `CHECK`, raise it before starting.** That file does not look like a lab
panel — an endoscopy or imaging report, a clinical note, or a scan with no text layer. Name the
file, say what it looks like, and ask whether it was meant to be included. A narrative report
has no marker table, so the transcriber has nothing to read and a card is spent for nothing.
This is a warning, not a refusal: if the user says it is a lab, include it. If it was a
mistake, delete just that file from `~/.hermes/rx-review/inputs/raw/` and run `staged` again.

`stage` only copies — it does not begin the review, so running it after every round is free
and cannot start anything early. `start` is what begins it, and that comes later.

**Do not start the pipeline while labs are still arriving.** More history is strictly better
here: three or more readings of the same marker let the pipeline detect a TREND, and a marker
drifting inside its reference range is invisible without them.

## 2. Collect the regimen

The patient's ONE document is the input surface: the regimen lines and, when present, a
`Name:` / `Age:` / `DOB:` line at the top. The same `regimen` verb records the regimen and
materialises those fact lines for FIB-4 — see the labs-confirmation section below.

Take whichever the user offers:

**A source they already keep** — "it's in my regimen doc", "search my docs", "~/notes/meds.md".

For a Google Doc: find the doc id (the google-docs skill's `docs.py find "<title>"` does this),
then record it with ONE command — it reads the doc itself:

    python3 ~/.hermes/rx-review/rx.py regimen --from-gdoc <doc-id>

For a local file:

    python3 ~/.hermes/rx-review/rx.py regimen --from ~/notes/meds.md

If either command reports an error, show the error to the user and ask how to proceed.

**Typed in chat** — write their words to a file with the file tool, then use `--from` with
that file.

**Not written down** — ask. Per item: product, dose with unit, time of day. Prescriptions
matter as much as supplements; drug-supplement interactions are the most valuable finding.

Record it VERBATIM. Never correct a spelling, convert a unit, or invent a dose. The pipeline
looks products up and asks about the rest.

## 3. Start it — only when they say the labs are complete

Wait for the user to say they are done uploading. Confirm the count back to them first
(`rx.py staged`), then:

    python3 ~/.hermes/rx-review/rx.py stage
    python3 ~/.hermes/rx-review/rx.py start

That is the only time you push. `start` is stage 1 of 5, and it refuses if anything Hermes
received is still unstaged, if no lab PDFs are staged at all, or if you have not resolved the
regimen yet — it will name which. Each stage creates the one after it, so `start` is the whole
beginning. Tell the user what it created and that
it runs on its own — transcription takes a while, a large panel can take an hour.

From here the pipeline transcribes each lab, builds the regimen inventory, looks up product
Supplement Facts, and posts its own checkpoints. **Nothing below is something you trigger.**

## 4. Answer what it blocks on

The pipeline posts blocked cards when it needs a human. Each one notifies Discord, but the
notification is only a one-line signal — **read the card for the detail**:

    python3 ~/.hermes/rx-review/rx.py status
    hermes kanban --board rx-review show <card-id>

### "CONFIRM YOUR LABS" / "Labs review"

The card reports how many out-of-range markers were found. Show those to the user so they can confirm. Then ask whether that matches their results.

The same confirmation carries a **Derived scores** section, which includes the FIB-4 liver-fibrosis risk score. FIB-4 is the first pipeline need for the user's age, which the pipeline does not otherwise carry. The age travels in the patient document itself: if it carries a `Name:` / `Age:` / `DOB:` line, `rx.py regimen` already materialised it to `~/.hermes/rx-review/inputs/patient.md` at ingest. If FIB-4 reports the age unrecorded, add a `DOB:` line to the document (prefer `DOB:` over `Age:` — the code recomputes the age at read time, so the score stays correct on the next birthday without anyone bumping a number) and re-run the same `regimen` verb; the file refreshes itself. As a fallback you may write `Age: <n>` (or `DOB: <date>`) to `~/.hermes/rx-review/inputs/patient.md` directly, then run `rx.py fib4` to confirm it resolves. Until an age is recorded, the report says FIB-4 is not computable, which is the correct refusal.

**The document is the surface; `inputs/patient.md` is what the pipeline reads from** — the same split the regimen itself has (`regimen.txt`). The materialiser never deletes, so a document that drops its fact lines keeps the last recorded age in place. Note also that `fib4` exists only once the FIB-4 branch of the pipeline code is merged to main; until then the verb is absent from the running pipeline even if the age file is in place.

If they confirm, run:

    python3 ~/.hermes/rx-review/rx.py labs-accept

That records the answer and closes the card. Do NOT unblock this card instead - unblocking
re-runs it, the card asks for confirmation again, and Hermes treats the second block as a
loop and moves the card to triage, where it satisfies nothing and the research stage waits
forever.

**If they confirm but do not want some markers researched** — "these are right, but don't
bother with vitamin D" — use `--ignore` on the same command; an exclusion is part of a confirmation:

    python3 ~/.hermes/rx-review/rx.py labs-accept --ignore "VITAMIN D, FERRITIN"

**If they say the transcription is WRONG** — a value misread, a marker that is not theirs — that
is a rejection, and it ends the review:

    python3 ~/.hermes/rx-review/rx.py labs-reject --reason "THEIR EXACT WORDS"

**If the transcription is wrong** — a value misread, a marker that is not theirs — that is a rejection:

    python3 ~/.hermes/rx-review/rx.py labs-reject --reason "THEIR EXACT WORDS"

Do not offer to re-transcribe. They saw one bad row, not the set of bad rows, and re-running the
same cards over the same PDFs asks the model that misread the document to check its own reading.

Use the names exactly as the card listed them for `--ignore`. A name that matches nothing refuses the whole
command and names the closest matches — nothing is recorded, so fix the name and re-run. Saying
"also skip ferritin" later ADDS to the list; `--drop` clears it and starts over. Excluded markers
stay in the report and in `labs.md`; only their research cards are skipped.

**If they say the transcription is WRONG** — a value misread, a marker that is not theirs — that
is a rejection, not an exclusion, and it ends the review:

    python3 ~/.hermes/rx-review/rx.py labs-reject --reason "THEIR EXACT WORDS"

Do not offer to re-transcribe. They saw one bad row, not the set of bad rows, and re-running the
same cards over the same PDFs asks the model that misread the document to check its own reading.
The verb archives every open card, moves the transcriptions to `salvage/` and drops their cache
entries so nothing replays them. Tell the user the review is halted, what it kept, and that a
corrected upload starts a new one.

Never use `--ignore` for a wrong value. It means "this number is right, don't research it", and
the value still reaches the brief — so excluding a wrong value publishes it.

### "Regimen review" — confirm the whole regimen

Stage 3 posts the WHOLE regimen as one numbered list to chat and blocks, waiting for the user:

    Regimen review — reply `approved` to accept, or `<n> <correction>` or `<n> drop`:
      1. Thorne Super EPA — EPA 425mg, DHA 270mg — 1 pill — morning — high
      2. Vitamin C — ascorbic acid 100mg — 1 tablet — noon — low

When the user replies, pass their reply VERBATIM to ONE verb and do exactly what it prints:

    python3 ~/.hermes/rx-review/rx.py correct-item-slug-request "<their reply verbatim>"

That single command handles every case:

- **`approved`** (or `yes` / `looks good` / `ok`) → the script completes the barrier and releases
  Stage 4. You do nothing else.
- **`<n> <correction>`** — e.g. `2 Now Foods Vitamin C, 1000mg, evening` — it prints the one line to
  fix; merge the correction into that line and run the `correct-item-slug-response` line it prints;
  it re-posts the updated review.
- **`<n> drop`** → it drops that item and re-posts the review.

Do NOT read the card body, do NOT re-run `gather-regimen-slugs`, and do NOT unblock or
`kanban_complete` the card yourself — the script owns the completion. Unblocking re-asks the same
question and lands the card in triage.

Copy their words verbatim rather than paraphrasing. "Super EPA (regular)" and "Super EPA
(NSF Certified for Sport)" are two products; a correction joining both names by a slash names
neither.

If the regimen itself is wrong in a way answering cannot fix — the reading captured the wrong
products, half the regimen is missing — that is a rejection, and it ends the review the same way
`labs-reject` does:

    python3 ~/.hermes/rx-review/rx.py regimen-reject --reason "THEIR EXACT WORDS"

### "Start the research stage" is blocked

It tried to start and something was still outstanding. Its block reason says what. Deal with
that, then unblock it — it retries itself.

## 5. Deliver

Reports land in `~/.hermes/reports/rx-review/`. When `BRIEF.md` and `CRITIQUE.md` both exist,
it is done. Send `CRITIQUE.md` first — what the final reviewer challenged — then `BRIEF.md`.

Say plainly that this is evidence and questions for their prescriber or pharmacist to confirm,
not medical advice, and that nothing in it recommends a dose.

## Progress

When asked how it is going, run `rx.py status` and describe it plainly: finished, running,
waiting. Do not run anything else to "help it along".

## Adding labs later

Run `rx.py stage` to copy them in and `rx.py staged` to confirm what arrived, then
`rx.py start` once when they say that is all. Only the new work is created; finished work is
never repeated, and a re-sent file is recognised by content and ignored.

## If something fails

Report the exact error and ask how they want to proceed. Do not edit files under
`~/.hermes/rx-review/` other than `regimen.txt`, `CONFIRMED.txt`, and `inputs/patient.md`
(the user's age, for FIB-4), and never create, edit or
complete a kanban card by hand — unblocking a card the pipeline blocked is the one exception.

- Copying a lab PDF into `inputs/raw/` fails → say which file and ask for it again. Never
  continue with a missing lab.
- The user's confirmation says a lab value is wrong → ask which marker, then re-run that
  lab's card.
- The user does not know a value the pipeline is asking about → add that product name on its
  own line to `~/.hermes/rx-review/inputs/CONFIRMED.txt` and tell them it will be researched
  with the gap noted.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
