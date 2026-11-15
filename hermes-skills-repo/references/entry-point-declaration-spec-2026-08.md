# Entry-point declaration — spec (2026-08-28)

How to decide which `scripts/*` files the four contract rules
(`scripts/json-contract`, `scripts/top-level-guard`, `scripts/exit-code`,
vendor-helper carve-out) should apply to. **the owner's decision is pending** — this is the
recommended mechanism with measured numbers, not a shipped change. Full doc + evidence
index: profile `future-work/entry-point-declaration-spec-2026-08-28.md`.

## The decision: declare in SKILL.md. No manifest as source of truth.

**Entry point ⇔ the file is referenced as a command in SKILL.md code** (an inline code
span or a fenced code block). Reasoning:

1. **The agent reads SKILL.md** — it's the runtime contract, the only place a bot learns
   which commands exist. A per-skill manifest is a file the agent never reads; making it
   authoritative gives the gate and the runtime two sources of truth that can disagree.
2. **The declaration already lives there.** Census of all 11 skills with `scripts/`:
   **all 33 genuine entry points (32 direct + 1 hop) are already referenced in a code
   span** today, in one of three path forms — bare `` `name.py` ``,
   `` `${HERMES_SKILL_DIR}/scripts/name.py` ``, or absolute `` `~/…/scripts/name.py` ``.
   **Phase 1 needs zero SKILL.md edits.** A manifest would re-encode what the docs already say.
3. **Prior art agrees on the boundary, not the parser.** skill-lint + skillscheck both use
   "referenced from SKILL.md" as the orphan boundary; no public tool uses a manifest.
   skill-validator's BFS+import resolver is the most sophisticated public parser, yet
   replayed on this repo it misclassifies **23/23** of our entry points (our docs use bare
   filenames, not paths).

Rejected: `# @entry-point` source marker (scatters into files, drifts from the docs,
invisible to an agent that only reads SKILL.md); **shebang = entry point** (weak — 23
non-entry files here carry shebangs: probes, daemons, internal helpers; useful only as a
warning-layer detector, see below).

A manifest survives *at most* as a **derived baseline** (`entrypoints_baseline.json`,
generated + checked-equal, removals-only) — trigger_baseline.json pattern. Never
authoritative.

## The derivation algorithm (prototyped, `/tmp/derive_proto.py`)

1. **Inventory = `git ls-files <skill>/scripts/`** — NEVER walk disk (pallo/square carry
   ~1,400 untracked `.venv` files under `scripts/`; a disk walk invents ~2,800 phantom
   scripts, see Pitfall 9).
