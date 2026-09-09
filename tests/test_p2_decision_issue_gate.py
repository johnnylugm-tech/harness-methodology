from pathlib import Path

from core.quality_gate.decision_issues import decision_issue_findings
from core.quality_gate.required_artifacts import declared_artifact_findings


def _srs(project: Path, text: str) -> None:
    path = project / "01-requirements" / "SRS.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def test_deferred_requirement_cannot_disappear_between_p1_and_p3(tmp_path: Path):
    _srs(tmp_path, "# SRS\nFR-01-deferred requires canonical confirmation\n")
    assert decision_issue_findings(tmp_path, [], entering_phase=3) == [
        "FR-01-DEFERRED appears in SRS.md but has no decision_issues lifecycle entry"
    ]


def test_open_issue_blocks_at_declared_boundary_but_not_before(tmp_path: Path):
    _srs(tmp_path, "# SRS\nNFR-99-deferred\n")
    rows = [{"id": "NFR-99-deferred", "status": "open", "blocks_phase": 3}]
    assert decision_issue_findings(tmp_path, rows, entering_phase=2) == []
    assert "blocks entry to Phase 3" in decision_issue_findings(
        tmp_path, rows, entering_phase=3
    )[0]


def test_resolved_issue_requires_durable_evidence(tmp_path: Path):
    _srs(tmp_path, "# SRS\nFR-02-deferred\n")
    rows = [{"id": "FR-02-deferred", "status": "resolved",
             "resolution_ref": "02-architecture/adr/ADR.md#ADR-2"}]
    assert "does not resolve" in decision_issue_findings(
        tmp_path, rows, entering_phase=3
    )[0]
    ref = tmp_path / "02-architecture" / "adr" / "ADR.md"
    ref.parent.mkdir(parents=True)
    ref.write_text("# ADR-2\n", encoding="utf-8")
    assert decision_issue_findings(tmp_path, rows, entering_phase=3) == []


def test_required_artifact_deadline_prevents_early_or_late_enforcement(tmp_path: Path):
    sab = {"required_artifacts": [
        "legacy-with-no-inferable-deadline.txt",
        {"path": "p2.txt", "required_by_phase": 2},
        {"path": "p3.txt", "required_by_phase": 3},
    ]}
    assert [f["declared"] for f in declared_artifact_findings(
        tmp_path, sab, required_by_phase=2
    )] == ["p2.txt"]
    assert [f["declared"] for f in declared_artifact_findings(
        tmp_path, sab, required_by_phase=3
    )] == ["p2.txt", "p3.txt"]
