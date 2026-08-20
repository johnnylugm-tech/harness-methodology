"""Round 65 站0 — half a process-group mechanism is worse than none.

`b12ff21` added `start_new_session=True` to both subprocess call sites, with
the commit message "prevent runaway child processes from becoming orphaned
(PPID 1)". Only one of the two sites also learned to kill the group. Measured
2026-08-20 against `harness/tool_runners.py` as shipped:

    PARENT 74753 pgrp 74748 start_new_session True
    CHILD  74754 74754  GC 74755
    -- after subprocess.run(timeout=2) raised TimeoutExpired --
    74755  PPID 1  PGID 74754   <- still running

`subprocess.run` kills the direct child and nothing else, so before the change
a grandchild was orphaned into the harness's OWN process group (pgid 74793,
measured in the same session) — reachable by the terminal's Ctrl-C and by a
group kill. `start_new_session=True` moved it into a group nobody signals. The
one cleanup path that existed was removed in the name of the leak it created.

`b90e227` then deleted the now-unused `os` and `signal` imports "to satisfy
ruff". Those imports were the missing half's only remaining trace: the linter
reported a half-built mechanism and the report was silenced instead of the
mechanism finished (Round 30's always-empty parameter, one layer down).

These tests execute real processes and look for real survivors. A mock cannot
observe an orphan.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not hasattr(os, "killpg"), reason="process groups are POSIX-only"
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Writes its grandchild's pid where the test can read it without depending on a
# pipe that the timeout path is itself responsible for draining, then blocks.
_SPAWNS_A_GRANDCHILD = (
    "import pathlib, subprocess, sys, time\n"
    "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "pathlib.Path(sys.argv[1]).write_text(str(gc.pid))\n"
    "time.sleep(120)\n"
)

_SLEEPS = "import time; time.sleep(120)"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _died_within(pid: int, seconds: float = 5.0) -> bool:
    """SIGKILL is asynchronous; the survivor question is 'still here later'."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _read_pid(pidfile: Path, seconds: float = 10.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pidfile.is_file():
            text = pidfile.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"{pidfile} never received a grandchild pid")


def _reap(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def test_a_timed_out_run_takes_the_whole_group_with_it(tmp_path):
    """The mechanism, stated once: nothing the command spawned outlives it.

    The wall-clock bound is load-bearing, not decoration. Killing only the
    direct child leaves the grandchild holding the stdout/stderr pipes it
    inherited, so the `communicate()` that follows the kill blocks until the
    LAST descendant exits — measured at 120.09s against a 3s timeout when the
    group kill was disabled. Without this bound the test still passes, on the
    strength of having waited two minutes for the leak to end by itself. The
    leak is the visible half; the stall is the half that stops a gate.
    """
    from core.utils.subprocess_group import run_isolated

    pidfile = tmp_path / "grandchild.pid"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_isolated(
            [sys.executable, "-c", _SPAWNS_A_GRANDCHILD, str(pidfile)],
            timeout=3,
        )
    elapsed = time.monotonic() - started
    grandchild = _read_pid(pidfile)
    try:
        assert elapsed < 30, (
            f"the timed-out call took {elapsed:.1f}s to return on a 3s "
            f"timeout — it is waiting on pipes a surviving descendant still "
            f"holds open"
        )
        assert _died_within(grandchild), (
            f"grandchild {grandchild} outlived the timeout. Isolating the "
            f"process group without killing it removes the only reaper the "
            f"process had"
        )
    finally:
        _reap(grandchild)


def test_an_interrupt_does_not_leave_the_command_running(monkeypatch):
    """KeyboardInterrupt is not an Exception, and the child does not care.

    `subprocess.run` catches it with a bare `except:` — CPython's own comment
    on that line reads "Including KeyboardInterrupt". Narrowing the same
    handler to `except Exception` means Ctrl-C unwinds past the kill, and in
    `_measure` that unwind also releases `source_tree_lock` while a pytest
    nobody is holding still writes into the project tree.
    """
    from core.utils.subprocess_group import run_isolated

    seen: list[int] = []
    real = subprocess.Popen.communicate

    def interrupt_once(self, *args, **kwargs):
        if not seen:
            seen.append(self.pid)
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        run_isolated([sys.executable, "-c", _SLEEPS], timeout=120)

    assert seen, "the interrupt never reached communicate()"
    child = seen[0]
    try:
        assert _died_within(child), (
            f"child {child} survived the interrupt — an `except Exception` "
            f"handler does not see KeyboardInterrupt"
        )
    finally:
        _reap(child)


def test_a_timed_out_tool_does_not_orphan_what_it_spawned(tmp_path, monkeypatch):
    """The live call site. system-verification runs the delivered product.

    Whatever that product starts — a uvicorn bound to a port, a worker — is a
    grandchild of `run_tool`. When the tool times out, the harness reports
    `TIMEOUT: … -2` and the next tool run meets "address already in use".
    """
    from harness import tool_runners
    from harness.toolchains.registry import ToolSpec

    pidfile = tmp_path / "grandchild.pid"
    spec = ToolSpec(
        tool_id="probe",
        cmd=(sys.executable, "-c", _SPAWNS_A_GRANDCHILD, str(pidfile)),
        timeout=3,
        check_cmd="true",
        human_name="probe",
        install_step="builtin",
    )
    monkeypatch.setattr(tool_runners, "get_tool_spec", lambda _tool: spec)

    output, rc = tool_runners.run_tool("probe", str(tmp_path), timeout_override=3)
    assert rc == -2 and "TIMEOUT" in output

    grandchild = _read_pid(pidfile)
    try:
        assert _died_within(grandchild), (
            f"grandchild {grandchild} is still running after run_tool reported "
            f"a timeout, with PPID 1 — the orphan b12ff21 said it was preventing"
        )
    finally:
        _reap(grandchild)


def test_isolating_a_group_has_one_producer():
    """`start_new_session=` names a mechanism only one module may own.

    Two call sites asked for the isolation independently and only one of them
    implemented the other half. The pairing is not something a reader can see
    at a call site, so it is not something a call site may request.

    Scans production sources only; this file names the keyword in prose.
    """
    skip = {".venv", ".git", "__pycache__", "node_modules", "tests"}
    owners: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if any(part in skip for part in rel.parts):
            continue
        if "start_new_session" in path.read_text(encoding="utf-8"):
            owners.append(str(rel))
    assert owners == ["core/utils/subprocess_group.py"], (
        f"start_new_session= appears in {owners}. Requesting process-group "
        f"isolation without killing the group removes the reaper the process "
        f"had; the pair lives in core/utils/subprocess_group.run_isolated"
    )
