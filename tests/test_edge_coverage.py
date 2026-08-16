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

    def test_preflight_fsm_allows_the_phase_just_closed(self, tmp_path):
        """current_phase == self.phase + 1 must pass: this is the pre-push
        hook (Fix B) retrospectively verifying the phase that advance-phase
        just closed, one phase behind the already-flipped current_phase —
        not a request to re-enter or redo that phase's work."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=4)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 5})
        )
        result = hooks.preflight_fsm_check()
        assert result["passed"] is True

    def test_preflight_fsm_two_phases_behind_still_blocks(self, tmp_path):
        """current_phase == self.phase + 2 must still block — the genuine
        backwards-navigation mistake this check exists to catch, distinct
        from the one-phase-behind retrospective-verification case above."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=3)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology/state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 5})
        )
        result = hooks.preflight_fsm_check()
        assert result["passed"] is False
        assert "Cannot go backwards" in result["message"]

    def test_preflight_fsm_no_state_file_p1_auto_init(self, tmp_path):
        """P1 auto-initializes state.json when missing (fresh project)."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        result = hooks.preflight_fsm_check()
        assert result["passed"] is True
        assert "Auto-initialized" in result["message"]
        assert (tmp_path / ".methodology" / "state.json").exists()

    def test_preflight_fsm_no_state_file_p2_fails(self, tmp_path):
        """P2+ must have state.json — missing is a hard failure."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=2)
        result = hooks.preflight_fsm_check()
        assert result["passed"] is False
        assert "not found" in result["message"]

    def test_preflight_constitution_import_error(self, tmp_path):
        from core.phase_hooks import PhaseHooks
        (tmp_path / "docs").mkdir()
        hooks = PhaseHooks(str(tmp_path), phase=5)
        with patch.dict("sys.modules", {"core.quality_gate.constitution": None}):
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

    # ── BVS preflight ────────────────────────────────────────────────────────
    def test_preflight_bvs_phase_order_passes_no_state(self, tmp_path):
        """BVS returns passed=True when state.json is absent."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=3)
        result = hooks.preflight_bvs_phase_order()
        assert result["passed"] is True

    def test_preflight_bvs_phase_order_detects_skip(self, tmp_path):
        """BVS detects phase-order violation (skipping prerequisite)."""
        from core.phase_hooks import PhaseHooks
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 1})
        )
        hooks = PhaseHooks(str(tmp_path), phase=3)
        result = hooks.preflight_bvs_phase_order()
        assert result["passed"] is False

    def test_preflight_bvs_phase_order_passes_valid(self, tmp_path):
        """BVS passes when prerequisites are satisfied."""
        from core.phase_hooks import PhaseHooks
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"state": "ACTIVE", "current_phase": 2})
        )
        hooks = PhaseHooks(str(tmp_path), phase=3)
        result = hooks.preflight_bvs_phase_order()
        assert result["passed"] is True

    def test_preflight_all_includes_bvs(self, tmp_path):
        """preflight_all() results include bvs_phase_order key."""
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(tmp_path), phase=1)
        # Mock heavier checks to keep test fast
        hooks.preflight_constitution = MagicMock(return_value={"passed": True})
        hooks.preflight_kill_switch = MagicMock(return_value={"passed": True})
        hooks.preflight_drift_detection = MagicMock(return_value={"passed": True})
        hooks.preflight_sab_check = MagicMock(return_value={"passed": True})
        hooks.preflight_tool_registry = MagicMock(return_value={"passed": True})
        hooks.preflight_traceability = MagicMock(return_value={"passed": True})
        hooks.preflight_previous_phase_artifacts = MagicMock(return_value={"passed": True})
        result = hooks.preflight_all()
        assert "bvs_phase_order" in result["details"]


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
        (tmp_path / "01-requirements").mkdir()
        st = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
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
        (tmp_path / "01-requirements").mkdir()
        st = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
        st.write_text("# Some doc\nStatus: Done\n## Update Log\n")
        checker = SpecTrackingChecker(str(tmp_path))
        result = checker.check_completeness()
        assert "Core Features table" in result["missing"]

    def test_run_enforcement_with_valid_spec(self, tmp_path):
        from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
        (tmp_path / "01-requirements").mkdir()
        st = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
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
        (tmp_path / "01-requirements").mkdir()
        st = tmp_path / "01-requirements" / "SPEC_TRACKING.md"
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
        from gap_detector.parser import ParsedSpec
        from gap_detector.scanner import ScannedCode, CodeFile

        spec = ParsedSpec(feature_items=spec_items or [])
        modules = [
            CodeFile(
                module_name="test", file_path="test.py",
                items=code_items or [],
            )
        ]
        code = ScannedCode(modules=modules)
        return GapDetector(spec, code)

    def test_detect_match_runtime_error_propagates(self):
        """Bug fix: only predictable schema/data errors are swallowed in
        _match_spec_to_code. Unexpected RuntimeError propagates so the caller
        can fail loud instead of silently producing false-PASS.
        """
        detector = self._make_detector()
        with patch.object(detector, "_match_spec_to_code", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                detector.detect()

    def test_detect_match_predictable_error_returns_empty(self, caplog):
        """Bug fix: KeyError/AttributeError/TypeError in _match_spec_to_code
        are still swallowed with a logged warning (legacy compatibility),
        but other exceptions propagate.
        """
        import logging
        detector = self._make_detector()
        with patch.object(detector, "_match_spec_to_code", side_effect=KeyError("bad-key")):
            with caplog.at_level(logging.WARNING, logger="gap_detector"):
                result = detector.detect()
        assert result == []
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("_match_spec_to_code" in m for m in warning_msgs)

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



    def test_check_pytest_not_found(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            passed, score, _ = verifier.check_pytest()
            assert passed is False

    def test_check_pytest_timeout(self, tmp_path):
        """A timed-out suite is reported as a timeout, not as failing tests.

        Round 25: check_pytest reads the shared measurement, so the timeout
        arrives as SuiteResult.returncode == 124 instead of a
        subprocess.TimeoutExpired raised inside the method.
        """
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        from core.quality_gate.test_suite_run import SuiteResult
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        timed_out = SuiteResult(
            passed=False, coverage=None, test_target="tests", cov_target="src",
            returncode=124, output="", ran=True,
            reason="test suite timed out after 300s",
        )
        with patch("core.quality_gate.test_suite_run.run_suite", return_value=timed_out):
            passed, score, details = verifier.check_pytest()
            assert passed is False
            assert "timed out" in details

    def test_verify_phase_lt_3(self, tmp_path, capsys):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=1)
        with patch.object(verifier, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_session_log", return_value=(True, 100.0, "ok")):
            result = verifier.verify()
        assert result["passed"] is True

    def test_verify_phase_gt_4(self, tmp_path, capsys):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        verifier = PhaseTruthVerifier(str(tmp_path), phase=6)
        with patch.object(verifier, "check_framework_block", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_previous_phase_artifacts", return_value=(True, 100.0, "ok")), \
             patch.object(verifier, "check_session_log", return_value=(True, 100.0, "ok")):
            result = verifier.verify()
        assert result["passed"] is True

    def test_check_framework_block_import_error(self, tmp_path):
        """CV-4: ImportError now raises InfraSkip (distinguishes infra
        unavailability from a check failure) — verify() handles it by
        renormalizing weights and emitting [SKIP] in output.
        """
        from core.quality_gate.phase_truth_verifier import (
            PhaseTruthVerifier,
            InfraSkip,
        )
        verifier = PhaseTruthVerifier(str(tmp_path), phase=3)
        # Force the import within check_framework_block to fail.
        import sys as _sys
        _sys.modules.pop("enforcement.framework_enforcer", None)
        with patch.dict("sys.modules", {"enforcement.framework_enforcer": None}):
            with pytest.raises(InfraSkip):
                verifier.check_framework_block()

    def test_get_manual_checklist_phase3_artifact_exists(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        (tmp_path / "03-development").mkdir()
        (tmp_path / "03-development" / "src").mkdir()
        (tmp_path / "03-development" / "tests").mkdir()
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


# ═══════════════════════════════════════════════════════════════════════════════
# _verify_entry_gate (harness_cli.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyEntryGate:
    """Direct tests for _verify_entry_gate covering all three code paths."""

    @staticmethod
    def _import():
        from cli.phase_cmds import _verify_entry_gate
        return _verify_entry_gate

    @staticmethod
    def _seed_phase_records(tmp_path, phase: int) -> None:
        """Record that every phase before *phase* completed.

        Round 53 站5c gave the P4+ entry gate a second condition: the previous
        phase must have left a `phase_completed` entry. These tests are about
        the FIRST condition — what the quality manifest says — so each seeds
        the record it is not trying to exercise. taskq-super is why the second
        condition exists: it reached Phase 9 with no entry for phase 5.
        """
        import json as _json
        meth = tmp_path / ".methodology"
        meth.mkdir(exist_ok=True)
        (meth / "state.json").write_text(_json.dumps({
            "current_phase": phase,
            "phase_completed": {
                str(n): {"sha": "0" * 40} for n in range(1, phase)
            },
        }))

    def test_p1_no_entry_gate(self, tmp_path):
        """P1 has no entry gate — always passes."""
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=1)
        assert result["passed"] is True
        assert result["gate"] == "None"

    def test_p2_git_log_found(self, tmp_path, monkeypatch):
        """P2 entry gate passes when git log finds human-approve commit."""
        import subprocess as sp

        class FakeCompleted:
            stdout = "abc123 phase1(review-complete): Phase 1 deliverables APPROVED\n"
            stderr = ""

        def fake_run(*args, **kwargs):
            return FakeCompleted()

        monkeypatch.setattr(sp, "run", fake_run)

        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=2)
        assert result["passed"] is True
        assert "Human1 (P1)" in result["gate"]
        assert "Found human APPROVE" in result["reason"]

    def test_p3_git_log_not_found(self, tmp_path, monkeypatch):
        """P3 entry gate fails when git log finds no human-approve commit."""
        import subprocess as sp

        class FakeCompleted:
            stdout = ""
            stderr = ""

        def fake_run(*args, **kwargs):
            return FakeCompleted()

        monkeypatch.setattr(sp, "run", fake_run)

        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=3)
        assert result["passed"] is False
        assert "Human1 (P2)" in result["gate"]
        assert "No human APPROVE" in result["reason"]

    def test_p2_git_log_error(self, tmp_path, monkeypatch):
        """P2 entry gate handles git command failure gracefully."""
        import subprocess as sp

        def fake_run(*args, **kwargs):
            raise sp.SubprocessError("git not found")

        monkeypatch.setattr(sp, "run", fake_run)

        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=2)
        assert result["passed"] is False
        assert "Git log check failed" in result["reason"]

    def test_p4_manifest_not_found(self, tmp_path):
        """P4 entry gate fails when quality_manifest.json is absent."""
        self._seed_phase_records(tmp_path, 4)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=4)
        assert result["passed"] is False
        assert "quality_manifest.json not found" in result["reason"]

    def test_p4_manifest_gate_pass(self, tmp_path):
        """P4 entry gate passes when manifest records Gate 2 PASS."""
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        import json
        manifest = {"gate_results": {"gate2": {"quality_complete": True, "score": 80.0}}}
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        self._seed_phase_records(tmp_path, 4)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=4)
        assert result["passed"] is True
        assert "Gate 2 PASS confirmed" in result["reason"]

    def test_p5_manifest_gate_not_pass(self, tmp_path):
        """P5 entry gate fails when manifest shows Gate 3 not passed."""
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        import json
        manifest = {"gate_results": {"gate3": {"quality_complete": False, "score": 65.0}}}
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        self._seed_phase_records(tmp_path, 5)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=5)
        assert result["passed"] is False
        assert "not PASS" in result["reason"]

    def test_p6_manifest_gate_pass(self, tmp_path):
        """P6 entry gate passes when manifest records Gate 3 PASS."""
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        import json
        manifest = {"gate_results": {"gate3": {"quality_complete": True, "score": 85.0}}}
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        self._seed_phase_records(tmp_path, 6)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=6)
        assert result["passed"] is True
        assert "Gate 3 PASS confirmed" in result["reason"]

    def test_p8_manifest_corrupt(self, tmp_path):
        """Corrupt manifest is handled gracefully."""
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        (manifest_dir / "quality_manifest.json").write_text("{not valid json")

        self._seed_phase_records(tmp_path, 8)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=8)
        assert result["passed"] is False
        assert "Manifest parse error" in result["reason"]
        assert "gate" in result  # Bug 3: the except path must not omit 'gate'

    def test_p8_manifest_gate_none_no_crash(self, tmp_path):
        """Bug 3: a freshly generated manifest seeds gate2/3/4 as None. The P8 entry
        gate must not crash on None.get(...) and must always include a 'gate' key —
        otherwise the caller's entry_gate['gate'] KeyError-crashes run-phase."""
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        manifest = {"gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None}}
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        self._seed_phase_records(tmp_path, 8)
        _verify_entry_gate = self._import()
        result = _verify_entry_gate(tmp_path, phase=8)
        assert result["passed"] is False
        assert result["gate"] == "Gate 4"
        assert "not PASS" in result["reason"]
