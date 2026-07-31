# web-access

Search and page-reading, through tooling we control. Two verbs, one JSON object each:

```
python3 scripts/web_access.py search --query "..."
python3 scripts/web_access.py fetch  --url "..." [--browser]
```

## Why this exists

Hermes' built-in `web` toolset picks a backend from whatever API keys happen to be in the
environment, ranking a paid provider first. On 2026-07-31 a stale key silently outranked the
self-hosted stack and an entire research stage failed on a service nobody had used in weeks.
Naming the backend in one script we own removes that class of failure, and an agent granted only
these scripts cannot reach anything else — which is what let the `web`, `search`, and `browser`
toolsets be removed from those profiles entirely.

## Tiers

`fetch` tries the cheapest source that could work and stops at the first that yields usable
text, reporting which one did in `via`:

| `via` | Source | Cost |
|---|---|---|
| `cache` | text we already extracted for this URL | free |
| `ncbi-api` | NCBI's own API, for NCBI URLs | one request, no bot wall |
| `http` | the page itself, retried with backoff | one request |
| `hermes-cache` | text some other worker extracted | free |
| `browser` | a real browser renders the page | seconds, a whole process |

The order is the point: everything above `browser` costs one request or nothing, so trying them
first is nearly free. The browser is the only tier that can read a JavaScript-rendered page and
by far the most expensive, so it is opt-in (`--browser`) and is reached only after a cheaper
tier returned `unreadable` — the one failure rendering can fix. A page that never responded
will not respond to a browser, and a 404 is an answer, not a bot wall; neither spends a render.

The browser tier shells out to the **browse-task** skill, which owns the browser and remembers
which sites need which mode. It is invoked with `--dump-text`, which returns the rendered text
with no agent in the loop — deliberately, because a citation audit locates exact quotes and a
model's paraphrase would break that silently.

## Throttling

Every tier, including the browser, runs inside one cross-process host gate. A politeness
interval that one client honours and another ignores is not a rate limit; before this,
browse-task drove a browser at whatever rate an agent asked for while `rxfetch` carefully spaced
its own requests to the same host. Centralising the gate here is the reason the browser tier
lives in `rxfetch.py` rather than in each caller.

## The 200-character floor

A response shorter than `--min-chars` (default 200) is treated as an interstitial rather than a
document. Lowering it to 1 to accommodate short pages was tried and immediately reported a
141-character JavaScript shell as `ok: true`. The two errors are not symmetric:

- a short real page called `unreadable` → the caller retries with `--browser` and gets it
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

## Requirements

`scripts/requirements.txt` (PyMuPDF, for PDF extraction). The `--browser` tier additionally
needs the browse-task skill installed; without it, that one tier reports unavailable and the
cheaper tiers still work.
