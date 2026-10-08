"""PR 4: gate trace dimension tests.

Confirms:
  - 4a semantic: 100% over FRs with status ∈ {IN_PROGRESS, VERIFIED}.
    PENDING FRs are NOT in the denominator.
    FRs defined in SAD.md but ALL still PENDING at G2+ → 0% (real failure, not vacuous pass).
    Truly empty project (no SAD.md) → 100% vacuous pass.
  - 4b semantic: TEST_SPEC → test (delegated to existing _run_spec_coverage_check).
  - 4c semantic: NFR-XX IDs from SRS.md must appear in at least one test file.
    No SRS.md or no NFR IDs → 100% vacuous pass.
  - merged = min(4a, 4b, 4c) — fail-closed.
  - threshold table: 4a=100% at G2/G3/G4, 4b=60/80/90% at G2/G3/G4, 4c=80/90% at
    G3/G4 (not due at G2 — Round 117).
  - active_uncoded / active_untested lists contain only IN_PROGRESS+VERIFIED FRs.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.support.nfr_project import make_nfr_project


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


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

    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
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
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(fixture_repo, gate=2)
    assert result["4a_fr_to_test_pct"] == 100.0
    assert result["passed"] is True


def test_4a_vacuously_100_for_empty_project(tmp_path):
    """Truly empty project (no SAD.md, no FR definitions) → 4a vacuously 100%."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4a_fr_to_test_pct"] == 100.0


