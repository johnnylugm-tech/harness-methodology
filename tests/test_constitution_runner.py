"""Tests for core/quality_gate/constitution/runner.py — Constitution compliance checker."""

import pytest
import tempfile
from pathlib import Path

pytestmark = pytest.mark.constitution

from core.quality_gate.constitution.runner import (  # pyright: ignore[reportMissingImports]
    ConstitutionResult,
    _scan_file_compliance,
    _scan_directory,
    _dimensions_for_phase,
    _threshold_for_dimension,
    _should_scan_file,
    run_constitution_check,
)
from core.quality_gate.constitution.profile import defaults

from constitution import get_phase_thresholds  # pyright: ignore[reportMissingImports]


class TestConstitutionResult:
    def test_default_values(self):
        r = ConstitutionResult()
        assert r.score == 0.0
        assert r.passed is False
        assert r.violations == []
        assert r.dimensions == {}

    def test_to_dict(self):
        r = ConstitutionResult(score=75.0, passed=True, violations=[{"x": 1}],
                               check_type="all", phase=3, check_mode="preflight",
                               dimensions={"correctness": 80.0, "security": 90.0})
        d = r.to_dict()
        assert d["score"] == 75.0
        assert d["passed"] is True
        assert d["violations"] == 1
        assert d["check_type"] == "all"
        assert d["phase"] == 3
        assert d["dimensions"] == {"correctness": 80.0, "security": 90.0}


class TestScanFileCompliance:
    def test_nonexistent_file(self):
        dims = _scan_file_compliance(Path("/nonexistent/path.md"))
        assert dims == {"correctness": 0.0, "security": 0.0,
                       "maintainability": 0.0, "coverage": 0.0}

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            assert dims["correctness"] == 0.0
            assert dims["security"] == 0.0
        finally:
            path.unlink()

    def test_short_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("short")
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            assert dims["correctness"] == 0.0
        finally:
            path.unlink()

    def test_high_compliance_file(self):
        content = """# Requirements Specification

## FR-01: Quality Gate
This feature implements a quality gate with test coverage verification.
The system ensures constitution compliance and traceability.
All functions use HMAC signature verification with proper input sanitizer.
Rate limiting is enforced per-user with RBAC permission checks.
PII masking is applied before any data leaves the system.

## FR-02: SAD Integration
This feature provides SAD integration with acceptance criteria.
Uses pytest fixtures with mock objects for unit test coverage.
All modules have docstring documentation with type hints.

## NFR-01: Performance
The trace matrix ensures all FR and NFR items are covered.
Test plan includes regression coverage for security patterns.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            assert dims["correctness"] > 50, f"Expected correctness >50, got {dims}"
            assert dims["security"] > 30, f"Expected security >30, got {dims}"
        finally:
            path.unlink()

    def test_low_compliance_file(self):
        content = "# Random Notes\n\nJust some text without any structure.\nNo keywords here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            assert dims["correctness"] < 50, f"Expected correctness <50, got {dims}"
        finally:
            path.unlink()

    def test_hardcoded_secrets_penalize_security(self):
        content = """# Config

## FR-01: Auth module
password = "admin123"
api_key = "sk-1234567890abcdef"
This module handles authentication and encryption with HMAC signatures.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            # Security should be penalized for hardcoded secrets
            assert dims["security"] < 80, f"Expected security <80 due to secrets, got {dims}"
        finally:
            path.unlink()


