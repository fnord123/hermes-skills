# browse-task

Delegate a **multi-step web task** to an autonomous browser agent. The Hermes
agent calls one script with a plain-English task; the script drives a real
browser to completion and returns the result as a single JSON object.

## Why this exists

Hermes' built-in web tools are great for a one-shot search or reading a single
page. They're a poor fit when the answer requires *operating* a site over many
steps — applying the site's own filters, paging through listings, following a
multi-screen flow. This skill hands those tasks to a purpose-built **computer-use
agent** that perceives the page and acts on it directly, then reports back.

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
3. **This skill** — `examples/browse_task.py` wraps `fara-cli`: it feeds the task,
   runs it in the per-site browser mode (below) with `/dev/null` stdin (so the agent's interactive prompt can't
   block), reads the trajectory's `data_point.json` (`status` + `outcome.answer`),
   and emits one domain-shaped JSON object. `SKILL.md` is the model contract and
   deliberately speaks only in web-task terms — none of the Fara / LiteLLM /
   screenshot machinery leaks into the model's context.

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

### 3. Configure the skill
```bash
cd ~/.hermes/skills/browse-task/examples
cp config.env.example config.env
# edit config.env:
#   FARA_HOME=/home/<you>/fara
#   BROWSE_BASE_URL=http://192.168.1.226:4000/v1   (your LiteLLM)
#   BROWSE_MODEL=fara
#   BROWSE_API_KEY=<your LiteLLM key>
```

Smoke test:
```bash
python3 examples/browse_task.py --task "Find the current time in Tokyo and report it"
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
then set `BROWSERBASE_API_KEY` and `BROWSERBASE_PROJECT_ID` in config; the
scaffold's `--browserbase` path is used automatically. Without those creds, a
`browserbase`-policy site fails with a clear "not configured" message rather than
wasting a run.

## Notes

- Fara1.5-27B on a P40 is **slow** — each step processes a full screenshot; a task
  can take minutes. `--max-steps` (default 25) bounds cost; the script also caps a
  run at 30 minutes and, on timeout, kills the **whole** browser process group
  (`start_new_session` + `killpg`) so no `Xvfb`/`chromium` is left orphaned.
- Trajectories (screenshots + `data_point.json`) are written to a temp folder and
  discarded after the result is read.
- `data_point.json` fields consumed: `status` (`complete` / `waiting_for_user` /
  `max_rounds` / `timed_out` / `aborted`) and `outcome.answer`.
