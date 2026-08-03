# web-access

Search, page-reading, and multi-step web tasks, through tooling we control. Three verbs, one
JSON object each:

```
python3 scripts/web_access.py search --query "..."
python3 scripts/web_access.py fetch  --url "..."
python3 scripts/web_access.py do     --task "..." [--confirm]
```

## Why this exists

Hermes' built-in `web` toolset picks a backend from whatever API keys happen to be in the
environment, ranking a paid provider first. On 2026-07-31 a stale key silently outranked the
self-hosted stack and an entire research stage failed on a service nobody had used in weeks.
Naming the backend in one script we own removes that class of failure, and an agent granted only
these scripts cannot reach anything else — which is what let the `web`, `search`, and `browser`
toolsets be removed from those profiles entirely.

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

## Tiers

`fetch` tries the cheapest source that could work and stops at the first that yields usable
text, reporting which one did in `via`:

| `via` | Source | Cost |
|---|---|---|
| `cache` | text we already extracted for this URL | free |
| `ncbi-api` | NCBI's own API, for NCBI URLs | one request, no bot wall |
| `http` | the page itself, retried with backoff | one request |
| `browser` | a real browser renders the page | seconds, a whole process |

There used to be a `hermes-cache` tier between `http` and `browser`: it scavenged text that
Hermes' own built-in `web` toolset had left in `~/.hermes/cache/web/`. It was removed on
2026-08-03. The profiles that fed it — `rx-research`, `rx-audit`, `rx-redteam` — no longer carry
the `web` toolset (removing it is the payoff described above), so those 601 files were frozen
residue that could only ever answer for pages already read. It was also the one tier that
matched fuzzily, by host plus a guessed identifier, which cost two incidents of auditing
citations against the wrong document. A tier that cannot gain new entries is not worth the
matching risk.

The order is the point: everything above `browser` costs one request or nothing, so trying them
first is nearly free. The browser is the only tier that can read a JavaScript-rendered page and
by far the most expensive, so it is reached only after a cheaper tier returned `unreadable` —
the one failure rendering can fix. A page that never responded will not respond to a browser,
and a 404 is an answer, not a bot wall; neither spends a render.

The browser tier runs `scripts/browse_task.py` with `--dump-text`, which returns the rendered
text with no agent in the loop — deliberately, because a citation audit locates exact quotes and
a model's paraphrase would break that silently.

It is still reached as a **subprocess by path**, not an import, even though it now sits in the
same directory. Its dependencies (Playwright, the fara-cli venv, xvfb) are not `rxfetch`'s. A
box with no browser installed must fail that one tier rather than fail to import `rxfetch` and
take every cheap tier down with it — rx-review's CI has no browser and imports `rxfetch`
through `verify.py`, so an import-time dependency there stops the pipeline's tests dead.
`web_access_test.py` asserts this.

## Throttling

Every tier, including the browser, runs inside one cross-process host gate. A politeness
interval that one client honours and another ignores is not a rate limit; before this, the
browser driver ran at whatever rate an agent asked for while `rxfetch` carefully spaced its own
requests to the same host. Centralising the gate is the reason the browser tier lives in
`rxfetch.py` rather than in each caller.

`flock` is per-process, so when `rxfetch` invokes the browser driver as a child while already
holding a host's gate, it passes `RXFETCH_GATE_HELD=<host>`. Without that hand-off the child
would block on its own parent until the timeout, every single time. The host is named rather
than a bare flag so a stale value cannot silently disable throttling for some other site.

## Consumers

`~/.hermes/rx-review/rxfetch.py` is a thin binding that loads this skill's `rxfetch.py` via
`importlib` and re-exports it — there is deliberately only one implementation. It resolves
`RXFETCH_IMPL` or the default path `~/hermes-skills/web-access/scripts/rxfetch.py`, which is why
this skill kept its name through the merge. The pipeline keeps its own `sources_dir` (its cached
corpus is auditable evidence tied to a run) but deliberately shares the lock directory, because
a rate limit counts the client, not the pipeline.

