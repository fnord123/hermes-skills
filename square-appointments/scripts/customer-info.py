#!/usr/bin/env python3
"""customer-info.py — manage the customer contact info used when booking
appointments at Square merchants.

The file is `~/.config/square-appointments/customer.json` (perms 600). The
agent invokes this script to confirm setup is in place and (rarely) to
update individual fields — it must NOT read the file directly.

Usage:
  python3 customer-info.py show
  python3 customer-info.py set --field <name> --value <val>

Fields: phone_country_code, phone, first_name, last_name, email
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_json import ok, fail, guard  # noqa: E402

CUSTOMER_FILE = Path.home() / ".config" / "square-appointments" / "customer.json"
VALID_FIELDS = {"phone_country_code", "phone", "first_name", "last_name", "email"}


def _redact_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    return f"…{digits[-4:]}" if len(digits) >= 4 else "(unset)"


def _redact_email(e: str) -> str:
    if not e or "@" not in e:
        return "(unset)"
    name, domain = e.split("@", 1)
    return f"{name[0]}…@{domain}" if name else f"…@{domain}"


def cmd_show() -> None:
    if not CUSTOMER_FILE.exists():
        ok(configured=False,
           path=str(CUSTOMER_FILE),
           hint="Run customer-info.py set --field <name> --value <val> for each of: "
                + ", ".join(sorted(VALID_FIELDS)))
        return
    data = json.loads(CUSTOMER_FILE.read_text())
    redacted = {
        "configured": True,
        "phone": _redact_phone(data.get("phone", "")),
        "phone_country_code": data.get("phone_country_code", ""),
        "first_name": data.get("first_name", "") or "(unset)",
        "last_name": data.get("last_name", "") or "(unset)",
        "email": _redact_email(data.get("email", "")),
    }
    redacted["complete"] = all(data.get(k) for k in VALID_FIELDS)
    ok(**redacted)


def cmd_set(field: str, value: str) -> None:
    if field not in VALID_FIELDS:
        fail(f"unknown field {field!r}", valid_fields=sorted(VALID_FIELDS))
        return
    CUSTOMER_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if CUSTOMER_FILE.exists():
        try:
            data = json.loads(CUSTOMER_FILE.read_text())
        except json.JSONDecodeError:
            data = {}
    if field == "phone":
        value = re.sub(r"\D", "", value)
    elif field == "phone_country_code":
        value = value.strip() if value.startswith("+") else f"+{value.lstrip('+').strip()}"
    elif field == "email":
        value = value.strip().lower()
    else:
        value = value.strip()
    data[field] = value
    CUSTOMER_FILE.write_text(json.dumps(data, indent=2))
    CUSTOMER_FILE.chmod(0o600)
    ok(updated=field, saved_to=str(CUSTOMER_FILE))


@guard
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p_set = sub.add_parser("set")
    p_set.add_argument("--field", required=True)
    p_set.add_argument("--value", required=True)
    args = ap.parse_args()
    if args.cmd == "show":
        cmd_show()
    elif args.cmd == "set":
        cmd_set(args.field, args.value)


if __name__ == "__main__":
    main()
