---
name: rx-review
description: "Review the user's medications and supplements against their blood tests. Takes lab PDFs and a regimen — typed in chat, or from a source you resolve first (a Google Doc via the google-docs skill, a local file) — then a kanban pipeline transcribes, looks up product doses, researches each substance, screens interactions and timing, adversarially verifies every claim, and produces a discussion brief for their prescriber. Use when the user asks to review their meds, supplements, or labs, add new lab results, answer a question the review is waiting on, or check on a review already running."
version: 2.0.0
license: MIT
metadata:
  hermes:
    tags: [Health, Labs, Medications, Supplements, Research]
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

Everything is one command:

    python3 ~/.hermes/rx-review/rx.py <regimen|intake|status|verify-labs|confirm|analyze|reset>

You only ever need `regimen`, `intake`, and `status`. The rest are run BY the pipeline.

## 1. Collect the labs

Ask for the lab PDFs. Attachments arrive as local paths — copy each into the intake folder,
keeping its name:

    cp "<attached path>" ~/.hermes/rx-review/inputs/raw/

If a copy fails, say which file and ask for it again. Never continue with a missing lab.

## 2. Collect the regimen

Take whichever the user offers:

**A source they already keep** — "it's in my regimen doc", "search my docs", "~/notes/meds.md".
Resolve it yourself with whatever skill fits (`google-docs` finds and reads a Google Doc; file
tools read a file), save the text, and hand over the path:

    python3 ~/.hermes/rx-review/rx.py regimen --from /tmp/regimen-source.txt

or pipe it: `... | python3 ~/.hermes/rx-review/rx.py regimen --stdin`

Finding the source is your job. That command only accepts a path or stdin.

**Typed in chat** — write their words to a file, pass it the same way.

**Not written down** — ask. Per item: product, dose with unit, time of day. Prescriptions
matter as much as supplements; drug-supplement interactions are the most valuable finding.

Record it VERBATIM. Never correct a spelling, convert a unit, or invent a dose. The pipeline
looks products up and asks about the rest.

## 3. Start it

    python3 ~/.hermes/rx-review/rx.py intake

That is the only time you push. Tell the user what it created and that it runs on its own —
transcription takes a while, a large panel can take an hour.

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

If they confirm, unblock the card. If not, ask which marker and re-run that lab's card.

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

Apply their answers by rewriting `~/.hermes/rx-review/inputs/regimen.txt`, then run
`rx.py intake` once so the corrected text is picked up. If they genuinely do not know a value,
add that product name on its own line to `~/.hermes/rx-review/inputs/CONFIRMED.txt` and tell
them it will be researched with the gap noted.

Then unblock the card.

### "Start the research stage" is blocked

It tried to start and something was still outstanding. Its block reason says what. Deal with
that, then unblock it — it retries itself. Never use `--force`.

## 5. Deliver

Reports land in `~/.hermes/reports/rx-review/`. When `BRIEF.md` and `CRITIQUE.md` both exist,
it is done. Send `CRITIQUE.md` first — what the final reviewer challenged — then `BRIEF.md`.

Say plainly that this is evidence and questions for their prescriber or pharmacist to confirm,
not medical advice, and that nothing in it recommends a dose.

## Progress

When asked how it is going, run `rx.py status` and describe it plainly: finished, running,
waiting. Do not run anything else to "help it along".

## Adding labs later

Copy the new PDFs into `inputs/raw/` and run `rx.py intake` once. Only the new work is
created; finished work is never repeated.

## If something fails

Report the exact error and ask how they want to proceed. Do not edit files under
`~/.hermes/rx-review/` other than `regimen.txt` and `CONFIRMED.txt`, and never create, edit or
complete a kanban card by hand — unblocking a card the pipeline blocked is the one exception.
