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
  - threshold table: 4a=100% at G2/G3/G4, 4b/4c=60/80/90% at G2/G3/G4.
  - active_uncoded / active_untested lists contain only IN_PROGRESS+VERIFIED FRs.
"""
from pathlib import Path
from unittest.mock import patch

import pytest


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
# 4c: NFR → test coverage
# ---------------------------------------------------------------------------

def _make_nfr_repo(tmp_path: Path, srs_nfrs: list, test_nfr_mentions: dict) -> Path:
    """Helper: build a minimal project with SRS.md NFRs and optional test annotations."""
    req = tmp_path / "01-requirements"
    req.mkdir(parents=True)
    nfr_text = "\n".join(f"| {n} | description |" for n in srs_nfrs)
    (req / "SRS.md").write_text(f"# Requirements\n\n{nfr_text}\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    for fname, content in test_nfr_mentions.items():
        (tests / fname).write_text(content)
    return tmp_path


def test_4c_no_nfrs_in_srs_is_vacuous_pass(tmp_path):
    """SRS.md with no NFR-XX IDs → 4c = 100% (trivially OK)."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text("# Requirements\nNo NFRs here.\n")
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] == 100.0
    assert result["nfr_untested"] == []


def test_4c_nfr_all_covered_passes(tmp_path):
    """All NFRs in SRS.md have test file mention → 4c = 100%."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _make_nfr_repo(tmp_path, ["NFR-01"], {"test_perf.py": "# NFR-01 performance\ndef test_latency(): pass\n"})
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] == 100.0
    assert result["nfr_untested"] == []


def test_4c_nfr_untested_fails_gate2(tmp_path):
    """NFRs in SRS.md but no test mentions → 4c = 0% → gate fails."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _make_nfr_repo(tmp_path, ["NFR-01", "NFR-08"], {})  # no test files
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] == 0.0
    assert set(result["nfr_untested"]) == {"NFR-01", "NFR-08"}
    assert result["passed"] is False


def test_4c_excludes_nfr99_placeholder(tmp_path):
    """NFR-99 is the harness convention for deferred/TBD/ambiguity markers.

    Per phase1_plan.md L96 + phase1_agent_b_rules (R-CANONICAL-INTERP-001),
    NFR-99 is a placeholder emitted when canonical spec contains TBD/TODO/
    ambiguous phrases that REQUIRE stakeholder resolution. It is NOT a real
    NFR that needs test coverage at Gate 2; demanding test coverage would
    create a self-contradictory requirement (write tests for unresolved
    ambiguity). Therefore 4c denominator must exclude NFR-99.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    # 7 NFRs total, 1 is NFR-99 placeholder, 6 are real.
    # Only 2 of 6 real NFRs have test references → 2/6 = 33.3%.
    # NFR-99 is excluded from denominator entirely.
    _make_nfr_repo(
        tmp_path,
        ["NFR-01", "NFR-02", "NFR-03", "NFR-04", "NFR-05", "NFR-06", "NFR-99"],
        {
            "test_a.py": "# NFR-01 perf\ndef test_latency(): pass\n",
            "test_b.py": "# NFR-02 sec\ndef test_redaction(): pass\n",
        },
    )
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    # 2 covered / 6 real = 33.33% (not 2/7 = 28.57%)
    assert result["4c_nfr_to_test_pct"] == 33.33
    # NFR-99 must NOT appear in nfr_untested (it's not in denominator)
    assert "NFR-99" not in result["nfr_untested"]
    # Real untested NFRs must appear
    assert set(result["nfr_untested"]) == {"NFR-03", "NFR-04", "NFR-05", "NFR-06"}


def test_4c_only_nfr99_present_is_vacuous_pass(tmp_path):
    """SRS.md containing ONLY NFR-99 placeholder → 4c = 100% vacuous pass."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _make_nfr_repo(tmp_path, ["NFR-99"], {})
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] == 100.0
    assert result["nfr_untested"] == []


def test_4c_nfr_partial_coverage_fails_gate2(tmp_path):
    """50% NFR coverage < 60% threshold at G2 → gate fails."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _make_nfr_repo(
        tmp_path,
        ["NFR-01", "NFR-08"],
        {"test_perf.py": "# NFR-01 covered\ndef test_latency(): pass\n"},
    )
    from core.quality_gate.spec_tracking_checker import compute_trace_dimension
    with patch("core.quality_gate.spec_coverage._run_spec_coverage_check", return_value=(0, 100.0)):
        result = compute_trace_dimension(tmp_path, gate=2)
    assert result["4c_nfr_to_test_pct"] == 50.0  # 1 of 2 covered
    assert "NFR-08" in result["nfr_untested"]
    assert result["passed"] is False  # 50% < 60% threshold


# ---------------------------------------------------------------------------
# HR-16 wording in SKILL.md
# ---------------------------------------------------------------------------

def test_skill_md_has_hr16():
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
    assert "HR-16" in skill
    assert "trace dimension" in skill.lower() or "traceability" in skill.lower()
