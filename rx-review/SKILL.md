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
| `regimen --from <path>` / `regimen --stdin` | Records the regimen text you have already resolved and saved. |
| `stage` | Copies every document Hermes has received into the intake folder. Run after **every** message that carries attachments. Creates nothing, so it is safe to repeat. |
| `start` | Begins the review. Run **once**, after the user says the labs are complete and you have resolved the regimen. |
| `staged` | What is waiting to be transcribed, across upload rounds. |
| `trends` | Markers moving consistently in one direction over their last three or more draws. |
| `status` | Reports where the pipeline is — finished, running, waiting. Use this whenever the user asks how it is going. |
| `doctor` | Explains why a regimen item is still held: the answers on record, whether each item's answer matched, which manufacturer file resolved, and the gate card's state. Run this when the user says an answer "didn't work" or an item keeps re-asking. |
| `regimen-confirm` | Records an answer to a regimen question and closes the gate card. Use this instead of unblocking. |
| `labs-confirm` | Records that the user confirmed the labs and closes the gate card. Use this instead of unblocking. |
| `verify-labs` | Gets the full transcription picture for the "CONFIRM YOUR LABS" card: markers read, out-of-range values, anything unverified. |
| `confirm --json` | Lists the items the "Confirm N item(s) before research" card is waiting on, with what intake already knows about each. |

Those nine are yours. Every other verb the script accepts belongs to the pipeline — it runs
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

### "CONFIRM YOUR LABS"

Run `rx.py verify-labs` to get the full picture, then tell the user:

- how many markers were read, from how many PDFs
- **every out-of-range marker, with its value and reference range** — quote them, do not
  summarize as "several are high"
- anything it could not verify against the source PDF

Send this as normal chat messages. Long output is fine — your messages are split
automatically; it is only the card notifications that are one-liners.

Then ask whether that matches their results (`clarify`: "yes" / "something's wrong"). The
machine already checked every value appears verbatim in the PDF; what it cannot catch is a
correct number attached to the wrong marker. That is what their eyes are for.

If they confirm, run:

    python3 ~/.hermes/rx-review/rx.py labs-confirm

That records the answer and closes the card. Do NOT unblock this card instead - unblocking
re-runs it, the card asks for confirmation again, and Hermes treats the second block as a
loop and moves the card to triage, where it satisfies nothing and the research stage waits
forever.

If they do not confirm, ask which marker looks wrong and re-run that lab's card.

### "Confirm N item(s) before research"

Run `rx.py confirm --json`. Each entry carries what you need for a real question:

- `item`, `why` — what is missing
- `note` — what intake found ambiguous
- `known` — brand, dose, unit, serving size, times taken
- `lookup` — the manufacturer's Supplement Facts if found: `serving_size`, panel `excerpt`,
  and `ambiguous` / `not_found`

**Spell it out.** Never ask a bare "X needs confirmation" — they cannot answer that without
hunting for the bottle. State what is known, state what the manufacturer says, ask the one
undetermined thing:

- panel found → *"Thorne Super EPA: the manufacturer lists EPA 425 mg + DHA 270 mg per
  serving, and a serving is 2 gelcaps — your 1 pill would be half that. Is that your product,
  and do you take 1 or 2?"*
- `ambiguous` → name the variants, ask which they have
- name looks misspelled → *"You wrote Prevastatin 20 mg at 9pm — did you mean Pravastatin?"*
- implausible dose → give both their number and the plausible one, ask which
- nothing found → ask for the amount per serving from the label

Record each answer with:

    python3 ~/.hermes/rx-review/rx.py regimen-confirm --item "PRODUCT" --answer "THEIR EXACT WORDS"

The gate notification already prints this command with `--item` filled in for each product — copy
that line and fill in `--answer` from their reply, rather than typing the product name yourself.

**Run it before you reply.** Recording the answer is what makes it stick; a reply that is only
spoken back in chat does not. Say "recorded" only after the command has printed its confirmation,
and quote what it said. If the user's answer arrives in a session that has lost the earlier
context — an overnight reset, a new thread — run `python3 ~/.hermes/rx-review/rx.py confirm` first
to see what is outstanding, then record against that.

Copy their words verbatim rather than paraphrasing. "Super EPA (regular)" and "Super EPA
(NSF Certified for Sport)" are two products; answering with both names joined by a slash records
an answer that names neither.

If they genuinely do not know a value, use `--accept-all` and tell them it will be researched
with the gap noted.

That records the answer and closes the card, which is what the pipeline is waiting on. Do NOT
unblock this card: unblocking re-runs it, the card asks the same question again, and Hermes
treats the second block as a loop and moves it to triage - where it satisfies nothing and the
research stage waits forever.

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
`~/.hermes/rx-review/` other than `regimen.txt` and `CONFIRMED.txt`, and never create, edit or
complete a kanban card by hand — unblocking a card the pipeline blocked is the one exception.

- Copying a lab PDF into `inputs/raw/` fails → say which file and ask for it again. Never
  continue with a missing lab.
- The user's confirmation says a lab value is wrong → ask which marker, then re-run that
  lab's card.
- The user does not know a value the pipeline is asking about → add that product name on its
  own line to `~/.hermes/rx-review/inputs/CONFIRMED.txt` and tell them it will be researched
  with the gap noted.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.
