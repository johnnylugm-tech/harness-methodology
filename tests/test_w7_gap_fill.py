"""
W7 — Gap-fill tests targeting Category C + D (84-86% goal).
"""
import json
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

def _make_spec_tracking(tmp_path, content: str = None) -> Path:  # type: ignore[reportArgumentType]
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
class TestPhaseTruthVerifier:
    def _make_verifier(self, tmp_path, phase=1):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        return PhaseTruthVerifier(str(tmp_path), phase)







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

    # Round 25: check_pytest/check_coverage read one shared suite execution
    # (core.quality_gate.test_suite_run.run_suite) instead of spawning pytest
    # themselves, so these supply the measurement rather than mocking
    # subprocess. Three of these previously passed for the wrong reason —
    # tmp_path has no source tree, so the old subprocess mock was never even
    # consulted on the paths that mattered.
    @staticmethod
    def _suite(**kwargs):
        from core.quality_gate.test_suite_run import SuiteResult

        base = dict(
            passed=True, coverage=100.0, test_target="tests", cov_target="src",
            returncode=0, output="", ran=True,
        )
        base.update(kwargs)
        return SuiteResult(**base)  # type: ignore[arg-type]

    def test_check_pytest_passes(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite()):
            passed, score, _ = v.check_pytest()
        assert passed is True
        assert score == 100.0

    def test_check_pytest_fails(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite(passed=False, returncode=1,
                                            output="1 failed\nFAILED test")):
            passed, score, _ = v.check_pytest()
        assert passed is False
        assert score == 0.0

    def test_check_pytest_timeout(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite(passed=False, coverage=None, returncode=124)):
            passed, score, detail = v.check_pytest()
        assert passed is False
        assert "timed out" in detail

    def test_check_pytest_not_runnable(self, tmp_path):
        v = self._make_verifier(tmp_path)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite(ran=False, passed=False, coverage=None,
                                            returncode=127,
                                            reason="pytest not runnable: no pytest")):
            passed, score, detail = v.check_pytest()
        assert passed is False
        assert "not runnable" in detail

    def test_check_coverage_reports_the_measurement(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=3)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite(coverage=90.0)):
            passed, score, detail = v.check_coverage()
        assert passed is True
        assert "90.0%" in detail

    def test_check_coverage_unmeasurable(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=3)
        with patch("core.quality_gate.test_suite_run.run_suite",
                   return_value=self._suite(ran=False, passed=False, coverage=None,
                                            reason="boom")):
            passed, score, detail = v.check_coverage()
        assert passed is False
        assert "boom" in detail

    def test_get_manual_checklist_phase3(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=3)
        checklist = v.get_manual_checklist()
        assert len(checklist) >= 1  # at least sessions_spawn.log

    def test_get_manual_checklist_phase1(self, tmp_path):
        v = self._make_verifier(tmp_path, phase=1)
        checklist = v.get_manual_checklist()
        assert any("SPEC_TRACKING" in item["item"] or "SRS" in item["item"]
                   for item in checklist)

    def test_verify_phase1_two_checks(self, tmp_path):
        """Phase 1-2 uses BLOCK + previous-phase artifacts (2 checks).

        Round 21 站3: the session-log check left the weighted list. It read a
        file the evaluated agent writes and that no review or CI ever sees, so
        every signal it carried was one the audited party could author.
        """
        v = self._make_verifier(tmp_path, phase=1)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert result["passed"] is True
        assert len(result["checks"]) == 2

    def test_verify_phase3_five_checks(self, tmp_path):
        """Phase 3-4 uses 6 checks: framework/pytest/coverage/previous/cross-artifact
        + SRS-mandatory reconciliation (no SRS.md in this fixture → InfraSkip,
        renormalized away, matching the other 5 explicitly-mocked checks)."""
        v = self._make_verifier(tmp_path, phase=3)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_pytest", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_coverage", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")), \
             patch.object(v, "check_cross_artifact", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert len(result["checks"]) == 6
        assert result["passed"] is True

    def test_verify_phase6_two_checks(self, tmp_path):
        """Phase 5-8 uses 4 checks: framework block + previous phase artifacts
        + SRS-mandatory reconciliation (no SRS.md in this fixture → InfraSkip)
        + cross-artifact consistency.

        Cross-artifact joined the list at Round 55 站6. It had lived only in
        the Phase 3-4 list while being the sole consumer of
        run_cross_artifact_checks, so every per-phase artifact check that
        function performs — including check_phase_title's P5..P9 entries and
        the CONFIG_RECORDS.md placeholder check — had no caller at those
        phases."""
        v = self._make_verifier(tmp_path, phase=6)
        with patch.object(v, "check_framework_block", return_value=(False, 0.0, "fail")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(False, 0.0, "fail")):
            result = v.verify()
        assert len(result["checks"]) == 4
        assert result["passed"] is False

    def test_verify_total_score_threshold(self, tmp_path):
        """Score >=threshold → passed, < → failed (renormalized over active checks)."""
        v = self._make_verifier(tmp_path, phase=1)
        # P1-2: framework_block 0.70 + previous 0.30. framework fail → 0*0.70 + 100*0.30
        # = 30 (renormalized: 30/1.0) → < threshold → not passed.
        with patch.object(v, "check_framework_block", return_value=(False, 0.0, "fail")), \
             patch.object(v, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")):
            result = v.verify()
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
        s.split_from_goal("design a system")
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
    def test_rule_invalid_regex_raises(self):
        """Bug fix: malformed regex on a FORBIDDEN rule must raise re.error
        at construction (fail-loud). Previously it was silently swallowed,
        making the rule inert and bypassing the check at runtime.
        """
        import re
        from detection.pattern_matcher import Rule, RuleType
        with pytest.raises(re.error):
            Rule(name="bad", rule_type=RuleType.FORBIDDEN, pattern="[invalid(", description="test")

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
    def test_log_event_is_stub(self, tmp_path):
        from kill_switch.audit_logger import AuditLogger, AuditEntry
        # Use tmp_path to avoid polluting the global tempdir across test runs
        # (the AuditLogger default writes to tempfile.gettempdir()/kill_switch_logs).
        logger = AuditLogger(log_dir=str(tmp_path))
        entry = AuditEntry(
            event_id="evt-001", agent_id="a1", event_type="STATE_CHANGE",
            action="triggered", outcome="success", reason="test"
        )
        # Should not raise; method writes JSONL + ring buffer entry.
        logger.log_event(entry)

    def test_query_returns_empty(self):
        from kill_switch.audit_logger import AuditLogger
        logger = AuditLogger()
        assert logger.query({}) == []

    def test_log_event_kwarg_form_interrupt_engine_compat(self, tmp_path):
        """Regression: InterruptEngine._log_event calls log_event with kwargs.

        Pre-fix:  AuditLogger.log_event(entry: AuditEntry) was a `pass` stub with
                  a positional-only signature, so this call raised
                  TypeError: log_event() got an unexpected keyword argument.
        Post-fix: log_event accepts kwargs and synthesises an AuditEntry.
        See kill_switch/interrupt_engine.py:45-48 for the call site.
        """
        from kill_switch.audit_logger import AuditLogger
        logger = AuditLogger(log_dir=str(tmp_path))

        # Exact call pattern from InterruptEngine._log_event.
        logger.log_event(
            event_type="INTERRUPT_STARTED",
            agent_id="a1",
            reason="test reason",
            actor="test-operator",
            metadata={"event_id": "evt-001"},
        )

        # The synthesised entry should be queryable via the in-memory ring.
        results = logger.query({"agent_id": "a1"})
        assert len(results) == 1
        entry = results[0]
        assert entry.event_type == "INTERRUPT_STARTED"
        assert entry.action == "test-operator"   # actor mapped to action
        assert entry.outcome == "recorded"
        assert entry.reason == "test reason"
        assert entry.metadata == {"event_id": "evt-001"}

        # And persisted to the JSONL log file for replay.
        persisted = logger.read_log_file()
        assert len(persisted) == 1
        assert persisted[0]["agent_id"] == "a1"
        assert persisted[0]["event_type"] == "INTERRUPT_STARTED"


# ─── CRGBridge extra paths ────────────────────────────────────────────────────

class TestCRGBridgeExtra:
    def test_run_reconnaissance_raises_when_no_recon_file(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import pytest
        with pytest.raises(FileNotFoundError, match="CRG reconnaissance data not found"):
            CRGBridge().run_reconnaissance(str(tmp_path))

    def test_get_minimal_context_calls_mcp_for_dimension(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.mcp__code_review_graph__get_minimal_context_tool.return_value = {
            "summary": "test dimension context"
        }
        result = CRGBridge().get_minimal_context(str(tmp_path), "correctness")
        assert result["summary"] == "test dimension context"

    def test_refresh_graph_does_not_require_recon_file(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        # refresh_graph() only builds the graph — no file read
        CRGBridge().refresh_graph(str(tmp_path))


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
