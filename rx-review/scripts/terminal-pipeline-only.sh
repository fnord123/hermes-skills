#!/usr/bin/env bash
# Restrict ONE kanban board's `terminal` to this pipeline's own named scripts.
#
# WHY. rx-research holds only the `web` toolset - web_search and web_extract - so when a page
# is rendered by JavaScript or guarded by a bot wall, it has nothing that can read it. On
# 2026-07-31 a product lookup blocked reporting exactly that: "Thorne's site is JS-rendered,
# Walmart blocks with CAPTCHA, Amazon returns minimal content." The browse-task skill can read
# all three, but running it needs `terminal`, and `terminal` is a general shell.
#
# This hook is what makes granting it acceptable: an allowlist of the pipeline's OWN scripts,
# enforced before the call runs, so the board gains its tools and nothing else. The list is
# derived from what the card bodies actually instruct workers to run - rx.py 16 times,
# verify.py 5, fanout.py 3, rxsplit.py and lenses.py twice each - plus the browse-task script
# that gives a research worker a way to read a JS-rendered or bot-walled page.
#
# An allowlist of named scripts beats singling out one skill: it is the boundary the pipeline
# already has in practice, and it blocks nothing that ran legitimately all day. Everything NOT
# on this board keeps the ordinary global protection - block-secret-reads.sh still applies to
# every caller, this hook simply returns.
#
# CLOSED-BOOK AUDIT (owner ruling 2026-09-24): the citation audit judges only the claim and the
# passage its items file hands it, so the two web scripts are refused for its assignee before
# the allowlist admits them — see the closed_book block. Every other assignee is unaffected.
#
# REGISTER IT GLOBALLY. It scopes itself to RESTRICTED_BOARD below, so a global registration
# restricts nothing else - and the global config is tracked in git, while profile configs are
# not and die on a rebuild. See the scope block for how that works.
#
# Wire protocol (agent/shell_hooks.py, and see block-secret-reads.sh): JSON on stdin as
#   {"tool_name": "...", "tool_input": {...}}
# A block is either shape:
#   {"decision": "block", "reason": "..."}      # Claude-Code style
#   {"action": "block", "message": "..."}       # Hermes-canonical
# Anything else, including no output, allows the call.

set -uo pipefail

# ── scope: this board's kanban workers only ─────────────────────────────────────────────
#
# Shell hooks are spawned with subprocess.run() and no env= argument (agent/shell_hooks.py),
# so they INHERIT the caller's environment. A kanban worker carries HERMES_KANBAN_DB, whose
# path names the board. That makes the hook self-scoping, which is why it can be registered
# once in the global config and still restrict nothing but this board:
#
#   * an interactive session, or any agent that is not a kanban worker, has no
#     HERMES_KANBAN_DB and is never touched;
#   * a worker on another board is never touched;
#   * only rx-review's workers are held to the allowlist.
#
# Preferred over per-profile config for two reasons: the profile configs are gitignored and
# die on a rebuild, and rx-intake shares this box and genuinely needs a general terminal to
# extract PDFs.
RESTRICTED_BOARD="rx-review"

board=""
if [ -n "${HERMES_KANBAN_DB:-}" ]; then
  board="$(basename "$(dirname "${HERMES_KANBAN_DB}")")"
elif [ -n "${HERMES_KANBAN_BOARD:-}" ]; then
  board="${HERMES_KANBAN_BOARD}"
fi
[ "$board" = "$RESTRICTED_BOARD" ] || exit 0

payload="$(cat)"

block() {
  python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$1"
  exit 0
}

tool="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_name") or "")' 2>/dev/null || true)"
[ "$tool" = "terminal" ] || exit 0

cmd="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    ti = json.load(sys.stdin).get("tool_input") or {}
except Exception:
    sys.exit(0)
# the terminal tool has used `command` and `cmd` across versions; take whichever is a string,
# and fall back to the whole payload so a rename cannot silently open the gate.
for k in ("command", "cmd", "input"):
    v = ti.get(k)
    if isinstance(v, str):
        print(v)
        sys.exit(0)
print(json.dumps(ti))
' 2>/dev/null || true)"

[ -z "$cmd" ] && block "terminal: could not read the command, so it cannot be checked against the allowlist."

# Fold backslash-continuations into one line BEFORE any check. A worker that wraps a long
# command over two lines wrote ONE command, and refusing it is a false positive that stops the
# pipeline dead — the card templates used to print exactly this shape. Folding is safe because a
# continuation cannot introduce a second command: joining `rx.py \` + `; evil` yields
# `rx.py ; evil`, which the metacharacter check below then catches. Order matters, though — fold
# first, then check, or the `;` hides behind the newline.
cmd="$(printf '%s' "$cmd" | python3 -c '
import re, sys
print(re.sub(r"\\\n[ \t]*", " ", sys.stdin.read()).strip())
' 2>/dev/null || printf '%s' "$cmd")"

# Command chaining only. The board owner DELIBERATELY narrowed this (2026-08-06) to block just the
# two separators `;` and `&&` — plus a raw newline, which is `;` written another way — and to ALLOW
# every other shell operator (| & < > $() `...`). The reason is that a user's regimen correction is
# passed to `rx.py correct-item-slug-request` as a quoted argument, and those characters occur in
# ordinary corrections ("with food & water", "dose <100mg", "morning | evening"); the broad block
# refused them. The owner accepts the injection surface this opens; the allowlist below — which
# holds the command to this pipeline's own scripts — is the boundary that remains.
#
# NOTE grep -E: `&&` and `;` are matched literally; a single `&` is not `&&` and passes.
if [ "$(printf '%s' "$cmd" | wc -l)" -gt 0 ] && printf '%s' "$cmd" | grep -q $'\n'; then
  block "terminal on the rx-review board takes a single-line command."
