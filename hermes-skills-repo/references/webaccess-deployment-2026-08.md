# webaccess service: deployment + consumer census (measured 2026-08-31)

Session-specific detail for the "Design stance: skill pattern vs MCP server"
section of SKILL.md. The census below is a point-in-time snapshot of 20 profile
configs; re-census with the keys at the bottom before trusting any of it.

## Architecture (verified against source 2026-08-31)

- **One handler core, two facades.** `{{REPO_DIR}}/web-access/scripts/`:
  core = `handlers.py` + `rxfetch.py` (the fetch ladder: cache → NCBI API →
  plain HTTP → self-hosted render → stealth browser → browserbase); HTTP
  facade = `app.py`/`service.py`/`run_service.py` on the docker host at
  `http://{{SERVICE_HOST}}:8910`; MCP facade = `mcp_server.py` at
  `http://{{SERVICE_HOST}}:8911/mcp` exposing `search`, `fetch`, `do`.
- **The repo IS the deployment source**: `service.yaml` (real deploy config,
  in-repo since `f371fcf`), `service.defaults.yaml` (`dab6de4`), and a
  no-live-values config template (`981d833`).
- **`web_access.py` is a shim, not an engine**: POSTs to the HTTP facade, prints
  the same JSON, exits 1 on `ok: false`. Same JSON contract over both facades.
- **Health** (live 2026-08-31): `GET :8910/health` → `searxng live: true`,
  `firecrawl live: true`, `bladebro configured: true`. (Note: `/health` is the
  HUMAN's diagnostic surface — it names the backends by design; it is not part
  of the agent-facing contract and is never routed to a model.)

## Black-box status — LANDED (2026-08-31)

the owner's directive: the container is a black box — internals "MUST NOT get out",
ever; the container may log them internally.

- **Why `via` was right to remove at all**: it's observability, not a decision
  field — SKILL.md had NO "if `via` == firecrawl then …" branch; the model keys
  every action off `outcome` (`ok`/`unreadable`/`unreachable`) and, for `do`,
  `status`. The emitted vendor names (`firecrawl` / `browser:bladebro` /
  `browserbase`) buy the model nothing and re-expose the vendor + stealth host
  the MCP conversion was meant to wall off.
- **The `web-access: {firecrawl}` LEAK_ALLOW entry added during the drain was
  the WRONG fix** — it blessed the name so it could stay. It is now DROPPED.
  Standing rule (the owner, 2026-08-31): no linter allow-list entry without his
  explicit direction.
- **The trap — the fix is per-response, not per-field.** The first pass removed
  only `via`, but the `fetch` response had two more doors open with the SAME
  internals: `attempts` (its `layer` field names every rung: `cache`/`http`/
  `firecrawl`/`bladebro`/`browserbase`) and `detail` ("firecrawl rendered 5000
  chars", "bladebro unreachable: …"). A black-box audit must cover EVERY field
  of the returned JSON and the tool description, not just the obvious one.
- **Final state (both facades share `cmd_fetch` in `handlers.py` — the single
  sanitizing boundary)**: the model gets `ok, url, outcome, chars, truncated,
  text` (+ a `next` what-to-do hint on `unreadable`/`unreachable`) — and nothing
  rung-shaped. `rxfetch` keeps its full internal trail (`Result.via`/`.detail`/
  `.attempts`) for the container's audit log; `cmd_fetch` still reads the raw
  `r.detail` INTERNALLY to pick the 404/DNS/unreachable hint branch (that
  classification stays; only the echo is gone). SKILL.md, the MCP tool
  description ("a self-hosted render, and a stealth browser"), and the shim
  docstring are all de-branded.
- **Verification recipe**: stub `rxfetch.fetch` to return a Result with a
  firecrawl `via` + full attempts trail. Call `cmd_fetch`. Enumerate the
  returned keys and grep every value for the vendor names (all must be False).
  Run `web_access_test.py` — its `Result.via`/`.attempts` asserts test the
  internal ladder, not the response, so they keep passing — and
  `python3 tools/test_lint_skills.py`. Take `python3 tools/lint_skills.py
  --json` before and after: the census must not move, and no web-access
  domain-leak may appear. Do not quote case or census counts — the commands
  measure them.

## Consumer census (measure per install — a dated local count is a false fact
on every other install)

| Mode | How to measure (per-profile config) |
|---|---|
| MCP only (web-access in `skills.disabled`) | `mcp_servers: webaccess:` block present AND `web-access` in `skills.disabled` |
| Skill only (`skills.external_dirs: {{REPO_DIR}}`, no MCP block) | `skills.external_dirs` includes `{{REPO_DIR}}`, no webaccess MCP block |
| Neither | the remainder |

- **MCP-only conversion of a profile:** add `web-access` to
  `skills.disabled`. Verify the blast radius BEFORE the change: no cron job or
  hook references `web_access.py`, and the live MCP server keeps its tools — a
  clean trim, no capability gap. A running gateway keeps its loaded skill
  index until restarted, so restart, then re-census.
- The skill is MCP-first by design (SKILL.md: "MCP tools (preferred)… Use
  these.") — dual-wired profiles call `mcp_webaccess_*` and fall to the shim
  for cron/terminal paths.
- **Third path under the surface**: some configs carry the builtin Hermes web
  toolset `web: search_backend: searxng / extract_backend: firecrawl` — the
  BUILTIN tools pointed at the same backends, independent of both the skill
  and the MCP server. Enumerate via the per-profile builtin `web:` toolset
  config.
- Note: `web-access` is a skill, not a profile — do not expect a
  `{{HERMES_HOME}}/profiles/web-access/` directory in any census.

## Re-census recipe

Per `{{HERMES_HOME}}/profiles/<p>/config.yaml` (and `{{HERMES_HOME}}/config.yaml` = default):
1. `mcp_servers:` → `webaccess:` block present + `enabled: true`? (note the
   `tools.include` list — `do` is the acting verb; its absence is deliberate
   for read-only profiles).
2. `skills.external_dirs:` contains `{{REPO_DIR}}`? → the skill is visible.
3. `skills.disabled:` contains `web-access`? → MCP-only mode.
4. `web:` block → `search_backend`/`extract_backend` → builtin web toolset path.
