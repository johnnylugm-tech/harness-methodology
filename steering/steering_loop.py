#!/usr/bin/env python3
"""
Steering Loop -- AB Workflow Direction Control Engine

Core features:
1. LLM-as-judge to evaluate A/B outputs
2. Three-stage iteration (exploration -> competition -> convergence)
3. Auto-convergence detection + history persistence

Usage:
    from steering.steering_loop import SteeringLoop, SteeringConfig

    loop = SteeringLoop(provider)
    result = loop.iterate(output_a, output_b)
    continue_it, reason = loop.should_continue()
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from enum import Enum
import json
import os
from pathlib import Path


class IterationStage(Enum):
    """Iteration stage"""
    EXPLORATION = "exploration"   # first N rounds, free competition
    COMPETITION = "competition"   # middle rounds, clear differences emerge
    CONVERGENCE = "convergence"   # final rounds, convergence phase


@dataclass
class ScoredOutput:
    """Scored output"""
    output: Union[Dict[str, Any], str]
    scores: Dict[str, float]      # per-dimension scores
    total_score: float
    stage: IterationStage = IterationStage.EXPLORATION


@dataclass
class IterationResult:
    """Single iteration result"""
    iteration: int
    stage: IterationStage
    winner: str                  # "A" or "B"
    scores: Dict[str, Dict[str, float]]  # {"A": {...}, "B": {...}}
    score_delta: float
    feedback: Dict[str, Any]
    convergence_score: float     # smaller = more converged
    best_so_far: ScoredOutput


@dataclass
class SteeringConfig:
    """Steering Loop configuration"""
    max_iterations: int = 5
    min_iterations: int = 3              # minimum iterations before early stop
    exploration_rounds: int = 2          # rounds in exploration stage
    convergence_threshold: float = 0.05  # convergence threshold (delta < this = converged)
    quality_threshold: float = 0.85      # high-quality threshold
    weights: Dict[str, float] = field(default_factory=lambda: {
        "quality": 0.4,      # correctness + completeness
        "efficiency": 0.2,   # token efficiency
        "clarity": 0.2,      # concision + maintainability
        "consistency": 0.2   # consistency with prior outputs
    })


# ---------------------------------------------------------------------------
# LLM Judge Scorer
# ---------------------------------------------------------------------------

class LLMJudgeScorer:
    """
    Uses LLM as judge to objectively evaluate A/B outputs.

    Fixes defect A: correctness/completeness scores were empty,
    effectively hard-coded to 0.5 without an objective source.
    """

    JUDGE_PROMPT = """You are an impartial judge. Compare the following two outputs:

=== Output A ===
{a_output}

=== Output B ===
{b_output}

Score each dimension (0.0~1.0):
1. correctness   - is the logic correct?
2. completeness  - requirement coverage completeness
3. consistency   - consistency with prior outputs
4. concision     - expression conciseness (no verbosity)
5. maintainability - structure/maintainability

Output JSON directly, no other text:
{{"A": {{"correctness": 0.0-1.0, "completeness": 0.0-1.0, "consistency": 0.0-1.0, "concision": 0.0-1.0, "maintainability": 0.0-1.0}}, "B": {{...}}, "reason": "..."}}"""

    FEEDBACK_PROMPT = """As a coach, assess the dimensional differences and provide specific improvement suggestions:

Dimensional differences:
{diffs}

In which dimensions does {winner}'s output lead?
What improvements does {loser}'s output need?

