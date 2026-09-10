#!/usr/bin/env python3
"""issue_pr.py — open a GitHub issue and a pull request against it, as a bot.

One JSON object per call on stdout: {"ok": true, ...} on success,
{"ok": false, "error": "..."} with exit 1 on failure (see skill_json).

Verbs:
  issue      open a new issue (or --list recent ones)
  comment    add a comment to an issue
  pr         open a pull request against an issue

Attribution: when GH_APP_ID, GH_APP_INSTALLATION_ID, and GH_APP_KEY_FILE are
set in the environment, every action is attributed to the app's bot
installation (the issue's author and the pull request's author become the
bot identity). The short-lived token is minted on demand and cached under
GH_TOKEN_CACHE (default ~/.cache/issue-pr) for 55 minutes. Without those
three variables, actions use the ambient credential (the GH_TOKEN
environment variable) and the response carries "as_bot": false. A
configured app whose token mint fails exits 1 rather than posting under a
different identity - a silent fallback would be a misattribution, not a
retry.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_json import fail, guard, ok  # noqa: E402

TTL_SECONDS = 55 * 60
API = "https://api.github.com"
ACCEPT = "application/vnd.github+json"


# ------------------------------------------------------------- credentials

def cache_path(app_id):
    d = Path(os.environ.get("GH_TOKEN_CACHE", "~/.cache/issue-pr")).expanduser()
    d.mkdir(parents=True, mode=0o700, exist_ok=True)
    return d / f"token-{app_id}.json"


def _app_config():
    app_id = os.environ.get("GH_APP_ID", "").strip()
    inst_id = os.environ.get("GH_APP_INSTALLATION_ID", "").strip()
    key_file = os.environ.get("GH_APP_KEY_FILE", "").strip()
    missing = [n for n, v in (("GH_APP_ID", app_id),
                              ("GH_APP_INSTALLATION_ID", inst_id),
                              ("GH_APP_KEY_FILE", key_file)) if not v]
    if missing or not (app_id.isdigit() and inst_id.isdigit()):
        return None
    key = Path(key_file)
    if not key.is_file():
        fail(f"app private key file not found: {key_file}")
    if key.stat().st_mode & 0o077:
        fail(f"app private key file must not be "
             f"group/world readable: {key_file}")
    return app_id, inst_id, key.read_text()


def _b64url_json(obj) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _rsa_sign(message: bytes, key_pem: str) -> bytes:
    """RSASSA-PKCS1-v1_5 over SHA-256, the signature an app JWT needs
    (declared in requirements.txt)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        fail("cryptography is not installed - run: python3 -m pip install "
             "-r requirements.txt (next to this script)")
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())


def _jwt_sign(app_id: str, key_pem: str) -> str:
    now = int(time.time())
    header = _b64url_json({"alg": "RS256", "typ": "JWT"})
    payload = _b64url_json({"iat": now - 60, "exp": now + 600, "iss": app_id})
    sig = _rsa_sign(f"{header}.{payload}".encode(), key_pem)
    token = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header}.{payload}.{token}"


