---
name: rx-review
description: "Review the user's medications and supplements against their blood tests. Takes lab PDFs and a regimen — typed in chat, or from a source you resolve first (a Google Doc via the google-docs skill, a local file) — then researches each substance, looks up doses for branded products, screens interactions and timing, adversarially verifies every claim, and produces a discussion brief for their prescriber. Use when the user asks to review their meds, supplements, or labs, add new lab results, or check on a review already running."
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

    python3 ~/.hermes/rx-review/rx.py <regimen|intake|verify-labs|confirm|status|analyze|reset>

## 1. Collect the labs

Ask the user to attach their lab result PDFs. Attachments arrive as local file paths — copy
each one into the intake folder, keeping its original name:

    cp "<attached path>" ~/.hermes/rx-review/inputs/raw/

If a path does not exist or the copy fails, tell the user which file failed and ask them to
send it again. Do not continue with a missing lab.

## 2. Collect the regimen

The regimen can come from three places. Take whichever the user offers.

**They point you at a source they already keep** — "it's in my regimen doc", "search my docs",
"it's in ~/notes/meds.md". Resolve the pointer YOURSELF with whatever skill fits — the
`google-docs` skill finds and reads a Google Doc, the file tools read a local file — then save
the text to a file and hand the path over:

    python3 ~/.hermes/rx-review/rx.py regimen --from /tmp/regimen-source.txt

Or pipe it straight in:

    ... | python3 ~/.hermes/rx-review/rx.py regimen --stdin

This step only accepts a path or stdin. Locating the source is your job, not its.

**They type it in chat** — write their words to a file and pass that same way.

**They have not written it down** — ask. For each item: product name, dose with its unit, and
what time of day. Tell them prescriptions matter as much as supplements, because
drug-supplement interactions are the most valuable thing this review finds.

Whatever the source, record it VERBATIM. Do not correct spellings, convert units, or fill in a
dose that is not there. Later steps look products up and ask about anything still unclear.

## 3. Run intake

    python3 ~/.hermes/rx-review/rx.py intake

This creates transcription work for each new lab PDF and for the regimen. Tell the user it is
running and roughly how many items it is processing.

Check progress with:

    python3 ~/.hermes/rx-review/rx.py status

Intake is finished when no card is `running` or `ready`. A large panel can take an hour.

## 4. Show the user their labs and get a yes

    python3 ~/.hermes/rx-review/rx.py verify-labs

This re-opens every source PDF and confirms each transcribed value appears in it verbatim, so
you are not asking the user to proofread arithmetic. Report what it says:

- how many markers were read, from how many PDFs
- **the out-of-range list, in full** — those drive the research
- anything it could not verify

Then ask the user to confirm the out-of-range list looks like their results. Use `clarify` with
"yes, that's right" / "something's wrong". The check catches invented or mistyped values; it
cannot catch a correct number attached to the wrong marker, which is what their eyes are for.

If they say something is wrong, ask which marker, and re-run that lab's card rather than
editing the file yourself.

## 5. Look up the products you can

Run `intake` again once the regimen inventory is built:

    python3 ~/.hermes/rx-review/rx.py intake

For any item where the user gave a product name but no dose, this creates a lookup that
fetches the manufacturer's published Supplement Facts, then rebuilds the inventory with them.
Tell the user which products are being looked up. Wait for those to finish before step 6 —
most missing doses resolve here without asking them anything.

## 6. Ask about anything still unclear

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

## 7. Start the analysis

    python3 ~/.hermes/rx-review/rx.py analyze

This builds the research graph: one investigation per substance, one per out-of-range lab
marker, an interaction and timing screen, four independent adversarial reviews, and a final
brief. Tell the user how many pieces of work it created and that it runs unattended — a full
review usually takes overnight.

It refuses to start while the labs are unverified or a regimen item is unconfirmed. If it
refuses, it prints exactly what is outstanding — go back to step 4 or 6.

## 8. Report progress and deliver

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
