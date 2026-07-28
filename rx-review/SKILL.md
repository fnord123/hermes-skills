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
| `intake` | Starts the pipeline over whatever is in the inputs folder. Run it once to begin, and once again after you apply the user's answers. |
| `status` | Reports where the pipeline is — finished, running, waiting. Use this whenever the user asks how it is going. |
| `verify-labs` | Gets the full transcription picture for the "CONFIRM YOUR LABS" card: markers read, out-of-range values, anything unverified. |
| `confirm --json` | Lists the items the "Confirm N item(s) before research" card is waiting on, with what intake already knows about each. |

Those five are yours. Every other verb the script accepts belongs to the pipeline — it runs
them itself, on its own schedule.

## 1. Collect the labs

Ask for the lab PDFs. Attachments arrive as local paths — copy each into the intake folder,
keeping its name:

    cp "<attached path>" ~/.hermes/rx-review/inputs/raw/

If a copy fails, say which file and ask for it again. Never continue with a missing lab.

## 2. Collect the regimen

Take whichever the user offers:

**A source they already keep** — "it's in my regimen doc", "search my docs", "~/notes/meds.md".
Resolve it with whatever skill fits, then pipe it straight in. `regimen --stdin` accepts either
plain text or a skill's JSON output, so no unwrapping step is needed.

For a Google Doc, that is one command:

    python3 ${HERMES_SKILL_DIR}/../google-docs/scripts/docs.py read <doc-id> \
      | python3 ~/.hermes/rx-review/rx.py regimen --stdin

For a local file:

    python3 ~/.hermes/rx-review/rx.py regimen --from ~/notes/meds.md

Pipe the source directly. Do not write an inline `python3 -c` to reshape it in between.

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

Copy the new PDFs into `inputs/raw/` and run `rx.py intake` once. Only the new work is
created; finished work is never repeated.

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
