# google-docs

Used to read contents & comments, and write google docs — for the Hermes
agent. The user speaks in document terms ("start a doc called Trip Plan",
"change every 'Rome' to 'Milan'", "make 'Agenda' a heading", "what did
people comment on this?"); the agent calls one small CLI and the change lands
in a real Google Doc — no Docs-API request-model or character-index thinking
required of the model.

Backed by the Google Docs + Drive APIs under a service account. The model-facing
surface (`SKILL.md` + the CLI's verbs/output) speaks only in documents; this
README is the human/developer side — setup, the backend, and rationale.

## What this is for

- *"Write a doc titled Trip Plan that says 'Rome trip'."*
- *"Add a line to the doc: 'Bring sunscreen'."*
- *"After 'Day 1' insert 'Fly to Rome'."*
- *"Change every 'Rome' to 'Milan'."*
- *"Rename the doc to 'Trip Plan — v2'."*
- *"Make 'Agenda' a heading and bold 'urgent'."*
- *"What does the document say?"*
- *"What comments are on the doc — and what text are they attached to?"*

## What this is NOT for

- **Spreadsheets** — cells, tabs, totals. Different surface; use the sheets /
  donations tooling.
- **Sharing / moving** documents. This skill writes document *contents*; it does
  not manage Drive permissions or foldering (beyond creating new docs in the one
  configured folder).

## How it works

```
docs.py <verb>
   │  (re-execs into the Hermes venv for google-api-python-client)
   ▼
Google Docs API v1 + Drive API v3  — service-account auth (ADC via GOOGLE_APPLICATION_CREDENTIALS)
   │  create → Drive files.create (mimeType google-apps.document) in the shared folder
   │  rename → Drive files.update (name) — title change, contents untouched
   │  edit   → Docs documents.batchUpdate (insertText / replaceAllText /
   │           updateTextStyle / updateParagraphStyle)
   │  comments → Drive v3 REST (files comments list/create) — the Docs API
   │           has no comment request at all
   │  images → Docs batchUpdate (insertInlineImage from a public URL /
   │           deleteContentRange); Google fetches the URL at insert time.
   │           --file uploads a local image to the Shared Drive, shares it
   │           link-readable, inserts it, then trashes the upload artifact
   ▼
one JSON object on stdout (document_id / title / url / action / occurrences …)
```

Edits are addressed by **text, not indices.** `insert --after`, `style`, and
`delete` locate the target by searching the document's text and translating the
match back into the Docs (startIndex, endIndex) range the API needs — so the
model never computes a character position. `delete` is implemented as a
find-and-replace with empty text, which lets the API manage index shifts safely.

### Identity & attribution (why a service account)

Runtime auth is a **Google Cloud service account**, not user OAuth — OAuth
3-legged flows carry the "agentic use → account suspension" risk; a service
account is a non-human identity metered against a GCP project with no user to
suspend. It acts as *itself* (no domain-wide delegation). Existing documents are
reached by **sharing them** to the service account's address as Editor; new
documents are created inside a shared folder the service account has Editor on,
so anyone with access to that folder sees them. Edits appear in each document's
revision history attributed to the service account — the intended split that
keeps the owner's personal Google account insulated. This skill reuses the same
service account and `.env` wiring as the Sheets work on this install; full
provisioning history lives in that project handoff, kept outside the repo.

## Setup

### 1. Reuse (or provision) the service account

This skill uses the same service-account key and ADC wiring as the Sheets
integration — `~/.hermes/creds/<key>.json` (chmod 600) pointed at by
`~/.hermes/.env`:

```
GOOGLE_APPLICATION_CREDENTIALS=/home/<user>/.hermes/creds/<key>.json
```

The Hermes venv (`~/.hermes/hermes-agent/venv/bin/python`) already has
`google-api-python-client` + `google-auth`; the script re-execs into it, so no
system `pip install` is needed.

### 2. Enable the Docs API (one-time)

The Sheets work enabled the Sheets + Drive APIs on the project; **Docs is
separate.** Enable the **Google Docs API** on the same GCP project, then wait a
minute for it to propagate:

```
https://console.cloud.google.com/apis/library/docs.googleapis.com
```

### 3. Configure the shared folder

New documents are created inside a Drive folder the service account has Editor
on (e.g. the "Hermes Shared" folder). Put its ID — the string in the folder's
URL — in the config:

```bash
mkdir -p ~/.config/google-docs
echo 'GOOGLE_DOCS_FOLDER_ID=<folder-id-from-its-URL>' > ~/.config/google-docs/config.env
chmod 600 ~/.config/google-docs/config.env
```

(See `templates/config.env.example`.) To let the agent edit an **existing**
document, share it (as Editor) with the service account's address, or drop it in
that folder.

### 4. Wire the skill into Hermes

```bash
ln -s ~/hermes-skills/google-docs ~/.hermes/skills/google-docs
hermes skills list        # confirm it's discovered
```

### 5. Test

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
D=~/.hermes/skills/google-docs/scripts/docs.py
ID=$($PY $D create --title "Docs skill test" --text "Rome trip" | python3 -c 'import sys,json;print(json.load(sys.stdin)["document_id"])')
$PY $D append  $ID --text "Day 1: fly to Rome"
$PY $D rename  $ID --name "Docs skill test (renamed)"
$PY $D replace $ID --find Rome --with Milan          # expect occurrences: 2
$PY $D style   $ID --find "Milan trip" --heading 1
$PY $D read    $ID                                   # inspect the result
# then trash the test document in Drive
```

## The verbs

`create` · `find` · `read` · `rename` · `read-comments` · `comment` · `append` ·
`insert` · `replace` · `style` · `delete` · `insert-image` · `resize-image` ·
`delete-image` — see
[`SKILL.md`](./SKILL.md) for the model-facing contract and the word→call
mapping. Each prints one JSON object; failures are `{"ok": false, "error": "…"}`
with exit 1. `delete` and `delete-image` are destructive and refuse to run
without `--confirm`.

## Design notes

- **Text-addressed editing.** Every edit locates its target by searching the
  document text, so the model works with words it can see (`--after "Day 1"`,
  `--find "urgent"`) instead of raw character indices.
- **Create lands in the shared folder.** A service account has no ordinary Drive
  of its own that a human can browse, so new docs are created directly inside the
  shared folder; folder access is what makes them visible to the user.
- **Destructive-op guard.** `delete` and `delete-image` require `--confirm`,
  matching the repo's footgun convention for a local small-model audience.
- **Comments are quote-based, not highlighted.** The Docs API has no request
  that creates a comment, so `comment` goes through the Drive v3 REST
  (`files comments create`, the same surface `read-comments` reads from).
  Google's Drive docs state that a developer-supplied anchor is *saved* but
  treated as un-anchored by the Workspace apps — the editor will not highlight
  the section (verified live: `quotedFileContent` comes back empty and the
  comment survives its text being deleted). So the verb quotes the section as
  the comment's first line (`"section text"\n<note>`), which is the most the
  API can guarantee, and sends a best-effort text-range anchor
  (`{"r":"head","a":"{\"startIndex\":N,\"endIndex\":M}"}`) so the API record
  carries the location. `read-comments` reports `quoted_anchor` empty for
  these comments — expected, not an error.
- **Images.** `insert-image` takes a public HTTPS image URL that
  Google fetches at insert time (`insertInlineImage.uri`). `--replace
  "<placeholder>"` swaps a text marker (e.g. `[IMAGE:x]`) for the image in one
  atomic batch. Passing only `--width` (points) preserves the image's aspect
  ratio.
- **Resize is delete-and-reinsert.** The Docs API has *no* request to change an
  existing image's size, so `resize-image` deletes the target image and
  re-inserts it (hence it needs `--url` again) at the new size and same position,
  in one atomic batch. Existing images are addressed by `--nth` (reading order)
  or `--after` an anchor.
- **Bad image URLs fail loudly.** `insert-image`/`resize-image` pre-check the
  `--url` (a web-page URL, a 404, or an unsupported/oversized type gets a clear
  "give a direct image link" error) and translate the Docs API's opaque
  "problem retrieving the image" 400 into the same actionable message — so a
  wrong URL is corrected, not retried blindly.
- **Local images (`--file`).** The Docs API can only insert an image by URL, so a
  local file is uploaded to the Shared Drive, shared link-readable, inserted, and
  then the upload is trashed — the document keeps its own embedded copy (verified:
  the embedded image is a fresh `googleusercontent.com/docsz/…` object that
  survives deleting the source). This is the *supported* way to add a local
  image; the earlier agent failures came from ad-hoc scripts that uploaded to
  My Drive (where the quota-less service account can't own files) and tried to
  insert by Drive `objectId` (not a valid image URI).

## Debug log

Every invocation and its result are appended to `~/.hermes/logs/google-docs.log`
(one JSON line each; string values truncated). This records exactly what the
agent passed — verb, arguments, the `--url` — so a failed run can be diagnosed
after the fact. It's local-only, next to the other Hermes logs, and logging
never breaks the tool (failures to write are swallowed).

## Files

| Path | What |
|---|---|
| `SKILL.md` | Model-facing contract (document vocabulary only). |
| `scripts/docs.py` | The CLI. |
| `templates/config.env.example` | Template for `~/.config/google-docs/config.env`. |
| `~/.config/google-docs/config.env` | `GOOGLE_DOCS_FOLDER_ID` (not in the repo). |
| `~/.hermes/creds/<key>.json`, `~/.hermes/.env` | Service-account key + ADC path (not in the repo). |

## License

MIT.