Output JSON:
{{
    "winner_advantages": ["specific advantage description..."],
    "loser_improvements": ["specific improvement suggestions..."],
    "actionable_guidance": "next concrete action step"
}}"""

    def __init__(self, provider):
        """Initialize instance with default configuration."""
        self.provider = provider
        # CRG bridge: lazy-init on first critic debate call; False = import failed, skip retry
        self._crg_bridge: Any = None
        self._crg_tried: bool = False

    def score(self, output_a: Union[Dict[str, Any], str], output_b: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        """
        Score A/B outputs using LLM judge.

        Returns:
            {"A": {...scores...}, "B": {...scores...}}
        """
        a_text = self._extract_text(output_a)
        b_text = self._extract_text(output_b)

        prompt = self.JUDGE_PROMPT.format(a_output=a_text, b_output=b_text)
        try:
            response = self.provider.chat([{"role": "user", "content": prompt}])
            result = json.loads(response)
            if "A" not in result or "B" not in result:
                raise ValueError("Invalid LLM response structure")
            return result
        except Exception:
            # Fallback: pessimistic scoring
            fallback = {
                "correctness": 0.5, "completeness": 0.5,
                "consistency": 0.5, "concision": 0.5, "maintainability": 0.5
            }
            return {"A": fallback.copy(), "B": fallback.copy()}

    def score_with_critic_debate(self, output_a: Union[Dict[str, Any], str], output_b: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        """
        Adversarial multi-agent debate scoring loop:
        1. Critic Agent flags gaps/vulnerabilities for both outputs.
        2. A/B sessions defend against criticism.
        3. Consensus Decider aggregates debate context for final score decision.
        """
        a_text = self._extract_text(output_a)
        b_text = self._extract_text(output_b)

        # 1. Critic Round
        # ODD: Inject runtime trace for semantic self-healing (consume-once to avoid stale bias)
        runtime_trace = ""
        trace_file = Path.cwd() / ".methodology" / "runtime_trace.json"
        if trace_file.exists():
            try:
                trace_data = trace_file.read_text(encoding="utf-8")
                trace_file.unlink(missing_ok=True)  # consume-once: prevent stale injection next call
                runtime_trace += f"\n\n=== Runtime Execution Trace ===\n{trace_data}\n"
            except Exception:
                pass

        # CRG: Inject Minimal Viable Context (bridge cached; no re-instantiation per call)
        if not self._crg_tried:
            try:
                from harness.crg_bridge import CRGBridge
                self._crg_bridge = CRGBridge()
            except Exception:
                pass
            self._crg_tried = True
        if self._crg_bridge is not None:
            try:
                crg_context = self._crg_bridge.get_minimal_context(
                    str(Path.cwd()), "architecture_or_logic"
                )
                if crg_context:
                    runtime_trace += f"\n\n=== Minimal Viable Context (CRG) ===\n{json.dumps(crg_context, indent=2)}\n"
            except Exception:
                pass

        critic_prompt = (
            "You are an adversarial critic. Contrast the following outputs under strict boundary conditions:\n"
            f"=== Output A ===\n{a_text}\n\n"
            f"=== Output B ===\n{b_text}\n"
            f"{runtime_trace}\n"
            "Expose the gaps, edge-case vulnerabilities, and code safety flaws in both outputs. "
            "If a runtime execution trace is provided, use it to identify logic semantic errors.\n"
            "Output JSON directly, with no other text: "
            '{"A_gaps": ["gap 1", ...], "B_gaps": ["gap 1", ...]}'
        )
        try:
            critic_resp = self.provider.chat([{"role": "user", "content": critic_prompt}])
            critic_data = json.loads(critic_resp)
        except Exception:
            critic_data = {"A_gaps": ["Failed to extract gaps"], "B_gaps": ["Failed to extract gaps"]}

        # 2. Defend Round (A/B mock response)
        a_defense_prompt = (
            f"Adversarial critic found these gaps in your output:\n{json.dumps(critic_data.get('A_gaps'), indent=2)}\n\n"
            f"Provide a brief defense statement justification for your design choices: {a_text}"
        )
        b_defense_prompt = (
            f"Adversarial critic found these gaps in your output:\n{json.dumps(critic_data.get('B_gaps'), indent=2)}\n\n"
            f"Provide a brief defense statement justification for your design choices: {b_text}"
        )
        try:
            a_defense = self.provider.chat([{"role": "user", "content": a_defense_prompt}])
        except Exception:
            a_defense = "Implicitly acceptable implementation."
        try:
            b_defense = self.provider.chat([{"role": "user", "content": b_defense_prompt}])
        except Exception:
            b_defense = "Implicitly acceptable implementation."

        # 3. Consensus Decider
        decider_prompt = (
            "You are an impartial judge. Analyze this multi-agent debate history:\n"
            f"=== Output A ===\n{a_text}\n"
            f"Critic gaps in A: {critic_data.get('A_gaps')}\n"
            f"A's Defense: {a_defense}\n\n"
            f"=== Output B ===\n{b_text}\n"
            f"Critic gaps in B: {critic_data.get('B_gaps')}\n"
            f"B's Defense: {b_defense}\n\n"
            "Assess the dimensional scores (0.0~1.0) under adversarial consensus:\n"
            "Output JSON directly, no other text:\n"
            '{"A": {"correctness": 0.0-1.0, "completeness": 0.0-1.0, "consistency": 0.0-1.0, "concision": 0.0-1.0, "maintainability": 0.0-1.0}, "B": {"correctness": 0.0-1.0, "completeness": 0.0-1.0, "consistency": 0.0-1.0, "concision": 0.0-1.0, "maintainability": 0.0-1.0}, "reason": "..."}'
        )
        try:
            response = self.provider.chat([{"role": "user", "content": decider_prompt}])
            result = json.loads(response)
            if "A" in result and "B" in result:
                return result
        except Exception:
            pass

        # Fallback to normal scoring if decider fails
        return self.score(output_a, output_b)

    def generate_feedback(
        self,
        output_a: Union[Dict[str, Any], str],
        output_b: Union[Dict[str, Any], str],
        scores_a: Dict[str, float],
        scores_b: Dict[str, float],
        winner: str
    ) -> Dict[str, Any]:
        """Generate specific improvement suggestions."""
        loser = "B" if winner == "A" else "A"

        diffs = {}
        for dim in scores_a:
            if dim in scores_b:
                delta = scores_a[dim] - scores_b[dim]
                if abs(delta) > 0.1:
                    diffs[dim] = {
                        "winner": winner if delta > 0 else loser,
                        "delta": round(delta, 3),
                        "a_score": scores_a[dim],
                        "b_score": scores_b[dim]
                    }

        prompt = self.FEEDBACK_PROMPT.format(
            diffs=json.dumps(diffs, indent=2, ensure_ascii=False),
            winner=winner,
            loser=loser
        )
        try:
            response = self.provider.chat([{"role": "user", "content": prompt}])
            feedback = json.loads(response)
            feedback["direction"] = f"prefer_{winner}"
            return feedback
        except Exception:
            return {
                "direction": f"prefer_{winner}",
                "winner_advantages": [],
                "loser_improvements": ["Manual review required"],
                "actionable_guidance": "Request human judge intervention"
            }

    def _extract_text(self, output: Union[Dict[str, Any], str]) -> str:
        """Extract text from output dict."""
        if isinstance(output, str):
            return output
        return output.get("text", output.get("content", str(output)))


# ---------------------------------------------------------------------------
# Steering Loop
# ---------------------------------------------------------------------------

class SteeringLoop:
    """
    Steering Loop -- AB Workflow Direction Control Engine

    Resolves three defects:
    - Defect A: scoring was fake -> use LLM as judge
    - Defect B: Efficiency logic was inverted -> fix to quality/tokens
    - Defect C: Convergence logic was inverted -> delta < threshold means converged

    Core concepts:
    1. LLM as judge (not mechanical scoring)
    2. Convergence = A/B scores close together (delta -> 0)
    3. Feedback = specific improvement suggestions
    4. History persistence (accumulated across sessions)
    """

    def __init__(
        self,
        provider,                      # LLM provider
        config: Optional[SteeringConfig] = None,
        history_path: str = ".methodology/steering_history.json",
        project_root: Optional[Path] = None,
    ):
        """Initialize instance with default configuration."""
        self.provider = provider
        self.config = config or SteeringConfig()
        self.history_path = history_path

        self.scorer = LLMJudgeScorer(provider)
        self.iterations: List[IterationResult] = []
        self.best_output: Optional[ScoredOutput] = None
        self.tracer: Any = None

        if project_root is not None:
            try:
                from core.observability import init_tracer
                self.tracer = init_tracer(project_root)
            except ImportError:
                pass
        self.stage = IterationStage.EXPLORATION

    # Sensitive module prefixes that trigger critic debate when changed
    SENSITIVE_MODULE_PREFIXES = ("steering/", "enforcement/", "core/auto_fix/", "core/fsm/")
    # Threshold for score delta below which critic debate is activated (any module)
    DEBATE_DELTA_THRESHOLD = 0.15
    # Wider threshold applied only to sensitive architectural modules
    SENSITIVE_DEBATE_THRESHOLD = 0.30

    def iterate(
        self,
        output_a: Union[Dict[str, Any], str],
        output_b: Union[Dict[str, Any], str],
        changed_modules: Optional[List[str]] = None,
    ) -> IterationResult:
        """
        Execute a single iteration, wrapped in an OpenTelemetry span if available.
        """
        iteration_num = len(self.iterations) + 1
        
        if self.tracer:
            with self.tracer.start_as_current_span(f"steering_iteration_{iteration_num}") as span:
                span.set_attribute("iteration", iteration_num)
                span.set_attribute("stage", self.stage.value)
                if changed_modules:
                    span.set_attribute("changed_modules", json.dumps(changed_modules))
                
                result = self._do_iterate(output_a, output_b, changed_modules, iteration_num)
                
                span.set_attribute("score_delta", result.score_delta)
                span.set_attribute("convergence_score", result.convergence_score)
                span.set_attribute("winner", result.winner)
                return result
        else:
            return self._do_iterate(output_a, output_b, changed_modules, iteration_num)

    def _do_iterate(
        self,
        output_a: Union[Dict[str, Any], str],
        output_b: Union[Dict[str, Any], str],
        changed_modules: Optional[List[str]],
        iteration_num: int,
    ) -> IterationResult:
        # 1. Update current stage
        self._update_stage(iteration_num)

        # 2. Quick scoring to compute initial delta for Dynamic Activation
        raw_scores = self.scorer.score(output_a, output_b)
        scores_a = raw_scores["A"]
        scores_b = raw_scores["B"]

        scored_a = self._compute_weighted_score(scores_a)
        scored_b = self._compute_weighted_score(scores_b)
        initial_delta = abs(scored_a - scored_b)

        # 3. Dynamic Activation: decide whether to escalate to critic debate
        use_critic = self._should_activate_debate(initial_delta, changed_modules)
        if use_critic:
            raw_scores = self.scorer.score_with_critic_debate(output_a, output_b)
            scores_a = raw_scores["A"]
            scores_b = raw_scores["B"]
            scored_a = self._compute_weighted_score(scores_a)
            scored_b = self._compute_weighted_score(scores_b)

        # 4. Determine winner
        winner = "A" if scored_a > scored_b else "B"
        score_delta = abs(scored_a - scored_b)

        # 5. Update best
        winner_scored = scored_a if winner == "A" else scored_b
        winner_output = output_a if winner == "A" else output_b
        winner_scores = scores_a if winner == "A" else scores_b

        if not self.best_output or winner_scored > self.best_output.total_score:
            self.best_output = ScoredOutput(
                output=winner_output,
                scores=winner_scores,
                total_score=winner_scored,
                stage=self.stage
            )

        # 6. Generate feedback
        feedback = self.scorer.generate_feedback(
            output_a, output_b, scores_a, scores_b, winner
        )

        # 7. Compute convergence score (smaller = more converged)
        convergence = self._calc_convergence_score()

        result = IterationResult(
            iteration=iteration_num,
            stage=self.stage,
            winner=winner,
            scores={"A": scores_a, "B": scores_b},
            score_delta=score_delta,
            feedback=feedback,
            convergence_score=convergence,
            best_so_far=self.best_output
        )

        self.iterations.append(result)
        self._persist_history()

        return result

    def should_continue(self) -> Tuple[bool, str]:
        """
        Determine whether to continue iterating.

        Resolves HR-12 >5 rounds conflict:
        - HR-12 intent: "don't iterate meaninglessly" (negative constraint)
        - SteeringLoop.max_iterations: "run at most N rounds" (positive cap)
        - Not contradictory: can stop early when convergence condition is met

        Returns:
            (should_continue, reason)
        """
        n = len(self.iterations)

        if n >= self.config.max_iterations:
            return False, "max_iterations_reached"

        if n < self.config.min_iterations:
            return True, "min_iterations_not_reached"

        last = self.iterations[-1]

        if self.best_output and self.best_output.total_score >= self.config.quality_threshold:
            return False, "quality_threshold_reached"

        # Convergence check (fixes defect C)
        # delta small = scores close = converged
        if self.stage == IterationStage.CONVERGENCE:
            if last.score_delta <= self.config.convergence_threshold:
                return False, "converged"

        return True, "continue_iterating"

    def _compute_weighted_score(self, scores: Dict[str, float]) -> float:
        """
        Compute weighted total score. Range: [0.0, 1.0].

        Formula (normalized so all-1.0 → 1.0, all-0.5 → 0.5):
          quality   = correctness * 0.7 + completeness * 0.3
          clarity   = concision   * 0.6 + maintainability * 0.4
          efficiency = scores["efficiency"] if present, else concision proxy
          total = quality * w[quality] + clarity * w[clarity]
                + consistency * w[consistency] + efficiency * w[efficiency]
        """
        w = self.config.weights

        correctness = scores.get("correctness", 0.5)
        completeness = scores.get("completeness", 0.5)
        consistency = scores.get("consistency", 0.5)
        concision = scores.get("concision", 0.5)
        maintainability = scores.get("maintainability", 0.5)

        quality_score = correctness * 0.7 + completeness * 0.3
        clarity_score = concision * 0.6 + maintainability * 0.4
        efficiency_score = scores.get("efficiency", concision)

        return (
            quality_score * w.get("quality", 0.4) +
            clarity_score * w.get("clarity", 0.2) +
            consistency * w.get("consistency", 0.2) +
            efficiency_score * w.get("efficiency", 0.2)
        )

    def _should_activate_debate(
        self, initial_delta: float, changed_modules: Optional[List[str]]
    ) -> bool:
        """
        Dynamic Activation: decide whether the Critic debate loop should fire.

        Activates when:
        - A/B score delta < DEBATE_DELTA_THRESHOLD (close call, any module), OR
        - Any changed module is in a sensitive architectural layer AND delta is still
          below SENSITIVE_DEBATE_THRESHOLD (wider window, but not unconditional —
          a clear winner on sensitive code doesn't need debate).

        Returns False (skip debate) to save LLM tokens on unambiguous outcomes.
        """
        if initial_delta < self.DEBATE_DELTA_THRESHOLD:
            return True

        if changed_modules and initial_delta < self.SENSITIVE_DEBATE_THRESHOLD:
            for mod in changed_modules:
                for prefix in self.SENSITIVE_MODULE_PREFIXES:
                    if mod.startswith(prefix):
                        return True

        return False

    def _update_stage(self, iteration_num: int):
        """Update iteration stage."""
        if iteration_num <= self.config.exploration_rounds:
            self.stage = IterationStage.EXPLORATION
        elif iteration_num >= self.config.max_iterations - 1:
            self.stage = IterationStage.CONVERGENCE
        else:
            self.stage = IterationStage.COMPETITION

    def _calc_convergence_score(self) -> float:
        """
        Compute convergence score (smaller = more converged).

        Average delta of last 3 rounds: smaller = scores closer = more converged.
        """
        if len(self.iterations) < 2:
            return 1.0  # no reference yet

        deltas = [i.score_delta for i in self.iterations[-3:]]
        return sum(deltas) / len(deltas)

    def _persist_history(self):
        """Persist history to file."""
        path = self.history_path
        if path:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

        data = {
            "iterations": [
                {
                    "iteration": i.iteration,
                    "stage": i.stage.value,
                    "winner": i.winner,
                    "score_delta": round(i.score_delta, 4),
                    "convergence_score": round(i.convergence_score, 4)
                }
                for i in self.iterations
            ],
            "best_score": round(self.best_output.total_score, 4) if self.best_output else None
        }

        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def get_summary(self) -> Dict[str, Any]:
        """Get iteration summary."""
        return {
            "total_iterations": len(self.iterations),
            "final_stage": self.stage.value,
            "final_winner": self.iterations[-1].winner if self.iterations else None,
            "best_score": round(self.best_output.total_score, 4) if self.best_output else None,
            "convergence": round(self._calc_convergence_score(), 4),
            "should_continue": self.should_continue()[0]
        }

    def run_until_converge(
        self,
        get_next_pair_fn,   # () -> (output_a, output_b)
        max_rounds: Optional[int] = None
    ) -> IterationResult:
        """
        Run until convergence.

        Args:
            get_next_pair_fn: function to produce next round A/B pair
            max_rounds: max rounds (overrides config.max_iterations)

        Returns:
            IterationResult: last round result
        """
        max_iter = max_rounds or self.config.max_iterations

        for _ in range(max_iter):
            output_a, output_b = get_next_pair_fn()
            self.iterate(output_a, output_b)

            continue_it, _ = self.should_continue()
            if not continue_it:
                break

        return self.iterations[-1]
