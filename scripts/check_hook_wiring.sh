#!/usr/bin/env bash
# =============================================================================
# check_hook_wiring.sh — will `git push` from this clone check anything?
# =============================================================================
# Round 80 站3 asked this question here, in shell. Round 81 站4 moved the
# question itself to core/git_hooks.py so that core/doctor_checks/git_state.py
# could ask it of CONSUMER projects too — where it matters more, because
# `git clone` copies neither `.git/hooks/*` nor `core.hooksPath`, so every
# project cloned since `init-project` ran has four dead hooks.
#
# This file is now a wrapper. It holds no copy of the question and no copy of
# the answers: `core.git_hooks.operator_report` renders the same text this
# script used to print, and tests/test_hook_wiring_is_asked_of_projects.py
# pins it byte-for-byte against a recording made before the move. Two
# statements of one rule is what Round 17 is named after.
#
# HONEST LIMIT (unchanged from 站3): this narrows the window, it does not close
# it. Someone who does not run self_check.sh does not see this either, and
# `git push --no-verify` never invokes a hook. Closing THAT needs a required
# status check on the branch. The doctor check added in 站4 closes a different
# hole — a project whose hooks are simply not there — and needs no such thing.
#
# Usage:  scripts/check_hook_wiring.sh [repo-root]
# Exits 0 when a pre-push hook would run (or when not applicable, saying so).
# =============================================================================

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${1:-$HERE}"

# Same preference scripts/self_check.sh applies, and for the same reason: the
# `python3` on this machine may be an interpreter without the project on its
# path, and a check that cannot run must fail rather than report a false green.
if [ -x "$HERE/.venv/bin/python" ]; then
    PYTHON="$HERE/.venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

if ! command -v "$PYTHON" >/dev/null 2>&1 && [ ! -x "$PYTHON" ]; then
    echo ""
    echo "  no usable Python interpreter ('$PYTHON'), so the hook-wiring check"
    echo "  could not run. It is not passing; it did not happen."
    exit 1
fi

PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m core.git_hooks "$REPO_ROOT"
