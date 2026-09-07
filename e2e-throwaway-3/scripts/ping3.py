#!/usr/bin/env python3
"""e2e-throwaway-3 — answer one short ping with the time.

Verbs (each prints ONE JSON object on stdout; exit 1 on error):
  ping   --greeting "<text>"   answer a short ping with the time
"""
import datetime
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from skill_json import ArgumentParser  # noqa: E402
from skill_json import fail  # noqa: E402
from skill_json import guard  # noqa: E402
from skill_json import ok  # noqa: E402


def cmd_ping(args) -> None:
    greeting = args.greeting.strip()
    if not greeting:
        fail("the greeting is empty")
    if len(greeting) > 80:
        fail("a greeting is one short line (80 characters or fewer)")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok(answer=greeting, time=now)


@guard
def main() -> None:
    parser = ArgumentParser(
        prog="ping3.py",
        description="Answer one short ping with the time.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_ping = subs.add_parser("ping", help="answer a short ping")
    p_ping.add_argument(
        "--greeting",
        required=True,
        help="the greeting text (one short line)",
    )
    p_ping.set_defaults(fn=cmd_ping)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