def _http(method: str, url: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "Content-Type": "application/json",
        "User-Agent": "issue-pr-skill"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        fail(f"GitHub rejected the request ({e.code}): {detail or e.reason}")
    except urllib.error.URLError as e:
        fail(f"could not reach GitHub: {e.reason}")


def _get_json(url: str, token: str):
    """Best-effort GET: returns the parsed body, or None on any error. For
    probes whose failure must not abort the call (the identity read)."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "User-Agent": "issue-pr-skill"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return None


def _mint(app_id: str, inst_id: str, key_pem: str) -> str:
    data = _http("POST",
                 f"{API}/app/installations/{inst_id}/access_tokens",
                 _jwt_sign(app_id, key_pem), body={})
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        fail("could not obtain an app token")
    return token


_TOKEN = None


def get_token():
    """Return (token, as_bot). as_bot is True only for a minted app token."""
    global _TOKEN
    cfg = _app_config()
    if cfg is None:
        ambient = os.environ.get("GH_TOKEN", "").strip()
        if not ambient:
            fail("no credential configured: set GH_APP_ID, "
                 "GH_APP_INSTALLATION_ID, and GH_APP_KEY_FILE (bot "
                 "attribution), or GH_TOKEN (the logged-in account)")
        return ambient, False
    app_id, inst_id, key_pem = cfg
    if _TOKEN:
        return _TOKEN, True
    cache = cache_path(app_id)
    if cache.is_file():
        try:
            entry = json.loads(cache.read_text())
            if entry.get("app_id") == app_id and \
                    entry.get("expires_at", 0) > time.time() + 120:
                _TOKEN = entry["token"]
                return _TOKEN, True
        except Exception:  # noqa: BLE001 - corrupt cache is a re-mint
            pass
    _TOKEN = _mint(app_id, inst_id, key_pem)
    try:
        cache.write_text(json.dumps({"app_id": app_id, "token": _TOKEN,
                                     "expires_at": time.time() + TTL_SECONDS}))
        os.chmod(cache, 0o600)
    except OSError:
        pass  # the cache is an optimization, not a requirement
    return _TOKEN, True


def _whoami(token: str):
    """Best-effort actor read for the response. App tokens cannot GET /user,
    so the bot login is reported from the token's embedded app id
    (ghs_<app_id>_...)."""
    data = _get_json(f"{API}/user", token)
    if isinstance(data, dict) and data.get("login"):
        return data["login"]
    if token.startswith("ghs_"):
        return f"app-{token.split('_', 2)[1]}[bot]"
    return None


def _gh(method: str, path: str, token: str, body=None, query=None):
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return _http(method, url, token, body)


def _ref_tip(repo: str, ref: str, token: str) -> str:
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/"):]
    data = _get_json(f"{API}/repos/{repo}/git/ref/heads/{ref}", token)
    obj = data.get("object") if isinstance(data, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not sha:
        fail(f"branch {ref!r} was not found on the remote")
    return sha


# ------------------------------------------------------------------ verbs

def v_issue(args):
    token, as_bot = get_token()
    if args.list:
        data = _gh("GET", f"/repos/{args.repo}/issues", token,
                   query={"state": "open", "sort": "created",
                          "direction": "desc", "per_page": 20})
        items = [{"issue": i["number"], "title": i["title"],
                  "author": (i.get("user") or {}).get("login"),
                  "created": i["created_at"]}
                 for i in data if "pull_request" not in i][:20]
        ok(repo=args.repo, issues=items, as_bot=as_bot)
        return
    body = (args.body or "").strip()
    if not body and args.body_file:
        body = Path(args.body_file).read_text().strip()
    if not body:
        fail("issue body is empty - pass --body or --body-file")
    data = _gh("POST", f"/repos/{args.repo}/issues", token,
               body={"title": args.title, "body": body})
    if not data.get("html_url"):
        fail("issue was not created")
    actor = _whoami(token) if as_bot else None
    ok(repo=args.repo, issue=data["number"], url=data["html_url"],
       title=data["title"], as_bot=as_bot,
       **({"actor": actor} if actor else {}))


def v_comment(args):
    token, as_bot = get_token()
    text = (args.text or "").strip()
    if not text and args.file:
        text = Path(args.file).read_text().strip()
    if not text:
        fail("comment text is empty - pass --text or --file")
    data = _gh("POST", f"/repos/{args.repo}/issues/{args.issue}/comments",
               token, body={"body": text})
    if not data.get("html_url"):
        fail("comment was not posted")
    ok(repo=args.repo, issue=args.issue, url=data["html_url"], as_bot=as_bot)


def v_pr(args):
    token, as_bot = get_token()
    branch = args.branch
    if not branch:
        data = _gh("GET", f"/repos/{args.repo}/branches", token,
                   query={"per_page": 100})
        cands = [b["name"] for b in data if b["name"].startswith("issue-pr/")]
        if len(cands) == 1:
            branch = cands[0]
        elif not cands:
            fail("no branch to open a pull request from - pass --branch")
        else:
            fail("several issue-pr branches exist - pass --branch "
                 "explicitly: " + ", ".join(sorted(cands)[:5]))
    _ref_tip(args.repo, branch, token)
    rdata = _gh("GET", f"/repos/{args.repo}", token)
    base = rdata.get("default_branch", "main")
    body = (args.body or "").strip()
    if not body and args.body_file:
        body = Path(args.body_file).read_text().strip()
    if not body:
        body = (f"Addresses issue #{args.issue}.\n\n"
                "Opened with the issue-pr skill.")
    if re.search(rf"(?im)^\s*(closes|fixes|resolves)\s*#\s*{args.issue}\b",
                 body):
        fail(f"the pull request body must not auto-close issue #{args.issue} "
             "(the issue closes when the pull request is merged)")
    data = _gh("POST", f"/repos/{args.repo}/pulls", token,
               body={"title": args.title, "head": branch, "base": base,
                     "body": body})
    if not data.get("html_url"):
        fail("pull request was not created")
    actor = _whoami(token) if as_bot else None
    ok(repo=args.repo, pr=data["number"], url=data["html_url"],
       title=data["title"], issue=args.issue, head=branch, base=base,
       as_bot=as_bot, **({"actor": actor} if actor else {}))


# ------------------------------------------------------------------- main

@guard
def main():
    ap = argparse.ArgumentParser(prog="issue_pr.py")
    ap.add_argument("--repo", required=True, help="owner/name")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("issue", help="open a new issue (or --list recent ones)")
    p.add_argument("--title", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--list", action="store_true")
    p.set_defaults(fn=v_issue)

    p = sub.add_parser("comment", help="add a comment to an issue")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--text")
    p.add_argument("--file")
    p.set_defaults(fn=v_comment)

    p = sub.add_parser("pr", help="open a pull request against an issue")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--branch")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.set_defaults(fn=v_pr)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
