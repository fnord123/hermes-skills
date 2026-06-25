#!/usr/bin/env python3
"""pallo-trip-status.py — is Pallo's boarding set up for a trip? (read-only).

Two modes:

  Single-trip (give a trip identifier):
    python3 pallo-trip-status.py --trip-name Paris
    python3 pallo-trip-status.py --trip-start 2026-07-12 --trip-end 2026-07-14

  All-trips sweep (give nothing): enumerates every upcoming Kayak trip in the
  horizon and reports each.
    python3 pallo-trip-status.py
    python3 pallo-trip-status.py --horizon-days 120

Cross-checks the trip's drop-off/pickup window (afternoon-before / morning-
after) against Pallo's actual Gingr reservations. Date coverage is verified
against the live portal. The per-day activity slate (§5) is NOT independently
verifiable from the portal read, so the expected slate is returned for
reference rather than asserted as booked.

Single-trip status: all_set | boarding_present_dates_off | partial_coverage |
no_boarding | no_trip_found | ambiguous_trip. Sweep status:
no_trips_in_horizon | kayak_feed_unreachable | ok.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import triplib  # noqa: E402


def _fetch_stays() -> list[dict]:
    """Pallo's non-canceled stays via pallo-stays.py (which self-execs its venv).

    Includes past so a trip whose drop-off already started is still matched."""
    out = subprocess.run(
        ["python3", str(SCRIPT_DIR / "pallo-stays.py"), "--include-past"],
        capture_output=True, text=True, timeout=120,
    )
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise triplib.CalendarError(
            f"pallo-stays returned non-JSON: {(out.stdout or out.stderr)[:200]}")
    if data.get("status") != "ok":
        raise triplib.CalendarError(
            f"pallo-stays status {data.get('status')}: {data.get('reason', '')}")
    return data.get("stays", [])


def _covered_dates(stays: list[dict]) -> set[str]:
    days: set[str] = set()
    for s in stays:
        try:
            a = date.fromisoformat(s["start_date"])
            b = date.fromisoformat(s["end_date"])
        except (KeyError, ValueError):
            continue
        d = a
        while d <= b:
            days.add(d.isoformat())
            d += timedelta(days=1)
    return days


def _overlaps(s: dict, lo: date, hi: date) -> bool:
    try:
        a = date.fromisoformat(s["start_date"])
        b = date.fromisoformat(s["end_date"])
    except (KeyError, ValueError):
        return False
    return a <= hi and b >= lo


def _evaluate(trip_start: date, trip_end: date, stays: list[dict]) -> dict:
    drop_off = trip_start - timedelta(days=1)
    pick_up = trip_end + timedelta(days=1)
    window = []
    d = drop_off
    while d <= pick_up:
        window.append(d.isoformat())
        d += timedelta(days=1)

    relevant = [s for s in stays
                if s.get("status") != "Canceled" and _overlaps(s, drop_off, pick_up)]
    matched = [{
        "stay_id": s.get("stay_id"),
        "start_date": s.get("start_date"),
        "end_date": s.get("end_date"),
        "status": s.get("status"),
    } for s in relevant]

    slate = triplib.activity_slate(drop_off, pick_up)
    base = {
        "drop_off": drop_off.isoformat(),
        "pick_up": pick_up.isoformat(),
        "matched_stays": matched,
        "expected_activity_slate": slate,
        "activity_note": "Activity slate is not verifiable from the portal read; "
                         "expected slate shown for reference only.",
    }

    if not relevant:
        return {**base, "status": "no_boarding",
                "detail": f"No reservation covers {drop_off.isoformat()}..{pick_up.isoformat()}."}

    covered = _covered_dates(relevant)
    missing = [d for d in window if d not in covered]
    if not missing:
        return {**base, "status": "all_set",
                "detail": f"Boarding covers {drop_off.isoformat()}..{pick_up.isoformat()} "
                          f"(drop-off afternoon-before, pickup morning-after)."}

    boundary_only = all(d in (drop_off.isoformat(), pick_up.isoformat()) for d in missing)
    if boundary_only:
        return {**base, "status": "boarding_present_dates_off",
                "detail": f"Reservation exists but misses buffer day(s): {', '.join(missing)}. "
                          f"Expected window {drop_off.isoformat()}..{pick_up.isoformat()}."}
    return {**base, "status": "partial_coverage",
            "detail": f"Boarding present but {len(missing)} day(s) uncovered: {', '.join(missing)}."}


def _single(trip_name, trip_start, trip_end) -> dict:
    if trip_start and trip_end:
        ts, te = trip_start, trip_end
        name = trip_name or f"{ts.isoformat()}..{te.isoformat()}"
    else:
        try:
            res = triplib.resolve_trip_by_name(trip_name)
        except triplib.CalendarError as e:
            return {"status": "calendar_error", "reason": str(e)}
        if res["status"] != "ok":
            return res
        ts = date.fromisoformat(res["trip_start"])
        te = date.fromisoformat(res["trip_end"])
        name = res["trip_name"]

    try:
        stays = _fetch_stays()
    except triplib.CalendarError as e:
        return {"status": "stays_unavailable", "reason": str(e),
                "trip_name": name, "trip_start": ts.isoformat(), "trip_end": te.isoformat()}

    ev = _evaluate(ts, te, stays)
    return {"trip_name": name, "trip_start": ts.isoformat(),
            "trip_end": te.isoformat(), **ev}


def _sweep(horizon_days: int) -> dict:
    try:
        trips = triplib.enumerate_trips(horizon_days=horizon_days)
    except triplib.CalendarError as e:
        return {"status": "kayak_feed_unreachable", "reason": str(e),
                "horizon_days": horizon_days, "trips": []}
    if not trips:
        return {"status": "no_trips_in_horizon", "horizon_days": horizon_days, "trips": []}

    try:
        stays = _fetch_stays()
    except triplib.CalendarError as e:
        return {"status": "stays_unavailable", "reason": str(e),
                "horizon_days": horizon_days, "trips": []}

    results = []
    for t in trips:
        ts = date.fromisoformat(t["trip_start"])
        te = date.fromisoformat(t["trip_end"])
        ev = _evaluate(ts, te, stays)
        results.append({
            "trip_name": t["trip_name"],
            "trip_start": t["trip_start"],
            "trip_end": t["trip_end"],
            "status": ev["status"],
            "detail": ev["detail"],
            "drop_off": ev["drop_off"],
            "pick_up": ev["pick_up"],
            "matched_stays": ev["matched_stays"],
        })
    results.sort(key=lambda r: r["trip_start"])
    return {"status": "ok", "horizon_days": horizon_days, "trips": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trip-name", default=None)
    ap.add_argument("--trip-start", default=None, help="ISO date (with --trip-end).")
    ap.add_argument("--trip-end", default=None, help="ISO date (with --trip-start).")
    ap.add_argument("--horizon-days", type=int, default=180,
                    help="Sweep horizon when no trip identifier is given.")
    args = ap.parse_args()

    if args.trip_start or args.trip_end:
        if not (args.trip_start and args.trip_end):
            print(json.dumps({"status": "error",
                              "reason": "supply both --trip-start and --trip-end"}, indent=2))
            return 2
        try:
            ts = date.fromisoformat(args.trip_start)
            te = date.fromisoformat(args.trip_end)
        except ValueError:
            print(json.dumps({"status": "dates_invalid"}, indent=2))
            return 2
        if te < ts:
            print(json.dumps({"status": "dates_invalid",
                              "reason": "trip-end before trip-start"}, indent=2))
            return 2
        print(json.dumps(_single(args.trip_name, ts, te), indent=2))
        return 0

    if args.trip_name:
        print(json.dumps(_single(args.trip_name, None, None), indent=2))
        return 0

    print(json.dumps(_sweep(args.horizon_days), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
