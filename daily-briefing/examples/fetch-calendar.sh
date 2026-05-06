#!/usr/bin/env bash
# Fetches Google Calendar events from the iCal secret address.
# Normal mode: outputs today's events as a clean bullet list for the morning briefing.
# Test mode:   outputs events across the next N days, grouped by date.
#
# Usage:
#   fetch-calendar.sh              # today only (default, used by heartbeat)
#   fetch-calendar.sh --days=3     # next 3 days, grouped, for testing
#
# Requires: GCAL_ICAL_KEY env var, python3 (stdlib only)
# Person attribution: calendar-people.json (maps organizer emails to names)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/tmp/daily-briefing-calendar.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] CALENDAR $*" >> "$LOG_FILE"; }

# Allow .env to be loaded if invoked directly (morning-briefing.sh exports this already)
if [[ -z "${GCAL_ICAL_KEY:-}" && -f "${SCRIPT_DIR}/.env" ]]; then
  set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

DAYS=0
for arg in "$@"; do
  case "$arg" in
    --help)
      awk 'NR==1{next} /^#/{sub(/^#[[:space:]]?/,""); print; next} /^[^#[:space:]]/{exit}' "$0"
      exit 0 ;;
    --days=*) DAYS="${arg#--days=}" ;;
    *) echo "ERROR: Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

log "=== START (days=${DAYS}) ==="

: "${GCAL_ICAL_KEY:?GCAL_ICAL_KEY is not set}"
log "GCAL_ICAL_KEY set (${#GCAL_ICAL_KEY} chars)"

ICAL_FILE=$(mktemp --suffix=.ics)
trap 'rm -f "$ICAL_FILE"' EXIT

if curl -sf "$GCAL_ICAL_KEY" -o "$ICAL_FILE" 2>/dev/null; then
  log "curl ok ical_bytes=$(wc -c < "$ICAL_FILE")"
else
  log "ERROR: curl failed"
  echo "Calendar fetch failed"
  exit 1
fi

CALENDAR_PEOPLE_JSON="${SCRIPT_DIR}/calendar-people.json"
export ICAL_FILE DAYS LOG_FILE CALENDAR_PEOPLE_JSON

python3 <<'PYEOF'
import os, sys, json
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict

LOG_FILE = os.environ.get('LOG_FILE', '/var/tmp/daily-briefing-calendar.log')
def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{ts}] CALENDAR {msg}\n')

TZ = ZoneInfo("America/Los_Angeles")
today = datetime.now(tz=TZ).date()
days = int(os.environ.get('DAYS', '0'))
date_range = {today + timedelta(d) for d in range(max(1, days))}
min_date = min(date_range)
max_date = max(date_range)

log(f'python start today={today} days={days} range={min_date}..{max_date}')

# ── Load people config ─────────────────────────────────────────────────────────

people_file = os.environ.get('CALENDAR_PEOPLE_JSON', '')
people_map = {}
people_default = None
if people_file and os.path.exists(people_file):
    with open(people_file) as f:
        cfg = json.load(f)
    people_map = {k.lower(): v for k, v in cfg.get('organizers', {}).items()}
    people_default = cfg.get('default')

def organizer_name(ev):
    """Return person name from ORGANIZER email, falling back to default."""
    if 'ORGANIZER' not in ev:
        return people_default
    kp, val = ev['ORGANIZER']
    email = val.replace('mailto:', '').strip().lower()
    if email in people_map:
        return people_map[email]
    return people_default

# ── iCal parsing ───────────────────────────────────────────────────────────────

with open(os.environ['ICAL_FILE'], encoding="utf-8", errors="replace") as f:
    data = f.read()
log(f'ical read bytes={len(data)}')

def unfold(text):
    lines = text.replace('\r\n', '\n').replace('\r', '\n').splitlines()
    out = []
    for line in lines:
        if line and line[0] in (' ', '\t'):
            if out:
                out[-1] += line[1:]
        else:
            out.append(line)
    return out

DAY_MAP = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}

def parse_date_val(key_part, val):
    """Parse iCal date/datetime value. Returns (datetime_or_date, is_allday)."""
    if 'VALUE=DATE' in key_part or (len(val) == 8 and val.isdigit()):
        try:
            return datetime.strptime(val, '%Y%m%d').date(), True
        except ValueError:
            return None, False
    if val.endswith('Z'):
        try:
            dt = datetime.strptime(val, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ), False
        except ValueError:
            return None, False
    tzid = None
    for part in key_part.split(';'):
        if part.startswith('TZID='):
            tzid = part[5:]
    try:
        dt = datetime.strptime(val[:15], '%Y%m%dT%H%M%S')
    except ValueError:
        return None, False
    if tzid:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(TZ)
        except Exception:
            dt = dt.replace(tzinfo=TZ)
    else:
        dt = dt.replace(tzinfo=TZ)
    return dt, False

