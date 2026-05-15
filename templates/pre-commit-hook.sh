#!/bin/bash
# harness-methodology pre-commit hook
# Installed by: scripts/setup-git-hooks.sh or scripts/harness-init.sh
#
# Usage (manual install):
#   cp templates/pre-commit-hook.sh .git/hooks/prepare-commit-msg
#   chmod +x .git/hooks/prepare-commit-msg

PHASE=$(git config --local --get quality.phase 2>/dev/null || echo "1")
command -v python3 &>/dev/null || exit 0

GIT_DIR=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
HARNESS_CLI="$GIT_DIR/harness_cli.py"
[ -f "$HARNESS_CLI" ] || HARNESS_CLI="$GIT_DIR/harness/harness_cli.py"
[ -f "$HARNESS_CLI" ] || { echo "harness_cli.py not found — skipping quality check"; exit 0; }

cd "$GIT_DIR"
python3 "$HARNESS_CLI" pre-commit-check --phase "$PHASE" --project "$GIT_DIR" || {
    echo ""
    echo "PREFLIGHT FAILED (Phase $PHASE)"
    echo "Fix the issues above before committing."
    exit 1
}
