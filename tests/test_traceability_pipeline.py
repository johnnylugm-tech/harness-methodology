"""
Tests for ASPICE traceability pipeline: build → check → preflight.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.requirement_traceability import (
    TraceStatus
)
from core.phase_hooks import PhaseHooks


# ---------------------------------------------------------------------------
# build_traceability tests
# ---------------------------------------------------------------------------

class TestBuildTraceability:
    """Tests for scripts/build_traceability.py."""

    def _make_project(self, tmp_path):
        """Create a minimal project with SAD.md + annotated code + tests."""
        sad = tmp_path / "SAD.md"
        sad.write_text("Requirements: FR-01, FR-02, FR-03\n\n"
                       "| FR-01 | `mod_a.py` |\n"
                       "| FR-02 | `mod_b.py` |\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "mod_a.py").write_text('""" [FR-01] Module A """\ndef run(): pass')
        (src / "mod_b.py").write_text('""" Module B — no FR tag """\ndef run(): pass')
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fr_01.py").write_text('""" [FR-01] Tests for FR-01 """\ndef test_a(): pass')
        (tests_dir / "test_other.py").write_text('""" Generic tests """\ndef test_x(): pass')
        return tmp_path

    def test_extract_fr_ids_from_sad(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01: something\nFR-002: another\nFR-3: third")
        from scripts.build_traceability import extract_fr_ids_from_sad
        ids = extract_fr_ids_from_sad(sad)
        assert "FR-01" in ids
        assert "FR-02" in ids  # FR-002 normalized
        assert "FR-03" in ids   # FR-3 normalized

    def test_extract_fr_ids_from_sad_missing(self, tmp_path):
        from scripts.build_traceability import extract_fr_ids_from_sad
        assert extract_fr_ids_from_sad(tmp_path / "nonexistent.md") == []

    def test_scan_python_fr_annotations(self, tmp_path):
        (tmp_path / "mod_a.py").write_text('""" [FR-01] [FR-02] """')
        (tmp_path / "mod_b.py").write_text('""" no FR tags """')
        from scripts.build_traceability import scan_python_fr_annotations
        result = scan_python_fr_annotations(tmp_path)
        assert "FR-01" in result
        assert "FR-02" in result
        assert "mod_a.py" in result["FR-01"]

    def test_scan_python_skips_venv(self, tmp_path):
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "third.py").write_text('""" [FR-99] """')
        from scripts.build_traceability import scan_python_fr_annotations
        result = scan_python_fr_annotations(tmp_path)
        assert "FR-99" not in result

    def test_scan_test_fr_coverage_from_filename(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fr_01.py").write_text("")
        (tests_dir / "test_fr_002.py").write_text("")  # 3-digit
        from scripts.build_traceability import scan_test_fr_coverage
        result = scan_test_fr_coverage(tests_dir)
        assert "FR-01" in result
        assert "FR-02" in result  # FR-002 normalized

    def test_scan_test_fr_coverage_from_content(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_misc.py").write_text('""" Tests for [FR-03] and [FR-04] """')
        from scripts.build_traceability import scan_test_fr_coverage
        result = scan_test_fr_coverage(tests_dir)
        assert "FR-03" in result
        assert "FR-04" in result

    def test_scan_sad_fr_modules(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("| FR-01 | `core/auth.py` | auth module |\n"
                       "| FR-02 | `lib/utils.py` | utility |\n")
        from scripts.build_traceability import scan_sad_fr_modules
        result = scan_sad_fr_modules(sad)
        assert "FR-01" in result
        assert "core/auth.py" in result["FR-01"]
        assert "FR-02" in result
        assert "lib/utils.py" in result["FR-02"]

    def test_build_traceability_populates_model(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability
        rt = build_traceability(proj, sad_path=proj / "SAD.md")

        assert "FR-01" in rt.requirements
        assert rt.requirements["FR-01"].status == TraceStatus.VERIFIED
        assert "FR-02" in rt.requirements
        # FR-02 has SAD module mapping but no code annotation → IN_PROGRESS
        assert rt.requirements["FR-02"].status == TraceStatus.IN_PROGRESS
        assert "FR-03" in rt.requirements
        assert rt.requirements["FR-03"].status == TraceStatus.PENDING

    def test_build_traceability_links(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability
        rt = build_traceability(proj, sad_path=proj / "SAD.md")

        downstream = rt.get_downstream("FR-01")
        assert len(downstream["code"]) > 0
        assert len(downstream["test"]) > 0

    def test_build_traceability_completeness(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability
        rt = build_traceability(proj, sad_path=proj / "SAD.md")

        c = rt.verify_completeness()
        assert c["total_requirements"] >= 2
        assert "FR-02" in c["missing_mappings"]["fr_without_code"]
        assert "FR-02" in c["missing_mappings"]["fr_without_test"]

    def test_generate_markdown_matrix(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability, generate_markdown_matrix
        rt = build_traceability(proj, sad_path=proj / "SAD.md")

        matrix_path = tmp_path / "TRACEABILITY_MATRIX.md"
        generate_markdown_matrix(rt, matrix_path)
        assert matrix_path.exists()
        content = matrix_path.read_text()
        assert "# Traceability Matrix" in content
        assert "FR-01" in content
        assert "ASPICE Compliance" in content
        assert "SWE.3" in content

    def test_build_traceability_no_sad(self, tmp_path):
        """Works with no SAD.md — only FRs from code annotations."""
        (tmp_path / "mod.py").write_text('""" [FR-05] """')
        from scripts.build_traceability import build_traceability
        rt = build_traceability(tmp_path)
        assert "FR-05" in rt.requirements

    def test_norm_fr_normalization(self):
        from scripts.build_traceability import _norm_fr
        assert _norm_fr("1") == "FR-01"
        assert _norm_fr("01") == "FR-01"
        assert _norm_fr("001") == "FR-01"
        assert _norm_fr("99") == "FR-99"


# ---------------------------------------------------------------------------
# check_spec_trace (upgraded) tests
# ---------------------------------------------------------------------------

class TestCheckSpecTraceUpgraded:
    """Tests for upgraded scripts/check_spec_trace.py."""

    def _make_project(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01: auth\nFR-02: payment\n")
        (tmp_path / "mod_a.py").write_text('""" [FR-01] Auth module """')
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        return tmp_path

    def test_check_traceability_all_covered(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(proj, sad_path=proj / "SAD.md")
        assert report["sad_frs"] == 2
        assert report["coded"] == 1
        assert report["tested"] == 1
        assert "FR-02" in report["untested"]
        assert "FR-02" in report["uncoded"]

    def test_check_traceability_complete(self, tmp_path):
        """When all FRs have code + test, complete=True."""
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(tmp_path, sad_path=sad)
        assert report["complete"] is True
        assert report["untested"] == []
        assert report["uncoded"] == []

    def test_check_traceability_no_sad(self, tmp_path):
        """Only FRs from code annotations when SAD.md is missing."""
        (tmp_path / "mod.py").write_text('""" [FR-07] """')
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(tmp_path)
        assert report["sad_frs"] == 0
        assert report["coded"] == 1

    def test_check_traceability_empty_project(self, tmp_path):
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(tmp_path)
        assert report["total"] == 0
        assert report["complete"] is True

    def test_check_traceability_test_from_content(self, tmp_path):
        """Test files without FR in filename but with FR in content."""
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_misc.py").write_text('""" Covers [FR-01] """')
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(tmp_path, sad_path=sad)
        # FR-01 found via content scan in test_misc.py
        assert report["tested"] >= 1

    def test_main_block_flag(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\nFR-02\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" Tests """')
        from scripts.check_spec_trace import main
        # --block exits 1 when gaps exist
        rc = main(["--project", str(tmp_path), "--sad", str(sad), "--block"])
        assert rc == 1

    def test_main_passes_when_complete(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        from scripts.check_spec_trace import main
        rc = main(["--project", str(tmp_path), "--sad", str(sad)])
        assert rc == 0

    def test_main_json_flag(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        from scripts.check_spec_trace import main
        rc = main(["--project", str(tmp_path), "--sad", str(sad), "--json"])
        assert rc == 0

    def test_export_report(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        export_path = tmp_path / "report.json"
        from scripts.check_spec_trace import main
        rc = main(["--project", str(tmp_path), "--sad", str(sad),
                    "--export", str(export_path)])
        assert rc == 0
        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert "completeness" in data


# ---------------------------------------------------------------------------
# preflight_traceability tests
# ---------------------------------------------------------------------------

class TestPreflightTraceability:
    """Tests for PhaseHooks.preflight_traceability()."""

    def _make_hooks(self, tmp_path, phase=3):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(
            f'{{"state": "ACTIVE", "current_phase": {phase}}}'
        )
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01: test requirement\nFR-02: another\n")
        (tmp_path / "mod_a.py").write_text('""" [FR-01] Module A """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" [FR-01] Tests for FR-01 """')
        h = PhaseHooks(str(tmp_path), phase=phase, enable_kill_switch=False)
        return h

    def test_p1_skips_traceability(self, tmp_path):
        h = PhaseHooks(str(tmp_path), phase=1, enable_kill_switch=False)
        result = h.preflight_traceability()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p2_skips_traceability(self, tmp_path):
        h = PhaseHooks(str(tmp_path), phase=2, enable_kill_switch=False)
        result = h.preflight_traceability()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p3_info_not_blocking(self, tmp_path):
        """P3 traceability is informational — passes even with gaps."""
        h = self._make_hooks(tmp_path, phase=3)
        result = h.preflight_traceability()
        assert result["passed"] is True
        assert result["blocking"] is False

    def test_p4_blocks_on_gaps(self, tmp_path):
        """P4+ traceability blocks if gaps exist."""
        h = self._make_hooks(tmp_path, phase=4)
        result = h.preflight_traceability()
        # FR-02 has no code and no test → should fail at P4
        assert result["passed"] is False
        assert result["blocking"] is True

    def test_p4_passes_when_complete(self, tmp_path):
        """P4 passes when all FRs have code + test."""
        sad = tmp_path / "SAD.md"
        sad.write_text("FR-01\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(
            '{"state": "ACTIVE", "current_phase": 4}'
        )
        h = PhaseHooks(str(tmp_path), phase=4, enable_kill_switch=False)
        result = h.preflight_traceability()
        assert result["passed"] is True

    def test_preflight_all_includes_traceability(self, tmp_path):
        """preflight_all() result dict includes 'traceability' key."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(
            '{"state": "ACTIVE", "current_phase": 2}'
        )
        h = PhaseHooks(str(tmp_path), phase=2, enable_kill_switch=False)
        result = h.preflight_all()
        assert "traceability" in result["details"]
        assert result["details"]["traceability"].get("skipped") is True

    def test_traceability_includes_completeness(self, tmp_path):
        h = self._make_hooks(tmp_path, phase=3)
        result = h.preflight_traceability()
        assert "completeness" in result
        assert "total_frs" in result
        assert result["total_frs"] >= 1

    def test_traceability_reports_untested(self, tmp_path):
        h = self._make_hooks(tmp_path, phase=3)
        result = h.preflight_traceability()
        assert "FR-02" in result["untested"]
        assert "FR-02" in result["uncoded"]

    def test_traceability_handles_import_error(self, tmp_path):
        """Graceful fallback when check_spec_trace can't be imported."""
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        with patch("scripts.check_spec_trace.check_traceability",
                   side_effect=ImportError):
            result = h.preflight_traceability()
            assert result["passed"] is True
            assert result.get("skipped") is True
