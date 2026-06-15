"""ical_lib.py — small, stdlib-only iCalendar parser for the schedule skill.

The scope of this library is deliberately narrow: fetch an iCal feed by URL,
parse it into events, expand recurring events over a bounded date window, and
return a clean list. No third-party dependencies — only Python stdlib +
`zoneinfo`.

Originally lifted from `~/daily-briefing/fetch-calendar.sh`, which has been
the production iCal parser for the user's morning-briefing pipeline since
May 2026. Carved into a module so the schedule-skill scripts can share it
without re-implementing the RFC 5545 corner cases each.

What it handles:
- RFC 5545 line unfolding
- DTSTART / DTEND with `VALUE=DATE` (all-day) and `TZID=…` (zoned datetime)
- Multi-day all-day events (DTEND is exclusive)
- RRULE for FREQ=DAILY, WEEKLY, MONTHLY, YEARLY, with INTERVAL, BYDAY,
  BYMONTHDAY, COUNT, UNTIL, WKST
- EXDATE exclusions
- RECURRENCE-ID exception instances (the modified occurrence replaces the
  synthesised one)
- STATUS:CANCELLED filtering
- Optional organizer → display-name mapping (mirrors daily-briefing's
  calendar-people.json)

What it does NOT handle:
- VTODO, VJOURNAL, VFREEBUSY components — only VEVENT.
- BYSETPOS, BYMONTH, BYWEEKNO, BYHOUR, BYMINUTE, BYSECOND, BYYEARDAY in
  RRULE — uncommon for Google Calendar exports of personal calendars.
- VALARM (we don't surface reminders).
- Attachments and complex properties beyond SUMMARY, LOCATION, ORGANIZER,
  DESCRIPTION.

If you find your feed has corner cases this misses, the right fix is to
extend the parser rather than to install `icalendar` + `recurring-ical-events`
— the user's hermes-skills repo philosophy is to keep skills stdlib-friendly
so they install cleanly even in EXTERNALLY-MANAGED Python environments.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

DAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


# ────────────────────────────────────────────────────────────────────────────
# Public dataclass + entrypoint
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class Event:
    """A single calendar event occurrence, normalised for agent consumption."""
    title: str
    start: str                     # ISO-8601; date for all-day, datetime for timed
    end: str | None                # ISO-8601 or None if unknown
    all_day: bool
    location: str | None
    organizer: str | None
    description: str | None
    day_label: str | None          # e.g. "Day 2 of 3" for multi-day all-day events

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_and_parse(
    ical_url: str,
    tz: ZoneInfo,
    *,
    min_date: date,
    max_date: date,
    people_file: str | Path | None = None,
    timeout_seconds: int = 30,
) -> list[Event]:
    """Fetch the iCal feed and return a list of Event occurrences whose date
    falls within [min_date, max_date] inclusive.

    `tz` is the local timezone used to convert UTC/Z and TZID datetimes for
    display. `people_file`, if given, is a JSON document of the form
        {"default": "Someone", "organizers": {"email@x.com": "Name", ...}}
    used to derive the organizer name.

    Events are returned sorted by start time.
    """
    text = _fetch(ical_url, timeout_seconds)
    return parse(text, tz, min_date=min_date, max_date=max_date, people_file=people_file)


def parse(
    ical_text: str,
    tz: ZoneInfo,
    *,
    min_date: date,
    max_date: date,
    people_file: str | Path | None = None,
) -> list[Event]:
    """Like `fetch_and_parse` but operates on pre-fetched iCal text. Useful
    for tests."""
    people_map, people_default = _load_people(people_file)

    unfolded = _unfold(ical_text)
    raw_events = _collect_vevents(unfolded)

    base_events, exceptions, simple_events = _classify(raw_events)

    output: list[Event] = []

    for ev in simple_events:
        output.extend(_emit(ev, tz, min_date, max_date, people_map, people_default))

    for uid, ev in base_events.items():
        dtstart_kp, dtstart_val = ev["DTSTART"]
        dtstart_dt, _ = _parse_date_val(dtstart_kp, dtstart_val, tz)
        if dtstart_dt is None:
            continue
        rrule_str = ev["RRULE"][1]

        exdates: set[date] = set()
        for exkp, exval in ev.get("EXDATE_LIST", []):
            for expart in exval.split(","):
                exdt, _ = _parse_date_val(exkp, expart.strip(), tz)
                if exdt is not None:
                    exdates.add(_to_date(exdt))

        exc_by_date: dict[date, dict] = {}
        for exc in exceptions.get(uid, []):
            rec_kp, rec_val = exc.get("RECURRENCE-ID", ("", ""))
            rec_dt, _ = _parse_date_val(rec_kp, rec_val, tz)
            if rec_dt is not None:
                exc_by_date[_to_date(rec_dt)] = exc

        for occ_date in _rrule_dates(dtstart_dt, rrule_str, min_date, max_date, exdates):
            if occ_date in exc_by_date:
                output.extend(_emit(exc_by_date[occ_date], tz, min_date, max_date,
                                    people_map, people_default))
            else:
                fake = _shift_to_occurrence(ev, dtstart_kp, dtstart_dt, occ_date, tz)
                output.extend(_emit(fake, tz, min_date, max_date, people_map, people_default))

    output.sort(key=lambda e: (e.start, e.title))
    return output


# ────────────────────────────────────────────────────────────────────────────
# Internals — fetch + line-level parsing
# ────────────────────────────────────────────────────────────────────────────


def _fetch(url: str, timeout: int) -> str:
    """Pull the iCal feed. Caller's responsibility to handle SystemExit."""
    req = urllib.request.Request(url, headers={"User-Agent": "schedule-skill/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"iCal fetch HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise SystemExit(f"iCal fetch network error: {e}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: continuation lines start with space or tab."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    out: list[str] = []
    for line in lines:
        if line and line[0] in (" ", "\t"):
            if out:
                out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _collect_vevents(unfolded: list[str]) -> list[dict]:
    """Pass 1: gather raw VEVENT blocks as dicts of key → (key_part, value).
    EXDATEs accumulate into an EXDATE_LIST entry because there may be many."""
    raw: list[dict] = []
    in_event = False
    current: dict = {}
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current = {}
            in_event = True
        elif line == "END:VEVENT" and in_event:
            in_event = False
            raw.append(current)
        elif in_event and ":" in line:
            key_part, _, value = line.partition(":")
            key = key_part.split(";")[0]
            if key == "EXDATE":
                current.setdefault("EXDATE_LIST", []).append((key_part, value))
            else:
                current[key] = (key_part, value)
    return raw


def _classify(raw_events: list[dict]) -> tuple[dict, dict, list]:
    """Split into base-recurring, exception instances, and simple events.
    Skips CANCELLED entries."""
    base_events: dict = {}
    exceptions: defaultdict = defaultdict(list)
    simple: list = []
    for ev in raw_events:
        if ev.get("STATUS", ("", ""))[1].upper() == "CANCELLED":
            continue
        uid = ev.get("UID", ("", ""))[1]
        if "RECURRENCE-ID" in ev:
            exceptions[uid].append(ev)
        elif "RRULE" in ev:
            base_events[uid] = ev
        else:
            simple.append(ev)
    return base_events, exceptions, simple


# ────────────────────────────────────────────────────────────────────────────
# Date / datetime parsing
# ────────────────────────────────────────────────────────────────────────────


def _parse_date_val(
    key_part: str, val: str, tz: ZoneInfo
) -> tuple[date | datetime | None, bool]:
    """Parse iCal date/datetime value. Returns (value, is_allday).

    `is_allday` is True for VALUE=DATE / YYYYMMDD; False for datetime values.
    Datetime values are converted to the supplied local `tz` so callers can
    compare them with locally-anchored ranges."""
    if "VALUE=DATE" in key_part or (len(val) == 8 and val.isdigit()):
        try:
            return datetime.strptime(val, "%Y%m%d").date(), True
        except ValueError:
            return None, False
    if val.endswith("Z"):
        try:
            dt = datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return dt.astimezone(tz), False
        except ValueError:
            return None, False
    tzid = None
    for part in key_part.split(";"):
        if part.startswith("TZID="):
            tzid = part[5:]
    try:
        dt = datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    if tzid:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(tz)
        except Exception:
            dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.replace(tzinfo=tz)
    return dt, False


def _to_date(val) -> date | None:
    """Project a parsed value down to a plain `date` for set membership."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _unescape(s: str) -> str:
    return (s.replace("\\,", ",")
             .replace("\\;", ";")
             .replace("\\n", " ")
             .replace("\\\\", "\\")
             .strip())


# ────────────────────────────────────────────────────────────────────────────
# RRULE expansion
# ────────────────────────────────────────────────────────────────────────────


def _rrule_dates(
    dtstart_val,
    rrule_str: str,
    min_d: date,
    max_d: date,
    exdates: set[date],
) -> Iterator[date]:
    """Yield occurrence dates in [min_d, max_d] for the given RRULE.
    Supports DAILY, WEEKLY, MONTHLY, YEARLY with INTERVAL, BYDAY, BYMONTHDAY,
    COUNT, UNTIL, WKST. Caller is expected to apply EXDATE and RECURRENCE-ID
    exception logic on top of this stream."""
    base = _to_date(dtstart_val)
    if base is None or base > max_d:
        return

    params: dict[str, str] = {}
    for part in rrule_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v

    freq      = params.get("FREQ", "")
    interval  = int(params.get("INTERVAL", 1))
    count_lim = int(params["COUNT"]) if "COUNT" in params else None
    wkst_str  = params.get("WKST", "MO")
    wkst_day  = DAY_MAP.get(wkst_str, 0)

    until: date | None = None
    if "UNTIL" in params:
        u = params["UNTIL"]
        try:
            until = datetime.strptime(u[:8], "%Y%m%d").date()
        except ValueError:
            pass

    end_d = min(max_d, until) if until else max_d
    if end_d < min_d:
        return

    byday_wd: set[int] = set()
    if "BYDAY" in params:
        for token in params["BYDAY"].split(","):
            day_code = token[-2:]
            if day_code in DAY_MAP:
                byday_wd.add(DAY_MAP[day_code])

    seen = 0  # counts occurrences for COUNT regardless of whether they're in [min, max]

    def emit(d: date) -> bool:
        nonlocal seen
        if d in exdates:
            return False
        if d >= min_d:
            seen += 1
            return True
        seen += 1
        return False

    if freq == "DAILY":
        d = base
        while d <= end_d:
            if count_lim and seen >= count_lim:
                break
            if emit(d):
                yield d
            d += timedelta(days=interval)
        return

    if freq == "WEEKLY":
        if not byday_wd:
            byday_wd = {base.weekday()}
        days_from_wkst = (base.weekday() - wkst_day) % 7
        week_epoch = base - timedelta(days=days_from_wkst)
        d = max(min_d, base)
        weeks_from_epoch = (d - week_epoch).days // 7
        aligned_week = weeks_from_epoch - (weeks_from_epoch % interval)
        d = week_epoch + timedelta(weeks=aligned_week)
        while d <= end_d:
            if count_lim and seen >= count_lim:
                break
            for offset in range(7 * interval):
                candidate = d + timedelta(days=offset)
                if candidate < base:
                    continue
                if candidate > end_d:
                    break
                if candidate.weekday() in byday_wd:
                    if count_lim and seen >= count_lim:
                        break
                    if emit(candidate):
                        yield candidate
            d += timedelta(weeks=interval)
        return

    if freq == "MONTHLY":
        bymonthday = [int(x) for x in params.get("BYMONTHDAY", "").split(",") if x]
        if not bymonthday and not byday_wd:
            bymonthday = [base.day]
        cur_year, cur_month = base.year, base.month
        while True:
            if count_lim and seen >= count_lim:
                break
            months_diff = (cur_year - base.year) * 12 + (cur_month - base.month)
            if months_diff % interval == 0:
                if bymonthday:
                    for dy in bymonthday:
                        try:
                            candidate = date(cur_year, cur_month, dy)
                        except ValueError:
                            continue
                        if candidate < base or candidate > end_d:
                            continue
                        if count_lim and seen >= count_lim:
                            break
                        if emit(candidate):
                            yield candidate
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1
            if date(cur_year, cur_month, 1) > end_d:
                break
        return

    if freq == "YEARLY":
        y = base.year
        while True:
            if count_lim and seen >= count_lim:
                break
            try:
                candidate = base.replace(year=y)
            except ValueError:
                candidate = base.replace(year=y, day=28)
            if candidate > end_d:
                break
            if candidate >= base:
                if emit(candidate):
                    yield candidate
            y += interval
        return


# ────────────────────────────────────────────────────────────────────────────
# Event emission
# ────────────────────────────────────────────────────────────────────────────


def _load_people(people_file: str | Path | None) -> tuple[dict[str, str], str | None]:
    if not people_file:
        return {}, None
    p = Path(people_file)
    if not p.exists():
        return {}, None
    try:
        cfg = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}, None
    organizers = cfg.get("organizers") or {}
    return {k.lower(): v for k, v in organizers.items()}, cfg.get("default")


def _organizer_name(ev: dict, people_map: dict[str, str], default: str | None) -> str | None:
    if "ORGANIZER" not in ev:
        return default
    _, val = ev["ORGANIZER"]
    email = val.replace("mailto:", "").strip().lower()
    return people_map.get(email, default)


def _shift_to_occurrence(
    base_ev: dict,
    dtstart_kp: str,
    dtstart_dt,
    occ_date: date,
    tz: ZoneInfo,
) -> dict:
    """Build a synthetic VEVENT dict for an RRULE-expanded occurrence. Keeps
    DTEND duration when DTSTART/DTEND are datetimes."""
    fake = dict(base_ev)
    if isinstance(dtstart_dt, datetime):
        occ_dt = dtstart_dt.replace(year=occ_date.year, month=occ_date.month, day=occ_date.day)
        fake["DTSTART"] = (dtstart_kp, occ_dt.strftime("%Y%m%dT%H%M%S"))
        if "DTEND" in base_ev:
            end_dt, end_allday = _parse_date_val(*base_ev["DTEND"], tz)
            if end_dt and isinstance(end_dt, datetime) and not end_allday:
                duration = end_dt - dtstart_dt
                occ_end = occ_dt + duration
                fake["DTEND"] = (base_ev["DTEND"][0], occ_end.strftime("%Y%m%dT%H%M%S"))
    else:
        fake["DTSTART"] = (dtstart_kp, occ_date.strftime("%Y%m%d"))
    return fake


def _emit(
    ev: dict,
    tz: ZoneInfo,
    min_date: date,
    max_date: date,
    people_map: dict[str, str],
    people_default: str | None,
) -> list[Event]:
    """Turn one VEVENT dict (possibly multi-day all-day) into Event records.
    Filters by [min_date, max_date]."""
    kp, val = ev.get("DTSTART", ("", ""))
    dt, is_allday = _parse_date_val(kp, val, tz)
    if dt is None:
        return []
    ev_date = _to_date(dt)
    if ev_date is None:
        return []

    summary = _unescape(ev.get("SUMMARY", ("", "Untitled"))[1])
    location = _unescape(ev.get("LOCATION", ("", ""))[1]) or None
    description = _unescape(ev.get("DESCRIPTION", ("", ""))[1]) or None
    organizer = _organizer_name(ev, people_map, people_default)

    dtend = None
    if "DTEND" in ev:
        dtend, _ = _parse_date_val(*ev["DTEND"], tz)

    # Multi-day all-day events: DTEND is exclusive, so a 3-day all-day event
    # has DTEND = DTSTART + 3 days. Emit one Event per included day.
    if (is_allday and isinstance(dtend, date) and not isinstance(dtend, datetime)):
        total_days = (dtend - ev_date).days
        if total_days > 1:
            out: list[Event] = []
            for offset in range(total_days):
                day = ev_date + timedelta(days=offset)
                if not (min_date <= day <= max_date):
                    continue
                out.append(Event(
                    title=summary,
                    start=day.isoformat(),
                    end=(dtend.isoformat() if dtend else None),
                    all_day=True,
                    location=location,
                    organizer=organizer,
                    description=description,
                    day_label=f"Day {offset + 1} of {total_days}",
                ))
            return out

    if not (min_date <= ev_date <= max_date):
        return []

    start_iso = dt.isoformat() if isinstance(dt, datetime) else ev_date.isoformat()
    end_iso = None
    if dtend is not None:
        end_iso = dtend.isoformat() if isinstance(dtend, datetime) else dtend.isoformat()
    return [Event(
        title=summary,
        start=start_iso,
        end=end_iso,
        all_day=is_allday,
        location=location,
        organizer=organizer,
        description=description,
        day_label=None,
    )]


# ────────────────────────────────────────────────────────────────────────────
# Small helpers used by the CLI scripts
# ────────────────────────────────────────────────────────────────────────────


def load_env(path: Path) -> dict[str, str]:
    """Tiny .env loader (no python-dotenv dep). Comments + KEY=VALUE only."""
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


def env_value(env: dict[str, str], key: str) -> str | None:
    v = env.get(key) or os.environ.get(key)
    v = v.strip() if v else ""
    return v or None


def resolve_tz(env: dict[str, str], default: str = "America/New_York") -> ZoneInfo:
    """Resolve the timezone the scripts should anchor their queries to.
    Prefers SCHEDULE_TZ, falls back to BRIEFING_TZ (so users who already
    configured daily-briefing get the same anchor), then to `default`."""
    name = env_value(env, "SCHEDULE_TZ") or env_value(env, "BRIEFING_TZ") or default
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(default)


def emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
