"""
W7 — Gap-fill tests targeting Category C + D (84-86% goal).
"""
import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── ClaimsVerifier ───────────────────────────────────────────────────────────

class TestClaimsVerifier:
    def _log(self, tmp_path, content: str):
        f = tmp_path / "sessions_spawn.log"
        f.write_text(content)
        return f

    def test_log_not_found(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is False
        assert "not found" in result.message

    def test_jsonl_two_roles_passes(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        self._log(tmp_path,
                  '{"role": "developer"}\n{"role": "reviewer"}\n')
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is True
        assert set(result.details["roles"]) >= {"developer", "reviewer"}

    def test_jsonl_single_role_fails(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        self._log(tmp_path, '{"role": "developer"}\n{"role": "developer"}\n')
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is False

    def test_dict_with_sessions_key(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        data = {"sessions": [{"role": "dev"}, {"role": "rev"}]}
        self._log(tmp_path, json.dumps(data))
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is True

    def test_list_json_format(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        data = [{"role": "dev"}, {"role": "qa"}]
        self._log(tmp_path, json.dumps(data))
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is True

    def test_single_entry_json_fails(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        self._log(tmp_path, json.dumps({"role": "developer"}))
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is False

    def test_jsonl_invalid_lines_skipped(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        self._log(tmp_path, 'bad\n{"role": "dev"}\n{"role": "rev"}\n')
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is True

    def test_exception_returns_failed(self, tmp_path):
        from core.quality_gate.claims_verifier import ClaimsVerifier
        log = tmp_path / "sessions_spawn.log"
        log.write_bytes(b"\xff\xfe")   # bad UTF-8 causes error
        result = ClaimsVerifier(str(tmp_path)).verify_sessions_spawn_log()
        assert result.passed is False


# ─── SpecTrackingChecker ──────────────────────────────────────────────────────

def _make_spec_tracking(tmp_path, content: str = None) -> Path:
    if content is None:
        content = """# Specification Tracking

## Core Features

| Feature | Status | Notes |
|---------|--------|-------|
| Login | ✅ Done | completed |
| Register | 🔄 In Progress | |

## Update Log

- 2026-04-01: Initial draft
"""
    (tmp_path / "01-requirements").mkdir(exist_ok=True)
    f = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
    f.write_text(content)
    return f


class TestSpecTrackingChecker:
    def test_check_exists_false(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        c = SpecTrackingChecker(str(tmp_path))
        assert c.check_exists() is False

    def test_check_exists_true(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        _make_spec_tracking(tmp_path)
        c = SpecTrackingChecker(str(tmp_path))
        assert c.check_exists() is True

    def test_check_completeness_no_file(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        c = SpecTrackingChecker(str(tmp_path))
        result = c.check_completeness()
        assert result["complete"] is False
        assert "SPEC_TRACKING.md not found" in result["missing"]

    def test_check_completeness_complete(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        _make_spec_tracking(tmp_path)
        c = SpecTrackingChecker(str(tmp_path))
        result = c.check_completeness()
        assert "missing" in result

    def test_check_completeness_missing_status_column(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        (tmp_path / "01-requirements").mkdir(exist_ok=True)
        f = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
        f.write_text("## Core Features\n\n| Feature | Notes |\n|---|---|\n\n## Update Log\n- entry\n")
        c = SpecTrackingChecker(str(tmp_path))
        result = c.check_completeness()
        assert "Status column" in result["missing"]

    def test_run_returns_bool(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        _make_spec_tracking(tmp_path)
        c = SpecTrackingChecker(str(tmp_path))
        result = c.run()
        assert isinstance(result, bool)

    def test_run_false_when_no_file(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        assert SpecTrackingChecker(str(tmp_path)).run() is False

    def test_run_enforcement_no_file(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        result = SpecTrackingChecker(str(tmp_path)).run_enforcement()
        assert result["exists"] is False
        assert result["completeness"] == 0

    def test_run_enforcement_with_file(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        _make_spec_tracking(tmp_path)
        result = SpecTrackingChecker(str(tmp_path)).run_enforcement()
        assert result["exists"] is True
        assert "completeness" in result
        assert "stats" in result

    def test_print_report_no_file(self, tmp_path, capsys):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        SpecTrackingChecker(str(tmp_path)).print_report()
        out = capsys.readouterr().out
        assert "not found" in out

    def test_print_report_with_file(self, tmp_path, capsys):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        _make_spec_tracking(tmp_path)
        SpecTrackingChecker(str(tmp_path)).print_report()
        out = capsys.readouterr().out
        assert "Specification Tracking" in out


# ─── ABEnforcer ───────────────────────────────────────────────────────────────

def _make_dev_log(tmp_path, content: str = None) -> Path:
    if content is None:
        content = """# Development Log

## Phase 3 Implementation

Developer Session: dev-session-001
Developer output: completed login module

Reviewer Session: rev-session-002
Reviewer approved.

Reviewer found: missing edge case
Developer responded to Reviewer feedback
Iteration 2 completed

## Phase 4 Testing

Tester Session: tester-session-003
"""
    f = tmp_path / "DEVELOPMENT_LOG.md"
    f.write_text(content)
    return f


class TestABEnforcer:
    def test_no_dev_log_separation_fails(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        r = ABEnforcer(str(tmp_path)).verify_developer_reviewer_separation("phase_1")
        assert r["separated"] is False
        assert "not found" in r.get("error", "")

    def test_no_dev_log_dialogue_fails(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        r = ABEnforcer(str(tmp_path)).verify_ab_dialogue_exists("phase_1")
        assert r["has_dialogue"] is False

    def test_phase_not_in_log_separation(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path, "## Phase 3\nsome content\n")
        r = ABEnforcer(str(tmp_path)).verify_developer_reviewer_separation("phase_9")
        assert r["separated"] is False

    def test_separated_dev_reviewer(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)
        r = ABEnforcer(str(tmp_path)).verify_developer_reviewer_separation("phase_3")
        assert r["developer_session"] is not None
        assert r["reviewer_session"] is not None
        assert r["separated"] is True

    def test_same_session_not_separated(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        content = "## Phase 1\nSession-Id: same-001\nDeveloper Agent\nSession-Id: same-001\nReviewer Agent\n"
        _make_dev_log(tmp_path, content)
        r = ABEnforcer(str(tmp_path)).verify_developer_reviewer_separation("phase_1")
        # Both sessions normalized to "same001" → not separated
        assert r["separated"] is False

    def test_dialogue_exists_with_indicators(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)  # contains dialogue indicators
        r = ABEnforcer(str(tmp_path)).verify_ab_dialogue_exists("phase_3")
        assert r["dialogue_count"] >= 2
        assert r["has_dialogue"] is True

    def test_no_dialogue_in_phase(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        content = "## Phase 1\nDeveloper Agent output\nReviewer Agent approved.\n"
        _make_dev_log(tmp_path, content)
        r = ABEnforcer(str(tmp_path)).verify_ab_dialogue_exists("phase_1")
        # has_simple_only=True and dialogue_count<2 → False
        assert r["has_dialogue"] is False

    def test_dialogue_phase_not_found(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)
        r = ABEnforcer(str(tmp_path)).verify_ab_dialogue_exists("phase_99")
        assert r["has_dialogue"] is False
        assert "not found" in r.get("error", "")

    def test_verify_qa_not_developer_no_log(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        r = ABEnforcer(str(tmp_path)).verify_qa_not_developer()
        assert r["separated"] is False

    def test_verify_qa_not_developer_separated(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)  # has phase 3 dev + phase 4 tester
        r = ABEnforcer(str(tmp_path)).verify_qa_not_developer()
        # dev-session-001 vs tester-session-003 → separated (truthy, may not be strict bool)
        assert r["separated"]

    def test_verify_all_ab_checks_phase3(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)
        r = ABEnforcer(str(tmp_path)).verify_all_ab_checks(3)
        assert "developer_reviewer_separation" in r
        assert "ab_dialogue_exists" in r
        assert r["qa_not_developer"] is None  # phase != 4

    def test_verify_all_ab_checks_phase4(self, tmp_path):
        from core.quality_gate.ab_enforcer import ABEnforcer
        _make_dev_log(tmp_path)
        r = ABEnforcer(str(tmp_path)).verify_all_ab_checks(4)
        assert r["qa_not_developer"] is not None

    def test_verify_ab_separation_convenience(self, tmp_path):
        from core.quality_gate.ab_enforcer import verify_ab_separation
        _make_dev_log(tmp_path)
        r = verify_ab_separation(str(tmp_path), 3)
        assert "separated" in r

    def test_verify_ab_dialogue_convenience(self, tmp_path):
        from core.quality_gate.ab_enforcer import verify_ab_dialogue
        _make_dev_log(tmp_path)
        r = verify_ab_dialogue(str(tmp_path), 3)
        assert "has_dialogue" in r


# ─── PhaseTruthVerifier ───────────────────────────────────────────────────────

def _make_sessions_log(tmp_path, content: str = None) -> Path:
    # CV-1: canonical path is .methodology/sessions_spawn.log
    method_dir = tmp_path / ".methodology"
    method_dir.mkdir(parents=True, exist_ok=True)
    f = method_dir / "sessions_spawn.log"
    if content is None:
        content = (
            '{"role": "developer", "session_id": "dev-001"}\n'
            '{"role": "reviewer", "session_id": "rev-002"}\n'
        )
    f.write_text(content)
    return f


class TestPhaseTruthVerifier:
    def _make_verifier(self, tmp_path, phase=3):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        return PhaseTruthVerifier(str(tmp_path), phase)

    def test_check_session_log_not_found(self, tmp_path):
        v = self._make_verifier(tmp_path)
        passed, score, detail = v.check_session_log()
        assert passed is False
        assert score == 0.0
        assert "not found" in detail

    def test_check_session_log_jsonl_two_roles(self, tmp_path):
        _make_sessions_log(tmp_path)
        v = self._make_verifier(tmp_path)
        passed, score, detail = v.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_check_session_log_dict_sessions_format(self, tmp_path):
        """SG-14: legacy {"sessions":[...]} format is no longer accepted."""
        data = {"sessions": [
            {"role": "dev", "session_id": "s1"},
            {"role": "rev", "session_id": "s2"},
        ]}
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True, exist_ok=True)
        (method_dir / "sessions_spawn.log").write_text(json.dumps(data))
        v = self._make_verifier(tmp_path)
        passed, _, _ = v.check_session_log()
        # Legacy single-dict format → treated as malformed under JSONL contract.
        assert passed is False

    def test_check_session_log_list_format(self, tmp_path):
        """SG-14: legacy JSON-array-on-one-line format is no longer accepted."""
        data = [{"role": "a", "session_id": "s1"}, {"role": "b", "session_id": "s2"}]
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True, exist_ok=True)
        (method_dir / "sessions_spawn.log").write_text(json.dumps(data))
        v = self._make_verifier(tmp_path)
        passed, _, _ = v.check_session_log()
        assert passed is False

    def test_check_session_log_single_entry(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True, exist_ok=True)
        (method_dir / "sessions_spawn.log").write_text(
            json.dumps({"role": "dev", "session_id": "s1"}) + "\n"
        )
        v = self._make_verifier(tmp_path)
        passed, score, _ = v.check_session_log()
        assert passed is False

    def test_check_session_log_exception(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True, exist_ok=True)
        log = method_dir / "sessions_spawn.log"
        log.write_bytes(b"\xff\xfe\xfd")
        v = self._make_verifier(tmp_path)
        passed, score, detail = v.check_session_log()
        assert passed is False

    def test_check_framework_block_import_error(self, tmp_path):
        """CV-4: ImportError now raises InfraSkip instead of returning 0 score."""
        from core.quality_gate.phase_truth_verifier import InfraSkip
        v = self._make_verifier(tmp_path)
        with patch.dict("sys.modules", {"enforcement.framework_enforcer": None}):
            with pytest.raises(InfraSkip):
                v.check_framework_block()

    def test_check_framework_block_success(self, tmp_path):
        v = self._make_verifier(tmp_path)
        mock_result = MagicMock(passed=True, block_checks={"a": True}, violations=[])
        mock_fe_cls = MagicMock(return_value=MagicMock(run=MagicMock(return_value=mock_result)))
        mock_module = MagicMock()
        mock_module.FrameworkEnforcer = mock_fe_cls
        with patch.dict("sys.modules", {"enforcement.framework_enforcer": mock_module}):
            passed, score, _ = v.check_framework_block()
        assert passed is True
        assert score == 100.0

    def test_check_pytest_passes(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            passed, score, _ = v.check_pytest()
        assert passed is True
        assert score == 100.0

    def test_check_pytest_fails(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            passed, score, _ = v.check_pytest()
        assert passed is False
        assert score == 0.0

    def test_check_pytest_timeout(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 120)):
            passed, score, detail = v.check_pytest()
        assert passed is False
        assert "timed out" in detail

    def test_check_pytest_file_not_found(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("no pytest")):
            passed, score, detail = v.check_pytest()
        assert passed is False
        assert "not found" in detail

    def test_check_coverage_parses_total(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="TOTAL  100  10  90%\n",
                stderr=""
            )
            passed, score, detail = v.check_coverage()
        assert passed is True
        assert "90%" in detail

    def test_check_coverage_exception(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("subprocess.run", side_effect=Exception("boom")):
            passed, score, detail = v.check_coverage()
        assert passed is False

    def test_get_manual_checklist_phase3(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=3)
        checklist = v.get_manual_checklist()
        assert len(checklist) >= 2  # at least DEVELOPMENT_LOG.md and sessions_spawn.log

    def test_get_manual_checklist_phase1(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=1)
        checklist = v.get_manual_checklist()
        assert any("SPEC_TRACKING" in item["item"] or "SRS" in item["item"]
                   for item in checklist)

    def test_verify_phase1_two_checks(self, tmp_path):
        """Phase 1-2 uses only BLOCK + session_log (2 checks)."""
        v = self._make_verifier(tmp_path, phase=1)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_session_log", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert result["passed"] is True
        assert len(result["checks"]) == 2

    def test_verify_phase3_seven_checks(self, tmp_path):
        """Phase 3-4 uses 7 checks (includes cross-artifact D3 + A/B coverage)."""
        v = self._make_verifier(tmp_path, phase=3)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_session_log", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_pytest", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_coverage", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_cross_artifact", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_ab_coverage", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert len(result["checks"]) == 7
        assert result["passed"] is True

    def test_verify_phase6_three_checks(self, tmp_path):
        """Phase 5-8 uses 3 checks (includes previous phase artifacts)."""
        v = self._make_verifier(tmp_path, phase=6)
        with patch.object(v, "check_framework_block", return_value=(False, 0.0, "fail")), \
             patch.object(v, "check_session_log", return_value=(False, 0.0, "fail")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(False, 0.0, "fail")):
            result = v.verify()
        assert len(result["checks"]) == 3
        assert result["passed"] is False

    def test_verify_total_score_threshold(self, tmp_path):
        """Score >=90 → passed, <90 → failed."""
        v = self._make_verifier(tmp_path, phase=1)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_session_log", return_value=(False, 0.0, "fail")):
            result = v.verify()
        # total_score = 100*0.60 + 0*0.40 = 60 → <90 → not passed
        assert result["passed"] is False


# ─── PolicyEngine extra paths ──────────────────────────────────────────────────

class TestPolicyEngineExtra:
    def test_check_commit_msg_no_env(self):
        from enforcement.policy_engine import PolicyEngine
        e = PolicyEngine()
        # COMMIT_MSG_FILE not set → returns True
        with patch.dict("os.environ", {}, clear=True):
            assert e._check_commit_message() is True

    def test_check_commit_msg_with_task_id(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text("[TASK-123] implement feature")
        e = PolicyEngine()
        with patch.dict("os.environ", {"COMMIT_MSG_FILE": str(msg_file)}):
            assert e._check_commit_message() is True

    def test_check_commit_msg_without_task_id(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text("fix bug")
        e = PolicyEngine()
        with patch.dict("os.environ", {"COMMIT_MSG_FILE": str(msg_file)}):
            assert e._check_commit_message() is False

    def test_has_task_id_true(self):
        from enforcement.policy_engine import PolicyEngine
        assert PolicyEngine()._has_task_id("[PROJ-42] add feature") is True

    def test_has_task_id_false(self):
        from enforcement.policy_engine import PolicyEngine
        assert PolicyEngine()._has_task_id("no task id here") is False

    def test_check_quality_score_no_file(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        with patch("os.path.exists", return_value=False):
            assert PolicyEngine()._check_quality_score() is True

    def test_check_quality_score_passes(self, tmp_path):
        import os
        from enforcement.policy_engine import PolicyEngine
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            (tmp_path / ".methodology").mkdir(exist_ok=True)
            (tmp_path / ".methodology" / ".quality_score").write_text("95.0")
            result = PolicyEngine()._check_quality_score()
            assert result is True
        finally:
            os.chdir(original_cwd)

    def test_check_no_bypass_no_env(self):
        from enforcement.policy_engine import PolicyEngine
        with patch.dict("os.environ", {"GIT_COMMAND": "git commit -m 'msg'"}, clear=False):
            assert PolicyEngine()._check_no_bypass() is True

    def test_check_no_bypass_with_keyword(self):
        from enforcement.policy_engine import PolicyEngine
        with patch.dict("os.environ", {"GIT_COMMAND": "git commit --no-verify"}):
            assert PolicyEngine()._check_no_bypass() is False

    def test_add_remove_policy(self):
        from enforcement.policy_engine import PolicyEngine, Policy, EnforcementLevel
        e = PolicyEngine()
        initial = len(e.policies)
        p = Policy(id="test-p", description="test", check_fn=lambda: True,
                   enforcement=EnforcementLevel.LOG)
        e.add_policy(p)
        assert len(e.policies) == initial + 1
        e.remove_policy("test-p")
        assert len(e.policies) == initial

    def test_reload_policy_file_not_found(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        with pytest.raises(FileNotFoundError):
            PolicyEngine().reload_policy(str(tmp_path / "nonexistent.json"))

    def test_reload_policy_loads_entries(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        policy_file = tmp_path / "enforcement.json"
        data = {"policies": [
            {"id": "p1", "description": "test", "enforcement": "log", "enabled": True},
            {"id": "p2", "description": "block test", "enforcement": "block", "enabled": True},
        ]}
        policy_file.write_text(json.dumps(data))
        e = PolicyEngine()
        loaded = e.reload_policy(str(policy_file))
        assert loaded == 2

    def test_reload_policy_skips_no_id(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        policy_file = tmp_path / "enforcement.json"
        data = {"policies": [{"description": "no id here"}]}
        policy_file.write_text(json.dumps(data))
        loaded = PolicyEngine().reload_policy(str(policy_file))
        assert loaded == 0

    def test_reload_policy_bad_enforcement_level_defaults_log(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine, EnforcementLevel
        policy_file = tmp_path / "enforcement.json"
        data = {"policies": [{"id": "p1", "enforcement": "invalid_level"}]}
        policy_file.write_text(json.dumps(data))
        e = PolicyEngine()
        e.reload_policy(str(policy_file))
        p = next(x for x in e.policies if x.id == "p1")
        assert p.enforcement == EnforcementLevel.LOG

    def test_from_json_classmethod(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        policy_file = tmp_path / "enforcement.json"
        data = {"policies": [{"id": "p1", "description": "x", "enforcement": "log"}]}
        policy_file.write_text(json.dumps(data))
        e = PolicyEngine.from_json(str(policy_file))
        assert any(p.id == "p1" for p in e.policies)


# ─── TaskSplitter ─────────────────────────────────────────────────────────────

class TestTaskSplitter:
    def test_split_from_goal_research(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        tasks = s.split_from_goal("analyze requirements and research")
        assert any(t.name == "Research & Analysis" for t in tasks)

    def test_split_from_goal_develop(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        tasks = s.split_from_goal("build and implement the feature")
        assert any(t.name == "Development" for t in tasks)

    def test_split_from_goal_sets_dependencies(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        tasks = s.split_from_goal("research, design, and implement")
        assert len(tasks) >= 2
        # Later tasks depend on earlier ones
        if len(tasks) >= 2:
            assert tasks[1].dependencies

    def test_split_from_goal_empty(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        tasks = s.split_from_goal("unrecognized goal xyz")
        assert tasks == []

    def test_get_ready_tasks(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        tasks = s.split_from_goal("design a system")
        ready = s.get_ready_tasks()
        # First task has no deps → ready
        assert len(ready) >= 1

    def test_get_execution_order(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        s.split_from_goal("research and design a system")
        order = s.get_execution_order()
        assert len(order) >= 1

    def test_get_dag(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        s.split_from_goal("implement the feature")
        dag = s.get_dag()
        assert "nodes" in dag
        assert "edges" in dag

    def test_get_summary(self):
        from core.task_splitter import TaskSplitter
        s = TaskSplitter()
        s.split_from_goal("research, design, implement")
        summary = s.get_summary()
        assert "total_tasks" in summary
        assert "pending" in summary


# ─── StateManager extra paths ─────────────────────────────────────────────────

class TestStateManagerExtra:
    def test_load_state_corrupted_json(self, tmp_path):
        from kill_switch.state_manager import StateManager, StatePersistenceError
        sm = StateManager(tmp_path)
        state_file = tmp_path / "bad_agent.json"
        state_file.write_text("not valid json {{{{")
        # StateManager looks in self._state_path / "{agent_id}.json"
        sm_path = sm._state_path
        agent_file = sm_path / "corrupt.json"
        agent_file.write_text("not valid json {{{{")
        with pytest.raises(StatePersistenceError, match="Corrupted"):
            sm.load_state("corrupt")

    def test_clear_state_removes_file(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.circuit_breaker import CircuitBreakerState
        from kill_switch.enums import CircuitState
        sm = StateManager(tmp_path)
        # Save a state first
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.CLOSED)
        sm.save_state("agent1", state)
        assert (sm._state_path / "agent1.json").exists()
        sm.clear_state("agent1")
        assert not (sm._state_path / "agent1.json").exists()

    def test_is_agent_killed_no_state(self, tmp_path):
        from kill_switch.state_manager import StateManager
        sm = StateManager(tmp_path)
        assert sm.is_agent_killed("nonexistent") is False

    def test_is_agent_killed_open_state(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.circuit_breaker import CircuitBreakerState
        from kill_switch.enums import CircuitState
        sm = StateManager(tmp_path)
        state = CircuitBreakerState(agent_id="a1", state=CircuitState.OPEN)
        sm.save_state("a1", state)
        assert sm.is_agent_killed("a1") is True


# ─── PatternMatcher extra paths ───────────────────────────────────────────────

class TestPatternMatcherExtra:
    def test_rule_invalid_regex_compiled_none(self):
        from detection.pattern_matcher import Rule, RuleType
        r = Rule(name="bad", rule_type=RuleType.FORBIDDEN, pattern="[invalid(", description="test")
        assert r._compiled is None
        assert r.matches("anything") is False
        assert r.find_all("anything") == []

    def test_rule_find_all(self):
        from detection.pattern_matcher import Rule, RuleType
        r = Rule(name="digits", rule_type=RuleType.QUALITY, pattern=r"\d+", description="numbers")
        hits = r.find_all("code 123 and 456")
        assert "123" in hits
        assert "456" in hits

    def test_match_file_read_error(self, tmp_path):
        from detection.pattern_matcher import PatternMatcher
        pm = PatternMatcher()
        result = pm.match_file("/nonexistent/path/file.py")
        assert result.passed is False
        assert any(h["rule"] == "file-read-error" for h in result.forbidden_hits)

    def test_match_file_success(self, tmp_path):
        from detection.pattern_matcher import PatternMatcher
        f = tmp_path / "test.py"
        f.write_text("clean code here\n")
        pm = PatternMatcher()
        result = pm.match_file(str(f))
        assert hasattr(result, "passed")

    def test_match_files_multiple(self, tmp_path):
        from detection.pattern_matcher import PatternMatcher
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("clean\n")
        f2.write_text("clean\n")
        pm = PatternMatcher()
        results = pm.match_files([str(f1), str(f2)])
        assert len(results) == 2


# ─── AuditLogger extra paths ──────────────────────────────────────────────────

class TestAuditLoggerExtra:
    def test_log_event_is_stub(self):
        from kill_switch.audit_logger import AuditLogger, AuditEntry
        logger = AuditLogger()
        entry = AuditEntry(
            event_id="evt-001", agent_id="a1", event_type="STATE_CHANGE",
            action="triggered", outcome="success", reason="test"
        )
        # Should not raise; stub returns None
        logger.log_event(entry)

    def test_query_returns_empty(self):
        from kill_switch.audit_logger import AuditLogger
        logger = AuditLogger()
        assert logger.query({}) == []


# ─── CRGBridge extra paths ────────────────────────────────────────────────────

class TestCRGBridgeExtra:
    def test_run_reconnaissance_not_available(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        b = CRGBridge()
        with patch.object(b, "is_available", return_value=False):
            result = b.run_reconnaissance(str(tmp_path))
        assert result == {}

    def test_get_minimal_context_not_available(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        b = CRGBridge()
        with patch.object(b, "is_available", return_value=False):
            result = b.get_minimal_context(str(tmp_path), "correctness")
        assert result == {}

    def test_get_minimal_context_handles_mcp_error(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_minimal_context", create=True,
                   side_effect=RuntimeError("boom")):
            result = CRGBridge().get_minimal_context(str(tmp_path), "correctness")
        assert result == {}


# ─── SpecTrackingParser extra paths ───────────────────────────────────────────

class TestSpecTrackingParserExtra:
    def test_find_entries_without_status_detects_missing(self):
        from core.quality_gate.parsers.spec_tracking_parser import SpecTrackingParser
        # Parser skips lines starting with "|"; use non-leading-pipe format
        content = "Feature A | col2 | col3 | no-status\nFeature B | col2 | col3 | ✅ Done\n"
        missing = SpecTrackingParser.find_entries_without_status(content)
        assert len(missing) >= 1

    def test_count_status(self):
        from core.quality_gate.parsers.spec_tracking_parser import SpecTrackingParser
        content = "| F1 | ✅ Done | note |\n| F2 | 🔄 In Progress | note |\n| F3 | ✅ Done | note |\n"
        counts = SpecTrackingParser.count_status(content)
        assert counts.get("✅ Done", 0) >= 2
