#!/usr/bin/env python3
"""docs.py — create, read, and edit Google Docs via a service account.

A document-shaped wrapper over the Google Docs + Drive APIs so the agent works
in document vocabulary (title, text, headings, find-and-replace) and never has
to think about the batchUpdate request model or raw character indices.

Auth: a Google service account via GOOGLE_APPLICATION_CREDENTIALS (loaded from
~/.hermes/.env if not already in the environment). New documents are created
inside the shared Drive folder named in ~/.config/google-docs/config.env
(GOOGLE_DOCS_FOLDER_ID); an existing document is reachable when the service
account is shared as Editor on it (directly or via that folder). No user OAuth.

Verbs (each prints ONE JSON object on stdout; exit 1 on error):
  create  --title <t> [--text <initial>]        new document in the shared folder
  read    <doc_id>                              title + plain-text body
  append  <doc_id> --text <t>                    add text as a new paragraph at the end
  insert  <doc_id> --text <t> (--after "<anchor>" | --at-start)
                                                 insert text at a located spot
  replace <doc_id> --find <s> --with <s> [--match-case]
                                                 find-and-replace everywhere
  style   <doc_id> --find "<text>" [--bold] [--italic] [--underline]
                  [--heading N] [--match-case]   format every occurrence of text
  delete  <doc_id> --find "<text>" --confirm [--match-case]
                                                 DESTRUCTIVE: remove text everywhere
"""

import os
import sys
from pathlib import Path

# Run under the Hermes venv (has google-api-python-client + google-auth).
_VENV = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
if _VENV.exists() and sys.executable != str(_VENV):
    os.execv(str(_VENV), [str(_VENV), *sys.argv])

import argparse
import json

CONFIG_DIR = Path.home() / ".config" / "google-docs"
CONFIG_ENV = CONFIG_DIR / "config.env"
HERMES_ENV = Path.home() / ".hermes" / ".env"
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]
DOC_MIME = "application/vnd.google-apps.document"


def out(d, code=0):
    print(json.dumps(d))
    sys.exit(code)


def fail(msg):
    out({"ok": False, "error": str(msg)}, 1)


def _nl(s):
    """Interpret the escape sequences \\n and \\t in text passed on the command
    line (a literal '\\n' in a shell-quoted argument would otherwise be written
    to the document verbatim). Other backslashes are left untouched."""
    if not s:
        return s
    return s.replace("\\n", "\n").replace("\\t", "\t")


def _load_env_var(path, key):
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and "=" in line and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _folder_id():
    fid = os.environ.get("GOOGLE_DOCS_FOLDER_ID") or _load_env_var(CONFIG_ENV, "GOOGLE_DOCS_FOLDER_ID")
    if not fid:
        fail("GOOGLE_DOCS_FOLDER_ID not set — add it to "
             "~/.config/google-docs/config.env (see README).")
    return fid


def _creds():
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        gac = _load_env_var(HERMES_ENV, "GOOGLE_APPLICATION_CREDENTIALS")
        if gac:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gac
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        fail("GOOGLE_APPLICATION_CREDENTIALS is not set (expected in ~/.hermes/.env).")
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except Exception as e:  # noqa: BLE001
        fail(f"auth/build failed: {e}")


def _docs():
    try:
        from googleapiclient.discovery import build
        return build("docs", "v1", credentials=_creds(), cache_discovery=False)
    except Exception as e:  # noqa: BLE001
        fail(f"auth/build failed: {e}")


def _drive():
    try:
        from googleapiclient.discovery import build
        return build("drive", "v3", credentials=_creds(), cache_discovery=False)
    except Exception as e:  # noqa: BLE001
        fail(f"auth/build failed: {e}")


# ── document text / index helpers ────────────────────────────────────────────

def _get_doc(docs, doc_id):
    return docs.documents().get(documentId=doc_id).execute()


