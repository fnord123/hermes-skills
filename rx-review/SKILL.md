---
name: rx-review
description: "Review the user's medications and supplements against their blood tests. Collects lab PDFs and the regimen by conversation, researches each substance, adversarially verifies every claim, and produces a discussion brief for their prescriber. Use when the user asks to review their meds, supplements, or labs, add new lab results, or check on a review already running."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [Health, Labs, Medications, Supplements, Research]
---

# Medication and supplement review

You collect the user's labs and regimen through conversation, then run a review pipeline that
researches each substance, checks interactions and timing, and adversarially verifies every
claim before writing a brief for their prescriber.

The user never edits files. You do all of that for them.

Everything runs through one command:

    python3 ~/.hermes/rx-review/rx.py <intake|confirm|status|analyze>

## 1. Collect the labs

Ask the user to attach their lab result PDFs. Attachments arrive as local file paths — copy
each one into the intake folder, keeping its original name:

    cp "<attached path>" ~/.hermes/rx-review/inputs/raw/

If a path does not exist or the copy fails, tell the user which file failed and ask them to
send it again. Do not continue with a missing lab.

## 2. Collect the regimen

Ask the user what they take. Request, for each item: the product name, the dose with its unit,
and what time of day they take it. Tell them prescriptions matter as much as supplements,
because drug-supplement interactions are the most valuable thing this review finds.

Write exactly what they say — one item per line, their words — to:

    ~/.hermes/rx-review/inputs/regimen.txt

Do not correct spellings, convert units, or fill in a dose they did not give. A later step
asks them about anything unclear.

## 3. Run intake

    python3 ~/.hermes/rx-review/rx.py intake

This creates transcription work for each new lab PDF and for the regimen. Tell the user it is
running and roughly how many items it is processing.

Check progress with:

    python3 ~/.hermes/rx-review/rx.py status

Intake is finished when no card is `running` or `ready`. A large panel can take an hour.

## 4. Look up the products you can

Run `intake` again once the regimen inventory is built:

    python3 ~/.hermes/rx-review/rx.py intake

For any item where the user gave a product name but no dose, this creates a lookup that
fetches the manufacturer's published Supplement Facts, then rebuilds the inventory with them.
Tell the user which products are being looked up. Wait for those to finish before step 5 —
most missing doses resolve here without asking them anything.

## 5. Ask about anything still unclear

    python3 ~/.hermes/rx-review/rx.py confirm --json

This returns `unresolved` — items whose dose, unit, or identity could not be established even
after the lookups.

For EACH unresolved item, use the `clarify` tool to ask the user one specific question, with
choices when you can offer sensible ones. Ask about the actual ambiguity. Examples of the
shape:

- a drug name that looks misspelled → offer the likely correct spelling and "keep as written"
- a dose that looks implausible for that substance → offer the plausible value and "it is correct"
- a product with no dose → ask for the amount per serving from the label
- a product name that is not a real product → ask which product they actually have

When the user answers, rewrite `~/.hermes/rx-review/inputs/regimen.txt` with their corrections
and run `rx.py intake` again, then `rx.py confirm --json` again.

If the user does not know a value, tell them you will record it as unknown and it will be
researched with that gap noted. Add that product name on its own line to:

    ~/.hermes/rx-review/inputs/CONFIRMED.txt

Repeat until `confirm --json` reports `"clear": true`.

## 5. Start the analysis

    python3 ~/.hermes/rx-review/rx.py analyze

This builds the research graph: one investigation per substance, one per out-of-range lab
marker, an interaction and timing screen, four independent adversarial reviews, and a final
brief. Tell the user how many pieces of work it created and that it runs unattended — a full
review usually takes overnight.

It refuses to start while anything is unconfirmed. If it refuses, go back to step 4.

## 6. Report progress and deliver

When the user asks how it is going, run `rx.py status` and describe it in plain language: what
has finished, what is running, what is waiting.

Reports land in `~/.hermes/reports/rx-review/`. When `BRIEF.md` and `CRITIQUE.md` both exist,
the review is done. Send the user `CRITIQUE.md` first — it lists what the final reviewer
challenged — then `BRIEF.md`.

Tell the user plainly that the brief is evidence and questions for their prescriber or
pharmacist to confirm, not medical advice, and that no part of it recommends a dose.

## Adding labs later

The user can send new lab PDFs at any time. Copy them in, run `rx.py intake`, then `analyze`
again. Only the new work is created; finished work is not repeated.

## If something fails

Report the exact error to the user and ask how they want to proceed. Do not edit files under
`~/.hermes/rx-review/` other than `regimen.txt`, `CONFIRMED.txt`, and copying PDFs into
`inputs/raw/`, and do not create or modify kanban cards by hand.
