"""Unit tests for core/submodule_guard.py — SubmoduleGuardError, edit detection,
remote-drift detection, and assert_safe_to_update behavior.

Bug fix E (improvement E2 of plan): `git submodule update --remote` silently
clobbers uncommitted edits in a submodule. These tests verify the new guard
correctly detects modified / staged / untracked files and surfaces them via
the assert-safe / check-edits CLI surface, with no false positives on a clean
submodule.

Commonality: phase-agnostic — guards any git submodule path passed in.
"""

import subprocess
from pathlib import Path

import pytest

from core.submodule_guard import (
    SubmoduleGuardError,
    assert_safe_to_update,
    check_behind_remote,
    check_uncommitted_edits,
    is_submodule,
)


# ---------------------------------------------------------------------------
# Fixtures — fake parent repo + fake harness/ submodule in tmp_path
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in `cwd`; default to check=True so test fails on error."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=check,
    )


def _git_ok(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Like _git but never raises — returns the CompletedProcess so callers
    can inspect returncode when expected (e.g. fetch with no remote)."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True,
    )


@pytest.fixture
def fake_submodule(tmp_path: Path) -> Path:
    """Build a parent repo with .gitmodules + harness/ as a real submodule.

    Layout:
        tmp_path/.gitmodules         (declares harness/ as submodule)
        tmp_path/harness/.git/        (own git dir)
        tmp_path/harness/committed.txt (committed)
    """
    # Parent marker
    (tmp_path / ".gitmodules").write_text(
        "[submodule \"harness\"]\n"
        "\tpath = harness\n"
        "\turl = https://example.invalid/harness.git\n"
        "\tbranch = main\n"
    )

    # harness/ as its own git repo (mimics submodule layout: own .git dir)
    sub = tmp_path / "harness"
    sub.mkdir()
    _git(sub, "init", "-q", "--initial-branch=main")
    _git(sub, "config", "user.email", "test@example.com")
    _git(sub, "config", "user.name", "Test")
    _git(sub, "config", "commit.gpgsign", "false")

    (sub / "committed.txt").write_text("hello\n")
    _git(sub, "add", "committed.txt")
    _git(sub, "commit", "-q", "-m", "initial")

    return sub


# ---------------------------------------------------------------------------
# is_submodule
# ---------------------------------------------------------------------------


class TestIsSubmodule:
    def test_returns_true_for_submodule(self, fake_submodule: Path):
        assert is_submodule(fake_submodule) is True

    def test_returns_false_for_path_without_dot_git(self, tmp_path: Path):
        # tmp_path/.gitmodules exists but harness/ has no .git
        (tmp_path / ".gitmodules").write_text("[submodule \"h\"]\n\tpath = harness\n")
        (tmp_path / "harness").mkdir()
        assert is_submodule(tmp_path / "harness") is False

    def test_returns_false_for_path_without_dot_gitmodules(self, tmp_path: Path):
        # Submodule has its own .git but no parent .gitmodules
        sub = tmp_path / "loner"
        sub.mkdir()
        _git_ok(sub, "init", "-q")
        assert is_submodule(sub) is False


# ---------------------------------------------------------------------------
# check_uncommitted_edits
# ---------------------------------------------------------------------------


class TestCheckUncommittedEdits:
    def test_clean_repo_returns_empty_list(self, fake_submodule: Path):
        assert check_uncommitted_edits(fake_submodule) == []

    def test_detects_modified_file(self, fake_submodule: Path):
        (fake_submodule / "committed.txt").write_text("hello modified\n")
        edits = check_uncommitted_edits(fake_submodule)
        assert Path("committed.txt") in edits

    def test_detects_staged_file(self, fake_submodule: Path):
        new_file = fake_submodule / "staged.txt"
        new_file.write_text("staged\n")
        _git(fake_submodule, "add", "staged.txt")
        edits = check_uncommitted_edits(fake_submodule)
        assert Path("staged.txt") in edits

    def test_detects_untracked_file(self, fake_submodule: Path):
        (fake_submodule / "untracked.txt").write_text("u\n")
        edits = check_uncommitted_edits(fake_submodule)
        assert Path("untracked.txt") in edits

    def test_returns_empty_for_non_submodule(self, tmp_path: Path):
        # Bare dir without .git inside, no .gitmodules above
        plain = tmp_path / "plain"
        plain.mkdir()
        assert check_uncommitted_edits(plain) == []


