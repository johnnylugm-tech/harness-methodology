"""Tests for harness/git_strategy.py — gate-aligned git commit + push strategy."""

import os
import pytest
from pathlib import Path
from harness.git_strategy import GitStrategy


class TestGitStrategyDisabled:
    def test_disabled_by_flag(self, tmp_path):
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs.enabled is False

    def test_disabled_by_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_NO_GIT", "1")
        gs = GitStrategy(tmp_path)
        assert gs.enabled is False

    def test_enabled_by_default(self, tmp_path):
        gs = GitStrategy(tmp_path)
        assert gs.enabled is True

    def test_disabled_push_false(self, tmp_path):
        gs = GitStrategy(tmp_path, push=False)
        assert gs.push is False

    def test_ensure_gitignore_disabled(self, tmp_path):
        gs = GitStrategy(tmp_path, enabled=False)
        gs.ensure_gitignore()  # should not raise

    def test_commit_fr_gate1_disabled(self, tmp_path):
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs.commit_fr_gate1("FR-01", 85.0, 3) is True

    def test_all_push_methods_disabled(self, tmp_path):
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs.commit_and_push_p1(["FR-01"]) is True
        assert gs.commit_and_push_p2(["FR-01"]) is True
        assert gs.commit_and_push_p3_mid(1, 2, ["FR-01"]) is True
        assert gs.commit_and_push_p3_pre_ssi(["FR-01"]) is True
        assert gs.commit_and_push_gate(2, 3, 85.0) is True
        assert gs.commit_and_push_p5_baseline() is True
        assert gs.commit_and_push_p7() is True
        assert gs.commit_and_push_p8() is True
        assert gs.commit_and_push_final([7, 8]) is True


class TestFrSummary:
    def test_short_list(self):
        assert GitStrategy._fr_summary(["FR-01", "FR-02"]) == "FR-01,FR-02"

    def test_long_list_truncated(self):
        ids = [f"FR-{i:02d}" for i in range(1, 11)]
        result = GitStrategy._fr_summary(ids)
        assert "FR-01" in result
        assert "+5" in result
        assert result.endswith("+5")


class TestCp:
    def test_format(self):
        gs = GitStrategy(Path("/tmp"))
        result = gs._cp("P1-exit")
        assert result.startswith("P1-exit-")
        assert len(result.split("-")) >= 3  # P1-exit-YYYYMMDD