def to_date(val):
    """Get just the date from a parsed dt value."""
    if val is None:
        return None
    return val if isinstance(val, date) and not isinstance(val, datetime) else val.date() if isinstance(val, datetime) else val

def unescape(s):
    return s.replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ').replace('\\\\', '\\').strip()

# ── RRULE expander ─────────────────────────────────────────────────────────────

def rrule_dates(dtstart_val, rrule_str, min_d, max_d, exdates):
    """Yield dates in [min_d, max_d] that are RRULE occurrences of dtstart_val."""
    base = to_date(dtstart_val)
    if base is None or base > max_d:
        return

    params = {}
    for part in rrule_str.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[k] = v

    freq     = params.get('FREQ', '')
    interval = int(params.get('INTERVAL', 1))
    count_lim = int(params['COUNT']) if 'COUNT' in params else None
    wkst_str  = params.get('WKST', 'MO')
    wkst_day  = DAY_MAP.get(wkst_str, 0)

    until = None
    if 'UNTIL' in params:
        u = params['UNTIL']
        try:
            until = datetime.strptime(u[:8], '%Y%m%d').date()
        except ValueError:
            pass

    end_d = min(max_d, until) if until else max_d
    if end_d < min_d:
        return

    byday_wd = set()
    if 'BYDAY' in params:
        for d in params['BYDAY'].split(','):
            dc = d[-2:]
            if dc in DAY_MAP:
                byday_wd.add(DAY_MAP[dc])

    seen = 0  # for COUNT

    def emit(d):
        nonlocal seen
        if d in exdates:
            return False
        if d >= min_d:
            seen += 1
            return True  # yield it
        seen += 1
        return False  # count it but don't yield

    if freq == 'DAILY':
        d = base
        while d <= end_d:
            if count_lim and seen >= count_lim:
                break
            if emit(d):
                yield d
            d += timedelta(days=interval)

    elif freq == 'WEEKLY':
        if not byday_wd:
            byday_wd = {base.weekday()}
        # Week epoch: the WKST-aligned week start on or before base
        days_from_wkst = (base.weekday() - wkst_day) % 7
        week_epoch = base - timedelta(days=days_from_wkst)

        d = min_d
        if d < base:
            d = base
        # Snap d back to the start of its interval-aligned week
        weeks_from_epoch = (d - week_epoch).days // 7
        # Round down to interval boundary
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

    elif freq == 'MONTHLY':
        bymonthday = [int(x) for x in params.get('BYMONTHDAY', '').split(',') if x]
        if not bymonthday and not byday_wd:
            bymonthday = [base.day]

        cur_year, cur_month = base.year, base.month
        while True:
            if count_lim and seen >= count_lim:
                break
            months_diff = (cur_year - base.year) * 12 + (cur_month - base.month)
            if months_diff % interval == 0:
                if bymonthday:
                    for day in bymonthday:
                        try:
                            candidate = date(cur_year, cur_month, day)
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

    elif freq == 'YEARLY':
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

# ── Parse all VEVENTs ──────────────────────────────────────────────────────────

unfolded = unfold(data)

# First pass: collect all VEVENTs
raw_events = []
in_event = False
current = {}
for line in unfolded:
    if line == 'BEGIN:VEVENT':
        current = {}
        in_event = True
    elif line == 'END:VEVENT' and in_event:
        in_event = False
        raw_events.append(current)
    elif in_event and ':' in line:
        key_part, _, value = line.partition(':')
        key = key_part.split(';')[0]
        # Store as (key_part, value); allow EXDATE to accumulate
        if key == 'EXDATE':
            current.setdefault('EXDATE_LIST', []).append((key_part, value))
        else:
            current[key] = (key_part, value)

# ── Separate into base events, exceptions, and simple events ──────────────────

base_events   = {}  # uid -> event dict (has RRULE)
exceptions    = defaultdict(list)  # uid -> [modified instance dicts]
simple_events = []  # no RRULE, no RECURRENCE-ID

for ev in raw_events:
    if ev.get('STATUS', ('', ''))[1].upper() == 'CANCELLED':
        continue
    uid = ev.get('UID', ('', ''))[1]
    if 'RECURRENCE-ID' in ev:
        exceptions[uid].append(ev)
    elif 'RRULE' in ev:
        # Keep the most recent version if duplicate UIDs
        base_events[uid] = ev
    else:
        simple_events.append(ev)

# ── Collect today's events ─────────────────────────────────────────────────────

# tuple: (sort_key, dtstart, is_allday, summary, location, dtend, day_label, person)
results = defaultdict(list)

