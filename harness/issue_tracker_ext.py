"""Issue tracker extension with FR traceability."""
# Gap G5: Extends IssueTracker with FR bidirectional traceability.
from __future__ import annotations
from dataclasses import dataclass

try:
    from software_self_improvement.scripts.issue_tracker import IssueTracker  # pyright: ignore[reportMissingImports]
except ImportError:
    class IssueTracker:  # type: ignore[no-redef]
        def __init__(self):
            self._issues: list[dict] = []
        def add_finding(self, dimension, severity, file, line, message, evidence):
            import uuid
            fid = str(uuid.uuid4())[:8]
            self._issues.append({"id": fid, "dimension": dimension, "severity": severity,
                "file": file, "line": line, "message": message, "evidence": evidence,
                "status": "open", "fr_ids": []})
            return fid
        def open_issues(self):
            return [i for i in self._issues if i["status"] == "open"]

@dataclass
class FindingData:
    """Container for finding details to reduce parameter count."""
    dimension: str
    severity: str
    file: str
    line: int
    message: str
    evidence: str
    fr_id: str | None = None

class IssueTrackerExt(IssueTracker):
    """IssueTracker + per-FR tagging + FR-level saturation detection."""

    def __init__(self):
        super().__init__()
        self._round_findings: dict[str, set[str]] = {}
        self._saturation_counters: dict[str, int] = {}

    def add_finding_data(self, data: FindingData) -> str:
        """Adds a finding using FindingData object to satisfy linting."""
        fid = super().add_finding(
            dimension=data.dimension, 
            severity=data.severity,
            file=data.file, 
            line=data.line, 
            message=data.message, 
            evidence=data.evidence
        )
        if data.fr_id:
            for issue in self.open_issues():
                if issue["id"] == fid:
                    issue.setdefault("fr_ids", []).append(data.fr_id)
        return fid

    def add_finding(
        self, dimension: str, severity: str, file: str, line: int,
        message: str, evidence: str, fr_id: str | None = None,
    ) -> str:
        """Legacy compatibility wrapper."""
        return self.add_finding_data(FindingData(
            dimension=dimension, severity=severity, file=file, line=line,
            message=message, evidence=evidence, fr_id=fr_id
        ))

    def get_findings_by_fr(self, fr_id: str) -> list[dict]:
        """Returns open issues tagged with specific FR ID."""
        return [i for i in self.open_issues() if fr_id in i.get("fr_ids", [])]

    def fr_coverage_summary(self) -> dict[str, int]:
        """Returns count of open issues per FR."""
        summary: dict[str, int] = {}
        for issue in self.open_issues():
            for fr_id in issue.get("fr_ids", []):
                summary[fr_id] = summary.get(fr_id, 0) + 1
        return summary

    # ── FR Saturation Detection ───────────────────────────────────────────

    def record_round_findings(self, fr_id: str) -> None:
        """Snapshot current finding IDs for *fr_id* at end of an auto-fix round."""
        current_ids = {
            issue["id"] for issue in self.open_issues()
            if fr_id in issue.get("fr_ids", [])
        }
        prev_ids = self._round_findings.get(fr_id, set())
        if prev_ids and current_ids:
            overlap = len(current_ids & prev_ids)
            total = max(len(current_ids | prev_ids), 1)
            if overlap / total >= 0.5:
                self._saturation_counters[fr_id] = self._saturation_counters.get(fr_id, 0) + 1
            else:
                self._saturation_counters[fr_id] = 0
        self._round_findings[fr_id] = current_ids

    def fr_saturation_check(self, fr_id: str, threshold: int = 3) -> bool:
        """True when the same findings have persisted for ≥ *threshold* rounds."""
        return self._saturation_counters.get(fr_id, 0) >= threshold

    def reset_saturation(self, fr_id: str) -> None:
        """Reset saturation counter for *fr_id* (e.g. after manual fix)."""
        self._saturation_counters.pop(fr_id, None)
        self._round_findings.pop(fr_id, None)
