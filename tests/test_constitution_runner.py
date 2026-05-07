"""Tests for core/quality_gate/constitution/runner.py — Constitution compliance checker."""

import pytest
import tempfile
from pathlib import Path

from core.quality_gate.constitution.runner import (  # pyright: ignore[reportMissingImports]
    ConstitutionResult,
    _PHASE_DIR_MAP,
    _scan_file_compliance,
    _scan_directory,
    _dimensions_for_phase,
    run_constitution_check,
)


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


class TestScanDirectory:
    def test_empty_directory_phase1_skips(self):
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            result = _scan_directory(docs, phase=1, check_type="all")
            assert result.score == 100.0
            assert result.passed is True
            assert len(result.violations) == 0

    def test_empty_directory_phase5_fails(self):
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            result = _scan_directory(docs, phase=5, check_type="all")
            assert result.score == 0.0
            assert result.passed is False
            assert len(result.violations) == 1

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

    def test_dimensions_in_result(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## FR-01 quality gate test coverage\n\n"
            "Constitution traceability with HMAC signature verification.\n"
            "Acceptance criteria defined with pytest unit test coverage.\n"
            "## FR-02 security auth validation RBAC permission token encrypt\n"
        )
        result = _scan_directory(docs, phase=3, check_type="sad")
        assert "correctness" in result.dimensions
        assert "security" in result.dimensions
        assert "maintainability" in result.dimensions
        assert "coverage" in result.dimensions


class TestDimensionsForPhase:
    def test_phase1_2_only_correctness_security(self):
        assert _dimensions_for_phase(1) == ["correctness", "security"]
        assert _dimensions_for_phase(2) == ["correctness", "security"]

    def test_phase3_plus_all_four_dimensions(self):
        for p in range(3, 9):
            dims = _dimensions_for_phase(p)
            assert "correctness" in dims
            assert "security" in dims
            assert "maintainability" in dims
            assert "coverage" in dims


class TestPhaseDirMap:
    def test_all_phases_mapped(self):
        for p in range(1, 9):
            assert p in _PHASE_DIR_MAP, f"Phase {p} missing from _PHASE_DIR_MAP"

    def test_phase_dirs_match_standard(self):
        assert _PHASE_DIR_MAP[1] == "01-requirements"
        assert _PHASE_DIR_MAP[4] == "04-testing"
        assert _PHASE_DIR_MAP[6] == "06-quality"
        assert _PHASE_DIR_MAP[8] == "08-config"


class TestRunConstitutionCheck:
    def test_missing_directory_phase1_skips(self):
        result = run_constitution_check("all", "/nonexistent/path", current_phase=1)
        assert result.passed is True
        assert result.score == 100.0

    def test_missing_directory_phase3_fails(self):
        result = run_constitution_check("all", "/nonexistent/path", current_phase=3)
        assert result.passed is False
        assert result.score == 0.0

    def test_strict_mode_raises_on_missing_dir(self):
        with pytest.raises(RuntimeError, match="directory not found"):
            run_constitution_check("all", "/nonexistent/path", current_phase=3, strict=True)

    def test_strict_mode_raises_on_failure(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
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

    def test_phase3_uses_all_dimensions(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## quality gate\n\n## test coverage\n\n## constitution\n\n"
            "## traceability matrix\n\n## FR-01\n\n## FR-02\n\n## acceptance criteria\n"
            "## security auth validation RBAC HMAC signature encrypt\n"
            "## pytest unit test mock fixture assert coverage report\n"
        )
        result = run_constitution_check("sad", str(docs), current_phase=3)
        assert result.phase == 3
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
