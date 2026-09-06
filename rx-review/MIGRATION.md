# rx-review relocation — the phase record

Moving the pipeline from `~/.hermes/rx-review` (repo `fnord123/Hermes`) into the skill at
`~/hermes-skills/rx-review/` (repo `fnord123/hermes-skills`), executed 2026-09-04 to 2026-09-06.
This file is the durable record of the plan — it previously existed only in chat history.

**Target state.** Code lives in `scripts/`, docs at the skill root; the skill repo's CI and the
local pre-push guard run the full test battery; the Hermes repo keeps only the `rx-*` profiles,
`provision-profiles.py`, and a thin allowlist shim; no code, tests, or docs remain in the old
location.

| Phase | Work | Commit(s) | Status |
|---|---|---|---|
| 0 | Pre-move verification: inventory of everything tracked in Hermes.git (24 files, 17,946 lines — code, tests, docs, ops scripts); kanban board confirmed empty, no in-flight risk | — | done 2026-09-04 |
| 1 | Relocate the code: files into `scripts/`, docs at the skill root; every state anchor made `__file__-relative` (no hardcoded `~/.hermes`); destructive `--yes` renamed `--confirm`; old directory replaced by a local symlink; old copy deleted from the Hermes repo | `a67c45b` (hermes-skills), `0523638` (Hermes) | done 2026-09-04 |
| 2 | Card-map check moves with the code: the CI step that validates ARCHITECTURE.md's generated card map moves from the Hermes repo workflow into the hermes-skills workflow | `e1b4637` | done 2026-09-04 |
| 3 | Allowlist goes dual-path: `terminal-pipeline-only.sh` accepts the new path; the old path kept as a transitional alias; the allowlist's own bash block-test now runs in CI beside the real file | `05ac2ff` | done 2026-09-04 |
| 4 | Hermes repo cleanup: gitignore retires the rx-review script allowlist; CI drops the rx-review test and allowlist steps; `hooks/terminal-pipeline-only.sh` becomes a shim to the skill's copy | `4ae7bd1`, `910f132`, `b591135`, `4de15ec` | done 2026-09-04 |
| 5 | Every reference re-pointed: 97 old-path refs across 15 files (card bodies, help text, tests, docs) now read `~/hermes-skills/rx-review/scripts`; the allowlist keeps the old-path alias with explicit dual-path test cases until Phase 8 | `0fb95db` | done 2026-09-04 |
| 6 | The local guard moves with the code: hermes-skills `tools/install-git-hooks.sh` pre-push runs the full six (lint, lintself, vendor, run_tests, cardmap, allowlist battery); the Hermes pre-commit is reduced to provisioning-only; the stale `rx-review/scripts/install-git-hooks.sh` deleted | `98e8f06` | done 2026-09-04 |
| 7 | Stale SKILL.md content fix: the FIB-4 ingest paragraph carried pre-split single-verb wording ("merge happens after branch merge"); `merge-labs` is live, so the hedge was dropped. (Path references had already been fixed in Phase 5; this was the remaining *content* staleness.) | `da554e0` | done 2026-09-04 |
| 8 | Retire the transition: remove the `~/.hermes/rx-review` symlink and the old-path alias — the allowlist's dual-path regex and its two transitional test cases in `test-terminal-pipeline-only.sh` | — | **pending** |

**Phase 8 gate.** One clean run with no in-flight tasks on the rx-review board, so nothing
resolves through the old path at the moment of removal. Readiness check (satisfied 2026-09-06):
the kanban `tasks` table contains 0 tasks, and the Hermes pre-commit no longer references the
old path (Phase 6).

**Ordering note.** Phases 0–7 are done and pushed; the two repos' `main` branches are clean.
Do not delete the symlink or the allowlist alias before a run completes through the new path —
workers created before the move may still carry old-path card bodies.
