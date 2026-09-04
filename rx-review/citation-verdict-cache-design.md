# Citation-verdict cache — design (for starters)

**Status:** built (2026-08-13). Slice 1 — store (`rxverdict.py`), keying + hit-rate metrics
(`cmd_build`), population (`cmd_merge`). Slice 2 — the reuse gate: embeddings retrieve (litellm
`mxbai-embed-large`) → strength-aware confirm (`qwen-3.6-27b-noreasoning`), writing reused verdicts
to `CONTEXT-audit-cache.md` so `cmd_fanout` (which skips `already_judged`) drops them.

**Reuse is ON by default (2026-08-13)** — `RX_VERDICT_REUSE=0` disables it. The confirm gate is
deliberately conservative (re-judges on any doubt), so early reuse rate is low until tuned; the
dashboard's reuse-rate panel is how that decision is now made against real data. Every endpoint call
is fail-safe: any error → no reuse → the citation is judged normally, and a bad cached verdict is
authoritative forever (the confirm reuses only on a clean pass). Tunables: `RX_EMBED_MODEL`,
`RX_CONFIRM_MODEL`, `RX_VERDICT_EMBED_THRESHOLD` (default 0.80), `RX_LITELLM_BASE`.

## The idea

Stage 7's citation audit is the most expensive part of a run: last run **768 citations → ~92
`Context audit` cards** (30-min workers) plus the sweep and its retries. But the pipeline reviews
the **same regimen** run after run, so most of those judgments are re-derived from scratch every
time. Cache the **verdict** — "does this source support this claim?" — so a citation judged once is
not re-judged next run.

Crucially, the fetch is **already** cached (the shared web-access page cache). The uncached,
expensive thing is the **LLM judgment**, and that is what this cache targets.

## What the audit does today (baseline)

`verify.py`, Stage 7:

1. **Extract** (`endnotes()` → `cmd_build`): each endnote `[n] source, "quote" URL` becomes a row
   `{report, n, url, quote, claim, match, heading, section}`. `claim` = what the report body did
   with the source; `section` = the enclosing text located around the quote in the fetched source.
2. **Mechanical locate** (`find_best_quote`): the quoted sentence is located in the source →
   `exact | fuzzy | absent | unfetched`. Cheap, deterministic, no LLM.
3. **LLM judge** (`Context audit NN/MM` cards): given `claim + located-section + quote`, judge
   whether the source supports the report's use → a verdict line into `CONTEXT-audit-*.md`.
4. **Sweep + merge**: `cmd_sweep` re-judges stragglers (loop-until-dry, 4-round limit); `cmd_merge`
   → `CONTEXT-AUDIT.md`.
5. **Consume** (Stage 8 reconcile): `unsupported`/`misquoted`/`absent` claims are dropped/demoted →
   `VETTED.md`; the assembler may use only survivors.

Verdicts are keyed within-run on **`(report, endnote-number)`** — an *ephemeral* key used only for
retry idempotency. Nothing is reused across runs.

## Design

### Key: the source anchor, not the claim

Key the cache on **`hash(located-section + quote)`** — content-addressed on the *source* side:

- Same page (page-cache hit) → same located section → same key → hit.
- Page changed under the URL → different section → miss → re-judge. **Self-invalidating.**
- Do **not** put the claim in the key. Reports are LLM-generated, so the same underlying claim is
  **reworded** every run ("X lowers LDL" → "X reduces LDL cholesterol"); claim-in-key turns those
  into misses and pays full judgment anyway.
- Prefix the key with a **format/logic version** (like `rxcache`), so a change to
  `enclosing_section`/`find_quote` or the judging rubric invalidates cleanly instead of reusing
  verdicts made under old logic.

### Entry: a list of judged claims per anchor

The same `(section, quote)` gets cited for **different claims** over time, so each anchor stores a
list:

```json
{
  "key": "cvc1:<sha1(section+quote)[:16]>",
  "url": "https://…",              // provenance only, not part of the key
  "quote": "…",
  "section_sha": "…",              // full hash, for audit
  "claims": [
    { "claim": "omega-3 lowers triglycerides",
      "verdict": "supported",
      "reason": "…",
      "run": "2026-08-13", "audit_version": "cvc1" }
  ]
}
```

### The reuse gate: two stages, embeddings then LLM

On a lookup hit, decide whether the **new** report claim may inherit a cached verdict. Claim
equivalence for THIS purpose is **asymmetric**: reuse only when the new claim asserts **no more**
than the cached one (same direction, same-or-weaker magnitude / hedging / scope). A *weaker* new
claim may inherit a `supported`; a *stronger/more specific* one may not, even though it "entails"
the cached claim.

1. **Embedding retrieve + reject (free, no inference).** Embed the new claim; rank the anchor's
   cached claims by cosine; drop obvious non-matches. Embeddings are trustworthy at saying *"not
   the same"* (low cosine → re-judge), and good for candidate ranking — reuse the existing egpu
   embeddings endpoint.
2. **Strength-aware LLM confirm (two short sentences).** For the top candidate(s), a small LLM call
   answers: *"Does the new claim assert anything the cached claim did not — a number, a stronger
   verb, a broader population, less hedging, opposite direction? If yes → re-judge."* This reads
   ~100–200 tokens (two claims), not the ~1,250-token section — the whole point. Batch many of
   these into one card. **Bias to re-judge on any doubt.**

