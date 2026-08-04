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
| # | `via` | Source | Cost |
|---|---|---|---|
| 1 | `cache` | text we already extracted for this URL | free, no request |
| 2 | `ncbi-api` | **NCBI URLs only** — NCBI's own API | one request, no bot wall |
| 3 | `http` | the page itself, retried with backoff | one request |
| 4 | `browser` | a **local** browser renders the page, no agent | seconds, a local process |
| 5 | *agent* | the **local** browser driven by the model, working the page | minutes, a local process + GPU |
| 6 | `browserbase` | a **remote managed** browser built to pass bot detection | seconds, plus money — a paid third-party service |

**Order is by cost, and `browserbase` is always last.** Rungs 4 and 5 use the same free local
browser and differ only in who drives it: 4 loads the page and reads it, 5 lets the model click,
scroll, dismiss consent walls and page through results. Spending the model is cheaper than
spending money, so the agent is always tried before the managed browser. `browserbase` is the
only rung that leaves the machine and bills a metered account (free tier: 1 browser-hour/month),
so it is the last resort — **currently switched off entirely via `BROWSE_NO_BROWSERBASE=true`.**

This ordering is enforced, not merely documented: `rxfetch._browser_attempt` passes
`--no-browserbase` unconditionally, so rung 4 can never jump to rung 6 on a site whose policy
says browserbase (costco.com does). Without that, `http` would escalate straight to the paid
remote browser and skip both free local rungs.

Two honest gaps in the table as it stands:

- **Rung 5 is not yet wired into `fetch`.** It is reachable only through the `do` verb. Wiring it
  in needs a decision first: `do` returns the model's *answer*, while `fetch` promises the
  page's verbatim text, and the citation audit depends on that difference. The likely shape is
  to let the agent navigate and then dump text from wherever it lands, so the verbatim guarantee
  survives — not to return its prose.
- **Rungs 4 and 6 both report `via: browser`.** A caller cannot currently tell a free local
  render from a paid remote one, which is exactly the distinction `via` exists to make.

`ncbi-api` is conditional, not a step every fetch walks through. `_ncbi_url()` returns a URL only
for a PubMed article or a PMC id on an NCBI host; for anything else the tier does not exist and
`http` is the first request made. NCBI Bookshelf (StatPearls) is deliberately excluded — `efetch
db=books` answers with a 193-byte id list while a plain GET of the page returns ~94KB of real
text, so routing it would swap working content for an empty request.

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

### Which failures escalate

`WITHHELD_STATUS = {401, 403, 429}` returns `unreadable`, so the browser tier fires. These are
the statuses where the server *answered* and withheld the document, which is the definition of
`unreadable` and exactly what a render fixes — Home Depot's Akamai edge returns 403 to a plain
client and serves the page fine in a browser. Before this they were classified `unreachable`
and dead-ended at the precise point rendering would have helped. 429 stays in
`TRANSIENT_STATUS` too, so it is retried with backoff first and only escalates once the retries
are spent; 403 is not retried, because a wall does not soften.

**A connection timeout still does not escalate**, and that is a real gap rather than a decision
to be proud of. Best Buy drops non-browser clients silently, so `fetch` sees `TimeoutError`,
classifies it `unreachable`, and stops — even though a browser is exactly what would get in.
The argument for leaving it is that a genuinely dead host would then cost a render on every
attempt. Unresolved.

**The browser tier never reaches `browserbase`.** `--dump-text` resolves one mode from the site
policy and runs it; the headless → headful → browserbase probe ladder is wired only into the
agent path (`do`). So `fetch` effectively stops at the `browser` rung: a site whose policy
resolves to the default `headful` but which actually needs the managed browser — Home Depot,
verified 2026-08-03, `mode=headful why=default` returning a 155-character shell — gives up
there. The last rung exists but `fetch` cannot climb to it, so no BrowserBase fix helps until
either the ladder is shared with the dump path or the site gets an explicit policy rule.

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

Every tier that makes a **request** runs inside one cross-process host gate — `ncbi-api`, `http`
and `browser`, plus `search` in `web_access.py`. The `cache` tier takes no gate and should not:
reading a local file is not traffic, and throttling it would make the cheap path pay for the
expensive one's politeness. Rate limiting begins where the network does.

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

## Local patches to the Fara scaffold

`~/fara` is a clone of `microsoft/fara`. These edits live in that working tree and are **lost on
any re-clone or upgrade** — re-apply them and re-run the checks below.

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
*Pre-seeding cookies* below.

Check both settings are honoured:

```
grep -n "advanced_stealth" ~/fara/src/fara/environments/playwright/environment.py
grep -a "BrowserBase session: proxies=" /tmp/browse-task/browse-task-*.log | tail -1
```

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

### Switching the managed browser off

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
