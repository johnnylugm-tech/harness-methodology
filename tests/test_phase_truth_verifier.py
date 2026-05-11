"""
tests/test_phase_truth_verifier.py — Unit tests for PhaseTruthVerifier (crg-004).

Tests cover: check_session_log, get_manual_checklist, check_pytest, check_coverage.
check_framework_block is excluded (requires full FrameworkEnforcer env).
"""

import json
import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.gate

from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier


# ---------------------------------------------------------------------------
# check_session_log
# ---------------------------------------------------------------------------

class TestCheckSessionLog:
    def test_log_not_found(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, details = v.check_session_log()
        assert passed is False
        assert score == 0.0
        assert "not found" in details

    def test_single_role_fails_ab(self, tmp_path):
        (tmp_path / "sessions_spawn.log").write_text(
            json.dumps({"sessions": [{"role": "developer", "session_id": "s1"}]})
        )
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False

    def test_ab_roles_json_object(self, tmp_path):
        (tmp_path / "sessions_spawn.log").write_text(json.dumps({"sessions": [
            {"role": "developer", "session_id": "s1"},
            {"role": "reviewer",  "session_id": "s2"},
        ]}))
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is True
        assert score == 100.0

    def test_ab_roles_json_list(self, tmp_path):
        (tmp_path / "sessions_spawn.log").write_text(json.dumps([
            {"role": "developer", "session_id": "s1"},
            {"role": "reviewer",  "session_id": "s2"},
        ]))
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is True

    def test_ab_roles_linewise(self, tmp_path):
        lines = (
            json.dumps({"role": "developer", "session_id": "s1"}) + "\n" +
            json.dumps({"role": "reviewer",  "session_id": "s2"})
        )
        (tmp_path / "sessions_spawn.log").write_text(lines)
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is True

    def test_single_json_entry(self, tmp_path):
        (tmp_path / "sessions_spawn.log").write_text(
            json.dumps({"role": "developer", "session_id": "s1"})
        )
        passed, _, _ = PhaseTruthVerifier(str(tmp_path), 1).check_session_log()
        assert passed is False  # only 1 role


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
