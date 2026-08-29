# Outstanding conventions work

Snapshot: **80 findings — 0 critical, 26 major, 54 minor** across 16 skills
(post entry-point Phase 1, 2026-08-28). CI is green; it gates on criticals only —
`layout/dirs`, `scripts/confirm`, `frontmatter/requires-toolsets`,
`routing/triggers-baseline` (and the seven pre-existing criticals) block a merge,
the remaining major/minor are conformance work and non-blocking. The three script
contract rules now scope to **derived entry points** (SKILL.md code references),
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

## 2. Script contract — 13 findings

`scripts/top-level-guard` (9), `scripts/json-contract` (4). All 9 files in two skills:
`pallo-logistics` (gina-pending, gina-where, pallo-modify-stay, pallo-trip-plan/prep/status)
and `square-appointments` (customer-info, list-merchants, square-list).

**28 findings removed, not fixed, by the 2026-08-28 entry-point Phase 1:** the contract
rules now apply to derived entry points (scripts SKILL.md references in code), so the
premise-false class — `triplib.py` (imported, never invoked), bambu's probes/logins,
web-access's service files — no longer carries a contract the agent can never trigger.
Those files are still hygiene-checked (silent-except, destructive subcommands).
`scripts/exit-code` hit 0 findings at Phase 1 and is promotion-safe the moment the set
is final; `json-contract` + `top-level-guard` become promotion-safe after the Phase 2
fixes below (reserved call, per the gate policy).

The fix is the same everywhere and already written: vendor `tools/skill_json.py` into the
skill's `scripts/` and use `ok()` / `fail()` / `@guard`.

Why it matters: without `@guard`, an uncaught exception prints a traceback to stderr and
**nothing to stdout**, so the model cannot tell whether the action happened — and defaults to
assuming it did. That is how a booking script reports success it never achieved.

- [ ] `pallo-logistics` — 6 files (Phase 2)
- [ ] `square-appointments` — 3 files (Phase 2)

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

## 5. Structure — 15 findings

- [ ] `body/tools-table` (5) — the four investment skills and `pet-care-tracker` have no tools
      table because they invoke no scripts. Either exempt script-less skills in the linter or
      give `pet-care-tracker` the `scripts/pet_care.py` it should have had (it currently hands
      the model raw `curl`, `jq` and a bearer token, which defeats the domain-abstraction rule
      wholesale).
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
