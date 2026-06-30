"""
Regression tests for _detect_push_ancestor_direction + _commit_and_push
hinter added in commit (this file).

Why this exists
---------------
`_commit_and_push` previously issued a bare `git push` and surfaced
failure only as `[git WARN] git push failed: ...`. When local HEAD was
an ancestor of the upstream tracking ref (common in baseline / replay
re-run scenarios where local was reset to an old SHA), `git push` was
silently rejected by the remote with no actionable hint for the
operator — leaving push-checkpoint to spin through 5 retries with the
same opaque error and ultimately report "push-checkpoint did not PASS
in 5 attempts".

The fix pre-flights the ancestor direction via merge-base --is-ancestor
and refuses to push when local would clobber upstream history,
returning a one-line diagnostic the operator can act on.  These
tests pin each branch of the matrix.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.git_strategy import GitStrategy


@pytest.fixture
def gs(tmp_path: Path) -> GitStrategy:
    return GitStrategy(project=tmp_path, enabled=True, push=True)


def _mk(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _set_run_git_sequence(gs: GitStrategy, sequence: list[MagicMock]) -> None:
    """Wire `gs._run_git` to return each item of `sequence` on successive
    calls (consumed via MagicMock.side_effect=list per unittest.mock
    contract — no callable wrapper needed, so no unused-argument lint
    noise).
    """
    gs._run_git = MagicMock(side_effect=list(sequence))


# ── _detect_push_ancestor_direction: 4 branches ──────────────────────────────


class TestAncestorDirection:
    def test_no_upstream_returns_no_remote(self, gs: GitStrategy):
        """Detached HEAD or no `branch.X.merge` configured → 'no-remote'."""
        gs._run_git = MagicMock(return_value=_mk(returncode=1))
        assert gs._detect_push_ancestor_direction() == "no-remote"

    def test_ahead_returns_ahead(self, gs: GitStrategy):
        """Remote SHA is an ancestor of local → 'ahead' (FF push OK)."""
        _set_run_git_sequence(gs, [
            _mk(0, "origin/main\n"),  # 1. rev-parse @{u}
            _mk(0, "", ""),            # 2. fetch --no-tags origin
            _mk(0, "remote_sha\n"),    # 3. rev-parse origin/main
            _mk(0, "local_sha\n"),     # 4. rev-parse HEAD (ahead)
            _mk(0, "", ""),            # 5. merge-base returns 0 → 'ahead'
        ])
        assert gs._detect_push_ancestor_direction() == "ahead"

    def test_behind_returns_behind(self, gs: GitStrategy):
        """Local SHA is an ancestor of remote → 'behind' (refuse push).

        Reproduces the exact failure mode that motivated this fix:
        baseline replay (local reset to an old SHA) + upstream moved
        forward during the replay window.
        """
        _set_run_git_sequence(gs, [
            _mk(0, "origin/main\n"),
            _mk(0, "", ""),
            _mk(0, "remote_sha\n"),
            _mk(0, "local_sha\n"),
            _mk(1, "", ""),  # remote NOT ancestor of local
            _mk(0, "", ""),  # local IS ancestor of remote → 'behind'
        ])
        assert gs._detect_push_ancestor_direction() == "behind"

    def test_diverged_returns_diverged(self, gs: GitStrategy):
        """Neither is ancestor of the other → 'diverged' (refuse push)."""
        _set_run_git_sequence(gs, [
            _mk(0, "origin/main\n"),
            _mk(0, "", ""),
            _mk(0, "remote_sha\n"),
            _mk(0, "local_sha\n"),
            _mk(1, "", ""),  # remote NOT ancestor of local
            _mk(1, "", ""),  # local NOT ancestor of remote
        ])
        assert gs._detect_push_ancestor_direction() == "diverged"

    def test_identical_sha_returns_ahead(self, gs: GitStrategy):
        """Remote == local SHA → 'ahead' (no-op push is safe)."""
        _set_run_git_sequence(gs, [
            _mk(0, "origin/main\n"),
            _mk(0, "", ""),
            _mk(0, "same_sha\n"),
            _mk(0, "same_sha\n"),
        ])
        assert gs._detect_push_ancestor_direction() == "ahead"

    def test_fetch_failure_does_not_raise(self, gs: GitStrategy):
        """Network failure on `fetch` is best-effort; fall back to
        stale-tracking-ref comparison rather than raise."""
        _set_run_git_sequence(gs, [
            _mk(0, "origin/main\n"),
            _mk(1, "", "could not fetch\n"),  # fetch fails (offline)
            _mk(0, "remote_sha\n"),
            _mk(0, "local_sha\n"),
            _mk(0, "", ""),  # remote ancestor of local → 'ahead'
        ])
        assert gs._detect_push_ancestor_direction() == "ahead"


# ── _commit_and_push: end-to-end behavior per direction ──────────────────────


class TestCommitAndPushAncestorGuards:
    """Pin the early-return contract: when behind/diverged,
    `_run_git('push')` MUST NOT be called."""

    def _setup_recording_push(self, gs: GitStrategy):
        """Commit succeeds; record whether `push` was invoked.

        Returns (gs, push_calls) where push_calls is a closure-local
        list recording every `git push` invocation. Using a local
        list (not an instance attribute) keeps Pyright / lint happy.
        """
        gs._commit = MagicMock(return_value=True)
        push_calls: list[tuple] = []
        original_run_git = gs._run_git

        def tracking_run_git(*args, **kwargs):
            _ = kwargs
            if args and args[0] == "push":
                push_calls.append(args)
                return _mk(0, "", "")  # harmless success
            return original_run_git(*args, **kwargs)

        gs._run_git = MagicMock(side_effect=tracking_run_git)
        return gs, push_calls

    def test_behind_skips_push_and_returns_false(self, gs, capfd):
        gs, push_calls = self._setup_recording_push(gs)
        gs._detect_push_ancestor_direction = MagicMock(return_value="behind")
        result = gs._commit_and_push("msg")
        assert result is False
        assert push_calls == [], (
            "behind must short-circuit before invoking git push"
        )
        out = capfd.readouterr().out
        assert "REFUSE" in out
        # Actionable hint must mention both rebase and merge options.
        assert "rebase" in out
        assert "merge" in out

    def test_diverged_skips_push_and_returns_false(self, gs, capfd):
        gs, push_calls = self._setup_recording_push(gs)
        gs._detect_push_ancestor_direction = MagicMock(return_value="diverged")
        result = gs._commit_and_push("msg")
        assert result is False
        assert push_calls == []
        out = capfd.readouterr().out
        assert "REFUSE" in out
        assert "diverged" in out

    def test_ahead_proceeds_with_push(self, gs, capfd):
        gs, push_calls = self._setup_recording_push(gs)
        gs._detect_push_ancestor_direction = MagicMock(return_value="ahead")
        result = gs._commit_and_push("msg")
        assert result is True
        assert len(push_calls) == 1
        out = capfd.readouterr().out
        assert "pushed" in out

    def test_no_remote_proceeds_with_push(self, gs):
        """First push / detached HEAD: no upstream tracking, but we
        still attempt the push (caller's `git push` decides if it
        succeeds or surfaces its own error)."""
        gs, push_calls = self._setup_recording_push(gs)
        gs._detect_push_ancestor_direction = MagicMock(return_value="no-remote")
        result = gs._commit_and_push("msg")
        assert result is True
        assert len(push_calls) == 1

    def test_push_disabled_short_circuits(self, gs):
        """When push=False (CI runs with --no-push), the ancestor
        check is preserved only when push would actually happen —
        keeping the commit-only hot path free of subprocess noise.
        """
        gs = GitStrategy(project=gs.project, enabled=True, push=False)
        gs._commit = MagicMock(return_value=True)
        gs._detect_push_ancestor_direction = MagicMock(return_value="behind")
        result = gs._commit_and_push("msg")
        # The fix preserves the existing skip-when-push-disabled contract.
        assert result is True
