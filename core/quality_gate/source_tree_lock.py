"""Exclusive lock guarding the live project source tree during mutation
testing.

mutmut mutates ``paths_to_mutate`` files in place at their real project
path (``mutation_enforcer._abs_paths_to_mutate`` resolves absolute paths
into the project, not a copy) — mutation testing has to mutate the exact
code Python imports when the test command runs, so isolating it to a
``workdir`` copy would additionally require a full, separate install of
the project in that copy for every run. ``cwd=workdir`` isolates mutmut's
own execution context (cache location, test discovery) but never isolated
the mutated files themselves; that was a real gap between what
``evaluate_dimension.md`` documents ("cwd isolation via temp workdir") and
what the code does.

Anything else that reads or executes those files between mutmut applying
a mutant and reverting it observes a broken tree. On this project that
surfaced as a live Gate 2 failure: ``PhaseTruthVerifier.check_pytest``
(``core.quality_gate.test_suite_run.run_suite``) re-runs the real test
suite independently of the SSI dimension scoring, and when that run
landed inside a mutation window it saw a genuinely broken source file,
failed HR-11, and blocked ``finalize-gate`` from writing
``state.json.last_gate`` — even though both the SSI composite and the
mutation score itself passed their thresholds. The dispatched agent
observing this misdiagnosed it as an external process injecting
regressions and spent an entire round trying to "fix" files mutmut was
about to mutate again.

This lock is held for the whole ``mutmut run`` subprocess
(``mutation_enforcer.py``) and acquired, blocking, by
``run_against_source_tree`` below — so a concurrent test execution simply
waits for the in-flight mutation window to close instead of racing it.

Round 66: a lock only serialises the callers who take it. Measured on
taskq-cc at 04:15 on 2026-08-21, six harness processes were queued on this
flock while 25 pytest runs that never ask for it had the machine — every one
of them launched by an agent through its own Bash tool, and three more
started by harness code that built its own ``subprocess.run``. The two halves
a caller needs are the wait for the mutation window and the group kill that
makes its own timeout mean something, and neither is optional, so
``run_against_source_tree`` pairs them and is the way in.
"""
from __future__ import annotations

import contextlib
import fcntl
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Generator, Optional, Sequence

from core.utils.subprocess_group import run_isolated

_LOCK_FILENAME = ".mutation_exclusive.lock"

# (thread, lock file) pairs currently held. `flock` is per-file-DESCRIPTION,
# so a second acquisition opens a fresh descriptor and conflicts with the
# first — which is correct between two threads (they really are two callers,
# and one waiting for the other is the lock working) and a permanent
# self-deadlock within one call stack, where nothing is left to release it.
# Keyed by thread for exactly that reason: it is the *same caller* asking
# twice that is the bug, not the same process.
_HELD: set[tuple[int, str]] = set()


@contextlib.contextmanager
def source_tree_lock(project: "str | Path") -> Generator[None]:
    """Block until exclusive access to *project*'s source tree is held.

    Backed by ``flock`` on a sentinel file under ``.methodology/`` — safe
    across processes (every mutmut/pytest invocation is its own `harness_cli.py`
    subprocess), released automatically on process exit even if the holder
    crashes.

    Nesting it for the same project on the same THREAD raises. That used to
    be a sentence in this docstring and an unbounded, silent hang for anyone
    who did not read it — the exact failure shape Round 66 is about. Waiting
    on another process, or on another thread, is the lock working; waiting on
    yourself is a bug in the caller, and it should read as one immediately.
    """
    lock_path = Path(project) / ".methodology" / _LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = (threading.get_ident(), str(lock_path.resolve()))
    if key in _HELD:
        raise RuntimeError(
            f"source_tree_lock({project}) is already held by this thread. "
            f"Nesting it queues the thread behind itself and nothing will "
            f"ever release it — hold it once, at the outermost caller."
        )
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _HELD.add(key)
        try:
            yield
        finally:
            _HELD.discard(key)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def run_against_source_tree(
    cmd: Sequence[str],
    *,
    project: "str | Path",
    timeout: float,
    env: Optional[dict] = None,
) -> "subprocess.CompletedProcess[str]":
    """Run *cmd* inside *project*, once the mutation window is closed.

    The one way to execute a project's own tree. Raises
    ``subprocess.TimeoutExpired`` and ``FileNotFoundError`` exactly as
    ``subprocess.run`` does, so a caller keeps the handlers it already had —
    what it does not keep is the ability to time out and leave the command's
    children running.

    Where the lock is already held (``mutation_enforcer`` holds it around the
    whole ``mutmut run``), call ``run_isolated`` directly instead; nesting
    raises.
    """
    with source_tree_lock(project):
        return run_isolated(cmd, timeout=timeout, cwd=str(project), env=env)