def add_event(ev, dtstart_override=None):
    kp, val = dtstart_override if dtstart_override else ev.get('DTSTART', ('', ''))
    dt, is_allday = parse_date_val(kp, val)
    if dt is None:
        return
    ev_date = to_date(dt)
    summary  = unescape(ev.get('SUMMARY',  ('', 'Untitled'))[1])
    location = unescape(ev.get('LOCATION', ('', ''))[1])
    dtend    = None
    if 'DTEND' in ev:
        dtend, _ = parse_date_val(*ev['DTEND'])
    sort_key = (not is_allday, dt if not is_allday else datetime.min.replace(tzinfo=TZ))
    person   = organizer_name(ev)

    # Multi-day all-day events: DTEND is exclusive and > 1 day after DTSTART
    if is_allday and isinstance(dtend, date) and not isinstance(dtend, datetime):
        total_days = (dtend - ev_date).days
        if total_days > 1:
            for offset in range(total_days):
                day = ev_date + timedelta(days=offset)
                if day in date_range:
                    day_label = f"Day {offset + 1} of {total_days}"
                    results[day].append((sort_key, dt, True, summary, location, dtend, day_label, person))
            return

    if ev_date not in date_range:
        return
    results[ev_date].append((sort_key, dt, is_allday, summary, location, dtend, None, person))

# Simple (non-recurring) events
for ev in simple_events:
    add_event(ev)

# Recurring events: expand RRULE then apply exceptions
for uid, ev in base_events.items():
    dtstart_kp, dtstart_val = ev['DTSTART']
    dtstart_dt, _ = parse_date_val(dtstart_kp, dtstart_val)
    rrule_str = ev['RRULE'][1]

    # Collect EXDATEs
    exdates = set()
    for exkp, exval in ev.get('EXDATE_LIST', []):
        for expart in exval.split(','):
            exdt, _ = parse_date_val(exkp, expart.strip())
            if exdt:
                exdates.add(to_date(exdt))

    # Collect exception (modified) instances for this UID, keyed by their recurrence date
    exc_by_date = {}
    for exc in exceptions.get(uid, []):
        rec_kp, rec_val = exc.get('RECURRENCE-ID', ('', ''))
        rec_dt, _ = parse_date_val(rec_kp, rec_val)
        if rec_dt:
            exc_by_date[to_date(rec_dt)] = exc

    # Expand
    for occ_date in rrule_dates(dtstart_dt, rrule_str, min_date, max_date, exdates):
        if occ_date in exc_by_date:
            # Use the modified instance instead
            add_event(exc_by_date[occ_date])
        else:
            # Synthesize event data with adjusted date (keep same time)
            fake_ev = dict(ev)
            # Rebuild DTSTART with this occurrence's date
            if isinstance(dtstart_dt, datetime):
                occ_dt = dtstart_dt.replace(
                    year=occ_date.year, month=occ_date.month, day=occ_date.day
                )
                fake_ev['DTSTART'] = (dtstart_kp, occ_dt.strftime('%Y%m%dT%H%M%S'))
                if 'DTEND' in ev:
                    end_dt, end_allday = parse_date_val(*ev['DTEND'])
                    if end_dt and not end_allday:
                        duration = (end_dt.date() - dtstart_dt.date()) if isinstance(end_dt, datetime) else timedelta(0)
                        occ_end = occ_dt + (end_dt - dtstart_dt if isinstance(end_dt, datetime) else timedelta(hours=1))
                        fake_ev['DTEND'] = (ev['DTEND'][0], occ_end.strftime('%Y%m%dT%H%M%S'))
            else:
                fake_ev['DTSTART'] = (dtstart_kp, occ_date.strftime('%Y%m%d'))
            add_event(fake_ev)

# ── Format output ──────────────────────────────────────────────────────────────

def format_event(dt, is_allday, summary, location, dtend, day_label=None, person=None):
    if is_allday:
        time_str = f"{day_label} (all day)" if day_label else "All day"
    else:
        time_str = dt.strftime("%-I:%M %p")
        if dtend and isinstance(dtend, datetime):
            time_str += " - " + dtend.strftime("%-I:%M %p")
    name_prefix = f"[{person}] " if person else ""
    out = f"- {time_str}: {name_prefix}{summary}"
    if location:
        out += f" @ {location}"
    return out

total_events = sum(len(v) for v in results.values())
log(f'events_in_range={total_events} days_with_events={len(results)}')

if not results:
    log('output=no_events')
    print("No calendar events today." if days <= 1 else "No calendar events in range.")
elif days <= 1:
    items = sorted(results.get(today, []))
    if not items:
        log('output=no_events_today')
        print("No calendar events today.")
    else:
        log(f'output={len(items)}_events')
        for sort_key, dt, is_allday, summary, location, dtend, day_label, person in items:
            print(format_event(dt, is_allday, summary, location, dtend, day_label, person))
else:
    log(f'output=multi_day {len(results)}_dates')
    for d in sorted(results):
        label = d.strftime("%A, %B %-d")
        if d == today:
            label += " (today)"
        print(f"\n{label}")
        for sort_key, dt, is_allday, summary, location, dtend, day_label, person in sorted(results[d]):
            print(format_event(dt, is_allday, summary, location, dtend, day_label, person))

log('python done')
PYEOF

log "=== END ==="
