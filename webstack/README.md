# webstack — the web-access egress stack (docker host .226)

Single compose project that replicates the whole stack:

```
cd ~/webstack && docker compose up -d          # everything
cd ~/webstack && docker compose ps             # verify: webaccess healthy
curl -s http://127.0.0.1:8910/health           # service gate
```

## Members

| Directory | What it is |
|---|---|
| `webaccess/` | The fetch service (HTTP :8910 + MCP :8911) — the consumer. **Build context; see its README.** Service code's source of truth is the skill in this repo: `web-access/` at the root. |
| `searxng/` | Search tier. webnet-only, no published ports. Includes the engine-tuning `settings.yml` + `api-engines.yml` and the apply script. |
| `firecrawl/` | Render tier. The file here is the **webstack-pinned compose** (image-based, not build-based; pinned container names and volume names; webnet join; no published ports). It is NOT a firecrawl checkout — it's our overlay on the vendor stack. |
| `bladebro/` | NOT a compose member — image source for webaccess's one-shot tier: `docker build -t bladebro-mcp:local bladebro/` |
| `rollback/` | Previous compose generations. `pre-webnet/` = the full stack as it stood before the shared-network migration (2026-08-24). |

## Invariants (why the files look the way they do)

- **webnet** — one shared user-defined bridge. Container DNS gives
  `searxng:8080` / `api:3002` to webaccess; the tiers keep no published
  ports. The only LAN-visible ports are webaccess :8910/:8911 (plus the
  documented loopback binds for the host-local name).
- **Pinned state** — every named volume carries its EXTERNAL name
  (`webaccess_webaccess-state`, `firecrawl_fdb-data`, `firecrawl_fdb-cluster-file`)
  so joining/moving projects never re-keys state.
- **Launch ordering** — enforced by `restart: unless-stopped` everywhere,
  firecrawl's internal depends_on edges, and webaccess's healthcheck (its
  tiers are fail-safe and degrade until their ports answer).
- **Out-of-project by design** — litellm :4000 and loki :3100 are shared
  with other stacks; webaccess degrades gracefully without either.

## Rebuild webaccess (the only member that builds an image)

```
webstack/webaccess/sync-from-skill.sh          # refresh service code from ../web-access
docker compose build webaccess                 # Fara + docker CLI fetched at pinned pins
docker compose up -d webaccess
```

No blobs to manage: Fara is a pinned `microsoft/fara` commit verified
against the 174-file sha256 manifest, and the docker CLI is the pinned
official static build (see `webaccess/Dockerfile`).

## Secrets

All runtime secrets live in `.env` files (gitignored, `chmod 0600` on the
host); tracked config carries **no** secret bytes — not even redacted stubs.

- `webaccess/.env` — LiteLLM/BrowserBase keys for the agent rung.
- top-level `.env` — firecrawl vendor env **plus `SEARXNG_SECRET`** (the
  SearXNG session-signing secret; the member compose passes it into the
  container, where the image's settings loader lets it override the file —
  which is why `searxng/config/settings.yml` has no `secret_key` line).
- `searxng/.env` — engine API keys (currently `SEARXNG_BRAVE_API_KEY`).
  Read host-side by `apply-api-engines.sh`, which expands `${VAR}` refs in
  `api-engines.yml` before merging into the live settings; an empty/missing
  var skips that engine instead of installing a broken one.

`.env.example` files show the variable names only. `deploy.sh` hard-fails if
`SEARXNG_SECRET` or `SEARXNG_BRAVE_API_KEY` is absent/empty at deploy time —
a deploy must never silently run searxng unsigned or keyless.
