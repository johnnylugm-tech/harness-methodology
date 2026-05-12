#!/usr/bin/env python3
"""
Verification Gates — Gate Remediation Report.

Generates structured gate-failure diagnosis for HANDOVER.md crash recovery.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


#: Per-gate default thresholds (score 0–100).
_GATE_THRESHOLDS: Dict[int, float] = {1: 75.0, 2: 75.0, 3: 80.0, 4: 85.0}

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
        Optional raw evidence dict.
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
        for check in self.failing_checks:
            items.append(f"Fix failing check: **{check}** (score={self.score:.1f})")
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
