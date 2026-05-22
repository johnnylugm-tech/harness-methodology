"""
Unit tests for core/quality_gate/confidence_scorer.py

Tests are fully offline — subprocess calls are monkeypatched.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.quality_gate.confidence_scorer import (
    AUTO_APPROVE_P1P2_THRESHOLD,
    _score_artifact_completeness,
    _score_linting,
    _score_security,
    _score_test_coverage,
    _score_test_pass_rate,
    _score_traceability,
    _score_type_safety,
    compute_confidence,
    format_confidence_report,
    should_auto_approve_p1p2,
)


# ── C1: artifact_completeness ────────────────────────────────────────────────

class TestArtifactCompleteness:
    def test_all_present_returns_100(self, tmp_path):
        # Create P1 artifacts
        (tmp_path / "01-requirements").mkdir(parents=True)
        for name in ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md"]:
            (tmp_path / "01-requirements" / name).write_text("content", encoding="utf-8")

        score, detail = _score_artifact_completeness(tmp_path, phase=1)
        assert score == pytest.approx(100.0)
        assert "3/3" in detail

    def test_partial_returns_proportional(self, tmp_path):
        # Only 1 of 3 P1 artifacts
        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text("content", encoding="utf-8")

        score, detail = _score_artifact_completeness(tmp_path, phase=1)
        assert score == pytest.approx(100 / 3, abs=1)
        assert "1/3" in detail

    def test_empty_file_not_counted(self, tmp_path):
        (tmp_path / "01-requirements").mkdir(parents=True)
        # Empty file should not count as present
        (tmp_path / "01-requirements" / "SRS.md").write_text("", encoding="utf-8")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("content", encoding="utf-8")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("content", encoding="utf-8")

        score, detail = _score_artifact_completeness(tmp_path, phase=1)
        assert score == pytest.approx(200 / 3, abs=1)   # 2/3
        assert "2/3" in detail

    def test_phase_with_all_artifacts_present_returns_100(self, tmp_path):
        # Phase 3 (IMPLEMENT) requires src + tests dirs; both present → 100%
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        score, detail = _score_artifact_completeness(tmp_path, phase=3)
        assert score == pytest.approx(100.0)


# ── C2: test_coverage ────────────────────────────────────────────────────────

class TestTestCoverage:
    def test_reads_cached_coverage_json(self, tmp_path):
        cov = tmp_path / "coverage.json"
        cov.write_text(json.dumps({"totals": {"percent_covered": 87.5}}), encoding="utf-8")
        score, detail = _score_test_coverage(tmp_path)
        assert score == pytest.approx(87.5)
        assert "cached" in detail

    def test_reads_percent_covered_display_fallback(self, tmp_path):
        """Handles pytest-cov >= 4.x 'percent_covered_display' string key."""
        cov = tmp_path / "coverage.json"
        cov.write_text(json.dumps({"totals": {"percent_covered_display": "72.5%"}}), encoding="utf-8")
        score, detail = _score_test_coverage(tmp_path)
        assert score == pytest.approx(72.5)
        assert "cached" in detail

    def test_reads_covered_lines_fallback(self, tmp_path):
        """Falls back to covered_lines/num_statements when no percent key present."""
        cov = tmp_path / "coverage.json"
        cov.write_text(
            json.dumps({"totals": {"covered_lines": 80, "num_statements": 100}}),
            encoding="utf-8",
        )
        score, detail = _score_test_coverage(tmp_path)
        assert score == pytest.approx(80.0)
        assert "cached" in detail

    def test_returns_none_on_tool_missing(self, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            score, detail = _score_test_coverage(tmp_path)
        assert score is None

    def test_returns_none_on_timeout(self, tmp_path):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 30)):
            score, detail = _score_test_coverage(tmp_path)
        assert score is None


# ── C3: linting ─────────────────────────────────────────────────────────────

class TestLinting:
    def test_zero_violations_returns_100(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            score, detail = _score_linting(tmp_path)
        assert score == pytest.approx(100.0)
        assert "0 violation" in detail

    def test_violations_reduce_score(self, tmp_path):
        violations = [{"code": "E501"}] * 10  # 10 violations
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout=json.dumps(violations), stderr=""
            )
            score, detail = _score_linting(tmp_path)
        assert score == pytest.approx(80.0)  # 100 - 10*2

    def test_many_violations_floor_at_zero(self, tmp_path):
        violations = [{"code": "E501"}] * 60  # > 50 → floor
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout=json.dumps(violations), stderr=""
            )
            score, _ = _score_linting(tmp_path)
        assert score == pytest.approx(0.0)

    def test_ruff_not_installed_returns_none(self, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            score, detail = _score_linting(tmp_path)
        assert score is None
        assert "not installed" in detail


# ── C4: type_safety ──────────────────────────────────────────────────────────

class TestTypeSafety:
    def test_zero_errors_returns_100(self, tmp_path):
        pyright_output = json.dumps({"summary": {"errorCount": 0}})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=pyright_output, stderr=""
            )
            score, detail = _score_type_safety(tmp_path)
        assert score == pytest.approx(100.0)

    def test_errors_reduce_score(self, tmp_path):
        pyright_output = json.dumps({"summary": {"errorCount": 5}})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout=pyright_output, stderr=""
            )
            score, _ = _score_type_safety(tmp_path)
        assert score == pytest.approx(90.0)  # 100 - 5*2


# ── C5: test_pass_rate ───────────────────────────────────────────────────────

class TestTestPassRate:
    def test_all_pass_returns_100(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="10 passed in 1.23s",
                stderr="",
            )
            score, detail = _score_test_pass_rate(tmp_path)
        assert score == pytest.approx(100.0)
        assert "10 passed" in detail

    def test_partial_pass_returns_ratio(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="8 passed, 2 failed in 1.23s",
                stderr="",
            )
            score, detail = _score_test_pass_rate(tmp_path)
        assert score == pytest.approx(80.0)

    def test_no_tests_returns_none(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=5,  # pytest exit 5 = no tests collected
                stdout="no tests ran",
                stderr="",
            )
            score, _ = _score_test_pass_rate(tmp_path)
        assert score is None


# ── C6: security ─────────────────────────────────────────────────────────────

class TestSecurity:
    def test_no_findings_returns_100(self, tmp_path):
        bandit_output = json.dumps({"results": []})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=bandit_output, stderr=""
            )
            score, detail = _score_security(tmp_path)
        assert score == pytest.approx(100.0)

    def test_high_severity_deducts_20_each(self, tmp_path):
        bandit_output = json.dumps({
            "results": [
                {"issue_severity": "HIGH"},
                {"issue_severity": "HIGH"},
                {"issue_severity": "MEDIUM"},
            ]
        })
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout=bandit_output, stderr=""
            )
            score, detail = _score_security(tmp_path)
        # 100 - 2*20 - 1*5 = 55
        assert score == pytest.approx(55.0)
        assert "HIGH=2" in detail
        assert "MED=1" in detail

    def test_floor_at_zero(self, tmp_path):
        bandit_output = json.dumps({
            "results": [{"issue_severity": "HIGH"}] * 10  # -200 pts
        })
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout=bandit_output, stderr=""
            )
            score, _ = _score_security(tmp_path)
        assert score == pytest.approx(0.0)


# ── C7: traceability ─────────────────────────────────────────────────────────

class TestTraceability:
    def test_manifest_missing_returns_partial_credit(self, tmp_path):
        score, detail = _score_traceability(tmp_path)
        assert score == pytest.approx(50.0)
        assert "not found" in detail

    def test_no_fr_ids_returns_partial_credit(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": [], "gate_results": {}}),
            encoding="utf-8",
        )
        score, detail = _score_traceability(tmp_path)
        assert score == pytest.approx(50.0)

    def test_all_frs_with_project_level_gate_returns_100(self, tmp_path):
        """gate2/3/4 are project-level: passing any one credits all FRs."""
        (tmp_path / ".methodology").mkdir()
        manifest = {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate2": {"quality_complete": True},
            }
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        score, detail = _score_traceability(tmp_path)
        assert score == pytest.approx(100.0)

    def test_generic_gate1_does_not_credit_all_frs(self, tmp_path):
        """gate1 is per-FR; a generic 'gate1' key must NOT credit every FR."""
        (tmp_path / ".methodology").mkdir()
        manifest = {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1": {"quality_complete": True},  # generic — should be ignored
            }
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        score, detail = _score_traceability(tmp_path)
        # 0 FRs credited → partial credit 40
        assert score == pytest.approx(40.0)

    def test_fr_specific_gate1_credits_only_that_fr(self, tmp_path):
        """gate1_FR-01 credits FR-01 but not FR-02."""
        (tmp_path / ".methodology").mkdir()
        manifest = {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1_FR-01": {"quality_complete": True},
            }
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        score, detail = _score_traceability(tmp_path)
        # 1/2 FRs credited
        assert score == pytest.approx(50.0)
        assert "1/2" in detail


# ── compute_confidence() ─────────────────────────────────────────────────────

class TestComputeConfidence:
    def test_p1_only_uses_doc_metrics(self, tmp_path):
        """Phase 1 should only compute artifact_completeness + traceability."""
        # Populate P1 artifacts
        (tmp_path / "01-requirements").mkdir(parents=True)
        for name in ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md"]:
            (tmp_path / "01-requirements" / name).write_text("x", encoding="utf-8")

        conf = compute_confidence(tmp_path, phase=1)
        assert "composite" in conf
        assert "scores" in conf
        # Code metrics should be skipped for P1
        assert "test_coverage" in conf["skipped"]
        assert "linting" in conf["skipped"]
        assert "security" in conf["skipped"]

    def test_composite_is_between_0_and_100(self, tmp_path):
        conf = compute_confidence(tmp_path, phase=1)
        assert 0.0 <= conf["composite"] <= 100.0

    def test_skipped_metrics_dont_crash(self, tmp_path):
        """All tools unavailable → skipped, composite falls back gracefully."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            conf = compute_confidence(tmp_path, phase=6)
        # Should not raise; composite may be 0 or partial
        assert isinstance(conf["composite"], float)

    def test_format_confidence_report_is_string(self, tmp_path):
        conf = compute_confidence(tmp_path, phase=1)
        report = format_confidence_report(conf)
        assert isinstance(report, str)
        assert "composite" in report.lower()


# ── should_auto_approve_* ────────────────────────────────────────────────────

class TestAutoApproveThresholds:
    def test_p1p2_above_threshold_approved(self):
        conf = {"composite": AUTO_APPROVE_P1P2_THRESHOLD + 0.1, "scores": {}, "skipped": []}
        assert should_auto_approve_p1p2(conf) is True

    def test_p1p2_at_threshold_approved(self):
        conf = {"composite": AUTO_APPROVE_P1P2_THRESHOLD, "scores": {}, "skipped": []}
        assert should_auto_approve_p1p2(conf) is True

    def test_p1p2_below_threshold_blocked(self):
        conf = {"composite": AUTO_APPROVE_P1P2_THRESHOLD - 0.1, "scores": {}, "skipped": []}
        assert should_auto_approve_p1p2(conf) is False