rx-review's card templates invoke `web_access.py search` and `fetch` by absolute path. They pass
no `--browser`, so the merge required no pipeline change.

## The 200-character floor

A response shorter than `--min-chars` (default 200) is treated as an interstitial rather than a
document. Lowering it to 1 to accommodate short pages was tried and immediately reported a
141-character JavaScript shell as `ok: true`. The two errors are not symmetric:

- a short real page called `unreadable` → the browser tier escalates and gets it
- a JavaScript shell called `ok` → the caller writes conclusions from an empty page

The escalation makes the first harmless, so the conservative default is the correct one.

## `unreachable` vs `unreadable`

`unreadable` means the server answered and withheld the document (JavaScript shell, bot wall,
login). `unreachable` means no usable response arrived. A caller that cannot tell them apart
writes "the source does not support this claim" when the truth is "we were throttled" — which is
how one citation audit came to judge claims against the text "Checking your browser before
accessing pubmed". Hence the distinct outcomes and the explicit guidance in SKILL.md never to
report an unread page as an empty one.

## Verification

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

---

# The browser agent (`do`, and the browser tier)

## Architecture

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

## Setup

### 1. LiteLLM route (transport)
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

### 2. Install the Fara scaffold
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

### 3. Configure
```bash
cd ~/.hermes/skills/web-access/scripts
cp ../templates/config.env.example config.env
# edit config.env:
#   FARA_HOME=/home/<you>/fara
#   BROWSE_BASE_URL=http://192.168.1.226:4000/v1   (your LiteLLM)
#   BROWSE_MODEL=fara
#   BROWSE_API_KEY=<your LiteLLM key>
```

Smoke test:
```bash
python3 scripts/web_access.py do --task "Find the current time in Tokyo and report it"
```

## Safety model

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

## Pre-seeding cookies (skip location / login setup)

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

## Browser modes — per-site policy

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

### Auto-detect for unknown sites

For a site with **no** rule (not in the table, learned cache, or an override), the
wrapper runs a quick pre-flight **probe** of the start URL — load it headless; if
that's blocked (403/503/429 or a bot-wall page), try headful; if that's blocked
too, use browserbase (when configured). It then **remembers** the winning mode
per-domain in the learned cache (`BROWSE_LEARNED_POLICY`), so later runs skip
straight to it. This grows the policy automatically. Disable with
`BROWSE_AUTOPROBE=false`. (Caveat: the probe checks the *start URL*; a site whose
homepage loads but whose deeper pages are blocked — like Costco — still needs an
explicit rule, which is why Costco is in the built-in table.)

**BrowserBase** (for `browserbase` mode) is a paid service with a small free tier
(1 browser-hour/month). Sign up at [browserbase.com](https://www.browserbase.com),
then set **both** `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID` — in this
skill's `config.env`, the process env, or `~/.hermes/.env` (checked in that
order). Any other `BROWSERBASE_*` settings (e.g. `BROWSERBASE_PROXIES`,
`BROWSERBASE_ADVANCED_STEALTH`) are passed through too. The scaffold's
`--browserbase` path is then used automatically. Without both creds, a
`browserbase`-policy site fails with a clear "not fully configured" message
(naming the missing var) rather than wasting a run.

## Notes

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
- Trajectories (screenshots + `data_point.json`) are written to a temp folder and
  discarded after the result is read.
- `data_point.json` fields consumed: `status` (`complete` / `waiting_for_user` /
  `max_rounds` / `timed_out` / `aborted`) and `outcome.answer`.

## Requirements

`scripts/requirements.txt` (PyMuPDF, for PDF extraction). The browser tier and the `do` verb
additionally need the Fara scaffold above; without it, that one tier reports unavailable and
`search` plus the cheaper `fetch` tiers still work.
