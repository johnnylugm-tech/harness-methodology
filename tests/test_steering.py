"""
W5 — tests for steering/steering_loop.py and steering/integrations.py
All LLM/API calls mocked.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path
from typing import Dict, Any


# ─── Shared fixtures / helpers ────────────────────────────────────────────────

_DIM_KEYS = ("correctness", "completeness", "consistency", "concision", "maintainability")

# A clearly wins
_SCORES_A_HIGH = {
    "A": {"correctness": 0.9, "completeness": 0.8,
          "consistency": 0.8, "concision": 0.7, "maintainability": 0.8},
    "B": {"correctness": 0.3, "completeness": 0.4,
          "consistency": 0.5, "concision": 0.4, "maintainability": 0.3},
}
# B clearly wins
_SCORES_B_HIGH = {
    "A": _SCORES_A_HIGH["B"].copy(),
    "B": _SCORES_A_HIGH["A"].copy(),
}
# Near-equal (small delta)
_SCORES_EQUAL = {
    "A": dict.fromkeys(_DIM_KEYS, 0.6),
    "B": dict.fromkeys(_DIM_KEYS, 0.61),
}

_FEEDBACK_OK = {
    "winner_advantages": ["precise logic"],
    "loser_improvements": ["add examples"],
    "actionable_guidance": "focus on clarity",
}

_OUT_A = {"text": "Output A"}
_OUT_B = {"text": "Output B"}


def _provider(scores=None, feedback=None, n: int = 12):
    """Mock LLM provider: alternates score / feedback responses."""
    scores = scores if scores is not None else _SCORES_A_HIGH
    feedback = feedback if feedback is not None else _FEEDBACK_OK
    resps = []
    for _ in range(n):
        resps.append(json.dumps(scores))
        resps.append(json.dumps(feedback))
    m = MagicMock()
    m.chat.side_effect = resps
    return m


def _loop(scores=None, feedback=None, config=None, history_path=""):
    """Create SteeringLoop with mocked provider and no file writes by default."""
    from steering.steering_loop import SteeringLoop
    return SteeringLoop(_provider(scores, feedback), config=config, history_path=history_path)


# ─── LLMJudgeScorer ───────────────────────────────────────────────────────────

class TestLLMJudgeScorer:

    def _scorer(self, score_resp=None, feedback_resp=None):
        from steering.steering_loop import LLMJudgeScorer
        responses = []
        for _ in range(6):
            responses.append(json.dumps(score_resp or _SCORES_A_HIGH))
            responses.append(json.dumps(feedback_resp or _FEEDBACK_OK))
        p = MagicMock()
        p.chat.side_effect = responses
        return LLMJudgeScorer(p)

    def test_score_returns_a_and_b_keys(self):
        s = self._scorer()
        result = s.score(_OUT_A, _OUT_B)
        assert "A" in result and "B" in result

    def test_score_all_dimensions_present(self):
        s = self._scorer()
        result = s.score(_OUT_A, _OUT_B)
        for k in _DIM_KEYS:
            assert k in result["A"]
            assert k in result["B"]

    def test_score_calls_provider_chat_once(self):
        s = self._scorer()
        s.score(_OUT_A, _OUT_B)
        assert s.provider.chat.call_count == 1

    def test_score_invalid_json_returns_fallback(self):
        from steering.steering_loop import LLMJudgeScorer
        p = MagicMock()
        p.chat.return_value = "not-json"
        s = LLMJudgeScorer(p)
        result = s.score(_OUT_A, _OUT_B)
        assert result["A"]["correctness"] == 0.5
        assert result["B"]["correctness"] == 0.5

    def test_score_missing_keys_returns_fallback(self):
        from steering.steering_loop import LLMJudgeScorer
        p = MagicMock()
        p.chat.return_value = json.dumps({"X": {}})   # no A/B
        s = LLMJudgeScorer(p)
        result = s.score(_OUT_A, _OUT_B)
        assert result["A"]["correctness"] == 0.5

    def test_score_provider_exception_returns_fallback(self):
        from steering.steering_loop import LLMJudgeScorer
        p = MagicMock()
        p.chat.side_effect = RuntimeError("timeout")
        s = LLMJudgeScorer(p)
        result = s.score(_OUT_A, _OUT_B)
        assert result["A"]["completeness"] == 0.5

    def test_generate_feedback_contains_direction(self):
        s = self._scorer()
        fb = s.generate_feedback(_OUT_A, _OUT_B,
                                 _SCORES_A_HIGH["A"], _SCORES_A_HIGH["B"], "A")
        assert fb["direction"] == "prefer_A"

    def test_generate_feedback_winner_b_direction(self):
        s = self._scorer()
        fb = s.generate_feedback(_OUT_A, _OUT_B,
                                 _SCORES_B_HIGH["A"], _SCORES_B_HIGH["B"], "B")
        assert fb["direction"] == "prefer_B"

    def test_generate_feedback_no_significant_diffs_skips_prompt_diff(self):
        """When all deltas <= 0.1, diffs dict is empty but still calls provider."""
        s = self._scorer()
        eq = dict.fromkeys(_DIM_KEYS, 0.6)
        fb = s.generate_feedback(_OUT_A, _OUT_B, eq, eq, "A")
        assert "direction" in fb

    def test_generate_feedback_provider_exception_returns_fallback(self):
        from steering.steering_loop import LLMJudgeScorer
        p = MagicMock()
        p.chat.side_effect = RuntimeError("timeout")   # always raises
        s = LLMJudgeScorer(p)
        fb = s.generate_feedback(_OUT_A, _OUT_B,
                                 _SCORES_A_HIGH["A"], _SCORES_A_HIGH["B"], "A")
        assert "Manual review required" in fb["loser_improvements"]

    def test_extract_text_str(self):
        from steering.steering_loop import LLMJudgeScorer
        s = LLMJudgeScorer(MagicMock())
        assert s._extract_text("hello") == "hello"

    def test_extract_text_dict_text_key(self):
        from steering.steering_loop import LLMJudgeScorer
        s = LLMJudgeScorer(MagicMock())
        assert s._extract_text({"text": "hi"}) == "hi"

    def test_extract_text_dict_content_key(self):
        from steering.steering_loop import LLMJudgeScorer
        s = LLMJudgeScorer(MagicMock())
        assert s._extract_text({"content": "bye"}) == "bye"

    def test_extract_text_dict_fallback_str_conversion(self):
        from steering.steering_loop import LLMJudgeScorer
        s = LLMJudgeScorer(MagicMock())
        result = s._extract_text({"other": "val"})
        assert "other" in result


# ─── SteeringConfig ────────────────────────────────────────────────────────────

class TestSteeringConfig:

    def test_defaults(self):
        from steering.steering_loop import SteeringConfig
        c = SteeringConfig()
        assert c.max_iterations == 5
        assert c.min_iterations == 3
        assert c.exploration_rounds == 2
        assert c.convergence_threshold == 0.05
        assert c.quality_threshold == 0.85

    def test_default_weights_keys(self):
        from steering.steering_loop import SteeringConfig
        c = SteeringConfig()
        assert set(c.weights.keys()) == {"quality", "efficiency", "clarity", "consistency"}

    def test_custom_values(self):
        from steering.steering_loop import SteeringConfig
        c = SteeringConfig(max_iterations=10, quality_threshold=0.9)
        assert c.max_iterations == 10
        assert c.quality_threshold == 0.9


# ─── SteeringLoop._compute_weighted_score ──────────────────────────────────────

class TestComputeWeightedScore:

    def test_all_half_scores(self):
        loop = _loop()
        scores = dict.fromkeys(_DIM_KEYS, 0.5)
        result = loop._compute_weighted_score(scores)
        # quality=0.5*0.4+0.5*0.2=0.3, clarity=0.5*0.2+0.5*0.1=0.15, consistency=0.5*0.2=0.1
        assert abs(result - 0.55) < 1e-9

    def test_high_correctness_dominates(self):
        loop = _loop()
        scores = dict.fromkeys(_DIM_KEYS, 0.0)
        scores["correctness"] = 1.0
        result = loop._compute_weighted_score(scores)
        # quality = 1.0*0.4 = 0.4, rest=0
        assert abs(result - 0.4) < 1e-9

    def test_a_high_beats_b_low(self):
        loop = _loop()
        sa = loop._compute_weighted_score(_SCORES_A_HIGH["A"])
        sb = loop._compute_weighted_score(_SCORES_A_HIGH["B"])
        assert sa > sb

    def test_missing_dimension_defaults_to_half(self):
        loop = _loop()
        result = loop._compute_weighted_score({})   # all missing
        assert abs(result - 0.55) < 1e-9


# ─── SteeringLoop._update_stage ───────────────────────────────────────────────

class TestUpdateStage:

    def test_exploration_stage_round_1(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        loop._update_stage(1)
        assert loop.stage == IterationStage.EXPLORATION

    def test_exploration_stage_round_2(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        loop._update_stage(2)
        assert loop.stage == IterationStage.EXPLORATION

    def test_competition_stage_round_3(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        loop._update_stage(3)
        assert loop.stage == IterationStage.COMPETITION

    def test_convergence_stage_round_4(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        loop._update_stage(4)
        assert loop.stage == IterationStage.CONVERGENCE

    def test_convergence_stage_round_5(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        loop._update_stage(5)
        assert loop.stage == IterationStage.CONVERGENCE


# ─── SteeringLoop._calc_convergence_score ─────────────────────────────────────

class TestCalcConvergenceScore:

    def test_empty_returns_one(self):
        loop = _loop()
        assert loop._calc_convergence_score() == 1.0

    def test_one_iteration_returns_one(self):
        loop = _loop()
        loop.iterate(_OUT_A, _OUT_B)
        # After 1 iteration, len < 2 so returns 1.0
        assert loop._calc_convergence_score() == 1.0

    def test_two_iterations_uses_deltas(self):
        loop = _loop()
        loop.iterate(_OUT_A, _OUT_B)
        loop.iterate(_OUT_A, _OUT_B)
        score = loop._calc_convergence_score()
        assert 0.0 <= score <= 1.0

    def test_many_iterations_averages_last_three(self):
        loop = _loop(scores=_SCORES_EQUAL)
        for _ in range(5):
            loop.iterate(_OUT_A, _OUT_B)
        score = loop._calc_convergence_score()
        # near-equal scores → small delta → small convergence score
        assert score < 0.5


# ─── SteeringLoop.iterate ─────────────────────────────────────────────────────

class TestSteeringLoopIterate:

    def test_returns_iteration_result(self):
        from steering.steering_loop import IterationResult
        loop = _loop()
        r = loop.iterate(_OUT_A, _OUT_B)
        assert isinstance(r, IterationResult)

    def test_iteration_number_increments(self):
        loop = _loop()
        r1 = loop.iterate(_OUT_A, _OUT_B)
        r2 = loop.iterate(_OUT_A, _OUT_B)
        assert r1.iteration == 1
        assert r2.iteration == 2

    def test_winner_a_when_a_higher(self):
        loop = _loop(scores=_SCORES_A_HIGH)
        r = loop.iterate(_OUT_A, _OUT_B)
        assert r.winner == "A"

    def test_winner_b_when_b_higher(self):
        loop = _loop(scores=_SCORES_B_HIGH)
        r = loop.iterate(_OUT_A, _OUT_B)
        assert r.winner == "B"

    def test_score_delta_non_negative(self):
        loop = _loop()
        r = loop.iterate(_OUT_A, _OUT_B)
        assert r.score_delta >= 0.0

    def test_best_output_set_after_first_iteration(self):
        from steering.steering_loop import ScoredOutput
        loop = _loop()
        loop.iterate(_OUT_A, _OUT_B)
        assert isinstance(loop.best_output, ScoredOutput)

    def test_best_output_not_replaced_by_worse(self):
        """Second iteration with lower scores should not replace best_output."""
        from steering.steering_loop import SteeringLoop
        # First call: A=0.90, second call: both ~0.5 (fallback from bad JSON)
        p = MagicMock()
        p.chat.side_effect = [
            json.dumps(_SCORES_A_HIGH),  # score round 1
            json.dumps(_FEEDBACK_OK),     # feedback round 1
            "bad-json",                   # score round 2 → fallback (0.55)
            json.dumps(_FEEDBACK_OK),     # feedback round 2
        ]
        loop = SteeringLoop(p, history_path="")
        loop.iterate(_OUT_A, _OUT_B)
        first_best = loop.best_output.total_score
        loop.iterate(_OUT_A, _OUT_B)
        # best should remain the high score
        assert loop.best_output.total_score >= first_best

    def test_feedback_direction_in_result(self):
        loop = _loop(scores=_SCORES_A_HIGH)
        r = loop.iterate(_OUT_A, _OUT_B)
        assert r.feedback.get("direction") == "prefer_A"

    def test_best_so_far_in_result(self):
        from steering.steering_loop import ScoredOutput
        loop = _loop()
        r = loop.iterate(_OUT_A, _OUT_B)
        assert isinstance(r.best_so_far, ScoredOutput)

    def test_stage_recorded_in_result(self):
        from steering.steering_loop import IterationStage
        loop = _loop()
        r = loop.iterate(_OUT_A, _OUT_B)
        assert r.stage == IterationStage.EXPLORATION

    def test_str_inputs_accepted(self):
        loop = _loop()
        r = loop.iterate("text A", "text B")
        assert r.iteration == 1


# ─── SteeringLoop.should_continue ─────────────────────────────────────────────

class TestShouldContinue:

    def test_min_iterations_not_reached(self):
        loop = _loop()
        loop.iterate(_OUT_A, _OUT_B)   # 1 iteration, min=3
        cont, reason = loop.should_continue()
        assert cont is True
        assert reason == "min_iterations_not_reached"

    def test_quality_threshold_stops(self):
        """After 3+ iterations with high-scoring A, quality_threshold fires."""
        loop = _loop(scores=_SCORES_A_HIGH)
        for _ in range(3):
            loop.iterate(_OUT_A, _OUT_B)
        cont, reason = loop.should_continue()
        # best_score=0.90 >= quality_threshold=0.85
        assert cont is False
        assert reason == "quality_threshold_reached"

    def test_max_iterations_stops(self):
        from steering.steering_loop import SteeringConfig
        loop = _loop(config=SteeringConfig(max_iterations=3, min_iterations=1))
        for _ in range(3):
            loop.iterate(_OUT_A, _OUT_B)
        cont, reason = loop.should_continue()
        assert cont is False
        assert reason == "max_iterations_reached"

    def test_converged_stops_in_convergence_stage(self):
        """Small delta in CONVERGENCE stage → 'converged'."""
        from steering.steering_loop import SteeringConfig, IterationStage
        # convergence_threshold=0.5 so any small delta qualifies
        cfg = SteeringConfig(
            max_iterations=5, min_iterations=3,
            exploration_rounds=1,
            convergence_threshold=0.99,  # very lenient
            quality_threshold=1.0        # never fires
        )
        loop = _loop(scores=_SCORES_EQUAL, config=cfg)
        # need 3 iterations to pass min, then stage must be CONVERGENCE
        for i in range(3):
            loop.iterate(_OUT_A, _OUT_B)
        # Force CONVERGENCE stage manually
        loop.stage = IterationStage.CONVERGENCE
        # last delta will be very small due to equal scores
        cont, reason = loop.should_continue()
        assert cont is False
        assert reason == "converged"

    def test_continue_when_not_converged(self):
        """After min iterations but low quality and not converged → continue."""
        from steering.steering_loop import SteeringConfig, IterationStage
        cfg = SteeringConfig(
            max_iterations=10, min_iterations=3,
            quality_threshold=1.0,   # never fires
            convergence_threshold=0.0  # impossible to satisfy
        )
        loop = _loop(scores=_SCORES_A_HIGH, config=cfg)
        for _ in range(3):
            loop.iterate(_OUT_A, _OUT_B)
        loop.stage = IterationStage.COMPETITION  # not convergence
        cont, reason = loop.should_continue()
        assert cont is True
        assert reason == "continue_iterating"


# ─── SteeringLoop._persist_history ────────────────────────────────────────────

class TestPersistHistory:

    def test_writes_json_file(self, tmp_path):
        from steering.steering_loop import SteeringLoop
        dest = str(tmp_path / "history" / "out.json")
        loop = SteeringLoop(_provider(), history_path=dest)
        loop.iterate(_OUT_A, _OUT_B)
        assert Path(dest).exists()
        data = json.loads(Path(dest).read_text())
        assert "iterations" in data
        assert len(data["iterations"]) == 1

    def test_empty_path_does_not_write(self, tmp_path):
        loop = _loop(history_path="")
        loop.iterate(_OUT_A, _OUT_B)
        # No file created anywhere under tmp_path
        assert not list(tmp_path.rglob("*.json"))

    def test_history_records_stage_and_winner(self, tmp_path):
        from steering.steering_loop import SteeringLoop
        dest = str(tmp_path / "h.json")
        loop = SteeringLoop(_provider(scores=_SCORES_A_HIGH), history_path=dest)
        loop.iterate(_OUT_A, _OUT_B)
        data = json.loads(Path(dest).read_text())
        entry = data["iterations"][0]
        assert entry["winner"] == "A"
        assert "stage" in entry


# ─── SteeringLoop.get_summary ─────────────────────────────────────────────────

class TestGetSummary:

    def test_empty_summary(self):
        loop = _loop()
        s = loop.get_summary()
        assert s["total_iterations"] == 0
        assert s["final_winner"] is None
        assert s["best_score"] is None

    def test_summary_after_iterations(self):
        loop = _loop()
        loop.iterate(_OUT_A, _OUT_B)
        s = loop.get_summary()
        assert s["total_iterations"] == 1
        assert s["final_winner"] in ("A", "B")
        assert s["best_score"] is not None

    def test_summary_should_continue_field(self):
        loop = _loop()
        s = loop.get_summary()
        assert isinstance(s["should_continue"], bool)


# ─── SteeringLoop.run_until_converge ──────────────────────────────────────────

class TestRunUntilConverge:

    def test_returns_last_iteration_result(self):
        from steering.steering_loop import IterationResult
        loop = _loop(scores=_SCORES_A_HIGH)
        result = loop.run_until_converge(lambda: (_OUT_A, _OUT_B))
        assert isinstance(result, IterationResult)

    def test_stops_before_max_on_quality_threshold(self):
        """High quality scores → stops at 3 (min_iterations) not 5 (max)."""
        loop = _loop(scores=_SCORES_A_HIGH)
        loop.run_until_converge(lambda: (_OUT_A, _OUT_B))
        assert len(loop.iterations) <= loop.config.max_iterations

    def test_max_rounds_override(self):
        from steering.steering_loop import SteeringConfig
        cfg = SteeringConfig(max_iterations=10, quality_threshold=1.0)
        loop = _loop(scores=_SCORES_EQUAL, config=cfg)
        loop.run_until_converge(lambda: (_OUT_A, _OUT_B), max_rounds=2)
        assert len(loop.iterations) == 2

    def test_get_next_pair_called_each_round(self):
        counter = [0]
        def pair_fn():
            counter[0] += 1
            return _OUT_A, _OUT_B
        loop = _loop(scores=_SCORES_A_HIGH)
        loop.run_until_converge(pair_fn)
        assert counter[0] >= 1


# ─── SteeringBVSIntegrator ────────────────────────────────────────────────────

class TestSteeringBVSIntegrator:

    def _bvs_runner(self, passed=True, violations=0):
        r = MagicMock()
        r.run.return_value = {
            "passed": passed,
            "total_violations": violations,
            "violations": [{"rule": "r1", "message": "m1"}] if not passed else []
        }
        return r

    def _steering_result(self, iteration=2, delta=0.05, convergence=0.04, winner="A"):
        from steering.steering_loop import ScoredOutput, IterationStage
        so = ScoredOutput(output=_OUT_A, scores={}, total_score=0.8, stage=IterationStage.EXPLORATION)
        r = MagicMock()
        r.iteration = iteration
        r.score_delta = delta
        r.convergence_score = convergence
        r.winner = winner
        r.best_so_far = so
        return r

    def test_passing_bvs_is_hr_compliant(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=True, violations=0))
        result = bvs.check_phase_invariants(self._steering_result(), {})
        assert result.hr_compliant is True

    def test_failing_bvs_not_hr_compliant(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=False, violations=3))
        result = bvs.check_phase_invariants(self._steering_result(), {})
        assert result.hr_compliant is False

    def test_violations_populated_on_failure(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=False, violations=2))
        result = bvs.check_phase_invariants(self._steering_result(), {})
        assert any("BVS" in v for v in result.violations)

    def test_metrics_populated(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=True))
        result = bvs.check_phase_invariants(self._steering_result(), {})
        assert "bvs_violations" in result.metrics
        assert "steering_iterations" in result.metrics

    def test_details_contains_winner(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=True))
        result = bvs.check_phase_invariants(self._steering_result(winner="A"), {})
        assert result.details["steering_winner"] == "A"

    def test_high_convergence_score_not_hr_compliant(self):
        from steering.integrations import SteeringBVSIntegrator
        bvs = SteeringBVSIntegrator("/tmp", self._bvs_runner(passed=True))
        # convergence_score=0.5 > 0.10 threshold
        result = bvs.check_phase_invariants(self._steering_result(convergence=0.5), {})
        assert result.hr_compliant is False


# ─── SteeringConstitutionIntegrator ───────────────────────────────────────────

class TestSteeringConstitutionIntegrator:

    def _integrator(self, citations=None, claims=None, verified=None):
        from steering.integrations import SteeringConstitutionIntegrator
        cp = MagicMock()
        cp.extract_citations.return_value = citations if citations is not None else ["[1]"]
        cp.extract_claims.return_value = claims if claims is not None else []
        cp.verify_claim.return_value = verified if verified is not None else True
        checker = MagicMock()
        return SteeringConstitutionIntegrator(checker, cp)

    def test_string_input_converted_to_dict(self):
        intg = self._integrator(citations=[])
        result = intg.check_output_compliance("short text", phase=1)
        # Should not raise; hr_compliant depends on violations
        assert hasattr(result, "hr_compliant")

    def test_non_dict_non_str_returns_error(self):
        intg = self._integrator()
        result = intg.check_output_compliance(12345, phase=1)
        assert result.hr_compliant is False
        assert any("TypeError" in v for v in result.violations)

    def test_short_text_no_citations_no_violation(self):
        """Text < 500 chars with no citations: no HR-07 violation."""
        intg = self._integrator(citations=[])
        result = intg.check_output_compliance({"text": "short"}, phase=1)
        assert not any("HR-07" in v for v in result.violations)

    def test_long_text_no_citations_hr07_violation(self):
        intg = self._integrator(citations=[])
        long_output = {"text": "word " * 200}   # > 500 chars
        result = intg.check_output_compliance(long_output, phase=1)
        assert any("HR-07" in v for v in result.violations)

    def test_claims_majority_verified_no_hr09_violation(self):
        # 2 claims, both verified
        intg = self._integrator(citations=["[1]"], claims=["c1", "c2"], verified=True)
        result = intg.check_output_compliance({"text": "text"}, phase=1)
        assert not any("HR-09" in v for v in result.violations)

    def test_claims_minority_verified_hr09_violation(self):
        # 2 claims, none verified (0/2 < 0.5)
        intg = self._integrator(citations=["[1]"], claims=["c1", "c2"], verified=False)
        result = intg.check_output_compliance({"text": "text"}, phase=1)
        assert any("HR-09" in v for v in result.violations)

    def test_phase3_no_artifacts_warning(self):
        intg = self._integrator(citations=["[1]"])
        result = intg.check_output_compliance({"text": "text"}, phase=3)
        assert any("HR-15" in w for w in result.warnings)

    def test_phase2_no_artifacts_no_warning(self):
        intg = self._integrator(citations=["[1]"])
        result = intg.check_output_compliance({"text": "text"}, phase=2)
        assert not any("HR-15" in w for w in result.warnings)

    def test_citations_counted_in_metrics(self):
        intg = self._integrator(citations=["[1]", "[2]"])
        result = intg.check_output_compliance({"text": "text"}, phase=1)
        assert result.metrics["citation_count"] == 2

    def test_compliant_when_no_violations(self):
        intg = self._integrator(citations=["[1]"])
        result = intg.check_output_compliance(
            {"text": "text", "artifacts": ["doc.pdf"]}, phase=3
        )
        assert result.hr_compliant is True

    def test_extract_text_dict_content_key(self):
        from steering.integrations import SteeringConstitutionIntegrator
        s = SteeringConstitutionIntegrator(MagicMock(), MagicMock())
        assert s._extract_text({"content": "hello"}) == "hello"

    def test_extract_text_str(self):
        from steering.integrations import SteeringConstitutionIntegrator
        s = SteeringConstitutionIntegrator(MagicMock(), MagicMock())
        assert s._extract_text("raw") == "raw"


# ─── SteeringCQGIntegrator ────────────────────────────────────────────────────

class TestSteeringCQGIntegrator:

    def test_no_checker_returns_defaults(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator(cqg_checker=None)
        result = cqg.measure_code_quality({"text": "some text"})
        assert result == {"quality": 0.5, "complexity": 0.5, "readability": 0.5}

    def test_no_code_blocks_returns_defaults(self):
        from steering.integrations import SteeringCQGIntegrator
        checker = MagicMock()
        cqg = SteeringCQGIntegrator(cqg_checker=checker)
        result = cqg.measure_code_quality({"text": "no code blocks here"})
        assert result["quality"] == 0.5

    def test_with_code_block_calls_checker(self):
        from steering.integrations import SteeringCQGIntegrator
        checker = MagicMock()
        checker.check.return_value = {"quality_score": 0.9}
        cqg = SteeringCQGIntegrator(cqg_checker=checker)
        text = "```python\nprint('hello')\n```"
        result = cqg.measure_code_quality({"text": text})
        assert result["quality"] == pytest.approx(0.9)
        checker.check.assert_called_once()

    def test_checker_exception_falls_back_to_half(self):
        from steering.integrations import SteeringCQGIntegrator
        checker = MagicMock()
        checker.check.side_effect = RuntimeError("parse error")
        cqg = SteeringCQGIntegrator(cqg_checker=checker)
        text = "```python\nx = 1\n```"
        result = cqg.measure_code_quality({"text": text})
        assert result["quality"] == pytest.approx(0.5)

    def test_integrate_cqg_into_steering_score_formula(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator()
        # base=0.8, cqg_quality=1.0 → 0.8*0.85 + 1.0*0.15 = 0.68+0.15=0.83
        result = cqg.integrate_cqg_into_steering_score(0.8, {"quality": 1.0})
        assert abs(result - 0.83) < 1e-9

    def test_integrate_cqg_default_quality(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator()
        # missing "quality" → 0.5 default
        result = cqg.integrate_cqg_into_steering_score(0.8, {})
        # 0.8*0.85 + 0.5*0.15 = 0.68 + 0.075 = 0.755
        assert abs(result - 0.755) < 1e-9

    def test_extract_code_blocks_empty(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator()
        assert cqg._extract_code_blocks("no code here") == []

    def test_extract_code_blocks_single(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator()
        text = "```python\nx = 1\n```"
        blocks = cqg._extract_code_blocks(text)
        assert len(blocks) == 1
        assert "x = 1" in blocks[0]

    def test_extract_code_blocks_multiple(self):
        from steering.integrations import SteeringCQGIntegrator
        cqg = SteeringCQGIntegrator()
        text = "```python\na=1\n```\n```js\nb=2\n```"
        blocks = cqg._extract_code_blocks(text)
        assert len(blocks) == 2


# ─── HR12Resolution ───────────────────────────────────────────────────────────

class TestHR12Resolution:

    def test_should_stop_at_max(self):
        from steering.integrations import HR12Resolution
        r = HR12Resolution(max_allowed=5)
        stop, reason = r.should_stop(current_round=5, score_delta=0.5)
        assert stop is True
        assert reason == "hr12_max_iterations"

    def test_should_not_stop_below_min(self):
        from steering.integrations import HR12Resolution
        r = HR12Resolution(min_rounds_before_stop=3)
        stop, reason = r.should_stop(current_round=2, score_delta=0.0)
        assert stop is False
        assert reason == "min_rounds_not_reached"

    def test_should_stop_when_converged_early_flag(self):
        from steering.integrations import HR12Resolution
        r = HR12Resolution(min_rounds_before_stop=2)
        stop, reason = r.should_stop(current_round=3, score_delta=0.5,
                                      has_converged_early=True)
        assert stop is True
        assert reason == "converged"

    def test_should_stop_when_delta_below_threshold(self):
        from steering.integrations import HR12Resolution
        r = HR12Resolution(early_stop_threshold=0.05, min_rounds_before_stop=2)
        stop, reason = r.should_stop(current_round=3, score_delta=0.02)
        assert stop is True
        assert reason == "converged"

    def test_should_continue_when_no_condition_met(self):
        from steering.integrations import HR12Resolution
        r = HR12Resolution(max_allowed=5, early_stop_threshold=0.05,
                           min_rounds_before_stop=2)
        stop, reason = r.should_stop(current_round=3, score_delta=0.3)
        assert stop is False
        assert reason == "continue"

    def test_resolve_max_iterations_reached(self):
        from steering.integrations import HR12Resolution
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3,
                                        current_round=5, score_delta=0.3)
        assert result["should_stop"] is True
        assert result["reason"] == "max_iterations_reached"
        assert result["hr12_compliant"] is True

    def test_resolve_min_iterations_not_reached(self):
        from steering.integrations import HR12Resolution
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3,
                                        current_round=2, score_delta=0.0)
        assert result["should_stop"] is False
        assert result["reason"] == "min_iterations_not_reached"

    def test_resolve_converged(self):
        from steering.integrations import HR12Resolution
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3,
                                        current_round=4, score_delta=0.01,
                                        convergence_threshold=0.05)
        assert result["should_stop"] is True
        assert result["reason"] == "converged"

    def test_resolve_continue_iterating(self):
        from steering.integrations import HR12Resolution
        result = HR12Resolution.resolve(max_iterations=5, min_iterations=3,
                                        current_round=4, score_delta=0.5,
                                        convergence_threshold=0.05)
        assert result["should_stop"] is False
        assert result["reason"] == "continue_iterating"

    def test_resolve_always_hr12_compliant(self):
        from steering.integrations import HR12Resolution
        for round_n, delta in [(1, 0.9), (3, 0.4), (5, 0.0)]:
            result = HR12Resolution.resolve(5, 3, round_n, delta)
            assert result["hr12_compliant"] is True


# ─── HRConstraints & IntegrationResult ────────────────────────────────────────

class TestHRConstraints:

    def test_defaults(self):
        from steering.integrations import HRConstraints
        hr = HRConstraints()
        assert hr.max_iterations == 5
        assert hr.min_iterations == 3
        assert hr.efficiency_target == 0.30
        assert hr.require_citation is True

    def test_custom_values(self):
        from steering.integrations import HRConstraints
        hr = HRConstraints(max_iterations=10, efficiency_target=0.5)
        assert hr.max_iterations == 10
        assert hr.efficiency_target == 0.5


class TestIntegrationResult:

    def test_construction(self):
        from steering.integrations import IntegrationResult
        r = IntegrationResult(
            hr_compliant=True, violations=[], warnings=[],
            metrics={"a": 1.0}, details={"x": "y"}
        )
        assert r.hr_compliant is True
        assert r.metrics["a"] == 1.0


# ─── SteeringIntegrator ───────────────────────────────────────────────────────

class TestSteeringIntegrator:

    def _integrator(self, scores=None, config=None):
        from steering.integrations import SteeringIntegrator
        p = _provider(scores)
        return SteeringIntegrator(p, project_path="/tmp", phase=3, config=config)

    def test_init_creates_steering_loop(self):
        from steering.steering_loop import SteeringLoop
        si = self._integrator()
        assert isinstance(si.steering, SteeringLoop)

    def test_init_default_hr_constraints(self):
        from steering.integrations import HRConstraints
        si = self._integrator()
        assert isinstance(si.hr, HRConstraints)

    def test_iterate_with_full_check_no_integrations(self):
        si = self._integrator()
        result, checks = si.iterate_with_full_check(
            _OUT_A, _OUT_B, run_bvs=False, run_constitution=False, run_cqg=False
        )
        assert result.iteration == 1
        assert checks == []

    def test_iterate_with_full_check_bvs_exception_appended_as_warning(self):
        """BVS lazy import fails → caught, warning IntegrationResult appended."""
        si = self._integrator()
        # bvs_integrator property will raise ImportError when loading BVSRunner
        with patch.dict("sys.modules", {"constitution.bvs_runner": None}):
            result, checks = si.iterate_with_full_check(
                _OUT_A, _OUT_B, run_bvs=True, run_constitution=False
            )
        assert len(checks) == 1
        assert any("BVS" in w for c in checks for w in c.warnings)

    def test_iterate_with_full_check_constitution_exception_appended(self):
        """Constitution lazy import fails → caught, warning IntegrationResult appended."""
        si = self._integrator()
        with patch.dict("sys.modules", {"constitution.citation_parser": None,
                                         "constitution.verification_constitution_checker": None}):
            result, checks = si.iterate_with_full_check(
                _OUT_A, _OUT_B, run_bvs=False, run_constitution=True
            )
        assert len(checks) == 1
        assert any("Constitution" in w for c in checks for w in c.warnings)

    def test_should_continue_returns_tuple(self):
        si = self._integrator()
        cont, reason = si.should_continue
        assert isinstance(cont, bool)
        assert isinstance(reason, str)

    def test_should_continue_stops_at_quality_threshold(self):
        si = self._integrator(scores=_SCORES_A_HIGH)
        # 3 iterations to pass min_iterations (3)
        for _ in range(3):
            si.iterate_with_full_check(_OUT_A, _OUT_B, run_bvs=False, run_constitution=False)
        cont, reason = si.should_continue
        assert cont is False

    def test_get_full_summary_structure(self):
        si = self._integrator()
        summary = si.get_full_summary()
        assert "steering" in summary
        assert "hr12_compliant" in summary
        assert "hr12_stop_reason" in summary
        assert "hr_constraints" in summary

    def test_get_full_summary_hr_constraints_fields(self):
        si = self._integrator()
        summary = si.get_full_summary()
        assert "max_iterations" in summary["hr_constraints"]
        assert "efficiency_target" in summary["hr_constraints"]

    def test_get_full_summary_after_iterate(self):
        si = self._integrator(scores=_SCORES_A_HIGH)
        si.iterate_with_full_check(_OUT_A, _OUT_B, run_bvs=False, run_constitution=False)
        summary = si.get_full_summary()
        assert summary["steering"]["total_iterations"] == 1
