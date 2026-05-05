"""Edge/boundary coverage for phase_hooks, policy_engine, spec_tracking_checker,
gap_detector, phase_truth_verifier, state_manager."""

import json
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# phase_hooks
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseHooksEdge:
    def test_preflight_fsm_freeze_blocks(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "FREEZE", "current_phase": 1})
        )
        result = hooks.preflight_fsm_check()
        assert result["passed"] is False
        assert result["state"] == "FREEZE"

    def test_preflight_fsm_cannot_go_backwards(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 3})
        )
        result = hooks.preflight_fsm_check()
        assert result["passed"] is False
        assert "Cannot go backwards" in result["message"]

    def test_preflight_fsm_no_state_file(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        result = hooks.preflight_fsm_check()
        assert result["passed"] is False
        assert "not found" in result["message"]

    def test_preflight_constitution_import_error(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        with patch.dict("sys.modules", {"quality_gate.constitution": None}):
            result = hooks.preflight_constitution()
        assert result["passed"] is False

    def test_preflight_tool_registry_import_error(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        result = hooks.preflight_tool_registry()
        assert result["passed"] is True
        assert result["skipped"] is True

    def test_preflight_all_aggregates(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        hooks.preflight_fsm_check = MagicMock(return_value={"passed": True})
        hooks.preflight_constitution = MagicMock(return_value={"passed": True})
        hooks.preflight_tool_registry = MagicMock(return_value={"passed": True})
        result = hooks.preflight_all()
        assert result["all_passed"] is True

    def test_preflight_all_fails_when_any_fails(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        hooks.preflight_fsm_check = MagicMock(return_value={"passed": False})
        hooks.preflight_constitution = MagicMock(return_value={"passed": True})
        hooks.preflight_tool_registry = MagicMock(return_value={"passed": True})
        result = hooks.preflight_all()
        assert result["all_passed"] is False

    def test_postflight_constitution_calls_preflight(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        hooks.preflight_constitution = MagicMock(return_value={"passed": True, "score": 90, "violations": 0})
        result = hooks.postflight_constitution()
        assert result["passed"] is True

    def test_postflight_update_state_advances_phase(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=3)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 2})
        )
        result = hooks.postflight_update_state(success=True)
        assert result["updated"] is True
        assert result["new_phase"] == 3

    def test_postflight_update_state_no_advance(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 3})
        )
        result = hooks.postflight_update_state(success=True)
        assert result["updated"] is False
        assert "phase_not_advanced" in result["reason"]

    def test_postflight_update_state_execution_failed(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        result = hooks.postflight_update_state(success=False)
        assert result["updated"] is False
        assert "execution_failed" in result["reason"]

    def test_postflight_all_full_flow(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        hooks.postflight_constitution = MagicMock(return_value={"passed": True})
        hooks.postflight_update_state = MagicMock(return_value={"updated": True})
        hooks.postflight_summary = MagicMock(return_value={"total_frs": 0, "approved": 0})
        result = hooks.postflight_all()
        assert "success" in result

    def test_monitoring_hr12_triggers_at_max(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        assert hooks.monitoring_hr12_check("FR-01", iteration=5, max_iterations=5) is False

    def test_add_fr_result_records(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        dev = MagicMock(status="done", confidence=8)
        rev = MagicMock(status="done", review_status="APPROVE", confidence=9)
        hooks.add_fr_result("FR-01", dev, rev)
        assert hooks.fr_results[-1]["review_status"] == "APPROVE"


# ═══════════════════════════════════════════════════════════════════════════════
# policy_engine
# ═══════════════════════════════════════════════════════════════════════════════

class TestPolicyEngineEdge:
    def test_check_policy_not_found(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.check("nonexistent-policy")
        assert result.passed is False
        assert "not found" in result.message

    def test_check_policy_fn_raises_exception(self):
        from enforcement.policy_engine import PolicyEngine, Policy, EnforcementLevel
        engine = PolicyEngine()
        engine.policies = [
            Policy(id="broken", description="broken check",
                   check_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                   enforcement=EnforcementLevel.BLOCK)
        ]
        result = engine.check("broken")
        assert result.passed is False

    def test_enforce_all_raises_on_block(self):
        from enforcement.policy_engine import (
            PolicyEngine, Policy, EnforcementLevel, PolicyViolationException,
        )
        engine = PolicyEngine()
        engine.policies = [
            Policy(id="fail-hard", description="always fails",
                   check_fn=lambda: False, enforcement=EnforcementLevel.BLOCK)
        ]
        with pytest.raises(PolicyViolationException):
            engine.enforce_all()

    def test_enforce_all_skips_disabled(self):
        from enforcement.policy_engine import PolicyEngine, Policy, EnforcementLevel
        engine = PolicyEngine()
        engine.policies = [
            Policy(id="disabled-block", description="disabled",
                   check_fn=lambda: False, enforcement=EnforcementLevel.BLOCK,
                   enabled=False)
        ]
        results = engine.enforce_all()
        assert len(results) == 0

    def test_reload_policy_from_json(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        jf = tmp_path / "enforcement.json"
        jf.write_text(json.dumps({"policies": [{"id": "custom-pol", "description": "desc",
            "enforcement": "block", "severity": "high", "enabled": True}]}))
        engine = PolicyEngine()
        count = engine.reload_policy(str(jf))
        assert count == 1

    def test_reload_policy_file_not_found(self):
        from enforcement.policy_engine import PolicyEngine
        with pytest.raises(FileNotFoundError):
            PolicyEngine().reload_policy("/nonexistent/policy.json")

    def test_from_json_factory(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        jf = tmp_path / "enforcement.json"
        jf.write_text(json.dumps({"policies": []}))
        engine = PolicyEngine.from_json(str(jf))
        assert isinstance(engine, PolicyEngine)

    def test_disable_policy_warns(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        with pytest.warns(DeprecationWarning):
            engine.disable(engine.policies[0].id)

    def test_remove_policy(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        original_count = len(engine.policies)
        engine.remove_policy(engine.policies[0].id)
        assert len(engine.policies) == original_count - 1

    def test_get_summary(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        engine.results = []  # clear accumulated results
        # Add known good/bad results
        engine.check(engine.policies[0].id)
        summary = engine.get_summary()
        assert "total" in summary
        assert "pass_rate" in summary

    def test_raise_on_block_raises(self):
        from enforcement.policy_engine import (
            PolicyEngine, PolicyResult, EnforcementLevel, PolicyViolationException,
        )
        blocked = PolicyResult(
            policy_id="test", passed=False, enforcement=EnforcementLevel.BLOCK,
            message="fail", timestamp="2026-01-01T00:00:00", blocked=True,
        )
        with pytest.raises(PolicyViolationException):
            PolicyEngine().raise_on_block([blocked])

    def test_raise_on_block_no_block(self):
        from enforcement.policy_engine import PolicyEngine, PolicyResult, EnforcementLevel
        ok = PolicyResult(
            policy_id="test", passed=True, enforcement=EnforcementLevel.BLOCK,
            message="ok", timestamp="2026-01-01T00:00:00", blocked=False,
        )
        PolicyEngine().raise_on_block([ok])  # no raise → pass

    def test_create_hard_block_engine(self):
        from enforcement.policy_engine import create_hard_block_engine, EnforcementLevel
        engine = create_hard_block_engine()
        for p in engine.policies:
            assert p.enforcement == EnforcementLevel.BLOCK

    def test_check_commit_message_without_env(self):
        from enforcement.policy_engine import PolicyEngine
        with patch.dict("os.environ", {}, clear=True):
            engine = PolicyEngine()
            assert engine._check_commit_message() is True

    def test_check_commit_message_with_file(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        commit_file = tmp_path / "COMMIT_MSG"
        commit_file.write_text("fix: add validation [JIRA-123]")
        with patch.dict("os.environ", {"COMMIT_MSG_FILE": str(commit_file)}):
            engine = PolicyEngine()
            assert engine._check_commit_message() is True

    def test_check_no_bypass_detects_keyword(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        with patch.dict("os.environ", {"GIT_COMMAND": "commit --no-verify"}):
            assert engine._check_no_bypass() is False

    def test_check_test_coverage_reads_file(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        cov = tmp_path / ".methodology"
        cov.mkdir()
        (cov / ".coverage").write_text("82.5")
        engine = PolicyEngine()
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = "82.5"
            result = engine._check_test_coverage()
            assert result is True

    def test_check_security_score_below_threshold(self):
        from enforcement.policy_engine import PolicyEngine
        engine = PolicyEngine()
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = "50"
            result = engine._check_security_score()
            assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# spec_tracking_checker
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpecTrackingCheckerEdge:
    def test_check_completeness_with_entry_no_status(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        st = tmp_path / "SPEC_TRACKING.md"
        st.write_text(
            "# Core Features\n"
            "Feature | Status | Notes |\n"
            "FR-001 | ✅ Done | desc |\n"
            "FR-002 |  | no status |\n"   # empty status → detected
            "\n## Update log\n"
            "Date | Change | Notes |\n"
            "2024-01-01 | init | done |\n"
        )
        checker = SpecTrackingChecker(str(tmp_path))
        result = checker.check_completeness()
        assert "Entry missing status" in str(result["missing"])

    def test_check_completeness_missing_table(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        st = tmp_path / "SPEC_TRACKING.md"
        st.write_text("# Some doc\nStatus: Done\n## Update Log\n")
        checker = SpecTrackingChecker(str(tmp_path))
        result = checker.check_completeness()
        assert "Core Features table" in result["missing"]

    def test_run_enforcement_with_valid_spec(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        st = tmp_path / "SPEC_TRACKING.md"
        st.write_text(
            "# Core Features\n| Feature | Status | Notes |\n"
            "| FR-001 | ✅ Done | done |\n\n## Update Log\n"
        )
        checker = SpecTrackingChecker(str(tmp_path))
        result = checker.run_enforcement()
        assert result["exists"] is True
        assert result["completeness"] >= 0

    def test_run_enforcement_not_found(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        checker = SpecTrackingChecker(str(tmp_path))
        result = checker.run_enforcement()
        assert result["exists"] is False
        assert result["completeness"] == 0

    def test_print_report_found_complete(self, tmp_path, capsys):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        st = tmp_path / "SPEC_TRACKING.md"
        st.write_text(
            "# Core Features\n| Feature | Status | Notes |\n"
            "| FR-001 | ✅ Done | |\n\n## Update Log\n| Date | Change |\n"
        )
        checker = SpecTrackingChecker(str(tmp_path))
        checker.print_report()
        captured = capsys.readouterr().out
        assert "Specification Tracking Report" in captured

    def test_print_report_not_found(self, tmp_path, capsys):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        checker = SpecTrackingChecker(str(tmp_path))
        checker.print_report()
        captured = capsys.readouterr().out
        assert "not found" in captured

    def test_run_returns_bool(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        checker = SpecTrackingChecker(str(tmp_path))
        assert checker.run() is False


# ═══════════════════════════════════════════════════════════════════════════════
# gap_detector
# ═══════════════════════════════════════════════════════════════════════════════

class TestGapDetectorEdge:
    def _make_detector(self, spec_items=None, code_items=None):
        from gap_detector.detector import GapDetector
        from gap_detector.parser import ParsedSpec, FeatureItem
        from gap_detector.scanner import ScannedCode, CodeFile, CodeItem

        spec = ParsedSpec(feature_items=spec_items or [])
        modules = [
            CodeFile(
                module_name="test", file_path="test.py",
                items=code_items or [],
            )
        ]
        code = ScannedCode(modules=modules)
        return GapDetector(spec, code)

    def test_detect_match_exception_returns_empty(self):
        detector = self._make_detector()
        with patch.object(detector, "_match_spec_to_code", side_effect=RuntimeError("boom")):
            result = detector.detect()
            assert result == []

    def test_get_summary_orphaned(self):
        from gap_detector.detector import Gap
        detector = self._make_detector()
        detector._gaps = [
            Gap(gap_type="ORPHANED", code_item="orphan_func", severity="minor",
                reason="no spec", recommended_action="add spec")
        ]
        s = detector.get_summary()
        assert s.orphaned == 1

    def test_get_summary_critical(self):
        from gap_detector.detector import Gap
        detector = self._make_detector()
        detector._gaps = [
            Gap(gap_type="MISSING", spec_item="critical_fr", severity="critical",
                reason="missing", recommended_action="implement")
        ]
        s = detector.get_summary()
        assert s.critical == 1

    def test_compute_similarity_both_empty(self):
        from gap_detector.detector import GapDetector
        d = GapDetector.__new__(GapDetector)
        assert d._compute_similarity("", "") == 1.0

    def test_compute_similarity_one_empty(self):
        from gap_detector.detector import GapDetector
        d = GapDetector.__new__(GapDetector)
        assert d._compute_similarity("hello", "") == 0.0

    def test_find_best_match_skips_non_public(self):
        from gap_detector.detector import GapDetector
        from gap_detector.parser import FeatureItem
        from gap_detector.scanner import CodeItem

        d = GapDetector.__new__(GapDetector)
        d._normalize_name = GapDetector._normalize_name.__get__(d, GapDetector)
        d._compute_similarity = GapDetector._compute_similarity.__get__(d, GapDetector)
        d.similarity_threshold = 0.6

        si = FeatureItem(id="FR-1", name="public_func", priority="P1", description="",
                         line_number=1, depends_on=[])
        ci = CodeItem(id="c1", kind="function", module="test", name="public_func",
                      file_path="test.py", line_number=1,
                      docstring="yes", is_public=False)
        match = d._find_best_match(si, [ci])
        assert match.match_type == "none"

    def test_find_best_match_exact(self):
        from gap_detector.detector import GapDetector
        from gap_detector.parser import FeatureItem
        from gap_detector.scanner import CodeItem

        d = GapDetector.__new__(GapDetector)
        d._normalize_name = GapDetector._normalize_name.__get__(d, GapDetector)
        d._compute_similarity = GapDetector._compute_similarity.__get__(d, GapDetector)
        d.similarity_threshold = 0.6

        si = FeatureItem(id="FR-1", name="my_func", priority="P1", description="",
                         line_number=1, depends_on=[])
        ci = CodeItem(id="c1", kind="function", module="test", name="my_func",
                      file_path="test.py", line_number=1,
                      docstring="yes", is_public=True)
        match = d._find_best_match(si, [ci])
        assert match.match_type == "exact"


# ═══════════════════════════════════════════════════════════════════════════════
# phase_truth_verifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhaseTruthVerifierEdge:
    def test_check_session_log_line_by_line(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        log = tmp_path / "sessions_spawn.log"
        log.write_text(
            '{"role":"architect","session_id":"s1"}\n'
            '{"role":"reviewer","session_id":"s2"}\n'
        )
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        passed, score, details = verifier.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_check_session_log_single_json_dict(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        log = tmp_path / "sessions_spawn.log"
        log.write_text(json.dumps({"sessions": [
            {"role": "architect", "session_id": "s1"},
            {"role": "reviewer", "session_id": "s2"},
        ]}))
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        passed, _, _ = verifier.check_session_log()
        assert passed is True

    def test_check_session_log_not_found(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        passed, score, _ = verifier.check_session_log()
        assert passed is False
        assert score == 0.0

    def test_check_pytest_not_found(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            passed, score, _ = verifier.check_pytest()
            assert passed is False

    def test_check_pytest_timeout(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        import subprocess as _sp
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired("pytest", 120)):
            passed, score, details = verifier.check_pytest()
            assert passed is False
            assert "timed out" in details

    def test_verify_phase_lt_3(self, tmp_path, capsys):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=1)
        with patch.object(verifier, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_session_log", return_value=(True, 100.0, "ok")):
            result = verifier.verify()
        assert result["passed"] is True

    def test_verify_phase_gt_4(self, tmp_path, capsys):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=6)
        with patch.object(verifier, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_session_log", return_value=(True, 100.0, "ok")):
            result = verifier.verify()
        assert result["passed"] is True

    def test_check_framework_block_import_error(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        with patch("enforcement.framework_enforcer.FrameworkEnforcer",
                   side_effect=ImportError("no module")):
            passed, score, details = verifier.check_framework_block()
            assert passed is False
            assert "Cannot import" in details

    def test_get_manual_checklist_phase3_artifact_exists(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        (tmp_path / "03-implementation").mkdir()
        (tmp_path / "03-implementation" / "src").mkdir()
        (tmp_path / "03-implementation" / "tests").mkdir()
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        checklist = verifier.get_manual_checklist()
        assert len(checklist) > 0
        assert any("present" in c["status"] for c in checklist)


# ═══════════════════════════════════════════════════════════════════════════════
# state_manager
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateManagerEdge:
    def test_load_state_returns_cached(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.models import CircuitBreakerState
        from kill_switch.enums import CircuitState

        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent-1", state=CircuitState.CLOSED,
                                    failure_count=0)
        sm.save_state("agent-1", state)
        # Second load should hit cache
        loaded = sm.load_state("agent-1")
        assert loaded is not None
        assert loaded.agent_id == "agent-1"

    def test_load_state_file_not_found(self, tmp_path):
        from kill_switch.state_manager import StateManager
        sm = StateManager(state_path=tmp_path)
        assert sm.load_state("nonexistent") is None

    def test_load_state_corrupted_json(self, tmp_path):
        from kill_switch.state_manager import StateManager, StatePersistenceError
        sm = StateManager(state_path=tmp_path)
        (tmp_path / "agent-bad.json").write_text("{not json}")
        with pytest.raises(StatePersistenceError, match="Corrupted"):
            sm.load_state("agent-bad")

    def test_clear_state_removes_file(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.models import CircuitBreakerState
        from kill_switch.enums import CircuitState

        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent-1", state=CircuitState.CLOSED,
                                    failure_count=0)
        sm.save_state("agent-1", state)
        assert (tmp_path / "agent-1.json").exists()
        sm.clear_state("agent-1")
        assert not (tmp_path / "agent-1.json").exists()

    def test_is_agent_killed_true(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.models import CircuitBreakerState
        from kill_switch.enums import CircuitState

        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent-1", state=CircuitState.OPEN,
                                    failure_count=5)
        sm.save_state("agent-1", state)
        assert sm.is_agent_killed("agent-1") is True

    def test_is_agent_killed_false_when_no_state(self, tmp_path):
        from kill_switch.state_manager import StateManager
        sm = StateManager(state_path=tmp_path)
        assert sm.is_agent_killed("agent-nonexistent") is False

    def test_dict_to_state_handles_utc_z(self, tmp_path):
        from kill_switch.state_manager import StateManager
        from kill_switch.enums import CircuitState
        sm = StateManager(state_path=tmp_path)
        data = {
            "agent_id": "agent-1",
            "state": CircuitState.CLOSED.value,
            "failure_count": 0,
            "last_failure_time": "2026-01-01T00:00:00Z",
            "cooldown_end": None,
            "last_success_time": None,
            "opened_at": None,
            "closed_at": None,
        }
        state = sm._dict_to_state(data)
        assert state.agent_id == "agent-1"
        assert state.last_failure_time is not None
