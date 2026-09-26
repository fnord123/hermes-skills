#!/usr/bin/env bash
# Tests for terminal-pipeline-only.sh.  Run: bash ~/.hermes/hooks/test-terminal-pipeline-only.sh
#
# An allowlist is only worth having if it actually refuses things, and this one has been wrong
# in both directions already: an unanchored path admitted `python3 /tmp/evil/rx-review/rx.py`,
# and an ERE `\n` (which matches the letter n, not a newline) blocked every command containing
# "python3". Both looked like a working guard. Hence the escape cases below, and hence a real
# test file rather than a manual check nobody repeats.
#
# The first two cases are the ones that protect everyone else on this box: the hook is
# registered GLOBALLY, so it must be inert for every caller that is not an rx-review worker.

set -uo pipefail
HOOK="$(dirname "$0")/terminal-pipeline-only.sh"
DB="$HOME/.hermes/kanban/boards/rx-review/kanban.db"
OTHER="$HOME/.hermes/kanban/boards/other-board/kanban.db"
pass=0; fail=0

t() {  # description, ALLOW|BLOCK, HERMES_KANBAN_DB, command
  local d="$1" want="$2" db="$3" c="$4" out got
  out=$(python3 -c '
import json,sys; print(json.dumps({"tool_name":"terminal","tool_input":{"command":sys.argv[1]}}))' "$c" \
    | HERMES_KANBAN_DB="$db" bash "$HOOK" 2>/dev/null)
  got=BLOCK; [ -z "$out" ] && got=ALLOW
  if [ "$got" = "$want" ]; then pass=$((pass+1)); printf '  ok   %-6s %s\n' "$got" "$d"
  else fail=$((fail+1)); printf ' FAIL  %-6s (wanted %s) %s\n' "$got" "$want" "$d"; fi
}

echo "every other caller on this box is untouched"
t "no kanban env — an interactive agent"   ALLOW ""      'rm -rf /tmp/x'
t "a worker on a different board"          ALLOW "$OTHER" 'rm -rf /tmp/x'

echo
echo "the restricted board — the pipeline's own scripts"
t "web_access search"                      ALLOW "$DB" 'python3 ~/hermes-skills/web-access/scripts/web_access.py search --query "thorne super epa"'
t "web_access fetch-content --browser"             ALLOW "$DB" 'python3 /home/dputzolu/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com" --browser'
t "web_access from the other skills dir"   ALLOW "$DB" 'python3 ~/.hermes/skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'
t "browse_task"                            ALLOW "$DB" 'python3 ~/hermes-skills/browse-task/scripts/browse_task.py --task "read it"'
t "rx.py"                                  ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py labs-report'
t "rxsplit.py"                             ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rxsplit.py extract --pdf a.pdf'
# Transition cases (the old path stays permitted until the ~/.hermes/rx-review symlink is
# removed): a worker on an in-flight card still carries the old path, and a planted copy at the
# old path's shape must still be refused.
t "old-path symlink form, allowed"         ALLOW "$DB" 'python3 ~/.hermes/rx-review/rx.py labs-report'
t "planted old-path shape, refused"        BLOCK "$DB" 'python3 /tmp/evil/.hermes/rx-review/rx.py x'

echo
echo "a wrapped command is one command"
t "backslash continuation, permitted"      ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rxsplit.py extract \
    --pdf a.pdf --pages 1-4 --out /tmp/a.txt'
t "continuation cannot smuggle a second"   BLOCK "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py x \
    ; cat ~/.hermes/.env'

echo
echo "the restricted board — command chaining (only ; && and newlines) is blocked"
t "chained after a permitted script (;)"   BLOCK "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py x; cat ~/.hermes/.env'
t "chained with &&"                        BLOCK "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py x && cat ~/.hermes/.env'
t "newline smuggling"                      BLOCK "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py
cat ~/.hermes/.env'

echo
echo "the restricted board — commands not on the allowlist"
t "a general shell command"                BLOCK "$DB" 'cat ~/.hermes/.env'
t "planted dir of the right name"          BLOCK "$DB" 'python3 /tmp/evil/rx-review/rx.py'
t "planted skills dir"                     BLOCK "$DB" 'python3 /tmp/hermes-skills/web-access/scripts/web_access.py search --query x'
t "a pipeline script not on the list"      BLOCK "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rxcache.py'

echo
echo "operators the owner deliberately allows in an argument (relaxed 2026-08-06)"
t "single & in a reply argument"           ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py correct-item-slug-request "2 food & water"'
t "pipe in a reply argument"               ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py correct-item-slug-request "2 morning | evening"'
t "redirect chars in a reply argument"     ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py correct-item-slug-request "2 dose <100mg >50"'
t "dollar-paren in a reply argument"       ALLOW "$DB" 'python3 ~/hermes-skills/rx-review/scripts/rx.py correct-item-slug-request "2 $(pill)"'

echo
echo
echo "closed-book citation audit (owner ruling 2026-09-24)"
# The hook reads the card's assignee from the board DB (read-only), keyed on the dispatcher-set
# HERMES_KANBAN_TASK. These fixtures mirror that: a tasks table with assignees, in a directory
# named rx-review so the hook's board-scope check fires the same way it does in production.
FIX="$(mktemp -d)"; trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/rx-review"
DBX="$FIX/rx-review/kanban.db"
python3 - "$DBX" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("create table tasks (id text primary key, assignee text)")
con.executemany("insert into tasks values (?,?)",
                [("t_audit", "rx-audit"), ("t_research", "rx-research"), ("t_blind", None)])
con.commit()
PY

t5() {  # description, ALLOW|BLOCK, db, task-id ("" = no task env), command
  local d="$1" want="$2" db="$3" tid="$4" c="$5" out got
  # NOTE: no quotes inside ${tid:+...} — they would survive expansion into the value.
  out=$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"terminal","tool_input":{"command":sys.argv[1]}}))' "$c" \
    | env -u HERMES_KANBAN_TASK ${tid:+HERMES_KANBAN_TASK=$tid} HERMES_KANBAN_DB="$db" bash "$HOOK" 2>/dev/null)
  got=BLOCK; [ -z "$out" ] && got=ALLOW
  if [ "$got" = "$want" ]; then pass=$((pass+1)); printf '  ok   %-6s %s\n' "$got" "$d"
  else fail=$((fail+1)); printf ' FAIL  %-6s (wanted %s) %s\n' "$got" "$want" "$d"; fi
}

