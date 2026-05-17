"""
tests/test_phase_truth_verifier.py — Unit tests for PhaseTruthVerifier (crg-004).

Tests cover: check_session_log, get_manual_checklist, check_pytest, check_coverage.
check_framework_block is excluded (requires full FrameworkEnforcer env).
"""

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.gate

from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier


# ---------------------------------------------------------------------------
# check_session_log
# ---------------------------------------------------------------------------

class TestCheckSessionLog:
    """Tests for check_session_log.

    Updated for CV-1 (canonical path is .methodology/sessions_spawn.log) and
    SG-14 (only JSONL is accepted — one JSON object per line, matching
    SessionsSpawnLogger._write_entries). Single-dict and JSON-array formats
    are no longer accepted because they were never produced by the canonical
    writer.
    """

    @staticmethod
    def _log_path(project: Path) -> Path:
        p = project / ".methodology" / "sessions_spawn.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_log_not_found(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, details = v.check_session_log()
        assert passed is False
        assert score == 0.0
        assert "not found" in details

    def test_single_role_fails_ab(self, tmp_path):
        # JSONL: single entry with one role → fails A/B check.
        self._log_path(tmp_path).write_text(
            json.dumps({"role": "developer", "session_id": "s1"}) + "\n"
        )
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False

    def test_ab_roles_linewise(self, tmp_path):
        """JSONL with two distinct roles + sessions → passes."""
        lines = (
            json.dumps({"role": "developer", "session_id": "s1"}) + "\n" +
            json.dumps({"role": "reviewer",  "session_id": "s2"}) + "\n"
        )
        self._log_path(tmp_path).write_text(lines)
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is True
        assert score == 100.0

    def test_single_json_entry_one_role(self, tmp_path):
        """JSONL single entry → only 1 role → fails."""
        self._log_path(tmp_path).write_text(
            json.dumps({"role": "developer", "session_id": "s1"}) + "\n"
        )
        passed, _, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False

    def test_legacy_dict_format_rejected(self, tmp_path):
        """SG-14: single-dict format is no longer accepted (JSONL only).

        The outer dict has no "role" or "session_id" keys, so neither the
        A/B check nor the sessions check can pass → score must be 0 and
        passed must be False.
        """
        self._log_path(tmp_path).write_text(
            json.dumps({"sessions": [
                {"role": "developer", "session_id": "s1"},
                {"role": "reviewer",  "session_id": "s2"},
            ]})
        )
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False
        assert score == 0.0

    def test_legacy_array_format_rejected(self, tmp_path):
        """SG-14: JSON array on a single line is not the JSONL format we expect.

        The array parses as a list → isinstance(entry, dict) is False →
        counted as malformed → score 0.
        """
        self._log_path(tmp_path).write_text(json.dumps([
            {"role": "developer", "session_id": "s1"},
            {"role": "reviewer",  "session_id": "s2"},
        ]))
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False
        assert score == 0.0


# ---------------------------------------------------------------------------
# get_manual_checklist
# ---------------------------------------------------------------------------

class TestGetManualChecklist:
    @pytest.mark.parametrize("phase", range(1, 9))
    def test_all_phases_return_list(self, tmp_path, phase):
        checklist = PhaseTruthVerifier(str(tmp_path), phase).get_manual_checklist()
        assert isinstance(checklist, list)
        assert len(checklist) >= 2  # always includes DEVELOPMENT_LOG + sessions_spawn

    def test_always_includes_dev_log(self, tmp_path):
        items = [c["item"] for c in PhaseTruthVerifier(str(tmp_path), 1).get_manual_checklist()]
        assert "DEVELOPMENT_LOG.md" in items

    def test_always_includes_sessions_spawn(self, tmp_path):
        items = [c["item"] for c in PhaseTruthVerifier(str(tmp_path), 2).get_manual_checklist()]
        assert "sessions_spawn.log" in items

    def test_missing_files_show_missing_status(self, tmp_path):
        checklist = PhaseTruthVerifier(str(tmp_path), 1).get_manual_checklist()
        dev_log = next(c for c in checklist if c["item"] == "DEVELOPMENT_LOG.md")
        assert "missing" in dev_log["status"]

    def test_present_files_show_present_status(self, tmp_path):
        (tmp_path / "DEVELOPMENT_LOG.md").write_text("# Log")
        checklist = PhaseTruthVerifier(str(tmp_path), 1).get_manual_checklist()
        dev_log = next(c for c in checklist if c["item"] == "DEVELOPMENT_LOG.md")
        assert "present" in dev_log["status"]

    def test_each_item_has_required_keys(self, tmp_path):
        for item in PhaseTruthVerifier(str(tmp_path), 3).get_manual_checklist():
            assert "item" in item
            assert "status" in item
            assert "action" in item


# ---------------------------------------------------------------------------
# check_pytest (subprocess mocked)
# ---------------------------------------------------------------------------

class TestCheckPytest:
    @patch("subprocess.run")
    def test_passes_when_returncode_0(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is True
        assert score == 100.0

    @patch("subprocess.run")
    def test_fails_when_returncode_nonzero(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED 2", stderr="")
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is False
        assert score == 0.0

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_pytest_not_found(self, _, tmp_path):
        passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is False
        assert "not found" in details


# ---------------------------------------------------------------------------
# check_coverage (subprocess mocked)
# ---------------------------------------------------------------------------

class TestCheckCoverage:
    @patch("subprocess.run")
    def test_parses_total_line(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="TOTAL  500  100  80%", stderr=""
        )
        passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is True
        assert "80%" in details

    @patch("subprocess.run")
    def test_below_threshold_fails(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="TOTAL  500  400  20%", stderr=""
        )
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False

    @patch("subprocess.run")
    def test_no_coverage_output(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="no match", stderr="")
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 0

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_coverage_not_found(self, _, tmp_path):
        passed, _, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False

    def test_verify_method(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with patch.object(v, "check_session_log", return_value=(True, 100.0, "ok")):
            with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")):
                result = v.verify()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "checks" in result
        assert "checklist" in result
