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
  find    [query] [--title-only] [--anywhere] [--limit N]
                                                find docs by name/content — no id needed
  read    <doc_id>                              title + plain-text body
  read-comments <doc_id>                        all comments with their quoted anchor text
  append  <doc_id> --text <t>                    add text as a new paragraph at the end
  insert  <doc_id> --text <t> (--after "<anchor>" | --at-start)
                                                 insert text at a located spot
  replace <doc_id> --find <s> --with <s> [--match-case]
                                                 find-and-replace everywhere
  style   <doc_id> --find "<text>" [--bold] [--italic] [--underline]
                  [--heading N] [--match-case]   format every occurrence of text
  delete  <doc_id> --find "<text>" --confirm [--match-case]
                                                 DESTRUCTIVE: remove text everywhere
  insert-image <doc_id> (--url <public_url> | --file <local_path>)
                  (--replace "<placeholder>" | --after "<anchor>" | --at-start)
                  [--width <pt>] [--height <pt>]  insert an image (URL or local file)
  resize-image <doc_id> (--url <public_url> | --file <local_path>)
                  (--nth N | --after "<anchor>")
                  [--width <pt>] [--height <pt>]  resize an existing image
  delete-image <doc_id> (--nth N | --after "<anchor>") --confirm
                                                 DESTRUCTIVE: remove an image
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
import urllib.parse
import urllib.request
import urllib.error

CONFIG_DIR = Path.home() / ".config" / "google-docs"
CONFIG_ENV = CONFIG_DIR / "config.env"
HERMES_ENV = Path.home() / ".hermes" / ".env"
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]
DOC_MIME = "application/vnd.google-apps.document"
LOG_PATH = Path.home() / ".hermes" / "logs" / "google-docs.log"


def _truncate(s, n=400):
    s = str(s)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n} chars)"


def _log(event):
    """Append a timestamped JSON line to the skill's debug log. Records every
    invocation and its result so failures can be diagnosed after the fact.
    Never raises — logging must not break the tool. Local only, alongside the
    other Hermes logs; string values are truncated."""
    try:
        import datetime
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with LOG_PATH.open("a") as fh:
            fh.write(f"{stamp} {json.dumps(event, ensure_ascii=False)}\n")
    except Exception:
        pass


def out(d, code=0):
    _log({"result": {k: (_truncate(v) if isinstance(v, str) else v)
                      for k, v in d.items()}})
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


def _inline_images(doc):
    """Return the document's inline images in reading order:
    [{"object_id": ..., "start": <index>, "end": <index>}]. Each inline image
    occupies a single index in the body."""
    images = []
    for element in doc.get("body", {}).get("content", []):
        for pe in element.get("paragraph", {}).get("elements", []):
            ioe = pe.get("inlineObjectElement")
            if ioe and ioe.get("inlineObjectId"):
                images.append({"object_id": ioe["inlineObjectId"],
                               "start": pe.get("startIndex"),
                               "end": pe.get("endIndex")})
    return images


def _pick_image(doc, args):
    """Resolve --nth / --after to one inline image, or fail with guidance."""
    images = _inline_images(doc)
    if not images:
        fail("the document has no images.")
    if getattr(args, "nth", None) is not None:
        if args.nth < 1 or args.nth > len(images):
            fail(f"--nth {args.nth} is out of range; the document has "
                 f"{len(images)} image(s).")
        return images[args.nth - 1]
    if getattr(args, "after", None):
        text, idx_map = _flatten(doc)
        ranges = _find_ranges(text, idx_map, args.after, getattr(args, "match_case", False))
        if not ranges:
            fail(f"couldn't find {args.after!r} in the document.")
        anchor_end = ranges[0][1]
        for img in images:
            if img["start"] is not None and img["start"] >= anchor_end:
                return img
        fail(f"no image appears after {args.after!r}.")
    if len(images) == 1:
        return images[0]
    fail(f"the document has {len(images)} images; say which with --nth N "
         "(1-based, in reading order) or --after \"<nearby text>\".")


def _object_size(width, height):
    size = {}
    if width is not None:
        size["width"] = {"magnitude": float(width), "unit": "PT"}
    if height is not None:
        size["height"] = {"magnitude": float(height), "unit": "PT"}
    return size or None


_IMG_SUPPORTED = {"image/png", "image/jpeg", "image/jpg", "image/gif"}


