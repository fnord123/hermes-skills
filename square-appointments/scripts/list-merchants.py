#!/usr/bin/env python3
"""list-merchants.py — show configured merchant aliases.

Reads ~/.config/square-appointments/merchants.json and emits a small JSON
summary the agent can relay to the user when they ask "what merchants do I
have configured?" or when the agent needs to discover valid aliases.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

DEFAULT_MERCHANTS = Path.home() / ".config" / "square-appointments" / "merchants.json"


def _load_env_value(key: str) -> str | None:
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'") or None
    return os.environ.get(key)


def main() -> int:
    path = Path(_load_env_value("MERCHANTS_FILE") or DEFAULT_MERCHANTS)
    if not path.exists():
        print(json.dumps({"error": "merchants file not found", "path": str(path)}, indent=2))
        return 1
    try:
        merchants = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": "merchants.json is not valid JSON", "detail": str(e), "path": str(path)}, indent=2))
        return 1

    out = []
    for alias, cfg in sorted(merchants.items()):
        out.append({
            "alias": alias,
            "name": cfg.get("name") or alias,
            "configured_service_id": bool(cfg.get("default_service_id")),
            "configured_booking_url": bool(cfg.get("booking_url")),
        })
    print(json.dumps({"merchants": out, "count": len(out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
