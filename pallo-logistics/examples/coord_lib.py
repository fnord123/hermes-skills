"""coord_lib.py — shared config + ledger helpers for Gina coordination.

Stdlib-only (no venv needed): the Discord side is a plain webhook POST and
the ledger is a small JSON file. Both gina-notify.py and gina-pending.py
import this.

Config lives in the single ~/.config/pallo-logistics/secrets.env the user
maintains (DISCORD_CHANNEL_WEBHOOK_URL, GINA_DISCORD_USER_ID,
SELF_DISCORD_USER_ID). The pending-coordination ledger lets the agent thread
Gina's replies back to the ask that prompted them, independent of how much
Discord history happens to be loaded in a given turn.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "pallo-logistics"
SECRETS = CONFIG_DIR / "secrets.env"
LEDGER = CONFIG_DIR / "pending-coordination.json"


def load_env(path: Path = SECRETS) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _val(env: dict[str, str], key: str) -> str | None:
    v = env.get(key, "")
    v = v.strip() if v else ""
    if not v or v.startswith("<"):
        return None
    return v


def discord_config() -> dict:
    """Return {webhook_url, gina_user_id, self_user_id} or {error: ...}."""
    env = load_env()
    webhook = _val(env, "DISCORD_CHANNEL_WEBHOOK_URL")
    gina = _val(env, "GINA_DISCORD_USER_ID")
    me = _val(env, "SELF_DISCORD_USER_ID")
    missing = [k for k, v in (
        ("DISCORD_CHANNEL_WEBHOOK_URL", webhook),
        ("GINA_DISCORD_USER_ID", gina),
        ("SELF_DISCORD_USER_ID", me),
    ) if not v]
    if missing:
        return {"error": f"missing in secrets.env: {', '.join(missing)}"}
    return {"webhook_url": webhook, "gina_user_id": gina, "self_user_id": me}


# ── ledger ──────────────────────────────────────────────────────────────────

def read_ledger() -> dict:
    if not LEDGER.exists():
        return {"pending": []}
    try:
        data = json.loads(LEDGER.read_text())
    except (json.JSONDecodeError, OSError):
        return {"pending": []}
    if "pending" not in data or not isinstance(data["pending"], list):
        return {"pending": []}
    return data


def write_ledger(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2))
    LEDGER.chmod(0o600)


def add_pending(entry: dict) -> dict:
    data = read_ledger()
    data["pending"] = [e for e in data["pending"] if e.get("id") != entry.get("id")]
    data["pending"].append(entry)
    write_ledger(data)
    return entry


def resolve_pending(entry_id: str) -> tuple[bool, dict]:
    data = read_ledger()
    before = len(data["pending"])
    data["pending"] = [e for e in data["pending"] if e.get("id") != entry_id]
    removed = len(data["pending"]) < before
    write_ledger(data)
    return removed, data
