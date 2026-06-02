"""PR 4: gate trace dimension tests.

Confirms:
  - 4a semantic: 100% over FRs with status ∈ {IN_PROGRESS, VERIFIED}.
    PENDING FRs are NOT in the denominator.
  - 4b semantic: TEST_SPEC → test (delegated to existing _run_spec_coverage_check).
  - merged = min(4a, 4b) — fail-closed.
  - threshold table: 4a=100% at G2/G3/G4, 4b=60/80/90% at G2/G3/G4.
  - active_uncoded / active_untested lists contain only IN_PROGRESS+VERIFIED FRs.
"""
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo: 2 active FRs, 1 pending. Used to test 4a denominator."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text(
        "FR-01: active alpha\n"
        "FR-02: active beta\n"
        "FR-03: pending gamma (not yet started)\n"
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01]"""\n')
    (tmp_path / "core" / "b.py").write_text('"""[FR-02]"""\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    # FR-02 has code but no test (uncoded test, but it IS active)
    # FR-03 has no code, no test (PENDING — not in denominator)
    return tmp_path


# ---------------------------------------------------------------------------
# Threshold table + active-FR filter
# ---------------------------------------------------------------------------

def test_threshold_table_constants():
    from core.quality_gate.spec_tracking_checker import (
        TRACE_THRESHOLDS, SPEC_COV_THRESHOLDS, ACTIVE_STATUSES,
    )
    assert TRACE_THRESHOLDS == {2: 100, 3: 100, 4: 100}
    assert SPEC_COV_THRESHOLDS == {2: 60.0, 3: 80.0, 4: 90.0}
    assert "in_progress" in ACTIVE_STATUSES
    assert "verified" in ACTIVE_STATUSES
    assert "pending" not in ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# 4a: PENDING excluded from denominator
# ---------------------------------------------------------------------------

def test_4a_pending_fr_excluded_from_denominator(fixture_repo):
    """FR-03 is PENDING (no code, no test) but must NOT be in 4a denominator."""
    from core.quality_gate.spec_tracking_checker import (
        _filter_active_frs,
    )
    from scripts.build_traceability import build_traceability

    rt = build_traceability(fixture_repo)
    # sanity: the scanner gives FR-03 in fr_without_code/test (raw)

    # Build a synthetic missing dict mimicking verify_completeness output
    missing = {"fr_without_code": ["FR-02", "FR-03"],
               "fr_without_test": ["FR-02", "FR-03"],
               "fr_without_srs": []}
    active_uncoded, active_untested = _filter_active_frs(rt, missing)
    # Only FR-02 (IN_PROGRESS — has code) is in the active denominator
    assert "FR-02" in active_uncoded
    assert "FR-02" in active_untested
    # FR-03 is PENDING — NOT in active denominator
    assert "FR-03" not in active_uncoded
    assert "FR-03" not in active_untested


def test_4a_pct_uses_active_denominator(fixture_repo):
    """With 2 active FRs and FR-02 missing test, 4a = 50% (1 of 2 complete)."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension

    with patch("harness_cli._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(fixture_repo, gate=2)
    # 2 active FRs: FR-01 has code+test, FR-02 has code but no test.
    # complete = 2 - 0 - 1 = 1 → 1/2 = 50%
    assert result["4a_fr_to_test_pct"] == 50.0
    assert "FR-02" in result["active_untested"]
    # 4a=50% < threshold 100% → passed False
    assert result["passed"] is False


def test_4a_passes_at_100_when_all_active_traced(fixture_repo):
    """After adding test for FR-02, all active FRs are traced → 4a = 100%."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    (fixture_repo / "tests" / "test_b.py").write_text('"""[FR-02]"""\n')
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("harness_cli._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(fixture_repo, gate=2)
    assert result["4a_fr_to_test_pct"] == 100.0
    assert result["passed"] is True


def test_4a_vacuously_100_when_no_active_frs(tmp_path):
    """Empty project → no active FRs → 4a vacuously 100%."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("harness_cli._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4a_fr_to_test_pct"] == 100.0


# ---------------------------------------------------------------------------
# Merged score and threshold
# ---------------------------------------------------------------------------

def test_merged_pct_is_min_of_4a_and_4b(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension

    with patch("harness_cli._run_spec_coverage_check", return_value=(0, 50.0)):
        result = compute_trace_dimension(fixture_repo, gate=2)
    # 4a = 50% (FR-02 missing test), 4b = 50% (mocked)
    # merged = min(50, 50) = 50
    assert result["merged_pct"] == 50.0
    # 4a=50% < threshold 100% → passed False
    assert result["passed"] is False


def test_gate_4a_threshold_100_at_all_gates():
    """4a threshold is 100% at G2, G3, G4 (locked decision)."""
    from core.quality_gate.spec_tracking_checker import TRACE_THRESHOLDS
    assert TRACE_THRESHOLDS[2] == 100
    assert TRACE_THRESHOLDS[3] == 100
    assert TRACE_THRESHOLDS[4] == 100


def test_gate_4b_thresholds_match_existing():
    """4b thresholds (60/80/90) must match the existing D4 spec-coverage."""
    from core.quality_gate.spec_tracking_checker import SPEC_COV_THRESHOLDS
    assert SPEC_COV_THRESHOLDS == {2: 60.0, 3: 80.0, 4: 90.0}


def test_result_has_required_keys(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("harness_cli._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(fixture_repo, gate=3)
    for key in ("name", "4a_fr_to_test_pct", "4b_test_spec_pct",
                "merged_pct", "passed", "threshold_4a", "threshold_4b",
                "active_uncoded", "active_untested", "blocking"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# Gate configs
# ---------------------------------------------------------------------------

def test_gate_configs_include_traceability():
    """All gate configs G2/G3/G4 must have a `traceability` dimension entry."""
    for gate in (2, 3, 4):
        path = (Path(__file__).resolve().parent.parent
                / "harness" / "gate_configs"
                / f"gate{gate}_p3_exit.yaml" if gate == 2
                else Path(__file__).resolve().parent.parent
                     / "harness" / "gate_configs"
                     / f"gate{gate}_p4_exit.yaml" if gate == 3
                else Path(__file__).resolve().parent.parent
                     / "harness" / "gate_configs"
                     / f"gate{gate}_p6_full.yaml")
        text = path.read_text()
        assert "traceability" in text, f"gate{gate} config missing traceability"
        assert "requires_tool_execution: false" in text, \
            f"gate{gate} must have requires_tool_execution: false"


# ---------------------------------------------------------------------------
# HR-16 wording in SKILL.md
# ---------------------------------------------------------------------------

def test_skill_md_has_hr16():
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    assert "HR-16" in skill
    assert "trace dimension" in skill.lower() or "traceability" in skill.lower()