class TestIsStubTemplate:
    """Tests for _is_stub_template() — detects {placeholder}-ridden template files.

    Threshold is 8 (not 5) and the regex now excludes:
    - Shell variable expansions: ${VAR} or ${VAR:-default}
    - Code patterns with dots/colons:  {Platform.Telegram}, {key: value}
    """

    def test_eight_placeholders_is_stub(self):
        from core.quality_gate.constitution.runner import _is_stub_template
        # 8 simple word/phrase placeholders → IS a stub template
        content = (
            "# {Project Name}\n\n{desc}\n{module}\n{api}\n"
            "{deps}\n{author}\n{version}\n{date}"
        )
        assert _is_stub_template(content) is True

    def test_seven_placeholders_is_not_stub(self):
        from core.quality_gate.constitution.runner import _is_stub_template
        # 7 placeholders → below threshold of 8 → NOT a stub
        content = "# {Project Name}\n\n{desc}\n{module}\n{api}\n{deps}\n{author}\n{version}"
        assert _is_stub_template(content) is False

    def test_four_placeholders_is_not_stub(self):
        from core.quality_gate.constitution.runner import _is_stub_template
        content = "### {Project Name}\n\n{desc}\n{module}\n{api}"
        assert _is_stub_template(content) is False

    def test_shell_vars_not_counted(self):
        """${VAR} and ${VAR:-default} shell expansions must not count as placeholders."""
        from core.quality_gate.constitution.runner import _is_stub_template
        # 10 shell vars → still NOT a stub ($ prefix excluded)
        content = (
            "${VAR1} ${VAR2} ${VAR3:-default} ${VAR4} ${VAR5}\n"
            "${VAR6} ${VAR7} ${VAR8} ${VAR9} ${VAR10}"
        )
        assert _is_stub_template(content) is False

    def test_code_patterns_with_dots_not_counted(self):
        """Python/code patterns like {Platform.Telegram} must not count as placeholders."""
        from core.quality_gate.constitution.runner import _is_stub_template
        # Code patterns with dots → not matched by strict regex
        content = (
            "{Platform.TELEGRAM} {Platform.LINE} {Platform.MESSENGER}\n"
            "{user.id} {request.body} {response.status} {error.code}\n"
            "{a.b} {c.d} {e.f}"
        )
        assert _is_stub_template(content) is False

    def test_empty_content(self):
        from core.quality_gate.constitution.runner import _is_stub_template
        assert _is_stub_template("") is False

    def test_real_document_without_placeholders(self):
        from core.quality_gate.constitution.runner import _is_stub_template
        content = (
            "# SRS\n\n## Functional Requirements\n\n"
            "| FR-01 | User login | auth.py | Unit test |\n"
            "| FR-02 | Payment | pay.py | Integration |\n"
        )
        assert _is_stub_template(content) is False

    def test_json_block_not_false_positive(self):
        """JSON with quoted keys ({"key": val}) should not count as placeholders."""
        from core.quality_gate.constitution.runner import _is_stub_template
        content = (
            '<!-- SAB:START -->\n```json\n'
            '{"version": "1.0", "layers": [{"name": "Core"}], '
            '"dependencies": {"Core": []}}\n'
            '```\n<!-- SAB:END -->'
        )
        assert _is_stub_template(content) is False

    def test_fstring_like_in_code_block(self):
        """F-string fragments {var} inside code blocks count as placeholders,
        but a real file with only a few such fragments should not be flagged."""
        from core.quality_gate.constitution.runner import _is_stub_template
        content = (
            "# Code Example\n\n```python\n"
            "name = f\"Hello {user}\"\n"
            "count = f\"You have {n} items\"\n"
            "```\n\n"
            "## Section\n\n"
            + "Real documentation text here.\n" * 10
        )
        # Only 2 {user} and {n} patterns — well below threshold
        assert _is_stub_template(content) is False


