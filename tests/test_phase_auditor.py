"""Tests for PhaseAuditor — fallback path and C3 session separation."""
from __future__ import annotations

import json
from typing import Optional

import pytest

from scripts.phase_auditor import PhaseAuditor, _ENTRY_GATE_MAP

# Standard valid A/B JSONL for testing
VALID_AB_JSONL = (
    json.dumps({"timestamp": "2026-01-01T10:00:00", "fr_id": "FR-01", "role": "developer",
                "session_id": "dev-001", "status": "success", "confidence": 8}) + "\n" +
    json.dumps({"timestamp": "2026-01-01T10:05:00", "fr_id": "FR-01", "role": "reviewer",
                "session_id": "rev-001", "status": "success", "review_status": "APPROVE"}) + "\n"
)


class FakeGitHubFetcher:
    """Test double that serves files from a dict in memory — no gh CLI calls."""

    def __init__(self, files: dict[str, str]):
        self.repo = "fake/repo"
        self._files = files  # path -> content

    def get_tree(self) -> list[dict]:
        """Dynamically produce tree entries from stored file keys."""
        return [{"path": path, "type": "blob"} for path in self._files]

    def resolve_path(self, candidates: list[str]) -> Optional[str]:
        for path in candidates:
            if path in self._files:
                return path
        return None

    def get_file_content(self, path: str) -> Optional[str]:
        return self._files.get(path)

    def file_exists(self, path: str) -> bool:
        return path in self._files


@pytest.fixture
def auditor_factory():
    """Build a PhaseAuditor that reads from an in-memory file dict."""
    def _make(files: dict[str, str], phase: int = 3) -> PhaseAuditor:
        fetcher = FakeGitHubFetcher(files)
        return PhaseAuditor(fetcher, phase)
    return _make


class TestC3SessionSeparation:
    """C3: A/B session separation via sessions_spawn.log."""

    def test_c3_fallback_to_root_log(self, auditor_factory):
        """When .methodology/sessions_spawn.log is absent but root sessions_spawn.log
        exists, C3 must produce at least one PASS finding and must NOT produce a CRITICAL
        about the log file being missing/absent."""
        auditor = auditor_factory({"sessions_spawn.log": VALID_AB_JSONL})
        auditor.check_c3_session_separation()
        passes = [f for f in auditor.result.findings if f.severity == "PASS"]
        # Check that the log-not-found CRITICAL is absent (the fallback worked)
        missing_log_criticals = [
            f for f in auditor.result.findings
            if f.severity == "CRITICAL" and "does not exist" in f.title
        ]
        assert len(passes) >= 1, (
            f"Expected at least one PASS finding, got: "
            f"{[(f.check_id, f.severity, f.title) for f in auditor.result.findings]}"
        )
        assert len(missing_log_criticals) == 0, (
            f"Expected no 'log does not exist' CRITICAL, got: "
            f"{[(f.check_id, f.severity, f.title) for f in missing_log_criticals]}"
        )

    def test_c3_prefers_methodology_over_root(self, auditor_factory):
        """When both exist, .methodology/sessions_spawn.log is resolved first."""
        auditor = auditor_factory({
            ".methodology/sessions_spawn.log": VALID_AB_JSONL,
            "sessions_spawn.log": "{}",
        })
        auditor.check_c3_session_separation()
        passes = [f for f in auditor.result.findings if f.severity == "PASS"]
        assert len(passes) >= 1

    def test_c3_missing_log_is_critical(self, auditor_factory):
        """When neither path exists, C3 must produce a CRITICAL finding."""
        auditor = auditor_factory({})
        auditor.check_c3_session_separation()
        criticals = [f for f in auditor.result.findings if f.severity == "CRITICAL"]
        assert len(criticals) >= 1

    def test_c3_empty_log_is_critical(self, auditor_factory):
        """When the log exists but has no parseable entries, C3 is CRITICAL."""
        auditor = auditor_factory({
            ".methodology/sessions_spawn.log": "\n\n\n",
        })
        auditor.check_c3_session_separation()
        criticals = [f for f in auditor.result.findings if f.severity == "CRITICAL"]
        assert len(criticals) >= 1


