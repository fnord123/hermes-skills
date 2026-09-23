# grill-me — rationale & provenance

**Why this skill exists.** Plans fail in the gaps nobody thought to ask
about. grill-me makes those gaps a mechanical procedure: represent the
plan as a decision tree, ask only the questions that are answerable *now*
(the frontier), attach a recommendation to every question so the user's
job is reaction not generation, and refuse to proceed until the frontier
is empty and the user says go.

**Provenance.** Merged from two upstreams (both MIT):
- Rafael Zendron (rafaumeu)'s original grill-me — the phase discipline:
  understanding → technical decisions → edge cases → synthesis, and the
  rule that code waits for an explicit green light.
- Matt Pocock (mattpocock/skills, `grilling`) — the frontier-rounds
  mechanic: ask the whole answerable-now set in one message, then let each
  answer reshape the tree.

**Design decisions.**
- *Facts vs. decisions split*: the agent must look up anything verifiable
  in the environment rather than burning a user answer on it. This is the
  main divergence from both upstreams and what makes it usable inside
  Hermes (tool access) rather than as a pure prompt.
- *Recommendation on every question*: an interview that only extracts is
  an interrogation; recommendations keep rounds cheap for the user.
- *Errors end in escalation to the user*: a stalled interview must not be
  "resolved" by the agent guessing what the user would have said.

**House conformance (2026-09-23).** Imported from three identical
profile-local copies (laszlo-bock, jarvis, nagata) at the owner's
standing rule that every profile always has this skill. Rewritten to the
repo contract at 0.1.0: PREFER clause, verbatim trigger list, When-NOT-to-
use section, Errors section with the mandatory closing sentence, tags
capitalized, "schema" de-leaked to "data-model changes". The three local
copies were removed in favor of live inheritance — no twins.
