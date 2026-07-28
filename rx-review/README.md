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
