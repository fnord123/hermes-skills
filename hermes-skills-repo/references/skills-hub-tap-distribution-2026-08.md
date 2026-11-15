# Hermes skills hub / tap distribution — source-verified mechanics (2026-08-31)

Grounding for the "distribute house skills to other profiles" decision. Verified in
the LOCAL install source: `tools/skills_hub.py` (HubLockFile, TapsManager,
`check_for_skill_updates`) and `hermes_cli/skills_hub.py` (do_check/do_update/do_tap),
plus `hermes skills --help` output. Not marketing.

## The surface (CLI, verified via --help on this install)

- `hermes skills install <identifier | https-URL-to-SKILL.md>` — identifier form:
  `owner/repo/path/skill`; URL form fetches SKILL.md + referenced support files.
  `--category`, `--name` (when frontmatter lacks `name:`), `--force` (blocked scan
  verdict), `-y`. Installs run a security scan; a blocked verdict stops it.
- `hermes skills tap add owner/repo` / `list` / `remove` — register a GitHub repo as a
  standing source. Taps default to looking under `skills/` in the repo.
- `hermes skills search <q>` — sources: official, skills.sh, well-known, github,
  clawhub, lobehub, browse.sh, nvidia, openai, anthropic, huggingface, voltagent,
  gstack, minimax.
- `inspect` (preview pre-install), `check` (update scan), `update [--force]`,
  `audit` (re-scan), `uninstall`.
- Installs take effect in NEW sessions (`/reset` or `--now` for the current one).

## State files (per profile, under `<profile>/skills/.hub/`)

- `taps.json` — `[{"repo": "owner/repo", "path": "skills/"}]` (TapsManager).
- `lock.json` (HubLockFile) — per installed skill: `source`, `identifier` (full
  `owner/repo/path/skill`), `trust_level`, `scan_verdict`, `content_hash`,
  `install_path`, `files`, `installed_at`, `updated_at`. One `cat` = full provenance.
- `audit.log` — append-only line per install/uninstall (timestamp, action, skill,
  source:trust, verdict).
- All paths resolve via `get_hermes_home()` — profile-safe by construction.

## check / update mechanics (the part that decides fleet safety)

`check_for_skill_updates` (tools/skills_hub.py:4148):
1. For each lock entry, candidate sources are filtered to match the RECORD source
   (`_source_matches`). If none match (tap removed, repo renamed) → status
   `unavailable`. **No fallback to other sources** — a same-named skill in another
   registry can never satisfy a check (names are not namespaced; the fallback was
   removed as "unsafe by construction").
2. Otherwise fetch the bundle, compare `content_hash` vs `bundle_content_hash` →
   `up_to_date` | `update_available`.

`do_update` (hermes_cli/skills_hub.py:1107):
- Takes only `update_available` entries.
- **Local-edit protection:** recomputes `content_hash(skill_path)` from disk; if it
  differs from the recorded hash → SKIP with a visible "you have local edits (update
  would overwrite them)" message. `--force` overrides. Mirrors the bundled-skill
  user-modified protection; the comment cites paperclip#10978: destructive replacement
  must be an explicit caller choice, never a rerun default.
- **Provenance pinning on update:** installs via `do_install(..., source_id=<recorded
  source>)` so an update can never re-point the skill at a different registry.
- Note: `do_update` does an rmtree-style replace (`do_install(force=True)`) — there is
  NO merge of local edits. Reconcile first or lose them.

## The gap (why a convention + drift detector are needed)

- Nothing tells the profile the copy is managed: no read-only marker is injected when
  a tap/hub-installed SKILL.md loads (contrast: plugin skills DO get a load banner).
- If a bot edits its installed copy, nothing stops it at write time. Consequence is
  deferred: `check`/`update` see the hash mismatch and the skill silently stops
  receiving updates until reconciled or `--force`d. Same failure class as the .226
  build-context drift, at skill granularity.
- `list-modified`/`diff`/`reset` (the user-modified tracking) cover BUNDLED skills;
  hub/tap skills lean on the lock hash instead.
- Taps vs `skills.external_dirs`: external_dirs is the fleet mechanism ALREADY in use
  for `{{REPO_DIR}}` (all profiles inherit it; updates ride a git commit — no
  per-profile drift possible as long as nobody edits the shared checkout). Taps add a
  per-profile install with its own drift surface. They are not exclusive: keep
  external_dirs for existing profiles, consider a tap only for consumers that can't
  see the local repo.
- **Orphan-lock blind spot (measured 2026-09-01):** `check` iterates lock entries and
  compares RECORDED vs UPSTREAM hashes only — it never verifies the install dir exists
  on disk (the disk-vs-lock compare lives in `do_update`, and only for
  `update_available` entries). Two states are invisible to `check`/`update` alike:
  (1) lock entry present, install dir absent (orphan lock); (2) dir present with disk
  hash != lock hash (local edits) — the latter surfaces as a skip only when an update
  is also available. A real fleet drift audit must read each `installed` entry and
  hash the dir itself with the hermes-agent's own `content_hash`.
