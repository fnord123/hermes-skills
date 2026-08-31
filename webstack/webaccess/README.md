# webaccess — deployment (build context)

One service, two facades: HTTP API `:8910`, MCP `:8911`. Built on the Docker
host from this directory:

    cd ~/webstack && docker compose build webaccess && docker compose up -d webaccess

The **service source of truth is the skill in this same repo**
(`../web-access/` — SKILL.md + handler scripts). Before building here, sync
the changed files from the skill, or (preferred, once the repo is the
canonical checkout) build from a worktree that contains both:

    rsync -a --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
        --exclude 'config.env' ../web-access/scripts/ webaccess/scripts/

Files that live HERE and not in the skill:

- `Dockerfile` — the image build. Two re-derivable assets are fetched at
  build time instead of being committed as blobs:
  - **Fara**: pinned `microsoft/fara` commit, cloned in a `--build-arg` step.
    The currently deployed tree was verified **byte-identical (174/174 files,
    sha256) to commit `a675d6d`** (2026-07-22, version 0.2.0 — the repo's
    version string has not moved since 0.2.0 despite the "Fara-1.5" merge
    label).
  - **docker CLI**: official static build
    `https://download.docker.com/linux/static/stable/x86_64/docker-<ver>.tgz`,
    pinned version `27.3.1` (sha256 in this file). The host's `/usr/bin/docker`
    is musl-linked and will not run inside the Debian image.
- `docker-compose.yml` — the service definition, **including the webnet
  environment block** (`SEARXNG_URL=http://searxng:8080`,
  `FIRECRAWL_API_URL=http://api:3002`). When this file is included in the
  top-level `webstack` project, those container-DNS names resolve on the
  shared bridge; the standalone fallback is `service.yaml` in the skill
  (host-name URLs), which compose env overrides.
- `.env` (NOT committed — see `.env.example`) — the only runtime secrets:
  `BROWSE_API_KEY` (LiteLLM), optional BrowserBase creds.

State: one named volume pinned to `webaccess_webaccess-state` (cache,
cookies, learned policy, logs). Never re-key it when moving directories.

## Build-from-zero on a fresh host

1. `git clone` this repo (private; requires access).
2. `cp webaccess/.env.example webaccess/.env`, fill in `BROWSE_API_KEY`.
3. `docker compose build webaccess` — Fara and the docker CLI are fetched
   inside the build (network required; both pinned above).
4. `docker compose up -d webaccess`, then `curl -s localhost:8910/health`.

## Rollback

`../rollback/` holds the previous compose generations (top-level `pre-webnet/`
state and per-service files). Restoring one is a `cp` + `docker compose up -d`.
