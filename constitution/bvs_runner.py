"""BVS Runner — Behavioral Verification System phase-order invariant checker.

Checks that the project FSM state in .methodology/state.json is consistent
with the requested phase before each SteeringLoop iteration (HR-03).

Used by:
    steering/integrations.py :: SteeringIntegrator.bvs_integrator

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
from typing import Any


class BVSRunner:
    """Phase-order invariant checker based on .methodology/state.json."""

    # Minimum phase prerequisites: key phase must have key-1 complete first
    PHASE_PREREQUISITES: dict[int, int] = {
        2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7,
    }

    def __init__(self, project_path: str, phase: int = 1) -> None:
        """Initialize with project root and target phase."""
        self.project_path = Path(project_path)
        self.phase = phase
        self.state_path = self.project_path / ".methodology" / "state.json"

    def run(self) -> dict[str, Any]:
        """
        Run phase-order invariant checks (HR-03).

        Returns:
            Report dict: {passed, total_violations, violations}
        """
        violations: list[dict[str, str]] = []

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

    def _load_state(self) -> dict[str, Any] | None:
        """Load .methodology/state.json, returning None if absent or malformed."""
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
