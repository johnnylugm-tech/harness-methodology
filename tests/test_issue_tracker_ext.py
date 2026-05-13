"""Unit tests for harness/issue_tracker_ext.py — FR-tagged issue tracking."""
import pytest

from harness.issue_tracker_ext import IssueTrackerExt, FindingData


# ── Setup ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tracker():
    return IssueTrackerExt()


# ── add_finding (legacy API) ──────────────────────────────────────────────

class TestAddFinding:
    def test_add_finding_returns_id(self, tracker):
        fid = tracker.add_finding(
            dimension="security", severity="high", file="src/auth.py",
            line=42, message="Hardcoded key", evidence="API_KEY = 'sk-...'",
        )
        assert fid
        assert isinstance(fid, str)

    def test_add_finding_with_fr_id_tags_issue(self, tracker):
        fid = tracker.add_finding(
            dimension="coverage", severity="medium", file="tests/test_x.py",
            line=10, message="No assertion", evidence="def test_x(): pass",
            fr_id="FR-01",
        )
        by_fr = tracker.get_findings_by_fr("FR-01")
        assert len(by_fr) >= 1
        assert any(i["id"] == fid for i in by_fr)

    def test_add_finding_without_fr_id(self, tracker):
        fid = tracker.add_finding(
            dimension="quality", severity="low", file="README.md",
            line=1, message="Missing section", evidence="empty",
        )
        by_fr = tracker.get_findings_by_fr("FR-99")
        assert not by_fr


# ── add_finding_data ──────────────────────────────────────────────────────

class TestAddFindingData:
    def test_findingdata_adds_and_tags(self, tracker):
        data = FindingData(
            dimension="linting", severity="warning",
            file="app.py", line=5, message="Unused import",
            evidence="import os", fr_id="FR-02",
        )
        fid = tracker.add_finding_data(data)
        assert fid
        issues = tracker.get_findings_by_fr("FR-02")
        assert len(issues) == 1
        assert issues[0]["id"] == fid

    def test_findingdata_without_fr_id(self, tracker):
        data = FindingData(
            dimension="docs", severity="info",
            file="README.md", line=3, message="Typo",
            evidence="speling", fr_id=None,
        )
        fid = tracker.add_finding_data(data)
        assert fid
        # Issue is created but untagged
        assert len(tracker.get_findings_by_fr("FR-99")) == 0


# ── get_findings_by_fr ────────────────────────────────────────────────────

class TestGetFindingsByFR:
    def test_returns_only_tagged_issues(self, tracker):
        tracker.add_finding("dim", "high", "f1.py", 1, "m1", "e1", fr_id="FR-01")
        tracker.add_finding("dim", "high", "f2.py", 1, "m2", "e2", fr_id="FR-02")
        tracker.add_finding("dim", "high", "f3.py", 1, "m3", "e3")
        assert len(tracker.get_findings_by_fr("FR-01")) == 1
        assert len(tracker.get_findings_by_fr("FR-02")) == 1

    def test_no_findings_for_untagged_fr(self, tracker):
        assert tracker.get_findings_by_fr("FR-NONEXISTENT") == []


# ── fr_coverage_summary ───────────────────────────────────────────────────

class TestFRCoverageSummary:
    def test_returns_counts_per_fr(self, tracker):
        tracker.add_finding("d", "h", "a.py", 1, "m", "e", fr_id="FR-01")
        tracker.add_finding("d", "h", "b.py", 2, "m", "e", fr_id="FR-01")
        tracker.add_finding("d", "h", "c.py", 3, "m", "e", fr_id="FR-02")
        summary = tracker.fr_coverage_summary()
        assert summary["FR-01"] == 2
        assert summary["FR-02"] == 1

    def test_empty_summary(self, tracker):
        assert tracker.fr_coverage_summary() == {}

    def test_multiple_fr_tags_on_single_issue(self, tracker):
        """fr_coverage_summary correctly counts issues tagged with FR-01 vs FR-02."""
        tracker.add_finding("d", "h", "a.py", 1, "m1", "e1", fr_id="FR-01")
        tracker.add_finding("d", "h", "b.py", 2, "m2", "e2", fr_id="FR-02")
        tracker.add_finding("d", "h", "c.py", 3, "m3", "e3", fr_id="FR-01")
        summary = tracker.fr_coverage_summary()
        assert summary["FR-01"] == 2
        assert summary["FR-02"] == 1


# ── Saturation Detection ──────────────────────────────────────────────────

class TestSaturation:
    def test_initial_round_no_saturation(self, tracker):
        tracker.add_finding("d", "h", "f.py", 1, "m", "e", fr_id="FR-01")
        tracker.record_round_findings("FR-01")
        assert not tracker.fr_saturation_check("FR-01")

    def test_saturation_after_persistent_findings(self, tracker):
        # Add findings once, then snapshot the same set across 4+ rounds
        # Round 1 snapshots (no counter increment), rounds 2-4 increment → 3
        tracker.add_finding("d", "h", "f.py", 1, "m", "e", fr_id="FR-01")
        for _ in range(4):
            tracker.record_round_findings("FR-01")
        assert tracker.fr_saturation_check("FR-01")

    def test_new_findings_reset_counter(self, tracker):
        # Round 1 & 2: same finding set A → saturation builds
        tracker.add_finding("d1", "h", "f.py", 1, "m1", "e1", fr_id="FR-01")
        tracker.record_round_findings("FR-01")
        tracker.record_round_findings("FR-01")
        assert not tracker.fr_saturation_check("FR-01")  # only 2 rounds
        # Round 3: add a completely different finding → overlap drops → counter resets to 0
        tracker.add_finding("d2", "l", "g.py", 99, "m2", "e2", fr_id="FR-01")
        tracker.record_round_findings("FR-01")
        assert not tracker.fr_saturation_check("FR-01")

    def test_reset_saturation_clears_counter(self, tracker):
        tracker.add_finding("d", "h", "f.py", 1, "m", "e", fr_id="FR-01")
        for _ in range(4):
            tracker.record_round_findings("FR-01")
        assert tracker.fr_saturation_check("FR-01")
        tracker.reset_saturation("FR-01")
        assert not tracker.fr_saturation_check("FR-01")


# ── Fallback IssueTracker ─────────────────────────────────────────────────

def test_embedded_issuetracker_fallback():
    """When software_self_improvement is not installed, use embedded IssueTracker."""
    tracker = IssueTrackerExt()
    fid = tracker.add_finding("security", "critical", "env.py", 1,
                               "secret found", "SECRET=123")
    assert fid
    open_issues = tracker.open_issues()
    assert len(open_issues) == 1
    assert open_issues[0]["status"] == "open"
