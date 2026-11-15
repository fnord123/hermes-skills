# lint_skills.py rule map (verified against source, 2026-08-27; revised 2026-08-28,
# entry-point derivation ported at Phase 1)

Full path: `{{REPO_DIR}}/tools/lint_skills.py`. Run:
`python3 tools/lint_skills.py --skill <name>` (or `--severity critical`).
Exit code 1 only on **critical** findings — that is the CI ship line.
`--json` output: the top level is a **list** of finding objects
(`{skill, severity, rule, where, message}`) — NOT a dict with a findings
key. `where` is `SKILL.md:NN` or `scripts/foo.py:NN` — strip the `:NN` to
group by file.

**Gate semantics (2026-08-27 hardening):** `--severity` filters what is
*printed*, not what is *gated* — the exit code is computed from the unfiltered
critical count, so a red repo still exits 1 under `--severity major`. A
single unreadable `SKILL.md` (invalid UTF-8) is reported as a `readability` **critical** (promoted 2026-08-31) and the
run continues — it can no longer crash the linter and mask the rest of the repo. Frontmatter parsing tolerates a UTF-8 BOM and CRLF.

**Promotion status (measured 2026-08-28; promotion executed at `a5173d2`; contract
scoping at entry-point Phase 1):**
`scripts/confirm`, `routing/triggers-baseline`, `frontmatter/requires-toolsets`,
and `layout/dirs` are **CRITICAL** as of `a5173d2` — they gate the build, not just
report. `body/model-context` is the one still-held major: it has two known false
positives (a fence-comment heading and a `## Background processing` operational
heading) that would block a *correct* merge at critical, so it stays major until the
rule is fence-aware and the heading words are narrowed (see Known false positives).
**Entry-point Phase 1 (spec approved 2026-08-28: DP1 body-section mechanism, 3a on at
minor, 3b off, Phase 4 after Phase 2):** the three contract rules (`scripts/json-contract`,
`scripts/top-level-guard`, `scripts/exit-code`) now apply only to **derived entry points**
— a `scripts/` file whose name appears in SKILL.md **code** (code span or fence, prose never
counts, last-component matching, one bounded `.sh`→`.py` hop). Ported in the linter as
`derive_entrypoints()` + `_skill_inventory()` (git-tracked + untracked-not-ignored, never a
disk walk; `.venv` excluded). Effect (measured, not projected): 41 → 13 contract findings
(json-contract 14→4, top-level-guard 13→9, exit-code 14→**0**); 28 premise-false findings
removed (bambu probes/logins, web-access service files, `triplib.py`); repo 94 → 80.
**`scripts/exit-code` is promotion-safe** — 0 findings on the derived set. **Phase 2 landed 2026-08-29:** the 9 surviving files (pallo ×6,
square ×3) converted to the vendored pattern — `json-contract` 4 → 0, `top-level-guard`
9 → 0, repo 80 → 67 (the other 54 findings byte-identical). **Phase 4 promotions executed 2026-08-29** (the owner's reserved call,
executed the same day): all three contract rules are now **critical** — a documented
entry point that loses its `ok` field, its exception guard, or its non-zero failure
exit blocks a merge. Teeth pinned by gate test G6 (severity + exit code, verified
both directions: pre-flip the violating lab exited 0 with three majors, post-flip
it exits 1 with three criticals); a demotion back to major breaks G6.

**Body/invocation promotions (2026-08-31; commit `gate: promote
scripts/invocation, readability, body/error-sentence, body/section-flow to
critical`, the owner to execute):** `scripts/invocation`, `readability`,
`body/error-sentence`, `body/section-flow` moved major → **CRITICAL**, all at
0 findings at promotion, so the repo run was byte-identical across the flip
(64 findings: 0 critical, 9 major, 55 minor — the 64th is a new
`scripts/silent-except` at `pallo-logistics/scripts/gingr_lib.py:76` from
`81f7bf3`, not a flip effect). Teeth pinned by gate test **G7** (two lab
skills in one repo: a corrupted SKILL.md for readability — the linter
EARLY-RETURNS after it, so that skill yields only its own finding — and a
degraded-but-readable skill for the other three; asserts critical severity
AND exit 1 under `--severity major`). **`body/model-context` was REJECTED in
the same pass: both documented FP shapes re-probed LIVE and both still fire**
(fence comment `# why this is fast`; `## Background processing`) — a
pre-approved candidate still loses to a live false positive.

