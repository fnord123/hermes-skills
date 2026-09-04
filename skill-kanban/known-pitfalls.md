# Known pitfalls — the failure classes this pipeline has actually hit

Each entry is a rule that exists because something real broke. No run ids or
commit hashes (this file is host-neutral); the originating fleet's dated
ledger keeps the specifics. When you see the symptom, the rule is the
fix — do not re-derive it.

## The successor's body is the only channel the worker sees

- **The no-op placeholder committed (and was reverted).** A placeholder
  body that *reads like a work order* is a work order to a mid-tier model.
  A throwaway fixture got committed and pushed because a downstream card's
  body described the work in imperative form; it was caught by the owner
  within minutes and reverted (revert, not force-push), leaving the
  incident pair in git history as the record this rule exists because.
- **Rule:** a body that must NOT be executed says so in unambiguous
  negative form ("Do NOT write or edit any script"). A body that DOES
  carry a real handoff is the canonical copied VERBATIM with tokens
  substituted, and the creating card asserts no unsubstituted token
  remains. A handoff that is just a paraphrase is a latent work-order
  bug.

## The linter

- **Dot-dir trap:** the linter's root scan skips dot-prefixed directories.
  `--skill <name>` against a non-skill or dot-dir path returns "0 skills /
  0 findings", which means **nothing was linted**, not "clean". Stage the
  whole skill (SKILL.md + README + scripts/) at the repo root, lint, move
  out (and verify the undo).
- **One-directional `ok` token test:** the `json-contract` rule is a
  substring check. It passes scripts where `"ok"` appears only in
  comments/docstrings or as a different field name, and it misses scripts
  that use `{"status": ...}` instead of `{"ok": ...}`. Marker present is
  not behavioral verification — the Verifier's runs are the truth.
- **`--severity` bypasses the gate:** the exit code is computed from
  filtered findings, so a skill with a critical issue exits 0 if linted
  with `--severity major`. Lint with no severity filter at a gate.
- **One corrupt file kills the whole run:** a single non-UTF-8 file makes
  the linter raise `UnicodeDecodeError` → exit 1, empty stdout, masking
  every other skill.
- **FP classes are real; classify against source, never "fix" the draft.**
  Cite file:line + the source fact for every true/FP call.

## The board substrate

- **Parentage is a creation-time gate, and archive bypasses it.** A child
  with no parent is `ready` immediately and runs against nothing. A parent
  that was `archived` (never `done`) STILL satisfies the gate
  (done-or-archived) — the child promotes ~1s after creation. The child's
  own body must therefore carry the full handoff; it cannot rely on having
  seen a `done` parent.
- **Reclaim re-runs a card.** A manual reclaim intended as a stop signal
  re-queues the task and the dispatcher re-spawns it within seconds. Block
  and archive do NOT signal an already-spawned worker; the in-flight run
  finishes its work order and its later `kanban_complete` is rejected
  ("already terminal"). The durable record for such a run is a final
  `kanban_comment`, not the completion.
- **The DB is the witness, not a `list` snapshot.** Mid-handoff a `list`
  line can show a short-lived claim id whose durable id differs; `show` on
  it says "unknown task". Query the board DB (`tasks` / `task_runs` /
  `task_links`) for the canonical id.
- **`block <id> --kind X "long reason"` mis-parses.** A multi-clause
  reason after `--kind` dumps top-level usage and blocks nothing. Use the
  plain form `block <id> "short reason"` and verify the block took with
  `list`/`show`.
- **8 KB body cap is silent.** The worker context is tail-truncated at
  build time while the DB keeps the full row — so the DB can show an
  over-cap body the worker never saw. Keep bodies under 8 KB; put the
  load-bearing steps at the head.
- **A timeout re-queues the card, and a re-run re-executes the work
  order.** When a run hits `max_runtime_seconds`, the substrate marks it
  `timed_out` and puts the card back in the queue; the next spawn
  re-reads the whole work order from the top. If that run had ALREADY
  created its successor before dying, the re-run will create a duplicate
  unless it checks first. Every successor-creating card therefore opens
  its HANDOFF with a RE-QUEUE GUARD (step 0): query the board for an
  existing successor whose `parents` list contains the card's own id and
  whose title matches its branch; if one exists, create NOTHING, verify
  its payload, and `kanban_complete` citing the existing id.

## Token and assembly discipline

- **A token appearing in more than one place double-substitutes.**
  Replace-all hits both; the second hit mangles live instruction. A token
  that is itself referenced in the successor-instruction text collides
  with its own substitution. Assert on the FULL token after substitution
  (a bare `__` match is wrong — `__pycache__` in prose also contains
  `__`).
- **Kickoff bodies are assembled like worker bodies.** Template +
  substitute + assert no placeholder remains. A worker handed an
  unsubstituted placeholder recovers from the parent summary per R6 (and
  has done so correctly), but the kickoff assembly is the one to fix.

## The census and the self-report

- **A worker WILL edit its own loaded skill mid-run.** A report-only
  fleet-check card once spent part of its run self-editing the very house
  skill it had loaded. The edits were accurate but not owner-directed.
  The card body carries an explicit "do not edit any skill file at all —
  including the skill you load to do this job"; if a worker learns
  something durable it records it in its completion summary + a
  `kanban_comment`, and the OWNER maintains the skills.
- **A census headline and its table must agree.** A fleet check once
  reported "17/20" in the headline while its own table (and an
  independent re-parse) said 18/20 — the headline had dropped the
  home-based profile whose config is at the repo root, not under a
  `profiles/<x>/` dir. When headline and table disagree, the table + an
  independent re-parse win. R6 re-verification is what caught it.

## The sandbox

- **The security scanner blocks `rm -rf` / bulk `rm` for kanban workers**
  and flags multi-line heredocs. Card bodies that touch scratch say "move
  to /tmp, never `rm`" and "one-line commands"; the worker self-corrects
  to `mv`.
- **Git operations that look destructive get flagged.** The Commit card's
  "move scratch to /tmp" and "stage by explicit list" language exists so
  the worker never reaches for a bulk delete or `git add -A`.

## Model tiers

- **A mid-tier model executes a body more literally, not less.** The
  no-op-placeholder incident and the "body is the only channel" rule both
  come from mid-tier grunt cards (Author/Scripter) treating a descriptive
  sentence as an instruction. Judgment calls (the PASS/FAIL calls, the
  push) stay on the high tier; drafting and scripting sit on the mid
  tier. Do not pin a judgment card to mid.
