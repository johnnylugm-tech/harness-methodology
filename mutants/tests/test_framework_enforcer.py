"""Tests for framework_enforcer module."""

from unittest.mock import patch

import pytest

from enforcement.framework_enforcer import (
    EnforcementResult,
    FrameworkEnforcer,
)


class TestEnforcementResult:
    def test_add_violation(self):
        r = EnforcementResult()
        r.add_violation("bad thing", "fix it")
        assert len(r.violations) == 1
        assert r.violations[0] == ("bad thing", "fix it")

    def test_add_warning(self):
        r = EnforcementResult()
        r.add_warning("heads up")
        assert len(r.warnings) == 1
        assert r.warnings[0][0] == "heads up"


class TestFrameworkEnforcer:
    def test_init_defaults(self):
        fe = FrameworkEnforcer()
        assert fe.phase == 1

    def test_init_with_root(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        assert fe.phase == 3

    def test_check_constitution(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_constitution()
        assert "score" in result

    def test_check_coverage_threshold_not_found(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        result = fe.check_coverage_threshold()
        assert result["passed"] is False
        assert result["threshold"] == 70

    def test_run_warn_level(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.run(level="WARN")
        assert isinstance(result, EnforcementResult)

    def test_run_with_exit_pass(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        with patch.object(fe, "run") as mock_run:
            mock_result = EnforcementResult()
            mock_result.passed = True
            mock_run.return_value = mock_result
            assert fe.run_with_exit(level="BLOCK") == 0

    def test_run_with_exit_fail(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        with patch.object(fe, "run") as mock_run:
            mock_result = EnforcementResult()
            mock_result.add_violation("fail")
            mock_run.return_value = mock_result
            assert fe.run_with_exit(level="ALL") == 1

    def test_check_decision_framework(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_decision_framework()
        assert isinstance(result, dict)

    def test_check_enhanced_checklist(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_enhanced_checklist()
        assert isinstance(result, dict)

    def test_check_enhanced_checklist_phase5(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=5)
        result = fe.check_enhanced_checklist()
        assert result["exists"] is False

    def test_check_enhanced_checklist_found(self, tmp_path):
        (tmp_path / "CHECKLIST.md").write_text("# Checklist")
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=5)
        result = fe.check_enhanced_checklist()
        assert result["exists"] is True

    def test_check_constitution_docs_not_found(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_constitution()
        if "error" in result:
            assert not result["passed"]

    def test_check_constitution_phase2(self, tmp_path):
        (tmp_path / "docs").mkdir()
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=2)
        result = fe.check_constitution()
        assert "score" in result

    def test_check_coverage_threshold_parse_error(self, tmp_path):
        (tmp_path / "coverage.xml").write_text("<not-xml>")
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        result = fe.check_coverage_threshold()
        assert result["passed"] is False

    def test_check_coverage_threshold_passes(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(
            '<?xml version="1.0"?><coverage line-rate="0.85"></coverage>'
        )
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        result = fe.check_coverage_threshold()
        assert result["passed"] is True
        assert result["coverage"] == pytest.approx(85.0)
        assert result["threshold"] == 70

    def test_check_traceability_not_found(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_traceability_matrix()
        assert result["exists"] is False
        assert result["complete"] is False

    def test_check_traceability_with_file(self, tmp_path):
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text(
            "src/module.py ✅\nsrc/other.py ❌\n"
        )
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_traceability_matrix()
        assert result["exists"] is True
        assert result["total"] == 2
        assert result["completed"] == 1

    def test_check_aspice_completeness_phase1(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_aspice_completeness()
        assert "missing_docs" in result
        assert "found" in result["phase_coverage"]

    def test_check_aspice_completeness_phase3_no_mandatory_artifacts(self, tmp_path):
        """P3 has no mandatory document artifacts — completeness depends on P1+P2 only."""
        for d in ["01-requirements", "02-architecture", "03-development"]:
            (tmp_path / d).mkdir(parents=True)
        (tmp_path / "01-requirements/SRS.md").write_text("# SRS")
        (tmp_path / "02-architecture/SAD.md").write_text("# SAD")
        # P3 dir exists but has no mandatory artifact files — that's fine
        (tmp_path / "03-development/src").mkdir()
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        result = fe.check_aspice_completeness()
        assert result["complete"] is True
        assert len(result["missing_docs"]) == 0

    def test_generate_aspice_report_produces_sections(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        with patch.object(fe, "check_phase_traceability") as mock_trace, \
             patch.object(fe, "check_aspice_completeness") as mock_aspice, \
             patch.object(fe, "check_constitution") as mock_const, \
             patch.object(fe, "check_spec_tracking") as mock_spec:
            mock_trace.return_value = {
                "stats": {"total": 2, "verified": 1, "missing": 1},
                "verified_phases": ["specify -> plan"],
                "missing_links": ["plan -> implement"],
                "all_verified": False,
            }
            mock_aspice.return_value = {
                "complete": False,
                "missing_docs": ["Phase 3 (IMPLEMENT)/03-development/IMPLEMENTATION.md"],
                "phase_coverage": {"total_phases": 8, "phases_with_docs": 2, "found": 2},
            }
            mock_const.return_value = {"score": 75, "passed": True}
            mock_spec.return_value = {"completeness": 90, "complete": True, "exists": True}
            report = fe.generate_aspice_report()
            assert "ASPICE TRACEABILITY REPORT" in report
            assert "Constitution Score" in report
            assert "Specification Tracking" in report

    def _mock_spec_checker(self, fe):
        from unittest.mock import MagicMock
        fe._spec_checker = MagicMock()
        fe._spec_checker.run_enforcement.return_value = {"exists": True, "completeness": 95, "complete": True}

    def test_run_block_level_phase1(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        self._mock_spec_checker(fe)
        result = fe.run(level="BLOCK")
        assert isinstance(result, EnforcementResult)
        assert "SPEC_TRACKING" in result.block_checks
        assert "CONSTITUTION_SCORE" in result.block_checks
        assert "ASPICE_PHASE_TRACE" in result.block_checks
        assert result.block_checks["ASPICE_PHASE_TRACE"] is True

    def test_run_block_level_phase3(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
        self._mock_spec_checker(fe)
        with patch.object(fe, "check_phase_traceability") as mock_trace, \
             patch.object(fe, "check_aspice_completeness") as mock_aspice:
            mock_trace.return_value = {"all_verified": True, "verified_phases": [], "missing_links": [], "stats": {"total": 0, "verified": 0, "missing": 0}}
            mock_aspice.return_value = {"complete": True, "missing_docs": [], "phase_coverage": {"total_phases": 8, "phases_with_docs": 0, "found": 0}}
            result = fe.run(level="BLOCK")
        assert "COVERAGE_THRESHOLD" in result.block_checks

    def test_run_block_level_phase2_traceability(self, tmp_path):
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=2)
        self._mock_spec_checker(fe)
        with patch.object(fe, "check_phase_traceability") as mock_trace, \
             patch.object(fe, "check_aspice_completeness") as mock_aspice:
            mock_trace.return_value = {"all_verified": True, "verified_phases": [], "missing_links": [], "stats": {"total": 0, "verified": 0, "missing": 0}}
            mock_aspice.return_value = {"complete": True, "missing_docs": [], "phase_coverage": {"total_phases": 8, "phases_with_docs": 0, "found": 0}}
            result = fe.run(level="BLOCK")
        assert "ASPICE_PHASE_TRACE" in result.block_checks
