#!/usr/bin/env python3
"""skill_issue_pr.py - propose a local skill as an issue plus a pull request.

One JSON object per call on stdout: {"ok": true, ...} on success,
{"ok": false, "error": "..."} with exit 1 on failure (the house JSON
contract; see the ok/fail/guard helpers below).

Verbs:
  propose  <skill-folder> [--repo o/n] [--title T] [--body B] [--draft]
           [--as-bot]
           One call opens the issue, creates the branch, commits the
           skill's files onto it, and opens the pull request against the
           issue. Refuses when a pull request for the same skill folder
           is already open (reports it in a warning field instead).
  update   <pr-number> <skill-folder> [--repo o/n] [--as-bot]
           Commits the skill folder's current files as a new commit on
           the branch behind the given open pull request.

The repo is --repo, or REPO in the config file, or the current
directory's repo. Attribution: the person's account by default (the
account the machine's repo client is already signed in as); --as-bot
files under the bot account (see the skill's README for setup).
"""

import argparse
import base64
from datetime import date
import functools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import NoReturn
import urllib.error
import urllib.parse
import urllib.request

API = "github.com"
API_V3 = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
USER_AGENT = "skill-issue-pr"
TTL_SECONDS = 55 * 60
CONFIG_PATH = Path.home() / ".config" / "skill-issue-pr" / "config.env"

_TOKEN_CACHE = {}


# ------------------------------------------------- the house JSON contract
# ok/fail/guard print exactly ONE compact JSON object on stdout and nothing
# else (progress goes to stderr). Success exits 0; failure exits 1, never 0
# and never 2; an uncaught exception still emits an object, so the model can
# always tell success from failure by a stable rule.