t5 "audit: web_access fetch-content refused"      BLOCK "$DBX" t_audit 'python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'
t5 "audit: browse_task refused"           BLOCK "$DBX" t_audit 'python3 ~/hermes-skills/browse-task/scripts/browse_task.py --task "read it"'
t5 "audit: its scripts still run"         ALLOW "$DBX" t_audit 'python3 ~/hermes-skills/rx-review/scripts/verify.py merge'
t5 "audit: verify build runs too"         ALLOW "$DBX" t_audit 'python3 ~/.hermes/rx-review/verify.py build --dry-run'
t5 "audit: no pipe onto a script tail"    BLOCK "$DBX" t_audit 'python3 ~/hermes-skills/rx-review/scripts/rx.py x | cat'
t5 "audit: assignee unknown -> closed"    BLOCK "$DBX" t_blind  'python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'
t5 "audit: unknown, scripts still run"    ALLOW "$DBX" t_blind  'python3 ~/hermes-skills/rx-review/scripts/verify.py merge'
t5 "audit: task not in the board db"      BLOCK "$DBX" t_ghost  'python3 ~/hermes-skills/web-access/scripts/web_access.py search --query x'
t5 "audit: unreadable board db -> closed" BLOCK "$FIX/rx-review/missing.db" t_audit 'python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'
t5 "research: fetch-content unaffected"           ALLOW "$DBX" t_research 'python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'
t5 "research: relaxed operators kept"     ALLOW "$DBX" t_research 'python3 ~/hermes-skills/rx-review/scripts/rx.py correct-item-slug-request "2 morning | evening"'
t5 "no task env — non-worker untouched"   ALLOW "$DBX" ""       'python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url "https://x.com"'

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
