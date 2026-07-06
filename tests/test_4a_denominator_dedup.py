"""Audit F-2.1 regression test: 4a denominator must dedup FRs in both lists.

An FR with `has_module` (SAD table mapping) but no actual code/test
appears in BOTH `fr_without_code` and `fr_without_test`. The old
formula `total - len(uncoded) - len(untested)` over-subtracts and
yields a negative pct_4a. The fix uses `| active_uncoded ∪ active_untested |`
so each incomplete FR is counted once.
"""
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def has_module_only_repo(tmp_path: Path) -> Path:
    """An FR that has SAD-table module mapping but no code or test file.
    Status = IN_PROGRESS (per build_traceability's has_code OR has_module
    branch). Appears in BOTH fr_without_code AND fr_without_test."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text(
        "| FR | Component |\n"
        "|---|---|\n"
        "| FR-99 | `core/auto_fr99.py` |\n"
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "auto_fr99.py").write_text("def placeholder(): pass\n")
    (tmp_path / "tests").mkdir()
    return tmp_path


def _phase(project: Path, gate: int):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    return compute_trace_dimension


def test_4a_no_longer_negative_for_has_module_only(has_module_only_repo):
    """The old code returned pct_4a = -100.0 for this case. The fix
    must return a non-negative percentage."""
    compute = _phase(has_module_only_repo, 5)
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute(has_module_only_repo, gate=5)
    assert result["4a_fr_to_test_pct"] >= 0, (
        f"4a went negative: {result['4a_fr_to_test_pct']} — denominator dedup regression"
    )
    # 1 active FR, 1 incomplete → complete = 0 → pct_4a = 0
    assert result["4a_fr_to_test_pct"] == 0.0


def test_4a_complete_when_no_gaps(has_module_only_repo):
    """After we add a test stub for FR-99, 4a should be 100%."""
    (has_module_only_repo / "tests" / "test_fr_99.py").write_text('"""[FR-99] stub"""\n')
    compute = _phase(has_module_only_repo, 5)
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute(has_module_only_repo, gate=5)
    assert result["4a_fr_to_test_pct"] == 100.0
