#!/usr/bin/env python3
"""
Verification Gates - Verification Gates
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
from datetime import datetime


class GateStatus(Enum):
    NOT_REACHED = "not_reached"
    PASSED = "passed"
    FAILED = "failed"
    BYPASSED = "bypassed"


class Gate:
    """Single verification gate"""

    def __init__(self, name: str, required_output: Optional[str] = None,
                 validator: Optional[Callable[..., Any]] = None, auto_pass: bool = False):
        self.name = name
        self.required_output = required_output
        self.validator = validator
        self.auto_pass = auto_pass
        self.status = GateStatus.NOT_REACHED
        self.verified_at: Optional[datetime] = None
        self.evidence: Optional[Any] = None

    def check(self, context: dict) -> bool:
        if self.auto_pass:
            self.status = GateStatus.PASSED
            self.verified_at = datetime.now()
            self.evidence = {"auto_pass": True}
            return True
        if self.validator:
            try:
                result = self.validator(context)
                self.status = GateStatus.PASSED if result else GateStatus.FAILED
                self.verified_at = datetime.now()
                self.evidence = {"validator_result": result}
                return result
            except Exception as e:
                self.status = GateStatus.FAILED
                self.verified_at = datetime.now()
                self.evidence = {"error": str(e)}
                return False
        if self.required_output:
            if self.required_output in context:
                self.status = GateStatus.PASSED
                self.verified_at = datetime.now()
                self.evidence = {"output_found": self.required_output}
                return True
            self.status = GateStatus.NOT_REACHED
            return False
        return False

    def bypass(self, reason: Optional[str] = None):
        self.status = GateStatus.BYPASSED
        self.verified_at = datetime.now()
        self.evidence = {"bypass_reason": reason or "manual_bypass"}

    def reset(self):
        self.status = GateStatus.NOT_REACHED
        self.verified_at = None
        self.evidence = None


class VerificationGates:
    """Verification gate manager"""

    DEFAULT_GATES: dict[str, dict[str, Any]] = {
        "task_created": {"name": "Task Created", "required_output": "task_spec", "auto_pass": False},
        "agent_assigned": {"name": "Agent Assigned", "required_output": "assignment", "auto_pass": False},
        "output_generated": {"name": "Output Generated", "required_output": "result", "auto_pass": False},
        "quality_check": {"name": "Quality Check", "auto_pass": False},
        "human_approved": {"name": "Human Approved", "required_output": "approval", "auto_pass": False},
        "completed": {"name": "Task Completed", "required_output": "final_result", "auto_pass": False},
    }

    def __init__(self):
        self.gates: Dict[str, Gate] = {}
        self.gate_sequence: List[str] = []

    def register_gate(self, gate_id: str, gate: Gate):
        self.gates[gate_id] = gate

    def register_default_gates(self, gate_ids: Optional[List[str]] = None):
        if gate_ids is None:
            gate_ids = list(self.DEFAULT_GATES.keys())
        for gate_id in gate_ids:
            if gate_id in self.DEFAULT_GATES:
                config = self.DEFAULT_GATES[gate_id]
                self.register_gate(gate_id, Gate(
                    name=config["name"],
                    required_output=config.get("required_output"),
                    auto_pass=config.get("auto_pass", False)
                ))

    def execute_sequence(self, context: dict) -> dict:
        return {gid: self.gates[gid].check(context)
                for gid in self.gate_sequence if gid in self.gates}

    def check_gate(self, gate_id: str, context: dict) -> bool:
        gate = self.gates.get(gate_id)
        return gate.check(context) if gate else False

    def get_status(self) -> dict:
        return {gid: {"name": g.name, "status": g.status.value,
                      "verified_at": g.verified_at.isoformat() if g.verified_at else None,
                      "evidence": g.evidence}
                for gid, g in self.gates.items()}

    def get_passed_count(self) -> int:
        return sum(1 for g in self.gates.values() if g.status == GateStatus.PASSED)

    def get_failed_count(self) -> int:
        return sum(1 for g in self.gates.values() if g.status == GateStatus.FAILED)

    def reset_all(self):
        for gate in self.gates.values():
            gate.reset()


class HITLGates(VerificationGates):
    def __init__(self):
        super().__init__()
        self.gate_sequence = ["task_created", "output_generated", "human_approved", "completed"]
        self.register_default_gates(self.gate_sequence)


class AutonomousGates(VerificationGates):
    def __init__(self):
        super().__init__()
        self.gate_sequence = ["task_created", "agent_assigned", "output_generated",
                               "quality_check", "completed"]
        self.register_default_gates(self.gate_sequence)


# ---------------------------------------------------------------------------
# Gate Remediation Report
# ---------------------------------------------------------------------------

#: Per-gate default thresholds (score 0–100).
_GATE_THRESHOLDS: Dict[int, float] = {1: 70.0, 2: 75.0, 3: 80.0, 4: 85.0}

#: Per-gate generic action templates when score is below threshold.
_GATE_ACTION_TEMPLATES: Dict[int, List[str]] = {
    1: [
        "Review FR spec — ensure acceptance criteria are unambiguous",
        "Check unit-test coverage for this FR (target ≥ 70 %)",
        "Re-run Gate-1 validator after fixing test gaps",
        "Consult FR traceability matrix for missing implementation links",
    ],
    2: [
        "Review SSI round report — identify lowest-scoring dimensions",
        "Run 'ruff check --fix' to clear linting violations (D1)",
        "Run 'mypy --strict' and fix type errors (D2)",
        "Add or fix unit tests to raise coverage above 70 % (D3)",
        "Run 'bandit -r .' and resolve HIGH/CRITICAL issues (D5)",
        "Re-run SSI round after each fix and check incremental progress",
    ],
    3: [
        "Check pytest collection — ensure all test files are discovered",
        "Raise branch coverage to target (≥ 80 %)",
        "Review integration-test stubs — add real assertions",
        "Resolve any flaky tests causing intermittent failures",
        "Run full test suite locally before re-triggering Gate 3",
    ],
    4: [
        "Review Gate-4 composite score breakdown — identify failing dimensions",
        "Fix any CRITICAL or HIGH constitution violations first",
        "Re-run SSI 3-round cycle on failing modules",
        "Verify BASELINE.md is current (P5 checkpoint not stale)",
        "Check risk register for unmitigated HIGH risks that lower score",
    ],
}


@dataclass
class GateRemediationReport:
    """
    Structured remediation report generated when a harness gate fails.

    Attach to ``HANDOVER.md`` via ``HandoverGenerator`` so the next session
    knows exactly what to fix.

    Parameters
    ----------
    gate_num:
        Failing gate number (1–4).
    phase:
        Pipeline phase at failure.
    score:
        Actual composite score (0–100).
    threshold:
        Minimum passing score.  ``None`` uses the default for *gate_num*.
    failing_checks:
        Optional list of specific check names / dimension names that failed
        (e.g. ``["D3_Coverage", "D5_Security"]``).  Surfaced in the report.
    gate_evidence:
        Optional raw ``Gate.evidence`` dict from ``VerificationGates``.
    """

    gate_num: int
    phase: int
    score: float
    threshold: Optional[float] = None
    failing_checks: List[str] = field(default_factory=list)
    gate_evidence: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def effective_threshold(self) -> float:
        return self.threshold if self.threshold is not None else _GATE_THRESHOLDS.get(
            self.gate_num, 70.0
        )

    @property
    def gap(self) -> float:
        """How many points below threshold."""
        return max(0.0, self.effective_threshold - self.score)

    def action_items(self) -> List[str]:
        """
        Return ordered action items for the next session.

        Failing-check–specific items come first; generic gate items follow.
        """
        items: List[str] = []
        # Specific check items
        for check in self.failing_checks:
            items.append(f"Fix failing check: **{check}** (score={self.score:.1f})")
        # Generic gate actions
        items.extend(_GATE_ACTION_TEMPLATES.get(self.gate_num, [
            f"Investigate Gate {self.gate_num} failure — review score report",
            "Re-run gate after fixing identified issues",
        ]))
        return items

    def to_status_string(self) -> str:
        """
        One-paragraph status suitable for ``HandoverGenerator.current_status``.
        """
        checks_str = (
            f"Failing checks: {', '.join(self.failing_checks)}. "
            if self.failing_checks else ""
        )
        evidence_str = ""
        if self.gate_evidence:
            evidence_str = f"Evidence: {self.gate_evidence}. "
        return (
            f"Gate {self.gate_num} FAILED: score={self.score:.1f} "
            f"(threshold={self.effective_threshold:.1f}, "
            f"gap={self.gap:.1f}). "
            f"{checks_str}{evidence_str}"
            f"Phase={self.phase}."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable dict for logging / decision log integration."""
        return {
            "gate_num": self.gate_num,
            "phase": self.phase,
            "score": round(self.score, 2),
            "threshold": self.effective_threshold,
            "gap": round(self.gap, 2),
            "failing_checks": self.failing_checks,
            "action_items": self.action_items(),
            "gate_evidence": self.gate_evidence,
        }
