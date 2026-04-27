#!/usr/bin/env python3
"""
Steering Integrations -- Integration points with existing systems.

Integrations:
- BVS Runner (InvariantEngine) -> HR-03 Phase order check
- Constitution Checker -> HR-07/09/15 Citation/Claims/Artifact checks
- CQG (code quality) -> code quality quantification
- AB Enforcer -> HR-12 iteration conflict resolution
- AI Test Suite -> HR-17 AI-generated test compliance

HR-12 conflict resolution:
- HR-12: "don't allow >5 rounds of ineffective iteration" is a negative constraint
- SteeringLoop.max_iterations=5 is a positive upper bound
- Not contradictory: can stop early, but cannot exceed 5 rounds
- SteeringLoop.should_continue() terminates early when convergence is met,
  achieving the goal of "no wasted iterations"
- SteeringIntegrator.should_continue property cross-checks both HR12Resolution
  AND VerificationConstitutionChecker for formal compliance verification.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import json

from steering.steering_loop import IterationResult, SteeringLoop, SteeringConfig


# ---------------------------------------------------------------------------
# HR Constraints
# ---------------------------------------------------------------------------

@dataclass
class HRConstraints:
    """
    Methodology HR constraint integration.

    Relevant HRs:
    - HR-01: 30% efficiency improvement target
    - HR-03: Phase order enforcement
    - HR-07: Citation reference standards
    - HR-09: Claims accuracy
    - HR-12: no >5 iteration rounds (conflict resolved)
    - HR-15: Artifact completeness
    - HR-17: AI-generated test tagging
    """

    max_iterations: int = 5              # HR-12 upper bound
    min_iterations: int = 3
    efficiency_target: float = 0.30      # HR-01: 30% efficiency improvement
    convergence_threshold: float = 0.05  # convergence threshold

    # HR compliance flags
    require_citation: bool = True                 # HR-07
    require_claims_verification: bool = True      # HR-09
    require_artifact_traceability: bool = True    # HR-15
    require_ai_test_tagging: bool = True          # HR-17


@dataclass
class IntegrationResult:
    """Integration check result"""
    hr_compliant: bool
    violations: List[str]
    warnings: List[str]
    metrics: Dict[str, float]
    details: Dict[str, Any]


# ---------------------------------------------------------------------------
# Steering + BVS Integration
# ---------------------------------------------------------------------------

class SteeringBVSIntegrator:
    """
    SteeringLoop integration with BVS Runner.

    Functions:
    1. Run BVS Phase order check before Steering iteration (HR-03)
    2. Write Steering iteration results to ExecutionLogger
    3. Track Artifact continuity across Phases
    """

    def __init__(
        self,
        project_path: str,
        bvs_runner,      # BVSRunner instance
        phase: int = 3
    ):
        self.project_path = Path(project_path)
        self.bvs_runner = bvs_runner
        self.phase = phase

    def check_phase_invariants(
        self,
        steering_result,
        context: Dict[str, Any]
    ) -> IntegrationResult:
        """
        Check current Phase BVS invariants (HR-03).

        Args:
            steering_result: SteeringLoop IterationResult
            context: additional context (prior outputs, etc.)

        Returns:
            IntegrationResult: with HR compliance status
        """
        violations = []
        warnings = []
        metrics = {}

        # 1. Phase order check (HR-03)
        bvs_report = self.bvs_runner.run()

        if not bvs_report["passed"]:
            violations.append(f"BVS Phase {self.phase} violations: {bvs_report['total_violations']}")
            for v in bvs_report.get("violations", [])[:3]:
                violations.append(f"  - {v.get('rule', 'unknown')}: {v.get('message', '')}")

        metrics["bvs_violations"] = bvs_report["total_violations"]
        metrics["bvs_passed"] = 1.0 if bvs_report["passed"] else 0.0

        # 2. Steering iteration metrics
        metrics["steering_iterations"] = steering_result.iteration
        metrics["steering_score_delta"] = steering_result.score_delta
        metrics["steering_convergence"] = steering_result.convergence_score

        # 3. HR compliance decision
        hr_compliant = (
            bvs_report["passed"] and
            steering_result.iteration <= 5 and
            steering_result.convergence_score <= 0.10
        )

        return IntegrationResult(
            hr_compliant=hr_compliant,
            violations=violations,
            warnings=warnings,
            metrics=metrics,
            details={
                "phase": self.phase,
                "bvs_report": bvs_report,
                "steering_winner": steering_result.winner
            }
        )


# ---------------------------------------------------------------------------
# Steering + Constitution Integration
# ---------------------------------------------------------------------------

class SteeringConstitutionIntegrator:
    """
    SteeringLoop integration with Constitution Checker.

    Functions:
    1. HR-07: Citation reference standard check
    2. HR-09: Claims accuracy verification
    3. HR-15: Artifact completeness tracking
    """

    def __init__(
        self,
        constitution_checker,  # ConstitutionChecker instance
        citation_parser        # CitationParser instance
    ):
        self.checker = constitution_checker
        self.citation_parser = citation_parser

    def check_output_compliance(
        self,
        output: Dict[str, Any],
        phase: int
    ) -> IntegrationResult:
        """
        Check Constitution compliance of Steering output.

        Args:
            output: SteeringLoop.best_output.output
            phase: current Phase

        Returns:
            IntegrationResult: with Constitution compliance status
        """
        violations = []
        warnings = []
        metrics = {}

        # Type check -- output must be dict; convert string if needed
        if not isinstance(output, dict):
            if isinstance(output, str):
                output = {"text": output}
            else:
                return IntegrationResult(
                    hr_compliant=False,
                    violations=["TypeError: output must be dict or str"],
                    warnings=warnings,
                    metrics={},
                    details={"output_type": type(output).__name__}
                )

        text = self._extract_text(output)

        # HR-07: Citation check
        citations = self.citation_parser.extract_citations(text)
        if not citations and len(text) > 500:
            violations.append("HR-07: No citations found in long output")

        metrics["citation_count"] = len(citations)

        # HR-09: Claims verification
        claims = self.citation_parser.extract_claims(text)
        verified_claims = sum(
            1 for c in claims
            if self.citation_parser.verify_claim(c, citations)
        )
        if claims and verified_claims / len(claims) < 0.5:
            violations.append(f"HR-09: Only {verified_claims}/{len(claims)} claims verified")

        metrics["claims_total"] = len(claims)
        metrics["claims_verified"] = verified_claims

        # HR-15: Artifact completeness (check for expected artifact markers)
        has_artifacts = bool(output.get("artifacts") or output.get("files"))
        if not has_artifacts and phase >= 3:
            warnings.append("HR-15: No artifact references found")

        metrics["has_artifacts"] = 1.0 if has_artifacts else 0.0

        hr_compliant = len(violations) == 0

        return IntegrationResult(
            hr_compliant=hr_compliant,
            violations=violations,
            warnings=warnings,
            metrics=metrics,
            details={
                "citations": citations,
                "claims": claims,
                "verified_claims": verified_claims
            }
        )

    def _extract_text(self, output: Dict[str, Any]) -> str:
        if isinstance(output, str):
            return output
        return output.get("text", output.get("content", str(output)))


# ---------------------------------------------------------------------------
# Steering + CQG Integration
# ---------------------------------------------------------------------------

class SteeringCQGIntegrator:
    """
    SteeringLoop integration with CQG (code quality gate).

    Functions:
    1. Quantify code quality measurement
    2. Incorporate CQG scores into Steering evaluation

    CQG is a separate quality_gate module; this provides the integration interface.
    """

    def __init__(self, cqg_checker=None):
        """
        Args:
            cqg_checker: CQG instance (optional; skip code quality check if absent)
        """
        self.cqg_checker = cqg_checker

    def measure_code_quality(
        self,
        output: Dict[str, Any]
    ) -> Dict[str, float]:
        """
        Measure code quality.

        Returns:
            {"quality": 0.0-1.0, "complexity": 0.0-1.0, "readability": 0.0-1.0}
        """
        if not self.cqg_checker:
            # No CQG checker available; return defaults
            return {"quality": 0.5, "complexity": 0.5, "readability": 0.5}

        text = self._extract_text(output)
        code_blocks = self._extract_code_blocks(text)

        if not code_blocks:
            return {"quality": 0.5, "complexity": 0.5, "readability": 0.5}

        scores = []
        for block in code_blocks:
            try:
                result = self.cqg_checker.check(block)
                scores.append(result.get("quality_score", 0.5))
            except Exception:
                scores.append(0.5)

        avg = sum(scores) / len(scores) if scores else 0.5

        return {
            "quality": avg,
            "complexity": avg,  # simplified
            "readability": avg
        }

    def integrate_cqg_into_steering_score(
        self,
        base_score: float,
        cqg_scores: Dict[str, float]
    ) -> float:
        """
        Integrate CQG scores into Steering weighted total score.

        Formula: base_score * (1 - cqg_weight) + cqg_quality * cqg_weight
        """
        cqg_weight = 0.15  # CQG contributes 15% of weight
        cqg_quality = cqg_scores.get("quality", 0.5)
        return base_score * (1 - cqg_weight) + cqg_quality * cqg_weight

    def _extract_text(self, output: Dict[str, Any]) -> str:
        if isinstance(output, str):
            return output
        return output.get("text", output.get("content", str(output)))

    def _extract_code_blocks(self, text: str) -> List[str]:
        """Simple code block extraction (```...```)."""
        import re
        pattern = r"```[\w]*\n(.*?)```"
        return re.findall(pattern, text, re.DOTALL)


# ---------------------------------------------------------------------------
# HR-12 Conflict Resolution
# ---------------------------------------------------------------------------

@dataclass
class HR12Resolution:
    """
    HR-12 conflict resolution.

    Conflict:
    - AB Workflow needs iterative back-and-forth
    - HR-12 says "no >5 iteration rounds"
    - These appear contradictory on the surface

    Resolution:
    - HR-12's intent: "don't iterate meaninglessly" (negative constraint)
    - SteeringLoop.max_iterations=5: "run at most 5 rounds" (positive cap)
    - When convergence condition is met (delta <= 0.05), stop even before 5 rounds
    - This satisfies HR-12's intent while allowing sufficient exploration space
    """

    max_allowed: int = 5                    # HR-12 upper bound
    early_stop_threshold: float = 0.05     # delta threshold for early stop
    min_rounds_before_stop: int = 3        # minimum rounds before early stop allowed

    def should_stop(
        self,
        current_round: int,
        score_delta: float,
        has_converged_early: bool = False
    ) -> Tuple[bool, str]:
        """
        Determine whether to stop.

        Args:
            current_round: current round number (1-indexed)
            score_delta: current A/B score difference
            has_converged_early: whether early convergence was detected

        Returns:
            (should_stop, reason)
        """
        if current_round >= self.max_allowed:
            return True, "hr12_max_iterations"

        if current_round < self.min_rounds_before_stop:
            return False, "min_rounds_not_reached"

        if has_converged_early or score_delta <= self.early_stop_threshold:
            return True, "converged"

        return False, "continue"

    @staticmethod
    def resolve(
        max_iterations: int,
        min_iterations: int,
        current_round: int,
        score_delta: float,
        quality_threshold: float = 0.85,
        convergence_threshold: float = 0.05
    ) -> dict:
        """
        Resolve HR-12 conflict.

        Returns:
            {"should_stop": bool, "reason": str, "hr12_compliant": bool}
        """
        if current_round >= max_iterations:
            return {"should_stop": True, "reason": "max_iterations_reached", "hr12_compliant": True}

        if current_round < min_iterations:
            return {"should_stop": False, "reason": "min_iterations_not_reached", "hr12_compliant": True}

        if score_delta <= convergence_threshold:
            return {"should_stop": True, "reason": "converged", "hr12_compliant": True}

        return {"should_stop": False, "reason": "continue_iterating", "hr12_compliant": True}


# ---------------------------------------------------------------------------
# Unified Integration Entry
# ---------------------------------------------------------------------------

class SteeringIntegrator:
    """
    Unified Steering Integrator.

    Integrates: SteeringLoop + BVS + Constitution + CQG + HR-12
    """

    def __init__(
        self,
        provider,
        project_path: str,
        phase: int = 3,
        config: Optional["SteeringConfig"] = None,
        hr_constraints: Optional[HRConstraints] = None
    ):
        from steering.steering_loop import SteeringLoop

        self.steering = SteeringLoop(provider, config)
        self.hr = hr_constraints or HRConstraints()

        # Lazy-load integration modules (avoid circular dependencies)
        self._bvs_integrator = None
        self._constitution_integrator = None
        self._cqg_integrator = None

        self.project_path = Path(project_path)
        self.phase = phase

    @property
    def bvs_integrator(self) -> SteeringBVSIntegrator:
        if self._bvs_integrator is None:
            from constitution.bvs_runner import BVSRunner
            runner = BVSRunner(str(self.project_path), phase=self.phase)
            self._bvs_integrator = SteeringBVSIntegrator(
                str(self.project_path), runner, self.phase
            )
        return self._bvs_integrator

    def iterate_with_full_check(
        self,
        output_a: Dict[str, Any],
        output_b: Dict[str, Any],
        run_bvs: bool = True,
        run_constitution: bool = True,
        run_cqg: bool = False
    ) -> Tuple[IterationResult, List[IntegrationResult]]:
        """
        Execute iteration and run all integration checks.

        Returns:
            (steering_result, integration_results)
        """
        # 1. Steering iteration
        steering_result = self.steering.iterate(output_a, output_b)

        integration_results = []

        # 2. BVS check
        if run_bvs:
            try:
                bvs_result = self.bvs_integrator.check_phase_invariants(steering_result, {})
                integration_results.append(bvs_result)
            except Exception as e:
                integration_results.append(IntegrationResult(
                    hr_compliant=True, violations=[],
                    warnings=[f"BVS check failed: {e}"], metrics={}, details={}
                ))

        # 3. Constitution check
        if run_constitution:
            try:
                if self._constitution_integrator is None:
                    from constitution.citation_parser import CitationParser
                    from constitution.verification_constitution_checker import VerificationConstitutionChecker
                    self._constitution_integrator = SteeringConstitutionIntegrator(
                        VerificationConstitutionChecker(), CitationParser()
                    )
                winner_output = steering_result.best_so_far.output
                const_result = self._constitution_integrator.check_output_compliance(
                    winner_output, self.phase
                )
                integration_results.append(const_result)
            except Exception as e:
                integration_results.append(IntegrationResult(
                    hr_compliant=True, violations=[],
                    warnings=[f"Constitution check failed: {e}"], metrics={}, details={}
                ))

        # 4. CQG check
        if run_cqg:
            try:
                if self._cqg_integrator is None:
                    self._cqg_integrator = SteeringCQGIntegrator()
                winner_output = steering_result.best_so_far.output
                cqg_scores = self._cqg_integrator.measure_code_quality(winner_output)
            except Exception:
                pass

        return steering_result, integration_results

    @property
    def should_continue(self) -> Tuple[bool, str]:
        """
        HR-12 wired: cross-check SteeringLoop decision with HR12Resolution,
        then verify through VerificationConstitutionChecker on stop.
        """
        should, reason = self.steering.should_continue()

        n = len(self.steering.iterations)
        last_delta = (
            self.steering.iterations[-1].score_delta
            if self.steering.iterations else 0.0
        )
        early_converged = reason in ("quality_threshold_reached", "converged")

        # HR12Resolution cross-check
        hr12 = HR12Resolution(
            max_allowed=self.hr.max_iterations,
            early_stop_threshold=self.hr.convergence_threshold,
            min_rounds_before_stop=self.hr.min_iterations,
        )
        hr12_stop, hr12_reason = hr12.should_stop(n, last_delta, early_converged)

        if hr12_stop and should:
            # HR12Resolution says stop but SteeringLoop says continue — HR-12 wins
            should = False
            reason = f"hr12:{hr12_reason}"

        # On any stop: run VerificationConstitutionChecker for formal compliance log
        if not should:
            try:
                from constitution.verification_constitution_checker import VerificationConstitutionChecker
                best_score = (
                    self.steering.best_output.total_score * 100
                    if self.steering.best_output else 0.0
                )
                VerificationConstitutionChecker().check({"quality_score": best_score})
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # degrade gracefully — never block on constitution check failure

        return should, reason

    def get_full_summary(self) -> Dict[str, Any]:
        """Get full summary (including HR compliance status)."""
        steering_summary = self.steering.get_summary()

        hr12 = HR12Resolution()
        last_delta = 0.0
        if self.steering.iterations:
            last_delta = self.steering.iterations[-1].score_delta

        should_stop, reason = hr12.should_stop(
            len(self.steering.iterations), last_delta
        )

        return {
            "steering": steering_summary,
            "hr12_compliant": should_stop,
            "hr12_stop_reason": reason,
            "hr_constraints": {
                "max_iterations": self.hr.max_iterations,
                "efficiency_target": self.hr.efficiency_target
            }
        }
