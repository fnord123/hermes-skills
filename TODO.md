# Outstanding conventions work

Snapshot: **46 findings — 0 critical, 1 major, 45 minor** across 16 skills
(body/invocation promotions, 2026-08-31; domain-leak + explicit-verb + tags
drained, 2026-08-31). The 64th/65th were `pallo-logistics/scripts/gingr_lib.py:76`
(81f7bf3) and the promotion batch. CI is green; it gates on criticals only —
`layout/dirs`, `scripts/confirm`, `frontmatter/requires-toolsets`,
`routing/triggers-baseline`, the three script contract rules
(`scripts/json-contract`, `scripts/top-level-guard`, `scripts/exit-code`),
the four body/invocation rules promoted 2026-08-31
(`scripts/invocation`, `readability`, `body/error-sentence`,
`body/section-flow`), and the seven pre-existing criticals block a merge;
the remaining major/minor are conformance work and non-blocking. The three script
contract rules scope to **derived entry points** (SKILL.md code references),
which dropped 28 premise-false findings (probes, service files, imported helpers)
and added 14 `scripts/undocumented-shebang` warnings — see §2.

Regenerate this picture at any time:

```
python3 tools/lint_skills.py                    # all findings
python3 tools/lint_skills.py --severity major
python3 tools/lint_skills.py --skill calendar
python3 tools/lint_skills.py --json             # machine-readable
```

Ordered by what actually costs something, not by count.

---

## 1. Real bugs

- [ ] **`calendar-today.py` silently ignores every argument.** It has no argparse at all, so
      `calendar-today.py --date 2026-08-01` returns *today* as `{"ok": true}`. A small model
      asking for another date gets a confidently wrong answer with no error. Either accept
      `--date` or reject unknown arguments.

- [ ] **`bambu-store`'s remaining browser probes are model-visible.** `auth_probe.py`,
      `store_login.py`, `bambu_login.py`, `token_mint_probe.py`, `cart_capture.py`,
      `store_sso_probe.py`, `har_analyze.py` (the seven shebang'd probes — see
      `scripts/undocumented-shebang`, §2) sit under `scripts/`, which Hermes
      announces to the model. None writes to the cart or spends, so this is not
      urgent — but the payment scripts were deleted for exactly this reason, and the
      same argument applies. `har_analyze.py` additionally prints raw
      request/response bodies, redacting only a fixed key list.

- [ ] **`pallo-stays.py` still drops unparsed reservation cards silently.** Same shape as the
      fail-open overlap guard that was fixed in `pallo-book-trip.py`, but read-only, so it
      misreports rather than double-books.

---

## 2. Script contract — CLOSED (Phase 2, 2026-08-29)

All 9 files converted to the house JSON contract: `tools/skill_json.py` vendored
into `pallo-logistics` + `square-appointments` (generated copies, `tools/vendor.py`
governs), `@guard` on `main`, every output path through `ok()` / `fail()`.
`top-level-guard` 9 → 0, `json-contract` 4 → 0 (repo 80 → 67, the other 54
findings byte-identical). `ok()` / `fail()` are now `NoReturn`-annotated in the
source (and all five vendored copies), so fail-then-continue type-checks.
Live-verified: happy paths (`gina-pending.py`, `list-merchants.py`) emit
`{"ok": true, ...}` exit 0; bad args now emit `{"ok": false, ...}` exit 1 where
they previously exited 2 with usage text on stderr and nothing on stdout.
Behavioral note: informational outcomes the agent reports to the user
(`ambiguous_trip`, `no_trip_found`, `pallo-trip-status` sweep results) are
`ok: true` with the outcome in `status`; real failures (`calendar_error`,
`book_failed`, bad input) are `ok: false` exit 1 — matching pre-conversion
exit-code semantics.

Both contract rules reached **0 findings** at Phase 2 and were **promoted to
critical** with `scripts/exit-code` on 2026-08-29 (Phase 4 — David's reserved
call, executed on 2026-08-29): a documented entry point that loses its `ok`
field, its exception guard, or its non-zero failure exit now blocks a merge.
Teeth pinned by gate test G6 (severity + gate, both directions verified:
pre-flip the violating lab exited 0 with three majors, post-flip it exits 1
with three criticals); the repo run is byte-identical across the flip because
all three rules sat at 0 findings. CONVENTIONS.md's critical definition
carries the contract class now.