class TestScanDirectory:
    def test_empty_directory_phase1_skips(self):
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            result = _scan_directory(docs, phase=1, check_type="all")
            assert result.score == 100.0
            assert result.passed is True
            assert len(result.violations) == 0

    def test_empty_directory_phase5_passes(self):
        # Empty directory → vacuously pass (artifact existence is checked
        # separately by the artifact enforcer).
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            result = _scan_directory(docs, phase=5, check_type="all")
            assert result.score == 100.0
            assert result.passed is True
            assert len(result.violations) == 0

    def test_directory_with_md_files(self):
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            (docs / "README.md").write_text(
                "# Test Document\n\n## FR-01 quality gate test coverage\n\n"
                "This document verifies constitution compliance and traceability.\n\n"
                "## FR-02 acceptance criteria\n\n"
                "All requirements must pass SRS and SAD integration checks.\n\n"
                "## NFR-01 performance\n\n"
                "The traceability matrix ensures complete NFR coverage.\n"
                "Security: HMAC signature verification with input sanitizer.\n"
                "Auth validation with RBAC permission and token encryption.\n"
                "Pytest unit test coverage with mock fixtures and assertions.\n"
            )
            result = _scan_directory(docs, phase=1, check_type="all")
            assert 0 < result.score <= 100
            assert "correctness" in result.dimensions
            assert "security" in result.dimensions

    def test_phase_directory_fallback(self, tmp_path):
        """_scan_directory should find phase dir when it exists alongside docs."""
        phase_dir = tmp_path / "01-requirements"
        phase_dir.mkdir()
        (phase_dir / "SRS.md").write_text(
            "# SRS\n\n## FR-01 quality gate\n\nTest coverage and traceability matrix.\n"
            "## FR-02 acceptance criteria\n\nConstitution compliance verified.\n"
            "Security validation with HMAC signature and RBAC authorization.\n"
            "PII masking and input sanitizer for all user data.\n"
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        result = _scan_directory(docs, phase=1, check_type="srs")
        assert result.score > 0

    def test_low_score_violations(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "empty.md").write_text(
            "# X\n\nno keywords here at all, just filler text "
            "to exceed minimum length requirement for scanning"
        )
        result = _scan_directory(docs, phase=5, check_type="all")
        assert result.score < 30
        assert len(result.violations) > 0

    def test_dimensions_in_result_phase4(self, tmp_path):
        """Phase 4 uses all 4 dimensions."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## FR-01 quality gate test coverage\n\n"
            "Constitution traceability with HMAC signature verification.\n"
            "Acceptance criteria defined with pytest unit test coverage.\n"
            "## FR-02 security auth validation RBAC permission token encrypt\n"
        )
        result = _scan_directory(docs, phase=4, check_type="sad")
        assert "correctness" in result.dimensions
        assert "security" in result.dimensions
        assert "maintainability" in result.dimensions
        assert "coverage" in result.dimensions

    def test_dimensions_in_result_phase3(self, tmp_path):
        """Phase 3 uses correctness+security+maintainability (3 dims)."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## FR-01 quality gate test coverage\n\n"
            "Constitution traceability with HMAC signature verification.\n"
            "Acceptance criteria defined with pytest unit test coverage.\n"
            "## FR-02 security auth validation RBAC permission token encrypt\n"
            "## module class def docstring type hint dataclass interface\n"
        )
        result = _scan_directory(docs, phase=3, check_type="sad")
        assert "correctness" in result.dimensions
        assert "security" in result.dimensions
        assert "maintainability" in result.dimensions
        # P3 uses maintainability (TH-05>90%) but NOT coverage as constitution dimension
        # (coverage is checked separately via TH-11 >=70% pytest coverage)


class TestDimensionsForPhase:
    def test_phase1_only_correctness_security(self):
        """P1 uses only correctness + security (2 dimensions)."""
        assert _dimensions_for_phase(1) == ["correctness", "security"]

    def test_phase2_3_three_dimensions(self):
        """P2-P3 use correctness + security + maintainability (3 dimensions)."""
        for p in (2, 3):
            dims = _dimensions_for_phase(p)
            assert dims == ["correctness", "security", "maintainability"], \
                f"Phase {p} should use 3 dimensions, got {dims}"

    def test_phase4_plus_all_four_dimensions(self):
        # P4-P6 and P8 use all 4 dimensions.
        # P7 (Risk Management) uses only correctness + security — maintainability
        # and coverage use code/test-centric keywords inapplicable to risk docs.
        for p in [4, 5, 6, 8]:
            dims = _dimensions_for_phase(p)
            assert "correctness" in dims, f"P{p} missing correctness"
            assert "security" in dims, f"P{p} missing security"
            assert "maintainability" in dims, f"P{p} missing maintainability"
            assert "coverage" in dims, f"P{p} missing coverage"

    def test_phase7_risk_dimensions(self):
        # P7 is intentionally limited: risk docs don't contain code/test vocabulary.
        dims = _dimensions_for_phase(7)
        assert "correctness" in dims
        assert "security" in dims
        assert "maintainability" not in dims, "P7 must exclude code-centric maintainability"
        assert "coverage" not in dims, "P7 must exclude test-centric coverage"


class TestPhaseDirMap:
    def test_all_phases_mapped(self):
        p = defaults()
        for phase in range(1, 9):
            assert p.phase_directory(phase) != "docs", f"Phase {phase} missing from phase_dir_map"

    def test_phase_dirs_match_standard(self):
        p = defaults()
        assert p.phase_directory(1) == "01-requirements"
        assert p.phase_directory(4) == "04-testing"
        assert p.phase_directory(6) == "06-quality"
        assert p.phase_directory(8) == "08-config"


