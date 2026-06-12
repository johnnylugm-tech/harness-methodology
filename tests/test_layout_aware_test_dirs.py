"""Regression: test-dir resolution must honor the phase-dir layout.

E2E framework bug #3 (integration-test run, 2026-06-12): projects that keep
tests under 03-development/tests/ (taskq layout) had

  1. trace 4c (NFR→test) permanently at 0% — spec_tracking_checker scanned
     the hardcoded <project>/tests/,
  2. Gate-1 I-2/I-3 per-FR checks silently SKIPPED — the gating
     `(project / "tests").is_dir()` was False even though the layout-aware
     inner checks would have run fine,
  3. the pre-commit trace dirty probe blind to test edits.

All three sites now resolve via ProjectLayout.active_test_dir
(03-development/tests preferred, root tests/ fallback — tts-new layout).
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.spec_tracking_checker import compute_trace_dimension
from core.utils.project_layout import ProjectLayout


def _seed_project(root: Path, tests_rel: str) -> None:
    (root / "01-requirements").mkdir(parents=True)
    (root / "01-requirements" / "SRS.md").write_text(
        "# SRS\n\n### NFR-01: perf\n\n### NFR-02: security\n",
        encoding="utf-8",
    )
    tests = root / tests_rel
    tests.mkdir(parents=True)
    (tests / "test_nfr.py").write_text(
        '"""NFR suite.\n\n[NFR-01] p95 latency\n[NFR-02] injection rejected\n"""\n'
        "def test_nfr01_latency():\n    assert True\n"
        "def test_nfr02_injection():\n    assert True\n",
        encoding="utf-8",
    )


class TestActiveTestDirResolution:
    def test_prefers_phase3_dev_tests(self, tmp_path: Path):
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        assert (
            ProjectLayout(tmp_path).active_test_dir
            == tmp_path / "03-development" / "tests"
        )

    def test_falls_back_to_root_tests(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        assert ProjectLayout(tmp_path).active_test_dir == tmp_path / "tests"


class TestTrace4cLayoutAware:
    def test_4c_finds_nfr_tests_in_phase_dir_layout(self, tmp_path: Path):
        _seed_project(tmp_path, "03-development/tests")
        result = compute_trace_dimension(tmp_path, gate=2)
        assert result["4c_nfr_to_test_pct"] == 100.0
        assert result["nfr_untested"] == []

    def test_4c_finds_nfr_tests_in_root_layout(self, tmp_path: Path):
        _seed_project(tmp_path, "tests")
        result = compute_trace_dimension(tmp_path, gate=2)
        assert result["4c_nfr_to_test_pct"] == 100.0

    def test_4c_zero_when_no_nfr_referenced(self, tmp_path: Path):
        _seed_project(tmp_path, "03-development/tests")
        (tmp_path / "03-development" / "tests" / "test_nfr.py").write_text(
            "def test_unrelated():\n    assert True\n", encoding="utf-8"
        )
        result = compute_trace_dimension(tmp_path, gate=2)
        assert result["4c_nfr_to_test_pct"] == 0.0
        assert result["nfr_untested"] == ["NFR-01", "NFR-02"]
