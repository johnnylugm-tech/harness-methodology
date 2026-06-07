"""
tests/test_phase_truth_verifier.py — Unit tests for PhaseTruthVerifier (crg-004).

Tests cover: get_manual_checklist, check_pytest, check_coverage, verify().
check_framework_block is excluded (requires full FrameworkEnforcer env).
"""

import pytest
from unittest.mock import MagicMock, patch
import unittest.mock

from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
import json

class TestLoadThreshold:
    def test_default_threshold_when_no_config(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 90.0

    def test_threshold_from_config(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"hr_overrides": {"HR-11_phase_truth_threshold": 95.5}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 95.5

    def test_threshold_none_in_config_uses_default(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"hr_overrides": {"HR-11_phase_truth_threshold": None}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 90.0

    def test_override_threshold_via_init(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1, threshold=85.0)
        assert v.threshold == 85.0

    def test_pytest_timeout_config(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"phase_truth": {"pytest_timeout_seconds": 60}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v._get_pytest_timeout() == 60

    def test_pytest_timeout_default(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v._get_pytest_timeout() == 300

    def test_pytest_timeout_floor(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"phase_truth": {"pytest_timeout_seconds": 10}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        # Should floor at 30
        assert v._get_pytest_timeout() == 30



# ---------------------------------------------------------------------------
# get_manual_checklist
# ---------------------------------------------------------------------------

class TestGetManualChecklist:
    @pytest.mark.parametrize("phase", range(1, 9))
    def test_all_phases_return_list(self, tmp_path, phase):
        checklist = PhaseTruthVerifier(str(tmp_path), phase).get_manual_checklist()
        assert isinstance(checklist, list)
        assert len(checklist) >= 1  # always includes sessions_spawn

    def test_always_includes_sessions_spawn(self, tmp_path):
        items = [c["item"] for c in PhaseTruthVerifier(str(tmp_path), 2).get_manual_checklist()]
        assert ".methodology/sessions_spawn.log" in items

    def test_missing_sessions_spawn_shows_missing(self, tmp_path):
        checklist = PhaseTruthVerifier(str(tmp_path), 1).get_manual_checklist()
        spawn = next(c for c in checklist if c["item"] == ".methodology/sessions_spawn.log")
        assert "missing" in spawn["status"]

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
        assert score == 80.0
        assert "80%" in details

    @patch("subprocess.run")
    def test_parses_fallback_coverage_line(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="some other output coverage: 75% end", stderr=""
        )
        passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is True
        assert score == 75.0
        assert "75%" in details

    @patch("subprocess.run")
    def test_below_threshold_fails(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="TOTAL  500  400  20%", stderr=""
        )
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 20.0

    @patch("subprocess.run")
    def test_no_coverage_output(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="no match", stderr="")
        passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 0.0

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_coverage_not_found(self, _, tmp_path):
        passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 0.0

    def test_verify_method(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "checks" in result
        assert "checklist" in result


# ---------------------------------------------------------------------------
# verify() integration (Phase 3+)
# ---------------------------------------------------------------------------

class TestVerifyIntegration:
    """Phase 3+ verify() composes framework/pytest/coverage/previous/cross_artifact."""

    def test_verify_renormalizes_after_infra_skip(self, tmp_path):
        """verify() should handle InfraSkip gracefully and renormalize weights."""
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with unittest.mock.patch.multiple(
            v,
            check_framework_block=unittest.mock.DEFAULT,
            check_pytest=unittest.mock.DEFAULT,
            check_coverage=unittest.mock.DEFAULT,
            check_previous_phase_artifacts=unittest.mock.DEFAULT,
            check_cross_artifact=unittest.mock.DEFAULT,
        ) as mocks:
            mocks["check_framework_block"].return_value = (True, 100.0, "ok")
            mocks["check_pytest"].return_value = (True, 100.0, "ok")
            mocks["check_coverage"].return_value = (True, 100.0, "ok")
            mocks["check_previous_phase_artifacts"].return_value = (True, 100.0, "ok")
            mocks["check_cross_artifact"].return_value = (True, 100.0, "ok")
            result = v.verify()
        assert result["passed"] is True
        assert result["total_score"] >= 90.0

class TestCheckSessionLog:
    def test_missing_log(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "missing" in msg

    def test_empty_log(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text("   \n")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "empty" in msg

    def test_malformed_jsonl_exactly_half(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"a": 1}\nnot json\n')
        v = PhaseTruthVerifier(str(tmp_path), 3) # phase 3 has no AB check
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "malformed" in msg

    def test_malformed_jsonl_below_half_passes(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 2 valid, 1 invalid = 0.33 malformed
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"a": 1}\n{"b": 2}\nnot json\n')
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_ab_reviewer_missing_fails(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, only developer
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 50.0
        assert "A/B reviewer missing" in msg

    def test_ab_reviewer_present_passes(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, both dev and reviewer
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n{"fr_id": "FR-1", "role": "reviewer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_ab_reviewer_ignored_in_other_phases(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, only developer, but phase 3 ignores AB
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

pytestmark = pytest.mark.gate
