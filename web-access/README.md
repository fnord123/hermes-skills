# web-access

Self-hosted web access for agents and pipelines: find pages, read pages, and carry out
multi-step tasks on sites. Three verbs, one JSON object each, through tooling we control end
to end — nothing here depends on a paid third-party API being picked correctly, because the
backends are named in code we own.

The skill exists because Hermes' built-in `web` toolset picks a backend from whatever API keys
happen to be in the environment, ranking a paid provider first. On 2026-07-31 a stale key
silently outranked the self-hosted stack and an entire research stage failed on a service
nobody had used in weeks. Naming the backend in one script removes that class of failure, and
an agent granted only these verbs cannot reach anything else — which is what let the `web`,
`search`, and `browser` toolsets be removed from most profiles entirely.

Three properties hold across everything below:

- **Documents come back verbatim**, with the layer that produced them named in `via`.
  rx-review's citation audit locates exact quotes; a model's paraphrase would break that
  silently. Accordingly, **no render tier uses a chat model.**
- **Escalation is the fetcher's job, not the caller's.** A caller states a URL and gets text
  or an honest failure; it never chooses tiers. Failures are classified (`ok` /
  `unreadable` / `unreachable`) so a caller can never report an unread page as an empty one.
- **Cost orders everything.** The ladder tries the cheapest source that could work and stops
  at the first that yields usable text. The only metered rung is switched off entirely.

Consumers: Hermes agents reach the verbs through the **MCP facade**; the rx-review pipeline
through plain **HTTP** behind its library binding; the weekly research cron jobs through the
**CLI shim** under the cron hook. All three hit the same handlers.

This document runs: the verbs first, then interfaces and deployment, then the architectural
detail (the fetch ladder; throttling and caching; the browser tier), closing with an appendix
of prior decisions.

---

# The verbs

One section per verb: the contract, and the reason it looks the way it does. Mechanical depth
follows in the architecture part.

## `search` — find pages

Asks the **self-hosted SearXNG** on the docker host (`.226`) and returns titles, URLs and
snippets — never page content. Read what you found with `fetch`. `--scope literature` queries
the research databases (PubMed, Semantic Scholar, OpenAlex, Crossref, arXiv) rather than
SearXNG; the open-web scopes (`products`, default `web`) ask one high-quality engine first and
broaden automatically when it returns nothing, with `widened` recording which happened.

```
python3 scripts/web_access.py search --query "..." [--scope literature|products|web] [--max 10]
```

**Why search is self-hosted.** Until 2026-07-27 this ran on Tavily's free tier (1,000
queries/month), and the failure was structural twice over. The quota itself was the first
problem. The second was how Hermes chose backends: `web.backend` pinned *both* search and
extraction to one provider, so an exhausted quota took both capabilities down together. The
replacement is a self-hosted pair on `.226` — SearXNG for queries, Firecrawl for rendering
(see the `firecrawl` rung under *The fetch ladder*) — giving zero recurring cost, no
third-party API keys, and queries that never leave the LAN. Design record:
`~/homelab/docs/hermes-web-search.md`.

## `fetch` — read one page

You give it a URL, it returns **the document**: verbatim text, PDFs handled, plus `via`
naming the layer that produced the text and `attempts` listing everything tried. Verbatim is a
hard requirement, not a nicety — rx-review's citation audit locates exact quotes, which a
model's paraphrase would break silently. The climb up the tier ladder (cache → NCBI → plain
HTTP → rendered → stealth-rendered, cheapest-that-works, escalating only on *detected*
withholding) is specified in *The fetch ladder* and *How `fetch` climbs* below; the escalation
triggers in *Which failures escalate*; the outcome vocabulary in *`unreachable` vs
`unreadable`*.

```
python3 scripts/web_access.py fetch --url "..." [--max-chars 20000]
```

Two properties worth restating here because callers rely on them:

- **No render tier uses a chat model.** Every rung returns the document itself, so an audit
  that distrusts model-touched text can refuse anything claiming otherwise.
- **Escalation is the fetcher's job, not the caller's.** A caller never chooses a tier; it
  states a URL and gets text or an honest failure.

## `do` — carry out a task on a site

A browser session driven by the Fara computer-use model: apply the site's own filters, page
through listings, follow a flow across screens. It returns **an answer**, not a document —
that is the whole difference from `fetch`, which returns the page. It is the only verb that can
change anything on a site, and it is deliberately kept visible-but-separate rather than folded
into `fetch` as an automatic escalation: handing control to an acting agent must be a
deliberate choice, because auto-escalation would mean the model could sign in or submit
something without ever choosing to.

```
python3 scripts/web_access.py do --task "..." [--start-url ...] [--max-steps 25] [--confirm]
```

Contract details: `status: complete` carries the answer; `needs_input` carries a question for
the user; `max_rounds`/`timed_out` carry partial findings. Budgets differ from `fetch`'s
escalation path — a standalone `do` runs up to **30 minutes**, versus the 900s agent rung
inside a `fetch` climb (*Timeout budget* below). Architecture, setup and the Fara scaffold are
in the browser-tier part of the architecture section.

