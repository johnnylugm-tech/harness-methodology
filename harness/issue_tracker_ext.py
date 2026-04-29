"""Issue tracker extension with FR traceability."""
# harness/issue_tracker_ext.py
# Gap G5: Extends IssueTracker with FR bidirectional traceability.
from __future__ import annotations

try:
    from software_self_improvement.scripts.issue_tracker import IssueTracker
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


class IssueTrackerExt(IssueTracker):
    """IssueTracker + per-FR tagging + FR-level saturation detection."""

    def __init__(self):
        super().__init__()
        self._round_findings: dict[str, set[str]] = {}
        self._saturation_counters: dict[str, int] = {}

    def add_finding(
        self, dimension: str, severity: str, file: str, line: int,
        message: str, evidence: str, fr_id: str | None = None,
    ) -> str:
        fid = super().add_finding(dimension=dimension, severity=severity,
            file=file, line=line, message=message, evidence=evidence)
        if fr_id:
            for issue in self.open_issues():
                if issue["id"] == fid:
                    issue.setdefault("fr_ids", []).append(fr_id)
        return fid

    def get_findings_by_fr(self, fr_id: str) -> list[dict]:
        return [f for f in self.open_issues() if fr_id in f.get("fr_ids", [])]

    def fr_saturation_check(
        self, fr_id: str, current_finding_ids: set[str], threshold: int = 2
    ) -> bool:
        """True if no new issues for `threshold` consecutive rounds."""
        prev = self._round_findings.get(fr_id, set())
        new = current_finding_ids - prev
        self._round_findings[fr_id] = current_finding_ids
        self._saturation_counters[fr_id] = (
            0 if new else self._saturation_counters.get(fr_id, 0) + 1
        )
        return self._saturation_counters[fr_id] >= threshold

    def fr_coverage_summary(self, fr_ids: list[str]) -> dict:
        return {fr: len(self.get_findings_by_fr(fr)) for fr in fr_ids}
