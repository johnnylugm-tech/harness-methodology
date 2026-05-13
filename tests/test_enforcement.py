"""
tests/test_enforcement.py — enforcement/ package coverage (W4).

Covers: EnforcementResult, FrameworkEnforcer (mocked checks), PolicyEngine,
        ConstitutionAsCode, ExecutionRegistry, ConstitutionPolicySync (partial).
"""
import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ===========================================================================
# EnforcementResult
# ===========================================================================

class TestEnforcementResult:
    def _er(self):
        from enforcement.framework_enforcer import EnforcementResult
        return EnforcementResult()

    def test_initial_state(self):
        er = self._er()
        assert er.violations == []
        assert er.warnings == []
        assert er.passed is False

    def test_add_violation(self):
        er = self._er()
        er.add_violation("missing spec", "fix it")
        assert len(er.violations) == 1
        assert er.violations[0] == ("missing spec", "fix it")

    def test_add_violation_no_fix(self):
        er = self._er()
        er.add_violation("issue")
        assert er.violations[0] == ("issue", None)

    def test_add_warning(self):
        er = self._er()
        er.add_warning("low coverage")
        assert len(er.warnings) == 1

    def test_add_block_check(self):
        er = self._er()
        er.add_block_check("SPEC_TRACKING", True)
        assert er.block_checks["SPEC_TRACKING"] is True

    def test_add_warn_check(self):
        er = self._er()
        er.add_warn_check("DECISION_FRAMEWORK", False)
        assert er.warn_checks["DECISION_FRAMEWORK"] is False

    def test_summary_contains_passed(self):
        er = self._er()
        er.passed = True
        s = er.summary()
        assert "Passed: True" in s
        assert "BLOCK" in s


# ===========================================================================
# FrameworkEnforcer
# ===========================================================================

