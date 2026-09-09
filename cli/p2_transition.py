"""Deterministic Phase 2 transition contract."""

from __future__ import annotations

from pathlib import Path


def precheck_p2_transition(project, completed_phase: int) -> "int | None":
    """Block P3 entry on unresolved decisions, property gaps, or due artifacts."""
    if completed_phase != 2:
        return None
    from core.quality_gate.decision_issues import decision_issue_findings
    from core.quality_gate.property_check import property_mapping_findings
    from core.quality_gate.required_artifacts import declared_artifact_findings
    from core.quality_gate.sab_parser import extract_sab_from_sad, validate_sab_block
    from core.utils.project_layout import ProjectLayout

    sad_path = ProjectLayout(Path(project)).sad_path
    if not sad_path.is_file():
        return None  # canonical deliverable gate reports the missing file
    try:
        sab = extract_sab_from_sad(sad_path)
        findings = validate_sab_block(sad_path)
        findings.extend(decision_issue_findings(
            project, sab.decision_issues if sab else [], entering_phase=3
        ))
    except (OSError, RuntimeError, ValueError) as exc:
        sab = None
        findings = [f"SAD decision issue lifecycle cannot be validated: {exc}"]
    findings.extend(property_mapping_findings(project))
    for artifact in declared_artifact_findings(
        project, sab.to_dict() if sab else {}, required_by_phase=2
    ):
        findings.append(
            f"required artifact {artifact['declared']} is {artifact['status']} "
            "at its Phase 2 deadline"
        )
    if not findings:
        return None
    print("\n[BLOCKED] Phase 2 transition contract is incomplete:")
    for finding in findings:
        print(f"  - {finding}")
    print("  → close due decisions, exact property mappings/reviews, and "
          "required artifacts, then regenerate SAB.json and retry.")
    from cli.exit_codes import EX_ADVANCE_PRECONDITION_BLOCK
    return EX_ADVANCE_PRECONDITION_BLOCK