**Acting is gated by `--confirm`, and the gate is honest about what it is:** without it, a
strict read-only instruction is prepended to the task — an instruction to the model, not a
browser-level sandbox. With it, the script may act, and `SKILL.md` ties that flag to the
user having approved that exact action. The browser carries a real identity (pre-seeded
cookies are, where seeded, a logged-in session), so an acting run acts *as the user*. Treat
`--confirm` as a strong norm backed by user approval, never as a technical guarantee.

(The call forms above keep their shape: after the migration the CLI shim
(`web_access.py`) is a thin POST to the service — callers swap transport, not
syntax, and the JSON contract is unchanged. There is no local fallback: if
the service is down the shim fails cleanly, because the backends it would
have reached are not exposed to the LAN.)

---

# Interfaces and deployment

*(decided 2026-08-23)* The verbs are served from a **Docker container on the docker host
(`.226`)** over two facades backed by **one handler core**:

- **MCP shim — the preferred surface, used everywhere else.** Every agent profile reaches the
  verbs as typed tools (`mcp_webaccess_search` / `_fetch` / `_do`). Typed schemas guide the
  model instead of SKILL.md discipline, and profiles can expose the verbs without granting a
  general shell.
- **HTTP API — kept for rx-review and debug.** rx-review is a pipeline of plain Python that
  calls `fetch()` as a function; scripts cannot invoke MCP tools, and bolting an MCP client
  into the pipeline would buy nothing — the client speaks HTTP underneath anyway. And when a
  fetch misbehaves, `curl` beats an MCP inspector, especially inside cron post-mortems.

A thin CLI shim (~30 lines: POST, print JSON) preserves the old command-line shape for the
cron hook during migration.

## Why the code lives in a container

Agents run with file tools on this machine as `dputzolu`. Today the orchestrator source,
`config.env` (LiteLLM/browserbase keys), the page cache and `learned.json` are all readable by
any agent that goes looking — the credential guard filters *terminal command text*, not
`read_file`. Moving them into a container removes that class of exposure structurally rather
than by another text filter. It also deletes bladebro's SSH hop (the service runs beside it on
`.226`) and creates one place where every fetch can be audited.

## One implementation, two transports

MCP is mounted **on top of** the HTTP API's verb handlers — the GitHub-MCP-server pattern. The
product is the three verbs as JSON; `/search`, `/fetch`, `/do` and their MCP tool equivalents
delegate to identical functions. No second implementation exists anywhere; "shim" means thin
facade, not separate codepath.

## What moves and what stays

| Into the container | Stays on this machine |
|---|---|
| `web_access.py` / `rxfetch.py` / `browse_task.py` | rx-review's 88-line binding (`~/.hermes/rx-review/rxfetch.py`), re-pointed at HTTP behind its existing `fetch()` signatures |
| `config.env` → container env / compose secrets | the CLI shim, replacing the old script path |
| page cache, `learned.json`, lock dir → named volumes | cron hook (`cron-terminal-web-access-only.sh`), rewritten to allowlist only the shim |
| Fara scaffold + patched fork, Playwright/Chromium, xvfb | SKILL.md, updated to document both call forms |

Full v1: all three verbs live in the container from day one, Fara/Playwright/xvfb baked into
the image. The Fara patches documented under *Local patches to the Fara scaffold* must be
applied inside the image build (the cookie hook, `FARA_DUMP_MARKDOWN`, landed-URL sidecar) —
they are load-bearing for `fetch`'s agent tier.

## Access model

Two layers, deliberately different in strength:

1. **Network boundary (capability).** The container binds LAN-only. Anything on the LAN can
   reach all three verbs.
2. **Per-profile registration (visibility).** Each Hermes profile lists the MCP server in its
   own `config.yaml`; registering injects the three tools into every conversation in that
   profile; not registering keeps them out of the catalog entirely. Registration controls
   salience, not reachability — an unregistered-but-code-capable profile can still hand-write
   three lines of urllib against the API.

