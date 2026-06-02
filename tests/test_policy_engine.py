"""Tests for enforcement/policy_engine.py — mandatory enforcement engine."""

import os
import json
import pytest
from enforcement.policy_engine import (
    PolicyEngine,
    Policy,
    PolicyResult,
    PolicyViolationException,
    EnforcementLevel,
    create_hard_block_engine,
)


class TestEnforcementLevel:
    def test_values(self):
        assert EnforcementLevel.LOG.value == "log"
        assert EnforcementLevel.WARN.value == "warn"
        assert EnforcementLevel.BLOCK.value == "block"
        assert EnforcementLevel.FAIL_BUILD.value == "fail"


class TestPolicy:
    def test_defaults(self):
        p = Policy(id="test", description="desc", check_fn=lambda: True, enforcement=EnforcementLevel.BLOCK)
        assert p.severity == "medium"
        assert p.enabled is True
        assert p.metadata == {}

    def test_custom_metadata(self):
        p = Policy(id="test", description="desc", check_fn=lambda: True,
                   enforcement=EnforcementLevel.BLOCK, metadata={"key": "val"})
        assert p.metadata == {"key": "val"}


class TestPolicyResult:
    def test_defaults(self):
        r = PolicyResult(policy_id="p1", passed=True, enforcement=EnforcementLevel.LOG,
                         message="ok", timestamp="t")
        assert r.blocked is False

    def test_blocked_when_failed_and_block_level(self):
        r = PolicyResult(policy_id="p1", passed=False, enforcement=EnforcementLevel.BLOCK,
                         message="fail", timestamp="t", blocked=True)
        assert r.blocked is True


class TestPolicyEngine:
    def test_init_has_default_policies(self):
        engine = PolicyEngine()
        assert len(engine.policies) >= 6
        policies = {p.id: p for p in engine.policies}
        
        # Test exact attributes for "commit-has-task-id"
        p1 = policies["commit-has-task-id"]
        assert p1.enforcement == EnforcementLevel.BLOCK
        assert p1.severity == "critical"
        assert p1.metadata.get("problem_type") == "missing_commit_task_id"
        
        # Test "no-bypass-commands"
        p2 = policies["no-bypass-commands"]
        assert p2.enforcement == EnforcementLevel.BLOCK
        assert p2.severity == "critical"
        assert p2.metadata.get("problem_type") == "hard_rule_violation"
        
        # Test "test-coverage-80"
        p3 = policies["test-coverage-80"]
        assert p3.enforcement == EnforcementLevel.BLOCK
        assert p3.severity == "high"
        assert p3.metadata.get("problem_type") == "low_coverage"
        
        # Test "quality-gate-90"
        p4 = policies["quality-gate-90"]
        assert p4.enforcement == EnforcementLevel.BLOCK
        assert p4.severity == "critical"
        assert p4.metadata.get("problem_type") == "low_constitution_score"
        
        # Test "security-score-95"
        p5 = policies["security-score-95"]
        assert p5.enforcement == EnforcementLevel.BLOCK
        assert p5.severity == "high"
        assert p5.metadata.get("problem_type") == "low_constitution_score"
        
        # Test "aspice-docs-required"
        p6 = policies["aspice-docs-required"]
        assert p6.enforcement == EnforcementLevel.BLOCK
        assert p6.severity == "critical"
        assert p6.metadata.get("category") == "documentation"
        assert p6.metadata.get("problem_type") == "missing_aspice_docs"

    def test_add_policy(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="custom", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        assert len(engine.policies) == 1

    def test_remove_policy(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="custom", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        engine.remove_policy("custom")
        assert len(engine.policies) == 0

    def test_remove_nonexistent(self):
        engine = PolicyEngine()
        before = len(engine.policies)
        engine.remove_policy("nonexistent")
        assert len(engine.policies) == before

    def test_enable_disable(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        engine.disable("p1")
        assert engine.policies[0].enabled is False
        engine.enable("p1")
        assert engine.policies[0].enabled is True

    def test_disable_warns(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        with pytest.warns(DeprecationWarning):
            engine.disable("p1")

    def test_check_found_passes(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="always passes",
                                 check_fn=lambda: True, enforcement=EnforcementLevel.BLOCK))
        result = engine.check("p1")
        assert result.passed is True
        assert result.blocked is False

    def test_check_found_fails_block(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="always fails",
                                 check_fn=lambda: False, enforcement=EnforcementLevel.BLOCK))
        result = engine.check("p1")
        assert result.passed is False
        assert result.blocked is True

    def test_check_found_fails_log_level(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="fails but only logs",
                                 check_fn=lambda: False, enforcement=EnforcementLevel.LOG))
        result = engine.check("p1")
        assert result.passed is False
        assert result.blocked is False

    def test_check_not_found(self):
        engine = PolicyEngine()
        result = engine.check("nonexistent")
        assert result.passed is False
        assert "not found" in result.message

    def test_check_exception_is_fail(self):
        def boom():
            raise RuntimeError("boom")
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=boom,
                                 enforcement=EnforcementLevel.BLOCK))
        result = engine.check("p1")
        assert result.passed is False

    def test_get_summary(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        engine.check("p1")
        summary = engine.get_summary()
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["all_passed"] is True

    def test_get_summary_empty(self):
        engine = PolicyEngine()
        engine.results = []
        summary = engine.get_summary()
        assert summary["total"] == 0

    def test_has_task_id(self):
        engine = PolicyEngine()
        assert engine._has_task_id("[FR-01] implement feature") is True
        assert engine._has_task_id("[NFR-03] optimize") is True
        assert engine._has_task_id("no task id here") is False

    def test_check_no_bypass(self):
        engine = PolicyEngine()
        assert engine._check_no_bypass() is True
        os.environ["GIT_COMMAND"] = "git commit --bypass"
        assert engine._check_no_bypass() is False
        del os.environ["GIT_COMMAND"]

    def test_check_quality_score_no_file(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir("/tmp")
        engine = PolicyEngine()
        assert engine._check_quality_score() is True

    def test_check_test_coverage_success(self, tmp_path):
        engine = PolicyEngine()
        coverage_dir = tmp_path / ".methodology"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        (coverage_dir / ".coverage").write_text("80.0", encoding="utf-8")
        
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(str(tmp_path))
            assert engine._check_test_coverage() is True
            
            (coverage_dir / ".coverage").write_text("75.0", encoding="utf-8")
            assert engine._check_test_coverage() is False

    def test_check_quality_score_success(self, tmp_path):
        engine = PolicyEngine()
        score_file = tmp_path / ".methodology" / ".quality_score"
        score_file.parent.mkdir(parents=True, exist_ok=True)
        score_file.write_text('90.0', encoding="utf-8")
        
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(str(tmp_path))
            assert engine._check_quality_score() is True
            
            score_file.write_text('85.0', encoding="utf-8")
            assert engine._check_quality_score() is False


class TestPolicyViolationException:
    def test_raised(self):
        with pytest.raises(PolicyViolationException):
            raise PolicyViolationException("test")


class TestEnforceAll:
    def test_enforce_all_block_raises(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: False,
                                 enforcement=EnforcementLevel.BLOCK))
        with pytest.raises(PolicyViolationException):
            engine.enforce_all()

    def test_enforce_all_passes(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: True,
                                 enforcement=EnforcementLevel.BLOCK))
        results = engine.enforce_all()
        assert len(results) == 1
        assert results[0].passed is True

    def test_enforce_all_skips_disabled(self):
        engine = PolicyEngine()
        engine.policies = []
        engine.add_policy(Policy(id="p1", description="d", check_fn=lambda: False,
                                 enforcement=EnforcementLevel.BLOCK, enabled=False))
        results = engine.enforce_all()
        assert len(results) == 0


