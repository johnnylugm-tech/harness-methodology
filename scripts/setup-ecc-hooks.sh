#!/usr/bin/env bash
# setup-ecc-hooks.sh — Install ECC (Everything Claude Code) hooks for harness-methodology.
#
# ECC hooks operate at the Claude Code session layer, independently of the harness
# Python pipeline. They intercept tool calls BEFORE execution, providing a bypass-proof
# safety net that local git hooks alone cannot:
#
#   pre:bash:dispatcher  → blocks git push --no-verify, git commit --no-verify,
#                           git --no-verify, and reminds push-checkpoint before push
#   stop:cost-tracker    → tracks token/cost per session
#
# Usage:
#   bash scripts/setup-ecc-hooks.sh              # install
#   bash scripts/setup-ecc-hooks.sh --verify     # check if installed
#   bash scripts/setup-ecc-hooks.sh --uninstall  # remove
#
# Exit codes:
#   0 = hooks present (--verify) / installed successfully
#   1 = hooks missing (--verify) / install failed
#   2 = claude CLI not found

set -euo pipefail

HOOKS_FILE="$HOME/.claude/hooks/hooks.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── CLI detection ────────────────────────────────────────────────────────────

detect_claude() {
    if command -v claude &>/dev/null; then
        echo "claude"
        return 0
    fi
    return 1
}

# ── Verify ───────────────────────────────────────────────────────────────────

verify_hooks() {
    if [ ! -f "$HOOKS_FILE" ]; then
        echo "[verify] ECC hooks file not found: $HOOKS_FILE"
        echo "[verify] Run: bash $0"
        return 1
    fi

    # Check for the critical dispatcher hook that blocks --no-verify
    if grep -q '"pre:bash:dispatcher"' "$HOOKS_FILE" 2>/dev/null; then
        echo "[verify] OK — pre:bash:dispatcher hook present (blocks git --no-verify)"
    else
        echo "[verify] WARNING: pre:bash:dispatcher hook NOT found in $HOOKS_FILE"
        echo "[verify] git --no-verify bypass is NOT blocked at the Claude Code session layer"
        return 1
    fi

    # Check for cost tracker (non-critical)
    if grep -q '"stop:cost-tracker"' "$HOOKS_FILE" 2>/dev/null; then
        echo "[verify] OK — stop:cost-tracker hook present"
    else
        echo "[verify] INFO: stop:cost-tracker not found (optional, for token/cost tracking)"
    fi

    return 0
}

# ── Install ──────────────────────────────────────────────────────────────────

install_hooks() {
    CLAUDE_BIN=$(detect_claude) || {
        echo "[install] ERROR: claude CLI not found on PATH."
        echo "[install] ECC hooks require Claude Code to be installed."
        return 2
    }

    mkdir -p "$(dirname "$HOOKS_FILE")"

    # If hooks.json already exists, merge rather than overwrite.
    # We only manage pre:bash:dispatcher and stop:cost-tracker entries.
    if [ -f "$HOOKS_FILE" ]; then
        echo "[install] $HOOKS_FILE already exists — adding harness hooks to existing config"
        # Use Python for safe JSON merge (bash-only jq dependency avoided)
        python3 - "$HOOKS_FILE" "$CLAUDE_BIN" <<'PYEOF'
import json, sys

hooks_file = sys.argv[1]
with open(hooks_file) as f:
    hooks = json.load(f)

if not isinstance(hooks, dict):
    hooks = {}

# pre:bash:dispatcher — blocks git --no-verify
if "pre:bash:dispatcher" not in hooks:
    hooks["pre:bash:dispatcher"] = {
        "command": 'echo "$CLAUDE_CODE_TOOL_INPUT" | grep -qi "git.*--no-verify\\|git.*push.*--no\\|git.*commit.*--no" && { echo "[ECC] git --no-verify BLOCKED — use push-checkpoint or push-milestone instead"; exit 1; } || true',
        "description": "Block git --no-verify (harness-methodology HR compliance)"
    }

# stop:cost-tracker — tracks token/cost per session
if "stop:cost-tracker" not in hooks:
    hooks["stop:cost-tracker"] = {
        "command": "echo \"[cost] tokens: $CLAUDE_CODE_SESSION_TOKENS\" >> /tmp/claude-cost.log 2>/dev/null || true",
        "description": "Track session token usage (harness-methodology)"
    }

with open(hooks_file, 'w') as f:
    json.dump(hooks, f, indent=2)
    f.write('\n')

print(f"[install] Merged harness hooks into {hooks_file}")
PYEOF
    else
        echo "[install] Creating new $HOOKS_FILE"
        python3 - "$HOOKS_FILE" <<'PYEOF'
import json, sys

hooks_file = sys.argv[1]
hooks = {
    "pre:bash:dispatcher": {
        "command": 'echo "$CLAUDE_CODE_TOOL_INPUT" | grep -qi "git.*--no-verify\\|git.*push.*--no\\|git.*commit.*--no" && { echo "[ECC] git --no-verify BLOCKED — use push-checkpoint or push-milestone instead"; exit 1; } || true',
        "description": "Block git --no-verify (harness-methodology HR compliance)"
    },
    "stop:cost-tracker": {
        "command": "echo \"[cost] tokens: $CLAUDE_CODE_SESSION_TOKENS\" >> /tmp/claude-cost.log 2>/dev/null || true",
        "description": "Track session token usage (harness-methodology)"
    }
}

with open(hooks_file, 'w') as f:
    json.dump(hooks, f, indent=2)
    f.write('\n')

print(f"[install] Created {hooks_file} with harness hooks")
PYEOF
    fi

    echo "[install] Done — verifying installation..."
    verify_hooks
}

# ── Uninstall ────────────────────────────────────────────────────────────────

uninstall_hooks() {
    if [ ! -f "$HOOKS_FILE" ]; then
        echo "[uninstall] No hooks file found — nothing to remove."
        return 0
    fi

    python3 - "$HOOKS_FILE" <<'PYEOF'
import json, sys

hooks_file = sys.argv[1]
with open(hooks_file) as f:
    hooks = json.load(f)

removed = []
for key in ["pre:bash:dispatcher", "stop:cost-tracker"]:
    if key in hooks:
        del hooks[key]
        removed.append(key)

if removed:
    with open(hooks_file, 'w') as f:
        json.dump(hooks, f, indent=2)
        f.write('\n')
    print(f"[uninstall] Removed: {', '.join(removed)}")
else:
    print("[uninstall] No harness hooks found — nothing to remove.")
PYEOF
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "${1:-}" in
    --verify)
        verify_hooks
        ;;
    --uninstall)
        uninstall_hooks
        ;;
    *)
        install_hooks
        ;;
esac
