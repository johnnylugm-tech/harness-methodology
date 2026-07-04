"""Tests for core/quality_gate/constitution/runner.py — Constitution compliance checker."""
import pytest


import json
import tempfile
from pathlib import Path

from core.quality_gate.constitution.runner import (  # pyright: ignore[reportMissingImports]
    ConstitutionResult,
    _scan_file_compliance,
    _scan_directory,
    _dimensions_for_phase,
    _threshold_for_dimension,
    _should_scan_file,
    _get_completed_phases,
    missing_keywords,
    run_constitution_check,
)
from core.quality_gate.constitution.profile import get_profile
from core.quality_gate.constitution.profile import defaults

from constitution import get_phase_thresholds  # pyright: ignore[reportMissingImports]

pytestmark = [pytest.mark.mutation_oracle, pytest.mark.constitution]


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

    def test_short_python_init_main_file_vacuous_bug35(self, tmp_path):
        """Bug #35 regression: __init__.py / __main__.py <100 chars are
        Python convention placeholders, not documentation. Returning
        0/0/0/0 poisoned the directory aggregate for P3+ projects.
        They must be treated as vacuous (100/100/100/100).
        """
        for name in ("__init__.py", "__main__.py"):
            f = tmp_path / name
            f.write_text("")  # empty
            dims = _scan_file_compliance(f)
            assert dims == {
                "correctness": 100.0, "security": 100.0,
                "maintainability": 100.0, "coverage": 100.0,
            }, f"{name} empty should be vacuous, got {dims}"

    def test_short_python_non_boilerplate_still_zero(self, tmp_path):
        """Bug #35: only __init__.py and __main__.py get the vacuous
        treatment. Other small Python files (e.g. a stub helper) still
        return 0/0/0/0 as before — they should be fixed by adding content
        or removed.
        """
        f = tmp_path / "tiny_helper.py"
        f.write_text("x = 1\n")
        dims = _scan_file_compliance(f)
        assert dims["correctness"] == 0.0
        assert dims["security"] == 0.0

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