class TestRaiseOnBlock:
    def test_no_block_no_raise(self):
        engine = PolicyEngine()
        engine.results = [PolicyResult(policy_id="p1", passed=True,
                                       enforcement=EnforcementLevel.BLOCK,
                                       message="ok", timestamp="t")]
        engine.raise_on_block()

    def test_block_raises(self):
        engine = PolicyEngine()
        engine.results = [PolicyResult(policy_id="p1", passed=False,
                                       enforcement=EnforcementLevel.BLOCK,
                                       message="fail", timestamp="t", blocked=True)]
        with pytest.raises(PolicyViolationException):
            engine.raise_on_block()


class TestCreateHardBlockEngine:
    def test_all_policies_block(self):
        engine = create_hard_block_engine()
        for p in engine.policies:
            assert p.enforcement == EnforcementLevel.BLOCK


class TestReloadPolicy:
    def test_reload_from_file(self, tmp_path):
        config = tmp_path / "enforcement.json"
        config.write_text(json.dumps({
            "policies": [
                {"id": "json-policy", "description": "from json",
                 "enforcement": "block", "severity": "critical"}
            ]
        }))
        engine = PolicyEngine()
        engine.policies = []
        loaded = engine.reload_policy(str(config))
        assert loaded == 1
        assert any(p.id == "json-policy" for p in engine.policies)

    def test_reload_file_not_found(self):
        engine = PolicyEngine()
        with pytest.raises(FileNotFoundError):
            engine.reload_policy("/nonexistent/path.json")

    def test_reload_replaces_existing(self, tmp_path):
        config = tmp_path / "enforcement.json"
        config.write_text(json.dumps({
            "policies": [
                {"id": "my-policy", "description": "updated",
                 "enforcement": "warn", "severity": "low"}
            ]
        }))
        engine = PolicyEngine()
        engine.policies = []
        # Add existing policy with same id
        engine.add_policy(Policy(id="my-policy", description="original",
                                 check_fn=lambda: True, enforcement=EnforcementLevel.BLOCK))
        loaded = engine.reload_policy(str(config))
        assert loaded == 1
        existing = [p for p in engine.policies if p.id == "my-policy"]
        assert len(existing) == 1
        assert existing[0].description == "updated"

    def test_reload_skips_empty_id(self, tmp_path):
        config = tmp_path / "enforcement.json"
        config.write_text(json.dumps({
            "policies": [
                {"description": "no id", "enforcement": "log"}
            ]
        }))
        engine = PolicyEngine()
        engine.policies = []
        loaded = engine.reload_policy(str(config))
        assert loaded == 0

    def test_from_json_factory(self, tmp_path):
        config = tmp_path / "enforcement.json"
        config.write_text(json.dumps({
            "policies": [
                {"id": "factory-policy", "description": "from factory",
                 "enforcement": "block"}
            ]
        }))
        engine = PolicyEngine.from_json(str(config))
        assert any(p.id == "factory-policy" for p in engine.policies)
