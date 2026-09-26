# web-access — operator reference

The webaccess service: one handler core on the docker host, three facades
(HTTP POSTs, MCP tools, this CLI shim). **This file is operator documentation,
not model context** — the verbs are self-describing over MCP (tool names,
schemas, and behavioral doctrine arrive at `tools/list`), which is why there
is no SKILL.md here anymore (David's ruling, 2026-09-25: an MCP companion
doc that duplicates the tool descriptions is drift waiting to happen; only
the fleet's own deploy runbooks reference the CLI, and they are humans).

Deploy and probe procedures live in the profile-local skill
`webaccess-service-ops` (operator machines only). This file documents surface
contract and layout for whoever maintains the repo.

## Layout

- `scripts/handlers.py` — the verb cores (search, fetch-content, fetch-bytes, do).
- `scripts/rxfetch.py` — the fetch ladder, both caches (text `sources/`, bytes
  `bytes/`), 3-day fresh window + conditional-GET revalidation, per-host gate.
- `scripts/browse_task.py` — the browser driver; `--dump-text` (rendered text)
  and `--dump-bytes` (network-layer wire capture, local modes only).
- `scripts/app.py` — HTTP facade (`POST /search`, `/fetch-content`,
  `/fetch-bytes`, `/do`; `GET /health`), LAN-only by deployment choice.
- `scripts/mcp_server.py` — MCP facade; `TOOLS` is the registry models see.
- `scripts/web_access.py` — CLI shim (POSTs to the service; no local fallback).
- `scripts/web_access_test.py` — the battery; the contract is pinned there.

## Verbs and the always-array contract

`fetch-content` and `fetch-bytes` answer **always-array**:
`{ok, results: [{url, …}]}` — one element for one URL, N for a batch (`urls`,
max 20; `url` XOR `urls`). `ok` is the AND of elements; a site refusing one
URL is that element's story, never a batch abort. Top-level `ok: false` means
the *request* was malformed (both params, neither, over cap).

`fetch-bytes` defaults to metadata only — `sha256` (over the raw bytes,
pre-decode), `size`, `content_type`, `served_via`, `status`, `age_hours` when
served from cache. `raw=true` adds the payload up to `max_bytes` (over-cap
says so; never truncates a hashable body silently). `served_via`: `direct` =
plain-HTTP client's view; `rendered` = wire bytes a **local** browser was
served (network-layer capture, never the paid remote) — anti-bot sites can
answer clients differently, so the field keeps a hash honest about which view
it hashes. Batches run parallel across hosts, polite-serial within a host.

`search` returns `ok`, `query`, `count`, `results` (`title`, `url`,
`snippet`). `do` returns `ok`, `status`, `answer`; `confirm=true` is only for
a task the user approved explicitly.

## CLI shim

```
python3 ~/hermes-skills/web-access/scripts/web_access.py search        --query "QUERY" [--scope literature|products|web] [--max 10]
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "URL" [--max-chars 20000]           # or --urls u1,u2
python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-bytes   --url "URL" [--raw] [--max-bytes N]       # or --urls u1,u2
python3 ~/hermes-skills/web-access/scripts/web_access.py do            --task "TASK" [--start-url URL] [--max-steps 25] [--confirm]
```

## Outcomes (both fetch verbs)

- `ok` — the document / bytes arrived.
- `unreadable` — the server answered and withheld it (every permitted rung
  tried). Say so and name the URL; never state what an unread page "says".
- `unreachable` — no usable response. That is a fact about our reach, not the
  page's content. Never fill the gap from memory.

`age_hours` on any fetch result = hours since the copy was written or last
confirmed against the origin (~0 after a 304 revalidation). Its absence means
fetched live just now.

Always ask the user for guidance on errors; do not silently work around them.

## Empty results

`search` `count: 0` is a real answer — try broader terms, then report what was
searched and that it found nothing. `do` with an empty `answer`: say the site
did not yield it; an unconfirmed action task describes what it would do —
relay and ask.
