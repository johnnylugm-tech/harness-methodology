#!/usr/bin/env python3
"""
Phase Artifact Enforcer -- ASPICE traceability chain enforcement.

Validates that each phase's required artifacts exist and that
the ASPICE dependency chain (P1->P2->P3->...->P8) is intact.

Usage:
    from core.quality_gate.phase_artifact_enforcer import (
        Phase, PhaseArtifactRegistry,
    )

    registry = PhaseArtifactRegistry("/path/to/project")
    result = registry.verify_phase_link(Phase.SPECIFY, Phase.PLAN)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


class Phase(enum.Enum):
    """ASPICE pipeline phases aligned with CONSTITUTION.md SS2.3."""

    CONSTITUTION = 0
    SPECIFY = 1
    PLAN = 2
    IMPLEMENT = 3
    VERIFY = 4
    SYSTEM_TEST = 5
    QUALITY = 6
    RISK = 7
    CONFIG = 8
    MAINTENANCE = 9

    @classmethod
    def from_int(cls, n: int) -> "Phase":
        """Convert a phase integer (1-9) to a Phase enum member.

        Raises KeyError for unknown integers.  Phase 0 (CONSTITUTION) is
        intentionally excluded — it is an internal harness phase, not a
        user-facing pipeline phase.
        """
        _map: Dict[int, "Phase"] = {
            1: cls.SPECIFY,    2: cls.PLAN,       3: cls.IMPLEMENT,
            4: cls.VERIFY,     5: cls.SYSTEM_TEST, 6: cls.QUALITY,
            7: cls.RISK,       8: cls.CONFIG,     9: cls.MAINTENANCE,
        }
        if n not in _map:
            raise KeyError(f"No Phase enum for integer {n!r} (expected 1-9)")
        return _map[n]


@dataclass
class PhaseLinkResult:
    """Result of a phase dependency check."""

    from_phase: Phase
    to_phase: Phase
    passed: bool
    reason: str
    expected_artifacts: List[str] = field(default_factory=list)
    found_artifacts: List[str] = field(default_factory=list)
    missing_artifacts: List[str] = field(default_factory=list)
    # Finding #11: advisory-artifact warnings (recommended but not required).
    # Never block the phase link; UI surfaces them as yellow not red.
    advisory_warnings: List[str] = field(default_factory=list)


class PhaseArtifactRegistry:
    """Registry of required artifacts per phase with ASPICE dependency chain.

    Encodes the ASPICE principle: each phase's output becomes the next phase's input.
    Phase 1 input is the user-provided PRD (external, not checked here).
    """

    @property
    def PHASE_ARTIFACTS(self) -> Dict[Phase, Dict]:
        from core.utils.project_layout import ProjectLayout
        layout = ProjectLayout(self.project_root)
        
        return {
            Phase.SPECIFY: {
                "artifacts": [
                    layout.get_relative_str(layout.srs_path),
                    layout.get_relative_str(layout.spec_tracking_path),
                    layout.get_relative_str(layout.traceability_matrix_path),
                    layout.get_relative_str(layout.test_inventory_path),
                ],
                "depends_on": [],
            },
            Phase.PLAN: {
                "artifacts": [
                    layout.get_relative_str(layout.sad_path),
                ],
                "depends_on": [Phase.SPECIFY],
            },
            Phase.IMPLEMENT: {
                "artifacts": [
                    layout.get_relative_str(layout.active_src_dir),
                    layout.get_relative_str(layout.active_test_dir),
                ],
                "depends_on": [Phase.SPECIFY, Phase.PLAN],
            },
            Phase.VERIFY: {
                "artifacts": [
                    # TEST_PLAN.md moved to advisory below (Finding #11): no
                    # harness tool produces it; the P4 plan instructs the
                    # agent to write it manually. Hard-blocking on it traps
                    # agents that follow the plan verbatim and produce a
                    # well-formed but stub TEST_PLAN.md, then re-block on
                    # the validator. Treat it as a recommended artifact:
                    # agents are encouraged to write it (the P4 plan still
                    # tells them to), but its absence is a warning, not a
                    # gate block.
                    layout.get_relative_str(layout.test_results_path),
                ],
                "advisory": [
                    layout.get_relative_str(layout.test_plan_path),
                ],
                "depends_on": [Phase.IMPLEMENT],
            },
            Phase.SYSTEM_TEST: {
                "artifacts": [
                    layout.get_relative_str(layout.baseline_path),
                    layout.get_relative_str(layout.verification_report_path),
                ],
                "depends_on": [Phase.VERIFY],
            },
            Phase.QUALITY: {
                "artifacts": [
                    layout.get_relative_str(layout.quality_report_path),
                ],
                "depends_on": [Phase.SYSTEM_TEST],
            },
            Phase.RISK: {
                "artifacts": [
                    layout.get_relative_str(layout.risk_status_report_path),
                    layout.get_relative_str(layout.risk_register_path),
                ],
                "depends_on": [Phase.QUALITY],
            },
            Phase.CONFIG: {
                "artifacts": [
                    layout.get_relative_str(layout.config_records_path),
                    layout.get_relative_str(layout.release_checklist_path),
                ],
                "depends_on": [Phase.RISK],
            },
            Phase.MAINTENANCE: {
                "artifacts": [
                    layout.get_relative_str(layout.maintenance_log_path),
                ],
                "depends_on": [Phase.CONFIG],
            },
        }

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def verify_phase_link(self, from_phase: Phase, to_phase: Phase,
                          skip_to_side: bool = False) -> PhaseLinkResult:
        """Verify that from_phase's and to_phase's required artifacts exist.

        Invariant: when *entering* to_phase, its artifacts do not yet exist
        (they are the output of the phase we are about to execute).  Pass
        skip_to_side=True to suppress the existence check on the "to" side
        while still validating that all predecessor artifacts are in place.

        Checks:
        1. from_phase's required artifacts exist (predecessor produced output)
        2. to_phase's required artifacts exist (unless skip_to_side —
           the current phase has not yet produced them)

        The former check 3 (substring scan of the current artifact for the
        predecessor's filename) was removed — it was pure theatre (an agent
        passes it by pasting a filename keyword anywhere in the document).
        """
        from_info = self.PHASE_ARTIFACTS.get(from_phase, {})
        to_info = self.PHASE_ARTIFACTS.get(to_phase, {})

        from_artifacts: List[str] = from_info.get("artifacts", [])
        to_artifacts: List[str] = to_info.get("artifacts", [])
        # Advisory artifacts (Finding #11) are recommended but not required.
        # Their absence is recorded as a warning and bubbled up via
        # `advisory_warnings` on the result, but does NOT fail the phase
        # link check. `passed` stays True when only advisory items are
        # missing.
        from_advisory: List[str] = from_info.get("advisory", [])
        to_advisory: List[str] = to_info.get("advisory", [])

        found_from = [a for a in from_artifacts if (self.project_root / a).exists()]
        missing_from = [a for a in from_artifacts if not (self.project_root / a).exists()]
        found_to = [a for a in to_artifacts if (self.project_root / a).exists()]
        missing_to = [a for a in to_artifacts if not (self.project_root / a).exists()]

        # Advisory checks: surface as warnings, never block.
        advisory_warnings: List[str] = []
        for a in from_advisory:
            if not (self.project_root / a).exists():
                advisory_warnings.append(
                    f"{from_phase.name} advisory artifact missing: {a} "
                    f"(recommended but not required)"
                )
        for a in to_advisory:
            if not (self.project_root / a).exists():
                advisory_warnings.append(
                    f"{to_phase.name} advisory artifact missing: {a} "
                    f"(recommended but not required)"
                )

        # NOTE: the text "reference" check (substring scan of the current artifact
        # for the predecessor's filename) was removed — it was a pure-theatre check
        # an agent passes by pasting a filename keyword anywhere in the document.
        # Traceability now means: the predecessor and current artifacts actually exist.
        from_ok = len(found_from) > 0 or len(from_artifacts) == 0
        to_ok = len(found_to) > 0 or len(to_artifacts) == 0 or skip_to_side
        passed = from_ok and to_ok

        if not from_ok:
            reason = f"Previous phase {from_phase.name} missing artifacts: {missing_from}"
        elif not to_ok:
            reason = f"Current phase {to_phase.name} missing artifacts: {missing_to}"
        else:
            reason = f"Phase link {from_phase.name}->{to_phase.name} verified (artifact existence)"

        return PhaseLinkResult(
            from_phase=from_phase,
            to_phase=to_phase,
            passed=passed,
            reason=reason,
            expected_artifacts=list(set(from_artifacts + to_artifacts)),
            found_artifacts=list(set(found_from + found_to)),
            missing_artifacts=list(set(missing_from + missing_to)),
            advisory_warnings=advisory_warnings,
        )

    def verify_phase_chain(self, current_phase: int) -> Dict:
        """Verify the entire ASPICE chain up to current_phase.

        Returns dict with all_verified, verified_links, missing_links,
        advisory_warnings, stats. A top-level exception is caught and reported
        as a single failing link rather than crashing the caller.
        """
        phase_map = {
            Phase.CONSTITUTION: 0, Phase.SPECIFY: 1, Phase.PLAN: 2,
            Phase.IMPLEMENT: 3, Phase.VERIFY: 4, Phase.SYSTEM_TEST: 5,
            Phase.QUALITY: 6, Phase.RISK: 7, Phase.CONFIG: 8,
            Phase.MAINTENANCE: 9,
        }
        verified: List[str] = []
        missing: List[str] = []
        advisory_warnings: List[str] = []

        try:
            for phase in Phase:
                if phase_map.get(phase, 0) > current_phase:
                    continue
                is_current = phase_map.get(phase, 0) == current_phase
                for prev in self.PHASE_ARTIFACTS.get(phase, {}).get("depends_on", []):
                    if phase_map.get(prev, 0) >= current_phase:
                        continue
                    result = self.verify_phase_link(prev, phase,
                                                    skip_to_side=is_current)
                    entry = f"{prev.name}->{phase.name}: {result.reason}"
                    (verified if result.passed else missing).append(entry)
                    # Finding #11: collect advisory warnings across the whole
                    # chain so the operator sees all of them at once instead
                    # of one per phase.
                    advisory_warnings.extend(result.advisory_warnings)
        except Exception as exc:
            missing.append(f"CRASH: verify_phase_chain({current_phase}) — {type(exc).__name__}: {exc}")

        return {
            "all_verified": len(missing) == 0,
            "verified_links": verified,
            "missing_links": missing,
            "advisory_warnings": advisory_warnings,
            "stats": {
                "total": len(verified) + len(missing),
                "verified": len(verified),
                "missing": len(missing),
            },
        }
