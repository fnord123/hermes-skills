# External skill-linter landscape + entry-point declaration analysis (researched 2026-08-28)

Condensed knowledge bank for the pending entry-point declaration decision
(scoping of `scripts/json-contract`, `scripts/top-level-guard`,
`scripts/exit-code`). Sources: GitHub/PyPI rule SOURCE CODE read-only
(fetched via raw.githubusercontent; tools NOT run — verified against rule
implementation, not just READMEs).

## Public linters (2026-08-28 snapshot)

| Tool | Lang | Stars | Reference / entry-point logic (from rule source) | Severity |
|---|---|---|---|---|
| agent-ecosystem/skill-validator | Go | 233 | `CheckOrphanFiles`: BFS reachability seeded from SKILL.md body — word-boundary path tokens (`lint` ≠ `linting`), extensionless fallback (subdirectory-only), Python import resolution (relative, `__init__.py` package bridges), root text files as bridges; skips `__init__.py` | warning |
| Swival/skillscheck | Py | 7 | `_check_orphan_files`: FLAT — `rel_path in body or basename in body`; `scripts/ references/ assets/` only; skips `__init__.py` | info |
| himself65/skill-lint | TS | 14 | `checkUnreferencedFiles`: `references/` only — basename in body, plus ONE transitive hop (another reference file mentions the basename) | warning |
| William-Yeh/agent-skill-linter | Py | 10 | `check_pep723_entry_points`: **shebang on line 1 = entry point** (the only tool with an explicit entry-point classifier); then requires PEP 723 `# /// script` block for `uv run` | warning |
| swarmclawai/agent-skills-lint | TS | 4 | no reference/orphan rule | — |

Adjacent (not linters of this class): skills.sh = distribution registry
(`npx skills add`, 1.3M installs tracked); skillscheck.ai npm pkg =
verification commands; skillscore (Dart, 0–100); ~7 more PyPI packages,
mostly security scanners. The agentskills.io spec ships NO official
validator.

## What this means for the entry-point declaration decision

1. **Nobody uses a manifest.** Every tool that touches this defines
   "entry point / referenced file" as "referenced from SKILL.md".
2. **Reference-ness is a DISCOVERY warning, never a contract gate.**
   skill-lint's exact message: "Agents read a reference file only when the
   body points them at it, so this one never loads." No public tool
   conditions script-BEHAVIOR linting (JSON contract, exit code, guards) on
   entry-point status. Building behavior checks on top of entry-point
   scoping is still our unique mechanism.
3. **Replay of their four algorithms on this repo** (the 23 files behind the
   30 contract findings, 2026-08-28): **BFS 0/23, flat 9/23, transitive
   15/23, shebang 22/23.** The best public algorithm misclassifies ALL 23:
   our SKILL.md files reference scripts by BARE FILENAME (`gina-pending.py`
   in a markdown command table), and skill-validator's word-boundary matcher
   only accepts the full root-relative path (`scripts/gina-pending.py`), with
   its extensionless fallback restricted to subdirectory-relative forms.
   Shebang over-claims (22/23 includes plumbing); flat/transitive count
   dev-doc mentions. **No public algorithm cleanly separates our genuine
   entry points from orphan plumbing.**
4. **`.venv` hazard (repo-specific):** `pallo-logistics/scripts/` and
   `square-appointments/scripts/` each carry ~1,400 UNTRACKED `.venv` files
   on disk (gitignored via per-dir `.gitignore`). Any disk-walking scanner
   invents ~2,800 phantom scripts; pip's RECORD files "mention" hundreds of
   basenames — skill-lint's transitive method reaches 1,370/1,392 of pallo's
   inventory via RECORD files alone. **Any future mechanism must iterate
   `git ls-files` output, never the disk.**
5. **Conclusion (2026-08-28, the owner's decision pending):** declare entry
   points in SKILL.md — it is the agent's RUNTIME contract and already
   carries the declaration; a manifest would be a second source of truth the
   agent never reads, so linter and agent could disagree. To make SKILL.md
   machine-parseable, standardize the reference form (bare `<name>.py` in
   the command table) and parse that form specifically, not loose prose
   containment. At most a DERIVED baseline (the `trigger_baseline.json`
   pattern) for review ergonomics — validated against SKILL.md every run,
   never authoritative on its own.

## Where we are unique (no public tool has these)

- Script-behavior rules: `scripts/json-contract`, `scripts/top-level-guard`,
  `scripts/exit-code`, `scripts/confirm` — nobody lints what a script DOES.
- `routing/triggers-baseline` removal detection.
- Vendored-helper carve-out (vendored `skill_json.py` + actual `ok(`/`fail(`
  calls satisfy the contract; unused imports don't count).

## Parity-gap candidates for future rule batches

Broken local markdown links; orphaned supporting files; token budgets;
secret leakage; description "use when" hints.

## Caveats

Stars 4–233; the 2026 ecosystem is ~6 months into the spec; verified from
README/metadata + rule source only, tools not executed; the landscape will
move fast — re-pull rule source before relying on specifics again.
