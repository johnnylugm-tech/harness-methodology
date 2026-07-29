from unittest.mock import patch, MagicMock

from enforcement.framework_enforcer import EnforcementResult, FrameworkEnforcer

def test_enforcement_result_to_fix_context_empty():
    r = EnforcementResult()
    ctx = r.to_fix_context()
    assert ctx["problem_type"] == "low_constitution_score"
    assert ctx["severity"] == "medium"

def test_enforcement_result_to_fix_context_with_problem_type():
    r = EnforcementResult()
    r.add_violation("msg", "fix", problem_type="spec_missing")
    ctx = r.to_fix_context()
    assert ctx["problem_type"] == "spec_missing"
    assert ctx["severity"] == "critical"

def test_check_constitution_exception(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
    with patch("enforcement.framework_enforcer.Path.exists", return_value=True):
        with patch("core.quality_gate.constitution.run_constitution_check", side_effect=Exception("mocked error")):
            result = fe.check_constitution()
            assert result["passed"] is False
            assert "mocked error" in result["error"]

def _measured(coverage: float):
    """A SuiteResult standing in for one shared suite execution (Round 25)."""
    from core.quality_gate.test_suite_run import SuiteResult

    return SuiteResult(
        passed=True, coverage=coverage, test_target="tests", cov_target="src",
        returncode=0, output="", ran=True,
    )


def test_check_coverage_threshold_success(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
    with patch("core.quality_gate.test_suite_run.run_suite", return_value=_measured(85.0)):
        result = fe.check_coverage_threshold()
    assert result["passed"] is True
    assert result["coverage"] == 85.0
    assert result["threshold"] == 70

def test_check_coverage_threshold_fail(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=4)
    with patch("core.quality_gate.test_suite_run.run_suite", return_value=_measured(75.0)):
        result = fe.check_coverage_threshold()
    assert result["passed"] is False
    assert result["coverage"] == 75.0
    assert result["threshold"] == 80

def test_check_traceability_matrix_phase3_logic(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    matrix = tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md"
    # Phase <= 3: completeness = (total - failed) / total
    matrix.write_text("src/app.py ❌\nsrc/util.py\nsrc/other.py ✅\n")
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=3)
    result = fe.check_traceability_matrix()
    # total = 3, failed = 1. compliant = 2. completeness = 2/3 = 66.6%
    assert result["total"] == 3
    assert abs(result["completeness"] - 66.66) < 0.1
    assert len(result["missing_constitution"]) == 1

def test_check_traceability_matrix_phase4_logic(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    matrix = tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md"
    # Phase >= 4: completeness = (completed - failed) / total
    matrix.write_text("src/app.py ❌\nsrc/util.py\nsrc/other.py ✅\n")
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=4)
    result = fe.check_traceability_matrix()
    # total = 3, completed = 1, failed = 1. compliant = 0. completeness = 0%
    assert result["total"] == 3
    assert result["completeness"] == 0.0

def test_check_phase_traceability_success(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=4)
    with patch("core.quality_gate.phase_artifact_enforcer.PhaseArtifactRegistry.verify_phase_link") as mock_verify:
        # mock verify_phase_link to pass
        mock_link = MagicMock()
        mock_link.passed = True
        mock_verify.return_value = mock_link
        
        result = fe.check_phase_traceability()
        assert result["all_verified"] is True
        assert len(result["missing_links"]) == 0

def test_check_phase_traceability_failure(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=4)
    with patch("core.quality_gate.phase_artifact_enforcer.PhaseArtifactRegistry.verify_phase_link") as mock_verify:
        # mock verify_phase_link to fail
        mock_link = MagicMock()
        mock_link.passed = False
        mock_verify.return_value = mock_link
        
        result = fe.check_phase_traceability()
        assert result["all_verified"] is False
        assert len(result["missing_links"]) > 0

def test_check_aspice_completeness_missing(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=8)
    result = fe.check_aspice_completeness()
    assert result["complete"] is False
    assert len(result["missing_docs"]) > 0

def test_run_block_spec_tracking_violation(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
    fe._spec_checker = MagicMock()
    fe._spec_checker.run_enforcement.return_value = {"exists": False}
    with patch.object(fe, "check_constitution", return_value={"passed": True}), \
         patch.object(fe, "check_aspice_completeness", return_value={"complete": True}), \
         patch.object(fe, "check_traceability_matrix", return_value={"exists": True, "complete": True}):
        result = fe.run(level="BLOCK")
        assert any("SPEC_TRACKING.md does not exist" in v[0] for v in result.violations)
        
        fe._spec_checker.run_enforcement.return_value = {"exists": True, "completeness": 50}
        result = fe.run(level="BLOCK")
        assert any("50% < 90%" in v[0] for v in result.violations)

def test_run_block_const_error_warning_vs_violation(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
    fe._spec_checker = MagicMock()
    fe._spec_checker.run_enforcement.return_value = {"exists": True, "completeness": 100}
    with patch.object(fe, "check_aspice_completeness", return_value={"complete": True}), \
         patch.object(fe, "check_traceability_matrix", return_value={"exists": True, "complete": True}):
         
        with patch.object(fe, "check_constitution", return_value={"error": "docs/ directory not found"}):
            result = fe.run(level="BLOCK")
            assert len(result.warnings) == 1
            assert "skipped" in result.warnings[0][0]
            assert result.block_checks["CONSTITUTION_SCORE"] is True
            
        with patch.object(fe, "check_constitution", return_value={"error": "Some real exception"}):
            result = fe.run(level="BLOCK")
            assert len(result.violations) > 0
            assert any("real exception" in v[0] for v in result.violations)
            assert result.block_checks["CONSTITUTION_SCORE"] is False

def test_run_block_traceability_incomplete(tmp_path):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=4)
    fe._spec_checker = MagicMock()
    fe._spec_checker.run_enforcement.return_value = {"exists": True, "completeness": 100}
    with patch.object(fe, "check_constitution", return_value={"passed": True}), \
         patch.object(fe, "check_phase_traceability", return_value={"all_verified": True}), \
         patch.object(fe, "check_aspice_completeness", return_value={"complete": True}), \
         patch.object(fe, "check_coverage_threshold", return_value={"passed": True}), \
         patch.object(fe, "check_traceability_matrix", return_value={"exists": True, "complete": False, "missing_tests": ["a"], "missing_constitution": ["b"]}):
        
        result = fe.run(level="BLOCK")
        assert result.passed is False
        assert "1 items missing tests, 1 items failed Constitution" in result.violations[0][0]

def test_run_with_exit_warn_only(tmp_path, capsys):
    fe = FrameworkEnforcer(project_root=str(tmp_path), phase=1)
    with patch.object(fe, "run") as mock_run:
        res = EnforcementResult()
        res.passed = True
        res.add_warning("A warning")
        mock_run.return_value = res
        
        exit_code = fe.run_with_exit("ALL")
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "WARN: A warning" in captured.out
