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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
        sad.write_text("FR-01: something\nFR-002: another\nFR-3: third")
        from scripts.build_traceability import extract_fr_ids_from_sad
        ids = extract_fr_ids_from_sad(sad)
        assert "FR-01" in ids
        assert "FR-02" in ids  # FR-002 normalized
        assert "FR-03" in ids   # FR-3 normalized

    # ------------------------------------------------------------------ Round 25
    def test_build_traceability_nfr_uses_active_test_dir(self, tmp_path):
        """Regression (Round 25): NFR scan must use ProjectLayout.active_test_dir,
        not the hardcoded <project>/tests/ path. Bug: projects with tests under
        03-development/tests/ (taskq layout) silently produced empty
        nfr_test_coverage, rendering all NFR rows as PENDING in
        TRACEABILITY_MATRIX.md even when tests legitimately reference the NFR.
        """
        from scripts.build_traceability import build_traceability

        # SRS.md declares NFR-01 (the harness derives srs_path from here)
        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### NFR-01: performance\n", encoding="utf-8",
        )
        # Tests live ONLY under 03-development/tests (taskq layout)
        tests_dir = tmp_path / "03-development" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_nfr01.py").write_text(
            '"""[NFR-01] p95 latency under threshold."""\n'
            "def test_latency():\n    assert True\n",
            encoding="utf-8",
        )
        # Sanity: <project>/tests does NOT exist (the trap that caught the bug)
        assert not (tmp_path / "tests").exists()
        # Seed 02-architecture/SAD.md so build_traceability has a sad_path
        (tmp_path / "02-architecture").mkdir(parents=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "FR-01: stub\n", encoding="utf-8",
        )

        rt = build_traceability(tmp_path)
        nfr_data = getattr(rt, "nfr_data", {})
        assert "NFR-01" in nfr_data.get("nfr_ids", []), (
            f"SRS.md NFR-01 not extracted; got nfr_ids={nfr_data.get('nfr_ids')!r}"
        )
        cov = nfr_data["nfr_test_coverage"]
        assert "NFR-01" in cov, (
            f"NFR-01 has no coverage — bug at build_traceability.py:135 "
            f"still present (scanned wrong path); got cov={cov!r}"
        )
        assert len(cov["NFR-01"]) >= 1
        assert any("test_nfr01.py" in t for t in cov["NFR-01"])

    def test_build_traceability_nfr_markdown_verified(self, tmp_path):
        """Round 25: After the fix, NFR rows in TRACEABILITY_MATRIX.md show
        VERIFIED (not PENDING) when tests reference the NFR — even when the
        tests live under 03-development/tests/ instead of <project>/tests/.
        """
        from scripts.build_traceability import (
            build_traceability,
            generate_markdown_matrix,
        )

        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### NFR-01: performance\n", encoding="utf-8",
        )
        tests_dir = tmp_path / "03-development" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_nfr.py").write_text(
            '"""tests for NFR-01 latency."""\n', encoding="utf-8",
        )
        (tmp_path / "02-architecture").mkdir(parents=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "FR-01\n", encoding="utf-8",
        )

        rt = build_traceability(tmp_path)
        matrix_path = tmp_path / "TRACEABILITY_MATRIX.md"
        generate_markdown_matrix(rt, matrix_path)
        body = matrix_path.read_text(encoding="utf-8")
        assert "| NFR-01 | test_nfr.py | VERIFIED |" in body, (
            f"NFR-01 should be VERIFIED after Round 25 fix; matrix body:\n{body}"
        )
        assert "| NFR-01 | — | PENDING |" not in body, (
            f"NFR-01 should NOT be PENDING; matrix body:\n{body}"
        )

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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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
        rt = build_traceability(proj, sad_path=proj / "02-architecture" / "SAD.md")

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
        rt = build_traceability(proj, sad_path=proj / "02-architecture" / "SAD.md")

        downstream = rt.get_downstream("FR-01")
        assert len(downstream["code"]) > 0
        assert len(downstream["test"]) > 0

    def test_build_traceability_completeness(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability
        rt = build_traceability(proj, sad_path=proj / "02-architecture" / "SAD.md")

        c = rt.verify_completeness()
        assert c["total_requirements"] >= 2
        assert "FR-02" in c["missing_mappings"]["fr_without_code"]
        assert "FR-02" in c["missing_mappings"]["fr_without_test"]

    def test_generate_markdown_matrix(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.build_traceability import build_traceability, generate_markdown_matrix
        rt = build_traceability(proj, sad_path=proj / "02-architecture" / "SAD.md")

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
        from core.traceability.scanner import _norm_fr
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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
        sad.write_text("FR-01: auth\nFR-02: payment\n")
        (tmp_path / "mod_a.py").write_text('""" [FR-01] Auth module """')
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        return tmp_path

    def test_check_traceability_all_covered(self, tmp_path):
        proj = self._make_project(tmp_path)
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(proj, sad_path=proj / "02-architecture" / "SAD.md")
        assert report["sad_frs"] == 2
        assert report["coded"] == 1
        assert report["tested"] == 1
        assert "FR-02" in report["untested"]
        assert "FR-02" in report["uncoded"]

    def test_check_traceability_complete(self, tmp_path):
        """When all FRs have code + test, complete=True."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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

    def test_preflight_traceability_zero_fr_project_passes_at_p5(self, tmp_path, monkeypatch):
        """A pure-library project with zero FRs must vacuously pass
        traceability at P5+ — total==0 means nothing is untested or
        uncoded, not 'incomplete'. Attestation status is mocked clean so
        this test isolates the (unrelated) `complete` computation.

        SAD.md must actually exist (declaring no FRs) — a project that
        legitimately has zero requirements still produced an architecture
        doc at P2. Omitting SAD.md entirely is a different, unrelated
        case (see test_preflight_traceability_missing_sad_at_p5_blocks)."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text("# SAD\n\nNo FRs.\n")
        monkeypatch.setattr(
            "scripts.verify_trace_attestation.verify_attestation",
            lambda _project: (0, "clean"),
        )
        hooks = PhaseHooks(str(tmp_path), phase=5)
        result = hooks.preflight_traceability()
        assert result["passed"] is True

    def test_preflight_traceability_missing_sad_at_p5_blocks(self, tmp_path, monkeypatch):
        """Zero FRs because SAD.md itself is missing is a scan FAILURE,
        not 'no requirements' — must not silently pass at P5+ (the
        vacuous-pass rule above only applies when SAD.md exists)."""
        monkeypatch.setattr(
            "scripts.verify_trace_attestation.verify_attestation",
            lambda _project: (0, "clean"),
        )
        hooks = PhaseHooks(str(tmp_path), phase=5)
        result = hooks.preflight_traceability()
        assert result["passed"] is False

    def test_check_traceability_test_from_content(self, tmp_path):
        """Test files without FR in filename but with FR in content."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
        sad.write_text("FR-01\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_misc.py").write_text('""" Covers [FR-01] """')
        from scripts.check_spec_trace import check_traceability
        _, report = check_traceability(tmp_path, sad_path=sad)
        # FR-01 found via content scan in test_misc.py
        assert report["tested"] >= 1

    def test_main_block_flag(self, tmp_path):
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
        sad.write_text("FR-01\n")
        (tmp_path / "mod.py").write_text('""" [FR-01] """')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr_01.py").write_text('""" [FR-01] Tests """')
        from scripts.check_spec_trace import main
        rc = main(["--project", str(tmp_path), "--sad", str(sad)])
        assert rc == 0

    def test_main_json_flag(self, tmp_path):
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
        sad.write_text("FR-01\n")
        from scripts.check_spec_trace import main
        rc = main(["--project", str(tmp_path), "--sad", str(sad), "--json"])
        assert rc == 0

    def test_export_report(self, tmp_path):
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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

    def test_p4_info_not_blocking(self, tmp_path):
        """P4 traceability is informational — passes even with gaps (tests being built)."""
        h = self._make_hooks(tmp_path, phase=4)
        result = h.preflight_traceability()
        assert result["passed"] is True
        assert result["blocking"] is False

    def test_p5_blocks_on_gaps(self, tmp_path):
        """P5+ traceability blocks if gaps exist."""
        h = self._make_hooks(tmp_path, phase=5)
        result = h.preflight_traceability()
        # FR-02 has no code and no test → should fail at P5
        assert result["passed"] is False
        assert result["blocking"] is True

    def test_p4_passes_when_complete(self, tmp_path):
        """P4 passes when all FRs have code + test."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        sad = tmp_path / "02-architecture" / "SAD.md"
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
        """Graceful fallback when check_traceability can't be imported."""
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        with patch("core.traceability.scanner.check_traceability",
                   side_effect=ImportError):
            result = h.preflight_traceability()
            assert result["passed"] is True
            assert result.get("skipped") is True

    def test_traceability_import_error_blocks_p5(self, tmp_path):
        """ImportError at P5+ → blocks (was silent-pass before fix)."""
        h = PhaseHooks(str(tmp_path), phase=5, enable_kill_switch=False)
        with patch("core.traceability.scanner.check_traceability",
                   side_effect=ImportError):
            result = h.preflight_traceability()
            assert result["passed"] is False

    def test_traceability_runtime_error_blocks_p5(self, tmp_path):
        """RuntimeError at P5+ → blocks."""
        h = PhaseHooks(str(tmp_path), phase=5, enable_kill_switch=False)
        with patch("core.traceability.scanner.check_traceability",
                   side_effect=RuntimeError("crash")):
            result = h.preflight_traceability()
            assert result["passed"] is False

    def test_traceability_runtime_error_passes_p3(self, tmp_path):
        """RuntimeError at P3 → passes (informational only)."""
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        with patch("core.traceability.scanner.check_traceability",
                   side_effect=RuntimeError("crash")):
            result = h.preflight_traceability()
            assert result["passed"] is True


# ---------------------------------------------------------------------------
# preflight_fsm_state — corrupt state.json recovery
# ---------------------------------------------------------------------------


class TestPreflightFSMState:
    def test_corrupt_json_returns_corrupt_state(self, tmp_path):
        """Malformed state.json → passed=False, state=CORRUPT."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text("NOT VALID JSON!!!")
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        result = h.preflight_fsm_check()
        assert result["passed"] is False
        assert result["state"] == "CORRUPT"


# ---------------------------------------------------------------------------
# preflight_drift_detection — exception handling by phase
# ---------------------------------------------------------------------------


class TestPreflightDriftDetection:
    def test_drift_exception_passes_p3(self, tmp_path):
        """Drift module exception at P3 → passes (informational)."""
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        with patch("detection.DriftDetector") as mock_dd:
            mock_dd.side_effect = RuntimeError("module crash")
            result = h.preflight_drift_detection()
            assert result["passed"] is True

    def test_drift_exception_blocks_p4(self, tmp_path):
        """Drift module exception at P4+ → blocks (was silent-pass)."""
        h = PhaseHooks(str(tmp_path), phase=4, enable_kill_switch=False)
        with patch("detection.DriftDetector") as mock_dd:
            mock_dd.side_effect = RuntimeError("module crash")
            result = h.preflight_drift_detection()
            assert result["passed"] is False


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# preflight_previous_phase_artifacts — import error
# ---------------------------------------------------------------------------


class TestPreflightArtifactChain:
    def test_import_error_returns_false(self, tmp_path):
        """PhaseArtifactRegistry cannot be imported → preflight returns passed=False."""
        from core.phase_hooks import PhaseHooks
        h = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
        with patch("core.quality_gate.phase_artifact_enforcer.PhaseArtifactRegistry",
                   side_effect=ImportError("no module")):
            result = h.preflight_previous_phase_artifacts()
            assert result["passed"] is False


# (TestPreflightGapAnalysis removed with preflight_gap_analysis — the
#  check had no failing branch by construction; the on-demand
#  run-gap-analysis command with its real exit-2 path remains tested.)


class TestOverlayPathResolution:
    """Regression: preflight_traceability must load overlay from project root.

    Bug: a previous fix passed ``self.project_path / ".methodology" /
    TRACEABILITY_MATRIX.overlay.yaml"`` to ``load_overlay()``. The file does
    NOT live there — every other call site (harness_cli.py,
    scripts/build_trace_attestation.py, scripts/build_traceability.py)
    uses the project root, and the module docstring + TRACEABILITY_MATRIX.md
    both document the root path. The wrong path silently disabled the
    overlay filter that drops manually-VERIFIED FRs from the untested list.
    """

    def _make_hooks(self, tmp_path, phase=3):
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "FR-01: implemented\nFR-02: manual\n"
        )
        (tmp_path / "mod_a.py").write_text('"""[FR-01]"""\n')
        tests = tmp_path / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_fr_01.py").write_text('"""[FR-01]"""\n')
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(exist_ok=True)
        (method_dir / "state.json").write_text(
            f'{{"state": "ACTIVE", "current_phase": {phase}}}'
        )
        return PhaseHooks(str(tmp_path), phase=phase, enable_kill_switch=False)

    def test_overlay_at_project_root_filters_untested(self, tmp_path):
        """Overlay at project root: FRs marked Manual are dropped from untested list.

        The preflight predicate (phase_hooks.py:395) checks
        ``"Manual" in str(row.get("test_files", []))``. Putting the marker
        in the overlay's test_files list is the documented way to flag an
        FR as manually-tested. This test asserts the path fix (overlay at
        project root) makes that filter actually fire.
        """
        h = self._make_hooks(tmp_path, phase=3)

        # Baseline: FR-02 has no code/test → appears in untested/uncoded.
        baseline = h.preflight_traceability()
        assert "FR-02" in baseline["untested"], (
            f"Sanity check: FR-02 should be untested without overlay, "
            f"got {baseline['untested']!r}"
        )

        # Place overlay at project root (the documented location).
        overlay = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
        overlay.write_text(
            "schema: harness/traceability/overlay/v1\n"
            "overrides:\n"
            "  - fr_id: FR-02\n"
            "    test_files:\n"
            "      - \"Manual: documented in SRS §3.2\"\n"
        )

        h2 = self._make_hooks(tmp_path, phase=3)
        result = h2.preflight_traceability()
        assert "FR-02" not in result["untested"], (
            f"Overlay at project root did not filter FR-02 from untested. "
            f"This indicates the overlay path in phase_hooks.py is wrong. "
            f"Got untested={result['untested']!r}"
        )

    def test_overlay_at_methodology_subdir_is_ignored(self, tmp_path):
        """Overlay at .methodology/TRACEABILITY_MATRIX.overlay.yaml must NOT be loaded.

        The previous (broken) fix looked here. Documented location is project root.
        """
        h = self._make_hooks(tmp_path, phase=3)

        # Place overlay at the WRONG path only.
        bad = tmp_path / ".methodology" / "TRACEABILITY_MATRIX.overlay.yaml"
        bad.write_text(
            "schema: harness/traceability/overlay/v1\n"
            "overrides:\n"
            "  - fr_id: FR-02\n"
            "    status: verified\n"
        )

        result = h.preflight_traceability()
        assert "FR-02" in result["untested"], (
            f"Overlay at .methodology/ should NOT be loaded; FR-02 must remain "
            f"in untested. Got untested={result['untested']!r}"
        )