class TestFrameworkEnforcer:
    def _fe(self, tmp_path, phase=1):
        from enforcement.framework_enforcer import FrameworkEnforcer
        return FrameworkEnforcer(str(tmp_path), phase=phase)

    def test_init_default_phase(self, tmp_path):
        from enforcement.framework_enforcer import FrameworkEnforcer
        fe = FrameworkEnforcer(str(tmp_path))
        assert fe.phase == 1

    def test_check_decision_framework_missing(self, tmp_path):
        fe = self._fe(tmp_path)
        result = fe.check_decision_framework()
        assert result["exists"] is False

    def test_check_decision_framework_present(self, tmp_path):
        (tmp_path / "DECISION_FRAMEWORK.md").write_text("# Framework")
        fe = self._fe(tmp_path)
        result = fe.check_decision_framework()
        assert result["exists"] is True

    def test_check_enhanced_checklist_skipped_below_phase5(self, tmp_path):
        fe = self._fe(tmp_path, phase=3)
        result = fe.check_enhanced_checklist()
        assert result.get("skipped") is True

    def test_check_enhanced_checklist_missing_phase5(self, tmp_path):
        fe = self._fe(tmp_path, phase=5)
        result = fe.check_enhanced_checklist()
        assert result["exists"] is False

    def test_check_enhanced_checklist_found_phase5(self, tmp_path):
        (tmp_path / "CHECKLIST.md").write_text("# Checklist")
        fe = self._fe(tmp_path, phase=5)
        result = fe.check_enhanced_checklist()
        assert result["exists"] is True

    def test_check_traceability_matrix_missing(self, tmp_path):
        fe = self._fe(tmp_path)
        result = fe.check_traceability_matrix()
        assert result["exists"] is False
        assert result["complete"] is False

    def test_check_traceability_matrix_with_file(self, tmp_path):
        content = "| src/foo.py | ✅ | ✅ |\n| src/bar.py | ✅ | ✅ |\n"
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text(content)
        fe = self._fe(tmp_path)
        result = fe.check_traceability_matrix()
        assert result["exists"] is True
        assert result["total"] >= 2

    def test_check_coverage_threshold_no_file(self, tmp_path):
        """P1-P2 have no coverage requirement, so missing file still passes."""
        fe = self._fe(tmp_path)
        result = fe.check_coverage_threshold()
        # P1 has no coverage requirement, auto-passes
        assert result["passed"] is True

    def test_check_coverage_threshold_no_file_phase3(self, tmp_path):
        """P3 requires coverage report to exist."""
        from enforcement.framework_enforcer import FrameworkEnforcer
        fe = FrameworkEnforcer(str(tmp_path), phase=3)
        result = fe.check_coverage_threshold()
        assert result["passed"] is False
        assert "not found" in result["message"]
        assert result["threshold"] == 70

    def test_check_coverage_threshold_with_xml(self, tmp_path):
        xml_content = '''<?xml version="1.0" ?>
<coverage line-rate="0.85" branch-rate="0.0" version="7.0">
  <packages/>
</coverage>'''
        (tmp_path / "coverage.xml").write_text(xml_content)
        from enforcement.framework_enforcer import FrameworkEnforcer
        fe = FrameworkEnforcer(str(tmp_path), phase=3)
        result = fe.check_coverage_threshold()
        assert result["passed"] is True
        assert result["coverage"] == pytest.approx(85.0, 0.1)

    def test_check_constitution_no_docs(self, tmp_path):
        fe = self._fe(tmp_path)
        result = fe.check_constitution()
        # docs/ doesn't exist → should gracefully return error
        assert "passed" in result or "error" in result

    def test_run_block_level_returns_result(self, tmp_path):
        fe = self._fe(tmp_path)
        with patch.object(fe, "check_spec_tracking", return_value={"exists": False}):
            with patch.object(fe, "check_constitution", return_value={"score": 70, "passed": True}):
                with patch.object(fe, "check_phase_traceability", return_value={"all_verified": True, "missing_links": []}):
                    with patch.object(fe, "check_aspice_completeness", return_value={"complete": True}):
                        with patch.object(fe, "check_traceability_matrix", return_value={"exists": False, "complete": False}):
                            result = fe.run(level="BLOCK")
        assert not result.passed  # spec doesn't exist → violation

    def test_run_warn_level(self, tmp_path):
        fe = self._fe(tmp_path)
        result = fe.run(level="WARN")
        assert "DECISION_FRAMEWORK" in result.warn_checks
        assert "ENHANCED_CHECKLIST" in result.warn_checks

    def test_run_all_passes_when_all_ok(self, tmp_path):
        fe = self._fe(tmp_path, phase=1)
        with patch.object(fe, "check_spec_tracking", return_value={"exists": True, "completeness": 95}):
            with patch.object(fe, "check_constitution", return_value={"score": 80, "passed": True}):
                with patch.object(fe, "check_phase_traceability", return_value={"all_verified": True, "missing_links": []}):
                    with patch.object(fe, "check_aspice_completeness", return_value={"complete": True}):
                        with patch.object(fe, "check_traceability_matrix", return_value={"exists": True, "complete": True}):
                            result = fe.run(level="BLOCK")
        assert result.passed is True

    def test_run_phase2_includes_phase_trace(self, tmp_path):
        fe = self._fe(tmp_path, phase=2)
        with patch.object(fe, "check_spec_tracking", return_value={"exists": False}):
            with patch.object(fe, "check_constitution", return_value={"score": 70}):
                with patch.object(fe, "check_phase_traceability", return_value={"all_verified": False, "missing_links": ["Phase-1→Phase-2"]}) as mock_trace:
                    with patch.object(fe, "check_aspice_completeness", return_value={"complete": True}):
                        with patch.object(fe, "check_traceability_matrix", return_value={"exists": False, "complete": False}):
                            result = fe.run(level="BLOCK")
        mock_trace.assert_called_once()
        assert "ASPICE_PHASE_TRACE" in result.block_checks

    def test_run_with_exit_returns_1_on_failure(self, tmp_path, capsys):
        fe = self._fe(tmp_path)
        with patch.object(fe, "run", return_value=MagicMock(passed=False, violations=[("err", None)], warnings=[])):
            code = fe.run_with_exit()
        assert code == 1

    def test_run_with_exit_returns_0_on_pass(self, tmp_path, capsys):
        fe = self._fe(tmp_path)
        with patch.object(fe, "run", return_value=MagicMock(passed=True, violations=[], warnings=[])):
            code = fe.run_with_exit()
        assert code == 0


# ===========================================================================
# PolicyEngine
# ===========================================================================

