"""
Regression tests for 3 HIGH bugs in fr_progress_tracker:

  1. _update_fr (line 210) — round(score, 2) accepts NaN/inf, which
     json.dumps emits as the literal `NaN`/`Infinity` (invalid per
     RFC 8259). The next load() falls back to empty scaffold and
     loses ALL prior progress.

  2. _update_fr (line 217) — `data["phase"] = self.phase` clobbers
     the top-level phase that `advance_phase` previously persisted.
     After advance_phase(4), the next record_gate1_pass reverts to
     the tracker's instance phase (default 3).

  3. _write (line 226) — except ImportError only catches the missing-
     dep case. If atomic_write_json fails at runtime (TypeError for
     non-serializable data, ValueError for bad values, OSError for
     disk full / file locked), the exception propagates uncaught —
     contradicting the CV-3 docstring contract that promises
     graceful fallback to non-atomic write.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.fr_progress_tracker import FRProgressTracker


# ── Bug 1: NaN/inf score corrupts state ──────────────────────────────────────

class TestScoreValidation:
    def test_nan_score_raises_value_error(self, tmp_path: Path):
        """NaN must surface as ValueError, not silently corrupt the
        progress file with a non-RFC-8259 `NaN` token."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        with pytest.raises(ValueError, match="[Ss]core"):
            tracker.record_gate1_pass("FR-001", score=float("nan"), phase=3)

    def test_inf_score_raises_value_error(self, tmp_path: Path):
        tracker = FRProgressTracker(tmp_path, phase=3)
        with pytest.raises(ValueError, match="[Ss]core"):
            tracker.record_gate1_pass("FR-001", score=float("inf"), phase=3)
        with pytest.raises(ValueError, match="[Ss]core"):
            tracker.record_gate1_pass("FR-001", score=float("-inf"), phase=3)

    def test_non_numeric_score_raises_value_error(self, tmp_path: Path):
        """A non-parseable string score must raise ValueError, not
        silently coerce (which would let an agent bypass the
        contract by passing 'NaN' / 'Infinity' as strings)."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        with pytest.raises(ValueError, match="[Ss]core"):
            tracker.record_gate1_pass("FR-001", score="not-a-number")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="[Ss]core"):
            tracker.record_gate1_pass("FR-001", score=None)  # type: ignore[arg-type]

    def test_nan_score_does_not_corrupt_file(
        self, tmp_path: Path,
    ):
        """Sanity guard: a rejected NaN score must NOT leave the file
        in a corrupt state — the next load() must still return
        valid JSON (or the legitimate empty scaffold)."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        with pytest.raises(ValueError):
            tracker.record_gate1_pass("FR-001", score=float("nan"), phase=3)
        # File may not exist (no successful write) — but if it does,
        # it must be parseable as valid JSON.
        progress_file = tmp_path / ".methodology" / "fr_progress.json"
        if progress_file.exists():
            import json
            # Must NOT raise JSONDecodeError due to NaN token.
            json.loads(progress_file.read_text(encoding="utf-8"))


# ── Bug 2: phase clobber after advance_phase ────────────────────────────────

class TestPhasePreservation:
    def test_advance_phase_then_record_preserves_phase(
        self, tmp_path: Path,
    ):
        """After advance_phase(4), a subsequent record_gate1_pass
        must NOT revert the top-level phase back to the tracker's
        instance phase (3). The phase field is owned by
        advance_phase."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        tracker.advance_phase(4)
        # Sanity: phase is now 4 in the file.
        assert tracker.load()["phase"] == 4

        # Record a pass — top-level phase must STAY at 4.
        tracker.record_gate1_pass("FR-001", score=80.0, phase=4)
        assert tracker.load()["phase"] == 4, (
            "record_gate1_pass must not revert the top-level phase "
            "that advance_phase(4) set"
        )

    def test_fresh_tracker_records_uses_instance_phase(
        self, tmp_path: Path,
    ):
        """Sanity guard: for a brand-new tracker (no advance_phase
        call), the first record must use the tracker's instance
        phase so the top-level phase is set to a meaningful value."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        tracker.record_gate1_pass("FR-001", score=80.0, phase=3)
        assert tracker.load()["phase"] == 3


# ── Bug 3: atomic_write failure must fall back non-atomically ───────────────

class TestAtomicWriteFallback:
    def test_atomic_write_runtime_error_falls_back_non_atomic(
        self, tmp_path: Path, caplog,
    ):
        """If atomic_write_json raises a non-ImportError exception
        (e.g. TypeError for a non-serializable value, ValueError
        for NaN, OSError for disk full), the CV-3 contract promises
        a graceful fall-back to non-atomic write — but the current
        code lets the exception escape. The fix must (a) log a
        WARNING and (b) successfully write via the non-atomic path."""
        tracker = FRProgressTracker(tmp_path, phase=3)

        # Patch atomic_write_json to raise a runtime error.
        # The import happens inside _write() so we patch at the source.
        with patch(
            "core.atomic_io.atomic_write_json",
            side_effect=TypeError("simulated atomic_write failure"),
        ):
            with caplog.at_level(logging.WARNING, logger="harness.fr_progress_tracker"):
                # Must NOT raise — must fall back to non-atomic write.
                tracker.record_gate1_pass("FR-001", score=80.0, phase=3)

        # File must exist (the fallback path wrote it).
        progress_file = tmp_path / ".methodology" / "fr_progress.json"
        assert progress_file.exists(), (
            "fallback non-atomic write must produce the file"
        )
        # WARNING log must surface the atomic-write failure.
        assert any(
            "atomic" in rec.message.lower() and "fall" in rec.message.lower()
            for rec in caplog.records
        ), (
            f"atomic_write failure must produce a WARNING log entry; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )

    def test_import_error_still_falls_back(self, tmp_path: Path):
        """Sanity guard: the existing ImportError fallback path must
        still work (no regression on the graceful-degrade behavior)."""
        tracker = FRProgressTracker(tmp_path, phase=3)
        with patch(
            "core.atomic_io.atomic_write_json",
            side_effect=ImportError("simulated missing dep"),
        ):
            tracker.record_gate1_pass("FR-001", score=80.0, phase=3)
        progress_file = tmp_path / ".methodology" / "fr_progress.json"
        assert progress_file.exists()
