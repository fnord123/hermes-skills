# Outstanding conventions work

Snapshot: **113 findings — 0 critical, 73 major, 40 minor** across 16 skills.
CI is green; it gates on criticals only, so everything below is non-blocking.

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
      `store_sso_test.py`, `camou_test.py`, `har_analyze.py` sit under `scripts/`, which Hermes
      announces to the model, and account for all 23 of that skill's findings. None writes to
      the cart or spends, so this is not urgent — but the payment scripts were deleted for
      exactly this reason, and the same argument applies. `har_analyze.py` additionally prints
      raw request/response bodies, redacting only a fixed key list.

- [ ] **`pallo-stays.py` still drops unparsed reservation cards silently.** Same shape as the
      fail-open overlap guard that was fixed in `pallo-book-trip.py`, but read-only, so it
      misreports rather than double-books.

---

## 2. Script contract — 66 findings

`scripts/exit-code` (35), `scripts/json-contract` (17), `scripts/top-level-guard` (14).

The fix is the same everywhere and already written: vendor `tools/skill_json.py` into the
skill's `scripts/` and use `ok()` / `fail()` / `@guard`. `calendar`, `bambu-store` and
`donations` have adopted it; `pallo-logistics` (34 findings) and `square-appointments` (24)
have not, which is most of the remaining count.

Why it matters: without `@guard`, an uncaught exception prints a traceback to stderr and
**nothing to stdout**, so the model cannot tell whether the action happened — and defaults to
assuming it did. That is how a booking script reports success it never achieved.

- [ ] `pallo-logistics` — 16 scripts
- [ ] `square-appointments` — 6 scripts
- [ ] `daily-briefing` — 3 scripts
- [ ] `web-access`, `whatsapp-backfill`, `google-docs` — 1-2 each

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

---

## 4. Domain leaks — 9 findings

Backend vocabulary in model context. The model reasons about the words in front of it.

- [ ] `pallo-logistics` — "Gingr", "storage-state", "auth cookies", "portal rate limiting";
      `Gingr` is also a tag. Say "the kennel's booking system".
- [ ] `square-appointments` — "AgentMail", "bearer tokens", "internal selector state";
      `Square` is a tag.
- [ ] `whatsapp-backfill` — "Hindsight", "bank", "retain", "operation ids" throughout, and
      `Hindsight` is a tag. Say "the agent's memory"; rename `--bank` to `--collection`.
- [ ] `google-docs` — "service account", "Drive", `GOOGLE_APPLICATION_CREDENTIALS` in the
      error section.
- [ ] `donations` — the only leak in an otherwise clean surface: raw `googleapiclient`
      exceptions pass through as `error`, rendering the spreadsheet id and A1 ranges.
- [ ] Four reported leaks are false positives — "formula" meaning *show your math*, "cells"
      meaning table cells, "webhook" inside a literal URL. Consider narrowing the rule.

---

## 5. Structure — 10 findings

- [ ] `body/tools-table` (5) — the four investment skills and `pet-care-tracker` have no tools
      table because they invoke no scripts. Either exempt script-less skills in the linter or
      give `pet-care-tracker` the `scripts/pet_care.py` it should have had (it currently hands
      the model raw `curl`, `jq` and a bearer token, which defeats the domain-abstraction rule
      wholesale).
- [ ] `frontmatter/requires-toolsets` (2) — `calendar`, `donations`.
- [ ] `body/error-sentence` (1), `body/section-flow` (1), `body/model-context` (1).
- [ ] `body/explicit-verb` (6), `frontmatter/tags` (2), `frontmatter/version` (1) —
      `daily-briefing` is still `1.0.0`.

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
