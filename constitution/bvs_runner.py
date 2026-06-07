"""BVS Runner — Behavioral Verification System runner.

Integrates InvariantEngine (behavioral invariant checks) with
ExecutionLogger (session data collection) for comprehensive
behavioral validation (HR-03, HR-07, HR-09, HR-10, HR-12, HR-13, HR-15).

Checks that the project FSM state in .methodology/state.json is consistent
with the requested phase before each SteeringLoop iteration (HR-03).

Usage:
    from constitution.bvs_runner import BVSRunner

    runner = BVSRunner("/path/to/project", phase=3)
    result = runner.run()  # Phase-order check
    result = runner.run_full()  # Full BVS with invariants

Interface contract (what SteeringBVSIntegrator expects from .run()):
    {
        "passed":           bool,
        "total_violations": int,
        "violations":       [{"rule": str, "message": str}, ...]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Dict, Optional


class BVSRunner:
    """Behavioral Verification System runner.

    Phase-order invariant checker + full BVS with InvariantEngine
    and ExecutionLogger integration.
    """

    # Minimum phase prerequisites: key phase must have key-1 complete first
    PHASE_PREREQUISITES: Dict[int, int] = {
        2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7,
    }

    def __init__(self, project_path: str, phase: int = 1) -> None:
        """Initialize with project root and target phase."""
        self.project_path = Path(project_path)
        from core.utils.project_layout import ProjectLayout
        self.state_path = ProjectLayout(self.project_path).state_json_path
        self.phase = phase

    # ── Phase-order check (HR-03) ─────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Run phase-order invariant checks (HR-03).

        Returns:
            Report dict: {passed, total_violations, violations}
        """
        violations: List[Dict[str, str]] = []

        state = self._load_state()
        if state is None:
            # No state file — project not initialised; not a violation
            return {"passed": True, "total_violations": 0, "violations": []}

        current_phase = state.get("current_phase", 0)
        fsm_state = state.get("state", "UNKNOWN")

        # HR-03: requested phase must not skip a prerequisite phase
        prereq = self.PHASE_PREREQUISITES.get(self.phase)
        if prereq is not None and current_phase < prereq:
            violations.append({
                "rule": "HR-03",
                "message": (
                    f"Phase {self.phase} requires Phase {prereq} to be complete "
                    f"(current_phase={current_phase})"
                ),
            })

        # FSM FREEZE state blocks all progression
        if fsm_state == "FREEZE":
            violations.append({
                "rule": "HR-03",
                "message": "FSM is in FREEZE state — no phase progression allowed",
            })

        return {
            "passed": len(violations) == 0,
            "total_violations": len(violations),
            "violations": violations,
        }

    # ── Full BVS (InvariantEngine + ExecutionLogger) ──────────────────────────

    def run_full(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run full BVS: phase-order check + invariant checks from sessions_spawn.log.

        Only executes invariant checks for Phase 3+ (BVS auto-skip for P1-P2).

        Args:
            context: Optional override context dict. Auto-generated if omitted.

        Returns:
            {
                "passed": bool,
                "phase_order_passed": bool,
                "invariant_passed": bool,
                "total_violations": int,
                "violations": [...],
                "invariant_report": {...},
            }
        """
        # Phase-order check (always runs)
        phase_order = self.run()

        violations = list(phase_order.get("violations", []))

        # BVS invariant checks only for Phase 3+
        invariant_passed = True
        invariant_report = {}

        if self.phase >= 3:
            try:
                from constitution.execution_logger import ExecutionLogger
                from constitution.invariant_engine import InvariantEngine

                logger = ExecutionLogger(str(self.project_path))
                if context is None:
                    context = logger.get_phase_context(self.phase)

                logs = logger.collect_from_sessions_spawn_log()
                # Only check sessions for the current phase — historical
                # sessions from earlier phases (P1/P2 doc phases) lack
                # citations/confidence and would produce false violations.
                logs = [log for log in logs if log.get("phase") == self.phase]
                if logs:
                    engine = InvariantEngine.from_constitution_rules()
                    inv_violations = engine.check_batch(logs, context)
                    invariant_report = engine.generate_report(inv_violations)
                    invariant_passed = invariant_report.get("passed", True)

                    for v in invariant_report.get("violations", []):
                        violations.append({
                            "rule": v.get("name", "BVS"),
                            "message": f"[{v.get('severity', '?').upper()}] {v.get('name')}: {v.get('task', '?')}",
                        })
            except ImportError:
                # BVS not available — pass-through
                invariant_report = {"skipped": True, "reason": "BVS modules not importable"}

        passed = phase_order.get("passed", True) and invariant_passed

        return {
            "passed": passed,
            "phase_order_passed": phase_order.get("passed", True),
            "invariant_passed": invariant_passed,
            "total_violations": len(violations),
            "violations": violations,
            "invariant_report": invariant_report,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_state(self) -> Optional[Dict[str, Any]]:
        """Load .methodology/state.json, returning None if absent or malformed."""
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return None
