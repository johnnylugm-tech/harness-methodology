#!/usr/bin/env bash
# =============================================================================
# self_check.sh — what must be green before a change to this framework lands.
# =============================================================================
# Round 67 站5. There were two answers to that question. CI's Framework
# Self-Tests job ran ruff, the regression-guard registry and the full pytest
# suite; the pre-push hook ran the registry and `run-phase` preflight, and
# neither ruff nor pytest. So the only place the full rule was enforced was
# after the push had already landed.
#
# Measured over the last 25 CI runs on this repo: 12 red. Of the 9 whose logs
# are still retrievable, 6 failed on a check that is fully deterministic and
# runs in seconds locally —
#
#     test_file_size_ratchet::test_production_file_line_ratchet     x3
#     test_workflow_js_conventions (headroom / measured-size)       x2
#     test_patch_discipline::test_private_patch_ratchet             x1
#     test_spec_contract::test_id_06_type_safety_clean              x1
#     Lint (ruff)                                                   x1
#
# None of them needed a runner, a network, or luck. Every one was knowable
# before the push.
#
# This is that list, once. CI runs it and the pre-push hook runs it, so a
# check added to one is a check the other gains — which is the whole point,
# and is pinned by tests/test_selfcheck_single_source.py.
#
# Usage:  scripts/self_check.sh
# Exits non-zero on the first failing step, naming it.
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Prefer the project venv over whatever is on PATH: `python3` on this machine
# resolves to an interpreter without the project's dependencies, and a check
# that reports a false green is worse than one that does not run.
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

_step() {
    local name="$1"; shift
    echo ""
    echo "[self-check] $name"
    if ! "$@"; then
        echo ""
        echo "=============================================="
        echo "SELF-CHECK FAILED: $name"
        echo "=============================================="
        echo "This is what CI runs. Fix it here rather than after the push."
        exit 1
    fi
}

# First, because it is the precondition for every step below ever running at
# push time: a hook git does not invoke checks nothing (Round 80 站3).
_step "Git hook wiring"                 bash "$REPO_ROOT/scripts/check_hook_wiring.sh" "$REPO_ROOT"
_step "Lint (ruff)"                     "$PYTHON" -m ruff check .
_step "Regression-guard registry check" "$PYTHON" scripts/verify_regression_guards.py
_step "Unit tests"                      "$PYTHON" -m pytest tests/ -q

echo ""
echo "[self-check] all checks passed"
