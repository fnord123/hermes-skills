#!/usr/bin/env python3
"""Hermetic battery for tools/skillpipe-auth.py — the skillpipe credential helper.

WHY THIS EXISTS. The six pipeline roles authenticate to GitHub through this
helper (git credential fill -> minted installation token). A bug in its
protocol output or fail-closed behavior would surface only mid-pipeline,
hours in, as an undiagnosable 401. The live wiring (env-scoped git config in
each role's profile .env) makes this file the single credential path, so the
protocol contract is tested here, offline.

WHAT COUNTS AS A CASE. The helper is exercised two ways: as a subprocess,
exactly as git invokes a credential helper (the protocol + fail-closed
surface), and in-process (the JWT the on-disk key signs). No case touches
the network — the mint endpoint is monkeypatched before the helper runs.

CASES.
1. (subprocess) github https request -> reply carries protocol/host,
   username=git and a token; the patched mint saw the right app+install ids.
2. (subprocess) non-github host -> fail-closed: nonzero exit, one JSON
   object, no token on stdout.
3. (subprocess) missing SKILLPIPE_* env -> fail-closed naming the var.
4. (subprocess) key file world-readable -> fail-closed (the chmod-600 rule).
5. (in-process, needs PyJWT) the on-disk key signs a JWT that verifies
   against the public half. Skipped loudly if the wheel is absent, per the
   run_tests.py convention — a missing dependency is never a pass.

    python3 tools/test_skillpipe_auth.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "skillpipe-auth.py"

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"ok    {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def load_helper():
    spec = importlib.util.spec_from_file_location("skillpipe_auth", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper from {HELPER}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_verb(verb, stdin, env):
    """Run the helper as a subprocess, exactly as git would."""
    return subprocess.run(
        [sys.executable, str(HELPER), verb],
        input=stdin, capture_output=True, text=True, env=env, timeout=60)


def base_env(tmp, key_path):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SKILLPIPE_", "GIT_CONFIG_"))}
    env["SKILLPIPE_GH_APP_ID"] = "9990001"
    env["SKILLPIPE_GH_APP_INSTALLATION_ID"] = "9990002"
    env["SKILLPIPE_GH_APP_KEY_FILE"] = str(key_path)
    env["SKILLPIPE_TOKEN_CACHE"] = str(Path(tmp) / "tokcache")
    return env


def write_key(tmp, mode=0o600, name="test-key.pem"):
    """A throwaway RSA key. Returns None if no crypto lib is available."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return None
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    path = Path(tmp) / name
    path.write_bytes(pem)
    os.chmod(path, mode)
    return path


def as_json(text):
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def main():
    tmp = tempfile.mkdtemp(prefix="skillpipe-auth-test-")
    mod = load_helper()

    key = write_key(tmp)
    if key is None:
        print("SKIP  tools/test_skillpipe_auth.py "
              "(cryptography not available; pip install cryptography)")
        sys.exit(0)

    env = base_env(tmp, key)

    # 1. happy path: monkeypatched mint, real protocol reply (in-process).
    captured = {}

    def fake_mint(app_id, inst_id, key_pem):
        captured["minted_for"] = (app_id, inst_id)
        return "ghs_9990001_testtoken"

    mod.mint_token = fake_mint
    saved = {k: os.environ.get(k) for k in
             ("SKILLPIPE_GH_APP_ID", "SKILLPIPE_GH_APP_INSTALLATION_ID",
              "SKILLPIPE_GH_APP_KEY_FILE", "SKILLPIPE_TOKEN_CACHE")}
    os.environ.update(env)
    saved_argv = sys.argv
    sys.argv = ["skillpipe-auth.py", "git-cred"]
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO("protocol=https\nhost=github.com\n\n")
    buf = io.StringIO()
    code = None
    try:
        with redirect_stdout(buf):
            try:
                mod.main()
            except SystemExit as exc:
                code = exc.code
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
        sys.argv = saved_argv
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    reply = buf.getvalue()
    lines = {l.split("=", 1)[0]: l.split("=", 1)[1]
             for l in reply.splitlines() if "=" in l}
    record("github https request replies with a token",
           code == 0 and lines.get("username") == "git"
           and lines.get("password") == "ghs_9990001_testtoken"
           and lines.get("host") == "github.com"
           and captured.get("minted_for") == ("9990001", "9990002"),
           f"exit={code} reply={reply!r}")

    # 2. fail-closed: non-github host.
    p = run_verb("git-cred", "protocol=https\nhost=example.com\n\n", env)
    obj = as_json(p.stdout.strip())
    record("non-github host fails closed",
           p.returncode != 0 and obj.get("ok") is False
           and "ghs_" not in p.stdout,
           f"exit={p.returncode} stdout={p.stdout!r}")

    # 3. fail-closed: missing env names the var.
    broken = {k: v for k, v in env.items() if k != "SKILLPIPE_GH_APP_ID"}
    p = run_verb("git-cred", "protocol=https\nhost=github.com\n\n", broken)
    obj = as_json(p.stdout.strip())
    record("missing env fails closed and names the var",
           p.returncode != 0 and "SKILLPIPE_GH_APP_ID" in str(obj.get("error")),
           f"exit={p.returncode} stdout={p.stdout!r}")

    # 4. fail-closed: key not mode 600.
    key_world = write_key(tmp, mode=0o644, name="test-key-world.pem")
    p = run_verb("git-cred", "protocol=https\nhost=github.com\n\n",
                 base_env(tmp, key_world))
    obj = as_json(p.stdout.strip())
    record("world-readable key is refused",
           p.returncode != 0 and "accessible" in str(obj.get("error")),
           f"exit={p.returncode} stdout={p.stdout!r}")

    # 5. (needs PyJWT) the on-disk key signs a JWT that verifies.
    try:
        import jwt
    except ImportError:
        print("SKIP  case 5 (PyJWT not available; the mint path is "
              "covered live by whoami)")
    else:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key)
        pub_pem = load_pem_private_key(key.read_bytes(),
                                       password=None).public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        saved5 = {k: os.environ.get(k) for k in
                  ("SKILLPIPE_GH_APP_ID", "SKILLPIPE_GH_APP_INSTALLATION_ID",
                   "SKILLPIPE_GH_APP_KEY_FILE", "SKILLPIPE_TOKEN_CACHE")}
        os.environ.update(env)
        try:
            _app, _inst, pem = mod.load_env()
            token = mod.jwt_for("9990001", pem)
        finally:
            for k, v in saved5.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        claims = jwt.decode(token, key=pub_pem, algorithms=["RS256"],
                            issuer="9990001")
        record("on-disk key signs a verifiable RS256 JWT",
               claims.get("iss") == "9990001", f"claims={claims}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