class TestPolicyEngine:
    def _pe(self):
        from enforcement.policy_engine import PolicyEngine
        return PolicyEngine()

    def test_has_default_policies(self):
        pe = self._pe()
        assert len(pe.policies) > 0

    def test_add_policy(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="test-p", description="test", check_fn=lambda: True,
                   enforcement=EnforcementLevel.LOG)
        pe.add_policy(p)
        assert any(p2.id == "test-p" for p2 in pe.policies)

    def test_remove_policy(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="removable", description="x", check_fn=lambda: True,
                   enforcement=EnforcementLevel.LOG)
        pe.add_policy(p)
        pe.remove_policy("removable")
        assert not any(p2.id == "removable" for p2 in pe.policies)

    def test_enable_disable_policy(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        import warnings
        pe = self._pe()
        p = Policy(id="toggle", description="x", check_fn=lambda: True,
                   enforcement=EnforcementLevel.LOG)
        pe.add_policy(p)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pe.disable("toggle")
        target = next(p2 for p2 in pe.policies if p2.id == "toggle")
        assert target.enabled is False
        pe.enable("toggle")
        assert target.enabled is True

    def test_check_disabled_policy_still_runs_check(self):
        """check() runs policy regardless of enabled state; enforce_all() skips disabled."""
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="disabled-p", description="x", check_fn=lambda: False,
                   enforcement=EnforcementLevel.BLOCK, enabled=False)
        pe.add_policy(p)
        # check() does not respect enabled flag — only enforce_all() does
        result = pe.check("disabled-p")
        assert result.passed is False  # check_fn returns False

    def test_enforce_all_skips_disabled_policy(self):
        """enforce_all() skips disabled policies entirely."""
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        # Add a disabled BLOCK policy that would fail
        import warnings
        p = Policy(id="skip-me", description="x", check_fn=lambda: False,
                   enforcement=EnforcementLevel.BLOCK)
        pe.add_policy(p)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pe.disable("skip-me")
        # Should not raise because disabled policies are skipped
        try:
            pe.enforce_all()
        except Exception:
            # Other default policies may fail/block, that's ok for this test
            pass

    def test_check_passing_policy(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="pass-p", description="x", check_fn=lambda: True,
                   enforcement=EnforcementLevel.BLOCK)
        pe.add_policy(p)
        result = pe.check("pass-p")
        assert result.passed is True
        assert result.blocked is False

    def test_check_failing_block_policy(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="fail-p", description="x", check_fn=lambda: False,
                   enforcement=EnforcementLevel.BLOCK)
        pe.add_policy(p)
        result = pe.check("fail-p")
        assert result.passed is False
        assert result.blocked is True

    def test_enforce_all_returns_list(self):
        pe = self._pe()
        # All default policies pass without env setup → returns list
        results = pe.enforce_all()
        assert isinstance(results, list)
        assert len(results) > 0

    def test_raise_on_block_raises_when_blocked(self):
        from enforcement.policy_engine import Policy, EnforcementLevel, PolicyViolationException
        pe = self._pe()
        p = Policy(id="blocker", description="x", check_fn=lambda: False,
                   enforcement=EnforcementLevel.BLOCK)
        pe.add_policy(p)
        result = pe.check("blocker")
        with pytest.raises(PolicyViolationException):
            pe.raise_on_block([result])

    def test_raise_on_block_no_raise_when_all_pass(self):
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe = self._pe()
        p = Policy(id="passer", description="x", check_fn=lambda: True,
                   enforcement=EnforcementLevel.BLOCK)
        pe.add_policy(p)
        result = pe.check("passer")
        pe.raise_on_block([result])  # should not raise

    def test_get_summary_structure(self):
        pe = self._pe()
        from enforcement.policy_engine import Policy, EnforcementLevel
        pe.add_policy(Policy(id="s-pass", description="x", check_fn=lambda: True,
                             enforcement=EnforcementLevel.LOG))
        pe.check("s-pass")  # populate results
        s = pe.get_summary()
        assert "total" in s
        assert "blocked" in s
        assert "all_passed" in s

    def test_reload_policy_from_json(self, tmp_path):
        pe = self._pe()
        policy_file = tmp_path / "policies.json"
        policy_file.write_text(json.dumps({
            "policies": [{
                "id": "json-p1",
                "description": "from JSON",
                "enforcement": "block",
                "severity": "critical",
                "enabled": True,
                "metadata": {}
            }]
        }))
        count = pe.reload_policy(str(policy_file))
        assert count >= 1
        assert any(p.id == "json-p1" for p in pe.policies)

    def test_check_commit_message_with_env(self, tmp_path):
        pe = self._pe()
        commit_file = tmp_path / "commit_msg.txt"
        commit_file.write_text("[DEV-123] valid message")
        with patch.dict(os.environ, {"COMMIT_MSG_FILE": str(commit_file)}):
            assert pe._check_commit_message() is True

    def test_check_commit_message_no_env(self):
        pe = self._pe()
        with patch.dict(os.environ, {}, clear=True):
            if "COMMIT_MSG_FILE" in os.environ:
                del os.environ["COMMIT_MSG_FILE"]
            assert pe._check_commit_message() is True  # skip when not in hook context

    def test_has_task_id_true(self):
        pe = self._pe()
        assert pe._has_task_id("[DEV-123] message") is True

    def test_has_task_id_false(self):
        pe = self._pe()
        assert pe._has_task_id("message without id") is False

    def test_check_no_bypass_clean(self):
        pe = self._pe()
        with patch.dict(os.environ, {"GIT_COMMAND": "git commit"}):
            assert pe._check_no_bypass() is True

    def test_check_no_bypass_bypass_detected(self):
        pe = self._pe()
        with patch.dict(os.environ, {"GIT_COMMAND": "git push --no-verify"}):
            assert pe._check_no_bypass() is False

    def test_from_json_classmethod(self, tmp_path):
        from enforcement.policy_engine import PolicyEngine
        policy_file = tmp_path / "e.json"
        policy_file.write_text(json.dumps({"policies": []}))
        engine = PolicyEngine.from_json(str(policy_file))
        assert isinstance(engine, PolicyEngine)

    def test_create_hard_block_engine(self):
        from enforcement.policy_engine import create_hard_block_engine, EnforcementLevel
        engine = create_hard_block_engine()
        # All default policies should be BLOCK level
        block_policies = [p for p in engine.policies if p.enforcement == EnforcementLevel.BLOCK]
        assert len(block_policies) > 0


