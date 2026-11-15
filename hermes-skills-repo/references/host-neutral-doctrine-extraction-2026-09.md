# Host-Neutral Doctrine Extraction (verified 2026-09-03 — skill-kanban)

Class: promoting fleet-specific docs/work orders into the host-neutral house
repo (`{{REPO_DIR}}`). Trigger: a durability call where the source docs
carry host values (assignee, paths, model ids, run history).

## The split: doctrine vs instance

- DOCTRINE (committable): work orders, graph + rubric + mechanics,
  pitfalls (distilled, run ids stripped), card bodies as templates.
- INSTANCE (stays in the profile, gitignored by design): filled values,
  execution logs, decision ledgers, board/task/commit ids, censuses,
  personal names.
- If a live worker's handoff points at an instance file ("read the sibling
  canonical"), that file must STAY put — move only the doctrine, and update
  the local pointer doc to name the split + the commit sha.

## Tokens

`{{ASSIGNEE}}` `{{REPO_DIR}}` `{{CARDS_DIR}}` `{{MID_MODEL}}`
`{{HOUSE_SKILL}}` `{{STD_SKILL}}` — define per topic; document each token
and where its value comes from in a `PROFILE.example` beside the templates.

## Procedure

1. Read ALL source files in full before transforming — never transform from
   memory of a compacted session.
2. Enumerate host-bound tokens FIRST by grep (assignee, `/home/<user>`,
   model ids, `t_<hex8>`, repo org, incident hashes, personal names) — the
   counts justify the doctrine/instance split instead of assuming it.
3. Transform faithfully: keep work-order wording; replace only instance
   values with tokens; distill run history into a `known-pitfalls.md`
   with zero ids.
4. Close seams the transform exposes (e.g. a read step a doc implies but
   the work order never states) — in the template, not the instance.
5. Verify, in order, ALL FOUR (otherwise it is not de-hosted):
   a. Leak grep over the new tree:
      `grep -rnE '<assignee>|/home/<user>|<model id>|t_[0-9a-f]{8}|<org>|<incident hashes>'`
      → 0.
   b. Token inventory: `grep -rhoE '\{\{[A-Z_]+\}\}' . | sort | uniq -c`
      → only the declared tokens.
   c. Full-repo linter: `python3 tools/lint_skills.py --json` → exit 0 and
      0 new findings in the new dir. A docs dir without a `SKILL.md` is
      invisible to the skill list (like `archivist/`) — the full-repo run
      is the real gate, not `--skill <dir>`.
   d. Commit: explicit file staging (never `-A`), house-style commit
      (`<topic>: <imperative>`), push, then verify `git status -sb` shows
      `0 0` AND `git ls-tree origin/main` shows the tree on the remote.
6. Update the local pointer doc (the profile skill) to name the
   doctrine/instance split + the commit sha.
7. One-line operational state to memory: doctrine location + instance
   location.

## Pitfalls

- One `check-ignore` line is not a `.gitignore` reading. The fleet repo's
  `.gitignore` is deny-by-default + allowlist (root `/*`, then `!`
  re-allows); the profiles block re-allows exactly `config.yaml` +
  `SOUL.md` per profile. Read the file before citing a rule (2026-09-03:
  first answer inferred the whole file from one matched line — mechanism
  wrong, verdict right, the owner caught it).
- "git mv the files into the repo" is the wrong plan when the source is
  host-bound — this is a transform, not a move.
- Do not ship instance values as "obviously this fleet's value" — the leak
  scan is the only witness that accepts.
