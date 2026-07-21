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
NC='\033[0m' # No Color

echo "=============================================="
echo "Git Hooks Setup for harness-methodology"
echo "=============================================="
echo

# Resolve git working tree
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$PROJECT_ROOT" ]; then
    echo -e "${RED}Error: Not a Git repository${NC}"
    echo "Please run this script from within a Git repository."
    exit 1
fi

# Calculate hooks directory relative to PROJECT_ROOT
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$SCRIPT_DIR/hooks"
RELATIVE_HOOKS_DIR="${HOOKS_DIR#$PROJECT_ROOT/}"

if [ ! -d "$PROJECT_ROOT/$RELATIVE_HOOKS_DIR" ]; then
    echo -e "${RED}Error: Hooks directory not found at $RELATIVE_HOOKS_DIR${NC}"
    exit 1
fi

cd "$PROJECT_ROOT"

# Set Git core.hooksPath
git config core.hooksPath "$RELATIVE_HOOKS_DIR"

# Mirror canonical hooks to .git/hooks/<name> as SYMLINKS so older tooling
# that does `ls .git/hooks/prepare-commit-msg` (the pre-core.hooksPath
# convention) still finds an active hook there.
#
# Background: when this project moved to `core.hooksPath`, the previous
# cleanup step DELETED `.git/hooks/<name>` outright, which made the modern
# config invisible to preflight checks that look for hooks in the legacy
# location. `core.hooksPath` takes precedence at git-invocation time —
# both paths can coexist, but the .git/hooks/ side must be a *symlink*
# (relative, pointing back to the canonical hooks dir) so the canonical
# hook script remains the single source of truth. No copy drift.
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)"
GIT_HOOKS_DIR="$GIT_DIR/hooks"
mkdir -p "$GIT_HOOKS_DIR"
for hook_file in "$PROJECT_ROOT/$RELATIVE_HOOKS_DIR"/*; do
    [ -f "$hook_file" ] || continue
    hook_name="$(basename "$hook_file")"
    target="$GIT_HOOKS_DIR/$hook_name"
    # If a legacy physical hook exists, replace it with a symlink to the
    # canonical file. (Don't blindly clobber unrelated user files.)
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        if grep -q "harness_cli.py\|harness-methodology\|harness_quality_gate" "$target" 2>/dev/null; then
            rm -f "$target"
            echo "Replaced legacy physical hook: $target"
        else
            echo "SKIP: $target exists and is unrelated to harness (won't clobber)"
            continue
        fi
    fi
    # Make the symlink relative so it survives moving the repo (or submodule
    # pointer changes that relocate $PROJECT_ROOT). The canonical hooks dir
    # sits at $PROJECT_ROOT/$RELATIVE_HOOKS_DIR; the symlink target sits at
    # $GIT_HOOKS_DIR. Walking up one level, the canonical dir is
    # ../../$RELATIVE_HOOKS_DIR/$hook_name.
    relative_target="../../$RELATIVE_HOOKS_DIR/$hook_name"
    ln -sf "$relative_target" "$target"
done
echo "Mirrored $(ls "$PROJECT_ROOT/$RELATIVE_HOOKS_DIR" | wc -l | tr -d ' ') hook(s) to $GIT_HOOKS_DIR (symlinks to $RELATIVE_HOOKS_DIR/)"

echo -e "${GREEN}OK${NC} Set core.hooksPath to $RELATIVE_HOOKS_DIR"
echo ""
echo "=============================================="
echo -e "${GREEN}Git Hooks Setup Complete!${NC}"
echo "=============================================="
echo ""
echo "Hooks active:"
echo "  - prepare-commit-msg: Block commits if Phase not passed"
echo "  - pre-commit: Warn on uncommitted submodule edits + canonical FR-ID lint"
echo "  - post-merge: Check Phase status after merge"
echo "  - pre-push: Check before pushing"
echo ""
echo "Any future updates to $RELATIVE_HOOKS_DIR/ will apply automatically."
echo ""
