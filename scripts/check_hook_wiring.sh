#!/usr/bin/env bash
# =============================================================================
# check_hook_wiring.sh — will `git push` from this clone check anything?
# =============================================================================
# Round 80 站3. Every check the pre-push hook performs is worth exactly as much
# as the odds that git invokes the hook at all, and nothing asked. Measured in
# the clone this was written in, at dff609e6:
#
#     $ git config --get core.hooksPath        # nothing, exit 1
#     $ git rev-parse --git-path hooks/pre-push
#     .git/hooks/pre-push                      # does not exist
#
# So Round 79 站5's work — four silent-pass paths inside the hook converted
# into named BLOCKs — could not fire here. Round 72 recorded the same gap as
# deferred item B and it stayed open. tests/test_selfcheck_single_source.py
# opened by stating "There IS a pre-push hook, and it IS active", which was a
# premise, not an assertion.
#
# THE QUESTION ASKED
#
# `git rev-parse --git-path hooks/pre-push` is git's own answer to "which file
# would I run", and it resolves through core.hooksPath when that is set. That
# accepts both shapes scripts/setup-git-hooks.sh produces (the config, and the
# .git/hooks/* symlinks it mirrors for older tooling) without this script
# having to know which is in play.
#
# NOT asked: whether the resolved hook is canonical rather than a stale
# physical copy. setup-git-hooks.sh already replaces legacy physical hooks with
# symlinks, and no incident here has been a drifted copy.
#
# HONEST LIMIT: this narrows the window, it does not close it. Someone who does
# not run self_check.sh does not see this either, and `git push --no-verify`
# never invokes a hook. Closing it needs a required status check on the branch.
#
# Usage:  scripts/check_hook_wiring.sh [repo-root]
# Exits 0 when a pre-push hook would run (or when not applicable, saying so).
# =============================================================================

set -uo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# CI clones never push, so there is nothing to wire. Say it rather than
# skipping quietly — a check that did not run must not read like one that
# passed (Round 79 站5, the rule this station is the second application of).
if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "  not applicable: CI does not push, so no pre-push hook is installed here"
    exit 0
fi

if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "  not applicable: $REPO_ROOT is not a git repository"
    exit 0
fi

HOOK_REL="$(git -C "$REPO_ROOT" rev-parse --git-path hooks/pre-push 2>/dev/null)"
case "$HOOK_REL" in
    /*) HOOK_PATH="$HOOK_REL" ;;
    *)  HOOK_PATH="$REPO_ROOT/$HOOK_REL" ;;
esac

CONFIGURED="$(git -C "$REPO_ROOT" config --get core.hooksPath 2>/dev/null || true)"

if [ ! -f "$HOOK_PATH" ]; then
    echo ""
    echo "  git would run '$HOOK_REL' as the pre-push hook, and that file does"
    echo "  not exist. core.hooksPath is '${CONFIGURED:-<unset>}'."
    echo ""
    echo "  Nothing you push from this clone is checked by the hook, so every"
    echo "  check it performs is unreached — including the self-check this"
    echo "  script is the first step of."
    echo ""
    echo "  Fix:  scripts/setup-git-hooks.sh"
    exit 1
fi

if [ ! -x "$HOOK_PATH" ]; then
    echo ""
    echo "  '$HOOK_REL' exists but is not executable, and git will not run a"
    echo "  hook it cannot execute. core.hooksPath is '${CONFIGURED:-<unset>}'."
    echo ""
    echo "  Fix:  chmod +x '$HOOK_REL'   (or re-run scripts/setup-git-hooks.sh)"
    exit 1
fi

echo "  pre-push hook wired: $HOOK_REL (core.hooksPath='${CONFIGURED:-<unset>}')"
exit 0