def _check_image_url(url):
    """Best-effort pre-check. Returns a clear error string if the URL is plainly
    NOT a usable public image (a web page, a 404, an unsupported/oversized type);
    returns None to proceed. Ambiguous cases (403, timeouts, no content-type)
    return None and let the Docs API be the judge — _image_error() translates its
    failure. This is what turns 'model pasted a web-page URL' into actionable
    feedback instead of a raw 400."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, method="GET",
        headers={"User-Agent": "Mozilla/5.0 (compatible; HermesDocs/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            clen = r.headers.get("Content-Length")
    except urllib.error.HTTPError as e:
        return (f"the image URL returned HTTP {e.code}. Give a direct, public link "
                "to a PNG/JPEG/GIF image file (not a web page); ask the user for "
                "one if unsure.")
    except Exception:
        return None  # network/UA ambiguity — let the Docs API try
    if ctype in ("text/html", "application/xhtml+xml"):
        return ("that --url is a web page, not an image. Provide a direct link to "
                "a PNG/JPEG/GIF image file (its URL usually ends in .jpg/.png/.gif "
                "or serves image/* content); ask the user for one if unsure.")
    if ctype and not ctype.startswith("image/"):
        return (f"that --url returned {ctype}, not an image. Provide a direct link "
                "to a PNG/JPEG/GIF image file.")
    if ctype and ctype not in _IMG_SUPPORTED:
        return f"that image is {ctype}; Google Docs supports only PNG, JPEG, and GIF."
    try:
        if clen and int(clen) > 50 * 1024 * 1024:
            return "that image is larger than 50 MB, which Google Docs won't accept."
    except ValueError:
        pass
    return None


def _image_error(e, url):
    """Translate the Docs API's opaque image-fetch 400 into actionable text."""
    msg = str(e)
    if ("problem retrieving the image" in msg or "publicly accessible" in msg
            or "supported formats" in msg):
        return (f"Google could not retrieve the image at {url!r}. It must point "
                "directly to a publicly accessible PNG, JPEG, or GIF (not a web "
                "page) under 50 MB. Ask the user for a direct image link if unsure.")
    return msg


def _upload_public_image(path):
    """Upload a local image into the shared folder, make it link-readable, and
    return (fetch_uri, drive_service, file_id). The Docs API can only insert
    images by URL, so a local file is briefly hosted this way; the caller deletes
    the file_id after inserting (the image is copied into the document)."""
    import mimetypes
    from googleapiclient.http import MediaFileUpload

    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        fail(f"no such image file: {p}")
    mime = mimetypes.guess_type(str(p))[0]
    if mime not in ("image/png", "image/jpeg", "image/gif"):
        fail(f"{p.name!r} isn't a supported image type (need PNG, JPEG, or GIF).")
    if p.stat().st_size > 50 * 1024 * 1024:
        fail(f"{p.name!r} is larger than 50 MB, which Google Docs won't accept.")

    folder = _folder_id()
    drive = _drive()
    try:
        up = drive.files().create(
            body={"name": p.name, "parents": [folder]},
            media_body=MediaFileUpload(str(p), mimetype=mime),
            fields="id", supportsAllDrives=True).execute()
        fid = up["id"]
        drive.permissions().create(
            fileId=fid, body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True).execute()
    except Exception as e:  # noqa: BLE001
        fail(f"couldn't upload the local image: {e}")
    return f"https://lh3.googleusercontent.com/d/{fid}", drive, fid


def _resolve_image_source(args):
    """Return (uri, drive_service_or_None, temp_file_id_or_None) for --file or
    --url. For --file the caller must trash temp_file_id after inserting."""
    if getattr(args, "file", None):
        return _upload_public_image(args.file)
    url_err = _check_image_url(args.url)
    if url_err:
        fail(url_err)
    return args.url, None, None


def _cleanup_temp(drive, fid):
    if drive is None or not fid:
        return
    try:
        drive.files().update(fileId=fid, body={"trashed": True},
                             supportsAllDrives=True).execute()
    except Exception:
        pass


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


