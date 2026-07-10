"""Entry-level and cross-module strays from the old tests/test_harness_cli.py monolith (C1f): fr_num_str re-export, gate1_evidence SHA fallback, spec_coverage git patterns."""

from __future__ import annotations

import subprocess

import pytest
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports


# =============================================================================
# _fr_num_str
# =============================================================================


class TestFrNumStr:
    @pytest.mark.parametrize("fr_id,expected", [
        ("FR-01", "01"),
        ("FR-1", "01"),
        ("fr01", "01"),
        ("FR_12", "12"),
        ("FR-100", "100"),   # 3-digit preserved
        ("NFR-01", "01"),    # NFR prefix
        ("NFR-07", "07"),
        ("TASK-03", "03"),   # TASK prefix
        ("invalid", "invalid"),  # passthrough on parse failure
    ])
    def test_fr_num_str(self, fr_id, expected):
        from core.canonical_form import fr_num_str
        assert fr_num_str(fr_id) == expected


# =============================================================================
# Bug fix: _fr_gate1_commit_sha must fall back to batch "Gate1 PASS" commits
# =============================================================================

class TestFrGate1CommitShaFallback:
    """_fr_gate1_commit_sha must fall back to batch commits when per-FR format missing."""

    def _fake_run_factory(self, per_fr_sha: str, batch_sha: str):
        """Return a subprocess.run replacement: per-FR pattern returns per_fr_sha,
        broad 'Gate1 PASS' pattern returns batch_sha."""

        def fake_run(cmd, **kw):
            ns = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            idx = cmd.index("--grep") + 1 if "--grep" in cmd else -1
            if idx >= 0:
                pattern = cmd[idx]
                if "Gate1 PASS" == pattern:
                    ns.stdout = batch_sha + "\n" if batch_sha else ""
                else:
                    ns.stdout = per_fr_sha + "\n" if per_fr_sha else ""
            return ns

        return fake_run

    def test_per_fr_commit_returned_directly(self, tmp_path, monkeypatch):
        """If per-FR pattern matches, return that SHA without hitting fallback."""
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("abc123", "batch999"))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "abc123"

    def test_fallback_to_batch_commit(self, tmp_path, monkeypatch):
        """If per-FR pattern finds nothing, must return SHA from batch 'Gate1 PASS' grep."""
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", "deadbeef"))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "deadbeef"

    def test_returns_none_when_no_commit(self, tmp_path, monkeypatch):
        """No Gate1 PASS commit of any kind → returns None."""
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", ""))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha is None


# =============================================================================
# Bug A+B fix: phase-scoped fr_gate1_commit_sha lookup
# =============================================================================

class TestFrGate1CommitShaPhaseScoped:
    """When phase= is given, the lookup must be bounded by the phase-scoped
    finalize-gate sentinel and must NOT fall back to the unscoped batch-commit
    grep (which can bind to a different FR's 'Gate1 PASS' commit)."""

    def test_no_sentinel_returns_none_even_with_matching_commit(self, tmp_path, monkeypatch):
        """No finalize-gate sentinel for this phase → provably no Gate 1 PASS
        yet, even if a stale/other commit still matches the grep pattern."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr=""),
        )
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-05", tmp_path, phase=3)
        assert sha is None

    def test_sentinel_present_scopes_lookup_with_since(self, tmp_path, monkeypatch):
        """Sentinel's own write-timestamp bounds the git-log query via --since."""
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha, _finalize_sentinel_path
        sentinel = _finalize_sentinel_path(tmp_path, 1, "FR-05", phase=3)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("2026-07-10T12:00:00+00:00\n", encoding="utf-8")

        seen_cmds = []

        def fake_run(cmd, **kw):
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="cafef00d\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        sha = fr_gate1_commit_sha("FR-05", tmp_path, phase=3)
        assert sha == "cafef00d"
        assert len(seen_cmds) == 1
        assert "--since" in seen_cmds[0]
        assert "2026-07-10T12:00:00+00:00" in seen_cmds[0]

    def test_sentinel_present_no_match_does_not_fall_back_to_other_fr(self, tmp_path, monkeypatch):
        """Phase-scoped miss must return None directly — never degrade to the
        unscoped 'Gate1 PASS' batch-commit fallback used by legacy callers."""
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha, _finalize_sentinel_path
        sentinel = _finalize_sentinel_path(tmp_path, 1, "FR-05", phase=3)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("2026-07-10T12:00:00+00:00\n", encoding="utf-8")

        seen_cmds = []

        def fake_run(cmd, **kw):
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        sha = fr_gate1_commit_sha("FR-05", tmp_path, phase=3)
        assert sha is None
        assert len(seen_cmds) == 1, "must not attempt a second (fallback) git-log call"


# =============================================================================
# _git_test_patterns: symlink-aware test path resolution for git operations
# =============================================================================

class TestGitTestPatterns:

    def test_no_symlink_returns_standard_patterns(self, tmp_path):
        """When tests/ is a regular directory, return only standard patterns."""
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / "tests").mkdir()
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert patterns == ["tests/test_fr01.py", "tests/test_fr1.py"]
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0].name == "tests"

    def test_canonical_layout_directly(self, tmp_path):
        """Bug #130: 03-development/tests/ used directly (no symlink) works."""
        import harness_cli
        can_tests = tmp_path / "03-development" / "tests"
        can_tests.mkdir(parents=True)
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert "03-development/tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr1.py" in patterns
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0] == can_tests

    def test_symlink_adds_resolved_patterns(self, tmp_path):
        """When tests/ → 03-development/tests/, include git-tracked real paths."""
        import harness_cli
        real = tmp_path / "03-development" / "tests"
        real.mkdir(parents=True)
        (tmp_path / "tests").symlink_to(real)
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert "tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr1.py" in patterns
        assert len(patterns) == 4
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0] == real

    def test_symlink_outside_project_ignored(self, tmp_path):
        """Symlink resolving outside project root → ValueError caught, no extra patterns."""
        import tempfile
        outside = Path(tempfile.mkdtemp())
        try:
            (tmp_path / "tests").symlink_to(outside)
            from core.quality_gate.spec_coverage import _git_test_patterns
            patterns = _git_test_patterns(tmp_path, "01", "1")
            assert len(patterns) == 2  # only standard patterns, no crash
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)
