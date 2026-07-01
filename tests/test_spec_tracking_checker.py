from unittest.mock import patch, MagicMock

from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
from core.quality_gate.spec_tracking_checker import compute_trace_dimension

def test_spec_tracking_checker_not_found(tmp_path):
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert result["complete"] is False
    assert "SPEC_TRACKING.md not found" in result["missing"]

def test_spec_tracking_checker_complete(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    spec_file = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
    content = """
# SPEC_TRACKING

## Core Features
| Spec | Status |
|------|--------|
| FR-01| Done   |

## Update log
| Date | Item |
|------|------|
| 2026 | Init |
"""
    spec_file.write_text(content)
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert result["complete"] is True

def test_spec_tracking_checker_missing_table(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    spec_file = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
    content = """
# SPEC_TRACKING
No tables here.
"""
    spec_file.write_text(content)
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert result["complete"] is False
    assert "Core Features table" in result["missing"]


def test_spec_tracking_checker_run_returns_bool(tmp_path):
    checker = SpecTrackingChecker(tmp_path)
    result = checker.run()
    assert isinstance(result, bool)


def test_spec_tracking_checker_run_enforcement_returns_dict(tmp_path):
    checker = SpecTrackingChecker(tmp_path)
    result = checker.run_enforcement()
    assert isinstance(result, dict)
    assert "complete" in result


def test_completeness_with_spec_file(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    spec_file = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
    content = """
## Core Features
| Spec | Status | Notes |
|------|--------|-------|
FR-01 | Done | ok

## Update log
| Date | Item |
|------|------|
| 2026 | Init |
"""
    spec_file.write_text(content)
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert "complete" in result


def test_compute_trace_dimension_nfr_scan_exception_is_fail_closed(tmp_path):
    """NFR scan exception must set nfr_pct=0.0 and passed=False (fail-closed)."""
    # Minimal mock so 4a/4b succeed; 4c NFR scan raises
    mock_rt = MagicMock()
    mock_rt.requirements = {}
    mock_report = {"completeness": {"missing_mappings": {}}}

    with patch(
        "core.traceability.scanner.check_traceability",
        return_value=(mock_rt, mock_report),
    ), patch(
        "scripts.build_traceability.build_traceability",
        return_value=mock_rt,
    ), patch(
        "core.traceability.scanner.extract_nfr_ids_from_srs",
        return_value=["NFR-01", "NFR-02"],
    ), patch(
        "core.traceability.scanner.scan_test_nfr_coverage",
        side_effect=RuntimeError("malformed SRS"),
    ):
        result = compute_trace_dimension(tmp_path, gate=2)

    assert result["4c_nfr_to_test_pct"] == 0.0, (
        f"Expected nfr_pct=0.0 on scan exception, got {result['4c_nfr_to_test_pct']}"
    )
    assert result["passed"] is False, (
        "Expected passed=False on NFR scan exception (fail-closed), got True"
    )
    assert "4c" in result.get("error", ""), (
        f"Expected '4c' in error field, got: {result.get('error', '')}"
    )
