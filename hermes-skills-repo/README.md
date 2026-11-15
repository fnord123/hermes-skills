# hermes-skills-repo

The house-repo rubric skill for `hermes-skills` — the rule set, linter
interpretation, and pipeline mechanics every skill in this repo is authored
and graded against. This repo copy is its **canonical home**. The pipeline
adopted it from the previously hand-made per-role copies (issue #43). The
roles install it from here instead of carrying copies.

## Why this skill exists

CONVENTIONS.md states the rules. `tools/lint_skills.py` enforces them
mechanically. The gap between the two is procedural knowledge an author
needs in context:

- which findings are real and which are known false-positive classes
- how to change a rule without moving the whole repo's baseline
- how the pipeline cards operate

The seven pipeline role profiles force-loaded copies of it: graded, but
unversioned. It now lives where an author can review it through the
pipeline, like any other skill.

## Layout

- `SKILL.md` — the model-facing doctrine (rubric, lint interpretation,
  rule-change procedure, host-neutrality policy, pipeline mechanics).
- `references/` — the deep files SKILL.md points to: the linter rule
  map, the entry-point declaration spec, the host-neutral doctrine
  extraction workflow, and distribution and deployment notes. Some are
  dated and instance-adjacent: re-census before quoting any count in
  them. References are not linted (the linter checks SKILL.md + scripts).
- `templates/PROFILE.example` — the `{{tokens}}` used in SKILL.md after the
  host-neutrality transform, each with where its value comes from. The
  filled instance file is host-bound and is NOT committed.
- No `scripts/` yet: `scripts/trace-handoff.py` is declared as a script
  contract in SKILL.md ("The tool"); the pipeline's Scripter stage
  implements it to that contract.

## Import fidelity (issue #43)

The import source was the installed role copies (all seven byte-identical
at import time). It preserved meaning verbatim: it reworded no rule,
threshold, or trigger. Applied on top:

- the host-neutrality transform: host-bound paths, profile/role names,
  model ids, repo slugs, personal names, and run ids became `{{tokens}}`
  or neutral wording. This follows the skill's own standing policy.
- linter conformance — routing surface in the description (PREFER clause +
  trigger list), a `When NOT to use` section, the mandated error sentence,
  the tools table, `requires_toolsets`.
- removal of the historical-narration and version-arrow shapes the
  `body/history` critical rule forbids in model context (the live rule in
  each affected sentence stands unchanged).
- the stale `skill-kanban/` path pointers now name the renamed
  `skill-foundry/` skill, and the empty `references/skill-kanban-board-*.md`
  reference is gone (its content moved into skill-foundry).