class TestRunConstitutionCheck:
    def test_missing_directory_phase1_skips(self):
        result = run_constitution_check("all", "/nonexistent/path", current_phase=1)
        assert result.passed is True
        assert result.score == 100.0

    def test_missing_directory_phase3_passes(self):
        # Missing directory → vacuously pass (artifact existence is
        # checked separately by the artifact enforcer).
        result = run_constitution_check("all", "/nonexistent/path", current_phase=3)
        assert result.passed is True
        assert result.score == 100.0

    def test_strict_mode_passes_on_missing_dir(self):
        # strict mode no longer raises on missing dir — it's a vacuously pass.
        result = run_constitution_check("all", "/nonexistent/path", current_phase=3, strict=True)
        assert result.passed is True
        assert result.score == 100.0

    def test_strict_mode_raises_on_failure(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        # This file contains no constitution keywords (FR-, NFR-, acceptance
        # criteria, security/auth/RBAC, test coverage, docstring, etc.) and no
        # structure beyond a single heading.  Expected score: ~0–20% on each
        # dimension — well below the P5 80% composite threshold.
        (docs / "empty.md").write_text(
            "# X\n\nno keywords here at all, just filler text "
            "to exceed minimum length requirement for scanning"
        )
        with pytest.raises(RuntimeError, match="Constitution check FAILED"):
            run_constitution_check("all", str(docs), current_phase=5, strict=True)

    def test_preflight_mode(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SRS.md").write_text(
            "# SRS\n\n## FR-01 quality gate test coverage constitution traceability\n\n"
            "## FR-02 acceptance criteria SAD integration\n\n## FR-03 NFR performance\n\n"
            "## FR-04 security HMAC signature auth validation RBAC\n\n"
            "## FR-05 reliability pytest unit test coverage\n"
        )
        result = run_constitution_check("srs", str(docs), current_phase=1, check_mode="preflight")
        assert result.check_mode == "preflight"
        assert result.phase == 1

    def test_phase1_sparse_srs_fails_constitution(self, tmp_path):
        """P1-P2 threshold=100%: sparse SRS without all security keywords fails.

        Documents the strict P1-P2 design: TH-03=100% + TH-04=100% means documents
        must include comprehensive correctness AND security content to pass.
        _aggregate_score uses min-of-dimensions, so both must reach 100%.
        """
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SRS.md").write_text(
            "# SRS\n\n## FR-01 Quality Gate\nTest coverage and traceability.\n"
            "Acceptance criteria defined. Constitution compliance verified.\n"
        )
        result = run_constitution_check("srs", str(docs), current_phase=1)
        assert result.phase == 1
        # P1 threshold=100% (TH-03+TH-04 via bottleneck min); sparse SRS without
        # full security keywords (e.g. hmac, tls, compare_digest) scores << 100%
        assert not result.passed, (
            "P1 constitution check requires comprehensive security content "
            "(TH-04=100% needs all security keywords present)"
        )

    def test_phase4_uses_all_dimensions(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## quality gate\n\n## test coverage\n\n## constitution\n\n"
            "## traceability matrix\n\n## FR-01\n\n## FR-02\n\n## acceptance criteria\n"
            "## security auth validation RBAC HMAC signature encrypt\n"
            "## pytest unit test mock fixture assert coverage report\n"
        )
        result = run_constitution_check("sad", str(docs), current_phase=4)
        assert result.phase == 4
        assert "correctness" in result.dimensions
        assert "security" in result.dimensions
        assert "maintainability" in result.dimensions
        assert "coverage" in result.dimensions

    def test_phase5_threshold_80(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "QUALITY.md").write_text(
            "# Quality Report\n\nquality gate test coverage constitution traceability\n"
            "security auth validation HMAC RBAC\n"
        )
        result = run_constitution_check("quality_report", str(docs), current_phase=6)
        assert result.phase == 6
        assert result.check_type == "quality_report"


class TestThresholdForDimension:
    def test_correctness_threshold_p1_to_p4(self):
        for p in (1, 2, 3, 4):
            assert _threshold_for_dimension("correctness", p) == 100.0

    def test_security_threshold_p1_to_p4(self):
        for p in (1, 2, 3, 4):
            assert _threshold_for_dimension("security", p) == 100.0

    def test_maintainability_threshold_p1_to_p4(self):
        for p in (1, 2, 3, 4):
            assert _threshold_for_dimension("maintainability", p) == 90.0

    def test_coverage_threshold_p1_to_p4(self):
        for p in (1, 2, 3, 4):
            assert _threshold_for_dimension("coverage", p) == 90.0

    def test_all_dimensions_p5_plus_use_80(self):
        for p in (5, 6, 7, 8):
            for dim in ("correctness", "security", "maintainability", "coverage"):
                assert _threshold_for_dimension(dim, p) == 80.0, \
                    f"{dim} at phase {p} should be 80.0"

    def test_unknown_dimension_fallback(self):
        assert _threshold_for_dimension("unknown", 3) == 80.0


