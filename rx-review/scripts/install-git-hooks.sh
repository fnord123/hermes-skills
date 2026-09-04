#!/usr/bin/env bash
# Install a pre-commit hook that runs the rx-review tests before a commit is created.
#
# GitHub Actions is the authority - it cannot be skipped and it runs for every clone. But it
# reports AFTER the commit exists, and this repo's working tree is the live Hermes install, so
# a broken parser is already running on the machine by the time CI goes red. The hook closes
# that window locally.
#
# Hooks are not carried by `git clone`, which is why this is a script you run rather than a
# file that just appears:
#
#     bash ~/.hermes/rx-review/install-git-hooks.sh
#
# Bypass a single commit with `git commit --no-verify` when you genuinely mean to.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/.git/hooks/pre-commit"

if [ ! -d "$REPO/.git" ]; then
    echo "not a git repo: $REPO" >&2
    exit 1
fi

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# Installed by rx-review/install-git-hooks.sh
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"

# Only pay the cost when something under test is actually changing. The pattern used to be
# rx-review/*.py alone, which meant the terminal allowlist - a security boundary that has had
# two real bypasses - could be edited and committed with nothing run at all.
staged="$(git diff --cached --name-only)"
run_rx=0; run_hook=0; run_prov=0
grep -qE '^rx-review/.*\.py$'      <<<"$staged" && run_rx=1
grep -qE '^hooks/'                  <<<"$staged" && run_hook=1
grep -qE '^provision-profiles\.py$' <<<"$staged" && run_prov=1
# The card templates and the allowlist must agree, so a change to EITHER re-checks BOTH.
[ "$run_rx" = 1 ] && run_hook=1
[ "$run_hook" = 1 ] && run_rx=1
[ $((run_rx + run_hook + run_prov)) -eq 0 ] && exit 0

fail() {
    echo
    tail -30 "$2"
    echo
    echo "pre-commit: $1 FAILED — commit aborted (full log: $2)"
    echo "            override with: git commit --no-verify"
    exit 1
}

if [ "$run_rx" = 1 ]; then
    echo "pre-commit: rx-review lab parser tests"
    python3 "$REPO/rx-review/rx_test.py" >/tmp/rx-precommit.log 2>&1 \
        || fail "lab parser tests" /tmp/rx-precommit.log
    if ! python3 -m compileall -q "$REPO/rx-review/" >/dev/null 2>&1; then
        echo "pre-commit: a script under rx-review/ does not compile — commit aborted"
        exit 1
    fi
    # The card map in ARCHITECTURE.md is generated; adding or renaming a card must update it.
    python3 "$REPO/rx-review/cardmap.py" --check >/tmp/rx-cardmap.log 2>&1 \
        || fail "ARCHITECTURE.md card map is stale (python3 rx-review/cardmap.py --write)" \
                /tmp/rx-cardmap.log
fi

if [ "$run_hook" = 1 ]; then
    echo "pre-commit: terminal allowlist + card/allowlist agreement"
    bash "$REPO/hooks/test-terminal-pipeline-only.sh" >/tmp/rx-hook.log 2>&1 \
        || fail "terminal allowlist tests" /tmp/rx-hook.log
    python3 "$REPO/rx-review/card_command_test.py" >/tmp/rx-cards.log 2>&1 \
        || fail "a card instructs a command the allowlist forbids" /tmp/rx-cards.log
fi

if [ "$run_prov" = 1 ]; then
    echo "pre-commit: profile provisioning tests"
    python3 "$REPO/provision_profiles_test.py" >/tmp/rx-prov.log 2>&1 \
        || fail "provisioning tests" /tmp/rx-prov.log
fi

echo "pre-commit: ok"
EOF

chmod +x "$HOOK"
echo "installed: $HOOK"
echo "runs when rx-review/, hooks/ or provision-profiles.py is staged; skip once with --no-verify"