def _emit(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def ok(**fields) -> NoReturn:
    """Print a success object and exit 0 (field names in the user's domain)."""
    payload = {"ok": True}
    payload.update(fields)
    _emit(payload)
    sys.exit(0)


def fail(error, **fields) -> NoReturn:
    """Print a failure object and exit 1, in the user's domain (never a
    raw backend exception, which leaks ids and URLs into model context)."""
    payload = {"ok": False, "error": str(error)}
    payload.update(fields)
    _emit(payload)
    sys.exit(1)


def note(message):
    """Progress or warning for a human reading the logs. Never stdout."""
    sys.stderr.write(str(message).rstrip() + "\n")
    sys.stderr.flush()


def guard(fn):
    """Wrap main() so no failure path escapes without emitting the
    contract. Catches SystemExit(2) (argparse's bad-argument path, which
    otherwise prints usage and leaves stdout empty) and any exception."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            if e.code in (0, 1):
                raise
            fail("bad arguments - check the tools table for the exact flags")
        except KeyboardInterrupt:
            fail("interrupted")
        except Exception as e:  # noqa: BLE001
            fail("%s: %s" % (type(e).__name__, e))
    return wrapper


# ------------------------------------------------------------- config


def read_config():
    """Parse the config file into a dict ({} when it is absent)."""
    cfg = {}
    if not CONFIG_PATH.is_file():
        return cfg
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cfg[key.strip()] = value.strip()
    return cfg


def resolve_repo(flag_repo):
    """--repo, or REPO in the config, or the current directory's repo."""
    if flag_repo:
        return flag_repo
    cfg = read_config()
    if cfg.get("REPO"):
        return cfg["REPO"]
    proc = subprocess.run(["git", "remote", "get-url", "origin"],
                          capture_output=True, text=True, timeout=30)
    url = proc.stdout.strip()
    if proc.returncode == 0 and url:
        m = re.match(r"(?:https?://|git@github\.com:)?([^/]+/[^/]+?)"
                     r"(?:\.git)?$", url)
        if m:
            return m.group(1)
    fail("no repo configured: the install has no configured repo and the "
         "current directory is not one; pass --repo owner/name")


# ------------------------------------------------------------- token


def _app_config():
    """The install's app sign-in (the bot's), from the environment. The
    README's BOT_LOGIN records the bot's login name in the config file;
    the short-lived token itself comes from the app sign-in the install
    was given. Returns (app_id, inst_id, key_pem) or None when the app
    sign-in is not present."""
    app_id = os.environ.get("GH_APP_ID", "").strip()
    inst_id = os.environ.get("GH_APP_INSTALLATION_ID", "").strip()
    key_file = os.environ.get("GH_APP_KEY_FILE", "").strip()
    if not (app_id and inst_id and key_file):
        return None
    if not app_id.isdigit() or not inst_id.isdigit():
        fail("the bot account is not set up on this install: the app id "
             "and installation id must be numeric (see README.md)")
    key = Path(key_file)
    if not key.is_file():
        fail("the bot account is not set up on this install: the app key "
             "file is missing (see README.md)")
    if key.stat().st_mode & 0o077:
        fail("the bot account is not set up on this install: the app key "
             "file must not be group/world readable (chmod 600)")
    return app_id, inst_id, key.read_text()


def bot_login():
    """The bot's login name, from the config's BOT_LOGIN key, or None."""
    return read_config().get("BOT_LOGIN", "").strip() or None


def _b64url_json(obj) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _rsa_sign(message: bytes, key_pem: str) -> bytes:
    """RSASSA-PKCS1-v1_5 over SHA-256, the signature an app token needs
    (cryptography is declared in requirements.txt)."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        fail("the bot account is not set up on this install: the signing "
             "dependency is missing (run the install step in README.md)")
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())


def _jwt_sign(app_id: str, key_pem: str) -> str:
    now = int(time.time())
    header = _b64url_json({"alg": "RS256", "typ": "JWT"})
    payload = _b64url_json({"iat": now - 60, "exp": now + 600,
                            "iss": app_id})
    sig = _rsa_sign(f"{header}.{payload}".encode(), key_pem)
    token = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header}.{payload}.{token}"


def _cache_path(app_id: str) -> Path:
    d = Path(os.environ.get("GH_TOKEN_CACHE",
                            "~/.cache/skill-issue-pr")).expanduser()
    d.mkdir(parents=True, mode=0o700, exist_ok=True)
    return d / f"token-{app_id}.json"


def _http(method: str, url: str, token: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT})
    error = None
    parsed = {}
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            parsed = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        error = f"GitHub rejected the request ({e.code}): " \
            f"{detail or e.reason}"
    except urllib.error.URLError as e:
        error = f"could not reach GitHub: {e.reason}"
    if error is not None:
        fail(error)
    return parsed


def _http_get(url: str, token: str):
    return _http("GET", url, token)


def _actor_login(token: str):
    """Best-effort actor read. App tokens cannot GET /user, so the bot
    login is reported from the token's embedded app id (ghs_<app_id>_)."""
    req = urllib.request.Request(f"{API_V3}/user", headers={
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "User-Agent": USER_AGENT})
    data = None
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 - best-effort read
        pass
    if isinstance(data, dict) and data.get("login"):
        return data["login"]
    if token.startswith("ghs_"):
        return "app-" + token.split("_", 2)[1] + "[bot]"
    return None


def get_token(as_bot: bool) -> str:
    """Return the API token. Fail-closed: a bot that cannot sign in
    reports the error, never falls back to another account."""
    if as_bot:
        cfg = _app_config()
        if cfg is None:
            fail("the bot account is not set up on this install (see "
                 "README.md)")
        app_id, inst_id, key_pem = cfg
        if app_id in _TOKEN_CACHE:
            return _TOKEN_CACHE[app_id]
        cache = _cache_path(app_id)
        token = None
        if cache.is_file():
            try:
                entry = json.loads(cache.read_text())
                if (entry.get("app_id") == app_id
                        and entry.get("expires_at", 0) > time.time() + 120):
                    token = entry["token"]
            except Exception:  # noqa: BLE001 - a corrupt cache re-mints
                pass
        if not token:
            data = _http("POST",
                         f"{API_V3}/app/installations/{inst_id}/"
                         "access_tokens", _jwt_sign(app_id, key_pem),
                         body={})
            token = data.get("token") if isinstance(data, dict) else None
            if not token:
                fail("the bot account is not set up on this install: the "
                     "sign-in request failed (see README.md)")
        try:
            cache.write_text(json.dumps(
                {"app_id": app_id, "token": token,
                 "expires_at": time.time() + TTL_SECONDS}))
            os.chmod(cache, 0o600)
        except OSError:
            pass  # the cache is an optimization, not a requirement
        _TOKEN_CACHE[app_id] = token
        return token
    ambient = os.environ.get("GH_TOKEN", "").strip()
    if not ambient:
        fail("the person account is not signed in on this install (see "
             "README.md)")
    return ambient


# ------------------------------------------------------------- git


def _cred_helper_path() -> str:
    d = tempfile.gettempdir()
    return os.path.join(d, "sip-cred-helper-" + str(os.getpid()))


def git(args: list, cwd: str, token: str, actor=None) -> str:
    """Run a git step, signed in with the call's token. The credential
    helper is cached per process, so the token is embedded once."""
    helper = _cred_helper_path()
    if not os.path.isfile(helper):
        with open(helper, "w") as fh:
            fh.write(f"#!/bin/sh\nread -r _\nread -r _\n"
                     f"read -r _\nprintf 'username=git\\n'\n"
                     f"printf 'password={token}\\n'\n")
        os.chmod(helper, 0o700)
    fd, cfg = tempfile.mkstemp(prefix="sip-gitcfg-", suffix=".gitcfg")
    os.close(fd)
    with open(cfg, "w") as fh:
        fh.write('[credential "https://github.com"]\n'
                 f"\thelper = {helper}\n")
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = cfg
    if actor:
        env["GIT_AUTHOR_NAME"] = actor
        env["GIT_AUTHOR_EMAIL"] = actor + "@users.noreply.github.com"
        env["GIT_COMMITTER_NAME"] = actor
        env["GIT_COMMITTER_EMAIL"] = actor + "@users.noreply.github.com"
    proc = subprocess.run(["git", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        verb = args[0] if args else ""
        fail(f"git step failed ({verb}): {detail[-400:]}")
    return proc.stdout


def _clone(repo: str, ref: str, token: str) -> str:
    """A shallow working copy of the repo at the named ref (a full clone,
    so a same-day retry reuses the branch instead of stacking branches)."""
    url = f"https://{API}/{repo}.git"
    clone_dir = tempfile.mkdtemp(prefix="sip-clone-")
    git(["clone", "-q", "--depth", "1", "--branch", ref, url, clone_dir],
        cwd=os.getcwd(), token=token)
    return clone_dir


def _keep_for_cleanup(clone_dir: str):
    """Park a scratch clone out of the way (a move, never a delete) so a
    later sweep can trash it; the repo the skill ships into is untouched."""
    try:
        os.rename(clone_dir, clone_dir + "-kept-" + str(os.getpid()))
    except OSError:
        pass


# ------------------------------------------------------------- skill


def skill_folder(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_dir():
        fail(f"{path} is not a skill folder: the path does not exist or "
             "is not a directory")
    if not (p / "SKILL.md").is_file():
        fail(f"{path} is not a skill folder: it has no SKILL.md "
             "documentation file")
    return p


def frontmatter_fields(text: str) -> dict:
    """name: and the first description line, from a SKILL.md frontmatter."""
    out = {"name": None, "summary": None}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    in_desc = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^name:\s*(\S+)", line)
        if m:
            out["name"] = m.group(1).strip("\"'")
        if re.match(r"^description:\s*>?\s*$", line):
            in_desc = True
            continue
        if re.match(r"^\s+[A-Za-z_]+:\s", line):
            in_desc = False
            continue
        if in_desc and re.match(r"^\s+\S", line) and out["summary"] is None:
            out["summary"] = line.strip()
    return out


# ------------------------------------------------------------- verbs


def find_open_skill_pr(repo: str, token: str, skill_name: str):
    """The open pull request whose diff touches this skill folder, or
    None. (In the issues API, a pull request carries a `pull_request`.)"""
    q = urllib.parse.urlencode({"state": "open", "per_page": "100"})
    data = _http_get(f"{API_V3}/repos/{repo}/issues?{q}", token)
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict) or "pull_request" not in item:
            continue
        number = item.get("number")
        if number is None:
            continue
        try:
            files = _http_get(
                f"{API_V3}/repos/{repo}/pulls/{number}/files", token)
        except SystemExit:
            continue
        if not isinstance(files, list):
            continue
        for f in files:
            if str(f.get("filename", "")).startswith(skill_name + "/"):
                return {"pr": number,
                        "pr_url": (item["pull_request"]).get("url", "")}
    return None


def _default_branch(repo: str, token: str) -> str:
    data = _http_get(f"{API_V3}/repos/{repo}", token)
    return data.get("default_branch", "main") if isinstance(data, dict) \
        else "main"


def v_propose(args):
    folder = skill_folder(args.skill_folder)
    repo = resolve_repo(args.repo)
    token = get_token(bool(args.as_bot))
    actor = (bot_login() or _actor_login(token)) if args.as_bot else \
        _actor_login(token)
    meta = frontmatter_fields((folder / "SKILL.md").read_text())
    skill_name = meta["name"] or folder.name
    title = (args.title or "").strip() or \
        (meta["summary"] or f"{skill_name} skill")
    body = (args.body or "").strip() or \
        ("Proposed skill folder: " + skill_name)

    open_pr = find_open_skill_pr(repo, token, skill_name)
    if open_pr:
        ok(skill=skill_name,
           warning=f"a pull request for '{skill_name}' is already open "
                   f"#{open_pr['pr']} - use update with that number "
                   "instead of opening a second pair")

    display = actor or "the account this install is signed in as"
    issue = _http("POST", f"{API_V3}/repos/{repo}/issues", token,
                  body={"title": title, "body": body})
    if not issue.get("number"):
        fail("the issue was not created")
    issue_number = issue["number"]

    branch = f"sr/{skill_name}-{date.today().isoformat()}"
    base = _default_branch(repo, token)
    note(f"cloning {repo} at {base} ...")
    clone_dir = _clone(repo, base, token)
    try:
        _copy_folder(folder, clone_dir)
        git(["add", "-A", "."], cwd=clone_dir, token=token)
        git(["commit", "-q", "-m",
             f"propose: add the {folder.name} skill folder"],
            cwd=clone_dir, token=token, actor=actor)
        git(["push", "origin", "HEAD:refs/heads/" + branch],
            cwd=clone_dir, token=token)
    finally:
        _keep_for_cleanup(clone_dir)
    pr = _http("POST", f"{API_V3}/repos/{repo}/pulls", token, body={
        "title": title,
        "head": branch,
        "base": base,
        "body": ("Proposed skill folder: " + skill_name + "\n\n"
                 "Files for issue #" + str(issue_number) + ". Review the "
                 "pair top to bottom: the issue states the change, this "
                 "pull request carries the files.\n\n"
                 "Actor: " + display),
        "draft": bool(args.draft)})
    if not pr.get("number"):
        fail("the pull request was not created")
    ok(skill=skill_name, issue=issue_number,
       issue_url=issue.get("html_url", ""), pr=pr["number"],
       pr_url=pr.get("html_url", ""), branch=branch)


def v_update(args):
    folder = skill_folder(args.skill_folder)
    repo = resolve_repo(args.repo)
    token = get_token(bool(args.as_bot))
    actor = (bot_login() or _actor_login(token)) if args.as_bot else \
        _actor_login(token)
    pr_data = _http_get(f"{API_V3}/repos/{repo}/pulls/{args.pr}", token)
    if not isinstance(pr_data, dict) or not pr_data.get("number"):
        fail(f"pull request {args.pr} was not found in {repo} (or is not "
             "open there)")
    head = pr_data.get("head") or {}
    head_repo = head.get("repo") or {}
    head_name = head_repo.get("full_name") or head_repo.get("name")
    if head_name and head_name != repo:
        fail(f"pull request {args.pr} belongs to a different repository "
             f"({head_name}); update only works on pull requests in this "
             "repo")
    head_branch = head.get("ref")
    if not head_branch:
        fail(f"pull request {args.pr} has no branch to update")
    head_branch = str(head_branch)
    note(f"cloning {repo} at {head_branch} ...")
    clone_dir = _clone(repo, head_branch, token)
    try:
        _copy_folder(folder, clone_dir)
        if not git(["status", "--porcelain"], cwd=clone_dir,
                   token=token).strip():
            ok(pr=args.pr, pr_url=pr_data.get("html_url", ""),
               status="unchanged", commit=None)
        git(["add", "-A", "."], cwd=clone_dir, token=token)
        git(["commit", "-q", "-m",
             f"update: refresh the {folder.name} skill folder"],
            cwd=clone_dir, token=token, actor=actor)
        git(["push", "origin", "HEAD:refs/heads/" + head_branch],
            cwd=clone_dir, token=token)
        out_sha = git(["rev-parse", "--short", "HEAD"], cwd=clone_dir,
                      token=token)
        ok(pr=args.pr, pr_url=pr_data.get("html_url", ""),
           commit=out_sha.strip())
    finally:
        _keep_for_cleanup(clone_dir)


def _copy_folder(folder: Path, clone_dir: str):
    dst = Path(clone_dir) / folder.name
    if dst.exists():
        shutil.rmtree(str(dst))
    shutil.copytree(str(folder), str(dst))


# ------------------------------------------------------------- main


def build_parser():
    ap = argparse.ArgumentParser(prog="skill_issue_pr.py",
                                 description="propose a local skill as an "
                                             "issue plus a pull request")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("propose",
                       help="issue + branch + pull request in one call")
    p.add_argument("skill_folder")
    p.add_argument("--repo")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--as-bot", action="store_true")
    p.set_defaults(fn=v_propose)
    p = sub.add_parser("update",
                       help="commit the folder onto an open pull request")
    p.add_argument("pr", type=int)
    p.add_argument("skill_folder")
    p.add_argument("--repo")
    p.add_argument("--as-bot", action="store_true")
    p.set_defaults(fn=v_update)
    return ap


@guard
def main():
    args = build_parser().parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