# ---------------------------------------------------------------------------
# check_behind_remote
# ---------------------------------------------------------------------------


class TestCheckBehindRemote:
    def test_returns_minus_one_when_no_remote(self, fake_submodule: Path):
        # No origin set, fetch fails → -1 (offline behavior)
        behind = check_behind_remote(fake_submodule)
        assert behind == -1

    def test_returns_minus_one_for_non_submodule(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert check_behind_remote(plain) == -1


# ---------------------------------------------------------------------------
# assert_safe_to_update
# ---------------------------------------------------------------------------


class TestAssertSafeToUpdate:
    def test_silent_when_clean(self, fake_submodule: Path):
        # Should not raise
        assert_safe_to_update(fake_submodule)

    def test_raises_with_remediation_on_modified(self, fake_submodule: Path):
        (fake_submodule / "committed.txt").write_text("dirty\n")
        with pytest.raises(SubmoduleGuardError) as exc_info:
            assert_safe_to_update(fake_submodule)
        msg = str(exc_info.value)
        assert "uncommitted edit" in msg
        assert "committed.txt" in msg
        assert "Commit submodule changes first" in msg
        assert "--no-fetch" in msg

    def test_raises_with_more_marker_when_many_edits(self, fake_submodule: Path):
        # Create 7 modified files
        for i in range(7):
            f = fake_submodule / f"f{i}.txt"
            f.write_text(f"content {i}\n")
            _git(fake_submodule, "add", f"f{i}.txt")
            _git(fake_submodule, "commit", "-q", "-m", f"add {i}")
        # Modify 6 of them
        for i in range(6):
            (fake_submodule / f"f{i}.txt").write_text(f"modified {i}\n")
        with pytest.raises(SubmoduleGuardError) as exc_info:
            assert_safe_to_update(fake_submodule)
        assert "(+1 more)" in str(exc_info.value)


# ---------------------------------------------------------------------------
# pre_flight integration
# ---------------------------------------------------------------------------


class TestPreflightIntegration:
    def test_check_submodule_safety_returns_ok_when_clean(self, fake_submodule: Path):
        from core.pre_flight import check_submodule_safety
        ok, diag = check_submodule_safety(fake_submodule)
        assert ok is True
        assert diag == "ok"

    def test_check_submodule_safety_returns_false_when_dirty(self, fake_submodule: Path):
        (fake_submodule / "committed.txt").write_text("dirty\n")
        from core.pre_flight import check_submodule_safety
        ok, diag = check_submodule_safety(fake_submodule)
        assert ok is False
        assert "uncommitted edit" in diag
        assert "committed.txt" in diag

    def test_check_submodule_safety_silent_skip_for_non_submodule(self, tmp_path: Path):
        from core.pre_flight import check_submodule_safety
        plain = tmp_path / "not-a-sub"
        plain.mkdir()
        ok, diag = check_submodule_safety(plain)
        assert ok is True
        assert diag == "not-a-submodule-skip"


# ---------------------------------------------------------------------------
# CLI entry (mocked argv)
# ---------------------------------------------------------------------------


class TestCliEntry:
    def test_check_edits_mode_returns_modified_paths(self, fake_submodule: Path, monkeypatch, capsys):
        (fake_submodule / "committed.txt").write_text("dirty\n")
        monkeypatch.setattr("sys.argv", [
            "core.submodule_guard",
            "--submodule", str(fake_submodule),
            "--mode", "check-edits",
        ])
        from core.submodule_guard import _cli
        rc = _cli()
        assert rc == 0
        out = capsys.readouterr().out
        assert "committed.txt" in out

    def test_assert_safe_mode_blocks_on_dirty(self, fake_submodule: Path, monkeypatch, capsys):
        (fake_submodule / "committed.txt").write_text("dirty\n")
        monkeypatch.setattr("sys.argv", [
            "core.submodule_guard",
            "--submodule", str(fake_submodule),
            "--mode", "assert-safe",
        ])
        from core.submodule_guard import _cli
        rc = _cli()
        assert rc == 1
        err = capsys.readouterr().err
        assert "BLOCKED" in err
        assert "committed.txt" in err

    def test_assert_safe_mode_passes_on_clean(self, fake_submodule: Path, monkeypatch):
        monkeypatch.setattr("sys.argv", [
            "core.submodule_guard",
            "--submodule", str(fake_submodule),
            "--mode", "assert-safe",
        ])
        from core.submodule_guard import _cli
        assert _cli() == 0
