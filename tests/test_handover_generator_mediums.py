"""
Regression tests for 3 MEDIUM bugs in HandoverGenerator:

  1. write() (line 221) — HANDOVER.md is written via direct
     `Path.write_text` which is non-atomic. A SIGKILL/OOM/power-loss
     mid-flush leaves a truncated/partial file. The fix must use
     `core.atomic_io.atomic_write_text` (tempfile + os.replace).

  2. _state_snapshot (line 140) — broad `except Exception` silently
     masks corrupt state.json. The fix must narrow the catch to
     `(FileNotFoundError, json.JSONDecodeError, OSError)` and log
     a WARNING for any other error so corruption is visible.

  3. write() (line 159) — `phase: int` and `resume_phase: int` are
     typed but not validated at runtime. Invalid values (non-int,
     out of 1-8 range) flow into the rendered bash block. The fix
     must raise ValueError at the API boundary.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.handover_generator import HandoverGenerator


# ── Bug 1: non-atomic HANDOVER.md write ──────────────────────────────────────

class TestHandoverWriteAtomic:
    def test_handover_md_write_uses_atomic_write_text(
        self, tmp_path: Path, monkeypatch,
    ):
        """The final write of HANDOVER.md must not call Path.write_text
        directly. The fix uses core.atomic_io.atomic_write_text which
        writes to a tempfile in the same dir and atomically renames —
        a SIGKILL mid-write leaves the previous good copy intact."""
        # Track all Path.write_text calls.
        write_text_calls: list[str] = []
        orig_write_text = Path.write_text

        def _tracking(self, *args, **kwargs):
            write_text_calls.append(str(self))
            return orig_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _tracking)

        gen = HandoverGenerator(tmp_path)
        with patch.object(gen, "_git_remote", return_value=""), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )

        handover_writes = [
            p for p in write_text_calls if p.endswith("HANDOVER.md")
        ]
        assert handover_writes == [], (
            f"HANDOVER.md must be written via atomic_write_text, not "
            f"Path.write_text. Direct write_text calls: {handover_writes}"
        )

    def test_handover_md_actually_persists(
        self, tmp_path: Path,
    ):
        """Sanity guard: after the fix, HANDOVER.md must still exist
        with the rendered content (atomic_write_text must not skip the
        final write)."""
        gen = HandoverGenerator(tmp_path)
        with patch.object(gen, "_git_remote", return_value=""), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            path = gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "P3-pre-gate2-20260504" in content


# ── Bug 2: broad except in _state_snapshot ───────────────────────────────────

class TestStateSnapshotCorruptionVisible:
    def test_corrupt_state_json_logs_warning(
        self, tmp_path: Path, caplog,
    ):
        """When state.json is corrupt (not just missing), the
        _state_snapshot method must surface the failure via a WARNING
        log entry — not silently mask it as 'no state'."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            "{ this is not valid json", encoding="utf-8"
        )
        gen = HandoverGenerator(tmp_path)
        with caplog.at_level(logging.WARNING, logger="harness.handover_generator"):
            result = gen._state_snapshot()
        # A WARNING entry about state.json corruption must be logged.
        # (FileNotFoundError returns "" silently without a log entry —
        # that's the legitimate "no state yet" case.)
        assert any(
            "state.json" in rec.message and "corrupt" in rec.message.lower()
            for rec in caplog.records
        ), (
            f"corrupt state.json must produce a WARNING log entry; "
            f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
        )
        # And the result is still empty (pipeline keeps working)
        assert result == ""

    def test_missing_state_json_does_not_log(
        self, tmp_path: Path, caplog,
    ):
        """FileNotFoundError is the legitimate 'no state yet' case and
        must continue to return the empty string WITHOUT a warning
        log entry (warnings are for anomalies)."""
        gen = HandoverGenerator(tmp_path)
        with caplog.at_level(logging.WARNING, logger="harness.handover_generator"):
            result = gen._state_snapshot()
        assert result == ""
        assert not any(
            "corrupt" in rec.message.lower() for rec in caplog.records
        ), "missing state.json must not be logged as corruption"


# ── Bug 3: phase and resume_phase runtime validation ─────────────────────────

class TestPhaseValidation:
    def test_non_int_phase_raises_value_error(self, tmp_path: Path):
        """`phase` is typed as int but not validated; a string at runtime
        flows into the rendered bash block. The fix must raise ValueError
        at the API boundary."""
        gen = HandoverGenerator(tmp_path)
        with pytest.raises(ValueError, match="phase"):
            gen.write(
                checkpoint_id="P3-x",
                phase="three",  # type: ignore[arg-type]
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )

    def test_phase_out_of_range_raises_value_error(self, tmp_path: Path):
        """Phases outside 1-8 (the documented valid range) are nonsense
        for a HANDOVER.md and would render path injection in the
        bash block. Must raise ValueError."""
        gen = HandoverGenerator(tmp_path)
        with pytest.raises(ValueError, match="phase"):
            gen.write(
                checkpoint_id="P99-x",
                phase=99,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        with pytest.raises(ValueError, match="phase"):
            gen.write(
                checkpoint_id="P0-x",
                phase=0,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )

    def test_non_int_resume_phase_raises_value_error(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        with pytest.raises(ValueError, match="resume_phase"):
            gen.write(
                checkpoint_id="P3-x",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
                resume_phase="four",  # type: ignore[arg-type]
            )

    def test_resume_phase_out_of_range_raises_value_error(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        with pytest.raises(ValueError, match="resume_phase"):
            gen.write(
                checkpoint_id="P3-x",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
                resume_phase=42,
            )

    def test_valid_phase_range_still_works(self, tmp_path: Path):
        """Sanity guard: phases 1-8 must continue to work."""
        gen = HandoverGenerator(tmp_path)
        with patch.object(gen, "_git_remote", return_value=""), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            for phase in (1, 2, 3, 4, 5, 6, 7, 8):
                gen.write(
                    checkpoint_id=f"P{phase}-x",
                    phase=phase,
                    task_background="bg",
                    current_status="status",
                    next_steps=["step 1"],
                )