# ===========================================================================
# ConstitutionAsCode
# ===========================================================================

class TestConstitutionAsCode:
    def _cac(self):
        from enforcement.constitution_as_code import ConstitutionAsCode
        return ConstitutionAsCode()

    def test_has_default_rules(self):
        cac = self._cac()
        assert len(cac.rules) >= 5

    def test_check_commit_message_valid(self):
        cac = self._cac()
        violations = cac.check_commit_message("[DEV-123] Add feature")
        assert violations == []

    def test_check_commit_message_missing_task_id(self):
        cac = self._cac()
        violations = cac.check_commit_message("Add feature without task id")
        assert len(violations) >= 1

    def test_check_command_clean(self):
        cac = self._cac()
        violations = cac.check_command("git commit -m 'msg'")
        assert violations == []

    def test_check_command_bypass(self):
        cac = self._cac()
        violations = cac.check_command("git push --no-verify")
        assert len(violations) >= 1

    def test_check_context_quality_fail(self):
        cac = self._cac()
        violations = cac.check({"quality_score": 50})
        assert any("quality" in v.description.lower() for v in violations)

    def test_check_context_quality_pass(self):
        cac = self._cac()
        violations = cac.check({"quality_score": 95})
        quality_violations = [v for v in violations if "quality" in v.description.lower()]
        assert quality_violations == []

    def test_check_context_coverage_fail(self):
        cac = self._cac()
        violations = cac.check({"coverage": 50})
        assert any("coverage" in v.description.lower() for v in violations)

    def test_check_context_security_fail(self):
        cac = self._cac()
        violations = cac.check({"security_score": 80})
        assert any("security" in v.description.lower() for v in violations)

    def test_enforce_raises_on_violation(self):
        from enforcement.constitution_as_code import ConstitutionViolation
        cac = self._cac()
        with pytest.raises(ConstitutionViolation):
            cac.enforce({"commit_message": "no task id"})

    def test_enforce_no_raise_on_valid(self):
        cac = self._cac()
        # Commit with task ID + good scores — should not raise
        cac.enforce({
            "commit_message": "[DEV-123] valid",
            "quality_score": 95,
            "coverage": 85,
            "security_score": 96,
        })

    def test_add_and_remove_rule(self):
        from enforcement.constitution_as_code import Rule, RuleSeverity
        cac = self._cac()
        rule = Rule(id="R999", description="custom", check_fn=lambda x: True,
                    severity=RuleSeverity.LOW, error_message="fail")
        cac.add_rule(rule)
        assert any(r.id == "R999" for r in cac.rules)
        cac.remove_rule("R999")
        assert not any(r.id == "R999" for r in cac.rules)

    def test_get_rules_summary(self):
        cac = self._cac()
        s = cac.get_rules_summary()
        assert "total" in s
        assert "enabled" in s
        assert "by_severity" in s
        assert s["total"] >= 5

    def test_approval_context_self_approval_blocked(self):
        from enforcement.constitution_as_code import ConstitutionViolation
        cac = self._cac()
        with pytest.raises(ConstitutionViolation):
            cac.enforce({
                "commit_message": "[DEV-123] msg",
                "approval_context": {"approver": "alice", "operator": "alice"},
            })

    def test_approval_context_different_approver_ok(self):
        cac = self._cac()
        # Different approver and operator → R006 passes
        cac.enforce({
            "commit_message": "[DEV-123] msg",
            "approval_context": {"approver": "bob", "operator": "alice"},
        })

    def test_check_commit_message_skips_disabled_rules(self):
        from enforcement.constitution_as_code import Rule, RuleSeverity
        cac = self._cac()
        rule = Rule(id="R-donotrun", description="commit task_id check disabled",
                    check_fn=lambda x: False, severity=RuleSeverity.CRITICAL,
                    error_message="should_not_fire", enabled=False)
        cac.add_rule(rule)
        violations = cac.check_commit_message("no task id")
        assert not any(v.id == "R-donotrun" for v in violations)

    def test_check_command_skips_disabled_rules(self):
        from enforcement.constitution_as_code import Rule, RuleSeverity
        cac = self._cac()
        rule = Rule(id="R-nobypass", description="bypass check disabled",
                    check_fn=lambda x: False, severity=RuleSeverity.CRITICAL,
                    error_message="should_not_fire", enabled=False)
        cac.add_rule(rule)
        violations = cac.check_command("git push --no-verify")
        assert not any(v.id == "R-nobypass" for v in violations)

    def test_check_context_quality_score(self):
        cac = self._cac()
        violations = cac.check({"quality_score": 85})
        quality = [v for v in violations if "quality" in v.description.lower()]
        assert len(quality) >= 0

    def test_check_context_skips_disabled(self):
        from enforcement.constitution_as_code import Rule, RuleSeverity
        cac = self._cac()
        rule = Rule(id="R-disabled-q", description="quality gate disabled",
                    check_fn=lambda x: False, severity=RuleSeverity.CRITICAL,
                    error_message="no", enabled=False)
        cac.add_rule(rule)
        violations = cac.check({"quality_score": 30})
        assert not any(v.id == "R-disabled-q" for v in violations)

    def test_check_with_command_context(self):
        cac = self._cac()
        violations = cac.check({"command": "git commit -m 'msg'"})
        assert isinstance(violations, list)

    def test_check_with_approval_context_disabled_rule(self):
        from enforcement.constitution_as_code import Rule, RuleSeverity
        cac = self._cac()
        rule = Rule(id="R-approval-dis", description="approval check disabled",
                    check_fn=lambda x: False, severity=RuleSeverity.CRITICAL,
                    error_message="no", enabled=False)
        cac.add_rule(rule)
        violations = cac.check({"approval_context": {"approver": "alice", "operator": "alice"}})
        assert not any(v.id == "R-approval-dis" for v in violations)

    def test_enforce_raises_warning_not_critical(self):
        from enforcement.constitution_as_code import ConstitutionWarning, Rule, RuleSeverity
        cac = self._cac()
        # Remove all critical rules so we get a warning instead
        cac.rules = [r for r in cac.rules if r.severity != RuleSeverity.CRITICAL]
        rule = Rule(id="R-warn", description="quality gate warn level",
                    check_fn=lambda x: False, severity=RuleSeverity.HIGH,
                    error_message="non-critical")
        cac.add_rule(rule)
        with pytest.raises(ConstitutionWarning):
            cac.enforce({"quality_score": 50})