class TestC9GatePass:
    """C9: quality_manifest.json gate PASS verification."""

    def _make_auditor(self, phase: int, manifest_data: Optional[dict] = None,
                      missing: bool = False) -> PhaseAuditor:
        files: dict[str, str] = {} if missing else {
            ".methodology/quality_manifest.json": json.dumps(manifest_data or {})
        }
        return PhaseAuditor(FakeGitHubFetcher(files), phase)

    def test_phase_below_4_returns_info(self):
        """Phases 1-3 have no gate entry requirement — C9 should report INFO."""
        a = self._make_auditor(3)
        a.check_c9_gate_pass()
        assert any(f.severity == "INFO" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_manifest_missing_returns_critical(self):
        """Phase 4+ without quality_manifest.json should be CRITICAL."""
        a = self._make_auditor(4, missing=True)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate2_pass_returns_pass(self):
        """Phase 4 requires Gate 2 PASS — quality_complete=True should give PASS."""
        assert _ENTRY_GATE_MAP[4] == 2
        manifest = {"gate_results": {"gate2": {"quality_complete": True}}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "PASS" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate2_not_passed_returns_critical(self):
        """quality_complete=False should yield CRITICAL."""
        manifest = {"gate_results": {"gate2": {"quality_complete": False}}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate_key_missing_returns_critical(self):
        """If gate_results exists but the required gate key is absent, that is CRITICAL."""
        manifest = {"gate_results": {}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate4_required_for_phase7(self):
        """Phase 7 requires Gate 4 PASS."""
        assert _ENTRY_GATE_MAP[7] == 4
        manifest = {"gate_results": {"gate4": {"quality_complete": True}}}
        a = self._make_auditor(7, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "PASS" and f.check_id == "C9"
                   for f in a.result.findings)


class TestC3AgentBApprovals:
    """C3 supplement: agent_b_approvals/*.json presence and APPROVE status (P3+)."""

    def _sessions_log(self) -> str:
        return (
            json.dumps({"session_id": "a1", "role": "developer", "task": "impl"}) + "\n" +
            json.dumps({"session_id": "b1", "role": "reviewer", "task": "review"})
        )

    def _make_auditor(self, phase: int, approval_files: dict) -> PhaseAuditor:
        files: dict[str, str] = {".methodology/sessions_spawn.log": self._sessions_log()}
        files.update({k: json.dumps(v) for k, v in approval_files.items()})
        return PhaseAuditor(FakeGitHubFetcher(files), phase)

    def test_p3_with_approve_passes(self):
        """P3 with one APPROVE file should give PASS."""
        a = self._make_auditor(3, {
            ".methodology/agent_b_approvals/FR-01.json": {"review_status": "APPROVE"}
        })
        a._check_agent_b_approvals()
        assert any(f.severity == "PASS" and f.check_id == "C3"
                   for f in a.result.findings)

    def test_p3_no_approval_files_critical(self):
        """P3 with no approval files should be CRITICAL."""
        a = self._make_auditor(3, {})
        a._check_agent_b_approvals()
        assert any(f.severity == "CRITICAL" and f.check_id == "C3"
                   for f in a.result.findings)

    def test_p1_skipped(self):
        """P1/P2: _check_agent_b_approvals should not add any C3 findings."""
        a = self._make_auditor(1, {})
        a._check_agent_b_approvals()
        assert not any(f.check_id == "C3" for f in a.result.findings)

    def test_request_changes_not_approve_is_critical(self):
        """review_status=REQUEST_CHANGES should count as not APPROVE → CRITICAL."""
        a = self._make_auditor(3, {
            ".methodology/agent_b_approvals/FR-01.json": {"review_status": "REQUEST_CHANGES"}
        })
        a._check_agent_b_approvals()
        assert any(f.severity == "CRITICAL" and f.check_id == "C3"
                   for f in a.result.findings)

    def test_mixed_approvals_pass_if_any_approved(self):
        """If at least one file has APPROVE, result is PASS."""
        a = self._make_auditor(3, {
            ".methodology/agent_b_approvals/FR-01.json": {"review_status": "APPROVE"},
            ".methodology/agent_b_approvals/FR-02.json": {"review_status": "REQUEST_CHANGES"},
        })
        a._check_agent_b_approvals()
        assert any(f.severity == "PASS" and f.check_id == "C3"
                   for f in a.result.findings)
