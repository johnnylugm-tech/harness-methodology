#!/usr/bin/env python3
"""
AutoFixEngine — proactive auto-repair for harness-methodology.

Transforms detect->block->wait_for_human into detect->classify->auto_fix->verify->loop.

Reference: methodology-v2 SKILL.md "fail -> FIX + RETRY" execution protocol.

Exports:
    AutoFixEngine: main engine class
    FixResult: outcome of a fix attempt
    FixStrategy: AUTO_FIX / AUTO_FIX_WITH_VERIFICATION / HUMAN_REQUIRED
    FixContext: input to the engine (problem description + source)
    EscalationCondition: enum of human escalation reasons
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.auto_fix.error_class import ErrorClass

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class FixStrategy(Enum):
    AUTO_FIX = "auto_fix"
    AUTO_FIX_WITH_VERIFICATION = "auto_fix_verify"
    HUMAN_REQUIRED = "human_required"


class EscalationCondition(Enum):
    HR12_MAX_ROUNDS = "hr12_max_rounds_exceeded"
    HR13_TIMEOUT = "hr13_phase_timeout"
    HR14_INTEGRITY = "hr14_integrity_freeze"
    HARDCODED_SECRETS = "hardcoded_secrets"
    GATE_SCORE_LOW = "gate_score_below_60"
    HARD_RULE_VIOLATION = "hard_rule_violation"
    LOW_CONFIDENCE = "auto_fix_confidence_below_70"
    KILL_SWITCH = "kill_switch_open"
    GATE4_BLOCKED = "gate4_blocked"


@dataclass
class FixContext:
    """Input to the AutoFixEngine describing what to fix."""

    source: str  # module that detected the problem
    problem_type: str  # e.g., "missing_artifact", "low_coverage"
    severity: str  # "critical", "high", "medium", "low"
    phase: int  # current pipeline phase
    project_root: Path
    details: Dict[str, Any] = field(default_factory=dict)
    fr_id: Optional[str] = None
    gate_num: Optional[int] = None
    retry_count: int = 0


@dataclass
class FixResult:
    """Outcome of a fix attempt."""

    success: bool
    strategy: FixStrategy
    problem_type: str
    confidence: float  # 0-100
    action_taken: str
    verification_result: Optional[Dict[str, Any]] = None
    pre_fix_safety: Optional[Dict[str, Any]] = None
    post_fix_drift: Optional[Dict[str, Any]] = None
    rounds_used: int = 0
    escalation: Optional[EscalationCondition] = None
    error: Optional[str] = None


class AutoFixEngine:
    """Unified auto-repair engine for harness-methodology.

    Usage:
        engine = AutoFixEngine(project_root="/path/to/project", phase=3)
        context = FixContext(source="constitution/runner", problem_type="low_constitution_score",
                             severity="high", phase=3, project_root=Path("/project"))
        result = engine.fix(context)
        if result.escalation:
            raise HumanRequiredError(result)
    """

    def __init__(
        self,
        project_root: str | Path,
        phase: int = 1,
        max_rounds: int = 5,
        confidence_threshold: float = 70.0,
        max_phase_time_multiplier: float = 3.0,
        integrity_threshold: float = 40.0,
        gate_min_score: float = 60.0,
        gate_min_rounds: int = 3,
        crg_bridge=None,
    ):
        self.project_root = Path(project_root)
        self.phase = phase
        self.max_rounds = max_rounds
        self.confidence_threshold = confidence_threshold
        self.max_phase_time_multiplier = max_phase_time_multiplier
        self.integrity_threshold = integrity_threshold
        self.gate_min_score = gate_min_score
        self.gate_min_rounds = gate_min_rounds
        self._crg = crg_bridge
        self._round_counters: Dict[str, int] = {}
        self._phase_start_time: Optional[float] = None
        self._phase_estimate: Optional[float] = None
        self._all_results: List[FixResult] = []

    # ── classification ────────────────────────────────────────────────────

    def classify(self, context: FixContext) -> tuple[FixStrategy, float, int, str, "ErrorClass"]:
        """Classify severity and return (strategy, confidence, max_rounds, problem_type, error_class)."""
        from core.auto_fix.classifier import classify

        return classify(context.source, context.details)

    # ── main entry ─────────────────────────────────────────────────────────

    def fix(self, context: FixContext) -> FixResult:
        """Main entry: classify -> guard -> fix -> verify. Returns FixResult or escalates."""
        strategy, confidence, max_rounds, problem_type, error_class = self.classify(context)
        context.problem_type = problem_type

        # HUMAN_REQUIRED → escalate immediately
        if strategy == FixStrategy.HUMAN_REQUIRED:
            condition = self._human_condition_for(context)
            result = FixResult(
                success=False,
                strategy=strategy,
                problem_type=problem_type,
                confidence=0.0,
                action_taken="Escalated to human — auto-fix not permitted",
                escalation=condition,
                rounds_used=context.retry_count,
            )
            self._all_results.append(result)
            return result

        # Round counter
        round_key = f"{context.source}:{problem_type}"
        self._round_counters[round_key] = context.retry_count

        # Pre-fix safety
        from core.auto_fix.guardrails import pre_fix_safety_check

        files = self._files_for_context(context)
        safety = pre_fix_safety_check(self.project_root, files, crg_bridge=self._crg)
        if not safety.get("safe", True):
            return FixResult(
                success=False,
                strategy=strategy,
                problem_type=problem_type,
                confidence=0.0,
                action_taken=f"Blocked by pre-fix safety: {safety.get('message', 'unknown')}",
                pre_fix_safety=safety,
                rounds_used=context.retry_count,
            )

        # Apply fix
        from core.auto_fix.strategies import STRATEGY_REGISTRY

        fix_fn = STRATEGY_REGISTRY.get(problem_type)
        if fix_fn is None:
            return FixResult(
                success=False,
                strategy=strategy,
                problem_type=problem_type,
                confidence=0.0,
                action_taken=f"No strategy registered for {problem_type!r}",
                error=f"Unknown problem_type: {problem_type!r}",
                rounds_used=context.retry_count,
            )

        success, action_taken, conf = fix_fn(context, self.project_root)
        result = FixResult(
            success=success,
            strategy=strategy,
            problem_type=problem_type,
            confidence=conf,
            action_taken=action_taken,
            pre_fix_safety=safety,
            rounds_used=context.retry_count,
        )

        # Post-fix drift if verified strategy
        if strategy == FixStrategy.AUTO_FIX_WITH_VERIFICATION and success:
            from core.auto_fix.guardrails import post_fix_drift_check

            modified = self._files_for_context(context)
            drift = post_fix_drift_check(self.project_root, modified)
            # CRG Point 4: structural drift check after auto-fix
            if self._crg is not None:
                try:
                    crg_drifted = self._crg.check_drift(str(self.project_root))
                    drift["crg_drift_detected"] = crg_drifted
                except Exception:
                    drift["crg_drift_detected"] = None
            result.post_fix_drift = drift

        # Escalation check
        result.escalation = self.check_escalation(context, result)
        self._all_results.append(result)
        return result

    def check_escalation(
        self, context: FixContext, result: FixResult
    ) -> Optional[EscalationCondition]:
        """Check all 9 human escalation conditions."""
        round_key = f"{context.source}:{context.problem_type}"
        rounds = self._round_counters.get(round_key, context.retry_count)

        # HR-12: max rounds exceeded (only after exhausting all rounds;
        # the outer loop's post-for block owns the exhaustion path)
        if rounds > self.max_rounds:
            return EscalationCondition.HR12_MAX_ROUNDS

        # HR-14: integrity freeze
        if self._check_integrity() < self.integrity_threshold:
            return EscalationCondition.HR14_INTEGRITY

        # HR-13: phase timeout
        if self._is_phase_timed_out():
            return EscalationCondition.HR13_TIMEOUT

        # Gate score < min after gate_min_rounds (takes priority over LOW_CONFIDENCE)
        if context.gate_num is not None and context.gate_num != 4:
            score = context.details.get("score", 100)
            if score < self.gate_min_score and rounds >= self.gate_min_rounds:
                return EscalationCondition.GATE_SCORE_LOW

        # Confidence < threshold after max_rounds per problem
        max_for_type = self._max_rounds_for(context)
        if result.confidence < self.confidence_threshold and rounds >= max_for_type:
            return EscalationCondition.LOW_CONFIDENCE

        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def start_phase_timer(self, estimate_seconds: float) -> None:
        self._phase_start_time = time.time()
        self._phase_estimate = estimate_seconds

    def reset_rounds(self, problem_key: str) -> None:
        self._round_counters.pop(problem_key, None)

    def round_count(self, problem_key: str) -> int:
        return self._round_counters.get(problem_key, 0)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_fixes": len(self._all_results),
            "round_counters": dict(self._round_counters),
            "latest_results": [
                {
                    "problem_type": r.problem_type,
                    "success": r.success,
                    "confidence": r.confidence,
                    "escalation": r.escalation.value if r.escalation else None,
                }
                for r in self._all_results[-10:]
            ],
        }

    # ── internal ───────────────────────────────────────────────────────────

    def _human_condition_for(self, context: FixContext) -> EscalationCondition:
        source = context.source
        if "constitution" in source and "hardcoded_secrets" in context.problem_type:
            return EscalationCondition.HARDCODED_SECRETS
        if "constitution_as_code" in source:
            return EscalationCondition.HARD_RULE_VIOLATION
        if "kill_switch" in source:
            return EscalationCondition.KILL_SWITCH
        if context.gate_num == 4:
            return EscalationCondition.GATE4_BLOCKED
        if context.problem_type == "hard_rule_violation":
            return EscalationCondition.HARD_RULE_VIOLATION
        if context.problem_type == "hardcoded_secrets":
            return EscalationCondition.HARDCODED_SECRETS
        return EscalationCondition.HARD_RULE_VIOLATION  # conservative default for unknown HUMAN_REQUIRED

    def _max_rounds_for(self, context: FixContext) -> int:
        from core.auto_fix.classifier import CLASSIFICATION_TABLE

        key = f"{context.source}/{context.problem_type}"
        entry = CLASSIFICATION_TABLE.get(key, {})
        return entry.get("max_rounds", 3)

    def _is_phase_timed_out(self) -> bool:
        if self._phase_start_time is None or self._phase_estimate is None:
            return False
        elapsed = time.time() - self._phase_start_time
        return elapsed > self._phase_estimate * self.max_phase_time_multiplier

    def _check_integrity(self) -> float:
        """Read integrity score from .methodology/ state or kill_switch."""
        try:
            state_path = self.project_root / ".methodology" / "state.json"
            if state_path.exists():
                import json
                state = json.loads(state_path.read_text(encoding="utf-8"))
                return float(state.get("integrity", 100.0))
        except Exception:
            pass
        return 100.0

    @staticmethod
    def _files_for_context(context: FixContext) -> List[Path]:
        files = context.details.get("files", [])
        return [Path(f) if isinstance(f, str) else f for f in files]
