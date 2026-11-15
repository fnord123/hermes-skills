# gateway key architecture + kanban model/provider pinning (verified 2026-09-03)

Knowledge bank for "which key does the kanban use?" class questions and for the
pending Option-B dedicated pipeline-key plan. NO secret values in this file —
variable names only (a literal `KEY=*** value here would trip the
secret-redactor; build any value at runtime).

## Key architecture (measured 2026-09-03)

- Exactly ONE model key per profile: `LITELLM_API_KEY` in `profiles/<profile>/.env`
  (mode 600). No per-board, per-card, or inline `api_key` in any config.yaml.
- Root `{{HERMES_HOME}}/config.yaml` `providers:` block: `api_key: ${LITELLM_API_KEY}`;
  base_url `http://{{SERVICE_HOST}}:4000/v1`. ALL model tiers (`{{MID_MODEL}}`,
  `{{HIGH_MODEL}}`, compaction, approval) point at the SAME proxy + SAME
  key — a model pin changes the route, never the credential.
- Env-var placeholder resolution happens ONCE per process, at config load,
  against that process's environment (the profile .env).

## Consequences for live runs (all verified against source)

- A running worker is never affected by later `.env` edits — its env is frozen at
  spawn. Cards spawned AFTER the edit resolve the new key at their own spawn.
- A run can straddle: early cards on the old key, later ones on the new. Structurally
  fine (handoffs carry no key state) — but billing attribution straddles too, and
  spend already made on the old key is unrecoverable to the new key.
- Revoking the old key 401s everything still on it — including the live chat session
  (same profile, same key). Safe rotation order: create new key on proxy → verify
  with an authenticated round-trip (KEYOK) → edit `.env` → revoke old ONLY when no
  live session or worker is running on it. Both keys coexist during the window —
  that is what makes the swap interruption-free.
- Key identity is NOT readable via API: `/key/list` returns opaque hashes;
  `/key/info/all` is 404 on this proxy. Attribution = proxy logs/UI + the KEYOK
  round-trip.

## Kanban model/provider pinning (Option-B substrate, verified 2026-09-03)

- The worker-side `kanban_create` tool schema carries BOTH `model` and `provider`
  (`tools/kanban_tools.py:2286` / `:2295` — provider requires model). A card's
  handoff can therefore pin the successor's credential route; the chain does not
  drop it at hop 2.
- CLI `hermes kanban create --model / --provider` persist as `model_override` /
  `provider_override` on the task row; the dispatcher passes them to the spawned
  worker (`hermes_cli/kanban.py:411-420`, row dump `:79-80`).
- Workers are fresh `hermes -p <assignee>` processes (kanban_db.py:10173 comment) →
  config + .env load at spawn → NO gateway restart needed for future cards to pick
  up a new key; the live chat session is structurally untouched (its route is the
  profile default).
- A provider/alias entry's `api_key` accepts `"${VAR}"` references resolved against
  the worker's env (`hermes_cli/model_switch.py:558`) — same env-reference pattern
  the fleet already uses, so the new key stays out of tracked files.
