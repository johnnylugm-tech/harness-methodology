"""One way to run a subprocess so that nothing it started outlives it.

Round 65 站1. Two call sites asked for process-group isolation independently
(`b12ff21`) and only one of them wrote the half that kills the group. The
half that was missing is the reaper, and asking for isolation without it is
strictly worse than not asking:

    PARENT 74753 pgrp 74748 start_new_session True
    CHILD  74754 74754  GC 74755
    -- after subprocess.run(timeout=2) raised TimeoutExpired --
    74755  PPID 1  PGID 74754

    -- same script, no start_new_session --
    74797  PPID 1  PGID 74793     <- the harness's own group

`subprocess.run` kills the direct child and nothing else, so a grandchild was
being orphaned either way. What `start_new_session` changed is which group it
was orphaned INTO: pgid 74793 is the harness's own, so the terminal's Ctrl-C
and any group kill still reached it; pgid 74754 is a group nobody signals.

setsid and killpg are one mechanism with two ends. A caller cannot see the
other end from where it stands, so a caller does not get to request one end —
`tests/test_subprocess_group.py` pins this module as the only producer of
`start_new_session=`.

The leak is the visible half. Measured while counter-proving the fix: with
the group kill disabled, the `communicate()` after the kill took 120.09s to
return on a 3s timeout, because the surviving grandchild still held the
stdout/stderr pipes it had inherited. So killing only the direct child does
not merely leak a process, it stalls the caller for as long as the leak
lives — a gate that reported a 300s timeout could sit there far longer.

WHY BaseException AND NOT Exception

`subprocess.run`'s own handler is a bare `except:`, and CPython's comment on
that line reads "Including KeyboardInterrupt". Narrowing it to `except
Exception` lets Ctrl-C unwind past the kill — and in
`core.quality_gate.test_suite_run._measure` that unwind also releases
`source_tree_lock` while a pytest nobody is holding still writes into the
project tree, which is the one thing that lock exists to prevent.

WHY stdin IS /dev/null

Round 66: a new session has no controlling terminal, so a child that reads an
inherited terminal stdin there gets SIGTTIN and stops — a stall this module
would itself have introduced. Every caller is non-interactive by construction
(`claude -p` carries its prompt in argv; pytest and the tool runners read
files), so the answer is not to keep the terminal but to say so: an immediate
EOF. `core/agent_spawner.py:200` has to denoise the CLI's own "no stdin data
received in Ns, proceeding without it" — that wait is the same hazard already
costing time on the path that works.

WHERE THERE ARE NO PROCESS GROUPS

`os.killpg` does not exist on Windows, and `AttributeError` is not an
`OSError`. Rather than raise from the timeout handler, this module does not
ask for the isolation it could not undo there: no new session, and the direct
child is killed exactly as `subprocess.run` would have. The mechanism exists
where the OS provides it and is absent, not pretended, where it does not.
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404
from typing import Any, Optional, Sequence

__all__ = ["GROUP_KILL_AVAILABLE", "run_isolated"]

# setsid, getpgid and killpg are one transaction; a platform missing any of
# them gets none of them.
GROUP_KILL_AVAILABLE = all(
    hasattr(os, name) for name in ("setsid", "getpgid", "killpg")
)


def _terminate(proc: "subprocess.Popen[str]") -> None:
    """Kill everything *proc* leads, or *proc* alone where that is all there is."""
    if GROUP_KILL_AVAILABLE:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except OSError:
            # Already reaped, or the group went away between getpgid and
            # killpg. Fall through: kill() on an exited process is a no-op.
            pass
    proc.kill()


def run_isolated(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: "str | os.PathLike[str] | None" = None,
    env: Optional[dict] = None,
) -> "subprocess.CompletedProcess[str]":
    """`subprocess.run(capture_output=True, text=True, timeout=...)`, group-safe.

    Raises `subprocess.TimeoutExpired` (carrying whatever output was captured)
    and `FileNotFoundError` exactly as `subprocess.run` does, so a caller keeps
    the handlers it already had.
    """
    popen_kwargs: dict[str, Any] = {}
    if GROUP_KILL_AVAILABLE:
        popen_kwargs["start_new_session"] = True
    with subprocess.Popen(  # nosec B603
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(
                list(cmd), timeout, output=stdout, stderr=stderr
            ) from None
        except BaseException:
            _terminate(proc)
            raise
    return subprocess.CompletedProcess(list(cmd), proc.returncode, stdout, stderr)