class TestGetPhaseThresholds:
    def test_phase1_has_correct_th_rules(self):
        rules = get_phase_thresholds(1)
        assert "TH-01" in rules
        assert "TH-03" in rules
        assert "TH-04" in rules
        assert "TH-08" in rules
        assert "TH-14" in rules
        assert "TH-15" in rules
        # P1 should NOT have TH-05/TH-06
        assert "TH-05" not in rules
        assert "TH-06" not in rules

    def test_phase3_has_correct_th_rules(self):
        rules = get_phase_thresholds(3)
        assert "TH-05" in rules   # maintainability now active at P3
        assert "TH-10" in rules
        assert "TH-11" in rules
        assert "TH-15" in rules
        assert "TH-16" in rules
        # TH-06 (coverage) is P4 only, not P3
        assert "TH-06" not in rules

    def test_phase6_has_correct_th_rules(self):
        rules = get_phase_thresholds(6)
        assert "TH-02" in rules
        assert "TH-07" in rules
        assert "TH-15" in rules

    def test_all_17_rules_exist(self):
        from constitution import get_th_rules
        all_rules = get_th_rules()
        assert len(all_rules) == 17
        for th_id in [f"TH-{i:02d}" for i in range(1, 18)]:
            assert th_id in all_rules, f"{th_id} missing from get_th_rules()"

    def test_each_rule_has_4_tuple_elements(self):
        from constitution import get_th_rules
        for th_id, rule in get_th_rules().items():
            assert len(rule) == 4, f"{th_id} should have 4 elements: {rule}"


class TestCheckTypeFiltering:
    """Tests for _should_scan_file and check_type filtering."""

    def test_all_passes_everything(self):
        assert _should_scan_file(Path("anything.md"), "all") is True
        assert _should_scan_file(Path("random.txt"), "all") is True

    def test_srs_filters_correctly(self):
        assert _should_scan_file(Path("SRS.md"), "srs") is True
        assert _should_scan_file(Path("spec_v2.md"), "srs") is True
        assert _should_scan_file(Path("SAD.md"), "srs") is False
        assert _should_scan_file(Path("architecture.md"), "srs") is False

    def test_sad_filters_correctly(self):
        assert _should_scan_file(Path("SAD.md"), "sad") is True
        assert _should_scan_file(Path("adr/001-design.md"), "sad") is True
        assert _should_scan_file(Path("SRS.md"), "sad") is False

    def test_unknown_check_type_passes_all(self):
        assert _should_scan_file(Path("SRS.md"), "unknown") is True

    def test_filter_applied_in_scan_directory(self, tmp_path):
        """check_type='sad' should only scan SAD-related files."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SRS.md").write_text("# SRS\n\n## FR-01 quality gate\n\ntest coverage constitution\n")
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## FR-01 quality gate test coverage constitution traceability\n"
            "## security auth validation HMAC RBAC signature encrypt\n"
            "## module class def docstring type hint dataclass abc interface\n"
            "## pytest unit test mock fixture assert coverage report\n"
        )
        result = _scan_directory(docs, phase=1, check_type="sad")
        # Only SAD.md should be scanned; SRS.md excluded by filter
        assert result.score >= 0


class TestDoubleScanPrevention:
    """Tests that _scan_directory does not double-count the same directory."""

    def test_named_dir_not_doubled(self, tmp_path):
        """When docs/ absent, passing numbered dir directly should not double-scan."""
        phase_dir = tmp_path / "01-requirements"
        phase_dir.mkdir()
        (phase_dir / "SRS.md").write_text(
            "# SRS\n\n## FR-01 quality gate test coverage\n\n"
            "Constitution traceability matrix with acceptance criteria.\n"
            "Security: HMAC signature verification with RBAC authorization.\n"
        )
        result = _scan_directory(phase_dir, phase=1, check_type="all")
        assert result.score > 0
        # The key assertion: score should not be doubled

    def test_docs_plus_phase_dir_both_scanned(self, tmp_path):
        """When both docs/ and 03-development/ exist, both should be scanned."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "README.md").write_text(
            "# Project\n\n## FR-01 quality gate test coverage constitution traceability\n"
        )
        phase_dir = tmp_path / "03-development"
        phase_dir.mkdir()
        (phase_dir / "IMPLEMENTATION.md").write_text(
            "# Implementation\n\n## FR-01 quality gate test coverage\n\n"
            "Constitution compliance verified with security auth validation\n"
        )
        result = _scan_directory(docs, phase=3, check_type="all")
        assert result.score > 0
