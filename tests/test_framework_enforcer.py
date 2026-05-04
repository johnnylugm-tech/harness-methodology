"""Tests for framework_enforcer module."""

from unittest.mock import patch


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
        fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
        result = fe.check_coverage_threshold()
        assert result["passed"] is False

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