- **Per-profile runs:** `hermes skills check` is scoped by `HERMES_HOME` (the gateway
  sets it per profile); `HERMES_PROFILE` alone is NOT honored for home resolution
  (it feeds kanban authoring). Run another profile's check as
  `HERMES_HOME={{HERMES_HOME}}/profiles/<p> hermes skills check` — read-only
  (source-verified 2026-09-01: `check_for_skill_updates` reads lock, fetches,
  compares, no writes).
- **`lock.json` shape:** `{version, installed}` where `installed` is a name→entry map
  (entry fields as above; `install_path` is relative to the profile's `skills/` dir).
- **Resolution of config-less profiles (source-verified 2026-09-01, CURATOR
  CORRECTION):** a profile WITHOUT a `config.yaml` at its home sees NO external
  dirs — `get_external_skills_dirs` reads `get_config_path()`
  (= `HERMES_HOME/config.yaml`, `skill_utils.py`/`hermes_constants.py`);
  file absent → `return []`. It does NOT fall back to `{{HERMES_HOME}}/config.yaml`
  and NOT to `DEFAULT_CONFIG` (the `config_defaults.py` citation is a red
  herring: `DEFAULT_CONFIG` is the build-time scaffold, not the fallback for
  missing files). The home-based `default` profile DOES see the repo because
  `HERMES_HOME={{HERMES_HOME}}` and `{{HERMES_HOME}}/config.yaml` declares
  `external_dirs: ['{{REPO_DIR}}']` — a live-config fact, true only while
  that file declares it. a profile with no config at its home sees none until one
  is added. A stale `{{HERMES_HOME}}/profiles/default/` directory (SOUL.md + empty
  skills dirs, no config.yaml) shadows the real home-based default in naive
  `ls`-based censuses — treat `profiles/<x>/config.yaml` as the unit, and
  remember the default profile's home is `{{HERMES_HOME}}` itself.
- **Measured fleet state (2026-09-01, after commit `3888faa`; re-census before
  quoting):** 20 profiles. Channel A (external_dirs `{{REPO_DIR}}`): 18
  configs — 17 profile configs + `{{HERMES_HOME}}/config.yaml` (which serves the
  home-based `default` profile) — live inheritance for 18/20 profiles (one profile
  has no config at its home → `[]`; another overrides
  `external_dirs: []` → sees none). Channel B: 13 profiles hold one shared set of 7
  tap skills (agentmail-lite, daily-briefing, investment-hypothesis-investigation,
  municipal-bond-analysis, pet-care-tracker, pre-ipo-investment-analysis,
  stock-investment-analysis), lock hashes UNIFORM across holders; no tap copy of
  calredteam anywhere. Pre-existing drift (NOT caused by any commit; visible since
  at least the 2026-08-31 census): 84 lock entries = 64 orphan locks (dir absent —
  all 7 in four profiles + daily-briefing, the two investment
  skills, pre-ipo, stock in all 9 rx-*) + 27 real local edits (9 rx-* ×
  agentmail-lite/municipal-bond-analysis/pet-care-tracker: disk hash != lock hash;
  the ones `update` would skip without `--force`).

## Mitigation ladder (if we ever tap-distribute house skills)

1. SKILL.md convention line ("installed from the shared repo — read-only; propose
   fixes upstream") — the ONLY mitigation that reaches the model.
2. Per-profile cron: `hermes skills check` (or `--json` if/when added) → alert on any
   `update_available` or local-edit skip. Turns silent update-freeze into a weekly
   ping.
3. Triage by churn: tap-distribute stable docs-like skills; keep hot skills
   (google-docs and its profile-local twin are the exemplar of what NOT to
   tap-distribute) on external_dirs.

## URL install of a root-of-repo skill (2026-09-02, verified)

- `hermes skills install <raw URL to SKILL.md>` fetches SKILL.md plus the
  support files it references (`_referenced_support_paths` in
  `tools/skills_hub.py`) — verified with asd-ste100: 3 files installed
  (SKILL.md, references/writing-rules.md, examples/before-after.md), scan
  SAFE, lock entry written.
- The GitHub identifier form requires `owner/repo/<subdir>` (3+ parts;
  `_list_skills_in_repo` only enumerates SUBDIRECTORIES under the tap path,
  and `fetch` returns None for a 2-part identifier). A skill at the repo
  ROOT cannot be addressed by identifier — `hermes skills install
  danyuchn/asd-ste100-skill` fails (verified). Taps default to looking
  under `skills/`, so they don't see it either. Use the URL form for
  root-of-repo skills.
- Installs land in the profile's own `skills/<name>/` dir (the operator profile:
  `{{HERMES_HOME}}/profiles/{{OPERATOR}}/skills/asd-ste100/`) — visible to that
  profile's kanban workers via a force-load `--skill <name>`.