2. **Parse SKILL.md line by line.** A line whose stripped form starts with ``` toggles
   fence state; inside a fence the whole line is code text; outside, pair inline code
   spans **per line** — never pair backticks across line boundaries (see the L84 bug below).
3. **Direct:** in each code text, regex `([\w.${}~/~-]+\.(?:py|sh))\b` → take the last
   path component; if it's in the inventory, it's a direct entry point. Last-component
   matching is what makes all three path forms work with no standardization. **Prose
   mentions never count** (no backticks, no fence) — "the dedup lives in news-dedup.py"
   is a sentence, not a command; prose containment was the old bug.
4. **One hop:** a derived `.sh` whose **sole substantive command** (non-comment) invokes
   a tracked `.py` makes that `.py` an entry point. Repo-wide exactly one:
   `fetch-tickers.sh` `exec`s `fetch-tickers.py`. Distinction that keeps it mechanical:
   *delegation* (sole command) vs *orchestration* (`morning-briefing.sh` calls three
   `.sh` helpers — fan-out, no hop).

   **Repo-wide `.sh` census (2026-08-28; every `.sh` read in full):** only
   **daily-briefing** has `.sh` at all — 5 of them — out of 17 skills (+1 in archivist,
   out of lint scope). No other skill has any `.sh`. Shapes:
   - `fetch-tickers.sh` — **thin wrapper** (the one true hop): L33 `exec "$PY" …/fetch-tickers.py`;
     everything else is interpreter selection (venv → system fallback).
   - `fetch-news.sh` — **work-then-pipe**: loops topics, curls Brave, collects with `jq`,
     *then* pipes to `news-dedup.py` (L105). Real work before the delegation → its `.py`
     is a helper; NO hop even if documented.
   - `fetch-calendar.sh` — self-contained: bash + **embedded Python heredoc**
     (`python3 <<'PYEOF'`), no `.py` file at all.
   - `fetch-weather.sh` — pure bash + curl + jq; **no Python at all**.
   - `morning-briefing.sh` — **orchestrator**: fans out to the other four `.sh` + a
     heredoc + agent-browser; top of the pipeline, no hop.
   "Delegation" = a substantive (non-comment) line invoking python on a tracked
   `scripts/*.py` path. NOT delegation: comment mentions of sibling scripts, embedded
   heredocs, or delegation to an undocumented path.
5. **Exempt** (never entry points): `*_test.py`, `test_*.py`, vendored `skill_json.py`,
   `archivist/` (install kit, out of repo-mode lint).

**Result: 33 entry points (32 direct + 1 hop) across 10 skills.** bambu-store 1
(store_api.py); calendar 4; daily-briefing 4 (fetch-calendar.sh, fetch-tickers.sh,
morning-briefing.sh, fetch-tickers.py-hop); donations 1; gmail-thread-extract 1;
google-docs 1; pallo-logistics 13; square-appointments 6; web-access 1 (web_access.py);
whatsapp-backfill 1.

## Measured effect at `089cad5` (linter `--json`, not projected)

| rule | today | after gating | removed |
|---|---|---|---|
| scripts/json-contract | 17 | **5** | 12 |
| scripts/top-level-guard | 13 | **9** | 4 |
| scripts/exit-code | 15 | **0** | 15 |
| **total** | **45** | **14** | **31** |

- **Repo delta: 64 major → 33 major** (0 critical before and after).
- **14 survivors = 10 files, 3 skills**, all genuine entry points: daily-briefing
  (fetch-tickers.py); pallo (gina-pending, gina-where, pallo-modify-stay, pallo-trip-plan/
  prep/status); square (customer-info, list-merchants, square-list).
- **13 files reclassify non-entry** (premise-false findings vanish): bambu-store ×8
  (probes/logins), web-access ×3 (run_service, service, mcp_server), daily-briefing ×2
  (news-dedup, source-prefs).
- **Negative control verified:** the five fully-clean skills (calendar, donations,
  gmail-thread-extract, google-docs, whatsapp-backfill) lose zero findings.

## Re-measured effect at `a5173d2` (post-A1, re-ran the derivation 2026-08-28)

The table above was measured at `089cad5` (pre-A1) and is **superseded for planning** —
A1 deleted the daily-briefing mirror, so re-run the census before acting. Current state:

| rule | today (`a5173d2`) | after Phase 1 | removed |
|---|---|---|---|
| scripts/json-contract | 14 | **4** | 10 |
| scripts/top-level-guard | 13 | **9** | 4 |
| scripts/exit-code | 14 | **0** | 14 |
| **total** | **41** | **13** | **28** |

- **Repo delta: 94 major → 66 major** (0 critical before and after).
- **13 survivors = 9 files, 2 skills** (pallo 8, square 5): pallo gina-pending,
  gina-where, pallo-modify-stay, pallo-trip-plan/prep/status; square customer-info,
  list-merchants, square-list. daily-briefing is gone (A1); bambu-store and web-access
  drop all their contract findings at Phase 1 (their flagged files are all probes /
  service internals, premise-false).
- **28 findings removed** on 14 files: bambu ×8 (probes/logins/har_analyze),
  web-access ×5 service files + app.py + handlers.py (10 findings), pallo `triplib.py`.
- **Consequence worth planning around: `scripts/exit-code` reaches 0 at Phase 1** —
  it becomes promotion-safe immediately, with no Phase 2 script work. Phase 4's
  reserved call then splits: exit-code can promote on its own right after Phase 1;
  json-contract + top-level-guard still need Phase 2 first.
- **Derived entry set at `a5173d2`: 32 (32 direct + 0 hops)** — the hop consumer
  (fetch-tickers) is gone; hop mechanism retained, dormant. The five clean skills still
  lose zero findings (negative control re-verified on the current tree).

## Staged plan (#7/#9/#10 discipline)

- **Phase 1** — port `derive_entrypoints()` into `tools/lint_skills.py`; replace the
  `_is_library()` gate (L336) that scopes the four contract rules with "file is in the
  derived set." Keep `_is_library()` for file-hygiene rules (`silent-except` — a silent
  except in `triplib.py` is real even if nobody runs it). Battery: comment-only `"ok"`
  passes; code-span ⇒ entry; prose ⇒ not; `exec` hop works; fan-out `.sh` doesn't hop;
  git-ls-files inventory; L84 stray-backtick regression. Acceptance: battery green,
  repo byte-identical on all rules except the four contract rules (45→14), clean skills
  unchanged.
- **Phase 2** — fix the 14 survivors (mechanical): vendor `tools/skill_json.py` into the
  3 skills, `@guard` main, `ok()`/`fail()` replace print/exit, per-command `except
  Exception` funnels → contract rules at 0; run-verify one script/skill.
- **Phase 3** (user decision) — **3a `scripts/undocumented-shebang`** (major,
  warning-style): git-tracked `scripts/*` with a shebang, NOT a derived entry point, NOT
  a test, NOT a vendored helper → "document it or drop the shebang / rename `*_lib.py`."
  Census: **18 non-test files**. It's the public shebang heuristic used exactly as they
  use it: weak signal, never a gate. **3b optional baseline** for removal detection.
- **Phase 4** (the owner's reserved call, only after Phase 2) — promote json-contract +
  top-level-guard to critical, when the set is 33 known entry points, 0 premise-false, 0
  unknown.

**Out of scope (future):** behavioral `--smoke` (run entry points, assert JSON `ok` on
stdout — the real closure for the one-directional `ok` token test, but needs a
per-entry-point side-effect review); standardizing the three path forms (last-component
matching makes it unnecessary).

## Parsing hazards (both hit live, both in the prototype)

- **Backtick pairing across lines.** One stray backtick upstream of a code span
  misaligns *every* later span. Verified: square-appointments L84's `` `customer-info.py
  show` `` was invisible to a whole-doc `re.finditer` pair because an earlier line's
  backticks threw off parity — the file was tracked and documented yet dropped. Fix:
  pair spans **per line**.
- **`.venv` under `scripts/`.** Always `git ls-files`, never `os.walk` (Pitfall 9).

## Evidence index (re-runnable)

Live findings: `/tmp/lint_now.json`. Derivation + flagged intersection: `/tmp/derive_proto.py`.
Final counts: `/tmp/final_counts.py`, `/tmp/three_rule.py`, `/tmp/inventory_final.py`.
Undocumented-shebang census: `/tmp/undoc_check.py`. Public-parser replay: `/tmp/reach_replay.py`.
SKILL.md reference-shape census: `/tmp/skillmd_census.py`. L84 repro: `/tmp/ci_debug.py`.
