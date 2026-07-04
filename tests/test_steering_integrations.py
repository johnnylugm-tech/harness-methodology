"""Tests for steering/integrations.py — HR12Resolution, CQG, integration points."""

from steering.integrations import (
    HRConstraints,
    IntegrationResult,
    HR12Resolution,
    SteeringCQGIntegrator,
    SteeringIntegrator,
)


class TestHRConstraints:
    def test_defaults(self):
        hr = HRConstraints()
        assert hr.max_iterations == 5
        assert hr.min_iterations == 3
        assert hr.efficiency_target == 0.30
        assert hr.convergence_threshold == 0.05
        assert hr.require_citation is True
        assert hr.require_claims_verification is True
        assert hr.require_artifact_traceability is True
        assert hr.require_ai_test_tagging is True


class TestIntegrationResult:
    def test_defaults(self):
        r = IntegrationResult(hr_compliant=True, violations=[], warnings=[], metrics={}, details={})
        assert r.hr_compliant is True

    def test_with_violations(self):
        r = IntegrationResult(
            hr_compliant=False,
            violations=["bad"], warnings=["maybe"],
            metrics={"score": 0.5}, details={"phase": 3},
        )
        assert len(r.violations) == 1
        assert r.metrics["score"] == 0.5


class TestHR12Resolution:
    def test_defaults(self):
        hr12 = HR12Resolution()
        assert hr12.max_allowed == 5
        assert hr12.early_stop_threshold == 0.05
        assert hr12.min_rounds_before_stop == 3

    def test_should_stop_max_reached(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(5, 0.10)
        assert stop is True
        assert reason == "hr12_max_iterations"

    def test_should_stop_beyond_max(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(6, 0.10)
        assert stop is True

    def test_should_stop_min_not_reached(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(2, 0.01)
        assert stop is False
        assert reason == "min_rounds_not_reached"

    def test_should_stop_converged(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(4, 0.03)
        assert stop is True
        assert reason == "converged"

    def test_should_stop_early_converged_flag(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(3, 0.10, has_converged_early=True)
        assert stop is True

    def test_should_continue(self):
        hr12 = HR12Resolution()
        stop, reason = hr12.should_stop(3, 0.10)
        assert stop is False
        assert reason == "continue"

    def test_resolve_max_iterations(self):
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3, current_round=5, score_delta=0.10)
        assert result["should_stop"] is True
        assert result["reason"] == "max_iterations_reached"
        assert result["hr12_compliant"] is True

    def test_resolve_min_iterations(self):
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3, current_round=2, score_delta=0.10)
        assert result["should_stop"] is False
        assert result["reason"] == "min_iterations_not_reached"

    def test_resolve_converged(self):
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3, current_round=4, score_delta=0.01)
        assert result["should_stop"] is True
        assert result["reason"] == "converged"

    def test_resolve_continue(self):
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3, current_round=4, score_delta=0.10)
        assert result["should_stop"] is False
        assert result["reason"] == "continue_iterating"

    def test_resolve_custom_thresholds(self):
        result = HR12Resolution.resolve(
            max_iterations=10, min_iterations=5, current_round=7,
            score_delta=0.10, convergence_threshold=0.15,
        )
        assert result["should_stop"] is True
        assert result["reason"] == "converged"


class TestSteeringCQGIntegrator:
    def test_no_checker_defaults(self):
        cqg = SteeringCQGIntegrator()
        scores = cqg.measure_code_quality({"text": "test"})
        assert scores == {"quality": 0.5, "complexity": 0.5, "readability": 0.5}

    def test_measure_without_text(self):
        cqg = SteeringCQGIntegrator()
        scores = cqg.measure_code_quality("just a string")
        assert scores["quality"] == 0.5

    def test_integrate_cqg_score(self):
        cqg = SteeringCQGIntegrator()
        result = cqg.integrate_cqg_into_steering_score(0.8, {"quality": 0.9})
        expected = 0.8 * 0.85 + 0.9 * 0.15
        assert abs(result - expected) < 0.001

    def test_integrate_cqg_default_quality(self):
        cqg = SteeringCQGIntegrator()
        result = cqg.integrate_cqg_into_steering_score(0.8, {})
        expected = 0.8 * 0.85 + 0.5 * 0.15
        assert abs(result - expected) < 0.001

    def test_extract_text_from_string(self):
        cqg = SteeringCQGIntegrator()
        assert cqg._extract_text("hello") == "hello"

    def test_extract_text_from_dict(self):
        cqg = SteeringCQGIntegrator()
        assert cqg._extract_text({"text": "hello"}) == "hello"
        assert cqg._extract_text({"content": "world"}) == "world"

    def test_extract_code_blocks(self):
        cqg = SteeringCQGIntegrator()
        text = "Some text\n```python\nprint('hello')\n```\nMore text"
        blocks = cqg._extract_code_blocks(text)
        assert len(blocks) == 1
        assert "print('hello')" in blocks[0]

    def test_extract_code_blocks_none(self):
        cqg = SteeringCQGIntegrator()
        assert cqg._extract_code_blocks("no code here") == []


class _FakeSteeringResult:
    def __init__(self, best_so_far=None):
        self.best_so_far = best_so_far


class _FakeSteering:
    def __init__(self, best_so_far=None):
        self._result = _FakeSteeringResult(best_so_far)

    def iterate(self, output_a, output_b):
        return self._result


def _make_integrator(monkeypatch, best_so_far=None):
    """Bypass SteeringIntegrator.__init__ (needs a real provider) and wire
    only what iterate_with_full_check touches. Uses monkeypatch so the
    duck-typed fakes below don't have to satisfy the real collaborators'
    type annotations."""
    integrator = object.__new__(SteeringIntegrator)
    monkeypatch.setattr(integrator, "steering", _FakeSteering(best_so_far), raising=False)
    monkeypatch.setattr(integrator, "_bvs_integrator", None, raising=False)
    monkeypatch.setattr(integrator, "_constitution_integrator", None, raising=False)
    monkeypatch.setattr(integrator, "_cqg_integrator", None, raising=False)
    monkeypatch.setattr(integrator, "phase", 3, raising=False)
    return integrator


class TestIterateWithFullCheckExceptionHandling:
    """A check that raised did not verify compliance — hr_compliant must be
    False, not True, or a broken check silently masks a real violation."""

    def test_bvs_exception_marks_noncompliant(self, monkeypatch):
        integrator = _make_integrator(monkeypatch)

        class _RaisingBVS:
            def check_phase_invariants(self, *_a, **_k):
                raise RuntimeError("bvs boom")

        monkeypatch.setattr(integrator, "_bvs_integrator", _RaisingBVS(), raising=False)

        _steering_result, results = integrator.iterate_with_full_check(
            {}, {}, run_bvs=True, run_constitution=False, run_cqg=False
        )
        assert len(results) == 1
        assert results[0].hr_compliant is False
        assert "bvs boom" in results[0].warnings[0]

    def test_constitution_exception_marks_noncompliant(self, monkeypatch):
        winner = type("Winner", (), {"output": "code"})()
        integrator = _make_integrator(monkeypatch, best_so_far=winner)

        class _RaisingConstitution:
            def check_output_compliance(self, *_a, **_k):
                raise RuntimeError("constitution boom")

        monkeypatch.setattr(integrator, "_constitution_integrator", _RaisingConstitution(), raising=False)

        _steering_result, results = integrator.iterate_with_full_check(
            {}, {}, run_bvs=False, run_constitution=True, run_cqg=False
        )
        assert len(results) == 1
        assert results[0].hr_compliant is False
        assert "constitution boom" in results[0].warnings[0]

    def test_constitution_skips_cleanly_when_no_best_so_far(self, monkeypatch):
        integrator = _make_integrator(monkeypatch, best_so_far=None)

        _steering_result, results = integrator.iterate_with_full_check(
            {}, {}, run_bvs=False, run_constitution=True, run_cqg=False
        )
        assert len(results) == 1
        assert results[0].hr_compliant is False
        assert "best_so_far" in results[0].warnings[0]

    def test_cqg_skips_cleanly_when_no_best_so_far(self, monkeypatch):
        integrator = _make_integrator(monkeypatch, best_so_far=None)

        class _ExplodingCQG:
            def measure_code_quality(self, *_a, **_k):
                raise AssertionError("must not be called when best_so_far is None")

        monkeypatch.setattr(integrator, "_cqg_integrator", _ExplodingCQG(), raising=False)

        # Must not raise.
        integrator.iterate_with_full_check(
            {}, {}, run_bvs=False, run_constitution=False, run_cqg=True
        )
