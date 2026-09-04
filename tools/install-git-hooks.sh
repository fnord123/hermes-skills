#!/usr/bin/env bash
# Install this repo's git hooks. Run once per clone:
#
#     bash tools/install-git-hooks.sh
#
# .git/hooks is not version-controlled, so a hook that exists only on one machine protects only
# that machine. This script is the tracked copy.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/.git/hooks/pre-push"

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# Run what CI runs, before it becomes public. On 2026-07-31 a skill was pushed with three
# critical findings (no PREFER clause, no trigger list, an undeclared dependency) and the break
# was noticed only because someone asked. This takes a few seconds.
set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO" || exit 1

fail=0
if ! python3 tools/lint_skills.py --severity critical >/tmp/hs-lint.log 2>&1; then
    echo; sed -n '1,25p' /tmp/hs-lint.log; fail=1
fi
if ! python3 tools/test_lint_skills.py >/tmp/hs-linttest.log 2>&1; then
    echo; tail -20 /tmp/hs-linttest.log; fail=1
fi
if ! python3 tools/vendor.py check >/tmp/hs-vendor.log 2>&1; then
    echo; sed -n '1,15p' /tmp/hs-vendor.log; fail=1
fi
if ! python3 tools/run_tests.py >/tmp/hs-tests.log 2>&1; then
    echo; tail -20 /tmp/hs-tests.log; fail=1
fi
# Two CI steps are not *_test.py, so run_tests.py above does not cover them locally: the
# generated card map and the terminal allowlist's own block/allow battery (a security boundary
# with two real bypasses — see the step comments in .github/workflows/tests.yml).
if ! python3 rx-review/scripts/cardmap.py --check >/tmp/hs-cardmap.log 2>&1; then
    echo; tail -10 /tmp/hs-cardmap.log; fail=1
fi
if ! bash rx-review/scripts/test-terminal-pipeline-only.sh >/tmp/hs-allowlist.log 2>&1; then
    echo; tail -20 /tmp/hs-allowlist.log; fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo
    echo "pre-push: CI would fail on this — push aborted."
    echo "          override with: git push --no-verify"
    exit 1
fi
echo "pre-push: lint, lint self-test, vendor, tests, card map and allowlist ok"
EOF

chmod +x "$HOOK"
echo "installed: $HOOK"
