# skill-kanban — the skill-maintenance pipeline (host-neutral doctrine)

A kanban pipeline that creates, audits, writes, verifies, commits, and
fleet-checks skills in a house skill repo, one card per stage, so that no
draft is trusted on self-report and no failure vanishes silently.

This directory is **host-neutral**: it carries the *doctrine* (the board
design, the card work orders, the substrate rules) with the instance values
pulled out as `{{placeholder}}` tokens. It is reusable by any Hermes install
that wants this pipeline. What a given install must supply — the bot that
runs the cards, the repo checkout path, the model tiers, the skill names —
is documented in `PROFILE.example`.

## Layout

- `board.md` — the operating reference: goal of the board, the flow (one
  diagram, every retry loop drawn), one section per card, the loop caps,
  substrate facts, the decisions-as-patterns table, and re-census keys.
  **Read this first.**
- `spec.md` — the card spec: the graph, the R1–R8 binding rules, each card's
  input/work/output/verdict contract, the loop budgets, and the board-level
  mechanics (how a worker hands off, the parentage gate, the 8 KB body cap).
- `cards/` — the seven card bodies as **templates**, one per stage, with
  `{{placeholders}}`. These are the literal bodies a card is created with
  (after token substitution + the run's `__PIPELINE_INPUT__` substitution).
- `known-pitfalls.md` — distilled, id-free list of the failure classes this
  pipeline has actually hit and the rule that closes each.
- `PROFILE.example` — the token reference card: every `{{placeholder}}`,
  what it means, and the value to fill it with for your install.

## How to adopt this pipeline

1. Read `board.md` end to end. It is the single source of truth for *what
   the board is*; `spec.md` is the source of truth for *what each card does*.
2. Fill in a `PROFILE` from `PROFILE.example` for your install. The six
   tokens, and where they appear:

   | Token | Meaning | Appears in |
   |-------|---------|------------|
   | `{{ASSIGNEE}}` | the profile(s) that run the cards (`--assignee`); one profile name, or a `role=profile` map for role-isolated installs | every card's handoff args + kickoff |
   | `{{REPO_DIR}}` | absolute path to your house skill repo checkout (the `--workspace dir:` target) | every card |
   | `{{CARDS_DIR}}` | absolute dir holding these card bodies (for "read the sibling canonical" work-order lines) | Audit, STE100, Verifier, Commit |
   | `{{MID_MODEL}}` | the mid-tier model id for grunt-work cards (Author, Scripter, and their retry cards; the STE100 card itself runs on the operator default) | Author, Scripter, Verifier, STE100 |
   | `{{HOUSE_SKILL}}` | the house-repo skill name force-loaded on most cards | Author, Audit, Scripter, Verifier, Commit, Fleet |
   | `{{STD_SKILL}}` | the writing-standard (ASD-STE100) skill name force-loaded on the STE100 card | Audit, STE100 |

   Judgment cards (Audit, Verifier, Commit, Fleet-Update-Check) carry **no**
   `--model` — they run on the profile's default (your high tier). Only the
   grunt-work cards are pinned to `{{MID_MODEL}}`.

3. Copy `cards/` to a location your install loads from (e.g. a profile's
   `future-work/skill-kanban-cards/`), substitute your `{{...}}` tokens, and
   keep the `__PIPELINE_INPUT__` / `__CHANGESSET_MANIFEST__` tokens in place
   — those are substituted per run by the creating card, not at install time.
4. Create the board: `hermes kanban boards create skills` (the subcommand is
   `boards create`; there is no `boards add`).
5. Kick off a run from chat (see `board.md` §6 for the exact recipe). The
   kickoff command creates card 1 (Author) with `--parent` absent only for
   the very first card; every later card is created by its parent with a
   `parents` list.

## What stays host-local (do NOT commit this into a shared repo)

This directory is deliberately instance-free. A *running* install keeps its
own local state outside this tree and out of the shared repo: the live
`PROFILE` values, the per-run `__PIPELINE_INPUT__` substitutions, the board
SQLite DB (`~/.hermes/kanban/boards/skills/kanban.db`), the run history and
card ids, and any profile-specific census. Those are *your* fleet's record,
not doctrine others reuse.

## Conventions

- ASCII only (the linter and the transport both prefer it).
- No absolute machine paths, no profile names, no model ids, no card ids, no
  census counts as facts — those are the `{{placeholders}}` and the
  "re-census, don't trust" notes.
- The pipeline diagram appears in **two** files (`board.md` and `spec.md`).
  Add or remove a card and update BOTH, checking every edge — especially the
  retry loops — against the spec's branch tables one-for-one. The two
  diagrams drift independently if you only touch one.
