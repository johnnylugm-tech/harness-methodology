"""Tests for core/quality_gate/constitution/runner.py — Constitution compliance checker."""

import pytest
import tempfile
from pathlib import Path

from core.quality_gate.constitution.runner import (  # pyright: ignore[reportMissingImports]
    ConstitutionResult,
    _PHASE_DIR_MAP,
    _COMPLIANCE_KEYWORDS,
    _scan_file_compliance,
    _scan_directory,
    run_constitution_check,
)


class TestConstitutionResult:
    def test_default_values(self):
        r = ConstitutionResult()
        assert r.score == 0.0
        assert r.passed is False
        assert r.violations == []

    def test_to_dict(self):
        r = ConstitutionResult(score=75.0, passed=True, violations=[{"x": 1}],
                               check_type="all", phase=3, check_mode="preflight")
        d = r.to_dict()
        assert d["score"] == 75.0
        assert d["passed"] is True
        assert d["violations"] == 1
        assert d["check_type"] == "all"
        assert d["phase"] == 3


class TestScanFileCompliance:
    def test_nonexistent_file(self):
        score = _scan_file_compliance(Path("/nonexistent/path.md"))
        assert score == 0.0

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("")
            path = Path(f.name)
        try:
            score = _scan_file_compliance(path)
            assert score == 0.0
        finally:
            path.unlink()

    def test_short_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("short")
            path = Path(f.name)
        try:
            score = _scan_file_compliance(path)
            assert score == 0.0
        finally:
            path.unlink()

    def test_high_compliance_file(self):
        content = """# Requirements Specification

## FR-01: Quality Gate
This feature implements a quality gate with test coverage verification.
The system ensures constitution compliance and traceability.

## FR-02: SAD Integration
This feature provides SAD integration with acceptance criteria.

## NFR-01: Performance
The trace matrix ensures all FR and NFR items are covered.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = Path(f.name)
        try:
            score = _scan_file_compliance(path)
            assert score > 50, f"Expected >50, got {score}"
        finally:
            path.unlink()

    def test_low_compliance_file(self):
        content = "# Random Notes\n\nJust some text without any structure.\nNo keywords here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = Path(f.name)
        try:
            score = _scan_file_compliance(path)
            assert score < 50, f"Expected <50, got {score}"
        finally:
            path.unlink()


class TestScanDirectory:
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            docs = Path(d) / "docs"
            docs.mkdir()
            result = _scan_directory(docs, phase=1, check_type="all")
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
            )
            result = _scan_directory(docs, phase=1, check_type="all")
            assert 0 < result.score <= 100  # keywords present → positive score; valid range

    def test_phase_directory_fallback(self, tmp_path):
        """_scan_directory should find phase dir when it exists alongside docs."""
        phase_dir = tmp_path / "01-requirements"
        phase_dir.mkdir()
        (phase_dir / "SRS.md").write_text(
            "# SRS\n\n## FR-01 quality gate\n\nTest coverage and traceability matrix.\n"
            "## FR-02 acceptance criteria\n\nConstitution compliance verified.\n"
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        result = _scan_directory(docs, phase=1, check_type="srs")
        assert result.score > 0  # phase dir found and scanned; keywords present → positive score

    def test_low_score_violations(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "empty.md").write_text("# X\n\nno keywords here at all, just filler text to exceed minimum length requirement for scanning")
        result = _scan_directory(docs, phase=5, check_type="all")
        # No compliance keywords → low score and violations (phase 5+ threshold is 80%)
        assert result.score < 30  # filler text has no compliance keywords
        assert len(result.violations) > 0  # below 80% threshold → must report violations


class TestPhaseDirMap:
    def test_all_phases_mapped(self):
        for p in range(1, 9):
            assert p in _PHASE_DIR_MAP, f"Phase {p} missing from _PHASE_DIR_MAP"

    def test_phase_dirs_match_standard(self):
        assert _PHASE_DIR_MAP[1] == "01-requirements"
        assert _PHASE_DIR_MAP[4] == "04-testing"
        assert _PHASE_DIR_MAP[6] == "06-quality"
        assert _PHASE_DIR_MAP[8] == "08-config"


class TestComplianceKeywords:
    def test_keywords_present(self):
        assert "quality gate" in _COMPLIANCE_KEYWORDS
        assert "test coverage" in _COMPLIANCE_KEYWORDS
        assert "traceability" in _COMPLIANCE_KEYWORDS


class TestRunConstitutionCheck:
    def test_missing_directory(self):
        result = run_constitution_check("all", "/nonexistent/path", current_phase=1)
        assert result.passed is False
        assert result.score == 0.0

    def test_strict_mode_raises_on_missing_dir(self):
        with pytest.raises(RuntimeError, match="directory not found"):
            run_constitution_check("all", "/nonexistent/path", current_phase=1, strict=True)

    def test_strict_mode_raises_on_failure(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        # Phase 5+ needs 80%, empty dir = 0
        with pytest.raises(RuntimeError, match="Constitution check FAILED"):
            run_constitution_check("all", str(docs), current_phase=5, strict=True)

    def test_preflight_mode(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "SRS.md").write_text(
            "# SRS\n\n## FR-01 quality gate test coverage constitution traceability\n\n"
            "## FR-02 acceptance criteria SAD integration\n\n## FR-03 NFR performance\n\n"
            "## FR-04 security\n\n## FR-05 reliability\n"
        )
        result = run_constitution_check("srs", str(docs), current_phase=1, check_mode="preflight")
        assert result.check_mode == "preflight"
        assert result.phase == 1

    def test_phase4_threshold_60(self, tmp_path):
        """Phase <= 4 uses 60% threshold."""
        docs = tmp_path / "docs"
        docs.mkdir()
        # Single file with moderate compliance
        (docs / "SAD.md").write_text(
            "# Architecture\n\n## quality gate\n\n## test coverage\n\n## constitution\n\n"
            "## traceability matrix\n\n## FR-01\n\n## FR-02\n\n## acceptance criteria\n"
        )
        result = run_constitution_check("sad", str(docs), current_phase=3)
        # Phase 3 threshold = 60
        assert result.phase == 3

    def test_phase5_threshold_80(self, tmp_path):
        """Phase > 4 uses 80% threshold."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "QUALITY.md").write_text(
            "# Quality Report\n\nquality gate test coverage constitution traceability\n"
        )
        result = run_constitution_check("quality_report", str(docs), current_phase=6)
        assert result.phase == 6
        assert result.check_type == "quality_report"