That limitation is **accepted** for `search`/`fetch` (read-only; matches today's posture).
For `do` it is also accepted for now: no token today; the boolean `--confirm` plus per-profile
exclusion carries the weight, exactly as it does in the current CLI world.

## Migration

- **Cron jobs:** the weekly research jobs eventually drop `terminal` entirely — three typed
  MCP tools replace the hook dance. Jobs pin their model at creation; each needs editing as it
  moves.
- **Hook retirement:** `cron-terminal-web-access-only.sh` is removed only after no cron job
  still depends on it. Both paths coexist during transition.
- **Rollback:** the old CLI keeps working until the hook flips; run both until then.
- **JSON contracts unchanged:** same output shapes as today, so callers migrate by swapping
  transport, not parsing.

## Known future options (not built)

- A bearer token on `/do` alone — injected via `${VAR}` in the MCP headers block like TRMNL's
  key — if the no-token posture ever feels thin. No redesign needed; it slots into the existing
  facade.
- An arm/execute protocol for acting tasks (arm returns a short-TTL token; execution requires
  presenting it), which would make `--confirm` service-enforced instead of model-normed.
- An MCP facade variant exposing `search`/`fetch` while excluding `do` per profile, via the
  existing per-server `tools:` filter.

---

# Architecture

## The fetch ladder

`fetch` tries the cheapest source that could work and stops at the first that yields usable
text, reporting which one did in `via`:

| # | Layer | What runs | Model | Cost |
|---|---|---|---|---|
| 1 | `cache` | reads a file we wrote earlier, keyed by URL hash | none | free — no network at all |
| 2 | `ncbi-api` | one HTTPS GET to NCBI E-utilities (`efetch`). **NCBI URLs only** | none | one request, no bot wall |
| 3 | `http` | one `urllib` GET, retried with backoff; HTML stripped to text by regex, PDFs by PyMuPDF | none | one request |
| 4 | `firecrawl` | self-hosted Firecrawl (`.226:3002`) renders the page (its Playwright pool runs the JS) and returns clean, **full** markdown | none | one request to a local service |
| 5 | `browser:bladebro` | the bladebro **stealth** browser, one-shot in a fresh container, reached ONLY when Firecrawl is blocked — its edge is aggressive commercial anti-bot (e.g. Amazon). Returns bladebro's distilled content | none | ~seconds, a fresh container + Chromium |
| 6 | `browserbase` | a **remote managed** Chromium built to defeat bot detection; **OFF by default** (`WEB_ALLOW_BROWSERBASE=1` to enable), driven via `browse_task --mode browserbase` | none | seconds **plus money** — metered third party |

**Render order is cheapest-that-works, and each rung escalates only on a *detected* failure**
(HTTP shell/interstitial → Firecrawl; Firecrawl blocked/empty → bladebro; both free rungs
failed → browserbase). The complementary A/B is the reason for the order: Firecrawl wins
JS/full-text/speed and clears most walls, while bladebro's narrow win is stealth against the
hardest commercial anti-bot — so trying Firecrawl then bladebro covers each one's blind spot
for free before paying.

**Order is by cost, and `browserbase` is always last.** Layers 4, 5 and 6 all use the same
free local browser and differ in how hard they try. Spending the model is cheaper than
spending money, so the agent always precedes the managed browser. Layer 6 is the only rung
that leaves the machine and bills a metered account (free tier: 1 browser-hour/month) —
**currently switched off entirely via `BROWSE_NO_BROWSERBASE=true`.**

The ordering is enforced, not merely described: `rxfetch._browser_attempt` passes
`--no-browserbase` unconditionally, so the render tier can never jump to the paid rung on a
site whose policy names browserbase (costco.com does). Without it, `http` would escalate
straight to the paid remote browser past every free rung.

### How `fetch` climbs

`fetch` walks every layer, cheapest first, and stops at the first that yields usable text —
where "usable" means at least `--min-chars` (200 by default) and not an interstitial. Each rung
it tried, and what that rung returned, comes back in `attempts`, so a failure says where it got
to rather than just that it failed.

`via` names the rung that produced the text. A caller can therefore tell a free local render
from a paid remote one, and an audit that distrusts model-touched text can refuse it.

**Prior experience skips rungs below, never replaces the ladder.** If a site is known to need
headful, the ladder starts at headful and keeps the dearer rungs above it in reserve; retrying
a rung already known to fail for that host is pure latency. What worked is written back to the
learned cache (`BROWSE_LEARNED_POLICY`, default `~/.config/browse-task/learned.json`) so the
next fetch of that site starts there.

**`--all-layers` ignores that knowledge and climbs from the bottom.** The learned cache is only
as good as the code that wrote it: an entry recorded while a rung was broken says "this rung
failed" when the truth was "we had a bug". A real example — `www.bestbuy.com` was learned as
`browserbase` by a probe whose browserbase attempt then died on a plan error. A winner that
never won. **Run with `--all-layers`, or clear the cache, after fixing anything in the browser
path.** The 2026-08-03 cache was cleared for exactly this reason.

Two other switches shape the ladder: `--no-agent` stops it at the plain renders, and
`--no-browserbase` (currently on) removes the paid rung.

| Layer | `fetch` | `do` |
|---|---|---|
| 1 `cache` | yes | no |
| 2 `ncbi-api` | yes | no |
| 3 `http` | yes | no |
| 4/5 headless / headful | yes — climbs both | yes, with the probe ladder |
| 6 agent (fara) | yes — navigates, then returns the landed page's markdown | yes; this *is* `do`, and it returns the answer instead |
| 7 browserbase | yes when enabled, always last | last rung |

**The same agent rung, used two different ways.** `do` lets the agent read the page and returns
its answer. `fetch` uses the agent purely as a *navigator* — dismiss the consent wall, clear
the interstitial, reach the real page — and then takes `get_page_markdown()` from the landed
page in the same session, so cookies and JS state the agent just established still apply. What
comes back is the document. That is what keeps the citation audit working, and it needs the
`FARA_DUMP_MARKDOWN` patch listed under *Local patches*, because fara's own read action
extracts that markdown and then discards it in favour of the model's prose.

`ncbi-api` is conditional, not a step every fetch walks through. `_ncbi_url()` returns a URL
only for a PubMed article or a PMC id on an NCBI host; for anything else the tier does not
exist and `http` is the first request made. NCBI Bookshelf (StatPearls) is deliberately
excluded — `efetch db=books` answers with a 193-byte id list while a plain GET of the page
returns ~94KB of real text, so routing it would swap working content for an empty request.

There used to be a `hermes-cache` tier between `http` and `browser`: it scavenged text that
Hermes' own built-in `web` toolset had left in `~/.hermes/cache/web/`. It was removed on
2026-08-03. The profiles that fed it — `rx-research`, `rx-audit`, `rx-redteam` — no longer
carry the `web` toolset (removing it is the payoff described in the overview), so those 601
files were frozen residue that could only ever answer for pages already read. It was also the
one tier that matched fuzzily, by host plus a guessed identifier, which cost two incidents of
auditing citations against the wrong document. A tier that cannot gain new entries is not worth
the matching risk.

The order is the point: everything above the renders costs one request or nothing, so trying
them first is nearly free. The browser is the only tier that can read a JavaScript-rendered
page and by far the most expensive, so it is reached only after a cheaper tier returned
`unreadable` — the one failure rendering can fix. A page that never responded will not respond
to a browser, and a 404 is an answer, not a bot wall; neither spends a render.

### Which failures escalate

`WITHHELD_STATUS = {401, 403, 429}` returns `unreadable`, so the browser tier fires. These are
the statuses where the server *answered* and withheld the document, which is the definition of
`unreadable` and exactly what a render fixes — Home Depot's Akamai edge returns 403 to a plain
client and serves the page fine in a browser. Before this they were classified `unreachable`
and dead-ended at the precise point rendering would have helped. 429 stays in
`TRANSIENT_STATUS` too, so it is retried with backoff first and only escalates once the retries
are spent; 403 is not retried, because a wall does not soften.

The *browser* rungs apply the same judgement one level up, with `BLOCKED_STATUS = {401, 403,
429, 503}` in `browse_task.py`: a render that comes back with one of these has **not** got the
document, however many characters of refusal it printed. lowes.com answers a blocked product
page with a 403 and a 240-character "Access Denied" body — over the 200-char floor — so a length
test alone accepted a bot wall as success and stopped climbing. `503` is included here (but not
in `WITHHELD_STATUS`) because a render seeing a 503 has already spent the render; there is
nothing cheaper left to retry, only the next rung.

**A read timeout escalates too.** A silent drop is a bot wall in its own right: Best Buy accepts
the connection and never answers a plain client, while the same URL loads in a browser. So a
timeout is a fact about *this* client, not about the host being gone, and a local render is a
process and a few seconds — cheap enough to spend on the chance the site simply refuses
non-browsers. Handled at the transport level rather than in `WITHHELD_STATUS`, since it carries
no status code. Connection-refused and DNS failures stay `unreachable`: there is no server there
for a browser to reach either.

Worth knowing what this does *not* fix: Best Buy answers a real headful Chromium with
`net::ERR_HTTP2_PROTOCOL_ERROR` — it resets the connection on TLS/H2 fingerprint before any page
loads. Verified 2026-08-03. No local mode helps; that is what the paid rung is for.

The browser tier runs `scripts/browse_task.py` with `--dump-text`, which returns the rendered
text with no agent in the loop — deliberately, because a citation audit locates exact quotes and
a model's paraphrase would break that silently.

It is still reached as a **subprocess by path**, not an import, even though it sits in the
same directory. Its dependencies (Playwright, the fara-cli venv, xvfb) are not `rxfetch`'s. A
box with no browser installed must fail that one tier rather than fail to import `rxfetch` and
take every cheap tier down with it — rx-review's CI has no browser and imports `rxfetch`
through `verify.py`, so an import-time dependency there stops the pipeline's tests dead.
`web_access_test.py` asserts this.

### The 200-character floor

A response shorter than `--min-chars` (default 200) is treated as an interstitial rather than a
document. Lowering it to 1 to accommodate short pages was tried and immediately reported a
141-character JavaScript shell as `ok: true`. The two errors are not symmetric:

- a short real page called `unreadable` → the browser tier escalates and gets it
- a JavaScript shell called `ok` → the caller writes conclusions from an empty page

The escalation makes the first harmless, so the conservative default is the correct one.

### `unreachable` vs `unreadable`

`unreadable` means the server answered and withheld the document (JavaScript shell, bot wall,
login). `unreachable` means no usable response arrived. A caller that cannot tell them apart
writes "the source does not support this claim" when the truth is "we were throttled" — which is
how one citation audit came to judge claims against the text "Checking your browser before
accessing pubmed". Hence the distinct outcomes and the explicit guidance in SKILL.md never to
report an unread page as an empty one.

### Verified against real failures

Checked against the pages that actually failed a pipeline run on 2026-07-31. Thorne's product
pages return a 141-character shell to a plain read; via the browser tier they return ~8,000
characters including the Supplement Facts panel:

| Product | Panel |
|---|---|
| Magnesium Bisglycinate | 200 mg |
| Super EPA | EPA 425 mg / DHA 270 mg |
| Sacro-B | *Saccharomyces boulardii* 250 mg |
| Advanced Iron Complex | 25 mg |

Two are independently confirmed: the user read Magnesium and Sacro-B off the bottles by hand
when those cards blocked, and the tier agrees.

Amazon needs no browser at all — it returns fully over plain HTTP.

## Throttling and caching

Every tier that makes a **request** runs inside one cross-process host gate — `ncbi-api`,
`http` and the browser rungs, plus `search` in `web_access.py`. The `cache` tier takes no gate
and should not: reading a local file is not traffic, and throttling it would make the cheap
path pay for the expensive one's politeness. Rate limiting begins where the network does.

A politeness interval that one client honours and another ignores is not a rate limit; before
this, the browser driver ran at whatever rate an agent asked for while `rxfetch` carefully
spaced its own requests to the same host. Centralising the gate is the reason the browser tier
lives in `rxfetch.py` rather than in each caller.

### How the gate is built

One file per host under `~/.hermes/.fetchlocks`, holding the timestamp of the last request to
that host. To enter the gate a caller takes an **`flock`** on that file — an advisory lock the
OS provides on an open file (`fcntl.flock`), taken here exclusively, so only one holder at a
time. It is what makes the interval hold across *processes*; a `threading.Lock` would only
serialise one program's own threads, and several rx-review cards run as separate processes
against a rate limit that counts the client, not the process. "Advisory" means it constrains
only those who also ask for the lock — it does not stop an unrelated program from opening the
file — which is fine here, because every route to the network goes through this module.

Once inside, the holder reads the stored timestamp, sleeps out any remainder of that host's
interval, makes the request, and writes the new timestamp on the way out. The timestamp is
written even when the request raises: a 429 consumed our quota just as surely as a 200, and
retrying immediately is what earns the next one.

The lock is held by the process that opened it, so a **child process is a separate holder and
will queue behind its parent**. That matters because the browser tier spawns the driver as a
subprocess while already inside the gate, and the driver takes the same gate when it is run on
its own. Left alone it would wait for a lock its own parent is holding, until the timeout, every
single time. So `rxfetch` passes `RXFETCH_GATE_HELD=<host>` and the child skips the gate for
that host only. Naming the host rather than passing a bare flag means a stale value cannot
silently disable throttling for some other site.

### Cache consistency is a separate problem, solved separately

The cache needs no throttle, but it does need readers never to see a half-written file. That is
handled by writing a temp file and `os.replace`-ing it over the target, which is atomic on
POSIX — not by a lock. A lock would have to be taken by every reader, and readers are the common
case; an atomic rename costs them nothing.

This mattered more than it looks. A plain truncating write leaves a window where the target
holds a partial document, and `looks_unusable` declares anything at or above `SUBSTANTIAL_CHARS`
(20,000) a document without further inspection — so a large page torn mid-write would read back
as complete. In this pipeline that is a citation judged against half a source.

## The browser tier and `do`

Everything from here on concerns the agent-driven browser: `fetch`'s expensive rungs and the
whole of `do`.

### Architecture

Three layers — the model-facing skill never sees the lower two:

1. **Model transport** — an OpenAI-compatible endpoint serving the browser-agent
   model. Here that's a LiteLLM route named `fara` →
   [`Fara1.5-27B`](https://huggingface.co/microsoft/Fara1.5-27B) on the local
   llama-swap host (`192.168.1.222:8080`). Fara1.5 is Microsoft Research's
   browser computer-use agent: it observes the page via screenshots and emits
   grounded actions (click/type/scroll/visit/search).
2. **Scaffold** — Microsoft's [`microsoft/fara`](https://github.com/microsoft/fara)
   Playwright harness (`fara-cli`) runs the screenshot→action loop against that
   endpoint.
3. **This skill** — `scripts/browse_task.py` wraps `fara-cli`: it feeds the task,
   runs it in the per-site browser mode (below) with `/dev/null` stdin (so the agent's
   interactive prompt can't block), reads the trajectory's `data_point.json`
   (`status` + `outcome.answer`), and emits one domain-shaped JSON object. `SKILL.md`
   is the model contract and deliberately speaks only in web-task terms — none of the
   Fara / LiteLLM / screenshot machinery leaks into the model's context.

### Setup

#### 1. LiteLLM route (transport)
Add a route so the scaffold can reach the model through your proxy:

```yaml
  - model_name: fara
    litellm_params:
      model: openai/Fara1.5-27B
      api_base: http://192.168.1.222:8080/v1
      api_key: none
      timeout: 900
```
Restart LiteLLM and confirm `fara` appears in `/v1/models`.

#### 2. Install the Fara scaffold
```bash
git clone https://github.com/microsoft/fara.git ~/fara
cd ~/fara
python3 -m venv .venv
. .venv/bin/activate
pip install -e .          # NOT .[vllm] — the model is served remotely via LiteLLM
playwright install chromium
```
This provides `~/fara/.venv/bin/fara-cli`. Also install **xvfb** for headful mode
(below): `sudo apt-get install -y xvfb`.

#### 3. Configure
```bash
cd ~/hermes-skills/web-access/scripts
cp ../templates/config.env.example config.env
# edit config.env:
#   FARA_HOME=/home/<you>/fara
#   BROWSE_BASE_URL=http://docker.putzolu.com:4000/v1   (your LiteLLM)
#   BROWSE_MODEL=fara
#   BROWSE_API_KEY=<your LiteLLM key>
```

Smoke test:
```bash
python3 scripts/web_access.py do --task "Find the current time in Tokyo and report it"
```

### Safety model

- **Read-only is the default.** Without `--confirm`, the script appends a strict
  instruction telling the agent to only read and report — not to sign in, submit,
  buy, book, post, or change anything. This is an *instruction to the model*, not
  a browser-level sandbox: it relies on the agent obeying, so treat it as a strong
  default, not a hard guarantee.
- **`--confirm` gates acting.** Any state-changing task requires `--confirm`, and
  `SKILL.md` instructs the agent to use it only after the user approved that exact
  action — the standard footgun guard for a local model that might otherwise
  hallucinate a consequential call.
- The agent operates a **live browser**. If that browser profile is signed into
  accounts, an acting run can take real actions as the user. Keep the profile
  logged out of anything you don't want an agent touching.

### Pre-seeding cookies (skip location / login setup)

Sites like Costco geo-default the delivery ZIP (and reject deep-links/search),
so the agent otherwise burns many slow steps clicking the location into place —
and can get it wrong. Instead, pre-seed the cookies deterministically:

- `--cookies <file.json>` (or `BROWSE_COOKIES=<file>` in config) — a JSON list of
  Playwright cookies loaded into the browser **before** the agent's first
  navigation. Cookies are domain-scoped, so a Costco location file is inert on
  other sites. Example (`costco-97219.json`):
  ```json
  [{"name":"client-zip-short","value":"97219","domain":".costco.com","path":"/"},
   {"name":"invCheckPostalCode","value":"97219","domain":".costco.com","path":"/"},
   {"name":"invCheckCity","value":"Portland","domain":".costco.com","path":"/"},
   {"name":"invCheckStateCode","value":"OR","domain":".costco.com","path":"/"}]
  ```

This needs a one-line hook in the scaffold (fara-cli has no cookie flag). In
`~/fara/src/fara/environments/playwright/environment.py`, just before the initial
`await self._page.goto(self.config.start_page, ...)` in `_setup_browser`, add:
```python
import os as _os, json as _json
if _os.environ.get("FARA_INIT_COOKIES"):
    with open(_os.environ["FARA_INIT_COOKIES"]) as _f:
        await self._context.add_cookies(_json.load(_f))
```
The wrapper sets `FARA_INIT_COOKIES` when `--cookies`/`BROWSE_COOKIES` is given.

### Local patches to the Fara scaffold

`~/fara` is a clone of `microsoft/fara`. These edits live in that working tree and are **lost on
any re-clone or upgrade** — re-apply them and re-run the checks below. Under containerization
they move into the image build.

**1. `src/fara/environments/playwright/environment.py` — `_connect_browserbase_once`.** The
session was created with `browser_settings={"advanced_stealth": True, ...}` hardcoded.
`advanced_stealth` is BrowserBase's *Verified mode*, an Enterprise-plan feature, so on any
lesser plan every session failed with `403 Forbidden — "Verified mode is only available on the
Enterprise plan"`, retried five times, and crashed. `BROWSERBASE_ADVANCED_STEALTH=false` in
`~/.hermes/.env` was ignored outright: the config lied. Now `proxies` and `advanced_stealth`
both come from the environment (defaults `true` / `false`) and the chosen values are logged.

**2. `src/fara/fara_7b/browser/browser_bb.py` — `_init_browser_base`.** The same hardcode in a
second, older BrowserBase path. Patched identically. Note the *live* path is (1): the traceback
names `environment.py`. Patching only (2) changes nothing — that mistake cost a debug cycle.

**3. `src/fara/environments/playwright/environment.py` — `_setup_browser` cookie hook.** See
*Pre-seeding cookies* above.

**4. `src/fara/run_fara.py` — `FARA_DUMP_MARKDOWN`.** Before the browser is torn down, if that
env var names a path, write `await env.get_page_markdown()` to it. The capability was already
there and unreachable: `get_page_markdown()` renders the live DOM through MarkItDown (and
extracts PDFs), but `fara-cli` exposes no way to ask for it — the full option list is
`--task --start_page --headful --output_folder --save_screenshots --max_rounds --browserbase
--endpoint_config --api_key --base_url --model`. The agent's own `read_page_answer_question`
calls it, then throws the markdown away and returns the model's answer. This patch is what lets
the agent rung serve `fetch` with the document instead of a paraphrase.

Check both settings are honoured:

```
grep -n "advanced_stealth" ~/fara/src/fara/environments/playwright/environment.py
grep -a "BrowserBase session: proxies=" /tmp/browse-task/browse-task-*.log | tail -1
```

**5. `src/fara/run_fara.py` — landed-URL sidecar.** Alongside the markdown, write `env._page.url`
to `<dump>.url`. The agent reports success for *its own task*, which is not the same as "still
on the URL you asked for": on 2026-08-03 fara met Home Depot's error page, hit Refresh, landed
on the **homepage**, dismissed a popover and correctly declared its task done — and 18,307
characters of site navigation were accepted as the product page. `looks_unusable` cannot catch
that: the dump is long and carries no bot-wall marker. `run_agent_dump` compares host and path
and refuses a mismatch.

### One identity, and why headful matters

Every layer sends the same User-Agent (`RXFETCH_UA`, default a plain `Chrome/124.0` string).
They did not: `search` went out as `Python-urllib/3.x` and the renders as
`HeadlessChrome/134… ` with `navigator.webdriver` set true. One client telling a site three
different stories is both rude and detectable, so the renders now set a real UA on the context
and clear the webdriver flag.

**That fix does not defeat bot walls, and it is worth being clear about it.** Measured against
lowes.com on 2026-08-03: headless is refused with an `Access Denied` page (HTTP 200, 195 chars)
under *every* UA tried — Linux Chrome 124, Windows Edge 122, Windows Chrome 134 — while headful
under xvfb gets the real homepage (4,087 chars) even with no UA set at all. The signal is the
headless browser itself, not the string it sends. Plain `urllib` also gets in, so on that site
the ladder's cheapest and third-cheapest rungs work while the second does not.

### Probing is evidence, and only sometimes

`probe_ladder` returns `(mode, proven)`. Three outcomes, deliberately distinct:

- `OK` — the page loaded in that mode. Recorded in the learned cache.
- `BLOCKED` — the site refused us. Real evidence; climb.
- `ERR` — no xvfb, a launch failure, a timeout. This says something about **us**, not the site,
  and used to be indistinguishable from `BLOCKED`, so our own breakage was written into the
  cache as though it were the site's behaviour.

A fallback is never recorded either. When every rung is blocked the ladder still escalates, but
the mode it lands on is marked `probe-inconclusive` because it was inferred, not tested — which
is exactly how `www.bestbuy.com` came to be remembered as `browserbase` on the strength of a
browserbase attempt that crashed on a plan error before it ever loaded a page.

Probes go through the host gate and are spaced by `BROWSE_PROBE_COOLDOWN` (5s). A refused probe
leaves a site warier of the next one: lowes.com refused headless at 20:15:53 and headful six
seconds later, yet the same headful probe run alone minutes afterwards came back `OK`. The
cheapest rung was manufacturing the block the dearer one then measured.

### Timeout budget

There are **two** agent budgets, because there are two code paths and they are not the same run.

The **`fetch` ladder** — headless, headful, then the agent-as-navigator rung — is bounded by
`RXFETCH_BROWSER_TIMEOUT` (default 1200s) for the whole tier, with `BROWSE_AGENT_TIMEOUT`
(default 900s) bounding the agent rung inside it. The agent's limit is deliberately **smaller**
than the tier's, so a slow agent dies with its own diagnostics rather than being killed blind by
the parent. The floor was 180s when the tier was a single render, and became a bug the moment
the agent joined it: a run was cut off 173 seconds in and reported a bare "browser timed out"
with every per-rung detail lost.

The **`do` verb** is a different, longer-lived path — the agent is the whole point of the run,
not a last resort — and has its own **30-minute** cap (`communicate(timeout=1800)` in `main()`),
the one the *Notes* section refers to. So "agent 900s" and "caps a run at 30 minutes" are both
correct and describe different verbs: 900s is `fetch`'s escalation into the agent, 1800s is a
standalone `do`.

### Browser modes — per-site policy

This section describes how a **single** browser mode is chosen — which is what the `do` verb and
the legacy single-shot path do. `fetch` no longer picks one mode; it *climbs* them (see *How
`fetch` climbs*), using the policy below only as the rung to start on. The mode table, overrides
and probe still apply; they just seed the ladder rather than cap it.

Sites differ in how aggressively they block automated browsers, so the wrapper
picks the lightest mode that actually works, **transparently, by target site**:

| Mode | What it is | When |
|---|---|---|
| `headless` | plain headless Chromium (lightest/fastest) | sites that don't block it |
| `headful` | real browser under a virtual display (`xvfb-run --headful`) | sites that 503/deny headless |
| `browserbase` | a managed cloud browser built to pass bot detection | bot-hardened sites |

`--mode auto` (the default) resolves via a built-in table (verified empirically):

| Site | Mode | Why |
|---|---|---|
| costco.com | `browserbase` | Akamai blocks headless **and** headful past the homepage |
| amazon.* | `headful` | headless is 503-blocked on search; headful loads it |
| reddit.com | `headless` | loads fine headless |
| newegg.com | `headless` | loads fine headless |
| *(anything else)* | `headful` if xvfb present, else `headless` | safe default |

Override precedence: `--mode` / `BROWSE_MODE` → legacy `BROWSE_HEADFUL=true|false`
→ site policy → default. Extend or override the table with a JSON file named in
`BROWSE_SITE_POLICY`:
```json
{"default": "headful",
 "rules": [{"match": "target.com", "mode": "headless"}]}
```
User rules are checked before the built-ins, so you can override any site.

The mode ladder is shared: the browser *tier* of `fetch` reuses it via `--dump-text`, which is
the part worth reusing — this skill already knows which sites need headful or browserbase.

#### Auto-detect for unknown sites

For a site with **no** rule (not in the table, learned cache, or an override), the wrapper runs
a quick pre-flight **probe** of the start URL — headless, then headful, then browserbase when
configured — and remembers the winning mode per-domain in the learned cache
(`BROWSE_LEARNED_POLICY`). Disable with `BROWSE_AUTOPROBE=false`.

**What gets remembered is narrower than "the winning mode", and deliberately so** — see *Probing
is evidence, and only sometimes* above. Only a mode that actually **loaded** the page (`OK`) is
recorded. A `BLOCKED` refusal is real evidence but not a winner; an `ERR` (no xvfb, a launch
failure, a timeout) says nothing about the site at all; and a fallback the ladder lands on
without probing is marked `probe-inconclusive` and never written. The cache must not learn a bug
as if it were the site's behaviour.

Caveat unchanged: the probe checks the *start URL*, so a site whose homepage loads but whose
deeper pages are blocked — Costco, and as of 2026-08-03 Lowe's — still needs an explicit rule.
That is why Costco is in the built-in table.

#### Switching the managed browser off

`--no-browserbase`, or `BROWSE_NO_BROWSERBASE=true` in `config.env`, keeps a run on the free
local modes. Anything that resolves to `browserbase` — a policy rule, a learned rule, even an
explicit `--mode browserbase` — is demoted to `headful` (or `headless` without xvfb), and the
probe ladder ends locally rather than escalating. The reason is logged:

```
browserbase disabled; policy:costco.com wanted browserbase, using headful
browser mode=headful (policy:costco.com+no-browserbase)
```

It is the only rung that leaves the machine and bills a metered account, so it deserves a
switch independent of the free ones — both to exercise the cheaper layers honestly and to stop
an unwatched escalation spending money. **Currently set to `true` in this installation.**

**BrowserBase** (for `browserbase` mode) is a paid service with a small free tier
(1 browser-hour/month). Sign up at [browserbase.com](https://www.browserbase.com),
then set **both** `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID` — in this
skill's `config.env`, the process env, or `~/.hermes/.env` (checked in that
order). Any other `BROWSERBASE_*` settings (e.g. `BROWSERBASE_PROXIES`,
`BROWSERBASE_ADVANCED_STEALTH`) are passed through too. The scaffold's
`--browserbase` path is then used automatically. Without both creds, a
`browserbase`-policy site fails with a clear "not fully configured" message
(naming the missing var) rather than wasting a run.

### Notes

- **Logging.** Every run appends the command (API key redacted), the resolved
  browser mode, and the full agent stdout+stderr to a diagnostic log — by default
  a **per-day file in the OS temp dir** (`<tmp>/browse-task/browse-task-<date>.log`)
  so old days are auto-cleaned by the OS (e.g. `systemd-tmpfiles` ages `/tmp`)
  instead of one file growing forever. Set `BROWSE_LOG` (or `BROWSE_TASK_LOG`) to
  a persistent path if you want to keep logs indefinitely.
- Fara1.5-27B on a P40 is **slow** — each step processes a full screenshot; a task
  can take minutes. `--max-steps` (default 25) bounds cost; the script also caps a
  run at 30 minutes and, on timeout, kills the **whole** browser process group
  (`start_new_session` + `killpg`) so no `Xvfb`/`chromium` is left orphaned.
- Trajectories (per-round screenshots + `data_point.json`) are written to a temp folder and
  discarded after the result is read — **unless** a trace is enabled. `trace_rounds()` reads the
  per-round actions, the model's own reasoning, and the screenshot paths into the trace before
  the folder is deleted, and `--keep-trajectory <dir>` copies the whole folder out. This is what
  makes "what did the agent see, round by round" answerable after a run rather than lost.
- `data_point.json` fields consumed: `status` (`complete` / `waiting_for_user` /
  `max_rounds` / `timed_out` / `aborted`) and `outcome.answer`.

## Requirements

`scripts/requirements.txt` (PyMuPDF, for PDF extraction). The browser tier and the `do` verb
additionally need the Fara scaffold above; without it, that one tier reports unavailable and
`search` plus the cheaper `fetch` tiers still work.

---

# Appendix: prior decisions

## Why this absorbed browse-task (2026-08-03)

`search`/`fetch` and the multi-step browser agent shipped as two skills. They were never
siblings: this skill's most expensive fetch tier already shelled out to the other one. That left
two problems the merge fixes structurally rather than by documentation.

**Both skills claimed the JavaScript-page case.** browse-task's description invited the model to
use it directly for "a single page the ordinary fetch could not read"; this skill said to use
`fetch --browser`. Those are the same case, and the browse-task route skipped this module's
cache, its cheaper tiers, and its per-host gate. One skill with one entry point cannot be
mis-routed.

**`--browser` was a seam the model had to reason about.** Escalation is a property of the
fetcher, not a decision for the caller: it fires only on `unreadable`, which means the server
answered and withheld the document — the one failure a render can fix. It is automatic now.
`--no-browser` remains for a caller that would rather fail than spend the seconds.

The verb `do` stays visible and separate on purpose. `fetch` is deterministic plumbing; `do`
hands control to an LLM computer-use agent with a step budget, a timeout, and the ability to
act. Auto-escalating into it would mean the model could sign in or submit something without ever
choosing to, and the `--confirm` gate only means something if invoking the actor is deliberate.

## Records kept elsewhere

- **SearXNG/Firecrawl migration** (why search and render are self-hosted):
  `~/homelab/docs/hermes-web-search.md`
- **CloakBrowser experiment** — stealth-browser A/B; decision: not adopted, an unknown
  black-box binary was not worth the marginal gain:
  `~/homelab/docs/web-access-cloakbrowser-experiment.md`
- **`hermes-cache` tier removal** (2026-08-03): described under *How `fetch` climbs*.
