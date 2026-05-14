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


class PhaseArtifactRegistry:
    """Registry of required artifacts per phase with ASPICE dependency chain.

    Encodes the ASPICE principle: each phase's output becomes the next phase's input.
    Phase 1 input is the user-provided PRD (external, not checked here).
    """

    PHASE_ARTIFACTS: Dict[Phase, Dict] = {
        Phase.SPECIFY: {
            "artifacts": [
                "01-requirements/SRS.md",
                "01-requirements/SPEC_TRACKING.md",
                "01-requirements/TRACEABILITY_MATRIX.md",
            ],
            "depends_on": [],
        },
        Phase.PLAN: {
            "artifacts": [
                "02-architecture/SAD.md",
            ],
            "depends_on": [Phase.SPECIFY],
        },
        Phase.IMPLEMENT: {
            "artifacts": [
            ],
            "depends_on": [Phase.SPECIFY, Phase.PLAN],
        },
        Phase.VERIFY: {
            "artifacts": [
                "04-testing/TEST_PLAN.md",
                "04-testing/TEST_RESULTS.md",
            ],
            "depends_on": [Phase.IMPLEMENT],
        },
        Phase.SYSTEM_TEST: {
            "artifacts": [
                "05-verify/BASELINE.md",
                "05-verify/VERIFICATION_REPORT.md",
            ],
            "depends_on": [Phase.VERIFY],
        },
        Phase.QUALITY: {
            "artifacts": [
                "06-quality/QUALITY_REPORT.md",
            ],
            "depends_on": [Phase.SYSTEM_TEST],
        },
        Phase.RISK: {
            "artifacts": [
                "07-risk/RISK_ASSESSMENT.md",
                "07-risk/RISK_REGISTER.md",
            ],
            "depends_on": [Phase.QUALITY],
        },
        Phase.CONFIG: {
            "artifacts": [
                "08-config/CONFIG_RECORDS.md",
                "08-config/RELEASE_CHECKLIST.md",
            ],
            "depends_on": [Phase.RISK],
        },
    }

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def verify_phase_link(self, from_phase: Phase, to_phase: Phase,
                          skip_to_side: bool = False) -> PhaseLinkResult:
        """Verify that to_phase's artifacts exist and reference from_phase.

        Invariant: when *entering* to_phase, its artifacts do not yet exist
        (they are the output of the phase we are about to execute).  Pass
        skip_to_side=True to suppress the existence + traceability checks on
        the "to" side while still validating that all predecessor artifacts
        are in place.

        Checks:
        1. from_phase's required artifacts exist (predecessor produced output)
        2. to_phase's required artifacts exist (unless skip_to_side —
           the current phase has not yet produced them)
        3. At least one to_phase artifact references from_phase artifacts
           (skipped when to_phase artifacts don't exist yet, i.e. when
           skip_to_side is True)
        """
        from_info = self.PHASE_ARTIFACTS.get(from_phase, {})
        to_info = self.PHASE_ARTIFACTS.get(to_phase, {})

        from_artifacts: List[str] = from_info.get("artifacts", [])
        to_artifacts: List[str] = to_info.get("artifacts", [])

        found_from = [a for a in from_artifacts if (self.project_root / a).exists()]
        missing_from = [a for a in from_artifacts if not (self.project_root / a).exists()]
        found_to = [a for a in to_artifacts if (self.project_root / a).exists()]
        missing_to = [a for a in to_artifacts if not (self.project_root / a).exists()]

        ref_found = False
        for artifact_path in found_to:
            full_path = self.project_root / artifact_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="ignore")
                    for prev_artifact in from_artifacts:
                        prev_name = Path(prev_artifact).stem
                        if prev_name.lower() in content.lower():
                            ref_found = True
                            break
                except Exception:
                    pass
            if ref_found:
                break

        from_ok = len(found_from) > 0 or len(from_artifacts) == 0
        to_ok = len(found_to) > 0 or len(to_artifacts) == 0 or skip_to_side
        ref_ok = ref_found or len(from_artifacts) == 0 or len(to_artifacts) == 0 or skip_to_side
        passed = from_ok and to_ok and ref_ok

        if not from_ok:
            reason = f"Previous phase {from_phase.name} missing artifacts: {missing_from}"
        elif not to_ok:
            reason = f"Current phase {to_phase.name} missing artifacts: {missing_to}"
        elif not ref_ok:
            reason = f"No traceability reference from {to_phase.name} to {from_phase.name}"
        else:
            reason = f"Phase link {from_phase.name}->{to_phase.name} verified"

        return PhaseLinkResult(
            from_phase=from_phase,
            to_phase=to_phase,
            passed=passed,
            reason=reason,
            expected_artifacts=list(set(from_artifacts + to_artifacts)),
            found_artifacts=list(set(found_from + found_to)),
            missing_artifacts=list(set(missing_from + missing_to)),
        )

    def verify_phase_chain(self, current_phase: int) -> Dict:
        """Verify the entire ASPICE chain up to current_phase.

        Returns dict with all_verified, verified_links, missing_links, stats.
        """
        phase_map = {
            Phase.CONSTITUTION: 0, Phase.SPECIFY: 1, Phase.PLAN: 2,
            Phase.IMPLEMENT: 3, Phase.VERIFY: 4, Phase.SYSTEM_TEST: 5,
            Phase.QUALITY: 6, Phase.RISK: 7, Phase.CONFIG: 8,
        }
        verified: List[str] = []
        missing: List[str] = []

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

        return {
            "all_verified": len(missing) == 0,
            "verified_links": verified,
            "missing_links": missing,
            "stats": {
                "total": len(verified) + len(missing),
                "verified": len(verified),
                "missing": len(missing),
            },
        }