**Why embeddings can't do the confirm alone:** cosine is blind to exactly the verdict-flipping
differences — negation (*"lowers TG"* vs *"does not lower TG"* score nearly identical), magnitude
(*"lowers TG"* vs *"lowers TG by 40%"*), hedging, scope. High cosine ≠ same assertion. Embeddings
retrieve and reject; they never make the reuse decision.

### Flow

```
locate quote (mechanical)                → section  (absent/unfetched: mechanical fail, no cache)
key = version : sha1(section + quote)
lookup:
  miss  → full LLM judgment → write verdict → create/populate anchor entry
  hit   → embed new claim, rank cached claims
            no viable candidate → full judgment → append (claim, verdict) to anchor
            candidate           → strength-aware LLM confirm
                                     equivalent  → REUSE cached verdict (no section read)
                                     not equiv   → full judgment → append to anchor
```

The full-judgment path is unchanged from today; the cache only *skips* it on a confirmed hit.

## Trust discipline (from `rxcache`)

A bad cached verdict is **authoritative forever** — strictly worse than re-deriving, which gets
another chance to be caught. So:

- **Bias to re-judge** on any doubt in the confirm gate; a false miss costs one judgment, a false
  hit launders a defect.
- **Version-invalidate** on any change to section extraction, quote location, or the judging
  rubric.
- **Self-invalidate** on source change (the section is in the key).
- Store **evidence** (reason, run, version) per entry so an entry can be re-audited later.

## Where it plugs in

- `cmd_build` already produces the `{section, quote, claim}` rows — the natural place to compute
  the key and split rows into **cache-hit** (resolved, verdict attached) vs **needs-judging**
  before the `Context audit` fan-out is sized. Hits never become cards.
- The **sweep** and `already_judged()` stay as-is for the citations that still need judging.
- `cmd_merge` folds cached verdicts and fresh verdicts into `CONTEXT-AUDIT.md` identically —
  downstream (reconcile/VETTED) is unaware a verdict came from cache.

## Location & persistence

Both rx-review caches live under one root:

```
~/.hermes/cache/rx-review/
    transcriptions/     (rxcache — moved here from ~/.hermes/rx-review-cache/)
    citation-verdicts/  (this cache; env RX_VERDICT_CACHE)
```

- Kept across runs by **default**; dropped only by an explicit `--clear-verdict-cache` (mirror the
  web-cache rule — a plain "clear the board" never touches it, and `--clear-cache` clears only the
  transcription sibling). `reset` reports it like the web cache ("KEPT: N cached verdict(s)").
- `~/.hermes/cache/documents/` is deliberately **not** under this root: it is Hermes's Discord
  platform document cache (all uploads, any conversation), not an rx-review cache.

## Concurrency — never a shared mutable file (the `manifest.json` lesson)

The audit cards run in parallel and two of them can judge the **same** anchor in one run, so the
store is written by **exactly one** thread, never by the concurrent workers:

- **Store = content-addressed, one file per key** — `citation-verdicts/<ver>-<sha1(section+quote)>.json`,
  written **atomically** (temp file + `rename`, like `rxcache`). Different citations → different files.
- **Producers write per-*writer* files.** The parallel `Context audit NN/MM` cards keep writing
  their own `CONTEXT-audit-NN.md` (one writer per file — the existing pattern). They never touch
  the store.
- **A single-threaded step populates the store.** `cmd_merge` (already serial, one card) reads the
  per-card files and writes/appends the per-key cache files. One writer, no races even for a shared
  key. `cmd_build`'s lookup is serial too (runs on the Stage 7 Begin card), so reads are safe.

This is the exact shape that replaced the torn `manifest.json` (per-writer `.xcribe/<token>.json`
files + a serial merge).

## Open questions (for the next pass)

- **Magnitude granularity:** how strict is "asserts more"? A cheap numeric/hedge extractor before
  the LLM confirm may catch the common overreach cases deterministically.
- **Store format & size:** per-key files vs one json; expected cardinality across many runs.
- **Cache-poisoning audit:** a periodic job that re-judges a random sample of cached verdicts to
  measure drift, since a stale hit is silent.
- **False-hit rate:** the sample-audit job above is the only way to measure it — a stale hit is
  silent, so nothing in the live path can catch a bad reuse.

## Metrics (built 2026-08-13)

`verify.py._emit_verdict(kind, evs)` pushes fire-and-forget events to Loki (`job="rx-verdict"`,
mirroring the fetch/search emitters; honours `RX_METRICS=0`), grouped one stream per `outcome`:

- `kind="probe"` (per located citation, from `cmd_build`): `outcome ∈ {hit, miss}` — the anchor
  hit/miss rate, the reuse ceiling.
- `kind="reuse"` (per cached candidate, from `_resolve_from_cache`): `outcome ∈ {reused, rejudged}`
  — how often the strength-aware confirm actually reuses vs re-judges (surfaces an over-conservative
  confirm as a low reuse rate).
- `kind="verdict"` (per merged verdict, from `cmd_merge`): `outcome =` the verdict itself — the
  verdict distribution.

Every event also carries `card` (HERMES_KANBAN_TASK) and `report` (the reviewer) for per-card /
per-reviewer breakdowns. Charted in the Grafana dashboard **RX-Review Web Fetch, Search & Verdict
Cache** (`uid rx-fetch-cache`, provisioned from `observability/grafana/dashboards/rx-fetch.json`):
a "Citation verdict cache" section with hit/miss/reuse-rate stats, probe- and verdict-over-time
timeseries, per-reviewer probe and verdict tables, and a `$card`-filtered debug log.
