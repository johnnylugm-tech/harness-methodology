"""source_tree_lock: mutual exclusion between mutmut's live-tree mutation
window and any concurrent test-suite run (Gate 2 false-negative root cause
— see source_tree_lock.py's module docstring)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from core.quality_gate.source_tree_lock import source_tree_lock


def test_source_tree_lock_serialises_concurrent_holders(tmp_path: Path) -> None:
    (tmp_path / ".methodology").mkdir()
    intervals: list[tuple[float, float]] = []
    lock = threading.Lock()  # guards `intervals`, not the thing under test

    def hold(duration: float) -> None:
        with source_tree_lock(tmp_path):
            start = time.monotonic()
            time.sleep(duration)
            end = time.monotonic()
        with lock:
            intervals.append((start, end))

    threads = [threading.Thread(target=hold, args=(0.05,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == 4
    intervals.sort()
    for (_, prev_end), (next_start, _) in zip(intervals, intervals[1:]):
        assert next_start >= prev_end, (
            "two holders overlapped inside the critical section — "
            f"intervals={intervals}"
        )


def test_source_tree_lock_creates_methodology_dir_if_missing(tmp_path: Path) -> None:
    # No .methodology/ dir yet — the lock must create it rather than raise.
    with source_tree_lock(tmp_path):
        pass
    assert (tmp_path / ".methodology" / ".mutation_exclusive.lock").is_file()


def test_source_tree_lock_releases_on_normal_exit(tmp_path: Path) -> None:
    (tmp_path / ".methodology").mkdir()
    with source_tree_lock(tmp_path):
        pass
    # A second acquisition must not block forever now that the first has exited.
    acquired = threading.Event()

    def try_acquire() -> None:
        with source_tree_lock(tmp_path):
            acquired.set()

    t = threading.Thread(target=try_acquire)
    t.start()
    t.join(timeout=2)
    assert acquired.is_set(), "lock was not released after the first `with` block exited"


def test_source_tree_lock_releases_on_exception(tmp_path: Path) -> None:
    (tmp_path / ".methodology").mkdir()
    try:
        with source_tree_lock(tmp_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    acquired = threading.Event()

    def try_acquire() -> None:
        with source_tree_lock(tmp_path):
            acquired.set()

    t = threading.Thread(target=try_acquire)
    t.start()
    t.join(timeout=2)
    assert acquired.is_set(), "lock was not released after the holder raised"