def _flatten(doc):
    """Return (text, idx_map): the document's plain text, plus a parallel list
    where idx_map[i] is the Google Docs character index of text[i]. This lets a
    substring match be translated back into a Docs (startIndex, endIndex) range.
    Only paragraph text runs are walked (tables/other structures are skipped)."""
    text_parts, idx_map = [], []
    for element in doc.get("body", {}).get("content", []):
        for pe in element.get("paragraph", {}).get("elements", []):
            run = pe.get("textRun")
            if not run or not run.get("content"):
                continue
            start = pe.get("startIndex", 0)
            content = run["content"]
            for offset, ch in enumerate(content):
                text_parts.append(ch)
                idx_map.append(start + offset)
    return "".join(text_parts), idx_map


def _end_index(doc):
    """The insert index for appending at the very end of the body — just before
    the body's final newline (Docs indexes are 1-based)."""
    end = 1
    for element in doc.get("body", {}).get("content", []):
        ei = element.get("endIndex")
        if isinstance(ei, int) and ei > end:
            end = ei
    return max(end - 1, 1)


def _find_ranges(text, idx_map, needle, match_case):
    """Non-overlapping (startIndex, endIndex) Docs ranges for each occurrence."""
    if not needle:
        return []
    hay = text if match_case else text.lower()
    ndl = needle if match_case else needle.lower()
    ranges, pos = [], 0
    while True:
        i = hay.find(ndl, pos)
        if i < 0:
            break
        start = idx_map[i]
        end = idx_map[i + len(ndl) - 1] + 1
        ranges.append((start, end))
        pos = i + len(ndl)
    return ranges