class TestHasStubSentinel:
    """Tests for _has_stub_sentinel() — explicit author opt-out marker.

    The sentinel `<!-- harness:template-stub -->` is a content-level marker
    that exempts a file from scoring until the author removes it. Distinct
    from _is_stub_template (heuristic based on placeholder count).
    """

    def test_sentinel_present_in_content(self):
        from core.quality_gate.constitution.runner import _has_stub_sentinel
        content = "# ADR-01: foo\n\n<!-- harness:template-stub -->\n\n## Status\n"
        assert _has_stub_sentinel(content) is True

    def test_sentinel_absent_from_real_adr(self):
        from core.quality_gate.constitution.runner import _has_stub_sentinel
        content = (
            "# ADR-01: foo\n\n## Status\nAccepted\n\n## Context\n"
            "Real ADR content with no sentinel.\n" * 10
        )
        assert _has_stub_sentinel(content) is False

    def test_sentinel_inside_code_block_still_skips(self):
        """Author opt-out works even when the sentinel is inside a code fence."""
        from core.quality_gate.constitution.runner import _has_stub_sentinel
        content = (
            "# ADR\n\n```html\n<!-- harness:template-stub -->\n```\n\n## Status\n"
        )
        assert _has_stub_sentinel(content) is True

    def test_partial_match_does_not_trigger(self):
        """`not-harness:template-stub` is NOT the sentinel — exact string required."""
        from core.quality_gate.constitution.runner import _has_stub_sentinel
        # Missing `<!--` prefix
        assert _has_stub_sentinel("harness:template-stub\n") is False
        # Missing closing `-->`
        assert _has_stub_sentinel("<!-- harness:template-stub\n") is False
        # Different namespace
        assert _has_stub_sentinel("<!-- not-harness:template-stub -->\n") is False

    def test_empty_content(self):
        from core.quality_gate.constitution.runner import _has_stub_sentinel
        assert _has_stub_sentinel("") is False

    def test_scan_file_compliance_returns_vacuous_100s_with_sentinel(self):
        """File with sentinel + thin content returns {100,100,100,100}."""
        from core.quality_gate.constitution.runner import _scan_file_compliance
        content = (
            "# ADR-01: foo\n\n"
            "<!-- harness:template-stub -->\n\n"
            "## Status\n{Proposed}\n\n"
        )
        # Pad to ≥100 chars to bypass the length gate
        content += "Placeholder prose. " * 10
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            path = Path(f.name)
        try:
            dims = _scan_file_compliance(path)
            assert dims == {
                "correctness": 100.0,
                "security": 100.0,
                "maintainability": 100.0,
                "coverage": 100.0,
            }
        finally:
            path.unlink()

    def test_sentinel_and_placeholders_dual_skip(self):
        """Both early-exits return the same dict; order does not matter."""
        from core.quality_gate.constitution.runner import _has_stub_sentinel, _is_stub_template
        content = (
            "# {Project Name}\n\n"
            "<!-- harness:template-stub -->\n\n"
            "{desc}\n{module}\n{api}\n{deps}\n{author}\n{version}\n{date}\n{owner}"
        )
        # Both heuristics fire
        assert _is_stub_template(content) is True
        assert _has_stub_sentinel(content) is True


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
        # P5 constitution scans VERIFICATION_REPORT.md (not *.py).
        # A thin markdown with no keywords must score low.
        (docs / "VERIFICATION_REPORT.md").write_text(
            "# Verification Report\n\n"
            "Just some notes.\n\n"
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
    def test_phase1_only_correctness(self):
        """P1 enforces correctness only — security removed 2026-06-12 (topic-
        keyword checklist corpus-unsatisfiable for honest requirements docs;
        P1 security adequacy is owned by Agent B review + SAB NFR floors)."""
        assert _dimensions_for_phase(1) == ["correctness"]

    def test_phase2_only_correctness(self):
        """P2 enforces correctness only — security removed 2026-06-12 (same
        corpus-unsatisfiable defect as P1: tts-new 69%, taskq approved SAD 31%
        vs min-composite 80; owned by TECH_LEAD review + SAB floors + Gate 2-4
        tool-scored security)."""
        dims = _dimensions_for_phase(2)
        assert dims == ["correctness"], f"P2 should enforce correctness only, got {dims}"
        assert "maintainability" not in dims, "P2 must exclude code-centric maintainability"

    def test_phase3_three_dimensions(self):
        """P3 uses only correctness (Bug #35: drop security/maintainability
        from code-only phases; quality is gated via Gate 1/2 tool scores)."""
        dims = _dimensions_for_phase(3)
        assert dims == ["correctness"], \
            f"P3 should use 1 dimension (correctness), got {dims}"

    def test_phase4_all_four_dimensions(self):
        # Bug #35 extension: P4 = correctness only (same rationale as P3 —
        # test code is .py, security/maintainability/coverage keyword density
        # is meaningless for source).
        dims = _dimensions_for_phase(4)
        assert dims == ["correctness"]

    def test_phase5_verification_dimensions(self):
        # P5 (Verification) is a document phase — no code/test vocabulary.
        dims = _dimensions_for_phase(5)
        assert "correctness" in dims
        assert "security" in dims
        assert "maintainability" not in dims, "P5 must exclude code-centric maintainability"
        assert "coverage" not in dims, "P5 must exclude test-centric coverage"

    def test_phase6_quality_dimensions(self):
        # P6 (Quality) is a document phase — no code/test vocabulary.
        dims = _dimensions_for_phase(6)
        assert "correctness" in dims
        assert "security" in dims
        assert "maintainability" not in dims, "P6 must exclude code-centric maintainability"
        assert "coverage" not in dims, "P6 must exclude test-centric coverage"

    def test_phase7_risk_dimensions(self):
        # P7 is intentionally limited: risk docs don't contain code/test vocabulary.
        dims = _dimensions_for_phase(7)
        assert "correctness" in dims
        assert "security" in dims
        assert "maintainability" not in dims, "P7 must exclude code-centric maintainability"
        assert "coverage" not in dims, "P7 must exclude test-centric coverage"

    def test_phase8_config_dimensions(self):
        # P8 is intentionally limited: config/release docs don't contain code/test vocabulary.
        dims = _dimensions_for_phase(8)
        assert "correctness" in dims
        assert "security" in dims
        assert "maintainability" not in dims, "P8 must exclude code-centric maintainability"
        assert "coverage" not in dims, "P8 must exclude test-centric coverage"

    def test_per_phase_correctness_keywords(self):
        """P5-P8 use phase-appropriate correctness keywords, not SRS/SAD vocabulary."""
        p = defaults()
        global_kw = p.dimension_keywords("correctness")
        # Global keywords are SRS/SAD-centric (P1-P4).
        assert "srs" in global_kw
        assert "sad" in global_kw
        # Redundant format-specific keywords removed.
        assert "### fr-" not in global_kw
        assert "## fr-" not in global_kw
        # Phase-inappropriate P4 activity removed.
        assert "test case" not in global_kw

        # P5 (Verification): uses verification vocabulary.
        p5_kw = p.dimension_keywords_for_phase("correctness", 5)
        assert "verify" in p5_kw or "verification" in p5_kw
        assert "srs" not in p5_kw, "P5 correctness must not require SRS keyword"
        assert "sad" not in p5_kw, "P5 correctness must not require SAD keyword"

        # P6 (Quality): uses quality vocabulary.
        p6_kw = p.dimension_keywords_for_phase("correctness", 6)
        assert "quality" in p6_kw
        assert "monitoring" in p6_kw
        assert "srs" not in p6_kw

        # P7 (Risk): uses risk vocabulary.
        p7_kw = p.dimension_keywords_for_phase("correctness", 7)
        assert "risk" in p7_kw
        assert "mitigation" in p7_kw
        assert "vulnerability" in p7_kw
        assert "srs" not in p7_kw

        # P8 (Configuration): uses config vocabulary.
        p8_kw = p.dimension_keywords_for_phase("correctness", 8)
        assert "config" in p8_kw or "configuration" in p8_kw
        assert "deployment" in p8_kw
        assert "release" in p8_kw
        assert "srs" not in p8_kw

        # All phases P1-P8 now have per-phase correctness overrides.
        p1_kw = p.dimension_keywords_for_phase("correctness", 1)
        assert "sad" not in p1_kw, "P1 must not require SAD keyword (P2 artifact)"
        assert "fr-" in p1_kw and "requirement" in p1_kw and "srs" in p1_kw

        # P2 (Architecture): keeps traceability matrix + srs + sad, removes acceptance criteria.
        p2_kw = p.dimension_keywords_for_phase("correctness", 2)
        assert "sad" in p2_kw and "srs" in p2_kw and "traceability matrix" in p2_kw
        assert "acceptance criteria" not in p2_kw, "P2 correctness must not require acceptance criteria"

        # P3 (Source code): only FR-reference vocabulary appears in code comments.
        p3_kw = p.dimension_keywords_for_phase("correctness", 3)
        assert "fr-" in p3_kw and "requirement" in p3_kw
        assert "sad" not in p3_kw and "traceability matrix" not in p3_kw and "srs" not in p3_kw

        # P4 (Testing): adds "test case" (P4-specific), keeps "acceptance criteria".
        p4_kw = p.dimension_keywords_for_phase("correctness", 4)
        assert "test case" in p4_kw, "P4 correctness must include test case"
        assert "acceptance criteria" in p4_kw
        assert "sad" not in p4_kw and "traceability matrix" not in p4_kw

    def test_per_phase_keywords_fallback_for_unchanged_dimensions(self):
        """Dimensions without per-phase security overrides fall back to global keywords."""
        p = defaults()
        # Bug #35 extension: P3 and P4 both dropped their security dimension
        # (code-only phases). P5-P8 still use security (document phases).
        for phase in range(5, 9):
            # P5-P8 have no per-phase security override — fall back to global.
            # (P3, P4 have no security dimension at all — their phase lookup
            # would still return the global list because the dimensions dict
            # is consulted by name, but the active_dimensions list excludes
            # security — so these kwargs are never applied to scoring).
            _ = p.dimension_keywords_for_phase("security", phase)
        # The active_dimensions check is the authoritative gate: verify
        # P3-P4 don't have security in their active set.
        assert "security" not in p.active_dimensions(3)
        assert "security" not in p.active_dimensions(4)

    def test_phase_none_returns_global_keywords(self):
        """phase=None (default for _scan_file_compliance) must fall back to global keywords."""
        p = defaults()
        for dim in ("correctness", "security", "maintainability", "coverage"):
            kw_none = p.dimension_keywords_for_phase(dim, None)
            kw_global = p.dimension_keywords(dim)
            assert kw_none == kw_global, \
                f"dimension_keywords_for_phase('{dim}', None) should equal global keywords"

    def test_scan_file_compliance_default_phase_uses_global_keywords(self):
        """_scan_file_compliance called without phase uses global (P1-P4) correctness vocabulary."""
        import tempfile
        import os
        # SRS-vocabulary content — should score well under global correctness keywords.
        content = "\n".join([
            "# SRS Document", "## Overview", "## FR-01 Requirements",
            "## FR-02 Specification", "## FR-03 Traceability",
            "This document contains the srs and sad specifications.",
            "fr- nfr- acceptance criteria requirement specification traceability matrix.",
            "security encryption authentication tls https pii",
        ] * 6)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            dims_no_phase = _scan_file_compliance(Path(tmp))           # phase=None default
            dims_phase1 = _scan_file_compliance(Path(tmp), phase=1)    # explicit P1 (global kw)
            assert dims_no_phase["correctness"] == dims_phase1["correctness"], \
                "phase=None must produce identical correctness score to phase=1"
        finally:
            os.unlink(tmp)


class TestProjectLayoutPhaseMap:
    def test_all_phases_mapped(self):
        from core.utils.project_layout import ProjectLayout
        from pathlib import Path
        layout = ProjectLayout(Path("/tmp/project"))
        for phase in range(1, 9):
            assert layout.get_phase_dir(phase).name != "docs", f"Phase {phase} missing from get_phase_dir"

    def test_phase_dirs_match_standard(self):
        from core.utils.project_layout import ProjectLayout
        from pathlib import Path
        layout = ProjectLayout(Path("/tmp/project"))
        assert layout.get_phase_dir(1).name == "01-requirements"
        assert layout.get_phase_dir(4).name == "04-testing"
        assert layout.get_phase_dir(6).name == "06-quality"
        assert layout.get_phase_dir(8).name == "08-config"


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
        # P5 constitution scans VERIFICATION_REPORT.md (not *.py).
        # A thin markdown with no constitution keywords scores 0% —
        # well below the P5 80% threshold.
        (docs / "VERIFICATION_REPORT.md").write_text(
            "# Verification Report\n\nJust some notes.\n"
        )
        with pytest.raises(RuntimeError, match="Constitution check FAILED"):
            run_constitution_check("all", str(docs), current_phase=5,
                                   check_mode="postflight", strict=True)

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
        """P1 threshold=75%: sparse SRS without security keywords fails.

        A minimal SRS with no security vocabulary scores 0% on the security
        dimension; min-of-dimensions bottleneck makes composite = 0% < 75%.
        """
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SRS.md").write_text(
            "# SRS\n\n## FR-01 Quality Gate\nTest coverage and traceability.\n"
            "Acceptance criteria defined. Constitution compliance verified.\n"
        )
        result = run_constitution_check("srs", str(docs), current_phase=1,
                                        check_mode="postflight")
        assert result.phase == 1
        assert not result.passed, (
            "P1 constitution check fails when security keywords are absent "
            "(composite < 75% threshold)"
        )

    def test_phase2_sparse_sad_fails_constitution(self, tmp_path):
        """P2 threshold=80%: SAD with no security keywords fails."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# System Architecture\n\n"
            "## Overview\nDescribes the high-level architecture.\n"
        )
        result = run_constitution_check("sad", str(docs), current_phase=2,
                                        check_mode="postflight")
        assert result.phase == 2
        assert not result.passed, (
            "P2 constitution check fails when security keywords are absent "
            "(composite < 80% threshold)"
        )

    def test_phase3_sparse_source_fails_constitution(self, tmp_path):
        """P3 threshold=80%: Python file with no keywords fails.

        runner.py scans *.py files for P3+; uses check_type='all' so the file is
        not filtered by filename pattern. Content is deliberately keyword-free.
        """
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "sparse.py").write_text(
            "def foo():\n    x = 1\n    y = 2\n    return x + y\n\n\n"
            "def bar():\n    items = [1, 2, 3]\n    total = 0\n"
            "    for item in items:\n        total += item\n    return total\n"
        )
        result = run_constitution_check("all", str(docs), current_phase=3,
                                        check_mode="postflight")
        assert result.phase == 3
        assert not result.passed, (
            "P3 constitution check fails when security and FR-reference keywords are absent "
            "(composite < 80% threshold)"
        )

    def test_phase4_sparse_tests_fail_constitution(self, tmp_path):
        """P4 threshold=80%: Python file with no keywords fails.

        runner.py scans *.py files for P3+; uses check_type='all' so the file is
        not filtered by filename pattern. Content is deliberately keyword-free.
        """
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "sparse.py").write_text(
            "def foo():\n    x = 1\n    y = 2\n    return x + y\n\n\n"
            "def bar():\n    items = [1, 2, 3]\n    total = 0\n"
            "    for item in items:\n        total += item\n    return total\n"
        )
        result = run_constitution_check("all", str(docs), current_phase=4,
                                        check_mode="postflight")
        assert result.phase == 4
        assert not result.passed, (
            "P4 constitution check fails when security and coverage keywords are absent "
            "(composite < 80% threshold)"
        )

    def test_phase4_uses_correctness_only(self, tmp_path):
        # Bug #35 extension: P4 = correctness only. The runner still
        # reports all 4 dimensions in result.dimensions (it's the full
        # config snapshot), but only correctness contributes to the
        # composite score.
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## quality gate\n\n## test coverage\n\n## constitution\n\n"
            "## traceability matrix\n\n## FR-01\n\n## FR-02\n\n## acceptance criteria\n"
        )
        result = run_constitution_check("sad", str(docs), current_phase=4)
        assert result.phase == 4
        # P4 active_dimensions = ["correctness"] only; composite_threshold=30.
        # A well-formed spec doc with all required keywords scores high
        # enough to pass the threshold.
        assert "correctness" in result.dimensions
        assert result.score >= 30.0  # at or above P4 threshold
        assert "coverage" in result.dimensions

    def test_phase5_threshold_80(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "QUALITY.md").write_text(
            "# Quality Report\n\nquality gate test coverage constitution traceability\n"
            "security auth validation HMAC RBAC\n"
        )
        result = run_constitution_check("quality_report", str(docs), current_phase=6,
                                        check_mode="postflight")
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


class TestGetCompletedPhases:
    """Tests for _get_completed_phases() — derives from current_phase (closed-loop)
    with phase_completed as legacy fallback."""

    def test_missing_state_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        assert _get_completed_phases(state_path) == []

    def test_empty_state_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        assert _get_completed_phases(state_path) == []

    def test_no_phase_completed_key_phase1_vacuous(self, tmp_path):
        """Phase 1 (current_phase=1): nothing completed — vacuous pass."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"state": "ACTIVE", "current_phase": 1}')
        assert _get_completed_phases(state_path) == []

    def test_no_phase_completed_key_phase2_implies_p1_completed(self, tmp_path):
        """Closed-loop: current_phase=2 → phase 1 is implicitly completed."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"state": "ACTIVE", "current_phase": 2}')
        assert _get_completed_phases(state_path) == [1]

    def test_no_phase_completed_key_phase4_implies_p1_p2_p3(self, tmp_path):
        """Closed-loop: current_phase=4 → phases 1-3 are implicitly completed."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"state": "ACTIVE", "current_phase": 4}')
        assert _get_completed_phases(state_path) == [1, 2, 3]

    def test_legacy_phase_completed_still_read(self, tmp_path):
        """Legacy fallback: phase_completed key (backward compat)."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"phase_completed": {"1": {}, "2": {}, "3": {}}}')
        assert _get_completed_phases(state_path) == [1, 2, 3]

    def test_out_of_order_keys(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text('{"phase_completed": {"3": {}, "1": {}}}')
        assert _get_completed_phases(state_path) == [1, 3]

    def test_corrupted_state_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("not json")
        assert _get_completed_phases(state_path) == []


class TestPreflightPhaseBoundary:
    """Tests that preflight constitution check respects phase boundaries.

    Core invariant: preflight must scan the MOST RECENT completed phase's
    artifacts (read from state.json.phase_completed), NOT the current phase's
    directory — which may contain stale files from a prior run or be entirely
    empty (the chicken-and-egg bug).
    """

    def _make_phase_dir(self, base: Path, dir_name: str, content: str,
                        file_name: str = "SRS.md"):
        d = base / dir_name
        d.mkdir(parents=True, exist_ok=True)
        (d / file_name).write_text(content)
        return d

    def _make_state(self, base: Path, completed: dict):
        state_dir = base / ".methodology"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(
            json.dumps({"phase_completed": completed})
        )

    def _make_p1_artifact(self, base: Path):
        """Create a P1 SRS.md with all P1 correctness + security keywords."""
        return self._make_phase_dir(
            base, "01-requirements",
            "# Requirements Specification (SRS)\n\n"
            "## FR-01: Quality Gate\n"
            "This requirement defines the quality gate specification.\n"
            "Acceptance criteria: all FRs must have traceability to test cases.\n"
            "Constitution compliance verified with security validation.\n"
            "\n"
            "## FR-02: User Authentication\n"
            "Authentication uses HMAC signature verification with token.\n"
            "RBAC permission model for all API endpoints.\n"
            "TLS encryption required for external communication.\n"
            "Secrets managed via environment variables.\n"
            "\n"
            "## NFR-01: Performance\n"
            "PII must be masked before storage.\n"
            "Security vulnerability assessment required per release.\n",
            "SRS.md"
        )

    def _make_p2_artifact(self, base: Path):
        """Create a P2 SAD.md with all P2 correctness + security keywords."""
        return self._make_phase_dir(
            base, "02-architecture",
            "# Software Architecture Document (SAD)\n\n"
            "## FR-01: Platform Adapter\n"
            "This specification covers the Telegram adapter requirement.\n"
            "FR-02: Signature verification using HMAC for security.\n"
            "NFR-01: Performance traceability ensures architecture completeness.\n"
            "SRS input provides the foundation for this SAD document.\n"
            "\n"
            "## Security Architecture\n"
            "Authentication via HMAC signature validation.\n"
            "Sanitize all user input before processing.\n"
            "Encryption of sensitive data at rest.\n"
            "RBAC permission model with token-based verification.\n"
            "PII masking at input sanitizer boundary.\n"
            "Rate limit enforcement per user.\n"
            "TLS for all external connections.\n"
            "Secret management via environment variables.\n"
            "Security vulnerability scanning in CI pipeline.\n"
            "\n"
            "## Requirements Traceability\n"
            "Traceability matrix maps all FRs to architecture components.\n"
            "Acceptance criteria documented per requirement specification.\n",
            "SAD.md"
        )

    # ── Phase-specific scenarios ──────────────────────────────────────

    def test_p2_preflight_scans_p1_not_p2(self, tmp_path):
        """P2 preflight reads state.json P1 completed, scans 01-requirements/
        instead of 02-architecture/ (which has deliberately poor content)."""
        import warnings
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})
        # Poorly-scoring 02-architecture/ should NOT be scanned
        self._make_phase_dir(tmp_path, "02-architecture",
                             "# Old Arch\n\nno keywords.", "SAD.md")

        # The "sad" check_type request triggers the override warning by design
        # (caller asked for SAD but preflight scans P1 as SRS). That path is
        # covered by test_preflight_warns_when_check_type_overridden — silence
        # here to keep test output clean.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = run_constitution_check("sad", str(tmp_path / "docs"),
                                            current_phase=2, check_mode="preflight")
        assert result.passed, (
            "P2 preflight should scan P1 (01-requirements/) not P2's stale "
            f"02-architecture/ (score={result.score})"
        )

    def test_p3_preflight_scans_p2(self, tmp_path):
        """P3 preflight with P1+P2 completed scans 02-architecture/."""
        import warnings
        self._make_p1_artifact(tmp_path)
        self._make_p2_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}, "2": {"sha": "b"}})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = run_constitution_check("implementation", str(tmp_path / "docs"),
                                            current_phase=3, check_mode="preflight")
        assert result.passed, (
            "P3 preflight should scan P2 (02-architecture/), "
            f"score={result.score}"
        )

    def test_p2_preflight_no_completed_phases_vacuous_pass(self, tmp_path):
        """P2 preflight with state.json but empty phase_completed."""
        # No completed phases → no override possible → no warning expected.
        self._make_state(tmp_path, {})
        self._make_phase_dir(tmp_path, "02-architecture",
                             "# Stale\n\nNo keywords.", "SAD.md")

        result = run_constitution_check("sad", str(tmp_path / "docs"),
                                        current_phase=2, check_mode="preflight")
        assert result.passed is True
        assert result.score == 100.0

    def test_p2_preflight_no_state_json_vacuous_pass(self, tmp_path):
        """P2 preflight without .methodology/state.json."""
        self._make_phase_dir(tmp_path, "02-architecture",
                             "# Stale\n\nNo keywords.", "SAD.md")

        result = run_constitution_check("sad", str(tmp_path / "docs"),
                                        current_phase=2, check_mode="preflight")
        assert result.passed is True
        assert result.score == 100.0

    def test_p1_preflight_vacuous_pass(self, tmp_path):
        """Phase 1 preflight always passes — no prior phase."""
        result = run_constitution_check("srs", str(tmp_path / "docs"),
                                        current_phase=1, check_mode="preflight")
        assert result.passed is True
        assert result.score == 100.0

    def test_p2_preflight_prev_phase_dir_absent_vacuous_pass(self, tmp_path):
        """P1 completed but 01-requirements/ doesn't exist on disk."""
        import warnings
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        # Override warning is expected (caller asked for SAD, P1 is latest);
        # silence here — coverage lives in the dedicated warning tests below.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = run_constitution_check("sad", str(tmp_path / "docs"),
                                            current_phase=2, check_mode="preflight")
        assert result.passed is True
        assert result.score == 100.0

    def test_preflight_strict_mode_raises_on_prev_failure(self, tmp_path):
        """Strict preflight raises when P1 artifacts fail P1 threshold."""
        self._make_phase_dir(tmp_path, "01-requirements",
                             "# X\n\nno keywords here at all"
                             " to exceed minimum length requirement", "SRS.md")
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        with pytest.raises(RuntimeError, match="Constitution check FAILED"):
            run_constitution_check("srs", str(tmp_path / "docs"),
                                   current_phase=2, check_mode="preflight",
                                   strict=True)

    def test_preflight_return_passed_false_when_prev_fails(self, tmp_path):
        """Preflight returns passed=False when P1 artifacts fail."""
        self._make_phase_dir(tmp_path, "01-requirements",
                             "# X\n\nno keywords here at all"
                             " to exceed minimum length requirement", "SRS.md")
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        result = run_constitution_check("srs", str(tmp_path / "docs"),
                                        current_phase=2, check_mode="preflight")
        assert not result.passed

    # ── Postflight invariant ─────────────────────────────────────────

    def test_postflight_still_scans_current_phase(self, tmp_path):
        """Postflight mode unchanged: scans current phase's directory."""
        self._make_p2_artifact(tmp_path)

        result = run_constitution_check("sad", str(tmp_path / "02-architecture"),
                                        current_phase=2, check_mode="postflight")
        assert result.phase == 2
        assert result.passed

    # ── project_root resolution (walk-upward fix) ────────────────────

    def test_preflight_resolves_project_root_from_phase_directory(self, tmp_path):
        """Preflight called with a phase dir path (not docs/) still finds state.json.

        Regression test for the fragile project_root heuristic: callers that
        passed e.g. ``<project>/02-architecture`` used to silently bypass
        preflight because state.json was sought at ``<project>/02-architecture/
        .methodology/state.json``. The walk-upward resolver now finds the real
        project root regardless of which subdirectory was passed.
        """
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        # Pass the phase directory instead of <project>/docs
        result = run_constitution_check(
            "all", str(tmp_path / "02-architecture"),
            current_phase=2, check_mode="preflight",
        )
        assert result.passed, (
            "Preflight should still resolve project root and scan P1 even "
            f"when docs_path is a phase dir (score={result.score})"
        )
        # Verify it actually evaluated artifacts (non-vacuous)
        assert result.dimensions["correctness"] < 100.0 or result.score < 100.0, (
            "Should have scanned real artifacts, not returned vacuous 100%"
        )

    def test_preflight_resolves_project_root_when_passed_root_directly(self, tmp_path):
        """Preflight called with the project root itself (no docs/ suffix)."""
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        result = run_constitution_check(
            "all", str(tmp_path),
            current_phase=2, check_mode="preflight",
        )
        assert result.passed

    # ── check_type override warning ──────────────────────────────────

    def test_preflight_warns_when_check_type_overridden(self, tmp_path):
        """Caller asks for check_type='sad' but P1 is the latest completed →
        preflight will use 'srs' instead, and that override must be surfaced."""
        import warnings
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            run_constitution_check(
                "sad", str(tmp_path / "docs"),
                current_phase=2, check_mode="preflight",
            )
        msgs = [str(w.message) for w in caught]
        assert any("Preflight check_type override" in m for m in msgs), (
            f"Expected override warning; got: {msgs}"
        )

    def test_preflight_no_warning_when_check_type_is_all(self, tmp_path):
        """check_type='all' is a wildcard and must not trigger the warning."""
        import warnings
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            run_constitution_check(
                "all", str(tmp_path / "docs"),
                current_phase=2, check_mode="preflight",
            )
        msgs = [str(w.message) for w in caught]
        assert not any("Preflight check_type override" in m for m in msgs), (
            f"Did not expect override warning for check_type='all'; got: {msgs}"
        )

    def test_preflight_no_warning_when_check_type_matches_prev_phase(self, tmp_path):
        """Caller asks for 'srs' and P1 is the latest completed → no override."""
        import warnings
        self._make_p1_artifact(tmp_path)
        self._make_state(tmp_path, {"1": {"sha": "a"}})

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            run_constitution_check(
                "srs", str(tmp_path / "docs"),
                current_phase=2, check_mode="preflight",
            )
        msgs = [str(w.message) for w in caught]
        assert not any("Preflight check_type override" in m for m in msgs), (
            f"Did not expect override warning when check_type matches; got: {msgs}"
        )


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


