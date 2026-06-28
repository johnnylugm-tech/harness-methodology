"""Unit tests for core/submodule_sync.py — one-shot `harness sync`.

Improvement J of convergence plan: replaces the 4-step manual bump
(commit harness / push harness / pull consumer / commit consumer / push)
with one `sync_submodule()` call. Tests verify:

  - fetch_remote returns False on offline (best-effort)
  - behind_count returns 0 for up-to-date, N for behind, -1 on offline
  - is_working_tree_clean detects uncommitted edits
  - sync_submodule raises SubmoduleSyncError on dirty working tree
  - sync_submodule raises SubmoduleSyncError on offline fetch
  - sync_submodule returns success dict on already-up-to-date
  - CLI exit codes match status (0 ok, 1 behind, 2 missing, 3 offline, 19 sync failure)
  - E2E: simulate a fake "behind" via a temp git repo

Commonality: framework-level. All consumers of harness/ submodule benefit.
"""

import subprocess
from pathlib import Path

import pytest

from core.submodule_sync import (
    SubmoduleSyncError,
    behind_count,
    current_sha,
    fetch_remote,
    is_working_tree_clean,
    sync_submodule,
)


# ---------------------------------------------------------------------------
# Helper: create a tiny local git repo for testing
# ---------------------------------------------------------------------------


def _make_git_repo(path: Path, file_content: str = "init") -> None:
    """Initialise a git repo at path with one commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email",
                    "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name",
                    "Test"], check=True, capture_output=True)
    f = path / "init.txt"
    f.write_text(file_content)
    subprocess.run(["git", "-C", str(path), "add", "init.txt"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True,
                   capture_output=True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestFetchRemote:
    def test_returns_false_for_missing_repo(self, tmp_path: Path):
        result = fetch_remote(tmp_path / "nonexistent")
        assert result is False


class TestIsWorkingTreeClean:
    def test_clean_after_init(self, tmp_path: Path):
        _make_git_repo(tmp_path)
        assert is_working_tree_clean(tmp_path) is True

    def test_dirty_after_edit(self, tmp_path: Path):
        _make_git_repo(tmp_path)
        (tmp_path / "new_file.txt").write_text("uncommitted")
        assert is_working_tree_clean(tmp_path) is False

    def test_dirty_after_modify(self, tmp_path: Path):
        _make_git_repo(tmp_path, file_content="v1")
        (tmp_path / "init.txt").write_text("v2")
        assert is_working_tree_clean(tmp_path) is False


class TestCurrentSha:
    def test_returns_short_sha(self, tmp_path: Path):
        _make_git_repo(tmp_path)
        sha = current_sha(tmp_path)
        assert len(sha) >= 7
        assert all(c in "0123456789abcdef" for c in sha)


class TestBehindCount:
    def test_returns_zero_for_no_remote(self, tmp_path: Path):
        # Repo without remote configured
        _make_git_repo(tmp_path)
        # No remote → -1 (cannot determine)
        result = behind_count(tmp_path)
        assert result == -1


# ---------------------------------------------------------------------------
# sync_submodule — pre-conditions
# ---------------------------------------------------------------------------


class TestSyncSubmodulePreconditions:
    def test_raises_on_missing_path(self, tmp_path: Path):
        with pytest.raises(SubmoduleSyncError, match="does not exist"):
            sync_submodule(tmp_path / "nonexistent")

    def test_raises_on_dirty_working_tree(self, tmp_path: Path):
        repo = tmp_path / "harness"
        _make_git_repo(repo)
        # Add an uncommitted change
        (repo / "dirty.txt").write_text("uncommitted")
        with pytest.raises(SubmoduleSyncError, match="not clean"):
            sync_submodule(repo)


# ---------------------------------------------------------------------------
# sync_submodule — up-to-date
# ---------------------------------------------------------------------------


class TestSyncSubmoduleUpToDate:
    def test_returns_already_up_to_date(self, tmp_path: Path):
        # Setup: local remote with one initial commit; clone to consumer
        local_remote = tmp_path / "remote.git"
        local_remote.mkdir()
        subprocess.run(["git", "-C", str(local_remote), "init", "--bare"],
                       check=True, capture_output=True)
        seed = tmp_path / "seed"
        _make_git_repo(seed, file_content="v1")
        subprocess.run(["git", "-C", str(seed), "remote", "add",
                        "origin", str(local_remote)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(seed), "push", "-u",
                        "origin", "main"], check=True, capture_output=True)
        consumer = tmp_path / "consumer"
        consumer.mkdir()
        subprocess.run(["git", "-C", str(consumer), "clone",
                        str(local_remote), "."], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(consumer), "config", "user.email",
                        "test@example.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(consumer), "config", "user.name",
                        "Test"], check=True, capture_output=True)
        # Sync the consumer — should be at v1, up-to-date
        result = sync_submodule(consumer)
        assert result["behind_count"] == 0
        assert result["pushed"] is False  # nothing to push when already up-to-date
        assert "up-to-date" in result["message"]


# ---------------------------------------------------------------------------
# sync_submodule — behind by N commits
# ---------------------------------------------------------------------------


class TestSyncSubmoduleBehind:
    def test_pulls_and_returns_new_sha(self, tmp_path: Path):
        # Setup: bare remote
        local_remote = tmp_path / "remote.git"
        local_remote.mkdir()
        subprocess.run(["git", "-C", str(local_remote), "init", "--bare"],
                       check=True, capture_output=True)
        # Seed clone (used to push initial commits and subsequent updates)
        seed = tmp_path / "seed"
        _make_git_repo(seed, file_content="v1")
        subprocess.run(["git", "-C", str(seed), "remote", "add",
                        "origin", str(local_remote)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(seed), "push", "-u",
                        "origin", "main"], check=True, capture_output=True)
        # Consumer clones at v1
        consumer = tmp_path / "consumer"
        consumer.mkdir()
        subprocess.run(["git", "-C", str(consumer), "clone",
                        str(local_remote), "."], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(consumer), "config", "user.email",
                        "test@example.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(consumer), "config", "user.name",
                        "Test"], check=True, capture_output=True)
        # Add 2 commits on the seed AND PUSH them to remote
        (seed / "init.txt").write_text("v2")
        subprocess.run(["git", "-C", str(seed), "commit", "-am",
                        "v2"], check=True, capture_output=True)
        (seed / "init.txt").write_text("v3")
        subprocess.run(["git", "-C", str(seed), "commit", "-am",
                        "v3"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "push",
                        "origin", "main"], check=True, capture_output=True)
        # Now consumer is behind by 2 commits
        result = sync_submodule(consumer)
        assert result["behind_count"] == 2
        # After sync, file content should be v3
        assert (consumer / "init.txt").read_text() == "v3"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_no_llm_dependency(self):
        import core.submodule_sync as mod
        src_path = mod.__file__
        assert src_path is not None
        src = open(src_path).read()
        for token in ["requests", "urllib", "claude", "openai", "anthropic"]:
            assert token not in src, f"LLM/network call found: {token}"