def _batch(docs, doc_id, requests):
    return docs.documents().batchUpdate(
        documentId=doc_id, body={"requests": requests}).execute()


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_create(args):
    drive = _drive()
    folder = _folder_id()
    try:
        meta = {"name": args.title, "mimeType": DOC_MIME, "parents": [folder]}
        f = drive.files().create(
            body=meta, fields="id, name, webViewLink",
            supportsAllDrives=True).execute()
        doc_id = f["id"]
        if args.text:
            _batch(_docs(), doc_id,
                   [{"insertText": {"location": {"index": 1}, "text": _nl(args.text)}}])
        out({"ok": True, "document_id": doc_id, "title": f.get("name", args.title),
             "url": f.get("webViewLink") or f"https://docs.google.com/document/d/{doc_id}/edit"})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_read(args):
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        text, _ = _flatten(doc)
        out({"ok": True, "document_id": args.doc_id,
             "title": doc.get("title", ""), "text": text})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_append(args):
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        index = _end_index(doc)
        existing, _ = _flatten(doc)
        body = _nl(args.text)
        # Start a new paragraph unless the document is effectively empty.
        text = ("\n" + body) if existing.strip() else body
        _batch(docs, args.doc_id,
               [{"insertText": {"location": {"index": index}, "text": text}}])
        out({"ok": True, "document_id": args.doc_id, "action": "appended",
             "characters": len(body)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_insert(args):
    if not args.at_start and not args.after:
        fail("insert needs either --after \"<anchor text>\" or --at-start.")
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        if args.at_start:
            index = 1
        else:
            text, idx_map = _flatten(doc)
            ranges = _find_ranges(text, idx_map, args.after, args.match_case)
            if not ranges:
                fail(f"couldn't find {args.after!r} in the document to insert after.")
            index = ranges[0][1]
        body = _nl(args.text)
        _batch(docs, args.doc_id,
               [{"insertText": {"location": {"index": index}, "text": body}}])
        out({"ok": True, "document_id": args.doc_id, "action": "inserted",
             "at_index": index, "characters": len(body)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_replace(args):
    docs = _docs()
    try:
        resp = _batch(docs, args.doc_id, [{"replaceAllText": {
            "containsText": {"text": args.find, "matchCase": bool(args.match_case)},
            "replaceText": _nl(args.with_)}}])
        changed = 0
        for reply in resp.get("replies", []):
            changed += reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
        result = {"ok": True, "document_id": args.doc_id, "action": "replaced",
                  "occurrences": changed}
        if changed == 0:
            result["note"] = f"no occurrences of {args.find!r} were found; nothing changed."
        out(result)
    except Exception as e:  # noqa: BLE001
        fail(e)


_HEADINGS = {"0": "NORMAL_TEXT", "1": "HEADING_1", "2": "HEADING_2",
             "3": "HEADING_3", "4": "HEADING_4", "5": "HEADING_5", "6": "HEADING_6"}


def cmd_style(args):
    if not (args.bold or args.italic or args.underline or args.heading is not None):
        fail("style needs at least one of --bold, --italic, --underline, or --heading N.")
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        text, idx_map = _flatten(doc)
        ranges = _find_ranges(text, idx_map, args.find, args.match_case)
        if not ranges:
            fail(f"couldn't find {args.find!r} in the document to style.")

        text_style, fields = {}, []
        if args.bold:
            text_style["bold"] = True
            fields.append("bold")
        if args.italic:
            text_style["italic"] = True
            fields.append("italic")
        if args.underline:
            text_style["underline"] = True
            fields.append("underline")

        named = None
        if args.heading is not None:
            named = _HEADINGS.get(str(args.heading))
            if not named:
                fail("--heading must be 0 (normal) through 6.")

        requests = []
        for start, end in ranges:
            rng = {"startIndex": start, "endIndex": end}
            if text_style:
                requests.append({"updateTextStyle": {
                    "range": rng, "textStyle": text_style,
                    "fields": ",".join(fields)}})
            if named:
                requests.append({"updateParagraphStyle": {
                    "range": rng, "paragraphStyle": {"namedStyleType": named},
                    "fields": "namedStyleType"}})
        _batch(docs, args.doc_id, requests)
        out({"ok": True, "document_id": args.doc_id, "action": "styled",
             "occurrences": len(ranges)})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_delete(args):
    # Destructive. Requires --confirm (footgun guard). Implemented as a
    # find-and-replace with empty text so the API manages index shifts safely.
    if not args.confirm:
        fail("delete removes text from the document and cannot be undone by this "
             "tool. Re-run with --confirm ONLY after the user has explicitly "
             "approved removing this exact text.")
    docs = _docs()
    try:
        resp = _batch(docs, args.doc_id, [{"replaceAllText": {
            "containsText": {"text": args.find, "matchCase": bool(args.match_case)},
            "replaceText": ""}}])
        changed = 0
        for reply in resp.get("replies", []):
            changed += reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
        result = {"ok": True, "document_id": args.doc_id, "action": "deleted",
                  "occurrences": changed}
        if changed == 0:
            result["note"] = f"no occurrences of {args.find!r} were found; nothing removed."
        out(result)
    except Exception as e:  # noqa: BLE001
        fail(e)


def main():
    p = argparse.ArgumentParser(prog="docs", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("create", help="create a new document in the shared folder")
    g.add_argument("--title", required=True)
    g.add_argument("--text", help="initial body text (optional)")
    g.set_defaults(func=cmd_create)

    g = sub.add_parser("read", help="read a document's title and text")
    g.add_argument("doc_id")
    g.set_defaults(func=cmd_read)

    g = sub.add_parser("append", help="add text as a new paragraph at the end")
    g.add_argument("doc_id")
    g.add_argument("--text", required=True)
    g.set_defaults(func=cmd_append)

    g = sub.add_parser("insert", help="insert text after an anchor or at the start")
    g.add_argument("doc_id")
    g.add_argument("--text", required=True)
    g.add_argument("--after", help="insert immediately after the first occurrence of this text")
    g.add_argument("--at-start", dest="at_start", action="store_true",
                   help="insert at the very beginning of the document")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_insert)

    g = sub.add_parser("replace", help="find and replace text everywhere")
    g.add_argument("doc_id")
    g.add_argument("--find", required=True)
    g.add_argument("--with", dest="with_", required=True, help="replacement text")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_replace)

    g = sub.add_parser("style", help="format every occurrence of some text")
    g.add_argument("doc_id")
    g.add_argument("--find", required=True, help="the text to format")
    g.add_argument("--bold", action="store_true")
    g.add_argument("--italic", action="store_true")
    g.add_argument("--underline", action="store_true")
    g.add_argument("--heading", type=int, help="0 (normal) through 6")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_style)

    g = sub.add_parser("delete", help="DESTRUCTIVE: remove text everywhere")
    g.add_argument("doc_id")
    g.add_argument("--find", required=True, help="the text to remove")
    g.add_argument("--confirm", action="store_true",
                   help="required; only after explicit user approval")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_delete)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
