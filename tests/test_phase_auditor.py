"""Tests for PhaseAuditor — fallback path and C3 session separation."""
from __future__ import annotations

import json
from typing import Optional

import pytest

from scripts.phase_auditor import PhaseAuditor

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
        """When .methodology/sessions_spawn.log is absent but root
        sessions_spawn.log exists, C3 should PASS (not CRITICAL)."""
        auditor = auditor_factory({"sessions_spawn.log": VALID_AB_JSONL})
        auditor.check_c3_session_separation()
        passes = [f for f in auditor.result.findings if f.severity == "PASS"]
        criticals = [f for f in auditor.result.findings if f.severity == "CRITICAL"]
        assert len(passes) >= 1, (
            f"Expected at least one PASS finding, got: "
            f"{[(f.check_id, f.severity, f.title) for f in auditor.result.findings]}"
        )
        assert len(criticals) == 0, (
            f"Expected no CRITICAL findings, got: "
            f"{[(f.check_id, f.severity, f.title) for f in criticals]}"
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
