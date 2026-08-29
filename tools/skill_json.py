#!/usr/bin/env python3
"""The house JSON contract, in one place, so every skill's scripts obey it identically.

CONVENTIONS.md requires each script to print exactly ONE JSON object: `{"ok": true, ...}` on
success, `{"ok": false, "error": "..."}` with exit 1 on failure. An audit found almost no
script doing this.

Why it matters more than it looks: the model has to tell success from failure by a rule it can
rely on. When one skill signals failure with a missing key, another with exit 2, and a third
with a traceback on stderr and nothing on stdout, a small model guesses - and guesses wrong in
the direction of "it worked".

Copy this file into a skill as `scripts/skill_json.py`, or import it from the repo root during
development. It is deliberately dependency-free and short enough to vendor.

    from skill_json import ok, fail, guard

    @guard                      # any uncaught exception becomes {"ok": false, ...} + exit 1
    def main():
        ...
        ok(events=[...], count=3)          # -> {"ok": true, "events": [...], "count": 3}
        # or
        fail("no calendar is connected yet")   # -> {"ok": false, "error": "..."} exit 1

Three rules the helpers enforce that hand-written code keeps getting wrong:

  1. stdout carries the JSON object and NOTHING else. Progress, warnings and debug go to
     stderr. A stray print() corrupts the contract for the caller.
  2. Failure exits 1, never 0 and never 2.
  3. An unexpected exception still produces a JSON object. Without `guard`, a crash prints a
     traceback to stderr and leaves stdout empty - the model sees no output at all and has no
     idea whether the action happened.
"""

import functools
import json
import sys
from typing import NoReturn

__all__ = ["ok", "fail", "guard", "note", "ArgumentParser"]


def _emit(payload):
    """Write exactly one compact JSON object to stdout."""
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def ok(**fields) -> NoReturn:
    """Print a success object and exit 0. Field names must speak the user's domain."""
    payload = {"ok": True}
    payload.update(fields)
    _emit(payload)
    sys.exit(0)


def fail(error, **fields) -> NoReturn:
    """Print a failure object and exit 1.

    `error` is shown to the user via the model, so write it in the user's domain: "the
    donation log isn't connected yet", not "HttpError 403 on spreadsheets.values.get". Raw
    backend exception text leaks ids, URLs and ranges into model context and drags the model
    off the domain.
    """
    payload = {"ok": False, "error": str(error)}
    payload.update(fields)
    _emit(payload)
    sys.exit(1)


def note(message):
    """Progress or warning for a human reading the logs. Never touches stdout."""
    sys.stderr.write(str(message).rstrip() + "\n")
    sys.stderr.flush()


def guard(fn):
    """Wrap main() so no failure path can escape without emitting the contract.

    Catches SystemExit(2) as well, which is how argparse reports a bad argument: it prints
    'usage: ...' to stderr and exits 2, producing no JSON at all. A small model passing
    `--value $5` instead of `--value 5` would otherwise see nothing on stdout.
    """
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
        except Exception as e:                                 # noqa: BLE001
            fail("%s: %s" % (type(e).__name__, e))
    return wrapper


class ArgumentParser:
    """argparse.ArgumentParser that reports bad input in the house format.

    Stock argparse exits 2 with usage text on stderr. Use this instead so an invalid flag is
    a normal `{"ok": false, "error": ...}` the model can read and relay.
    """

    def __new__(cls, *args, **kwargs):
        import argparse

        class _P(argparse.ArgumentParser):
            def error(self, message):
                fail(message)

        return _P(*args, **kwargs)