def test_4a_zero_when_all_frs_pending_at_gate2(tmp_path):
    """FRs defined in SAD.md but no code annotations → all PENDING → 4a = 0% at G2."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    arch = tmp_path / "02-architecture"
    arch.mkdir(parents=True)
    (arch / "SAD.md").write_text("FR-01: feature alpha\nFR-02: feature beta\n")
    # No code annotations, no test files — FRs remain PENDING
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4a_fr_to_test_pct"] == 0.0
    assert result["passed"] is False
    assert "FR-01" in result["active_uncoded"] or "FR-02" in result["active_uncoded"]


# ---------------------------------------------------------------------------
# Merged score and threshold
# ---------------------------------------------------------------------------

def test_merged_pct_is_min_of_4a_and_4b(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension

    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 50.0)):
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
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(fixture_repo, gate=3)
    for key in ("name", "4a_fr_to_test_pct", "4b_test_spec_pct", "4c_nfr_to_test_pct",
                "merged_pct", "passed", "threshold_4a", "threshold_4b",
                "active_uncoded", "active_untested", "nfr_untested", "blocking"):
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
# 4c: NFR → declared, delivered TEST_SPEC case (Round 117)
# ---------------------------------------------------------------------------

def _trace(tmp_path: Path, gate: int = 3) -> dict:
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        return compute_trace_dimension(tmp_path, gate=gate)


def test_4c_is_not_due_at_gate2(tmp_path):
    """NFR-section tests are due at the P4 exit (Round 114 站5): at Gate 2 4c
    is None — neither 100 nor 0 — and stays out of merged."""
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"]}, [("test_nfr01_p95", "AC-N1.1")])
    result = _trace(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] is None
    assert result["merged_pct"] == min(result["4a_fr_to_test_pct"], result["4b_test_spec_pct"])


def test_4c_no_nfrs_in_srs_is_vacuous_pass(tmp_path):
    make_nfr_project(tmp_path, {}, [])
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 100.0
    assert result["nfr_untested"] == []


def test_4c_every_criterion_bound_to_a_delivered_case_passes(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1", "AC-N1.2"]},
                     [("test_nfr01_p95", "Q6/NP-06; AC-N1.1"), ("test_nfr01_sql", "AC-N1.2")],
                     delivered=["test_nfr01_p95", "test_nfr01_sql"])
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 100.0
    assert result["nfr_untested"] == []


def test_4c_a_declared_case_nobody_wrote_fails_gate3(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1", "AC-N1.2"]},
                     [("test_nfr01_p95", "AC-N1.1"), ("test_nfr01_sql", "AC-N1.2")],
                     delivered=["test_nfr01_p95"])
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 0.0
    assert result["nfr_untested"] == ["NFR-01"]
    assert any("AC-N1.2" in w and "test_nfr01_sql (absent)" in w
               for w in result["nfr_absent_witnesses"])
    assert result["passed"] is False


def test_4c_an_annotation_is_not_a_binding(tmp_path):
    """The rule this round retires: a passing test whose body names NFR-01."""
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"]}, [])
    (tmp_path / "03-development" / "tests" / "test_nfr.py").write_text(
        "# NFR-01 perf\ndef test_latency():\n    assert True\n", encoding="utf-8")
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 0.0


def _defer(tmp_path: Path, line: str) -> None:
    spec = tmp_path / "02-architecture" / "TEST_SPEC.md"
    spec.write_text(spec.read_text() + "\n" + line + "\n")


def test_4c_a_criterion_deferred_to_a_tool_is_neither_coverage_nor_a_miss(tmp_path):
    """Round 69 站5: a deferral is not coverage. Round 35: what a test cannot
    show is not zero. NFR-07 leaves the denominator and is named."""
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"], "NFR-07": ["AC-N7.1"]},
                     [("test_nfr01_p95", "AC-N1.1")], delivered=["test_nfr01_p95"])
    _defer(tmp_path, "Deferred: AC-N7.1 — pip-licenses at P5, not a TEST_SPEC case.")
    from core.quality_gate.ac_case_binding import nfr_case_coverage
    cov = nfr_case_coverage(tmp_path)
    assert cov["pct"] == 100.0 and cov["not_test_verified"] == ["NFR-07"]
    assert _trace(tmp_path)["4c_nfr_to_test_pct"] == 100.0


def test_4c_nothing_left_to_measure_is_not_a_pass(tmp_path):
    make_nfr_project(tmp_path, {"NFR-07": ["AC-N7.1"]}, [])
    _defer(tmp_path, "Deferred: AC-N7.1 — pip-licenses at P5, not a TEST_SPEC case.")
    assert _trace(tmp_path)["4c_nfr_to_test_pct"] == 0.0


def test_4c_a_deferral_naming_a_test_is_judged_by_that_test(tmp_path):
    """`check_ac_deferral_targets` reads the same clause (Round 83 站3)."""
    make_nfr_project(tmp_path, {"NFR-10": ["AC-N10.1"]}, [], delivered=["test_nfr10_line_coverage"])
    _defer(tmp_path, "Deferred: AC-N10.1 — test_nfr10_line_coverage at P4, not a TEST_SPEC case.")
    assert _trace(tmp_path)["4c_nfr_to_test_pct"] == 100.0
    (tmp_path / "03-development" / "tests" / "test_nfr.py").write_text("x = 1\n")
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 0.0
    assert any("test_nfr10_line_coverage (absent)" in w for w in result["nfr_absent_witnesses"])


def test_4c_an_nfr_with_no_criterion_is_not_covered(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": []}, [])
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 0.0
    assert any("declares no AC-id" in w for w in result["nfr_absent_witnesses"])


def test_4c_excludes_nfr99_placeholder(tmp_path):
    """NFR-99 is the deferred/TBD placeholder (R-CANONICAL-INTERP-001)."""
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"], "NFR-02": ["AC-N2.1"], "NFR-99": []},
                     [("test_nfr01_p95", "AC-N1.1")], delivered=["test_nfr01_p95"])
    result = _trace(tmp_path)
    assert result["4c_nfr_to_test_pct"] == 50.0
    assert result["nfr_untested"] == ["NFR-02"]


# ---------------------------------------------------------------------------
# HR-16 wording in SKILL.md
# ---------------------------------------------------------------------------

def test_skill_md_has_hr16():
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    assert "HR-16" in skill
    assert "trace dimension" in skill.lower() or "traceability" in skill.lower()
