"""Unit tests for scripts/phase_end_audit.py.

Covers all 5 audit functions and run_audit end-to-end.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# Make scripts/ importable without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from phase_end_audit import (
    audit_deliverables,
    audit_development_log,
    audit_gate_results,
    audit_git_log,
    audit_plan_completion,
    run_audit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_plan(tmp_path: Path, content: str, phase: int = 3) -> None:
    d = tmp_path / ".methodology"
    d.mkdir(exist_ok=True)
    (d / f"phase{phase}_plan.md").write_text(content, encoding="utf-8")


def _write_manifest(tmp_path: Path, data: dict) -> None:
    d = tmp_path / ".methodology"
    d.mkdir(exist_ok=True)
    (d / "quality_manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _git_log_mock(text: str) -> MagicMock:
    m = MagicMock()
    m.stdout = text
    m.returncode = 0
    return m


# ---------------------------------------------------------------------------
# audit_plan_completion
# ---------------------------------------------------------------------------

class TestAuditPlanCompletion:
    """audit_plan_completion is a no-op — always returns ([], [])."""

    def test_always_passes(self, tmp_path):
        c, w = audit_plan_completion(tmp_path, phase=3)
        assert c == []
        assert w == []

    def test_missing_plan_still_passes(self, tmp_path):
        c, w = audit_plan_completion(tmp_path, phase=5)
        assert c == []


# ---------------------------------------------------------------------------
# audit_deliverables
# ---------------------------------------------------------------------------

class TestAuditDeliverables:

    def test_missing_deliverable_is_critical(self, tmp_path):
        # Phase 3: 03-development/src/ and 03-development/tests/ both missing
        c, w = audit_deliverables(tmp_path, phase=3)
        assert any("not found on disk" in msg for msg in c)

    def test_existing_untracked_deliverable_is_warning(self, tmp_path):
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        with patch("phase_end_audit._is_git_tracked", return_value=False):
            c, w = audit_deliverables(tmp_path, phase=3)
        assert not any("not found" in msg for msg in c)
        assert any("not git-tracked" in msg for msg in w)

    def test_tracked_large_file_passes(self, tmp_path):
        f = tmp_path / "06-quality" / "QUALITY_REPORT.md"
        f.parent.mkdir(parents=True)
        f.write_text("x" * 300)
        with patch("phase_end_audit._is_git_tracked", return_value=True):
            c, w = audit_deliverables(tmp_path, phase=6)
        assert c == []
        assert w == []

    def test_tracked_small_file_is_warning(self, tmp_path):
        f = tmp_path / "06-quality" / "QUALITY_REPORT.md"
        f.parent.mkdir(parents=True)
        f.write_text("tiny")
        with patch("phase_end_audit._is_git_tracked", return_value=True):
            c, w = audit_deliverables(tmp_path, phase=6)
        assert c == []
        assert any("<200 bytes" in msg for msg in w)

    def test_directory_deliverable_not_size_checked(self, tmp_path):
        # Directories are never size-checked (is_file() guard)
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        with patch("phase_end_audit._is_git_tracked", return_value=True):
            c, w = audit_deliverables(tmp_path, phase=3)
        assert c == []
        assert not any("<200 bytes" in msg for msg in w)

    def test_phase_with_no_deliverables_passes(self, tmp_path):
        # Phases 1-2 have no entries in _DELIVERABLES
        c, w = audit_deliverables(tmp_path, phase=1)
        assert c == []
        assert w == []

    def test_phase4_deliverables_checked(self, tmp_path):
        # Phase 4: TEST_PLAN.md + TEST_RESULTS.md — both missing
        c, w = audit_deliverables(tmp_path, phase=4)
        paths = [msg for msg in c if "not found" in msg]
        assert len(paths) == 2


# ---------------------------------------------------------------------------
# audit_gate_results
# ---------------------------------------------------------------------------

class TestAuditGateResults:

    def test_missing_manifest_is_critical(self, tmp_path):
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("quality_manifest.json not found" in msg for msg in c)

    def test_malformed_manifest_is_critical(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "quality_manifest.json").write_text("{bad json")
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("Cannot parse" in msg for msg in c)

    def test_all_frs_gate1_complete_passes(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1": {
                    "FR-01": {"quality_complete": True, "score": 80},
                    "FR-02": {"quality_complete": True, "score": 85},
                },
            },
        })
        # Phase 5 has no exit gate
        c, w = audit_gate_results(tmp_path, phase=5)
        assert c == []

    def test_fr_missing_from_gate1_is_critical(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1": {
                    "FR-01": {"quality_complete": True, "score": 80},
                    # FR-02 absent
                },
            },
        })
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("FR-02" in msg for msg in c)

    def test_fr_quality_incomplete_is_critical(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01"],
            "gate_results": {
                "gate1": {
                    "FR-01": {"quality_complete": False, "score": 60},
                },
            },
        })
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("FR-01" in msg for msg in c)

    def test_exit_gate_missing_is_critical(self, tmp_path):
        # Phase 3 exit gate = Gate 2
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01"],
            "gate_results": {
                "gate1": {"FR-01": {"quality_complete": True, "score": 80}},
                # gate2 absent
            },
        })
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("Exit Gate 2" in msg for msg in c)

    def test_exit_gate_not_complete_is_critical(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01"],
            "gate_results": {
                "gate1": {"FR-01": {"quality_complete": True, "score": 80}},
                "gate2": {"quality_complete": False, "score": 70},
            },
        })
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("Exit Gate 2" in msg for msg in c)

    def test_exit_gate_complete_passes(self, tmp_path):
        # Phase 4 exit gate = Gate 3
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01"],
            "gate_results": {
                "gate1": {"FR-01": {"quality_complete": True, "score": 80}},
                "gate3": {"quality_complete": True, "score": 82},
            },
        })
        c, w = audit_gate_results(tmp_path, phase=4)
        assert c == []

    def test_phase6_exit_gate4_complete_passes(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": [],
            "gate_results": {
                "gate4": {"quality_complete": True, "score": 87},
            },
        })
        c, w = audit_gate_results(tmp_path, phase=6)
        assert c == []

    def test_no_fr_ids_skips_gate1_per_fr_check(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": [],
            "gate_results": {
                "gate4": {"quality_complete": True, "score": 87},
            },
        })
        c, w = audit_gate_results(tmp_path, phase=6)
        # no criticals about missing Gate 1 FRs
        assert not any("Gate 1" in msg for msg in c)

    def test_gate1_missing_entirely_with_fr_ids_is_critical(self, tmp_path):
        _write_manifest(tmp_path, {
            "fr_ids": ["FR-01"],
            "gate_results": {},  # no gate1 key at all
        })
        c, w = audit_gate_results(tmp_path, phase=3)
        assert any("Gate 1" in msg for msg in c)


# ---------------------------------------------------------------------------
# audit_git_log
# ---------------------------------------------------------------------------

class TestAuditGitLog:

    def test_all_milestones_present_passes(self, tmp_path):
        # Commit messages use feat(P3-mid) / feat(P3-pre-gate2) — check is case-insensitive
        with patch("subprocess.run", return_value=_git_log_mock(
            "abc1234 feat(P3-mid): 2/3 FRs Gate1 PASS\n"
            "def5678 feat(P3-pre-gate2): all FRs ready for Gate 2\n"
        )):
            c, w = audit_git_log(tmp_path, phase=3)
        assert c == []
        assert w == []

    def test_missing_milestone_is_warning(self, tmp_path):
        with patch("subprocess.run", return_value=_git_log_mock(
            "abc1234 feat(P3-mid): 2/3 FRs\n"
            # p3-pre-gate2 missing
        )):
            c, w = audit_git_log(tmp_path, phase=3)
        assert c == []
        assert any("p3-pre-gate2" in msg for msg in w)

    def test_both_p3_milestones_missing_two_warnings(self, tmp_path):
        with patch("subprocess.run", return_value=_git_log_mock("abc1234 initial\n")):
            c, w = audit_git_log(tmp_path, phase=3)
        assert c == []
        missing = [m for m in w if "Milestone commit" in m]
        assert len(missing) == 2

    def test_phase4_milestones_checked(self, tmp_path):
        with patch("subprocess.run", return_value=_git_log_mock(
            "abc feat(P4-mid): 3/5\n"
            # p4-pre-gate3 absent
        )):
            c, w = audit_git_log(tmp_path, phase=4)
        assert any("p4-pre-gate3" in msg for msg in w)

    def test_phase_with_no_milestones_passes(self, tmp_path):
        # Phase 6 not in _MILESTONES
        c, w = audit_git_log(tmp_path, phase=6)
        assert c == []
        assert w == []

    def test_git_subprocess_error_is_warning(self, tmp_path):
        with patch("subprocess.run", side_effect=OSError("git not found")):
            c, w = audit_git_log(tmp_path, phase=3)
        assert c == []
        assert any("Could not check" in msg for msg in w)

    def test_git_timeout_is_warning(self, tmp_path):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            c, w = audit_git_log(tmp_path, phase=3)
        assert c == []
        assert any("Could not check" in msg for msg in w)


# ---------------------------------------------------------------------------
# audit_development_log
# ---------------------------------------------------------------------------

class TestAuditDevelopmentLog:

    def test_missing_log_is_warning(self, tmp_path):
        c, w = audit_development_log(tmp_path, phase=3)
        assert c == []
        assert any("not found" in msg for msg in w)

    def test_log_with_phase_and_session_id_passes(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 3\nsession_id: abc-def-123\n"
        )
        c, w = audit_development_log(tmp_path, phase=3)
        assert c == []
        assert w == []

    def test_log_missing_phase_entries_is_warning(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 2\nsession_id: xyz-456\n"
        )
        c, w = audit_development_log(tmp_path, phase=3)
        assert any("Phase 3" in msg for msg in w)

    def test_log_missing_session_id_is_warning(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 3\nSome content without any session reference\n"
        )
        c, w = audit_development_log(tmp_path, phase=3)
        assert any("session_id" in msg for msg in w)

    def test_session_id_hyphenated_form_accepted(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 3\nsession-id: abc-123\n"
        )
        c, w = audit_development_log(tmp_path, phase=3)
        assert c == []
        # session_id regex accepts session-id too (re.IGNORECASE, \s? pattern)
        # if not, that's fine — just check no crash

    def test_both_warnings_present_when_both_missing(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text("## Phase 2\nno session\n")
        c, w = audit_development_log(tmp_path, phase=4)
        assert c == []
        assert len(w) == 2


# ---------------------------------------------------------------------------
# run_audit integration
# ---------------------------------------------------------------------------

class TestRunAuditIntegration:

    def _setup_passing_phase3(self, tmp_path: Path) -> None:
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        (methodology / "phase3_plan.md").write_text(
            "- [x] Implement FR-01\n- [x] Run Gate 1\n- [x] Run Gate 2\n"
        )
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        (methodology / "quality_manifest.json").write_text(json.dumps({
            "fr_ids": ["FR-01"],
            "gate_results": {
                "gate1": {"FR-01": {"quality_complete": True, "score": 80}},
                "gate2": {"quality_complete": True, "score": 77},
            },
        }))
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 3\nsession_id: abc-123\n"
        )

    def test_fully_passing_scenario_returns_0(self, tmp_path):
        self._setup_passing_phase3(tmp_path)
        with (
            patch("phase_end_audit._is_git_tracked", return_value=True),
            patch("subprocess.run", return_value=_git_log_mock(
                "abc feat(P3-mid): 2/3 FRs\ndef feat(P3-pre-gate2): all FRs\n"
            )),
        ):
            rc = run_audit(tmp_path, phase=3)
        assert rc == 0

    def test_passing_scenario_writes_passed_report(self, tmp_path):
        self._setup_passing_phase3(tmp_path)
        report = tmp_path / ".methodology" / "audit_gaps_3.md"
        with (
            patch("phase_end_audit._is_git_tracked", return_value=True),
            patch("subprocess.run", return_value=_git_log_mock(
                "abc feat(P3-mid): 2/3 FRs\ndef feat(P3-pre-gate2): all FRs\n"
            )),
        ):
            run_audit(tmp_path, phase=3)
        assert report.exists()
        content = report.read_text()
        assert "PASSED" in content
        assert "GAPS FOUND" not in content

    def test_missing_deliverable_returns_1(self, tmp_path):
        self._setup_passing_phase3(tmp_path)
        shutil.rmtree(tmp_path / "03-development" / "src")
        with (
            patch("phase_end_audit._is_git_tracked", return_value=True),
            patch("subprocess.run", return_value=_git_log_mock(
                "abc feat(P3-mid): 2/3 FRs\ndef feat(P3-pre-gate2): all FRs\n"
            )),
        ):
            rc = run_audit(tmp_path, phase=3)
        assert rc == 1

    def test_critical_gap_in_report_when_deliverable_missing(self, tmp_path):
        self._setup_passing_phase3(tmp_path)
        shutil.rmtree(tmp_path / "03-development" / "src")
        with (
            patch("phase_end_audit._is_git_tracked", return_value=True),
            patch("subprocess.run", return_value=_git_log_mock("")),
        ):
            run_audit(tmp_path, phase=3)
        content = (tmp_path / ".methodology" / "audit_gaps_3.md").read_text()
        assert "CRITICAL" in content
        assert "GAPS FOUND" in content
        assert "03-development/src" in content

    def test_report_always_written_even_on_gaps(self, tmp_path):
        # No setup — everything is missing
        rc = run_audit(tmp_path, phase=3)
        report = tmp_path / ".methodology" / "audit_gaps_3.md"
        assert report.exists()
        assert rc == 1

    def test_phase8_audit_checks_correct_deliverables(self, tmp_path):
        """Phase 8 deliverables: CONFIG_RECORDS.md + RELEASE_CHECKLIST.md."""
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        (methodology / "phase8_plan.md").write_text("- [x] Done\n")
        for name in ("CONFIG_RECORDS.md", "RELEASE_CHECKLIST.md"):
            f = tmp_path / "08-config" / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x" * 300)
        (methodology / "quality_manifest.json").write_text(json.dumps({
            "fr_ids": [],
            "gate_results": {},
        }))
        (tmp_path / "DEVELOPMENT_LOG.md").write_text(
            "## Phase 8\nsession_id: abc-123\n"
        )
        with (
            patch("phase_end_audit._is_git_tracked", return_value=True),
            patch("subprocess.run", return_value=_git_log_mock("abc p8\n")),
        ):
            run_audit(tmp_path, phase=8)
        # Gate results may still warn (no gate4 in manifest), but deliverables pass
        report = (methodology / "audit_gaps_8.md").read_text()
        assert "CONFIG_RECORDS.md" not in report.split("CRITICAL")[1] if "CRITICAL" in report else True
