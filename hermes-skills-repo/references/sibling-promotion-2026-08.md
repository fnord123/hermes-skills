# Sibling promotion 2026-08 — a peer profile `google-docs-raw-api` → shared `google-docs`

Reference record of the first profile-local → house-repo skill promotion.

## What triggered it
the owner asked "Naomi's been editing the google doc skill, check it." The shared
`{{REPO_DIR}}/google-docs` was untouched (local + `origin/main` at the last
commit, tree clean). The real change was a **profile-local** twin in another
profile: `{{HERMES_HOME}}/profiles/a peer profile/skills/productivity/google-docs-raw-api/`
(its SOUL identifies the bot as Naomi Nagata, CMO). It was built from a real
incident: a concurrent Docs-UI edit shifted character indexes and three
positional deletes each clipped one char off a short heading (`Plan`→`lan`,
`Cost:`→`Cot:`, `Phase 2`→`Phae 2`).

## How the profile-local history was read
The profile `skills/` dir is **not a git repo**. State lives in:
- `.curator_ledger.jsonl` — one entry per curator write, sha256 before/after.
- `.curator_backups/blobs/<sha>` — file content keyed by that hash.

The *creation* blob came back empty, so a full diff vs. creation was impossible;
diffing later blobs in ledger order reconstructed the refinements. The ledger is
the audit trail of what the bot changed and when — read it, not just the file.

## Fact-checking before promotion (other-agent self-report ≠ verified)
Every claim in the twin's SKILL.md was checked against the real shared
`docs.py`:
- It imports `d._creds()`, `d._docs()`, `d._get_doc()`, `d._batch()` — confirmed
  all four exist with those names.
- Its `batchUpdate` request-shape table (no `deleteText`, `containsText.text`,
  `deleteContentRange` inclusive) matched what the shared skill already knew.
- Two coupling notes, not defects: it imports **private** (`_`-prefixed)
  functions and **hardcodes** `{{REPO_DIR}}/...` — acceptable for
  a profile-local skill on one box, wrong for a shared one.

## What got promoted (commit `180ddb3`, v0.4.0)
- **New `rename <doc_id> --name "<title>"` verb** — Drive v3 `files.update`
  with `supportsAllDrives`. Live-tested happy path (renamed a throwaway, read
  back: title changed, contents intact) and the 404 error path. Test docs
  trashed and confirmed `trashed: true`.
- **Raw-API pitfall enriched** with the verified request shapes:
  `replaceAllText` needs `containsText.text` + `replaceText`; `insertText` at the
  body end index 400s (use `d._end_index`); `batchUpdate` is **atomic** — one bad
  request aborts the whole batch.
- **New pitfall: the concurrent-editor race** — fresh snapshot before, re-read +
  diff after, prefer string ops over positional when a human may be live-editing,
  rebase a stale plan on a fresh read (never retry), ask the user what changed,
  sweep for the corruption pattern.

## The rename-verb bug (see Pitfall 6)
First 404 test returned the raw `<HttpError ...>` string: the catch was
`urllib.error.HTTPError` but googleapiclient raises its own `HttpError`. Fixed to
`googleapiclient.errors.HttpError` with `e.status_code` / `e.content`, re-verified.

## What was deliberately NOT done
- **Twin not slimmed to a pointer** — that edits another profile's curator-managed
  skill; left for the owner/Naomi sign-off.
- No `old_name` field in the verb output (the Drive `files.update` response with
  `fields=id,name` returns only the new name; don't invent a field you don't have).

## Outcome / state
Both pieces shipped in one commit `180ddb3` (3 files, +62/−22), pre-push hook
(lint+vendor+tests) passed. `google-docs` v0.3.0 → v0.4.0; description 22 → 25
triggers, none dropped. Every profile inherits it via `skills.external_dirs`
with zero wiring.