class TestMissingKeywords:
    """missing_keywords() — actionable per-dimension keyword gap for a failing
    constitution dimension (which concepts to add), aligned with the scorer's
    file selection and lowercased-substring presence test."""

    def test_absent_keywords_reported(self, tmp_path):
        kws = get_profile().dimension_keywords_for_phase("security", 7)
        assert kws, "profile must define P7 security keywords for this test"
        content = "# Risk Register\n\nWe apply " + kws[0] + " throughout.\n" + "x" * 200
        (tmp_path / "RISK_REGISTER.md").write_text(content, encoding="utf-8")
        miss = missing_keywords(tmp_path, "security", 7)
        expected = [k for k in kws if k.lower() not in content.lower()]
        assert miss == expected
        assert kws[0] not in miss  # present keyword is not reported missing

    def test_all_present_returns_empty(self, tmp_path):
        kws = get_profile().dimension_keywords_for_phase("security", 7)
        content = "# Risk\n" + " ".join(kws) + "\n" + "x" * 200
        (tmp_path / "RISK_REGISTER.md").write_text(content, encoding="utf-8")
        assert missing_keywords(tmp_path, "security", 7) == []

    def test_unknown_dimension_returns_empty(self, tmp_path):
        (tmp_path / "RISK_REGISTER.md").write_text("x" * 200, encoding="utf-8")
        assert missing_keywords(tmp_path, "nonexistent_dim", 7) == []

    def test_missing_deliverable_reports_all(self, tmp_path):
        # P7 scores only RISK_REGISTER.md; if it is absent, every keyword is missing.
        kws = get_profile().dimension_keywords_for_phase("security", 7)
        assert missing_keywords(tmp_path, "security", 7) == kws

    def test_single_file_path_scoped_to_that_file(self, tmp_path):
        # check-constitution --file scores ONE file; missing_keywords must accept a
        # file path and report the gap for that file alone — a sibling holding the
        # other keywords must not mask it (same scope as check_single_file).
        kws = get_profile().dimension_keywords_for_phase("correctness", 2)
        assert kws and len(kws) >= 2, "profile must define P2 correctness keywords"
        target = tmp_path / "ADR.md"
        target_text = "# ADR\n\nThis record references " + kws[0] + ".\n" + "x" * 200
        target.write_text(target_text, encoding="utf-8")
        # Sibling containing ALL keywords — must NOT be scanned when a file is passed.
        (tmp_path / "SIBLING.md").write_text(
            " ".join(kws) + "\n" + "x" * 200, encoding="utf-8")
        miss = missing_keywords(target, "correctness", 2)
        expected = [k for k in kws if k.lower() not in target_text.lower()]
        assert miss == expected
        assert kws[0] not in miss  # the one present keyword is not reported missing
        assert miss, "sibling must not mask target's genuine gaps"