fi
if printf '%s' "$cmd" | grep -qE '&&|;'; then
  block "terminal on the rx-review board blocks command chaining (\`;\` and \`&&\`). Run one command at a time."
fi

# ── the citation audit is closed-book (owner ruling 2026-09-24) ────────────────────────
#
# An audit card judges the claim and the passage its items file hands it — nothing else.
# Re-fetching at judgment time replaces the pipeline's located evidence with whatever the live
# page serves today; on 2026-09-20 that made the same citation come back both supported and
# unsupported. The web scripts below are this board's ONLY route to the network (no rx-* profile
# carries a web toolset), so the route is shut for this one assignee. Pipeline scripts stay open:
# a card may still run what its body names.
#
# The worker cannot claim a different identity: HERMES_KANBAN_TASK is set by the dispatcher
# (hermes_cli/kanban_db.py) and never by the worker. The assignee is read READ-ONLY straight
# from the board DB — spawning the CLI inside a 5-second pre_tool_call hook would blow the
# timeout, and a mode=ro connection mutates nothing. An assignee that cannot be read fails
# closed: if the DB is unreadable we may stall a research card, which retries loudly; we do
# not let an audit card reach the web quietly.
closed_book=""
if [ -n "${HERMES_KANBAN_TASK:-}" ] && [ -n "${HERMES_KANBAN_DB:-}" ]; then
  assignee="$(python3 -c '
import os, sqlite3
try:
    con = sqlite3.connect("file:" + os.environ["HERMES_KANBAN_DB"] + "?mode=ro", uri=True, timeout=2)
    row = con.execute("select assignee from tasks where id=?",
                      (os.environ["HERMES_KANBAN_TASK"],)).fetchone()
    print(row[0] if row and row[0] else "UNKNOWN")
except Exception:
    print("UNKNOWN")
' 2>/dev/null)"
  case "$assignee" in rx-audit|UNKNOWN|"") closed_book=1 ;; esac
fi

# The permitted invocations. The script name is exact AND its directory must be one of the
# pipeline's own, so a same-named script planted elsewhere is not admitted. Paths are matched
# loosely enough to survive ${HERMES_SKILL_DIR} expansion and either skills directory.
# The path is ANCHORED, not merely contained. Matching `rx-review/rx.py` anywhere in the
# string admitted `python3 /tmp/evil/rx-review/rx.py` - a planted directory of the right name
# defeated the whole allowlist. The path must begin at the home directory.
HOME_RE='(~|/home/[A-Za-z0-9._-]+)'
SKILLS_RE="$HOME_RE/(hermes-skills|\.hermes/skills)"
# The pipeline lives in the hermes-skills repo (moved 2026-09-04); ~/.hermes/rx-review is a local
# symlink to it that will be removed after one clean run, so BOTH paths are permitted until then.
PIPELINE="$HOME_RE/((\.hermes/rx-review)|(hermes-skills/rx-review/scripts))/(rx|rxsplit|fanout|lenses|verify)\.py"
# The board's only route to the network, now that the `web` toolset is gone from these
# profiles. It wraps search and fetch, so removing the built-in tools costs the board nothing.
WEBACCESS="$SKILLS_RE/web-access/scripts/web_access\.py"
BROWSER="$SKILLS_RE/browse-task/scripts/browse_task\.py"

# Closed-book enforcement, HERE because the two web routes are defined HERE. web_access.py and
# browse_task.py are the network's only door on this board (no rx-* profile carries a web
# toolset), so refusing them before the allowlist's exit, for this assignee only, is the whole
# of the ruling. Audit cards need no shell operators either — their work is read the items
# file, append verdicts, complete — so the characters that would smuggle a second program onto
# a permitted script's tail are refused for them too. `| & < > $( ` were relaxed board-wide on
# 2026-08-06 for quoted regimen-correction arguments; those are rx-intake cards, never rx-audit
# cards, so closing them here costs nothing.
if [ -n "$closed_book" ]; then
  if printf '%s' "$cmd" | grep -qE "^[[:space:]]*python3?[[:space:]]+($WEBACCESS|$BROWSER)([[:space:]]|\$)"; then
    block "terminal on the rx-review board keeps the citation audit closed-book: this card has
no network access, and the fields in its items file are the whole record. Judge the claim
against the section text you were given; if the section cannot settle it, say so in the verdict
(misquoted / unsupported / absent with a reason) rather than seeking more text."
  fi
  if printf '%s' "$cmd" | grep -qE '[|<>&$()`]'; then
    block "a closed-book audit card runs one permitted script with plain arguments — no pipes,
redirects, substitutions or backgrounding."
  fi
fi

if printf '%s' "$cmd" | grep -qE "^[[:space:]]*python3?[[:space:]]+($PIPELINE|$WEBACCESS|$BROWSER)([[:space:]]|\$)"; then
  exit 0
fi

block "terminal on the rx-review board runs this pipeline's own scripts only. Permitted:
    python3 ~/hermes-skills/rx-review/scripts/{rx,rxsplit,fanout,lenses,verify}.py ...
    (alias while the local symlink exists: python3 ~/.hermes/rx-review/{...}.py)
    python3 ~/hermes-skills/web-access/scripts/web_access.py search --query \"...\"
    python3 ~/hermes-skills/web-access/scripts/web_access.py fetch-content --url \"...\" [--browser]
    python3 ~/hermes-skills/browse-task/scripts/browse_task.py --task \"...\" [--start-url ...]
web_access.py is how this board reaches the web; there is no web_search or web_extract tool.
If a fetch-content comes back unreadable, add --browser to the same command."