- Failure mode to guard: a handoff that omits `provider` silently falls back to the
  profile default key — a billing LEAK, not an error. The only guard is the
  handoff's own `kanban_show` check (the row renders `model: <tier>
  (provider: <name>)` — `hermes_cli/kanban.py:1862-1864`).

## Option B outcome (dedicated pipeline key) — EXECUTED 2026-09-04, shape REVISED

Plan + defect log: `{{HERMES_HOME}}/profiles/{{OPERATOR}}/future-work/litellm-{{REVIEW_KEY}}-key-plan-2026-09-03.md` (stamped Cutover DONE + DEFECT section). The provider-pin shape above was REJECTED — the owner: the root-config provider block is "the wrong layer"; a DEDICATED PROFILE supersedes it.

**Final shape:** one dedicated key identity — `{{REVIEW_KEY}}` — held by a key profile whose `.env` defines `LITELLM_API_KEY` with the dedicated key (key_alias `{{REVIEW_KEY}}`, same proxy); the profile config stays fleet-shape (`model.default` ONLY — NO `model.api_key` / `provider` / `base_url` override). The current fleet idiom: the seven role profiles (Role profiles generalization below) all share this ONE dedicated key; `--assignee <role-profile>` is all the kickoff needs; no provider pinning, no handoff threading.

**Bug + false-positive (2026-09-04, first live run):** first attempt wired the key as profile `model.api_key: ${LITELLM_PIPELINE_KEY}` + a .env var of that same NEW name. Two facts killed it: (1) a profile's `model.api_key` does NOT override the shared `providers.custom.api_key: ${LITELLM_API_KEY}` — that shared block is the credential that actually pays; (2) an UNRESOLVED `${VAR}` passes VERBATIM to the proxy — a 401 whose Authorization header is the literal `${LITELLM_PIPELINE_KEY}` (a literal `${…}` in the auth header = missing/renamed env var, NOT a bad key). The Phase-0 "KEYOK" gate was a FALSE POSITIVE: `hermes -p <key-profile> chat` from the curator shell authenticated on the INHERITED chat `LITELLM_API_KEY`, masking the defect; the live kanban worker did the same (its `/proc/<pid>/environ` carried only the chat key).

**Identity-proof recipe (the gate that should have been run):**
1. `env -u LITELLM_API_KEY hermes -p <profile> chat -q 'Reply with exactly one word: KEYOK'` — inherited key REMOVED; the profile .env must supply the credential.
2. Negative control (proves IDENTITY, not just success): set the INHERITED `LITELLM_API_KEY` to GARBAGE — the call must still auth (the profile .env loads override=True, so the inherited value cannot be the payer).
3. On a LIVE worker (diagnostic only — see 3a; NOT the identity witness): `tr '\0' '\n' < /proc/<pid>/environ` and sha256-compare its `LITELLM_API_KEY` to the dedicated key (never print values).
3a. **`/proc/<pid>/environ` is a RED HERRING for key identity** (2026-09-04):
 it is the SPAWN-TIME env snapshot; the profile `.env` loads with
 `override=True` at process start, AFTER that snapshot — so an inherited
 chat key visible there is NOT proof it is the payer (a live worker's
 `/proc` showed the inherited chat key, yet the poison test below proved
 the profile `.env` key actually authenticated). Trust the poison test
 (step 2) + a successful call as the identity witnesses; do NOT conclude
 payer identity from the `/proc` snapshot.

**Attribution framing:** `{{MID_MODEL}}*` routes to the LOCAL free vLLM — every key shows `total_spend: 0.0000` despite live `last_active`. The dedicated key's value is per-key ATTRIBUTION in Grafana (`api_key_alias`), not cost isolation — "billing" means "attribution" here.

## Role profiles generalization (built 2026-09-04, same key, one per role)

The Option-B shape generalized from 1 to 7 role profiles without any config change (the seven role profiles of the ASSIGNEE map) — each `hermes profile create --no-alias --no-skills` + the SAME dedicated key copied into its own `.env` as `LITELLM_API_KEY` (value-by-value, never printed) + fleet-shape config (`model.default` only) + per-role long-term-memory bank + symlinked `hermes-skills-repo` skill (STE100 also gets `asd-ste100`). Three verified facts that make this work unattended:

- **No per-profile gateway is needed for kanban workers.** The dispatcher is global: a `ready` card with `--assignee <role profile>` spawns `hermes -p <role>` on demand (a GATE card proved spawn + own-bank write for the author role). Headless role profiles need only config + .env + skills.
- **The identity proof is per-profile, not per-fleet.** The poison test (inherited `LITELLM_API_KEY` set to garbage, profile `.env` key must still win) was run for the author role on 2026-09-04 and passed; all seven share the identical wiring. `/proc/<pid>/environ` on the live worker still shows the inherited chat key — that is the spawn-time snapshot (RED HERRING, see above), not the payer.
- **The per-role split is expressed in ONE `{{ASSIGNEE}}` token** (the owner's standing call 2026-09-04: never seven per-role tokens): the token's VALUE is a `role=profile` map in a role-isolated install, or one profile name in a single-runner install; the assembler resolves each `{{ASSIGNEE}}` site by the role of the card that site creates. Doctrine changed at `{{REPO_DIR}}/skill-kanban` (PROFILE.example role-map form), doctrine stays host-neutral (generic `<role-profile>` placeholders in the worked example — a first pass leaked the real `sr-*` names and failed the leak grep).
