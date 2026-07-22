# whatsapp-backfill

Import an exported WhatsApp conversation into the Hermes agent's long-term memory
(Hindsight), so the agent can answer questions about it later — "what did Dan say
about the regatta?", "when did we agree to meet?". The user exports a chat from
WhatsApp; this parses it and stores it where the agent already recalls from.

`SKILL.md` is the model-facing contract; this README is the human/developer side
— how it works, the constraints, and setup.

## Why an export (not live capture)

WhatsApp has **no message-history API**. The only supported way to get existing
history is the app's own **Chat → Export chat → Without media**, which produces a
`.txt` transcript. A linked device (the Hermes WhatsApp bridge) only backfills a
small recent window, not full history — so file export is the right source for
backfill. Live, going-forward capture is a separate effort (deferred).

### Ban risk — why this skill avoids the live bridge

Meta bans accounts for automation, and the risk is real but it attaches to the
**live Baileys bridge**, not to this skill:

- **This skill has ~zero ban risk.** It only reads a `.txt` the user exported
  with WhatsApp's own built-in **Export chat** feature. It never connects Baileys
  or any unofficial client to WhatsApp, so there is nothing for Meta to detect.
- **The live bridge is where the risk lives.** Baileys reverse-engineers the
  WhatsApp Web protocol; Meta treats a personal AI assistant the same as a spam
  bot. Bans on the unofficial API are typically **permanent and unappealable**,
  and **unpredictable** — accounts have run for years and then been killed in an
  enforcement wave, while others are banned within a week. Detection is
  protocol-fingerprinting at the connection layer (independent of how little you
  send) plus behavioral ML (low reply-ratio, messaging strangers, robotic
  timing, block/report signals). Read-only linking is **not** a documented safe
  harbor — the connection itself is detectable and still violates the ToS.
- **If the live path is ever enabled:** use a **dedicated/burner number** (never
  the personal one), no unsolicited or bulk outbound, warm up slowly, run on
  Node.js, and treat the account as disposable. Avoid third-party "anti-ban" npm
  packages — at least one (`lotusbail`) was caught exfiltrating WhatsApp session
  credentials; the session directory grants full account access. The only
  zero-ToS-risk automated route is the official WhatsApp Business Cloud API
  (business numbers, templates, opt-in — not personal chats).

Sources: Baileys ban thread (WhiskeySockets/Baileys #1869), engineer risk guides
(zenvanriel, kraya-ai, achiya-automation), and the WhatsApp Help Center on
account bans.

## How it works

```
wa_backfill.py import --file chat.txt
   │  parse the export  (iOS "[date, time] Sender: msg" and Android
   │                     "date, time - Sender: msg" dialects; multi-line
   │                     continuations; skips encryption/join/left notices
   │                     and media placeholders)
   ▼
group into conversation blocks  (default: ≤30 messages, and a >6h gap starts a
   │                             new block — so each block is a coherent chunk
   │                             with participants + timestamps in its text)
   ▼
retain into Hindsight  (async, batched) → the SAME bank the agent uses
   ▼
one JSON object on stdout (blocks submitted, operation ids)
```

Each block is rendered as a self-describing transcript (chat name, date/range,
participants, then `[HH:MM] Sender: text` lines) so Hindsight's fact extractor
has enough context to attribute facts to the right person and time.

### Why blocks, not one-memory-per-message

Retaining each message separately would create thousands of tiny extraction jobs
and strip conversational context. Grouping into blocks gives the extractor
coherent context (better facts) and drastically fewer operations.

### Target bank

The script reads `~/.hermes/hindsight/config.json` and retains into that
`api_url` + `bank_id` — i.e. the exact bank the Hermes memory provider recalls
from — so imported conversations are queryable through the agent with no extra
wiring. Override with `--bank` if you want a separate bank.

## Requirements

- **Hindsight must be the active memory provider** (`hermes memory status` should
  show `Provider: hindsight`, available). Set up with `hermes memory setup`.
- A reachable Hindsight server with a **working background worker** — retains are
  processed asynchronously (the server runs an LLM to extract facts). If the
  worker is down or backed up, `import` will submit successfully but the facts
  won't become recallable until the worker catches up. Check
  `GET {api_url}/health` and the server's operation queue if imports don't show
  up in recall.

## Usage

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
WA=~/.hermes/skills/whatsapp-backfill/examples/wa_backfill.py

# 1) sanity-check the parse (no memory written)
$PY $WA preview --file ~/Downloads/"WhatsApp Chat with Sailing Group.txt"

# 2) import it
$PY $WA import  --file ~/Downloads/"WhatsApp Chat with Sailing Group.txt" --chat "Sailing Group"
```

Then ask the agent (once the server has processed it): *"From my WhatsApp, when's
the regatta and which berth?"*

## Design notes

- **Preview-first.** `preview` parses and reports counts/date-range/sample with no
  writes, so a wrong file is caught before ingesting.
- **Robust parsing.** Handles iOS and Android export dialects, multi-line
  messages, LTR/RTL marks, and skips system notices + media placeholders. Lines
  whose timestamp can't be parsed are still imported (counted in
  `unparsed_timestamps`); their text is kept, only the time metadata is omitted.
- **Async + batched.** Retains are submitted with `async: true` in batches
  (Hindsight extracts facts in the background); a synchronous retain times out at
  the proxy for anything non-trivial.
- **Idempotency.** Re-importing the same export retains it again. Hindsight
  deduplicates *facts/observations*, but to avoid duplicate source documents,
  import a given export once (or use a fresh `--bank` for re-runs).

## Files

| Path | What |
|---|---|
| `SKILL.md` | Model-facing contract. |
| `examples/wa_backfill.py` | The CLI. |
| `~/.hermes/hindsight/config.json` | Hindsight `api_url` + `bank_id` (read, not in repo). |

## License

MIT.