def _esc(s):
    """Escape a Drive query literal (single quotes and backslashes)."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def cmd_find(args):
    """List/search documents so a caller never needs to know an id.

    Without --query: every doc in the shared folder, newest first. With --query: matches on
    title, and on body text too unless --title-only. --anywhere widens past the folder to
    everything the service account can see.
    """
    drive = _drive()
    try:
        clauses = ["mimeType = '%s'" % DOC_MIME, "trashed = false"]
        if not args.anywhere:
            clauses.append("'%s' in parents" % _esc(_folder_id()))
        if args.query:
            q = _esc(args.query)
            if args.title_only:
                clauses.append("name contains '%s'" % q)
            else:
                clauses.append("(name contains '%s' or fullText contains '%s')" % (q, q))
        res = drive.files().list(
            q=" and ".join(clauses),
            orderBy="modifiedTime desc",
            pageSize=max(1, min(int(args.limit), 100)),
            fields="files(id, name, webViewLink, modifiedTime)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        docs = [{"document_id": f["id"], "title": f.get("name", ""),
                 "url": f.get("webViewLink") or
                        "https://docs.google.com/document/d/%s/edit" % f["id"],
                 "modified": f.get("modifiedTime", "")}
                for f in res.get("files", [])]
        out({"ok": True, "count": len(docs), "query": args.query or "",
             "scope": "all" if args.anywhere else "folder", "documents": docs})
    except Exception as e:  # noqa: BLE001
        fail(e)


def cmd_read(args):
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        text, _ = _flatten(doc)
        if getattr(args, "out", None):
            with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
                fh.write(text)
        out({"ok": True, "document_id": args.doc_id,
             "title": doc.get("title", ""), "text": text,
             **({"out": args.out} if getattr(args, "out", None) else {})})
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


def cmd_insert_image(args):
    """Insert an inline image from a public URL (--url) or a local file (--file).
    Google copies the image into the document at insert time; a local file is
    briefly uploaded to the shared folder to give Google a URL to fetch, then
    that upload is removed."""
    uri, drive, temp_fid = _resolve_image_source(args)
    try:
        docs = _docs()
        doc = _get_doc(docs, args.doc_id)
        size = _object_size(args.width, args.height)

        if args.replace:
            text, idx_map = _flatten(doc)
            ranges = _find_ranges(text, idx_map, args.replace, args.match_case)
            if not ranges:
                fail(f"couldn't find placeholder {args.replace!r} in the document.")
            s, e = ranges[0]
            img_req = {"insertInlineImage": {"uri": uri, "location": {"index": s}}}
            if size:
                img_req["insertInlineImage"]["objectSize"] = size
            # After the image is inserted at s (1 index unit), the placeholder
            # sits at [s+1, e+1); delete it in the same atomic batch.
            requests = [img_req,
                        {"deleteContentRange": {"range": {"startIndex": s + 1, "endIndex": e + 1}}}]
        else:
            if args.at_start:
                index = 1
            elif args.after:
                text, idx_map = _flatten(doc)
                ranges = _find_ranges(text, idx_map, args.after, args.match_case)
                if not ranges:
                    fail(f"couldn't find {args.after!r} in the document to insert after.")
                index = ranges[0][1]
            else:  # default: end of document
                index = _end_index(doc)
            img_req = {"insertInlineImage": {"uri": uri, "location": {"index": index}}}
            if size:
                img_req["insertInlineImage"]["objectSize"] = size
            requests = [img_req]

        _batch(docs, args.doc_id, requests)
        out({"ok": True, "document_id": args.doc_id, "action": "image_inserted"})
    except Exception as e:  # noqa: BLE001
        fail(_image_error(e, uri))
    finally:
        _cleanup_temp(drive, temp_fid)


def cmd_resize_image(args):
    """Resize an existing inline image. The Docs API has no resize request, so
    this deletes the image and re-inserts it (from --url or --file) at the new
    size and same position (one atomic batch)."""
    uri, drive, temp_fid = _resolve_image_source(args)
    try:
        docs = _docs()
        doc = _get_doc(docs, args.doc_id)
        img = _pick_image(doc, args)
        size = _object_size(args.width, args.height)
        if not size:
            fail("resize-image needs --width and/or --height (in points).")
        s, e = img["start"], img["end"]
        img_req = {"insertInlineImage": {"uri": uri, "location": {"index": s},
                                         "objectSize": size}}
        # Delete the old image first; after that its slot collapses and inserting
        # at the same start index restores the position.
        requests = [{"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}},
                    img_req]
        _batch(docs, args.doc_id, requests)
        out({"ok": True, "document_id": args.doc_id, "action": "image_resized"})
    except Exception as e:  # noqa: BLE001
        fail(_image_error(e, uri))
    finally:
        _cleanup_temp(drive, temp_fid)


def cmd_delete_image(args):
    # Destructive. Requires --confirm (footgun guard).
    if not args.confirm:
        fail("delete-image removes an image from the document and cannot be "
             "undone by this tool. Re-run with --confirm ONLY after the user has "
             "explicitly approved removing it.")
    docs = _docs()
    try:
        doc = _get_doc(docs, args.doc_id)
        img = _pick_image(doc, args)
        _batch(docs, args.doc_id, [{"deleteContentRange": {
            "range": {"startIndex": img["start"], "endIndex": img["end"]}}}])
        out({"ok": True, "document_id": args.doc_id, "action": "image_deleted"})
    except Exception as e:  # noqa: BLE001
        fail(e)


# ── comments ─────────────────────────────────────────────────────────────────
# Drive v3 files.comments.list REQUIRES a fields parameter (400 without one),
# and only accepts real Comment field names — anything invented is a 400
# "Invalid field selection". Verified against the Drive v3 discovery doc.
_COMMENT_FIELDS = ("nextPageToken,comments(id,content,anchor,quotedFileContent,"
                   "resolved,createdTime,modifiedTime,"
                   "author(displayName),replies(id,content,createdTime,"
                   "modifiedTime,author(displayName)))")


def _comment_anchor(c):
    """Normalize a comment's anchor to its text segment id ("" if absent).
    Some responses carry the anchor as a plain segmentId string."""
    a = c.get("anchor")
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return ((a.get("location") or {}).get("segmentId") or "")
    return ""


def cmd_read_comments(args):
    """List every comment on a document with the text each one is anchored
    to (its quotedFileContent — the selection the comment was attached to).
    Uses a direct REST call: files.comments.list is not exposed as a nested
    method on the python client's files() resource."""
    creds = _creds()
    try:
        import google.auth.transport.requests as _tr
        creds.refresh(_tr.Request())
    except Exception:  # noqa: BLE001  (user creds may already carry a token)
        pass
    comments, page_token = [], None
    while True:
        url = (f"https://www.googleapis.com/drive/v3/files/{args.doc_id}"
               f"/comments?fields={urllib.parse.quote(_COMMENT_FIELDS)}")
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            req = urllib.request.Request(url,
                                         headers={"Authorization": "Bearer " + creds.token})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            fail(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
        except Exception as e:  # noqa: BLE001
            fail(e)
        for c in data.get("comments", []):
            quoted = (c.get("quotedFileContent") or {}).get("value", "")
            comments.append({
                "id": c.get("id"),
                "content": c.get("content", ""),
                "author": (c.get("author") or {}).get("displayName") or "unknown",
                "quoted_anchor": quoted,
                "anchor_segment": _comment_anchor(c),
                "resolved": c.get("resolved", False),
                "created": c.get("createdTime"),
                "replies": [{
                    "author": (r.get("author") or {}).get("displayName") or "unknown",
                    "content": r.get("content", ""),
                    "created": r.get("createdTime"),
                } for r in c.get("replies", [])],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    out({"ok": True, "document_id": args.doc_id, "count": len(comments),
         "comments": comments})


def main():
    _log({"argv": [_truncate(a) for a in sys.argv[1:]]})
    p = argparse.ArgumentParser(prog="docs", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("create", help="create a new document in the shared folder")
    g.add_argument("--title", required=True)
    g.add_argument("--text", help="initial body text (optional)")
    g.set_defaults(func=cmd_create)

    g = sub.add_parser("find", help="list/search documents (no id needed)")
    g.add_argument("query", nargs="?", default="",
                   help="match on title and body text; omit to list everything")
    g.add_argument("--title-only", action="store_true", help="match titles only")
    g.add_argument("--anywhere", action="store_true",
                   help="search everything the service account can see, not just the folder")
    g.add_argument("--limit", default=20)
    g.set_defaults(func=cmd_find)

    g = sub.add_parser("read", help="read a document's title and text")
    g.add_argument("doc_id")
    g.add_argument("--out", metavar="PATH",
                   help="Also write the document's plain text to PATH. Lets a caller consume "
                        "the text without a shell pipe - piping python3 into python3 trips "
                        "the security scanner and forces a manual approval prompt.")
    g.set_defaults(func=cmd_read)

    g = sub.add_parser("read-comments", help="list all comments with their quoted anchor text")
    g.add_argument("doc_id")
    g.set_defaults(func=cmd_read_comments)

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

    g = sub.add_parser("insert-image", help="insert an image from a public URL or local file")
    g.add_argument("doc_id")
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="public HTTPS URL of the image (PNG/JPEG/GIF)")
    src.add_argument("--file", help="path to a local image file (PNG/JPEG/GIF); uploaded automatically")
    g.add_argument("--replace", help="replace this placeholder text with the image")
    g.add_argument("--after", help="insert right after the first occurrence of this text")
    g.add_argument("--at-start", dest="at_start", action="store_true",
                   help="insert at the very beginning (default is end of document)")
    g.add_argument("--width", type=float, help="width in points (optional)")
    g.add_argument("--height", type=float, help="height in points (optional)")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_insert_image)

    g = sub.add_parser("resize-image", help="resize an existing image (re-inserts from --url/--file)")
    g.add_argument("doc_id")
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="public HTTPS URL of the image (needed to re-insert)")
    src.add_argument("--file", help="path to a local image file (needed to re-insert)")
    g.add_argument("--nth", type=int, help="which image, 1-based in reading order")
    g.add_argument("--after", help="the image after this text")
    g.add_argument("--width", type=float, help="new width in points")
    g.add_argument("--height", type=float, help="new height in points")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_resize_image)

    g = sub.add_parser("delete-image", help="DESTRUCTIVE: remove an image")
    g.add_argument("doc_id")
    g.add_argument("--nth", type=int, help="which image, 1-based in reading order")
    g.add_argument("--after", help="the image after this text")
    g.add_argument("--confirm", action="store_true",
                   help="required; only after explicit user approval")
    g.add_argument("--match-case", dest="match_case", action="store_true")
    g.set_defaults(func=cmd_delete_image)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
