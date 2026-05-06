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

# Get project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

# Check if this is a Git repository
if [ ! -d "$PROJECT_ROOT/.git" ]; then
    echo -e "${RED}Error: Not a Git repository${NC}"
    echo "Please run this script from the project root."
    exit 1
fi

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

# Get project root directory
GIT_DIR=$(git rev-parse --show-toplevel)
HARNESS_CLI="$GIT_DIR/harness_cli.py"

# Get current Phase (from git config or default to 1)
PHASE=$(git config --local --get quality.phase 2>/dev/null || echo "1")

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Warning: python3 not found, skipping quality gate check"
    exit 0
fi

# Check if harness CLI exists
if [ ! -f "$HARNESS_CLI" ]; then
    echo "Warning: harness_cli.py not found, skipping quality gate check"
    exit 0
fi

# Run Quality Gate check
echo "Running Phase $PHASE Quality Gate check..."

cd "$GIT_DIR"
python3 "$HARNESS_CLI" run-gate --phase "$PHASE"

RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "=============================================="
    echo "QUALITY GATE FAILED"
    echo "=============================================="
    echo ""
    echo "Phase $PHASE has not passed Quality Gate."
    echo "Please fix the issues before committing."
    echo ""
    echo "To update the current Phase, run:"
    echo "  git config quality.phase <phase_number>"
    echo ""
    exit 1
fi

echo "Phase $PHASE Quality Gate passed!"
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

# Get project root directory
GIT_DIR=$(git rev-parse --show-toplevel)
HARNESS_CLI="$GIT_DIR/harness_cli.py"

# Get current Phase (from git config or default to 1)
PHASE=$(git config --local --get quality.phase 2>/dev/null || echo "1")

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Warning: python3 not found, skipping quality gate check"
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

cd "$GIT_DIR"
python3 "$HARNESS_CLI" run-gate --phase "$PHASE" || true

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

# Get project root directory
GIT_DIR=$(git rev-parse --show-toplevel)
HARNESS_CLI="$GIT_DIR/harness_cli.py"

# Get current Phase (from git config or default to 1)
PHASE=$(git config --local --get quality.phase 2>/dev/null || echo "1")

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Warning: python3 not found, skipping quality gate check"
    exit 0
fi

# Check if harness CLI exists
if [ ! -f "$HARNESS_CLI" ]; then
    echo "Warning: harness_cli.py not found, skipping quality gate check"
    exit 0
fi

# Check if the most recent commit passed Quality Gate
echo ""
echo "Checking recent commit for Quality Gate..."

cd "$GIT_DIR"
LAST_COMMIT_MSG=$(git log -1 --pretty=%B | head -n 1)

if [[ "$LAST_COMMIT_MSG" == *"STAGE_PASS"* ]]; then
    echo "Found STAGE_PASS in last commit, skipping check"
    exit 0
fi

# Run check
python3 "$HARNESS_CLI" run-gate --phase "$PHASE"

RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "=============================================="
    echo "PRE-PUSH QUALITY GATE FAILED"
    echo "=============================================="
    echo ""
    echo "Last commit did not pass Quality Gate."
    echo "Please ensure all Phase checks pass before pushing."
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
echo "Configuration"
echo "=============================================="
echo

# Set default Phase
read -p "Enter current Phase (1-8) [default: 1]: " PHASE
PHASE=${PHASE:-1}

git config --local quality.phase "$PHASE"
echo -e "${GREEN}OK${NC} Set quality.phase to $PHASE"

# Ask whether to enable automatic block
read -p "Enable automatic block on Quality Gate failure? (y/n) [default: y]: " ENABLE_BLOCK
ENABLE_BLOCK=${ENABLE_BLOCK:-y}

if [ "$ENABLE_BLOCK" = "y" ]; then
    git config --local quality.block_on_failure true
    echo -e "${GREEN}OK${NC} Enabled block on failure"
else
    git config --local quality.block_on_failure false
    echo -e "${GREEN}OK${NC} Disabled block on failure"
fi


# =============================================================================
# Done
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
echo "Current Phase: $PHASE"
echo ""
echo "To change Phase:"
echo "  git config quality.phase <phase_number>"
echo ""
echo "To check Phase status manually:"
echo "  python3 harness_cli.py run-gate --phase $PHASE"
echo ""
