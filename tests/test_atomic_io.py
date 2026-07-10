"""
tests/test_atomic_io.py — Unit tests for core/atomic_io.py.

Covers: atomic_write_text, atomic_write_json, file_lock, state_lock_path.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from core.atomic_io import (
    FileSnapshot,
    atomic_write_json,
    atomic_write_text,
    file_lock,
    state_lock_path,
)



# ---------------------------------------------------------------------------
# atomic_write_text
# ---------------------------------------------------------------------------

class TestAtomicWriteText:
    def test_creates_file(self, tmp_path):
        p = tmp_path / "out.txt"
        atomic_write_text(p, "hello")
        assert p.read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "out.txt"
        p.write_text("old", encoding="utf-8")
        atomic_write_text(p, "new")
        assert p.read_text(encoding="utf-8") == "new"

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.txt"
        atomic_write_text(p, "x")
        assert p.read_text(encoding="utf-8") == "x"

    def test_no_tmp_file_left_on_success(self, tmp_path):
        p = tmp_path / "state.json"
        atomic_write_text(p, "{}")
        # Only the target file should remain; no .tmp orphans
        files = list(tmp_path.iterdir())
        assert files == [p]

    def test_no_tmp_file_left_on_error(self, tmp_path):
        """If write raises mid-way, temp file is cleaned up."""
        p = tmp_path / "state.json"
        import unittest.mock as mock
        with mock.patch("os.replace", side_effect=OSError("forced")):
            with pytest.raises(OSError):
                atomic_write_text(p, "data")
        # tmp file should have been cleaned up
        tmps = list(tmp_path.glob(".state.json.*.tmp"))
        assert tmps == []

    def test_unicode_content(self, tmp_path):
        p = tmp_path / "uni.txt"
        content = "日本語テスト 🚀"
        atomic_write_text(p, content)
        assert p.read_text(encoding="utf-8") == content

    def test_empty_content(self, tmp_path):
        p = tmp_path / "empty.txt"
        atomic_write_text(p, "")
        assert p.read_text(encoding="utf-8") == ""

    def test_large_content(self, tmp_path):
        p = tmp_path / "large.txt"
        big = "x" * 1_000_000
        atomic_write_text(p, big)
        assert p.read_text(encoding="utf-8") == big


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path):
        p = tmp_path / "data.json"
        atomic_write_json(p, {"key": "value", "n": 42})
        parsed = json.loads(p.read_text(encoding="utf-8"))
        assert parsed == {"key": "value", "n": 42}

    def test_output_ends_with_newline(self, tmp_path):
        p = tmp_path / "data.json"
        atomic_write_json(p, {})
        assert p.read_text(encoding="utf-8").endswith("\n")

    def test_nested_structure(self, tmp_path):
        p = tmp_path / "nested.json"
        data = {"a": [1, 2, {"b": True}], "c": None}
        atomic_write_json(p, data)
        assert json.loads(p.read_text(encoding="utf-8")) == data

    def test_list_input(self, tmp_path):
        p = tmp_path / "list.json"
        atomic_write_json(p, [1, 2, 3])
        assert json.loads(p.read_text(encoding="utf-8")) == [1, 2, 3]

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "state.json"
        atomic_write_json(p, {"current_phase": 1})
        atomic_write_json(p, {"current_phase": 2})
        assert json.loads(p.read_text(encoding="utf-8"))["current_phase"] == 2

    def test_unicode_keys_and_values(self, tmp_path):
        p = tmp_path / "uni.json"
        data = {"名前": "テスト", "emoji": "🎉"}
        atomic_write_json(p, data)
        parsed = json.loads(p.read_text(encoding="utf-8"))
        assert parsed == data


# ---------------------------------------------------------------------------
# file_lock
# ---------------------------------------------------------------------------

class TestFileLock:
    def test_lock_creates_lockfile(self, tmp_path):
        lock = tmp_path / ".state.lock"
        with file_lock(lock):
            pass
        assert lock.exists()

    def test_lock_creates_parent_dirs(self, tmp_path):
        lock = tmp_path / ".methodology" / ".state.lock"
        with file_lock(lock):
            pass
        assert lock.exists()

    def test_yields_inside_context(self, tmp_path):
        lock = tmp_path / ".state.lock"
        called = []
        with file_lock(lock):
            called.append(True)
        assert called == [True]

    def test_nested_locks_different_paths(self, tmp_path):
        """Two different lock files can be held simultaneously."""
        lock_a = tmp_path / ".lock_a"
        lock_b = tmp_path / ".lock_b"
        results = []
        with file_lock(lock_a):
            with file_lock(lock_b):
                results.append("inside")
        assert results == ["inside"]

    def test_thread_serialization(self, tmp_path):
        """Two threads competing on the same lock must not interleave writes."""
        lock = tmp_path / ".state.lock"
        counter_path = tmp_path / "counter.txt"
        counter_path.write_text("0", encoding="utf-8")
        errors = []

        def increment():
            for _ in range(10):
                try:
                    with file_lock(lock):
                        val = int(counter_path.read_text(encoding="utf-8"))
                        counter_path.write_text(str(val + 1), encoding="utf-8")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final = int(counter_path.read_text(encoding="utf-8"))
        assert final == 40  # 4 threads × 10 increments

    def test_lock_released_after_exception(self, tmp_path):
        """Lock must be released even when the body raises."""
        lock = tmp_path / ".state.lock"
        with pytest.raises(ValueError):
            with file_lock(lock):
                raise ValueError("test error")
        # Can acquire the lock again immediately
        acquired = []
        with file_lock(lock):
            acquired.append(True)
        assert acquired == [True]


# ---------------------------------------------------------------------------
# state_lock_path
# ---------------------------------------------------------------------------

class TestStateLockPath:
    def test_returns_path_in_methodology(self, tmp_path):
        result = state_lock_path(tmp_path)
        assert result == tmp_path / ".methodology" / ".state.lock"

    def test_accepts_string(self, tmp_path):
        result = state_lock_path(str(tmp_path))  # type: ignore[reportArgumentType]
        assert isinstance(result, Path)
        assert result == tmp_path / ".methodology" / ".state.lock"

    def test_different_projects_different_paths(self, tmp_path):
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        assert state_lock_path(proj_a) != state_lock_path(proj_b)


# ---------------------------------------------------------------------------
# FileSnapshot
# ---------------------------------------------------------------------------

class TestFileSnapshot:
    def test_restores_content_byte_for_byte(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_bytes(b'{"current_phase": 3}\n')
        snap = FileSnapshot([f])
        f.write_bytes(b'{"current_phase": 4}\n')
        snap.restore()
        assert f.read_bytes() == b'{"current_phase": 3}\n'

    def test_deletes_files_absent_at_capture(self, tmp_path):
        f = tmp_path / "HANDOVER.md"
        snap = FileSnapshot([f])
        f.write_text("generated after capture")
        snap.restore()
        assert not f.exists()

    def test_restore_recreates_deleted_file(self, tmp_path):
        f = tmp_path / "fr_progress.json"
        f.write_text("original")
        snap = FileSnapshot([f])
        f.unlink()
        snap.restore()
        assert f.read_text() == "original"

    def test_restore_is_idempotent(self, tmp_path):
        present = tmp_path / "a.txt"
        present.write_text("keep")
        absent = tmp_path / "b.txt"
        snap = FileSnapshot([present, absent])
        present.write_text("mutated")
        absent.write_text("born")
        snap.restore()
        snap.restore()
        assert present.read_text() == "keep"
        assert not absent.exists()

    def test_restore_recreates_missing_parent_dirs(self, tmp_path):
        f = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        f.parent.mkdir()
        f.write_text("pass")
        snap = FileSnapshot([f])
        f.unlink()
        f.parent.rmdir()
        snap.restore()
        assert f.read_text() == "pass"

    def test_mixed_write_set(self, tmp_path):
        """The advance-phase shape: some targets exist, some don't yet."""
        state = tmp_path / ".methodology" / "state.json"
        state.parent.mkdir()
        state.write_text('{"current_phase": 2}')
        handover = tmp_path / "HANDOVER.md"  # not yet generated
        snap = FileSnapshot([state, handover])
        state.write_text('{"current_phase": 3}')
        handover.write_text("Phase 3 handover")
        snap.restore()
        assert state.read_text() == '{"current_phase": 2}'
        assert not handover.exists()


pytestmark = pytest.mark.gate
