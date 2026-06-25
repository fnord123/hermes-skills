"""triplib.py — trip detection, activity slate, and residency helpers.

Stdlib-only. Composes the read-only `calendar` skill by invoking its scripts
as subprocesses and reading their JSON, so all the iCal/feed-merge complexity
stays in one place. Used by gina-where.py, pallo-trip-plan.py, and
pallo-trip-status.py.

Trip detection (resolved from the live Kayak feed, 2026-06-18): each Kayak
trip is one all-day umbrella event titled "<Place> Trip" (e.g. "London Trip").
The calendar skill prefixes Kayak events with "[Trip] " and splits multi-day
all-day events into one per day, so the umbrella's own per-day events give the
trip's date span directly — no need to associate scattered flight/hotel
sub-events.

Residency (2Houses): events titled "<child> with <parent>". "<child> with
David" → user_home; "<child> with Christine" → gina_mom.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

CALENDAR_DIRS = [
    Path.home() / ".hermes" / "skills" / "calendar" / "examples",
    Path.home() / "hermes-skills" / "calendar" / "examples",
]

USER_PARENT = "David"       # Gina/Sky "with David" => at the user's house
OTHER_PARENT = "Christine"  # "with Christine" => at the kids' mother's house

TRIP_SUFFIX = " Trip"
TRIP_PREFIX = "[Trip] "
GINA_PREFIX = "[Gina] "


class CalendarError(Exception):
    pass


def _calendar_script(name: str) -> Path:
    for d in CALENDAR_DIRS:
        p = d / name
        if p.exists():
            return p
    raise CalendarError(f"calendar script {name} not found in {CALENDAR_DIRS}")


def _run_calendar(name: str, args: list[str]) -> dict:
    script = _calendar_script(name)
    # The calendar scripts are stdlib; always run with plain python3.
    try:
        out = subprocess.run(
            ["python3", str(script), *args],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise CalendarError(f"{name} timed out")
    if out.returncode != 0 and not out.stdout.strip():
        raise CalendarError(f"{name} failed: {out.stderr.strip()[:300]}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        raise CalendarError(f"{name} returned non-JSON: {out.stdout[:200]}")


def calendar_find(query: str, *, days_ahead: int, days_back: int = 0) -> dict:
    return _run_calendar("calendar-find.py", [
        "--query", query, "--days-ahead", str(days_ahead), "--days-back", str(days_back),
    ])


def calendar_range(start_iso: str, end_iso: str) -> dict:
    return _run_calendar("calendar-range.py", ["--start", start_iso, "--end", end_iso])


# ── trip detection ────────────────────────────────────────────────────────

def _strip_trip_title(title: str) -> str:
    t = title
    if t.startswith(TRIP_PREFIX):
        t = t[len(TRIP_PREFIX):]
    return t.strip()


def _is_umbrella(ev: dict) -> bool:
    if ev.get("source") != "kayak":
        return False
    return _strip_trip_title(ev.get("title", "")).endswith(TRIP_SUFFIX)


def _trip_name_from_title(title: str) -> str:
    t = _strip_trip_title(title)
    if t.endswith(TRIP_SUFFIX):
        t = t[: -len(TRIP_SUFFIX)]
    return t.strip()


def enumerate_trips(horizon_days: int = 180) -> list[dict]:
    """All upcoming Kayak trips within the horizon, one record per trip:
    {trip_name, trip_start, trip_end}. Sorted by start."""
    data = calendar_find("Trip", days_ahead=horizon_days, days_back=0)
    groups: dict[str, list[str]] = {}
    for ev in data.get("matches", []):
        if not _is_umbrella(ev):
            continue
        name = _trip_name_from_title(ev["title"])
        groups.setdefault(name, []).append(ev["start"][:10])
    trips = []
    for name, days in groups.items():
        trips.append({
            "trip_name": name,
            "trip_start": min(days),
            "trip_end": max(days),
        })
    trips.sort(key=lambda t: t["trip_start"])
    return trips


def resolve_trip_by_name(name: str, *, days_ahead: int = 400) -> dict:
    """Resolve a trip name to {status, trip_name, trip_start, trip_end}.

    status: "ok" | "ambiguous_trip" | "no_trip_found". Prefers Kayak umbrella
    events; falls back to grepping personal-calendar titles."""
    data = calendar_find(name, days_ahead=days_ahead, days_back=0)
    matches = data.get("matches", [])

    umbrellas: dict[str, list[str]] = {}
    for ev in matches:
        if _is_umbrella(ev):
            tn = _trip_name_from_title(ev["title"])
            umbrellas.setdefault(tn, []).append(ev["start"][:10])

    if umbrellas:
        if len(umbrellas) > 1:
            return {"status": "ambiguous_trip",
                    "candidates": sorted(umbrellas.keys())}
        tn, days = next(iter(umbrellas.items()))
        return {"status": "ok", "trip_name": tn,
                "trip_start": min(days), "trip_end": max(days), "source": "kayak"}

    # Fallback: any personal-calendar event whose title contains the name.
    personal = [ev for ev in matches if ev.get("source") == "personal"]
    if personal:
        days = [ev["start"][:10] for ev in personal]
        return {"status": "ok", "trip_name": name,
                "trip_start": min(days), "trip_end": max(days), "source": "personal"}

    return {"status": "no_trip_found", "trip_name": name}


# ── activity slate (§5) ───────────────────────────────────────────────────

def activity_slate(drop_off: date, pick_up: date) -> list[dict]:
    """Per §5: drop-off day = 1 afternoon Play Yard; full days = 2 Play Yard +
    1 Nature Walk; pickup day = 1 early-morning Nature Walk."""
    slate: list[dict] = []
    slate.append({"date": drop_off.isoformat(), "day_type": "drop_off",
                  "activities": [{"type": "Play Yard", "timing": "afternoon (after drop-off)"}]})
    d = drop_off + timedelta(days=1)
    while d < pick_up:
        slate.append({"date": d.isoformat(), "day_type": "full",
                      "activities": [
                          {"type": "Play Yard", "timing": "morning"},
                          {"type": "Play Yard", "timing": "afternoon"},
                          {"type": "Nature Walk", "timing": "midday"},
                      ]})
        d += timedelta(days=1)
    slate.append({"date": pick_up.isoformat(), "day_type": "pick_up",
                  "activities": [{"type": "Nature Walk", "timing": "early morning (before pickup)"}]})
    return slate


# ── residency (2Houses) ───────────────────────────────────────────────────

def gina_residency(date_iso: str, *, who: str = "Gina") -> dict:
    """Classify where `who` is on a date via the merged 2Houses feed.
    Returns {date, residency, event_title}. residency in
    {user_home, gina_mom, traveling_with_user, unknown}."""
    data = calendar_range(date_iso, date_iso)
    titles = []
    for day in data.get("days", []):
        for ev in day.get("events", []):
            if ev.get("source") != "2houses":
                continue
            raw = ev.get("title", "")
            t = raw[len(GINA_PREFIX):] if raw.startswith(GINA_PREFIX) else raw
            titles.append(t)

    who_titles = [t for t in titles if who.lower() in t.lower()]
    # "<who> with David" / traveling => user's house; "<who> with Christine" => mom
    for t in who_titles:
        low = t.lower()
        if "traveling" in low and USER_PARENT.lower() in low:
            return {"date": date_iso, "residency": "traveling_with_user", "event_title": t}
        if f"with {USER_PARENT.lower()}" in low:
            return {"date": date_iso, "residency": "user_home", "event_title": t}
        if f"with {OTHER_PARENT.lower()}" in low:
            return {"date": date_iso, "residency": "gina_mom", "event_title": t}
    if who_titles:
        return {"date": date_iso, "residency": "unknown", "event_title": who_titles[0]}
    return {"date": date_iso, "residency": "unknown", "event_title": None}
