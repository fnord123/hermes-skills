# google-docs

Create, read, and edit Google Docs documents for the Hermes agent. The user
speaks in document terms ("start a doc called Trip Plan", "change every 'Rome'
to 'Milan'", "make 'Agenda' a heading"); the agent calls one small CLI and the
change lands in a real Google Doc — no Docs-API request-model or character-index
thinking required of the model.

Backed by the Google Docs + Drive APIs under a service account. The model-facing
surface (`SKILL.md` + the CLI's verbs/output) speaks only in documents; this
README is the human/developer side — setup, the backend, and rationale.

## What this is for

- *"Write a doc titled Trip Plan that says 'Rome trip'."*
- *"Add a line to the doc: 'Bring sunscreen'."*
- *"After 'Day 1' insert 'Fly to Rome'."*
- *"Change every 'Rome' to 'Milan'."*
- *"Make 'Agenda' a heading and bold 'urgent'."*
- *"What does the document say?"*

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
   │  edit   → Docs documents.batchUpdate (insertText / replaceAllText /
   │           updateTextStyle / updateParagraphStyle)
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

(See `examples/config.env.example`.) To let the agent edit an **existing**
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
D=~/.hermes/skills/google-docs/examples/docs.py
ID=$($PY $D create --title "Docs skill test" --text "Rome trip" | python3 -c 'import sys,json;print(json.load(sys.stdin)["document_id"])')
$PY $D append  $ID --text "Day 1: fly to Rome"
$PY $D replace $ID --find Rome --with Milan          # expect occurrences: 2
$PY $D style   $ID --find "Milan trip" --heading 1
$PY $D read    $ID                                   # inspect the result
# then trash the test document in Drive
```

## The verbs

`create` · `read` · `append` · `insert` · `replace` · `style` · `delete` — see
[`SKILL.md`](./SKILL.md) for the model-facing contract and the word→call mapping.
Each prints one JSON object; failures are `{"ok": false, "error": "…"}` with
exit 1. `delete` is destructive and refuses to run without `--confirm`.

## Design notes

- **Text-addressed editing.** Every edit locates its target by searching the
  document text, so the model works with words it can see (`--after "Day 1"`,
  `--find "urgent"`) instead of raw character indices.
- **Create lands in the shared folder.** A service account has no ordinary Drive
  of its own that a human can browse, so new docs are created directly inside the
  shared folder; folder access is what makes them visible to the user.
- **Destructive-op guard.** `delete` requires `--confirm`, matching the repo's
  footgun convention for a local small-model audience.

## Files

| Path | What |
|---|---|
| `SKILL.md` | Model-facing contract (document vocabulary only). |
| `examples/docs.py` | The CLI. |
| `examples/config.env.example` | Template for `~/.config/google-docs/config.env`. |
| `~/.config/google-docs/config.env` | `GOOGLE_DOCS_FOLDER_ID` (not in the repo). |
| `~/.hermes/creds/<key>.json`, `~/.hermes/.env` | Service-account key + ADC path (not in the repo). |

## License

MIT.
