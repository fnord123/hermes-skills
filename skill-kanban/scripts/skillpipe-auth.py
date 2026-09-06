#!/usr/bin/env python3
"""skillpipe-auth: mint GitHub App installation tokens for skillpipe roles.

Host-neutral: every input comes from the environment, set in the role's
profile .env:

  SKILLPIPE_GH_APP_ID              numeric app id (JWT iss claim)
  SKILLPIPE_GH_APP_INSTALLATION_ID numeric installation id
  SKILLPIPE_GH_APP_KEY_FILE        path to the app's private key PEM (600)

Tokens are cached under $SKILLPIPE_TOKEN_CACHE (default
~/.cache/skillpipe) with a 55-minute TTL (GitHub tokens live 60).
Fail-closed: one JSON object on stdout, nonzero exit on any error.

Verbs:
  token              print a fresh installation token
  --export           print `export GH_TOKEN=...` (for: eval $(...))
  git-cred           git credential-helper: read a request on stdin,
                     reply with the token as the password for github.com
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import NoReturn

TTL_SECONDS = 55 * 60          # under GitHub's 60-min token life
TOKEN_HOST = "github.com"


def fail(msg: str) -> "NoReturn":  # noqa: F821 - typing.NoReturn on 3.8+
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


def load_env(require_inst: bool = True):
    app_id = os.environ.get("SKILLPIPE_GH_APP_ID", "").strip()
    inst_id = os.environ.get("SKILLPIPE_GH_APP_INSTALLATION_ID", "").strip()
    key_file = os.environ.get("SKILLPIPE_GH_APP_KEY_FILE", "").strip()
    missing = [n for n, v in (("SKILLPIPE_GH_APP_ID", app_id),
                              ("SKILLPIPE_GH_APP_KEY_FILE", key_file)) if not v]
    if require_inst and not inst_id:
        missing.append("SKILLPIPE_GH_APP_INSTALLATION_ID")
    if missing:
        fail("missing in environment: " + ", ".join(missing))
    if not app_id.isdigit() or (inst_id and not inst_id.isdigit()):
        fail("app id and installation id must be numeric")
    key = Path(key_file)
    if not key.is_file():
        fail(f"private key file not found: {key_file}")
    if key.stat().st_mode & 0o077:
        fail(f"private key file is world/other/group accessible: {key_file}"
             " (chmod 600)")
    return app_id, inst_id, key.read_text()


def _cache_path(app_id: str) -> Path:
    cache_dir = Path(os.environ.get("SKILLPIPE_TOKEN_CACHE",
                                    "~/.cache/skillpipe")).expanduser()
    cache_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    return cache_dir / f"token-{app_id}.json"


def jwt_for(app_id: str, key_pem: str) -> str:
    import jwt
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 600, "iss": app_id},
                      key_pem, algorithm="RS256")


def http_post_json(url: str, body: dict, auth: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {auth}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "skillpipe-auth"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def http_get(url: str, token: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "Accept": "application/vnd.github+json",
                                               "User-Agent": "skillpipe-auth"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def mint_token(app_id: str, inst_id: str, key_pem: str) -> str:
    token_jwt = jwt_for(app_id, key_pem)
    try:
        data = http_post_json(
            f"https://api.github.com/app/installations/{inst_id}/access_tokens",
            {}, token_jwt)
    except Exception as exc:  # noqa: BLE001
        fail(f"installation token request failed: {exc}")
    if not isinstance(data, dict) or not data.get("token"):
        fail(f"unexpected token response: {str(data)[:120]}")
    return data["token"]


def get_token() -> str:
    app_id, inst_id, key_pem = load_env()
    cache = _cache_path(app_id)
    if cache.is_file():
        try:
            entry = json.loads(cache.read_text())
            if entry.get("app_id") == app_id and \
                    entry.get("expires_at", 0) > time.time() + 120:
                return entry["token"]
        except Exception:  # noqa: BLE001 - corrupt cache is a re-mint
            pass
    token = mint_token(app_id, inst_id, key_pem)
    try:
        cache.write_text(json.dumps({"app_id": app_id, "token": token,
                                     "expires_at": time.time() + TTL_SECONDS}))
        os.chmod(cache, 0o600)
    except OSError:
        pass  # cache is an optimization, not a requirement
    return token


def git_cred() -> None:
    # git credential protocol: read key=value lines until EOF
    req = {}
    for line in sys.stdin.read().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            req[k.strip()] = v.strip()
    if req.get("protocol", "https") != "https" or \
            req.get("host") != TOKEN_HOST:
        fail("git-cred: only https://github.com is supported")
    token = get_token()
    print("protocol=https")
    print(f"host={TOKEN_HOST}")
    print("username=git")
    print(f"password={token}")
    sys.exit(0)


def discover() -> None:
    """List this app's installations (works before INSTALLATION_ID is known)."""
    app_id, _inst, key_pem = load_env(require_inst=False)
    token_jwt = jwt_for(app_id, key_pem)
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.github.com/app/installations",
            headers={"Authorization": f"Bearer {token_jwt}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "skillpipe-auth"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        fail(f"discovery request failed: {exc}")
    insts = data if isinstance(data, list) else data.get("installations", [])
    out = [{"installation_id": i["id"], "app_id": i.get("app_id"),
            "app_slug": i.get("app_slug"),
            "account": i.get("account", {}).get("login"),
            "repository_selection": i.get("repository_selection"),
            "repositories_url": i.get("repositories_url")}
           for i in insts]
    print(json.dumps({"ok": True, "installations": out}, indent=2))
    sys.exit(0)


def whoami() -> None:
    """Mint (or reuse) a token and prove what identity/repo it acts as.

    Installation tokens cannot call GET /user (GitHub returns 403 'Resource
    not accessible by integration'), so identity is proven by a repo-scoped
    read plus the app id embedded in the token prefix (ghs_<app_id>_).
    """
    token = get_token()
    # app id is baked into the token: ghs_<app_id>_<payload>
    app_id_from_tok = token.split("_", 2)[1] if token.startswith("ghs_") else "?"
    # prove repo access on the installed repo (the one the app was installed on)
    repo = os.environ.get("SKILLPIPE_PROBE_REPO", "fnord123/hermes-skills")
    try:
        r = http_get(f"https://api.github.com/repos/{repo}", token)
        probe = {"repo": r.get("full_name"),
                 "repo_access": "ok",
                 "private": r.get("private")}
    except Exception as exc:  # noqa: BLE001
        probe = {"repo": repo, "repo_access": f"failed: {exc}"}
    print(json.dumps({"ok": True, "acts_as": f"ghs_{app_id_from_tok}",
                      "app_id": app_id_from_tok, "token_prefix": token[:9] + "...",
                      **probe}))
    sys.exit(0)


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "git-cred":
        git_cred()
    if argv and argv[0] == "discover":
        discover()
    if argv and argv[0] == "whoami":
        whoami()
    token = get_token()
    if argv and argv[0] == "--export":
        print(f"export GH_TOKEN={token}")
    else:
        print(token)
    sys.exit(0)


if __name__ == "__main__":
    main()
