"""Tests for constitution/invariant_engine.py — behavioral invariant checking."""

from constitution.invariant_engine import (
    InvariantEngine,
    BehavioralInvariant,
    InvariantViolation,
)


class TestBehavioralInvariant:
    def test_is_in_scope_all_phases(self):
        bi = BehavioralInvariant(
            name="test", description="desc",
            check_func=lambda log, ctx: True,
        )
        for p in range(1, 9):
            assert bi.is_in_scope(p) is True

    def test_is_in_scope_restricted(self):
        bi = BehavioralInvariant(
            name="test", description="desc",
            check_func=lambda log, ctx: True,
            phase_scope=[3, 4],
        )
        assert bi.is_in_scope(3) is True
        assert bi.is_in_scope(4) is True
        assert bi.is_in_scope(1) is False
        assert bi.is_in_scope(5) is False

    def test_is_in_scope_none_is_all(self):
        bi = BehavioralInvariant(
            name="test", description="desc",
            check_func=lambda log, ctx: True,
            phase_scope=None,
        )
        assert bi.is_in_scope(99) is True

    def test_fields(self):
        bi = BehavioralInvariant(
            name="name", description="desc",
            check_func=lambda log, ctx: True,
            severity="critical", source="HR-03",
        )
        assert bi.name == "name"
        assert bi.severity == "critical"
        assert bi.source == "HR-03"


class TestInvariantRules:
    """Test the specific rules from constitution to kill mutants."""

    def test_hr03_phase_execution_order(self):
        from constitution.invariant_engine import InvariantEngine
        engine = InvariantEngine.from_constitution_rules()
        hr03 = next(i for i in engine.invariants if i.source == "HR-03")
        
        # log.get("phase", 1) <= ctx.get("max_allowed_phase", 999) boundary tests
        
        # Exact boundary: phase == max_allowed_phase (Should PASS)
        assert hr03.check_func({"status": "completed", "phase": 2}, {"max_allowed_phase": 2}) is True
        
        # Exceeds boundary: phase > max_allowed_phase (Should FAIL)
        assert hr03.check_func({"status": "completed", "phase": 3}, {"max_allowed_phase": 2}) is False
        
        # Defaults: log.get("phase", 1) and ctx.get("max_allowed_phase", 999)
        assert hr03.check_func({"status": "completed"}, {}) is True  # 1 <= 999
        
        # unable_to_proceed escapes the phase check
        assert hr03.check_func({"status": "unable_to_proceed", "phase": 5}, {"max_allowed_phase": 2}) is True


class TestInvariantViolation:
    def test_default_evidence(self):
        v = InvariantViolation(
            invariant_name="inv", severity="high", phase=3,
            session_id="s1", task="t1", message="msg",
        )
        assert v.evidence == {}

    def test_with_evidence(self):
        v = InvariantViolation(
            invariant_name="inv", severity="critical", phase=1,
            session_id="s2", task="t2", message="msg",
            evidence={"key": "val"},
        )
        assert v.evidence == {"key": "val"}


