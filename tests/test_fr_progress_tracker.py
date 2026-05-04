# tests/test_fr_progress_tracker.py
import json
import pytest
from pathlib import Path

from harness.fr_progress_tracker import FRProgressTracker


@pytest.fixture
def tracker(tmp_path: Path) -> FRProgressTracker:
    return FRProgressTracker(tmp_path, phase=3)


# ── Basic persistence ────────────────────────────────────────────────────────

class TestFRProgressPersistence:
    def test_file_created_on_first_record(self, tmp_path: Path, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0, phase=3)
        assert (tmp_path / ".methodology" / "fr_progress.json").exists()

    def test_pass_status_written(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=82.5, phase=3)
        data = tracker.load()
        assert data["frs"]["FR-001"]["status"] == "gate1_pass"
        assert data["frs"]["FR-001"]["score"] == 82.5

    def test_fail_status_written_with_reason(self, tracker: FRProgressTracker):
        tracker.record_gate1_fail("FR-002", score=60.0, phase=3, reason="low coverage")
        data = tracker.load()
        assert data["frs"]["FR-002"]["status"] == "gate1_fail"
        assert data["frs"]["FR-002"]["reason"] == "low coverage"

    def test_multiple_frs_accumulate(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.record_gate1_pass("FR-002", score=85.0)
        tracker.record_gate1_fail("FR-003", score=60.0)
        assert len(tracker.load()["frs"]) == 3

    def test_overwrite_previous_entry(self, tracker: FRProgressTracker):
        tracker.record_gate1_fail("FR-001", score=60.0)
        tracker.record_gate1_pass("FR-001", score=82.0)
        assert tracker.load()["frs"]["FR-001"]["status"] == "gate1_pass"

    def test_load_returns_empty_when_no_file(self, tracker: FRProgressTracker):
        data = tracker.load()
        assert data["frs"] == {}

    def test_load_handles_corrupt_file(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "fr_progress.json").write_text("NOT JSON")
        tracker = FRProgressTracker(tmp_path)
        assert tracker.load()["frs"] == {}

    def test_reset_deletes_file(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.reset()
        assert not (tracker._path).exists()


# ── Query methods ────────────────────────────────────────────────────────────

class TestFRProgressQueries:
    def test_passed_fr_ids_sorted(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-003", score=80.0)
        tracker.record_gate1_pass("FR-001", score=80.0)
        assert tracker.passed_fr_ids() == ["FR-001", "FR-003"]

    def test_failed_fr_ids(self, tracker: FRProgressTracker):
        tracker.record_gate1_fail("FR-002", score=60.0)
        assert "FR-002" in tracker.failed_fr_ids()

    def test_pending_returns_non_passed(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        all_frs = ["FR-001", "FR-002", "FR-003"]
        pending = tracker.pending(all_frs)
        assert pending == ["FR-002", "FR-003"]

    def test_pending_preserves_order(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-002", score=80.0)
        all_frs = ["FR-001", "FR-002", "FR-003"]
        assert tracker.pending(all_frs) == ["FR-001", "FR-003"]

    def test_completion_ratio(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.record_gate1_pass("FR-002", score=80.0)
        assert tracker.completion_ratio(total=4) == pytest.approx(0.5)

    def test_completion_ratio_zero_total(self, tracker: FRProgressTracker):
        assert tracker.completion_ratio(total=0) == 0.0

    def test_summary_string(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.record_gate1_pass("FR-002", score=80.0)
        s = tracker.summary(total=5)
        assert "2/5" in s
        assert "Gate1 PASS" in s

    def test_summary_truncates_over_five_passed(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.record_gate1_pass("FR-002", score=80.0)
        tracker.record_gate1_pass("FR-003", score=80.0)
        tracker.record_gate1_pass("FR-004", score=80.0)
        tracker.record_gate1_pass("FR-005", score=80.0)
        tracker.record_gate1_pass("FR-006", score=80.0)
        tracker.record_gate1_pass("FR-007", score=80.0)
        s = tracker.summary()
        assert "…+2" in s  # 7 passed, first 5 shown, +2 truncated

    def test_to_status_string_includes_failed(self, tracker: FRProgressTracker):
        tracker.record_gate1_pass("FR-001", score=80.0)
        tracker.record_gate1_fail("FR-002", score=60.0)
        status = tracker.to_status_string(total=3)
        assert "FR-001" in status
        assert "FR-002" in status
        assert "retry" in status.lower()


# ── GitStrategy integration ──────────────────────────────────────────────────

class TestGitStrategyFRProgress:
    def test_commit_fr_gate1_writes_progress(self, tmp_path: Path):
        """commit_fr_gate1 should persist progress even if git is not available."""
        from harness.git_strategy import GitStrategy
        from unittest.mock import MagicMock

        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        gs._commit = MagicMock(return_value=True)  # type: ignore[method-assign]
        gs.commit_fr_gate1("FR-001", score=82.5, phase=3)

        tracker = FRProgressTracker(tmp_path, phase=3)
        assert "FR-001" in tracker.passed_fr_ids()
