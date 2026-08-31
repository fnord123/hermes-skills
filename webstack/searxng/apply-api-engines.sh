#!/usr/bin/env bash
# Merge ~/searxng/api-engines.yml into SearXNG's settings.yml, restart, and VERIFY.
#
#   bash ~/searxng/apply-api-engines.sh
#
# Re-runnable: the managed section is replaced, never appended twice. api-engines.yml is
# the source of truth — edit that, then run this again.
#
# settings.yml is owned by uid 977 and the container drops DAC_OVERRIDE, so root inside the
# container cannot write it either. Every write goes through `docker exec -u searxng`.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/api-engines.yml"
LIVE="/etc/searxng/settings.yml"
PYBIN=/usr/local/searxng/.venv/bin/python     # the only python here with PyYAML
BEGIN="# --- BEGIN managed api engines (apply-api-engines.sh) ---"
END="# --- END managed api engines ---"

[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }

# 1. Keep only engine blocks whose placeholders have been filled in.
KEPT="$(python3 - "$SRC" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
starts = [m.start() for m in re.finditer(r"^  - name:", text, re.M)]
blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
kept, skipped = [], []
for b in blocks:
    name = re.search(r"^  - name:\s*(.+)$", b, re.M).group(1).strip()
    if "PUT_YOUR_" in b:
        skipped.append(name); continue
    # Trailing comments belong to the NEXT engine, not this one.
    lines = b.rstrip().splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()
    kept.append("\n".join(lines) + "\n")
for n in skipped: print("SKIP %s (placeholder not filled in)" % n, file=sys.stderr)
for b in kept:
    print("ADD  %s" % re.search(r"^  - name:\s*(.+)$", b, re.M).group(1).strip(), file=sys.stderr)
print("".join(kept) if kept else "NOTHING", end="")
PY
)"

if [ "$KEPT" = "NOTHING" ]; then
    echo >&2
    echo "No engine had its placeholders filled in. Edit $SRC first." >&2
    exit 1
fi

# 2. Back up (the copy carries a key, so lock it down).
STAMP="$(date +%Y%m%d-%H%M%S)"
docker exec searxng sh -c "cat $LIVE" > "$DIR/settings.yml.$STAMP.bak"
chmod 600 "$DIR/settings.yml.$STAMP.bak"
echo "backup: $DIR/settings.yml.$STAMP.bak"

# 3. Strip any previous managed section, append the current one.
CURRENT="$(docker exec searxng sh -c "cat $LIVE")"
CLEANED="$(printf '%s\n' "$CURRENT" | awk -v b="$BEGIN" -v e="$END" '
    $0 == b {skip=1} !skip {print} $0 == e {skip=0}')"
NEW="$(printf '%s\n%s\n%s\n%s' "$CLEANED" "$BEGIN" "$KEPT" "$END")"

# 4. Refuse to install anything that is not valid YAML, checked with the interpreter that
#    will actually load it.
printf '%s\n' "$NEW" | docker exec -i searxng "$PYBIN" -c '
import sys, yaml
try:
    cfg = yaml.safe_load(sys.stdin.read())
except Exception as exc:
    print("REFUSING: merged settings.yml is not valid YAML: %s" % exc, file=sys.stderr)
    raise SystemExit(1)
names = [e.get("name") for e in (cfg.get("engines") or [])]
dupes = {n for n in names if names.count(n) > 1}
if dupes:
    print("REFUSING: duplicate engine name(s): %s" % ", ".join(sorted(dupes)), file=sys.stderr)
    raise SystemExit(1)
print("engines after merge: %d" % len(names), file=sys.stderr)
' || exit 1

printf '%s\n' "$NEW" | docker exec -u searxng -i searxng sh -c "cat > $LIVE"
echo "settings.yml updated"

# 5. Restart, then wait for it to answer rather than assuming it came back.
( cd "$DIR" && docker compose restart searxng >/dev/null )
printf 'waiting for searxng '
for _ in $(seq 1 30); do
    curl -sf -m 3 'http://127.0.0.1:8888/config' >/dev/null 2>&1 && break
    printf '.'; sleep 1
done
echo

# 6. An engine that installs cleanly and then refuses every query is NOT a success.
#    An earlier version printed a note here and still exited 0, so an evening of 403s read
#    as "it worked". This exits non-zero and says where to look.
EXPECTED="$(printf '%s' "$KEPT" | sed -n 's/^  - name: *//p' | paste -sd, -)"
EXPECTED="$EXPECTED" python3 - <<'PY'
import json, os, urllib.request
from collections import Counter
expected = [e for e in os.environ.get("EXPECTED", "").split(",") if e]
cfg = json.load(urllib.request.urlopen("http://127.0.0.1:8888/config", timeout=10))
live = {e["name"]: (not e.get("disabled", False)) for e in cfg.get("engines", [])}
for n in expected:
    print("  registered: %-12s enabled=%s" % (n, live.get(n, "MISSING")))

url = ("http://127.0.0.1:8888/search?q=thorne+advanced+iron+complex"
       "&format=json&categories=general")

# A SUSPENDED engine looks exactly like a broken one: registered, enabled, answering
# nothing. SearXNG suspends after an error and the state SURVIVES A RESTART, so a bad key
# tried earlier makes the next run - with a good key - report failure. That happened, and
# the message confidently blamed the API. Distinguish the two before saying anything.
import time
SUSPEND_WAIT = 200          # a suspension window is ~180s
deadline = time.time() + SUSPEND_WAIT
while True:
    try:
        d = json.load(urllib.request.urlopen(url, timeout=45))
    except Exception as exc:
        print("  search check failed: %s" % exc); raise SystemExit(1)
    c = Counter(r.get("engine", "?") for r in d.get("results", []))
    unresponsive = {n: why for n, why in d.get("unresponsive_engines", [])}
    missing = [n for n in expected if n not in c]
    suspended = [n for n in missing if "suspend" in (unresponsive.get(n, "") or "").lower()]
    if not missing or not suspended or time.time() > deadline:
        break
    print("  %s suspended from an earlier error (%s) — waiting it out"
          % (", ".join(suspended), unresponsive.get(suspended[0], "")))
    time.sleep(20)

print("  general search answered by: %s" % dict(c))

if missing:
    print()
    if suspended:
        print("  FAILED: %s is SUSPENDED, not necessarily broken." % ", ".join(suspended))
        print("  SearXNG suspends an engine after an error and the state outlives a restart,")
        print("  so a key tried earlier can still be poisoning this check. Reason given:")
        for n in suspended:
            print("    %-12s %s" % (n, unresponsive.get(n, "")))
        print("  Wait out the window and re-run, or check the key if it repeats.")
    else:
        print("  FAILED: installed but answering nothing: %s" % ", ".join(missing))
        print("  Registered and not suspended, so the API itself refused. Look at:")
        print("    docker logs searxng 2>&1 | grep -i 'ERROR.*%s' | tail -3" % missing[0])
    raise SystemExit(1)
print()
print("  OK: %s answering" % ", ".join(expected))
PY
