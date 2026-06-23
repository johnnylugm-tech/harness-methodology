#!/bin/bash
# =============================================================================
# Git Hooks Setup Script
# =============================================================================
# Sets up Git Hooks for the methodology project, ensuring Phase status
# is checked before each commit.
#
# Usage:
#   bash scripts/setup-git-hooks.sh
# =============================================================================

set -e  # Exit on error

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=============================================="
echo "Git Hooks Setup for harness-methodology"
echo "=============================================="
echo

# Resolve git working tree and actual .git dir (handles submodule gitdir files)
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$PROJECT_ROOT" ]; then
    echo -e "${RED}Error: Not a Git repository${NC}"
    echo "Please run this script from within a Git repository."
    exit 1
fi

GIT_DIR_RESULT="$(git rev-parse --git-dir 2>/dev/null)"
case "$GIT_DIR_RESULT" in
    /*) GIT_DIR="$GIT_DIR_RESULT" ;;
    *)  GIT_DIR="$PROJECT_ROOT/$GIT_DIR_RESULT" ;;
esac
HOOKS_DIR="$GIT_DIR/hooks"

# Ensure hooks directory exists
mkdir -p "$HOOKS_DIR"

# =============================================================================
# prepare-commit-msg Hook
# =============================================================================
# Triggered before the commit message editor opens.
# Checks if the current Phase's Quality Gate has passed.
# =============================================================================

PREPARE_COMMIT_MSG_HOOK="$HOOKS_DIR/prepare-commit-msg"

cat > "$PREPARE_COMMIT_MSG_HOOK" << 'HOOK_SCRIPT'
#!/bin/bash
# =============================================================================
# prepare-commit-msg hook
# =============================================================================
# Check if the current Phase's Quality Gate has passed.
# If not passed, block the commit.
# =============================================================================

set -e

# Unset git worktree env vars so all git commands resolve to this repo
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY

# Get project root directory and cd into it
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT" || { echo "[harness] ERROR: cannot cd to $PROJECT_ROOT"; exit 1; }

# Auto-detect harness_cli.py: root first, then harness/ submodule
if [ -f "$PROJECT_ROOT/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness_cli.py"
elif [ -f "$PROJECT_ROOT/harness/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness/harness_cli.py"
else
    HARNESS_CLI=""
fi

# Detect venv-aware Python (prefer project .venv over system python3)
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python3"
else
    PYTHON="python3"
fi

# Get current Phase (from state.json or default to 1)
PHASE=$("$PYTHON" -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")

# Check if Python is available
if ! "$PYTHON" --version &>/dev/null 2>&1; then
    echo "Warning: python not found ($PYTHON), skipping quality gate check"
    exit 0
fi

# Check if harness CLI exists
if [ ! -f "$HARNESS_CLI" ]; then
    echo "Warning: harness_cli.py not found, skipping quality gate check"
    exit 0
fi

# Infrastructure-only commits (submodule pointer updates) do not change
# project code — skip the gate check. The harness version bump itself is
# the fix; blocking on a failing gate that the new version resolves is circular.
COMMIT_MSG=$(cat "$1" 2>/dev/null | head -1)
if echo "$COMMIT_MSG" | grep -qE '^chore\(harness\):'; then
    echo "Infrastructure commit (harness submodule) — skipping gate check"
    exit 0
fi

# Run Quality Gate check
echo "Running Phase $PHASE Quality Gate check..."

"$PYTHON" "$HARNESS_CLI" pre-commit-check --phase "$PHASE" --project "$PROJECT_ROOT"

RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "=============================================="
    echo "PREFLIGHT FAILED"
    echo "=============================================="
    echo ""
    echo "Phase $PHASE preflight checks failed."
    echo "Please fix the issues before committing."
    echo ""
    echo "Phase is read from .methodology/state.json (current_phase)."
    echo "To advance the phase after gates pass, run:"
    echo "  python3 harness_cli.py advance-phase --phase <next_phase> --project ."
    echo ""
    exit 1
fi

echo "Phase $PHASE preflight passed!"
exit 0
HOOK_SCRIPT

chmod +x "$PREPARE_COMMIT_MSG_HOOK"

echo -e "${GREEN}OK${NC} Created prepare-commit-msg hook"


# =============================================================================
# post-merge Hook
# =============================================================================
# Triggered after a merge completes.
# Automatically checks Phase status after merge.
# =============================================================================

POST_MERGE_HOOK="$HOOKS_DIR/post-merge"

cat > "$POST_MERGE_HOOK" << 'HOOK_SCRIPT'
#!/bin/bash
# =============================================================================
# post-merge hook
# =============================================================================
# Auto-check Phase status after merge.
# =============================================================================

set -e

# Unset git worktree env vars so all git commands resolve to this repo
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY

# Get project root directory and cd into it
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT" || { echo "[harness] ERROR: cannot cd to $PROJECT_ROOT"; exit 1; }

# Auto-detect harness_cli.py: root first, then harness/ submodule
if [ -f "$PROJECT_ROOT/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness_cli.py"
elif [ -f "$PROJECT_ROOT/harness/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness/harness_cli.py"
else
    HARNESS_CLI=""
fi

# Detect venv-aware Python (prefer project .venv over system python3)
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python3"
else
    PYTHON="python3"
fi

# Get current Phase (from state.json or default to 1)
PHASE=$("$PYTHON" -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")

# Check if Python is available
if ! "$PYTHON" --version &>/dev/null 2>&1; then
    echo "Warning: python not found ($PYTHON), skipping quality gate check"
    exit 0
fi

# Check if harness CLI exists
if [ ! -f "$HARNESS_CLI" ]; then
    echo "Warning: harness_cli.py not found, skipping quality gate check"
    exit 0
fi

# Run Quality Gate check
echo ""
echo "Running Phase $PHASE Quality Gate check after merge..."
echo ""

"$PYTHON" "$HARNESS_CLI" pre-commit-check --phase "$PHASE" --project "$PROJECT_ROOT" || true

echo ""
echo "Post-merge quality check completed."
exit 0
HOOK_SCRIPT

chmod +x "$POST_MERGE_HOOK"

echo -e "${GREEN}OK${NC} Created post-merge hook"


# =============================================================================
# pre-push Hook (optional)
# =============================================================================
# Triggered before push.
# Checks if commits about to be pushed pass the Quality Gate.
# =============================================================================

PRE_PUSH_HOOK="$HOOKS_DIR/pre-push"

cat > "$PRE_PUSH_HOOK" << 'HOOK_SCRIPT'
#!/bin/bash
# =============================================================================
# pre-push hook
# =============================================================================
# Check Quality Gate before push.
# =============================================================================

set -e

# Unset git worktree env vars so all git commands resolve to this repo
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY

# Get project root directory and cd into it
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT" || { echo "[harness] ERROR: cannot cd to $PROJECT_ROOT"; exit 1; }

# Auto-detect harness_cli.py: root first, then harness/ submodule
if [ -f "$PROJECT_ROOT/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness_cli.py"
elif [ -f "$PROJECT_ROOT/harness/harness_cli.py" ]; then
    HARNESS_CLI="$PROJECT_ROOT/harness/harness_cli.py"
else
    HARNESS_CLI=""
fi

# Detect venv-aware Python (prefer project .venv over system python3)
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python3"
else
    PYTHON="python3"
fi

# Get current Phase (from state.json or default to 1)
PHASE=$("$PYTHON" -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")

# Check if Python is available
if ! "$PYTHON" --version &>/dev/null 2>&1; then
    echo "Warning: python not found ($PYTHON), skipping quality gate check"
    exit 0
fi

# Check if harness CLI exists
if [ ! -f "$HARNESS_CLI" ]; then
    echo "Warning: harness_cli.py not found, skipping quality gate check"
    exit 0
fi

# Infrastructure-only commits (submodule pointer updates) do not change
# project code — skip the gate check. Check ALL commits being pushed;
# if every one is chore(harness):, skip. Mixed pushes (code + harness) still
# run the full check.
_ALL_HARNESS_CHORE=true
while read -r _local_ref _local_sha _remote_ref _remote_sha; do
    if [ "$_local_sha" = "0000000000000000000000000000000000000000" ]; then
        continue  # branch deletion — skip
    fi
    _RANGE=""
    if [ "$_remote_sha" = "0000000000000000000000000000000000000000" ]; then
        _RANGE="$_local_sha"  # new branch — all commits
    else
        _RANGE="${_remote_sha}..${_local_sha}"
    fi
    _NON_CHORE=$(git log "$_RANGE" --format="%s" 2>/dev/null | grep -vE '^chore\(harness\):' | head -1)
    if [ -n "$_NON_CHORE" ]; then
        _ALL_HARNESS_CHORE=false
        break
    fi
done
if [ "$_ALL_HARNESS_CHORE" = true ]; then
    echo "All commits are infrastructure (harness submodule) — skipping gate check"
    exit 0
fi

# Run full Phase preflight before push (no bypass — use run-phase for complete check)
echo ""
echo "Running Phase $PHASE full preflight before push..."

"$PYTHON" "$HARNESS_CLI" run-phase --phase "$PHASE" --project "$PROJECT_ROOT"

RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "=============================================="
    echo "PRE-PUSH PREFLIGHT FAILED"
    echo "=============================================="
    echo ""
    echo "Phase $PHASE run-phase checks failed."
    echo "Fix all issues before pushing."
    echo ""
    exit 1
fi

echo "Pre-push check passed!"
exit 0
HOOK_SCRIPT

chmod +x "$PRE_PUSH_HOOK"

echo -e "${GREEN}OK${NC} Created pre-push hook"


# =============================================================================
# Configure git config
# =============================================================================

echo ""
echo "=============================================="
echo -e "${GREEN}Git Hooks Setup Complete!${NC}"
echo "=============================================="
echo ""
echo "Hooks installed:"
echo "  - prepare-commit-msg: Block commits if Phase not passed"
echo "  - post-merge: Check Phase status after merge"
echo "  - pre-push: Check before pushing"
echo ""
echo "Phase auto-detected from .methodology/state.json"
echo ""
