"""Regression: test-dir resolution must honor the phase-dir layout.

E2E framework bug #3 (integration-test run, 2026-06-12): projects that keep
tests under 03-development/tests/ (taskq layout) had

  1. trace 4c (NFR→test) permanently at 0% — spec_tracking_checker scanned
     the hardcoded <project>/tests/ (Round 117: 4c reads delivery through
     spec_coverage, which scans both roots),
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
from tests.support.nfr_project import make_nfr_project


def _seed_project(root: Path, tests_rel: str) -> None:
    make_nfr_project(root, {"NFR-01": ["AC-N1.1"], "NFR-02": ["AC-N2.1"]},
                     [("test_nfr01_latency", "AC-N1.1"), ("test_nfr02_injection", "AC-N2.1")],
                     delivered=["test_nfr01_latency", "test_nfr02_injection"], tests_rel=tests_rel)


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
        result = compute_trace_dimension(tmp_path, gate=3)
        assert result["4c_nfr_to_test_pct"] == 100.0
        assert result["nfr_untested"] == []

    def test_4c_finds_nfr_tests_in_root_layout(self, tmp_path: Path):
        _seed_project(tmp_path, "tests")
        result = compute_trace_dimension(tmp_path, gate=3)
        assert result["4c_nfr_to_test_pct"] == 100.0

    def test_4c_zero_when_no_nfr_referenced(self, tmp_path: Path):
        _seed_project(tmp_path, "03-development/tests")
        (tmp_path / "03-development" / "tests" / "test_nfr.py").write_text(
            "def test_unrelated():\n    assert True\n", encoding="utf-8"
        )  # neither declared case was written
        result = compute_trace_dimension(tmp_path, gate=3)
        assert result["4c_nfr_to_test_pct"] == 0.0
        assert result["nfr_untested"] == ["NFR-01", "NFR-02"]