**Lint scope boundary:** repo mode lints only directories containing a
`SKILL.md`. Non-skill directories (e.g. `archivist/` = install kit) and their
scripts are never checked; `--skill <such-a-dir>` reports "1 skills, 0
findings" instead of erroring. See Pitfall 8 in SKILL.md.

## Findings by severity

| Severity | Rule | What it checks |
|---|---|---|
| critical | `frontmatter/name` | `name:` != folder name |
| critical | `routing/prefer` | description lacks literal "PREFER" |
| critical | `routing/triggers` | description lacks literal "Activate on any of" (list *completeness* is checked separately by `routing/triggers-baseline`) |
| critical | `frontmatter/yaml` | frontmatter block exists but is not parseable YAML — **one** finding, and the per-field checks (name/tags/version/license/prefer/triggers) + the toolset check are skipped (their input is unreadable); body/layout/script checks still run. A *missing* block is a different failure (the per-field findings fire, not this) |
| critical | `body/never-read-section` | "NEVER read" anywhere (forbidden section) |
| critical | `scripts/syntax` | script fails to parse |
| critical | `scripts/requirements` | third-party import not declared in `scripts/requirements.txt` |
| critical | `readability` | SKILL.md could not be read (invalid UTF-8 / decode error) — that skill's rules were NOT checked; ONE finding is recorded and the run continues instead of crashing. Promoted from major 2026-08-31 (G7 pin) |
| critical | `scripts/invocation` | SKILL.md documents `./scripts/foo.py` — the executable bit is lost over an HTTP install; must be documented as `python3 <path>`. Promoted from major 2026-08-31 (G7 pin; battery D13 fires on `./` / D14 quiet on `python3`) |
| major | `frontmatter/tags` | no `metadata.hermes.tags` at all (source: `add("major", ...)`; re-verified against source 2026-08-31 — the earlier minor note was a misread) |
| critical | `body/error-sentence` | mandated error sentence not present verbatim — promoted from major 2026-08-31 (0 findings at promotion; G7 pin) |
| critical | `body/section-flow` | missing `## When to use` / `## When NOT to use` headers — promoted from major 2026-08-31 (0 findings at promotion; G7 pin) |
| major | `body/tools-table` | no table with a `Purpose` column. **Scope change 2026-08-29** (`tool_table_exempt`): a skill that invokes no tools is EXCUSED — no scripts in the inventory, no derived entry points, and no command lines in SKILL.md code (a known shell command followed by more than just the word, or a script filename). The four investment analysts are the exemption class (web_search/web_extract in prose only). The bare-word exclusion is load-bearing: an inline span that merely NAMES a tool (agentmail-lite's `curl` prohibition) is a mention, not a call. pet-care-tracker KEEPS firing — its `curl -fsS ... | jq ...` recipes are tool calls in code, and its real defect (raw curl + bearer token, no table) stays visible. Battery T1 (exempt) / T2 (command line fires) / T3 (bare mention stays quiet) pin all three directions; B8 (documented script, table deleted) must still fire |
| major | `body/model-context` | rationale headers (why/rationale/background/design/architecture) or failure-mode phrasing ("tend to" / "tends to" — plural since 2026-08-27, it's CONVENTIONS' own example, "models often", "the model will try", "a common mistake", "don't reach for") |
| major | `body/domain-leak` | BACKEND_TERMS hit in **prose** — code blocks, inline code, and URLs are stripped before the scan |
| critical | `layout/dirs` | directory outside the four allowed (`references/ templates/ scripts/ assets/`) — promoted from major at `a5173d2` |
| major | `layout/readme` | no README.md |
| major | `scripts/json-contract` | **substring test, not a contract check** (probed 2026-08-28): source lacks a quoted `"ok"` token. **Scoped to derived entry points since Phase 1** — a file the SKILL.md code never references carries no contract. See Known false positives, "Entry-point-vs-library class" (RESOLVED). One-directional: no token → fires, sound; token present → passes, UNSOUND — a comment-only `"ok"` or `"ok"` as a dict *value* (`{"status": "ok"}`) satisfies it. It does not verify what the agent parses from stdout. Pending the owner's verdict (2026-08-28): six passing pallo/square scripts carry a `"status"` field whose VALUE is `"ok"` while the field the agent parses is not `"ok"` — the value-match direction is unsound in practice, not just in theory; tightening to key-requirement is a rule change needing his sign-off |
| major | `scripts/exit-code` | no literal `exit(1)`/`SystemExit(1)` AND no propagated non-zero exit — `sys.exit(main())`, `sys.exit(<code>)`, `raise SystemExit(<code>)` are accepted since the 2026-08-27 fixes; a script whose ONLY exit is `sys.exit(0)` still fires (it genuinely cannot signal failure). **0 findings as of Phase 1** (derived-set scope removed all 14, every one premise-false) — promotion-safe, promotion reserved to the owner |
| major | `scripts/top-level-guard` | `def main(` without `except Exception` (or missing `@guard` when vendoring skill_json). Scoped to derived entry points since Phase 1 |
| critical | `frontmatter/requires-toolsets` | `python3 *.py`/`curl`/`web_search`/`browser_*` detected but no `metadata.hermes.requires_toolsets`. A mention governed by a same-clause negation cue (do not / don't / dont / never / must not / avoid / forbid / prohibit / no; window 80; no clause break (`. ! ? ,` newline `; | )`) between cue and token) does NOT count as a use — 2026-08-28 clause-scoped filter (`_in_negation`); battery E3 (prohibited mention must not fire) / E4 (real use beside a prohibition must still fire) pin both directions. Promoted from major at `a5173d2` |
| critical | `routing/triggers-baseline` | a trigger present in the committed `tools/trigger_baseline.json` is no longer in the description. **Only removals gate** — adding a trigger never fires. Regenerate the baseline after an *intentional* removal with `python3 tools/lint_skills.py --update-triggers` (commit the diff; a full-repo run also drops deleted skills' stale entries). Skipped when no baseline file exists (hermetic tests) or the frontmatter is unreadable. Promoted from major at `a5173d2` |
| critical | `scripts/confirm` | a script declares an argparse subparser named with a destructive stem (scoped to ALL scripts since Phase 1, not just entry points — a destructive subparser is a footgun in whatever calls it) (`delete`, `remove`, `trash`, `clear`, `reset`, `purge`, `revoke`, hyphen/suffix tolerated) but its `add_parser(...)` block declares no `--confirm`. Scoped to the subparser's block — a `--confirm` on a *different* subparser does not satisfy it. Matches **subparser names**, not bare verbs in source (a word like `clear` in library code is not a destructive user op). Promoted from major at `a5173d2` |
| critical | `body/raw-host-ip` | Raw RFC1918 address (192.168.x, 10.x, 172.16–31.x) anywhere in SKILL.md — added 2026-09-17 (PR #40). Public/loopback/placeholder (`0.0.0.0`, `127.0.0.1`) stay legal. See Hostname & history rules section for the HOST_NAMES map and message contract |
| critical | `body/history` | Historical content in model context — changelog/history headings, `formerly` / `replaced by` / `migrated to` / `renamed from|to` / `used to be|have|live|call|use` / `deprecated` / `previously this|it|the|was|named|used|lived` in prose, or a prose version→version arrow. Required fix in the message: remove entirely; git log is the changelog. See Hostname & history rules for the deliberate exclusions |
| minor | `frontmatter/version` | not `0.x.y` |
| minor | `frontmatter/license` | not MIT |
| minor | `frontmatter/tags` | tag not Capitalized. **Refined 2026-08-31 (drain):** digit-leading tags are judged on the first *alphabetic* character — the old `t[:1].isupper()` rejected valid `3D Printing` (would have forced `3d printing`); A9a (digit-leading + uppercase clean) / A9b (digit-leading + lowercase fires) pin both directions |
| minor | `body/explicit-verb` | Purpose cell starts with The/A/An/This/It/Returns. **Scope change 2026-08-31 (drain):** only tables whose header carries a `Purpose` column — square-appointments' status tables (no Purpose column) were firing 6 premise-false findings on cells like "the user's next trip"; B11a pins the silence (a Status/What-it-means table stays quiet even with article-leading cells) |
| minor | `scripts/silent-except` | bare `except: pass` (scoped to ALL scripts since Phase 1, not just entry points — a silent except in a helper is a real defect even if the agent never runs it) |
| minor | `scripts/undocumented-shebang` | **3a (entry-point spec, approved 2026-08-28):** a shebang'd `scripts/*` file (.py/.sh, git-tracked or untracked-not-ignored, `.venv` excluded) that is not a derived entry point and not library-named (`_is_library` + vendored `skill_json.py`). "Document it (code span or fence) or drop the shebang / rename to a library name." **Weak signal by design — minor, never a gate** (the public linters use the raw shebang heuristic the same way). Day-one surface measured at Phase 1: 14 (bambu-store ×7, web-access ×7). Battery I4 (documented → quiet) / I5 (undocumented → warns AND no contract) / I6 (`_lib.py` rename → quiet) pin all three directions. 3b (an `entrypoints_baseline.json` reclassification check) was decided **off** |

## Hostname & history rules (added 2026-09-17, PR #40, merge `25b7c09`)

the owner's standing rules, landed at three layers the way the author-contract
boundary did (c1d2e8c): CONVENTIONS.md doctrine ("Hosts are named, never
numbered" + the no-history bullet under "SKILL.md is model context"), the
mechanical linter, and the Audit-stage brief in
`{{REPO_DIR}}/skill-foundry/references/audit-role.md`. **General lesson for
future rule changes (operation class (f), wording rules with live-semantics
homonyms): when a phrasing rule can't be completed mechanically because the
same words carry CURRENT meaning in house prose, don't ship the loose matcher
(that's the 39-finding exit-code class) and don't abandon the rule — match the
zero-ambiguity shapes as critical, document the deliberate exclusions in this
rule map with live file evidence, and codify the residual judgment in the
pipeline role's brief.** All three layers land in one commit.

**`body/raw-host-ip`** — `HOST_NAMES` in the linter is the canonical map, the
single place a lease change gets updated (one line per machine: a private
octet → its canonical hostname). The message contract is
pinned by gate test G8 (it reads message text, not just rule names): a MAPPED
IP carries the exact hostname verbatim so the author's fix is copy-paste and
must NOT hedge with 'canonical hostname'; an UNMAPPED private IP says 'use the
host's canonical hostname from the house host docs' and must NOT invent a
plausible-sounding hostname. Pitfall found live: the first draft's octet-sanity guard
was `<= 223`, which silently dropped the map's own highest octets — only the
real mapped-IP battery case (J1) caught it; sanity guards must tolerate the
highest octets in your own fixture data.

**`body/history`** — fires on the zero-ambiguity shapes only. The prose markers
run on text with fences AND inline code spans stripped (a `→` or a 'deprecated'
inside code is a command the model copies verbatim, not narration; J11 pins
the clean side). Deliberately NOT matched, with live in-repo evidence
re-verified before merge: `supersedes` (pallo's 'bump --sequence so the update
supersedes the prior invite' is live iCalendar semantics; pre-ipo's
'public-equity framework supersedes' is live routing) and `no longer X`
(pallo's `session_expired` error meaning, municipal-bond's pass conditions).
First drafts that fired on all of those — the fix was must-not-fire battery
cases J9/J10, not skill edits. The residual judgment (dated 'as of' framing,
old-vs-new narration) is codified in audit-role.md: the Audit asks 'does this
tell the reader something that USED to be true?' and demands deletion — while
past-tense CAUTIONARY examples (verified incidents whose lesson is a live
rule) are explicitly NOT historical content. Battery J1–J11 + G8: 93 → 105
cases, both directions per rule; repo stayed at 0 criticals on merge.

## BACKEND_TERMS (leak list)

`spreadsheet, worksheet, batchupdate, mime type, oauth, service account,
bearer token, endpoint, webhook, selector, dom, playwright, session cookie,
har file, sqlite, schema, primary key, vector, embedding, gingr, hindsight,
`agentmail, camoufox, browserbase, firecrawl, myshopify, home assistant,
litellm, twelve data` — plus per-skill exceptions in `LEAK_ALLOW` (as of the
2026-08-31 drain: `bambu-store: {myshopify}`, `agentmail-lite: {agentmail}` —
the product IS the skill's domain, its own `name:`/triggers fire — and
`web-access: {firecrawl}` — RECONSIDERED 2026-08-31 (second pass): the
`via` value is *observability, not a decision field* — no documented
behavior branches on it (no "if `via` == firecrawl then …"; the model keys
every action off `outcome`/`status`), so the model does NOT need the vendor
name to work, and the entry was the cheap path that let the leak stay. Right
fix = de-brand the two SKILL.md passages (generic layer names, as the MCP
tool description already does) and DROP the entry — pending the owner, not yet
applied. Rule of thumb for new entries, REFINED 2026-08-31: allow-list ONLY
when (a) the term is the product's OWN name/domain (agentmail-lite's
`agentmail`, bambu's `myshopify` — its own `name:`/triggers fire), OR (b) the
model must SELECT ON / distinguish the emitted value to act (a decision
field, an enum in a word→call table). A backend/vendor name emitted purely as
a debug/trace/`via` signal is NOT (b) — de-brand the docs instead. Prose that
merely *describes* the backend is always a leak. Before adding ANY
`LEAK_ALLOW` entry, ask: does documented behavior change based on this
value? If not, the name is decorative and the entry is a leak through the
back door.

## Vendored skill_json.py pattern

If `scripts/skill_json.py` exists and the entry point imports it, the three
script-contract checks are skipped **provided** the source actually calls
`ok(`/`fail(` and main is `@guard`ed. `calendar/`, `gmail-thread-extract/`,
`bambu-store/`, `pallo-logistics/`, `square-appointments/` use this pattern
(pallo + square since Phase 2, 2026-08-29); `google-docs` and `donations`
hand-roll it. `ok()` and `fail()` are `NoReturn`-annotated, so `fail()` in an
except-branch type-checks without an unreachable `return`.

## Known false positives

- `scripts/silent-except` ×2 on docs.py (log write, trashing an image upload)
  are deliberate best-effort paths, documented in the skill README.
- `body/model-context` — **two live false positives discovered 2026-08-28 while
  adding its negative controls (probed against the shipped patterns before
  asserting; both FIRED).** (a) The rule scans **raw body text** — fences are
  stripped only for the *domain-leak* scan — so a comment that looks like a
  heading inside a code fence (`# why this is fast` in a block) fires the
  rationale-heading check. (b) The heading test matches **any of five words**
  (why/rationale/background/design/architecture), so a legitimate *operational*
  heading `## Background processing` fires. Consequence: the rule was on the
  zero-finding "promotion-safe" list, but at critical either FP would block a
  *correct* merge — so it was deliberately **excluded from the `a5173d2`
  promotion and stays major**. **Re-probed LIVE 2026-08-31 during the
  body/invocation promotion pass: both shapes STILL fire** (fence comment
  `# why this is fast`; `## Background processing`) — a second candidate
  promotion was rejected on the fresh probe, not the note. The promotion
  prerequisites stand. Promotion prerequisites (all in the battery as a
  KNOWN LIMITATIONS comment next to B14a/B14b): strip fences before the scan
  (mirror domain-leak's `prose` construction), narrow the heading words, and add
  must-not-fire controls for both shapes. The sound negative controls already
  committed: B14a ("a tendency to" ≠ the failure prime "tends to"), B14b
  ("Background:" in *prose* is not a heading) — battery 93 cases as of the
  2026-08-31 drain (B11a/A9a/A9b added).
- `frontmatter/requires-toolsets` on agentmail-lite (2 findings — `curl` matched
  inside the very sentences that *forbid* it) — **fixed in the linter 2026-08-28
  (`e1b58e5`), not in the skill**: the clause-scoped negation filter (see the rule
  row above) drops same-clause prohibitions. The skill's sanctioned path is
  `mcp_agentmail_*`; it uses no terminal. The first draft's raw 40-char window
  also suppressed 3 genuine uses in adjacent clauses — the clause-boundary check
  is load-bearing, not cosmetic.
- **Entry-point-vs-library class — RESOLVED at Phase 1 (2026-08-28).** The
  premise-false class that once spanned the contract rules (re-censused 2026-08-28
  from live `--json`: 41 findings on 23 files) is now out of scope BY CONSTRUCTION:
  the contract rules apply only to derived entry points (SKILL.md code
  references, one hop, per the Promotion status section above). Measured effect:
  28 premise-false findings removed — 8 bambu-store probes/logins
  (`auth_probe`, `bambu_login`, `camou_probe`, `cart_capture`, `har_analyze`,
  `store_login`, `store_sso_probe`, `token_mint_probe`), web-access service
  files (`app`, `browse_task`, `handlers`, `mcp_server`, `run_service`,
  `rxfetch`, `service` — 10 findings), `triplib.py`; **13 true findings on 9 files survived into Phase 2** (pallo
 `gina-pending`, `gina-where`, `pallo-modify-stay`, `pallo-trip-plan`,
 `pallo-trip-prep`, `pallo-trip-status`; square
 `customer-info`, `list-merchants`, `square-list`) — **fixed 2026-08-29**:
 all 9 now vendor `skill_json.py` with `@guard` + `ok()`/`fail()` on every
 output path (see Phase 2 in the Promotion status section above).
  `scripts/exit-code` hit 0 (all 14 were premise-false) and is promotion-safe.
  The derived set at Phase 1: 29 entry points (29 direct + 0 hops; the hop
  rule's only in-repo consumer was deleted by A1, so it is dormant — battery
  I7/I8/I9 pin its three shapes regardless). The 14 reclassified
  probe/service files now surface as `scripts/undocumented-shebang` minors
  where they carry shebangs — that is 3a working, not a regression. The
  daily-briefing tier was already resolved by A1 (mirror deletion).
  Declaration-mechanism analysis (public prior art, replay, hazards):
  `references/external-linter-landscape-2026-08.md`; the approved spec with the
  four decision points: profile
  `future-work/entry-point-declaration-spec-2026-08-28.md`.

## What the linter does NOT check (verify by hand)

- Whether stdout actually parses as one JSON object carrying an `ok` field —
  `scripts/json-contract` is a static token test (see table above). A smoke
  run would close this, but auto-running scripts is only safe once entry
  points are declared (see the class entry in Known false positives).
- Factual accuracy of output shapes, flags, and behavior vs. script source
- The first-65-char routing window of the description
- Absolute paths (the in-repo authoring standard bans them; house repo relies
  on `${HERMES_SKILL_DIR}` by convention)
- Section order beyond the two mandated headers
