#!/usr/bin/env python3
"""Service entrypoint: apply the non-secret deploy DEFAULTS, then run the
single process that carries both facades.

The defaults live in service.defaults.yaml beside this file (baked into
the image at build). They are *defaults*: the process env (compose
environment:/env_file) is authoritative, and a key the caller already
provided — even set empty — wins over the baked value. This is the same
precedence the old bash entrypoint documented, with the parsing in one
obvious place instead of a shell loop.

Why not an env file: the values are configuration, not credentials. The
.env suffix made this file trippable as a secret (the machine's credential
guard refused to let a git commit name it), and env-file format is only
needed for shell sourcing — the Python consumers read os.environ and
default in code.
"""

import os
import re
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = os.path.join(HERE, "service.defaults.yaml")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _apply_defaults():
    import yaml  # PyYAML: the one added dependency, used only here at boot.

    with open(DEFAULTS) as f:
        baked = yaml.safe_load(f) or {}
    for key, value in (baked.get("env") or {}).items():
        if not _KEY.match(key):
            print("run_service: ignoring non-identifier key %r in %s"
                  % (key, DEFAULTS), flush=True)
            continue
        if key in os.environ:
            continue  # caller provided this (even empty); the caller wins
        os.environ[key] = str(value)


def main():
    os.chdir(HERE)
    _apply_defaults()
    # Exact equivalent of the old `exec python3 /opt/webaccess/service.py`.
    runpy.run_path(os.path.join(HERE, "service.py"), run_name="__main__")


if __name__ == "__main__":
    main()
