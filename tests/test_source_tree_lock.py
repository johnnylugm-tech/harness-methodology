"""source_tree_lock: mutual exclusion between mutmut's live-tree mutation
window and any concurrent test-suite run (Gate 2 false-negative root cause
— see source_tree_lock.py's module docstring)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from core.quality_gate.source_tree_lock import source_tree_lock


def test_source_tree_lock_serialises_concurrent_holders(tmp_path: Path) -> None:
    """Mutual exclusion, decided by the lock rather than by the scheduler.

    Round 67 站6. This used to start four threads that each slept 0.05s inside
    the critical section and then compared the intervals for overlap. Whether
    it passed depended on when the OS ran them: it went red on CI for Round 66
    (`assert 2 == 4`) after passing three consecutive full-suite runs locally,
    and then failed 4 of 6 direct runs on the same tree. A test whose verdict
    is a scheduling outcome reports whichever answer the machine felt like
    giving that minute — which is the shape Round 67 is about, in the tests.

    So nothing here races. The holder announces that it is inside the critical
    section before any contender starts, and the contenders are required to
    still be waiting while it holds; they are released only when it exits.
    """
    (tmp_path / ".methodology").mkdir()
    holding, may_release = threading.Event(), threading.Event()
    entered = threading.Semaphore(0)
    outcomes: list[object] = []
    guard = threading.Lock()  # guards `outcomes`, not the thing under test

    def holder() -> None:
        with source_tree_lock(tmp_path):
            holding.set()
            may_release.wait(30)

    def contender() -> None:
        try:
            with source_tree_lock(tmp_path):
                with guard:
                    outcomes.append(True)
        except BaseException as exc:  # noqa: BLE001 -- the thing under test
            with guard:
                outcomes.append(exc)
        finally:
            entered.release()

    h = threading.Thread(target=holder)
    h.start()
    assert holding.wait(30), "the holder never entered the critical section"

    contenders = [threading.Thread(target=contender) for _ in range(3)]
    for t in contenders:
        t.start()
    # Give them a real chance to acquire something they must not acquire.
    time.sleep(0.2)
    try:
        with guard:
            assert not outcomes, (
                "a contender got past the lock while another thread was "
                f"holding it: {outcomes}"
            )
    finally:
        may_release.set()

    for _ in contenders:
        assert entered.acquire(timeout=30), (
            "a contender never finished after the holder released the lock"
        )
    h.join(timeout=30)
    for t in contenders:
        t.join(timeout=30)

    assert outcomes == [True, True, True], (
        f"every contender should have acquired the lock in turn: {outcomes}"
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
