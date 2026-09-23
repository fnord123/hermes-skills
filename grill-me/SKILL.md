---
name: grill-me
description: >
  Adversarial plan interview before implementation. Models the user's plan
  as a design tree and interviews them in frontier rounds — every question
  numbered, each carrying a recommendation — until no branch is silently
  assumed, then synthesizes the decisions and waits for an explicit green
  light. PREFER THIS SKILL when the user wants a plan, idea, or approach
  stress-tested BEFORE anything is built or changed; never for reviewing
  code that already exists (that is requesting-code-review's job).
  Activate on any of: "grill me", "interview my plan", "stress test this
  idea", "tear my plan apart", "find the holes in this", "before we build
  this", "challenge my approach".
version: 0.1.0
author: "Rafael Zendron (rafaumeu) + Matt Pocock (mattpocock/skills, grilling) + Hermes Agent"
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Planning, Adversarial, Interview, Decision-Tree, Pre-Implementation, Review, Alignment]
    related_skills: [requesting-code-review, subagent-driven-development, test-driven-development]
---

# Grill Me

Stress-tests a plan through structured adversarial questioning before any
code is written. Models the plan as a **design tree** — every decision
branches into the decisions that hang off it — and interviews the user in
rounds until every branch is resolved and nothing is silently assumed.

Combines the phase discipline of the original with the frontier-rounds
mechanic from mattpocock/skills' `grilling` (see `README.md` for
rationale and provenance).

## When to use

- User says "grill me", "interview my plan", "stress test this idea"
- Before complex work: auth flows, data-model changes, migrations, payments
- A plan has unresolved decisions or seems vague
- Before `subagent-driven-development` decomposition

## When NOT to use

- Existing code or an open pull request → use `requesting-code-review`
- Simple one-off tasks with no open decisions
- The user has already approved a plan and asked for execution — do not
  re-litigate settled ground; run this only *before* that point

## Core mechanic: frontier rounds

Map the plan as a design tree. The **frontier** is every decision whose
prerequisites are already settled — the questions you can ask NOW without
guessing at answers you haven't heard yet.

Work in **rounds**: ask the whole current frontier in one message, numbered,
each question carrying your recommended answer. Then wait. A question whose
answer depends on another question still open in this round belongs to a
LATER round, not this one.

Format each round like so:

```
❓ Q1 — <question title>: <question body, options if relevant>
➡️ Recommendation: <your recommended answer + one-line why>

❓ Q2 — <question title>: <question body>
➡️ Recommendation: <...>
```

Each answer reshapes the tree: settled decisions push the frontier outward
and unblock dependent questions. Recompute the frontier and ask the next
round.

**Facts are your job; decisions are the user's.** When a frontier question
needs a fact from the environment (codebase, filesystem, config, docs), find
it yourself with `search_files` / `read_file` / `terminal` — or dispatch a
subagent via `delegate_task` for a heavy exploration. Never ask the user for
anything you could look up. Don't block on an exploration: only the questions
downstream of it wait; ask the rest of the frontier now.

## Question coverage (work these branches into the tree)

**Understanding** — the real goal and boundaries:
- What is the ACTUAL objective? What is explicitly IN and OUT of scope?
- What are the constraints (time, tech, team, budget)? Who are the users?

**Technical decisions** — for each design choice:
- "Why this approach and not X?" / "What happens if Y fails?"
- "What's the worst case?" / "How would you roll back?"
- Cross-reference the existing codebase; if the project already has a
  pattern for this, call it out.

**Edge cases:**
- "What happens if the user does Z?" / "What if dependency X goes down?"
- "What if volume is 100x expected?" / "What are the security implications?"

## Synthesis (when the frontier is empty)

1. Summarize ALL decisions in bullet points
2. List anything left open, and what is explicitly OUT of scope
3. Ask: "Aligned? Should I start implementing, or adjust anything?"

Do not act on the plan until the user confirms shared understanding.

## Pitfalls

1. **Asking questions out of dependency order.** A question that depends on
   an unanswered question is a guess wearing a question mark. Keep it for a
   later round.
2. **Skipping the codebase.** Find facts in code with Hermes tools instead of
   asking the user.
3. **Accepting "I don't know" as final.** Suggest options, explain
   trade-offs, make a recommendation.
4. **Writing code during the interrogation.** Alignment only — code after the
   explicit green light.
5. **Being too agreeable.** Your job is to find problems. If everything looks
   fine, look harder.
6. **Not adapting to the user's language.** Interview in whatever language
   the user speaks.

## Errors

- The plan is too vague to form a design tree → ask the user for a
  one-paragraph statement of what gets built and for whom, then start the
  tree.
- Two of the user's answers contradict each other → quote both, name the
  contradiction, and ask which stands before advancing the frontier.
- The user declines to continue or waves off remaining questions →
  summarize what is settled, list every unresolved branch explicitly as an
  open risk, and stop.

Always ask the user for guidance when there is an error; do not proactively try to resolve errors yourself.

## Verification

- [ ] Every question in a round had all its prerequisites already settled
- [ ] Provided a recommendation with each question
- [ ] Explored the codebase for facts instead of asking the user
- [ ] Frontier empty (no branch silently assumed) before synthesizing
- [ ] Produced a clear summary of all decisions and open items
- [ ] Confirmed user alignment before stopping