**Body/invocation promotions (2026-08-31, David's reserved call):**
`scripts/invocation`, `readability`, `body/error-sentence` and
`body/section-flow` moved major → critical. All four sat at 0 findings at
promotion, so the repo run is byte-identical across the flip; each is now
merge-blocking (a `./script.py` in docs — dead on an HTTP install —, an
unreadable SKILL.md — its rules silently unchecked —, a missing verbatim
ask-the-user sentence, or a missing When to use / When NOT to use section).
Severity + gate pinned by gate test G7 (both skills in one lab: a corrupted
SKILL.md for readability — the early-return means that skill yields only its
own finding — and a degraded-but-readable one carrying the other three).
`body/model-context` was REJECTED for promotion on the same pass: its two
documented false-positive shapes (a `# heading` inside a code fence; a
legitimate `## Background processing` operational heading) were re-verified
live and still fire, and the battery's own KNOWN LIMITATIONS block forbids
promotion without a fence-stripping fix plus negative controls for both
shapes.

### New: undocumented shebangs — 14 findings (minor)

`scripts/undocumented-shebang` (3a, entry-point spec): a shebang says "run me", but
SKILL.md code never references the file. `bambu-store` ×7 (auth_probe, bambu_login,
cart_capture, har_analyze, store_login, store_sso_probe, token_mint_probe) and
`web-access` ×7 (app, browse_task, handlers, mcp_server, run_service, rxfetch, service).
Warning-layer by design — minor, never gates. The fix is to document a file that is
really an entry point or drop the shebang from what is not.

(`daily-briefing` left this list 2026-08-28: its three pipeline `.py` files
were vendored mirror copies of `~/daily-briefing/` (the cron pipeline's own
git repo, single source of truth) — the skill is now a pure config companion
and the mirror is gone, not rewritten.)

### Linter accuracy — fixed

The linter used to grep each entry point's own source for `"ok"`, `exit(1)` and
`except Exception` without following imports, so it penalised the vendoring pattern
CONVENTIONS.md prescribes. It now resolves a local `skill_json` import and exempts a script
that genuinely calls `ok()`/`fail()` and is decorated with `@guard` — an unused import still
gets no pass. `calendar` went 14 findings -> 2, both real.

Leak detection also now scans prose only (fenced code, inline literals and URLs excluded) and
knows this repo's named backends, so `gingr`, `hindsight`, `agentmail`, `home assistant` and
`twelve data` are caught by rule rather than by reading. `cell` and `formula` were dropped as
ordinary English.

---

## 3. Silent excepts — 31 findings

`except Exception: pass` swallowing a real failure. Audited individually; most are legitimate
Playwright selector fallbacks with a second strategy immediately after, or best-effort
screenshots. The ones that hide a real failure:

- [ ] `square-book.py:523`, `square-cancel.py:265`, `square-find-slot.py:105,278,516,533`
- [ ] `bambu_lib.py:116,119` — both OTP-fill attempts swallowed
- [ ] `web-access/scripts/browse_task.py:149-150` — a malformed user `BROWSE_SITE_POLICY` override is ignored in
      silence, so the wrong browser mode is used with zero signal
- [ ] `web-access/scripts/browse_task.py:229-230` — unlocked read-modify-write on a shared JSON file; two
      concurrent runs lose an entry, and a permanently unwritable path re-probes every run
- [ ] `pallo-logistics/scripts/gingr_lib.py:76` (new, 81f7bf3) — `wait_for_url("…/public/login…")`
      raising is the "session is fine" signal; the follow-up URL check + header
      requirement is the real detector, so the swallow is defensible, but the
      comment should say so (audit note, not a suspected bug)

---

## 4. Domain leaks — CLOSED (2026-08-31)

All 8 findings cleared; the model-context vocabulary is now domain words only.
Two of the fixes were linter accuracy (the prose was right, the rule was wrong),
two skills got prose rewords across every occurrence:

- [x] `agentmail-lite` — `agentmail` moved to a `LEAK_ALLOW` entry: the product
      name IS the domain (the skill's own name, the trigger phrases mention
      agentmail.to). `schema` → "request formatting" in prose (2 spots).
- [x] `web-access` — "searxng, firecrawl" → "the service's backends" (1 prose
      spot). The `via` allow-list entry drafted on the same pass was
      retracted before commit: the drain turned out to be a black-box
      change (the `via`/`attempts`/`detail` fields stopped being returned
      altogether), so nothing emits a value the docs must name.
- [x] `pallo-logistics` — "Gingr" → "the kennel" (3 prose spots), "AgentMail"
      → "the agent's email" (3 prose spots); tag `Gingr` → `Kennel` (the
      product name was model-visible; the domain word is "kennel").
- [x] `whatsapp-backfill` — "Hindsight" → "long-term memory" / "the memory
      bank" (4 prose spots); tag `Hindsight` dropped (Memory stays). The
      error-message line keeps the literal `"Hindsight config not found…"` —
      it is the actual script output, and it sits in an inline code span,
      which the prose-only leak scan strips (a command the model copies
      verbatim is not a vocabulary leak).
- [x] `calendar` — "OAuth" → "sign-in" (2 spots); tag `iCal` → `ICal`.
- [x] `pet-care-tracker` — "tracked in Home Assistant" dropped from the intro
      (the tracker is the domain; HA is the backend); "dashboard" → "tracker".
- [x] `square-appointments` — "AgentMail" → dropped (the source is just
      "confirmation emails"), "Playwright" → "a real browser" (2 spots),
      "selector state" → "browser state".
- [x] `daily-briefing` — table header "Schema" → "Shape" (data, not backend).

The `frontmatter/tags` Capitalized check was also refined the same day: a
digit-leading tag (3D Printing) is judged on its first LETTER, not its first
CHARACTER (the old `t[:1].isupper()` rejected "3D Printing" outright). Battery
cases A9a/A9b pin both sides.

---

## 5. Structure — 1 open

- [ ] `body/tools-table` (1) — scope changed 2026-08-29 (`tool_table_exempt`):
      the mandate now fires only for skills that invoke tools — scripts present,
      or tool calls in SKILL.md code. The four investment analysts (no scripts,
      no commands) are exempt by construction — the premise-false class is gone,
      not allow-listed. `pet-care-tracker` KEEPS firing: its `curl`/`jq` recipes
      are tool calls in code, and the real fix is still the missing
      `scripts/pet_care.py` (it currently hands the model raw `curl`, `jq` and a
      bearer token, which defeats the domain-abstraction rule wholesale).
- [x] `frontmatter/requires-toolsets` (2) — closed 2026-08-28: `pallo-logistics` declared
      `requires_toolsets: [terminal]`; `agentmail-lite` was a false positive (the scan matched
      `curl` inside the sentence that *forbids* it) — fixed in the linter with a clause-scoped
      negation filter (`_in_negation`) + battery cases E3/E4.
- [x] `body/model-context` (1) — closed 2026-08-28: `donations` rephrased ("Don't reach for it"
      → "Out of scope").
- [x] `layout/dirs` (1) — closed 2026-08-28: `web-access/patches/` → `web-access/assets/`
      (Dockerfile `COPY` updated).
- [x] `body/error-sentence` (1), `body/section-flow` (1) — closed with A1 (`9f82ad5`):
      both findings were the daily-briefing mirror's; the mirror deletion removed the
      offending SKILL.md (0 findings post-A1, verified in the census chain).
- [x] `body/explicit-verb` (8) — closed 2026-08-31. Two directions: rx-review's
      two genuine Purpose-cell findings reworded to lead with a verb
      ("The FIB-4 score" → "Computes the FIB-4 score", "The ONE verb for a
      reply" → "Routes a regimen-review reply"); the other six (square-
      appointments' STATUS tables, which have no Purpose column) were
      premise-false — the check now scans only tables whose header carries a
      Purpose column (linter fix, battery case B11a pins the negative).
- [x] `frontmatter/tags` (2) — closed 2026-08-31: `bambu-store` `3DPrinting` →
      `3D Printing`, `calendar` `iCal` → `ICal` (the Capitalized-check
      refinement above made digit-leading tags expressible).

---

## 6. Conventions doc

- [ ] The `--confirm` rule reads as "refuse without it". `web-access`'s `do` inverts this: it always
      runs, and `--confirm` *widens* permission for act-verbs. That is reasonable for delegated
      long-running work, but the convention should say so, or the rule looks violated when it
      isn't.
- [ ] `web-access`'s `do --confirm` is advisory — without it the script still runs, and safety is
      a prompt string sent to a remote model. Add a hard pre-check that refuses when the task
      text matches act-verbs and `--confirm` is absent.
- [ ] `author:` appears in several frontmatters and is in neither the Hermes schema nor
      CONVENTIONS.md. Adopt it or drop it.
- [ ] Test files live in `scripts/`, which CONVENTIONS.md scopes to "code the skill INVOKES —
      the runtime, not demos". Move to a repo-level `tests/`.
