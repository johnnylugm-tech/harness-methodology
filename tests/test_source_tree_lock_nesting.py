"""Round 66 站0 — a lock that deadlocks silently is a stall with no author.

`core/quality_gate/source_tree_lock.py` serialises everything that reads or
executes the project's source files against mutmut's mutation window. Its
docstring already names the footgun:

    Do not nest calls for the same project within one process: `flock` is
    per-file-descriptor, so a second `with` block on the same project just
    re-enters on a fresh descriptor and blocks on itself.

Today the consequence of ignoring that sentence is an unbounded hang with no
message — the exact failure shape this round is about. It was survivable while
two call sites took the lock; Round 66 adds more, and a warning in a docstring
is not a mechanism.

The fix is not to make nesting work. It is to make nesting SAY so: the same
process asking twice is a bug in the caller, and it should read as one
immediately instead of joining a queue it can never leave.

Measured against the queue this round found on taskq-cc at 04:15 — six harness
processes on that flock, the holder (mutmut) advancing 0.67 CPU-seconds in
fourteen minutes because 25 unsynchronised agent-launched pytest runs had the
machine. Cross-process waiting is the lock working. Waiting on yourself is not.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core.quality_gate.source_tree_lock import source_tree_lock

REPO_ROOT = Path(__file__).resolve().parents[1]

_NESTED_ACQUIRE = """
import sys
sys.path.insert(0, {repo!r})
from core.quality_gate.source_tree_lock import source_tree_lock

with source_tree_lock({project!r}):
    with source_tree_lock({project!r}):
        print("REACHED")
"""


def test_nesting_the_source_tree_lock_reports_instead_of_hanging(tmp_path):
    """Run out-of-process: the failure being pinned is an unbounded hang.

    A `timeout=` on the subprocess is the only way to assert "does not hang"
    without risking the suite itself — the same reason Round 65's tests bound
    their wall clock rather than only checking the end state.
    """
    driver = tmp_path / "nested.py"
    driver.write_text(
        _NESTED_ACQUIRE.format(repo=str(REPO_ROOT), project=str(tmp_path)),
        encoding="utf-8",
    )

    try:
        proc = subprocess.run(  # nosec B603
            [sys.executable, str(driver)],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "a second source_tree_lock() on the same project in the same "
            "process blocked forever. flock is per-descriptor, so the process "
            "is queued behind itself and nothing will ever release it — an "
            "unbounded stall that names no author"
        )

    assert proc.returncode != 0, (
        "nesting the lock succeeded. Two acquisitions in one process mean the "
        "caller believes it is alone when it is not; the second one must be "
        "refused, not granted"
    )
    combined = proc.stdout + proc.stderr
    assert "REACHED" not in combined, "the inner block ran despite the outer hold"
    assert "source_tree_lock" in combined and "nest" in combined.lower(), (
        f"the refusal has to say what the caller did wrong. Got:\n{combined[-800:]}"
    )


def test_a_second_thread_waits_it_is_not_refused(tmp_path):
    """The refusal is for the same CALLER, not the same process.

    The first version of this keyed the held set by lock file alone, so a
    second thread was told it was nesting when it was doing the one thing the
    lock exists for. `tests/test_source_tree_lock.py`'s four-thread test
    caught it — but only on CI: locally it passed three full-suite runs and
    then failed 4 of 6 direct runs. A test that decides by thread scheduling
    reports whichever answer the machine felt like giving.

    So this one does not race. The holder signals that it is inside the
    critical section before the waiter is even started, and the waiter is
    required to still be blocked afterwards — the outcome cannot depend on
    who wakes up first.
    """
    (tmp_path / ".methodology").mkdir()
    holding, may_release = threading.Event(), threading.Event()
    outcome: dict[str, object] = {}

    def holder():
        with source_tree_lock(tmp_path):
            holding.set()
            may_release.wait(30)

    def waiter():
        try:
            with source_tree_lock(tmp_path):
                outcome["acquired"] = True
        except BaseException as exc:  # noqa: BLE001 -- the thing under test
            outcome["error"] = repr(exc)

    h = threading.Thread(target=holder)
    h.start()
    assert holding.wait(30), "the holder never entered the critical section"

    w = threading.Thread(target=waiter)
    w.start()
    w.join(timeout=1.0)
    try:
        assert "error" not in outcome, (
            f"a second thread was refused as though it were the same caller: "
            f"{outcome['error']}"
        )
        assert w.is_alive(), (
            "the second thread neither blocked nor failed — it acquired a lock "
            "another thread was holding"
        )
    finally:
        may_release.set()
        w.join(timeout=30)
        h.join(timeout=30)
    assert outcome.get("acquired") is True, (
        "the second thread never acquired the lock after the holder released it"
    )