class TestInvariantEngine:
    def test_init_empty(self):
        engine = InvariantEngine()
        assert engine.invariants == []

    def test_init_with_invariants(self):
        bi = BehavioralInvariant(
            name="test", description="desc",
            check_func=lambda log, ctx: True,
        )
        engine = InvariantEngine([bi])
        assert len(engine.invariants) == 1

    def test_from_constitution_rules(self):
        engine = InvariantEngine.from_constitution_rules()
        assert len(engine.invariants) >= 6
        names = {i.name for i in engine.invariants}
        assert "Phase execution order" in names
        assert "Artifact citation required" in names
        # The A/B log-based invariants were removed framework-wide — they re-checked
        # A≠B / claims from the agent-writable sessions_spawn.log (not tamper-evident).
        assert "Subagent isolation" not in names
        assert "HR-09: Claims verification" not in names

    def test_check_passing_invariant(self):
        bi = BehavioralInvariant(
            name="always_pass", description="desc",
            check_func=lambda log, ctx: True,
        )
        engine = InvariantEngine([bi])
        violations = engine.check(
            execution_log={"task": "test"},
            context={"phase": 1},
        )
        assert violations == []

    def test_check_failing_invariant(self):
        bi = BehavioralInvariant(
            name="always_fail", description="desc",
            check_func=lambda log, ctx: False,
        )
        engine = InvariantEngine([bi])
        violations = engine.check(
            execution_log={"task": "test", "session_id": "s1"},
            context={"phase": 1},
        )
        assert len(violations) == 1
        assert violations[0].invariant_name == "always_fail"
        assert violations[0].severity == "high"

    def test_check_phase_scope_filter(self):
        bi = BehavioralInvariant(
            name="phase3_only", description="desc",
            check_func=lambda log, ctx: False,
            phase_scope=[3],
        )
        engine = InvariantEngine([bi])
        violations = engine.check(
            execution_log={"task": "test"},
            context={"phase": 1},
        )
        assert violations == []

    def test_check_func_exception_becomes_violation(self):
        def broken(log, ctx):
            raise ValueError("boom")
        bi = BehavioralInvariant(
            name="broken", description="desc",
            check_func=broken,
        )
        engine = InvariantEngine([bi])
        violations = engine.check(
            execution_log={"task": "test", "session_id": "s1"},
            context={"phase": 1},
        )
        assert len(violations) == 1
        assert "raised exception" in violations[0].message

    def test_check_batch(self):
        bi = BehavioralInvariant(
            name="check_phase", description="desc",
            check_func=lambda log, ctx: log.get("phase", 1) <= ctx.get("max_phase", 8),
        )
        engine = InvariantEngine([bi])
        logs = [
            {"task": "t1", "phase": 3, "session_id": "s1"},
            {"task": "t2", "phase": 9, "session_id": "s2"},
        ]
        violations = engine.check_batch(logs, {"phase": 5, "max_phase": 8})
        assert len(violations) == 1

    def test_batch_empty_logs(self):
        engine = InvariantEngine.from_constitution_rules()
        violations = engine.check_batch([], {"phase": 1})
        assert violations == []


class TestGenerateReport:
    def test_empty_violations(self):
        engine = InvariantEngine()
        report = engine.generate_report([])
        assert report["passed"] is True
        assert report["total_violations"] == 0

    def test_with_violations(self):
        violations = [
            InvariantViolation(
                invariant_name="inv1", severity="critical", phase=1,
                session_id="s1", task="t1", message="m1",
            ),
            InvariantViolation(
                invariant_name="inv2", severity="high", phase=2,
                session_id="s2", task="t2", message="m2",
            ),
            InvariantViolation(
                invariant_name="inv3", severity="low", phase=3,
                session_id="s3", task="t3", message="m3",
            ),
        ]
        engine = InvariantEngine()
        report = engine.generate_report(violations)
        assert report["passed"] is False
        assert report["total_violations"] == 3
        assert report["critical"] == 1
        assert report["high"] == 1
        assert report["low"] == 1

    def test_passed_when_only_medium(self):
        violations = [
            InvariantViolation(
                invariant_name="inv1", severity="medium", phase=1,
                session_id="s1", task="t1", message="m1",
            ),
        ]
        engine = InvariantEngine()
        report = engine.generate_report(violations)
        assert report["passed"] is True


class TestHRConstitutionInvariants:
    def test_phase_execution_order_pass(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "Phase execution order"][0]
        assert bi.check_func({"phase": 2}, {"max_allowed_phase": 5}) is True
        assert bi.check_func({"status": "unable_to_proceed"}, {"max_allowed_phase": 1}) is True

    def test_phase_execution_order_fail(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "Phase execution order"][0]
        assert bi.check_func({"phase": 10}, {"max_allowed_phase": 5}) is False

    def test_citation_required_pass(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "Artifact citation required"][0]
        assert bi.check_func({"citations": ["FR-01"]}, {}) is True
        assert bi.check_func({"confidence": 7}, {}) is True
        assert bi.check_func({"status": "unable_to_proceed"}, {}) is True

    def test_citation_required_fail(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "Artifact citation required"][0]
        assert bi.check_func({"citations": [], "confidence": 3}, {}) is False

    def test_ab_review_threshold(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "A/B review threshold"][0]
        assert bi.check_func({}, {"review_iterations": 3}) is True
        assert bi.check_func({}, {"review_iterations": 6}) is False
        assert bi.check_func({"status": "unable_to_proceed"}, {"review_iterations": 6}) is True

    def test_confidence_calibration(self):
        engine = InvariantEngine.from_constitution_rules()
        bi = [i for i in engine.invariants if i.name == "Confidence calibration"][0]
        assert bi.check_func({"confidence": 5}, {}) is True
        assert bi.check_func({"confidence": 1, "status": "error"}, {}) is True
        assert bi.check_func({"confidence": 1}, {}) is False
