"""
Regression tests for CRITICAL bug:
    load() silently swallows JSON corruption, losing the entire FR registry
    on the next read-modify-write.

Contract under test:
  - json.JSONDecodeError on the progress file must NOT result in silent loss.
  - The corrupt file is preserved (renamed) so the next write cannot clobber it.
  - load() still returns the empty scaffold (pipeline must continue).
  - FileNotFoundError still returns the empty scaffold (legitimate case).
  - Other OSErrors (IsADirectoryError, PermissionError, etc.) propagate up
    so the caller can distinguish "no progress" from "cannot read progress".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.fr_progress_tracker import FRProgressTracker


def _progress_path(tmp_path: Path) -> Path:
    return tmp_path / ".methodology" / "fr_progress.json"


@pytest.fixture
def tracker(tmp_path: Path) -> FRProgressTracker:
    return FRProgressTracker(tmp_path, phase=3)


# ── Corruption preservation ──────────────────────────────────────────────────

class TestLoadCorruptionPreservation:
    def test_corrupt_file_is_renamed_to_backup(
        self, tmp_path: Path,
    ):
        """On json.JSONDecodeError the corrupt file must be renamed aside
        (so the next _write() cannot silently clobber it) and load() must
        return the empty scaffold so the pipeline can continue."""
        _progress_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        _progress_path(tmp_path).write_text("NOT VALID JSON {{{")
        tracker = FRProgressTracker(tmp_path, phase=3)

        result = tracker.load()

        # Return contract: empty scaffold.
        assert result == {"phase": 3, "updated_at": "", "frs": {}}
        # Preservation contract: original file is gone, a .corrupt.* backup
        # exists holding the original bytes verbatim.
        assert not _progress_path(tmp_path).exists(), (
            "corrupt file must be renamed aside, not left in place for the "
            "next _write() to clobber"
        )
        backups = list(_progress_path(tmp_path).parent.glob(
            "fr_progress.json.corrupt.*"
        ))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "NOT VALID JSON {{{"

    def test_next_write_does_not_destroy_corrupt_original(
        self, tmp_path: Path, tracker: FRProgressTracker,
    ):
        """Full bug repro: corrupt file → load() returns empty → next
        record_gate1_pass() runs the read-modify-write. The corrupt original
        must survive the write (live in the .corrupt.* backup) so an
        operator can recover it."""
        _progress_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        _progress_path(tmp_path).write_text("{garbage")

        tracker.load()  # triggers preservation
        tracker.record_gate1_pass("FR-001", score=80.0, phase=3)

        # The new pass is recorded at the canonical path.
        data = json.loads(_progress_path(tmp_path).read_text(encoding="utf-8"))
        assert "FR-001" in data["frs"]
        # The corrupt original is still recoverable.
        backups = list(_progress_path(tmp_path).parent.glob(
            "fr_progress.json.corrupt.*"
        ))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{garbage"

    def test_corrupt_rename_failure_still_returns_empty_scaffold(
        self, tmp_path: Path, monkeypatch,
    ):
        """If the rename-to-backup itself fails (e.g. read-only FS),
        load() must still return the empty scaffold — we never want to
        crash the pipeline over a backup failure."""
        _progress_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        _progress_path(tmp_path).write_text("BROKEN")

        def _raise(self, *a, **kw):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(Path, "rename", _raise)

        tracker = FRProgressTracker(tmp_path, phase=3)
        result = tracker.load()  # must not raise
        assert result == {"phase": 3, "updated_at": "", "frs": {}}


# ── Legitimate missing-file case (regression guard) ──────────────────────────

class TestLoadMissingFile:
    def test_missing_file_returns_empty_scaffold(
        self, tracker: FRProgressTracker,
    ):
        """FileNotFoundError is the legitimate "no progress yet" case and
        must continue to return the empty scaffold without any side effect."""
        result = tracker.load()
        assert result == {"phase": 3, "updated_at": "", "frs": {}}
        backups = list(_progress_path(tracker.project).glob(
            "fr_progress.json.corrupt.*"
        ))
        assert backups == [], (
            "missing file must not produce a corrupt-backup side effect"
        )


# ── Other OSErrors propagate (defensive contract) ────────────────────────────

class TestLoadOSErrorPropagation:
    def test_is_a_directory_error_propagates(self, tmp_path: Path):
        """Non-FileNotFoundError, non-decode OSErrors (e.g. the path is a
        directory, not a file) must propagate. Silent empty-scaffold
        fallback hid these in the original code."""
        _progress_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        # Make the progress file actually a directory → read_text raises
        # IsADirectoryError (a subclass of OSError, not a JSONDecodeError).
        _progress_path(tmp_path).mkdir()

        tracker = FRProgressTracker(tmp_path, phase=3)
        with pytest.raises(OSError):
            tracker.load()
