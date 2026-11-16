# Comments & Suggestions API (verified live against a Commenter-role service account)

The 2026 Docs API batchUpdate supports real anchored comments and suggestion-mode
writes. All request shapes below were verified end-to-end; response paths are
exact — parse them from these shapes, do not guess.

## Requests (batchUpdate)

- **Create anchored comment** (renders highlighted in the editor):
  `{"insertComment": {"content": "text", "range": {"startIndex": s, "endIndex": e}}}`
  Response: `replies[0].insertComment.commentThread.commentId` (NOT
  `replies[0].commentId` — the thread object sits in between). The thread also
  carries `status` ("OPEN"), `headPost.contentHtml`, and `headPost.plainTextQuote`.
  Fails with 400 "The provided range cannot be commented on" if the range has no
  real base text — an empty document has nothing to anchor on.
- **Reply to a comment**: `{"addCommentReply": {"commentId": "doco.…",
  "post": {"content": "text"}}}`.
- **Reply to a SUGGESTION** (attach discussion to a human's pending edit): same
  request with `"suggestionId": "suggest.…"` instead of `commentId`.
- **Resolve a thread**: `{"addCommentReply": {"commentId": …,
  "post": {"commentAction": "RESOLVE"}}}` — a post with only the action.
- **Suggestion-mode writes**: append `"writeControl": {"writeMode": "SUGGEST"}`
  to the batchUpdate body; every write request in the batch (insertText,
  replaceAllText, deleteContentRange) becomes a pending suggestion instead of an
  edit. Response carries `suggestionResponses[].createdSuggestionIds`.
  A suggested replace = `replaceAllText` in one request under SUGGEST.
- **Read pending suggestions**: `documents.get` with
  `suggestionsViewMode: "SUGGESTIONS_INLINE"`; marker ids appear as
  `suggestedInsertionIds` / `suggestedDeletionIds` in the JSON. The default view
  mixes suggested insertions into the rendered text — to compare base text
  before/after a suggestion write, diff two identical-view reads, not one read
  against an expectation.
- **Multi-occurrence targeting (gdocs-service verbs, live since 2026-11-16).**
  An anchor phrase that repeats is refused by default (message names the valid
  range). `occurrence=N` (1-based, document order) pins the Nth copy —
  `suggest_style` and `suggest_delete`. `through_occurrence=N` extends a delete
  span to the Nth copy of the end anchor. `all_occurrences=true` proposes on
  every copy in one batch (style: safe, indices never shift; delete: lands as
  one replaceAll-to-empty proposal). `through` cannot combine with
  multi-target modes. Never conclude "cannot target repeated text" — that
  limitation is retired.
- **Drive comments listing gotchas (live-verified 2026-11-16).**
  `comments.list` needs `supportsAllDrives=true` or a shared-drive doc answers
  "File not found" while the Docs API still serves the body. `isResolved` is
  NOT a valid field (400); replies accept only `id,createdTime,content` — a
  nested `author` field 400s the whole call, and an empty result may be a
  masked 400: print the status code, never just `.get("comments", [])`.

## List comment threads (Drive API v3)

`drive.files().comments().list(fileId=…)` lists Docs-native comment threads
created via the Docs API. Valid fields: `comments(id,content,deleted)` —
there is NO `status` field; requesting it 400s.

## Permission physics (what a Commenter can and cannot do)

Verified with a service account granted only Commenter:
- ALLOWED: insertComment, addCommentReply, RESOLVE, SUGGEST-mode writes,
  replying to human suggestions by suggestionId.
- REFUSED with `403 "Write access is required, comment-only access is not
  sufficient."`: any direct (non-SUGGEST) write, and `acceptSuggestion`.
  The human-gate is server-enforced, not client policy.
- `acceptSuggestion` / reject exist as request names and fail at that 403 for a
  commenter — an agent holding Commenter physically cannot land its own changes.

## Drive permission topology rules

- Drive inheritance is **additive-maximum**: a child grant can only UPGRADE,
  a child can never downgrade what an ancestor grants. Design least privilege
  as deny-by-default at the top with explicit Editor carve-out folders below
  ("carve-up"), never "allow-with-carve-down" (impossible).
- Shared drives have no Commenter member tier (Manager/Content manager/Editor/
  Contributor/Viewer only). A Commenter grant on a shared drive is a file/folder
  permission, and a doc carrying its own explicit Commenter grant works fine at
  a shared-drive root even when the SA holds no drive membership.
- Shared-drive **Editor cannot trash/delete** files (403
  insufficientFilePermissions); deletion needs File organizer. Do not write
  cleanup logic assuming Editor can clean up.
- A commenter-role grant sets `canShare: false` — the agent cannot promote
  itself.
- Quota: 100 service accounts per GCP project by default (raiseable). Per-agent
  SAs are free and enforce doc-scoping via Google's own ACLs; per-profile SAs
  also give comment/suggestion author attribution per agent.
- An SA cannot be @-mentioned in the Docs comment picker (users only) —
  address a specific agent in a comment by convention, e.g. a first-token
  prefix like `<sa-name>: request`.

## Reading pending STYLE suggestions back (live-verified 2026-11-16)

- `updateTextStyle` under SUGGEST returns its ids in
  `suggestionResponses[].updatedSummarySuggestionIds`, NOT
  `createdSuggestionIds` (text insert/delete uses created...). Count BOTH
  keys or a successful style write reports "0 suggestion(s)".
- Pending formatting is readable from a PLAIN `documents.get` (no special
  view): each affected `textRun` carries
  `suggestedTextStyleChanges: {suggestionId: {textStyle,
  textStyleSuggestionState}}`. SUGGESTIONS_INLINE does NOT merge style
  changes into `style` — only text insert/delete are inlined. Per-span
  verification of suggested formatting = read suggestedTextStyleChanges.
- Same-author style edits on an overlapping range MERGE into the existing
  summary suggestion id (updatedSummary, not a new created id).
- `documents.get` `suggestionsViewMode` accepts only SUGGESTIONS_INLINE or
  the parameter omitted; SUGGESTIONS_VIEW and SUGGESTED_RESULT_VIEW both
  400 (Invalid value at 'suggestions_view_mode') as of 11-16 — the older
  enum names in this file's earlier notes no longer work on get.

- PENDING-STATE SEMANTICS (live-verified 11-16, multi-occurrence delete
  probes): the SUGGESTIONS_INLINE view doc_find uses is AS-IF-ACCEPTED —
  a pending deletion REMOVES its text from subsequent match counts, so
  occurrence indices always address the current proposal-adjusted text.
  A pending deletion landing on a PENDING INSERTION cancels the pair and
  the text vanishes from the view entirely. Overlapping same-author
  deletes MERGE into the existing summary suggestion id.

## Tab-format documents

Newer documents put ALL content in `tabs[]` and have no top-level `body` key at
all. Any text harvest must fall back:
```python
def text_source(doc):
    src = doc.get("body")
    if doc.get("tabs"):
        src = doc["tabs"][0]["documentTab"].get("body")
    return src or {"content": []}
```
batchUpdate indexes address tab content directly (location/range work the same);
no tabId is needed for single-tab docs. Reading only top-level `body` returns
empty and falsely reports the document as empty.