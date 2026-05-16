#!/usr/bin/env bash
# =============================================================================
# harness-init.sh — Idempotent harness integration setup for a target project
# =============================================================================
# Safe to run multiple times; already-done steps are automatically skipped.
#
# Usage (run from inside your target project, or set TARGET_DIR):
#   bash /path/to/harness-methodology/scripts/harness-init.sh [--phase N]
#   TARGET_DIR=/your/project bash /path/to/harness/scripts/harness-init.sh --phase 3
#
# Embed in project init scripts (Makefile, setup.sh, etc.) — fully idempotent.
# =============================================================================

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${TARGET_DIR:-$(pwd)}"
PHASE_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE_ARG="$2"; shift 2 ;;
        *)       echo "Unknown arg: $1"; exit 1 ;;
    esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
skip() { echo -e "  ${YELLOW}↷${NC}  $1 (already done)"; }
err()  { echo -e "  ${RED}✗${NC}  $1"; exit 1; }

echo "══════════════════════════════════════════════"
echo "  Harness-Methodology Init"
echo "  Target : $TARGET_DIR"
echo "══════════════════════════════════════════════"
echo

[[ -d "$TARGET_DIR/.git" ]] || err "$TARGET_DIR is not a git repository"

HOOKS_DIR="$TARGET_DIR/.git/hooks"
HOOK_MARKER="# harness-methodology"

# ── Step 1: Git hooks ─────────────────────────────────────────────────────────
if grep -q "$HOOK_MARKER" "$HOOKS_DIR/prepare-commit-msg" 2>/dev/null; then
    skip "git hooks"
else
    mkdir -p "$HOOKS_DIR"

    # prepare-commit-msg (blocking)
    cat > "$HOOKS_DIR/prepare-commit-msg" << 'HOOK'
#!/bin/bash
# harness-methodology
command -v python3 &>/dev/null || exit 0
GIT_DIR=$(git rev-parse --show-toplevel)
PHASE=$(cd "$GIT_DIR" && python3 -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")
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
HOOK
    chmod +x "$HOOKS_DIR/prepare-commit-msg"

    # post-merge (non-blocking, informational)
    cat > "$HOOKS_DIR/post-merge" << 'HOOK'
#!/bin/bash
# harness-methodology
command -v python3 &>/dev/null || exit 0
GIT_DIR=$(git rev-parse --show-toplevel)
PHASE=$(cd "$GIT_DIR" && python3 -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")
HARNESS_CLI="$GIT_DIR/harness_cli.py"
[ -f "$HARNESS_CLI" ] || HARNESS_CLI="$GIT_DIR/harness/harness_cli.py"
[ -f "$HARNESS_CLI" ] || { echo "harness_cli.py not found — skipping quality check"; exit 0; }
cd "$GIT_DIR"
python3 "$HARNESS_CLI" pre-commit-check --phase "$PHASE" --project "$GIT_DIR" || true
HOOK
    chmod +x "$HOOKS_DIR/post-merge"

    # pre-push (blocking, no bypass)
    cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/bin/bash
# harness-methodology
command -v python3 &>/dev/null || exit 0
GIT_DIR=$(git rev-parse --show-toplevel)
PHASE=$(cd "$GIT_DIR" && python3 -c "import json; d=json.load(open('.methodology/state.json')); print(d.get('current_phase', 1))" 2>/dev/null || echo "1")
HARNESS_CLI="$GIT_DIR/harness_cli.py"
[ -f "$HARNESS_CLI" ] || HARNESS_CLI="$GIT_DIR/harness/harness_cli.py"
[ -f "$HARNESS_CLI" ] || { echo "harness_cli.py not found — skipping quality check"; exit 0; }
cd "$GIT_DIR"
python3 "$HARNESS_CLI" run-phase --phase "$PHASE" --project "$GIT_DIR" || {
    echo ""
    echo "PRE-PUSH PREFLIGHT FAILED (Phase $PHASE)"
    echo "Fix the issues above before pushing."
    exit 1
}
HOOK
    chmod +x "$HOOKS_DIR/pre-push"

    ok "git hooks installed (prepare-commit-msg | post-merge | pre-push)"
fi

# ── Step 2: state.json (phase source of truth) ────────────────────────────────
STATE_FILE="$TARGET_DIR/.methodology/state.json"
if [[ -f "$STATE_FILE" ]]; then
    CURRENT_PHASE=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('current_phase', '?'))" 2>/dev/null || echo "?")
    skip "state.json (current_phase: $CURRENT_PHASE)"
else
    PHASE="${PHASE_ARG:-1}"
    mkdir -p "$TARGET_DIR/.methodology"
    NOW=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())")
    cat > "$STATE_FILE" << JSON
{
  "state": "ACTIVE",
  "current_phase": $PHASE,
  "last_gate": null,
  "last_fr": null,
  "last_update": "$NOW"
}
JSON
    ok "state.json initialized (current_phase = $PHASE)"
fi

# ── Step 3: CI workflow ───────────────────────────────────────────────────────
CI_DEST="$TARGET_DIR/.github/workflows/harness_quality_gate.yml"
CI_TEMPLATE="$HARNESS_DIR/templates/harness_quality_gate.yml"

if [[ ! -f "$CI_TEMPLATE" ]]; then
    echo -e "  ${YELLOW}↷${NC}  CI workflow (template not found — skipped)"
elif [[ ! -f "$CI_DEST" ]]; then
    mkdir -p "$TARGET_DIR/.github/workflows"
    cp "$CI_TEMPLATE" "$CI_DEST"
    ok "CI workflow → .github/workflows/harness_quality_gate.yml"
elif diff -q "$CI_DEST" "$CI_TEMPLATE" > /dev/null 2>&1; then
    skip ".github/workflows/harness_quality_gate.yml"
else
    echo -e "  ${YELLOW}~${NC}  CI workflow outdated — updating (diff below):"
    diff --unified=2 "$CI_DEST" "$CI_TEMPLATE" | head -40 || true
    cp "$CI_TEMPLATE" "$CI_DEST"
    ok "CI workflow updated → .github/workflows/harness_quality_gate.yml"
fi

echo
echo "  Done."
echo "  Advance phase : python harness_cli.py advance-phase --phase N --project ."
echo "  Check phase   : python harness_cli.py run-phase --phase N --project ."
echo "  Phase source  : .methodology/state.json (current_phase)"
echo "══════════════════════════════════════════════"
