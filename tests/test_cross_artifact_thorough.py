import os
from unittest.mock import patch, MagicMock

from core.quality_gate.cross_artifact import (
    check_phase_title,
    check_fr_coverage,
    check_coverage_report,
    run_cross_artifact_checks,
)

def test_check_phase_title_file_read_error(tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    # create a directory where a file is expected to cause read_text to throw an exception
    (tmp_path / "04-testing" / "TEST_PLAN.md").mkdir()
    # It should catch the exception and skip, so violations == 0
    violations = check_phase_title(tmp_path, 4)
    assert len(violations) == 0

def test_check_phase_title_no_artifacts_for_phase(tmp_path):
    violations = check_phase_title(tmp_path, 99)
    assert len(violations) == 0

def test_check_fr_coverage_no_results_file(tmp_path):
    violations = check_fr_coverage(tmp_path, 4)
    assert len(violations) == 0

def test_check_fr_coverage_invalid_json_log(tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("FR-01\nFR-02")
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "sessions_spawn.log").write_text("invalid json\n{\"fr_id\": \"FR-01\"}")
    
    violations = check_fr_coverage(tmp_path, 4)
    # FR-02 is claimed but not in log
    assert len(violations) == 1
    assert violations[0]["fr_id"] == "FR-02"

def test_check_fr_coverage_missing_fr_id_in_log(tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("FR-01")
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "sessions_spawn.log").write_text("{\"role\": \"dev\"}\n")
    
    violations = check_fr_coverage(tmp_path, 4)
    assert len(violations) == 1
    assert violations[0]["fr_id"] == "FR-01"

def test_check_fr_coverage_read_error(tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "TEST_RESULTS.md").mkdir()
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "sessions_spawn.log").write_text("")
    violations = check_fr_coverage(tmp_path, 4)
    assert len(violations) == 0

def test_check_coverage_report_not_exist(tmp_path):
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 0

def test_check_coverage_report_no_percentage(tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("No coverage data")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 0

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "1"})
def test_check_coverage_report_subprocess_exception(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    mock_run.side_effect = Exception("error")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 0

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "1"})
def test_check_coverage_report_diff_greater_than_10(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    mock_run.return_value = MagicMock(stdout="TOTAL 100 20 80%", stderr="")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 1
    assert violations[0]["severity"] == "CRITICAL"
    assert "diff=15.0%" in violations[0]["issue"]

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "1"})
def test_check_coverage_report_diff_greater_than_5(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    mock_run.return_value = MagicMock(stdout="TOTAL 100 10 89%", stderr="")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 1
    assert violations[0]["severity"] == "HIGH"
    assert "diff=6.0%" in violations[0]["issue"]

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "0"}, clear=True)
def test_check_coverage_report_fast_path_success(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    (tmp_path / ".coverage").write_text("dummy")
    mock_run.return_value = MagicMock(stdout="82.5\n", stderr="")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 1
    assert violations[0]["severity"] == "CRITICAL"

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "0"}, clear=True)
def test_check_coverage_report_fast_path_no_coverage_file(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    # no .coverage file
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 0

@patch("subprocess.run")
@patch.dict(os.environ, {"HARNESS_CROSS_ARTIFACT_COV": "0"}, clear=True)
def test_check_coverage_report_fast_path_unparseable(mock_run, tmp_path):
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "COVERAGE_REPORT.md").write_text("coverage: 95%")
    (tmp_path / ".coverage").write_text("dummy")
    mock_run.return_value = MagicMock(stdout="unparseable", stderr="")
    violations = check_coverage_report(tmp_path, 4)
    assert len(violations) == 0

def test_run_cross_artifact_checks_phase_3(tmp_path):
    # Phase 3 does not check coverage or FR
    (tmp_path / "03-architecture").mkdir(parents=True)
    result = run_cross_artifact_checks(tmp_path, 3)
    assert result["passed"] is True
    assert result["checks_ran"] == 1

@patch("core.quality_gate.cross_artifact.check_phase_title")
@patch("core.quality_gate.cross_artifact.check_fr_coverage")
@patch("core.quality_gate.cross_artifact.check_coverage_report")
def test_run_cross_artifact_checks_phase_4(mock_cov, mock_fr, mock_title, tmp_path):
    mock_title.return_value = [{"severity": "INFO", "issue": "info issue"}]
    mock_fr.return_value = [{"severity": "HIGH", "issue": "high issue"}]
    mock_cov.return_value = [{"severity": "CRITICAL", "issue": "critical issue"}]
    
    result = run_cross_artifact_checks(tmp_path, 4)
    assert result["passed"] is False
    assert result["checks_ran"] == 3
    assert result["critical_count"] == 1
    assert result["high_count"] == 1
