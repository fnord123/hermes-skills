# Outstanding conventions work

Snapshot: **63 findings — 0 critical, 9 major, 54 minor** across 16 skills
(post tools-table scope change + Phase 4 contract promotions + Phase 2
script-contract fixes, 2026-08-29; entry-point Phase 1, 2026-08-28). CI is green; it gates on criticals only —
`layout/dirs`, `scripts/confirm`, `frontmatter/requires-toolsets`,
`routing/triggers-baseline`, the three script contract rules
(`scripts/json-contract`, `scripts/top-level-guard`, `scripts/exit-code`),
and the seven pre-existing criticals block a merge;
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

## 3. Silent excepts — 30 findings

`except Exception: pass` swallowing a real failure. Audited individually; most are legitimate
Playwright selector fallbacks with a second strategy immediately after, or best-effort
screenshots. The ones that hide a real failure:

- [ ] `square-book.py:523`, `square-cancel.py:265`, `square-find-slot.py:105,278,516,533`
- [ ] `bambu_lib.py:116,119` — both OTP-fill attempts swallowed
- [ ] `web-access/scripts/browse_task.py:149-150` — a malformed user `BROWSE_SITE_POLICY` override is ignored in
      silence, so the wrong browser mode is used with zero signal
- [ ] `web-access/scripts/browse_task.py:229-230` — unlocked read-modify-write on a shared JSON file; two
      concurrent runs lose an entry, and a permanently unwritable path re-probes every run

---

## 4. Domain leaks — 8 findings

Backend vocabulary in model context. The model reasons about the words in front of it.

- [ ] `pallo-logistics` — "agentmail", "gingr"; `Gingr` is also a tag. Say "the kennel's booking system".
- [ ] `square-appointments` — "agentmail", "playwright", "selector"; `Square` is a tag.
- [ ] `whatsapp-backfill` — "hindsight"; `Hindsight` is a tag. Say "the agent's memory".
- [ ] `agentmail-lite` — "agentmail", "schema" (the product name is the domain here; consider an allow-list entry like `bambu-store`'s `myshopify`).
- [ ] `calendar` — "oauth" (it drives a Google calendar; "sign-in" is the domain word).
- [ ] `pet-care-tracker` — "home assistant".
- [ ] `web-access` — "firecrawl" (a backend; the domain word is "the web").
- [ ] `daily-briefing` — "schema" (its four JSON config files are data, not backend vocabulary).

---

## 5. Structure — 11 findings

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
- [ ] `body/explicit-verb` (8: rx-review 2, square-appointments 6), `frontmatter/tags` (2:
      bambu-store, calendar). (`frontmatter/version` (1) closed 2026-08-28: `daily-briefing`
      was the lone `1.0.0` outlier; now `0.2.0`.)

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
