"""
phase_paths.py — Artifact path registry per phase.

Stub module: provides PHASE_ARTIFACT_PATHS used by PhaseTruthVerifier.
The verifier builds its own inline checklist and only imports this
symbol to avoid NameError; actual path resolution is inlined there.
"""
from typing import Dict, List

PHASE_ARTIFACT_PATHS: Dict[int, List[str]] = {
    1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
    2: ["02-architecture/SAD.md"],
    3: ["03-development/src/", "03-development/tests/", "03-development/COMPLIANCE_MATRIX.md"],
    4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
    5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md"],
    6: ["06-quality/QUALITY_REPORT.md"],
    7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
    8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
}
