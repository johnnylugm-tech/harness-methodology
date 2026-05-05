"""
W6 — Gap-fill tests to reach >80% scoped coverage.
Targets: enforcement_config, hybrid_workflow, verification_gate,
         sessions_spawn_logger, phase_hooks, agent_proof_hook,
         stage_pass_generator.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── EnforcementConfig ────────────────────────────────────────────────────────

class TestEnforcementMode:
    def test_values(self):
        from core.enforcement_config import EnforcementMode
        assert EnforcementMode.LOCAL.value == "local"
        assert EnforcementMode.SELF_HOSTED.value == "self_hosted"
        assert EnforcementMode.CLOUD.value == "cloud"


class TestPlatform:
    def test_values(self):
        from core.enforcement_config import Platform
        assert Platform.NONE.value == "none"
        assert Platform.GITHUB.value == "github"


class TestEnforcementConfig:
    def test_defaults(self):
        from core.enforcement_config import EnforcementConfig, EnforcementMode, Platform
        c = EnforcementConfig()
        assert c.mode == EnforcementMode.LOCAL
        assert c.platform == Platform.NONE
        assert c.quality_gate_threshold == 90.0
        assert c.coverage_threshold == 80.0
        assert c.enable_registry is True

    def test_from_dict_full(self):
        from core.enforcement_config import EnforcementConfig, EnforcementMode, Platform
        d = {
            "mode": "cloud", "platform": "github",
            "enforce_on_commit": False, "enforce_on_push": False,
            "enforce_on_pr": True, "enforce_on_merge": False,
            "strict_mode": False, "allow_bypass": True,
            "quality_gate_threshold": 85.0, "security_threshold": 90.0,
            "coverage_threshold": 75.0, "platform_config": {"key": "val"},
            "enable_registry": False, "enable_constitution_check": False,
            "enable_policy_engine": False,
        }
        c = EnforcementConfig.from_dict(d)
        assert c.mode == EnforcementMode.CLOUD
        assert c.platform == Platform.GITHUB
        assert c.allow_bypass is True
        assert c.platform_config == {"key": "val"}

    def test_from_dict_missing_keys_uses_defaults(self):
        from core.enforcement_config import EnforcementConfig
        c = EnforcementConfig.from_dict({})
        assert c.coverage_threshold == 80.0
        assert c.strict_mode is True

    def test_from_json(self):
        from core.enforcement_config import EnforcementConfig
        j = '{"mode": "local", "platform": "none"}'
        c = EnforcementConfig.from_json(j)
        assert c.mode.value == "local"

    def test_to_dict_round_trip(self):
        from core.enforcement_config import EnforcementConfig
        c = EnforcementConfig()
        d = c.to_dict()
        assert d["mode"] == "local"
        assert d["coverage_threshold"] == 80.0
        c2 = EnforcementConfig.from_dict(d)
        assert c2.coverage_threshold == c.coverage_threshold

    def test_to_json(self):
        from core.enforcement_config import EnforcementConfig
        j = EnforcementConfig().to_json()
        data = json.loads(j)
        assert "mode" in data

    def test_save_writes_file(self, tmp_path):
        from core.enforcement_config import EnforcementConfig
        path = str(tmp_path / "sub" / "enforcement.json")
        EnforcementConfig().save(path)
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert "mode" in data

    def test_load_returns_default_when_no_file(self, tmp_path):
        from core.enforcement_config import EnforcementConfig
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("METHODOLOGY_ENFORCEMENT_CONFIG", None)
            c = EnforcementConfig.load(str(tmp_path / "nonexistent.json"))
        assert c.mode.value == "local"

    def test_load_from_env(self):
        from core.enforcement_config import EnforcementConfig, EnforcementMode
        j = '{"mode": "cloud", "platform": "none"}'
        with patch.dict("os.environ", {"METHODOLOGY_ENFORCEMENT_CONFIG": j}):
            c = EnforcementConfig.load()
        assert c.mode == EnforcementMode.CLOUD

    def test_load_from_file(self, tmp_path):
        from core.enforcement_config import EnforcementConfig
        p = tmp_path / "enf.json"
        p.write_text('{"mode": "self_hosted", "platform": "none"}')
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("METHODOLOGY_ENFORCEMENT_CONFIG", None)
            c = EnforcementConfig.load(str(p))
        assert c.mode.value == "self_hosted"


class TestConfigGenerator:
    def test_local_only(self):
        from core.enforcement_config import ConfigGenerator, EnforcementMode
        c = ConfigGenerator.local_only()
        assert c.mode == EnforcementMode.LOCAL
        assert c.enforce_on_push is False

    def test_github_actions(self):
        from core.enforcement_config import ConfigGenerator, EnforcementMode, Platform
        c = ConfigGenerator.github_actions()
        assert c.mode == EnforcementMode.CLOUD
        assert c.platform == Platform.GITHUB
        assert "workflow_file" in c.platform_config

    def test_auto_detect_github(self):
        from core.enforcement_config import ConfigGenerator, EnforcementMode
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            c = ConfigGenerator.auto_detect()
        assert c.mode == EnforcementMode.CLOUD

    def test_auto_detect_local(self):
        from core.enforcement_config import ConfigGenerator, EnforcementMode
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
        with patch.dict("os.environ", env, clear=True):
            c = ConfigGenerator.auto_detect()
        assert c.mode == EnforcementMode.LOCAL


# ─── HybridWorkflow ────────────────────────────────────────────────────────────

class TestHybridWorkflowAnalyze:
    def test_small_change(self):
        from core.hybrid_workflow import HybridWorkflow, ChangeType
        hw = HybridWorkflow(small_change_threshold=10)
        diff = "\n".join(["+line"] * 3 + ["-line"] * 2)  # 5 lines
        a = hw.analyze_change(diff)
        assert a.type == ChangeType.SMALL

    def test_large_change_by_count(self):
        from core.hybrid_workflow import HybridWorkflow, ChangeType
        hw = HybridWorkflow(large_change_threshold=30)
        diff = "\n".join(["+line"] * 20 + ["-line"] * 15)
        a = hw.analyze_change(diff)
        assert a.type == ChangeType.LARGE

    def test_security_keyword_forces_large(self):
        from core.hybrid_workflow import HybridWorkflow, ChangeType
        hw = HybridWorkflow()
        a = hw.analyze_change("+token = 'abc'")
        assert a.type == ChangeType.LARGE
        assert a.is_security_related is True

    def test_new_feature_keyword_forces_large(self):
        from core.hybrid_workflow import HybridWorkflow, ChangeType
        hw = HybridWorkflow()
        a = hw.analyze_change("+# new function here")
        assert a.type == ChangeType.LARGE
        assert a.is_new_feature is True

    def test_medium_change_small_pass(self):
        from core.hybrid_workflow import HybridWorkflow, ChangeType
        hw = HybridWorkflow(small_change_threshold=5, large_change_threshold=30)
        # 10 changes — between thresholds → SMALL
        diff = "\n".join(["+line"] * 10)
        a = hw.analyze_change(diff)
        assert a.type == ChangeType.SMALL


class TestHybridWorkflowShouldReview:
    def test_mode_off_never_reviews(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode, ChangeType, ChangeAnalysis
        hw = HybridWorkflow(mode=WorkflowMode.OFF)
        a = ChangeAnalysis(type=ChangeType.LARGE, lines_changed=50, files_affected=1,
                           is_security_related=True, is_new_feature=False, reason="test")
        assert hw.should_review(a) is False
        assert hw.stats["auto_approved"] == 1

    def test_mode_on_always_reviews(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode, ChangeType, ChangeAnalysis
        hw = HybridWorkflow(mode=WorkflowMode.ON)
        a = ChangeAnalysis(type=ChangeType.SMALL, lines_changed=1, files_affected=1,
                           is_security_related=False, is_new_feature=False, reason="test")
        assert hw.should_review(a) is True
        assert hw.stats["review_required"] == 1

    def test_hybrid_large_reviews(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode, ChangeType, ChangeAnalysis
        hw = HybridWorkflow(mode=WorkflowMode.HYBRID)
        a = ChangeAnalysis(type=ChangeType.LARGE, lines_changed=50, files_affected=1,
                           is_security_related=False, is_new_feature=False, reason="test")
        assert hw.should_review(a) is True

    def test_hybrid_small_auto_passes(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode, ChangeType, ChangeAnalysis
        hw = HybridWorkflow(mode=WorkflowMode.HYBRID)
        a = ChangeAnalysis(type=ChangeType.SMALL, lines_changed=3, files_affected=1,
                           is_security_related=False, is_new_feature=False, reason="test")
        assert hw.should_review(a) is False


class TestHybridWorkflowExecute:
    def test_auto_approved_calls_code_func(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode
        hw = HybridWorkflow(mode=WorkflowMode.OFF)
        fn = MagicMock(return_value="output")
        result = hw.execute("+small change", fn)
        assert result["status"] == "auto_approved"
        fn.assert_called_once()

    def test_needs_review_skips_code_func(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode
        hw = HybridWorkflow(mode=WorkflowMode.ON)
        fn = MagicMock()
        result = hw.execute("+any change", fn)
        assert result["status"] == "needs_review"
        fn.assert_not_called()


class TestHybridWorkflowStats:
    def test_get_stats_empty(self):
        from core.hybrid_workflow import HybridWorkflow
        stats = HybridWorkflow().get_stats()
        assert stats["auto_approve_rate"] == "N/A"

    def test_get_stats_with_data(self):
        from core.hybrid_workflow import HybridWorkflow, WorkflowMode
        hw = HybridWorkflow(mode=WorkflowMode.OFF)
        for _ in range(4):
            hw.execute("+x", lambda: None)
        stats = hw.get_stats()
        assert stats["auto_approve_rate"] == "100.0%"
        assert stats["total_tasks"] == 4


# ─── VerificationGate ─────────────────────────────────────────────────────────

class TestGate:
    def test_auto_pass(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", auto_pass=True)
        assert g.check({}) is True
        assert g.status == GateStatus.PASSED

    def test_validator_passes(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", validator=lambda ctx: True)
        assert g.check({"x": 1}) is True
        assert g.status == GateStatus.PASSED

    def test_validator_fails(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", validator=lambda ctx: False)
        assert g.check({}) is False
        assert g.status == GateStatus.FAILED

    def test_validator_exception_fails(self):
        from core.verification_gate import Gate, GateStatus
        def bad(_): raise ValueError("oops")
        g = Gate("test", validator=bad)
        assert g.check({}) is False
        assert g.status == GateStatus.FAILED
        assert "oops" in g.evidence["error"]

    def test_required_output_found(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", required_output="result")
        assert g.check({"result": "ok"}) is True
        assert g.status == GateStatus.PASSED

    def test_required_output_not_found(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", required_output="result")
        assert g.check({}) is False
        assert g.status == GateStatus.NOT_REACHED

    def test_no_validator_no_required_output_returns_false(self):
        from core.verification_gate import Gate
        g = Gate("test")
        assert g.check({}) is False

    def test_bypass(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test")
        g.bypass("testing")
        assert g.status == GateStatus.BYPASSED
        assert g.evidence["bypass_reason"] == "testing"

    def test_reset(self):
        from core.verification_gate import Gate, GateStatus
        g = Gate("test", auto_pass=True)
        g.check({})
        g.reset()
        assert g.status == GateStatus.NOT_REACHED
        assert g.verified_at is None
        assert g.evidence is None


class TestVerificationGates:
    def test_register_gate(self):
        from core.verification_gate import VerificationGates, Gate
        vg = VerificationGates()
        vg.register_gate("g1", Gate("G1", auto_pass=True))
        assert "g1" in vg.gates

    def test_register_default_gates_all(self):
        from core.verification_gate import VerificationGates
        vg = VerificationGates()
        vg.register_default_gates()
        assert len(vg.gates) == 6

    def test_register_default_gates_subset(self):
        from core.verification_gate import VerificationGates
        vg = VerificationGates()
        vg.register_default_gates(["task_created", "completed"])
        assert len(vg.gates) == 2

    def test_check_gate_found(self):
        from core.verification_gate import VerificationGates, Gate
        vg = VerificationGates()
        vg.register_gate("g", Gate("G", auto_pass=True))
        assert vg.check_gate("g", {}) is True

    def test_check_gate_not_found(self):
        from core.verification_gate import VerificationGates
        assert VerificationGates().check_gate("missing", {}) is False

    def test_execute_sequence(self):
        from core.verification_gate import VerificationGates, Gate
        vg = VerificationGates()
        vg.register_gate("a", Gate("A", auto_pass=True))
        vg.register_gate("b", Gate("B", auto_pass=True))
        vg.gate_sequence = ["a", "b"]
        results = vg.execute_sequence({})
        assert results["a"] is True
        assert results["b"] is True

    def test_get_status(self):
        from core.verification_gate import VerificationGates, Gate
        vg = VerificationGates()
        vg.register_gate("a", Gate("A", auto_pass=True))
        vg.check_gate("a", {})
        status = vg.get_status()
        assert status["a"]["status"] == "passed"

    def test_get_passed_failed_counts(self):
        from core.verification_gate import VerificationGates, Gate
        vg = VerificationGates()
        vg.register_gate("p", Gate("P", auto_pass=True))
        vg.register_gate("f", Gate("F", validator=lambda _: False))
        vg.check_gate("p", {})
        vg.check_gate("f", {})
        assert vg.get_passed_count() == 1
        assert vg.get_failed_count() == 1

    def test_reset_all(self):
        from core.verification_gate import VerificationGates, Gate, GateStatus
        vg = VerificationGates()
        vg.register_gate("a", Gate("A", auto_pass=True))
        vg.check_gate("a", {})
        vg.reset_all()
        assert vg.gates["a"].status == GateStatus.NOT_REACHED


class TestHITLAndAutonomousGates:
    def test_hitl_gate_sequence(self):
        from core.verification_gate import HITLGates
        g = HITLGates()
        assert "human_approved" in g.gate_sequence
        assert len(g.gates) == 4

    def test_autonomous_gates(self):
        from core.verification_gate import AutonomousGates
        g = AutonomousGates()
        assert "agent_assigned" in g.gate_sequence
        assert "quality_check" in g.gate_sequence


# ─── SessionsSpawnLogger ───────────────────────────────────────────────────────

class TestSessionsSpawnLogger:
    def test_log_spawn_creates_entry(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        entry = logger.log_spawn("developer", "FR-01 implement", "sess-001")
        assert entry["role"] == "developer"
        assert entry["session_id"] == "sess-001"

    def test_log_spawn_appends_to_file(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        logger.log_spawn("dev", "task1", "s1")
        logger.log_spawn("rev", "task2", "s2")
        entries = logger._read_entries()
        assert len(entries) == 2

    def test_log_spawn_with_confidence(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        entry = logger.log_spawn("dev", "task", "s1", confidence=8)
        assert entry["confidence"] == 8

    def test_log_spawn_with_status(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        entry = logger.log_spawn("dev", "task", "s1", status="COMPLETED")
        assert entry["status"] == "COMPLETED"

    def test_log_spawn_extra_kwargs(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        entry = logger.log_spawn("dev", "task", "s1", phase=3)
        assert entry["phase"] == 3

    def test_log_update_existing_entry(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        logger.log_spawn("dev", "task", "sess-001", status="PENDING")
        updated = logger.log_update("sess-001", status="COMPLETED")
        assert updated is not None
        assert updated["status"] == "COMPLETED"

    def test_log_update_missing_session_returns_none(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        assert logger.log_update("nonexistent") is None

    def test_validate_empty_log(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        result = logger.validate()
        assert result["valid"] is True
        assert result["count"] == 0

    def test_validate_valid_entries(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        logger.log_spawn("dev", "task", "s1")
        result = logger.validate()
        assert result["valid"] is True
        assert result["count"] == 1

    def test_validate_detects_missing_fields(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        log_file = tmp_path / ".methodology" / "sessions_spawn.log"
        log_file.write_text(json.dumps({"task": "no role or session_id"}) + "\n")
        result = logger.validate()
        assert result["valid"] is False

    def test_get_summary_structure(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        logger.log_spawn("dev", "FR-01 task", "s1")
        s = logger.get_summary()
        assert "total_entries" in s
        assert "role_counts" in s
        assert "status_counts" in s

    def test_read_entries_empty_file(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        log_file = tmp_path / ".methodology" / "sessions_spawn.log"
        log_file.write_text("")
        assert logger._read_entries() == []

    def test_read_entries_skips_invalid_json(self, tmp_path):
        from core.sessions_spawn_logger import SessionsSpawnLogger
        logger = SessionsSpawnLogger(tmp_path)
        log_file = tmp_path / ".methodology" / "sessions_spawn.log"
        log_file.write_text('{"valid": true}\nbad json\n{"also": "valid"}\n')
        entries = logger._read_entries()
        assert len(entries) == 2

    def test_log_spawn_event_function(self, tmp_path):
        from core.sessions_spawn_logger import log_spawn_event
        entry = log_spawn_event(tmp_path, role="dev", task="FR-01", session_id="s1")
        assert entry["role"] == "dev"


# ─── PhaseHooks ───────────────────────────────────────────────────────────────

class TestPhaseHooks:
    def _hooks(self, tmp_path, phase=3):
        from core.phase_hooks import PhaseHooks
        return PhaseHooks(str(tmp_path), phase=phase)

    def _write_state(self, tmp_path, state="ACTIVE", current_phase=2):
        methodology = tmp_path / ".methodology"
        methodology.mkdir(exist_ok=True)
        state_file = methodology / "state.json"
        state_file.write_text(json.dumps({"state": state, "current_phase": current_phase}))
        return state_file

    def test_preflight_fsm_no_state_file(self, tmp_path):
        h = self._hooks(tmp_path)
        result = h.preflight_fsm_check()
        assert result["passed"] is False
        assert "UNKNOWN" in result["state"]

    def test_preflight_fsm_active_state_passes(self, tmp_path):
        self._write_state(tmp_path, state="ACTIVE", current_phase=2)
        h = self._hooks(tmp_path)
        result = h.preflight_fsm_check()
        assert result["passed"] is True

    def test_preflight_fsm_freeze_blocks(self, tmp_path):
        self._write_state(tmp_path, state="FREEZE", current_phase=2)
        h = self._hooks(tmp_path)
        result = h.preflight_fsm_check()
        assert result["passed"] is False

    def test_preflight_fsm_paused_blocks(self, tmp_path):
        self._write_state(tmp_path, state="PAUSED")
        h = self._hooks(tmp_path)
        result = h.preflight_fsm_check()
        assert result["passed"] is False

    def test_preflight_fsm_backward_phase_blocks(self, tmp_path):
        self._write_state(tmp_path, state="ACTIVE", current_phase=5)
        h = self._hooks(tmp_path, phase=3)   # trying to go to 3 when at 5
        result = h.preflight_fsm_check()
        assert result["passed"] is False

    def test_preflight_tool_registry_import_error(self, tmp_path):
        h = self._hooks(tmp_path)
        with patch.dict("sys.modules", {"tool_registry": None}):
            result = h.preflight_tool_registry()
        assert result["passed"] is True   # skipped = True

    def test_preflight_constitution_exception_fails(self, tmp_path):
        h = self._hooks(tmp_path)
        with patch.dict("sys.modules", {"quality_gate.constitution": None}):
            result = h.preflight_constitution()
        assert result["passed"] is False

    def test_monitoring_before_dev_appends_event(self, tmp_path):
        h = self._hooks(tmp_path)
        h.monitoring_before_dev("FR-01")
        assert len(h.monitoring_events) == 1
        assert h.monitoring_events[0]["type"] == "before_dev"

    def test_monitoring_after_dev_no_result(self, tmp_path):
        h = self._hooks(tmp_path)
        h.monitoring_after_dev("FR-01", result=None)
        assert h.fr_results[-1]["dev_status"] == "unknown"

    def test_monitoring_after_dev_with_result(self, tmp_path):
        h = self._hooks(tmp_path)
        res = MagicMock(status="DONE", confidence=8)
        h.monitoring_after_dev("FR-01", result=res)
        assert h.fr_results[-1]["dev_status"] == "DONE"
        assert h.fr_results[-1]["dev_confidence"] == 8

    def test_monitoring_before_rev(self, tmp_path):
        h = self._hooks(tmp_path)
        h.monitoring_before_rev("FR-01")
        assert h.monitoring_events[-1]["type"] == "before_rev"

    def test_monitoring_after_rev_no_result(self, tmp_path):
        h = self._hooks(tmp_path)
        h.monitoring_after_dev("FR-01")  # populate fr_results first
        h.monitoring_after_rev("FR-01", result=None)
        assert "rev_status" in h.fr_results[-1]

    def test_monitoring_hr12_below_max(self, tmp_path):
        h = self._hooks(tmp_path)
        assert h.monitoring_hr12_check("FR-01", iteration=3, max_iterations=5) is True

    def test_monitoring_hr12_at_max(self, tmp_path):
        h = self._hooks(tmp_path)
        assert h.monitoring_hr12_check("FR-01", iteration=5, max_iterations=5) is False

    def test_postflight_update_state_no_success(self, tmp_path):
        h = self._hooks(tmp_path)
        result = h.postflight_update_state(success=False)
        assert result["updated"] is False

    def test_postflight_update_state_no_state_file(self, tmp_path):
        h = self._hooks(tmp_path, phase=3)
        result = h.postflight_update_state(success=True)
        assert result["updated"] is False

    def test_postflight_update_state_advances_phase(self, tmp_path):
        self._write_state(tmp_path, state="ACTIVE", current_phase=2)
        h = self._hooks(tmp_path, phase=3)
        result = h.postflight_update_state(success=True)
        assert result["updated"] is True
        assert result["new_phase"] == 3

    def test_postflight_update_state_no_advance_same_phase(self, tmp_path):
        self._write_state(tmp_path, state="ACTIVE", current_phase=3)
        h = self._hooks(tmp_path, phase=3)
        result = h.postflight_update_state(success=True)
        assert result["updated"] is False

    def test_postflight_summary_empty(self, tmp_path):
        h = self._hooks(tmp_path)
        s = h.postflight_summary()
        assert s["total_frs"] == 0
        assert s["approved"] == 0

    def test_postflight_summary_with_frs(self, tmp_path):
        h = self._hooks(tmp_path)
        h.fr_results = [{"fr_id": "FR-01", "review_status": "APPROVE"}]
        s = h.postflight_summary()
        assert s["approved"] == 1

    def test_add_fr_result(self, tmp_path):
        h = self._hooks(tmp_path)
        dev = MagicMock(status="DONE", confidence=9)
        rev = MagicMock(status="OK", review_status="APPROVE", confidence=8)
        h.add_fr_result("FR-01", dev, rev)
        assert h.fr_results[-1]["fr_id"] == "FR-01"
        assert h.fr_results[-1]["review_status"] == "APPROVE"


# ─── AgentProofHook ────────────────────────────────────────────────────────────

class TestAgentProofHook:
    def _hook(self, tmp_path):
        from enforcement.agent_proof_hook import AgentProofHook
        # Create fake .git/hooks dir
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        return AgentProofHook(str(tmp_path))

    def test_init_sets_paths(self, tmp_path):
        h = self._hook(tmp_path)
        assert h.project_root == tmp_path
        assert "pre-commit" in str(h.hook_path)
        assert "agent_hook_core" in str(h.core_path)

    def test_generate_core_module_contains_enforce(self, tmp_path):
        h = self._hook(tmp_path)
        content = h._generate_core_module()
        assert "def enforce()" in content

    def test_install_creates_files(self, tmp_path):
        h = self._hook(tmp_path)
        with patch.object(h, '_try_immutable'):
            h.install()
        assert h.hook_path.exists()
        assert h.core_path.exists()

    def test_install_hook_wrapper_writes_template(self, tmp_path):
        h = self._hook(tmp_path)
        h._install_hook_wrapper(force=True)
        content = h.hook_path.read_text()
        assert "Agent-Proof Hook" in content
        assert h.hook_path.stat().st_mode & 0o111  # executable

    def test_install_hook_wrapper_backs_up_existing(self, tmp_path):
        h = self._hook(tmp_path)
        h.hook_path.write_text("old hook")
        h._install_hook_wrapper(force=False)
        backup = h.hook_path.with_suffix(".backup")
        assert backup.exists()

    def test_verify_missing_hook_fails(self, tmp_path):
        h = self._hook(tmp_path)
        assert h.verify() is False

    def test_verify_missing_core_fails(self, tmp_path):
        h = self._hook(tmp_path)
        h.hook_path.write_text("Agent-Proof Hook content")
        assert h.verify() is False

    def test_verify_tampered_hook_fails(self, tmp_path):
        h = self._hook(tmp_path)
        h.hook_path.write_text("tampered content")
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        h.core_path.write_text("def enforce(): pass")
        assert h.verify() is False

    def test_verify_tampered_core_fails(self, tmp_path):
        h = self._hook(tmp_path)
        h.hook_path.write_text("Agent-Proof Hook legit wrapper")
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        h.core_path.write_text("no enforce function here")
        assert h.verify() is False

    def test_verify_passes_with_valid_files(self, tmp_path):
        h = self._hook(tmp_path)
        with patch.object(h, '_try_immutable'):
            h.install()
        assert h.verify() is True

    def test_uninstall_removes_files(self, tmp_path):
        h = self._hook(tmp_path)
        with patch.object(h, '_try_immutable'):
            h.install()
        with patch("subprocess.run"):
            h.uninstall()
        assert not h.core_path.exists()

    def test_try_immutable_does_not_raise(self, tmp_path):
        h = self._hook(tmp_path)
        h._try_immutable()   # should silently ignore failures

    def test_try_immutable_with_lsattr(self, tmp_path):
        h = self._hook(tmp_path)
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run") as mock_run:
                h._try_immutable()
                assert mock_run.call_count >= 2  # chattr + lsattr

    def test_uninstall_chattr_exception_handled(self, tmp_path):
        h = self._hook(tmp_path)
        with patch.object(h, '_try_immutable'):
            h.install()
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            h.uninstall()
        assert not h.core_path.exists()

    def test_uninstall_restores_backup(self, tmp_path):
        h = self._hook(tmp_path)
        h.hook_path.write_text("original hook")
        backup = h.hook_path.with_suffix(".backup")
        backup.write_text("backup content")
        h.core_path.parent.mkdir(parents=True, exist_ok=True)
        h.core_path.write_text("def enforce(): pass")
        with patch("subprocess.run"):
            h.uninstall()
        assert h.hook_path.read_text() == "backup content"
        assert not backup.exists()


# ─── IntegratedStagePassGenerator (stage_pass_generator.py) ───────────────────

class TestIntegratedStagePassGenerator:
    """Tests for stage_pass_generator.py — heavy mocking."""

    def _make_gen(self, tmp_path, phase=3):
        mock_enforcer_cls = MagicMock()
        mock_cv_cls = MagicMock()
        with patch("core.quality_gate.stage_pass_generator.FrameworkEnforcer", mock_enforcer_cls), \
             patch("core.quality_gate.stage_pass_generator.ClaimsVerifier", mock_cv_cls):
            from core.quality_gate import stage_pass_generator
            # Fresh import each time through patches
            gen = stage_pass_generator.IntegratedStagePassGenerator(str(tmp_path), phase)
        return gen, mock_enforcer_cls, mock_cv_cls

    def _patched_gen(self, tmp_path, phase=3):
        """Return gen with pre-mocked enforcer and claims_verifier attributes."""
        with patch("core.quality_gate.stage_pass_generator.FrameworkEnforcer"), \
             patch("core.quality_gate.stage_pass_generator.ClaimsVerifier"):
            from core.quality_gate.stage_pass_generator import IntegratedStagePassGenerator
            gen = IntegratedStagePassGenerator(str(tmp_path), phase)
        return gen

    def test_init_sets_phase(self, tmp_path):
        gen = self._patched_gen(tmp_path, phase=3)
        assert gen.phase == 3

    def test_init_results_structure(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        assert "five_w1h_results" in gen.results
        assert "framework_results" in gen.results
        assert gen.results["phase"] == 3

    def test_run_step2b_no_log_file(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        result = gen.run_step2b_confidence_format()
        assert result["passed"] is True
        assert "log not found" in result["message"]

    def test_run_step2b_all_valid_confidence(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        log = tmp_path / "sessions_spawn.log"
        entries = [{"session_id": "s1", "confidence": 7}, {"session_id": "s2", "confidence": 5}]
        log.write_text("\n".join(json.dumps(e) for e in entries))
        result = gen.run_step2b_confidence_format()
        assert result["passed"] is True

    def test_run_step2b_invalid_confidence(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        log = tmp_path / "sessions_spawn.log"
        log.write_text(json.dumps({"session_id": "s1", "confidence": 99}))
        result = gen.run_step2b_confidence_format()
        assert result["passed"] is False
        assert len(result["invalid_entries"]) == 1

    def test_run_step2b_no_confidence_key_passes(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        log = tmp_path / "sessions_spawn.log"
        log.write_text(json.dumps({"session_id": "s1"}))
        result = gen.run_step2b_confidence_format()
        assert result["passed"] is True

    def test_run_step2b_parse_error_passes(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        log = tmp_path / "sessions_spawn.log"
        log.write_text("not-json-at-all")
        result = gen.run_step2b_confidence_format()
        assert result["passed"] is True

    def test_run_step4_all_passed(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["framework_results"]["BLOCK"] = {"passed": True}
        gen.results["session_log_results"] = {"passed": True}
        gen.results["test_evidence"] = {"pytest_passed": True, "coverage_passed": True}
        score = gen.run_step4_confidence()
        assert score == 100

    def test_run_step4_all_failed(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["framework_results"]["BLOCK"] = {"passed": False}
        gen.results["session_log_results"] = {"passed": False}
        gen.results["test_evidence"] = {"pytest_passed": False, "coverage_passed": False}
        score = gen.run_step4_confidence()
        assert score == 10   # only partial pytest credit

    def test_run_step4_partial(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["framework_results"]["BLOCK"] = {"passed": True}
        gen.results["session_log_results"] = {"passed": False}
        gen.results["test_evidence"] = {"pytest_passed": True, "coverage_passed": False}
        score = gen.run_step4_confidence()
        assert score == 60   # 40 (BLOCK) + 0 (session) + 20 (pytest) + 0 (coverage)

    def test_run_step3_mocks_subprocess(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        with patch("core.quality_gate.stage_pass_generator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="passed", stderr="")
            result = gen.run_step3_pytest_evidence()
        assert "pytest_passed" in result

    def test_run_step3_subprocess_exception(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        with patch("core.quality_gate.stage_pass_generator.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("timeout")
            result = gen.run_step3_pytest_evidence()
        assert "pytest_error" in result

    def test_generate_markdown_returns_string(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["confidence_score"] = 85
        gen.results["framework_results"]["BLOCK"] = {"passed": True, "violations": []}
        gen.results["framework_results"]["CONSTITUTION"] = {"score": 90.0, "passed": True}
        gen.results["session_log_results"] = {"passed": True}
        gen.results["test_evidence"] = {"pytest_passed": True, "coverage_passed": True}
        md = gen.generate_markdown()
        assert f"Phase {gen.phase} STAGE_PASS" in md
        assert "Agent A Self-Assessment" in md

    def test_generate_markdown_score_badges(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["confidence_score"] = 97
        gen.results["framework_results"] = {"BLOCK": {"passed": True, "violations": []},
                                             "CONSTITUTION": {"score": 90.0, "passed": True}}
        gen.results["session_log_results"] = {"passed": True}
        gen.results["test_evidence"] = {}
        md = gen.generate_markdown()
        assert "97/10" in md   # score embedded in confidence line

    def test_generate_markdown_constitution_fail_blocked(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["confidence_score"] = 50
        gen.results["framework_results"] = {"BLOCK": {"passed": False, "violations": [("err", "fix")]},
                                             "CONSTITUTION": {"score": 60.0, "passed": False}}
        gen.results["session_log_results"] = {"passed": False}
        gen.results["test_evidence"] = {}
        md = gen.generate_markdown()
        assert "BLOCKED" in md

    def test_git_push_subprocess_failure(self, tmp_path):
        import subprocess as _sp
        gen = self._patched_gen(tmp_path)
        err = _sp.CalledProcessError(1, "git", stderr=b"error")
        with patch("core.quality_gate.stage_pass_generator.subprocess.run") as mock_run:
            mock_run.side_effect = err
            result = gen.git_push("# STAGE_PASS content")
        assert result == ""

    def test_run_step1_calls_enforcer(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        mock_result = MagicMock(passed=True, violations=[], block_checks={})
        gen.enforcer.run.return_value = mock_result
        gen.enforcer.check_constitution.return_value = {"score": 90.0, "passed": True}
        passed = gen.run_step1_5w1h_scan()
        assert passed is True
        gen.enforcer.run.assert_called_once_with(level="BLOCK")

    def test_run_step1_failed_enforcer(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        mock_result = MagicMock(passed=False, violations=[("violation msg", "fix it")], block_checks={})
        gen.enforcer.run.return_value = mock_result
        gen.enforcer.check_constitution.return_value = {"score": 50.0, "passed": False}
        passed = gen.run_step1_5w1h_scan()
        assert passed is False

    def test_run_step2_session_log_passed(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        mock_log_result = MagicMock(passed=True, message="ok", details={})
        gen.claims_verifier.verify_sessions_spawn_log.return_value = mock_log_result
        passed = gen.run_step2_session_log()
        assert passed is True

    def test_run_step2_session_log_failed(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        mock_log_result = MagicMock(passed=False, message="missing", details={})
        gen.claims_verifier.verify_sessions_spawn_log.return_value = mock_log_result
        passed = gen.run_step2_session_log()
        assert passed is False

    def test_generate_markdown_with_violations(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["confidence_score"] = 60
        gen.results["framework_results"]["BLOCK"] = {
            "passed": False,
            "violations": [("Missing SRS.md", "generate SRS"), ("Low coverage", "add tests")],
        }
        gen.results["framework_results"]["CONSTITUTION"] = {"score": 55.0, "passed": False}
        gen.results["session_log_results"] = {"passed": False}
        gen.results["test_evidence"] = {}
        md = gen.generate_markdown()
        assert "Missing SRS.md" in md
        assert "Low coverage" in md
        assert "BLOCKED" in md

    def test_generate_markdown_all_sections_present(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["confidence_score"] = 90
        gen.results["framework_results"]["BLOCK"] = {"passed": True, "violations": []}
        gen.results["framework_results"]["CONSTITUTION"] = {"score": 95.0, "passed": True}
        gen.results["session_log_results"] = {"passed": True}
        gen.results["test_evidence"] = {"pytest_passed": True, "coverage_passed": True}
        md = gen.generate_markdown()
        assert "STAGE_PASS" in md
        assert "5W1H Compliance Check" in md
        assert "Agent A Self-Assessment" in md
        assert "Agent B Review" in md
        assert "SIGN-OFF" in md
        assert "Appendix: Actual Tool Results" in md

    def test_log_to_development_log_writes(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.results["framework_results"]["CONSTITUTION"] = {"score": 85.0}
        gen.results["framework_results"]["BLOCK"] = {"passed": True, "violations": []}
        gen.results["confidence_score"] = 90
        gen._log_to_development_log()
        log = tmp_path / "DEVELOPMENT_LOG.md"
        assert log.exists()
        content = log.read_text()
        assert "STAGE_PASS" in content

    def test_run_step5_no_trace_file(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        result = gen.run_step5_traceability()
        assert result is True  # no trace file → not blocking

    def test_run_step5_with_trace_file(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        (tmp_path / "traceability_report.json").write_text(
            '{"overall_completeness": "95%", "srs_coverage": 100, "code_coverage": 90, "test_coverage": 95}'
        )
        with patch("core.quality_gate.stage_pass_generator.RequirementTraceability", create=True) as mock_rt:
            mock_rt.load.return_value.verify_completeness.return_value = {
                "overall_completeness": "95%",
                "srs_coverage": 100, "code_coverage": 90, "test_coverage": 95,
            }
            result = gen.run_step5_traceability()
        assert result is True

    def test_run_step6_script_not_found(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        result = gen.run_step6_sab_generation()
        assert result is True  # script not found → not blocking

    def test_run_returns_true_when_score_high(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.run_step1_5w1h_scan = MagicMock(return_value=True)
        gen.run_step2_session_log = MagicMock(return_value=True)
        gen.run_step2b_confidence_format = MagicMock(return_value={"passed": True})
        gen.run_step3_pytest_evidence = MagicMock(return_value={"pytest_passed": True})
        gen.run_step4_confidence = MagicMock(return_value=85)
        gen.run_step5_traceability = MagicMock(return_value=True)
        gen.generate_markdown = MagicMock(return_value="# STAGE_PASS")
        gen.git_push = MagicMock(return_value="abc1234")
        gen._log_to_development_log = MagicMock()
        assert gen.run() is True

    def test_run_returns_false_when_score_low(self, tmp_path):
        gen = self._patched_gen(tmp_path)
        gen.run_step1_5w1h_scan = MagicMock(return_value=True)
        gen.run_step2_session_log = MagicMock(return_value=True)
        gen.run_step2b_confidence_format = MagicMock(return_value={"passed": True})
        gen.run_step3_pytest_evidence = MagicMock(return_value={"pytest_passed": False})
        gen.run_step4_confidence = MagicMock(return_value=55)
        gen.run_step5_traceability = MagicMock(return_value=True)
        gen.generate_markdown = MagicMock(return_value="# STAGE_PASS")
        gen.git_push = MagicMock(return_value="abc1234")
        gen._log_to_development_log = MagicMock()
        assert gen.run() is False


# ─── SubagentIsolator ─────────────────────────────────────────────────────────

class TestArtifactSpec:
    def test_exists_false(self):
        from core.subagent_isolator import ArtifactSpec
        a = ArtifactSpec(path="/nonexistent/file.py", role="input")
        assert a.exists() is False

    def test_exists_true(self, tmp_path):
        from core.subagent_isolator import ArtifactSpec
        f = tmp_path / "spec.md"
        f.write_text("content")
        a = ArtifactSpec(path=str(f), role="input")
        assert a.exists() is True

    def test_defaults(self):
        from core.subagent_isolator import ArtifactSpec
        a = ArtifactSpec(path="x.md", role="output")
        assert a.required is True
        assert a.description == ""


class TestSubagentContext:
    def test_isolation_id_auto_generated(self):
        from core.subagent_isolator import SubagentContext
        ctx = SubagentContext(task="do it", role="dev")
        assert len(ctx.isolation_id) == 16

    def test_isolation_id_stable(self):
        from core.subagent_isolator import SubagentContext
        ctx1 = SubagentContext(task="do it", role="dev")
        ctx2 = SubagentContext(task="do it", role="dev")
        assert ctx1.isolation_id == ctx2.isolation_id

    def test_to_spawn_config_structure(self):
        from core.subagent_isolator import SubagentContext, ArtifactSpec
        ctx = SubagentContext(
            task="impl", role="dev",
            artifacts=[ArtifactSpec("x.md", role="input")],
            persona_prompt="be precise",
            metadata={"phase": 3},
        )
        cfg = ctx.to_spawn_config()
        assert cfg["task"] == "impl"
        assert cfg["role"] == "dev"
        assert cfg["persona_prompt"] == "be precise"
        assert len(cfg["artifact_paths"]) == 1
        assert cfg["artifact_paths"][0]["path"] == "x.md"

    def test_to_spawn_config_messages_fresh(self):
        from core.subagent_isolator import SubagentContext
        ctx = SubagentContext(task="t", role="r", messages=[])
        cfg = ctx.to_spawn_config()
        assert cfg["messages"] == []

    def test_to_spawn_config_metadata_deep_copy(self):
        from core.subagent_isolator import SubagentContext
        meta = {"key": [1, 2, 3]}
        ctx = SubagentContext(task="t", role="r", metadata=meta)
        cfg = ctx.to_spawn_config()
        cfg["metadata"]["key"].append(99)
        assert meta["key"] == [1, 2, 3]  # original unchanged


class TestSubagentIsolator:
    def test_create_context_returns_fresh(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        ctx = iso.create_context(task="build", role="dev")
        assert ctx.messages == []
        assert ctx.task == "build"

    def test_create_context_registers_in_active(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        ctx = iso.create_context(task="t", role="r")
        assert iso.get_context(ctx.isolation_id) is ctx

    def test_validate_passes_when_inputs_exist(self, tmp_path):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec
        f = tmp_path / "input.md"
        f.write_text("spec")
        iso = SubagentIsolator()
        ctx = iso.create_context(
            task="t", role="r",
            artifacts=[ArtifactSpec(str(f), role="input", required=True)]
        )
        iso.validate(ctx)   # should not raise

    def test_validate_raises_when_inputs_missing(self):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec, ArtifactValidationError
        iso = SubagentIsolator()
        ctx = iso.create_context(
            task="t", role="r",
            artifacts=[ArtifactSpec("/nonexistent/file.py", role="input", required=True)]
        )
        with pytest.raises(ArtifactValidationError, match="Missing required"):
            iso.validate(ctx)

    def test_validate_skips_optional_missing(self):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec
        iso = SubagentIsolator()
        ctx = iso.create_context(
            task="t", role="r",
            artifacts=[ArtifactSpec("/nonexistent.py", role="input", required=False)]
        )
        iso.validate(ctx)  # should not raise

    def test_validate_outputs_complete(self, tmp_path):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec
        f = tmp_path / "out.py"
        f.write_text("code")
        iso = SubagentIsolator()
        ctx = iso.create_context(
            task="t", role="r",
            artifacts=[ArtifactSpec(str(f), role="output", required=True)]
        )
        result = iso.validate_outputs(ctx)
        assert result["complete"] is True
        assert str(f) in result["produced"]

    def test_validate_outputs_missing(self):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec
        iso = SubagentIsolator()
        ctx = iso.create_context(
            task="t", role="r",
            artifacts=[ArtifactSpec("/nonexistent.py", role="output", required=True)]
        )
        result = iso.validate_outputs(ctx)
        assert result["complete"] is False
        assert "/nonexistent.py" in result["missing"]

    def test_verify_isolation_passes_empty_messages(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        ctx = iso.create_context(task="t", role="r")
        iso.verify_isolation(ctx)  # should not raise

    def test_verify_isolation_raises_on_non_empty(self):
        from core.subagent_isolator import SubagentIsolator, IsolationViolationError
        iso = SubagentIsolator()
        ctx = iso.create_context(task="t", role="r")
        ctx.messages.append({"role": "user", "content": "stale"})
        with pytest.raises(IsolationViolationError, match="isolation violated"):
            iso.verify_isolation(ctx)

    def test_spawn_no_artifacts_returns_config(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        cfg = iso.spawn(task="build API", role="developer", validate=False)
        assert cfg["task"] == "build API"
        assert cfg["role"] == "developer"
        assert cfg["artifact_paths"] == []

    def test_spawn_with_validate_true_and_missing_raises(self):
        from core.subagent_isolator import SubagentIsolator, ArtifactSpec, ArtifactValidationError
        iso = SubagentIsolator()
        with pytest.raises(ArtifactValidationError):
            iso.spawn(
                task="t", role="r",
                artifacts=[ArtifactSpec("/nonexistent.py", role="input", required=True)],
                validate=True
            )

    def test_release_removes_context(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        ctx = iso.create_context(task="t", role="r")
        iso.release(ctx.isolation_id)
        assert iso.get_context(ctx.isolation_id) is None

    def test_active_count(self):
        from core.subagent_isolator import SubagentIsolator
        iso = SubagentIsolator()
        assert iso.active_count() == 0
        ctx = iso.create_context(task="t", role="r")
        assert iso.active_count() == 1
        iso.release(ctx.isolation_id)
        assert iso.active_count() == 0


class TestCreateIsolatedSpawn:
    def test_no_inputs_no_outputs(self):
        from core.subagent_isolator import create_isolated_spawn
        cfg = create_isolated_spawn(task="do", role="dev")
        assert cfg["task"] == "do"
        assert cfg["artifact_paths"] == []

    def test_with_existing_input(self, tmp_path):
        from core.subagent_isolator import create_isolated_spawn
        f = tmp_path / "spec.md"
        f.write_text("spec")
        cfg = create_isolated_spawn(task="do", role="dev", input_paths=[str(f)])
        assert len(cfg["artifact_paths"]) == 1
        assert cfg["artifact_paths"][0]["role"] == "input"

    def test_with_output_paths(self):
        from core.subagent_isolator import create_isolated_spawn
        cfg = create_isolated_spawn(
            task="do", role="dev",
            output_paths=["/out/result.py"]
        )
        assert any(a["role"] == "output" for a in cfg["artifact_paths"])


# ─── EffortTracker ─────────────────────────────────────────────────────────────

class TestEffortTracker:
    def test_init_creates_db(self, tmp_path):
        from harness.effort_tracker import EffortTracker
        db = str(tmp_path / "effort.db")
        t = EffortTracker(db_path=db)
        assert (tmp_path / "effort.db").exists()

    def test_record_and_summary(self, tmp_path):
        from harness.effort_tracker import EffortTracker, EffortRecord
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        t.record(EffortRecord(phase=3, agent_id="dev", operation="gate_run",
                               duration_s=1.5, token_in=100, token_out=50))
        s = t.summary()
        assert s["total_operations"] == 1
        assert s["total_duration_s"] == pytest.approx(1.5)
        assert s["total_tokens"] == 150

    def test_summary_empty_db(self, tmp_path):
        from harness.effort_tracker import EffortTracker
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        s = t.summary()
        assert s["total_operations"] == 0

    def test_summary_with_phase_filter(self, tmp_path):
        from harness.effort_tracker import EffortTracker, EffortRecord
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        t.record(EffortRecord(phase=2, agent_id="dev", operation="gate_run", duration_s=1.0))
        t.record(EffortRecord(phase=3, agent_id="dev", operation="tier1_eval", duration_s=2.0))
        s = t.summary(phase=3)
        assert "tier1_eval" in s
        assert "gate_run" not in s

    def test_query_phase_summary(self, tmp_path):
        from harness.effort_tracker import EffortTracker, EffortRecord
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        t.record(EffortRecord(phase=3, agent_id="dev", operation="review",
                               duration_s=3.0, token_in=200, token_out=100))
        s = t.query_phase_summary(3)
        assert "review" in s
        assert s["review"]["duration_s"] == pytest.approx(3.0)
        assert s["review"]["total_tokens"] == 300

    def test_query_gate_summary(self, tmp_path):
        from harness.effort_tracker import EffortTracker, EffortRecord
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        t.record(EffortRecord(phase=3, agent_id="dev", operation="gate_run",
                               duration_s=2.5, gate_num=1))
        t.record(EffortRecord(phase=3, agent_id="dev", operation="gate_run",
                               duration_s=1.5, gate_num=1))
        s = t.query_gate_summary(1)
        assert s["runs"] == 2
        assert s["total_duration_s"] == pytest.approx(4.0)

    def test_query_gate_summary_empty(self, tmp_path):
        from harness.effort_tracker import EffortTracker
        t = EffortTracker(db_path=str(tmp_path / "e.db"))
        s = t.query_gate_summary(99)
        assert s["runs"] == 0


# ─── ServerEnforcer ────────────────────────────────────────────────────────────

class TestServerEnforcer:
    def test_init_registers_four_checks(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        assert len(e.checks) == 4

    def test_enforce_all_returns_structure(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        results = e.enforce_all()
        assert "all_passed" in results
        assert "total" in results
        assert results["total"] == 4

    def test_check_constitution_exception_caught(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        with patch.dict("sys.modules", {"enforcement": None}):
            result = e._check_constitution()
        # ImportError caught → returns {"passed": False, ...}
        assert "passed" in result

    def test_check_policy_exception_caught(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        # ai_quality_gate not installed → exception caught
        result = e._check_quality_gate()
        assert result["passed"] is False

    def test_check_security_exception_caught(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        result = e._check_security()
        assert result["passed"] is False   # security_scanner not available

    def test_report_failure_prints(self, capsys):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        fake = {"failed": 1, "total": 2,
                "results": {
                    "constitution": {"passed": False, "error": "missing"},
                    "policy": {"passed": True}
                }}
        e.report_failure(fake)
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert "constitution" in out

    def test_on_git_hook_calls_framework_enforcer(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        mock_result = MagicMock(passed=True, violations=[])
        mock_enforcer = MagicMock()
        mock_enforcer.return_value.run.return_value = mock_result
        mock_module = MagicMock()
        mock_module.FrameworkEnforcer = mock_enforcer
        with patch.dict("sys.modules", {"enforcement.framework_enforcer": mock_module}):
            result = e.on_git_hook()
        assert result is True

    def test_on_git_hook_returns_false_on_failure(self):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        mock_result = MagicMock(passed=False, violations=[("bad msg", "fix it")])
        mock_enforcer = MagicMock()
        mock_enforcer.return_value.run.return_value = mock_result
        mock_module = MagicMock()
        mock_module.FrameworkEnforcer = mock_enforcer
        with patch.dict("sys.modules", {"enforcement.framework_enforcer": mock_module}):
            result = e.on_git_hook()
        assert result is False


# ─── ServerEnforcer main() + uncovered branches ────────────────────────────────

class TestServerEnforcerExtra:
    def test_report_failure_with_score(self, capsys):
        from enforcement.server_enforcer import ServerEnforcer
        e = ServerEnforcer()
        fake = {"failed": 1, "total": 2,
                "results": {
                    "quality-gate": {"passed": False, "score": 72.0},
                    "policy": {"passed": True}
                }}
        e.report_failure(fake)
        out = capsys.readouterr().out
        assert "72" in out

    def test_main_all_passed(self, capsys):
        from enforcement.server_enforcer import main
        mock_e = MagicMock()
        mock_e.enforce_all.return_value = {"all_passed": True, "total": 4,
                                            "passed": 4, "failed": 0, "results": {}}
        with patch("enforcement.server_enforcer.ServerEnforcer", return_value=mock_e), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_some_failed(self):
        from enforcement.server_enforcer import main
        mock_e = MagicMock()
        mock_e.enforce_all.return_value = {"all_passed": False, "total": 4,
                                            "passed": 2, "failed": 2, "results": {}}
        with patch("enforcement.server_enforcer.ServerEnforcer", return_value=mock_e), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


# ─── AgentProofHook main() ────────────────────────────────────────────────────

class TestAgentProofHookMain:
    def test_main_install_cmd(self, tmp_path):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py", "install"]):
            main()
        mock_hook.install.assert_called_once_with(force=True)

    def test_main_verify_cmd_pass(self, tmp_path):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        mock_hook.verify.return_value = True
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py", "verify"]):
            main()   # should not raise
        mock_hook.verify.assert_called_once()

    def test_main_verify_cmd_fail(self, tmp_path):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        mock_hook.verify.return_value = False
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py", "verify"]), \
             pytest.raises(SystemExit):
            main()

    def test_main_uninstall_cmd(self):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py", "uninstall"]):
            main()
        mock_hook.uninstall.assert_called_once()

    def test_main_unknown_cmd(self, capsys):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py", "badcmd"]):
            main()
        out = capsys.readouterr().out
        assert "Unknown" in out or "Available" in out

    def test_main_no_args_installs(self):
        from enforcement.agent_proof_hook import main
        mock_hook = MagicMock()
        with patch("enforcement.agent_proof_hook.AgentProofHook", return_value=mock_hook), \
             patch("sys.argv", ["hook.py"]):
            main()
        mock_hook.install.assert_called_once()