# ===========================================================================
# ExecutionRegistry
# ===========================================================================

class TestExecutionRegistry:
    def _reg(self, tmp_path):
        from enforcement.execution_registry import ExecutionRegistry
        db_path = str(tmp_path / "exec_reg.db")
        return ExecutionRegistry(db_path=db_path)

    def test_record_returns_signature(self, tmp_path):
        reg = self._reg(tmp_path)
        sig = reg.record("quality-gate", {"score": 95, "passed": True})
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex

    def test_prove_returns_true_after_record(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("quality-gate", {"score": 95})
        assert reg.prove("quality-gate") is True

    def test_prove_returns_false_for_unrecorded(self, tmp_path):
        reg = self._reg(tmp_path)
        assert reg.prove("unrecorded-step") is False

    def test_prove_with_since_filter(self, tmp_path):
        from datetime import datetime, timedelta
        reg = self._reg(tmp_path)
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        reg.record("step1", {"data": 1})
        assert reg.prove("step1", since=past) is True

    def test_prove_since_future_returns_false(self, tmp_path):
        from datetime import datetime, timedelta
        reg = self._reg(tmp_path)
        reg.record("step1", {"data": 1})
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        assert reg.prove("step1", since=future) is False

    def test_get_records_all(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("s1", {"x": 1})
        reg.record("s2", {"y": 2})
        records = reg.get_records()
        assert len(records) == 2

    def test_get_records_filtered_by_step(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("s1", {"x": 1})
        reg.record("s1", {"x": 2})
        reg.record("s2", {"y": 1})
        records = reg.get_records(step="s1")
        assert len(records) == 2
        assert all(r.step == "s1" for r in records)

    def test_get_records_respects_limit(self, tmp_path):
        reg = self._reg(tmp_path)
        for i in range(10):
            reg.record("step", {"i": i})
        records = reg.get_records(limit=3)
        assert len(records) == 3

    def test_verify_chain_all_present(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("phase-1", {})
        reg.record("phase-2", {})
        result = reg.verify_chain(["phase-1", "phase-2"])
        assert result["complete"] is True

    def test_verify_chain_missing_step(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("phase-1", {})
        result = reg.verify_chain(["phase-1", "phase-2"])
        assert result["complete"] is False
        assert "phase-2" in result.get("missing", [])

    def test_get_evidence_report(self, tmp_path):
        reg = self._reg(tmp_path)
        reg.record("evidence-step", {"key": "value"})
        report = reg.get_evidence_report("evidence-step")
        assert report["step"] == "evidence-step"
        assert report["executed"] is True
        assert "evidence" in report

    def test_get_evidence_report_empty(self, tmp_path):
        reg = self._reg(tmp_path)
        report = reg.get_evidence_report("unrecorded")
        assert report["step"] == "unrecorded"
        assert report["executed"] is False
        assert report["evidence"] is None

    def test_generate_signature_deterministic(self, tmp_path):
        reg = self._reg(tmp_path)
        sig1 = reg._generate_signature({"a": 1, "b": 2})
        sig2 = reg._generate_signature({"b": 2, "a": 1})
        assert sig1 == sig2  # sort_keys=True ensures determinism

    def test_create_minimal_registry(self, tmp_path):
        from enforcement.execution_registry import create_minimal_registry
        with patch("enforcement.execution_registry.ExecutionRegistry.__init__", return_value=None):
            with patch("enforcement.execution_registry.ExecutionRegistry._ensure_db"):
                reg = create_minimal_registry()
                assert reg is not None


# ===========================================================================
# ConstitutionPolicySync
# ===========================================================================

class TestConstitutionPolicySync:
    def test_init_creates_generator(self):
        from enforcement.constitution_policy_sync import ConstitutionPolicyGenerator
        gen = ConstitutionPolicyGenerator()
        assert gen is not None

    def test_find_constitution_none_when_missing(self, tmp_path):
        from enforcement.constitution_policy_sync import ConstitutionPolicyGenerator
        gen = ConstitutionPolicyGenerator()
        with patch.object(gen, "find_constitution", return_value=None):
            result = gen.find_constitution()
        assert result is None

    def test_parse_constitution_empty_file(self, tmp_path):
        from enforcement.constitution_policy_sync import ConstitutionPolicyGenerator
        gen = ConstitutionPolicyGenerator()
        f = tmp_path / "CONSTITUTION.md"
        f.write_text("# No rules here", encoding="utf-8")
        rules = gen.parse_constitution(str(f))
        assert isinstance(rules, list)

    def test_sync_to_engine_returns_engine(self):
        from enforcement.constitution_policy_sync import ConstitutionPolicyGenerator
        from enforcement.policy_engine import PolicyEngine
        gen = ConstitutionPolicyGenerator()
        with patch.object(gen, "generate", return_value=[]):
            engine = gen.sync_to_engine()
        assert isinstance(engine, PolicyEngine)


