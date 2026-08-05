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
``test_suite_run._measure`` before it runs pytest — so a concurrent test
execution simply waits for the in-flight mutation window to close instead
of racing it.
"""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path
from typing import Generator

_LOCK_FILENAME = ".mutation_exclusive.lock"


@contextlib.contextmanager
def source_tree_lock(project: "str | Path") -> Generator[None]:
    """Block until exclusive access to *project*'s source tree is held.

    Backed by ``flock`` on a sentinel file under ``.methodology/`` — safe
    across processes (every mutmut/pytest invocation is its own `harness_cli.py`
    subprocess), released automatically on process exit even if the holder
    crashes. Do not nest calls for the same project within one process:
    `flock` is per-file-descriptor, so a second `with` block on the same
    project just re-enters on a fresh descriptor and blocks on itself.
    """
    lock_path = Path(project) / ".methodology" / _LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
