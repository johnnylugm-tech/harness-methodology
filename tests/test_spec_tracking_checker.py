from core.quality_gate.spec_tracking_checker import SpecTrackingChecker

def test_spec_tracking_checker_not_found(tmp_path):
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert result["complete"] is False
    assert "SPEC_TRACKING.md not found" in result["missing"]

def test_spec_tracking_checker_complete(tmp_path):
    spec_file = tmp_path / "SPEC_TRACKING.md"
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
    spec_file = tmp_path / "SPEC_TRACKING.md"
    content = """
# SPEC_TRACKING
No tables here.
"""
    spec_file.write_text(content)
    checker = SpecTrackingChecker(tmp_path)
    result = checker.check_completeness()
    assert result["complete"] is False
    assert "Core Features table" in result["missing"]
