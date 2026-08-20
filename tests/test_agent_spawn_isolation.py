"""Round 66 站0 — giving up on a subprocess is not the same as reaping it.

Round 65 built `core.utils.subprocess_group.run_isolated` (setsid and killpg
are one mechanism with two ends) and wired two call sites. The site that most
needed it was not one of them: `core/agent_spawner.py` spawns `claude -p`
through a bare `subprocess.run(timeout=task_timeout)`, and a headless agent is
precisely a process that starts other processes.

Measured on taskq-cc's live Phase 4 run, 2026-08-21 04:10-04:20:

    load averages: 28.41 42.03 40.49
    99 processes with PPID 1; every one of their pgid leaders already dead
    25 concurrent `pytest 03-development/tests --cov=03-development/src`
    306 `multiprocessing.spawn` workers
    1781.7 CPU-seconds already burned by the orphans alone
    6 harness processes queued on .methodology/.mutation_exclusive.lock

The orphaned command is, verbatim, the coverage command the Phase 4 prompt
tells the agent to run. `.methodology/degradations.jsonl` carries 11
`wall-clock timeout at 600s` entries, three of them fired within one second of
each other at 04:13:57, and `/tmp/gate1delta_FR-01.log` records what happened
next: `task_timeout escalated 600 -> 1200` and a re-dispatch — launched on top
of a previous attempt that was still running.

Two ends, again, and this time they are `timeout=` and the reaper: a call that
carries a timeout is a call that intends to kill, and killing has to mean
killing the group. These tests execute real processes and look for real
survivors, because a mock cannot observe an orphan.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_subprocess_group import _died_within, _read_pid, _reap

pytestmark = pytest.mark.skipif(
    not hasattr(os, "killpg"), reason="process groups are POSIX-only"
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Stands in for the `claude -p` CLI. It ignores its argv, exactly as the real
# CLI ignores the shape of a prompt it cannot finish, starts one grandchild
# (the agent's `pytest --cov`), records that pid where the test can read it
# without depending on a pipe the timeout path itself has to drain, and blocks.
_FAKE_AGENT = (
    "import pathlib, subprocess, sys, time\n"
    "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "pathlib.Path(__file__).with_suffix('.pid').write_text(str(gc.pid))\n"
    "time.sleep(120)\n"
)


def _fake_agent_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text("#!" + sys.executable + "\n" + _FAKE_AGENT, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_a_timed_out_agent_does_not_orphan_what_it_started(tmp_path, monkeypatch):
    """The live call site: `AgentSpawner.spawn`'s own wall-clock budget.

    The wall-clock bound is load-bearing, not decoration (Round 65 站4 learned
    this the expensive way): a surviving grandchild still holds the
    stdout/stderr pipes it inherited, so the `communicate()` after the kill
    blocks until the LAST descendant exits. Without the bound this test passes
    on the strength of having waited two minutes for the leak to end by itself.
    """
    from core import agent_spawner as mod

    cli = _fake_agent_cli(tmp_path)
    monkeypatch.setattr(mod.shutil, "which", lambda name: str(cli) if name else None)

    spawner = mod.AgentSpawner(tmp_path)
    started = time.monotonic()
    result = spawner.spawn(
        role="developer", prompt="probe", context={}, task_timeout=3, phase=4,
    )
    elapsed = time.monotonic() - started

    assert result["status"] == "TIMEOUT"
    grandchild = _read_pid(cli.with_suffix(".pid"))
    try:
        assert elapsed < 30, (
            f"the timed-out spawn took {elapsed:.1f}s to return on a 3s budget "
            f"— it is waiting on pipes a surviving descendant still holds open"
        )
        assert _died_within(grandchild), (
            f"grandchild {grandchild} outlived the agent's timeout with PPID 1. "
            f"An agent is a process that starts processes; killing only the CLI "
            f"leaves its whole tool tree running against the project"
        )
    finally:
        _reap(grandchild)


# Driver for the SIGTERM test. Runs inside a real `harness_cli._dispatch`, so
# the exit code and the reaping are the shipped ones, not a reconstruction.
_SIGTERM_DRIVER = """
import argparse, contextlib, io, os, signal, sys
sys.path.insert(0, {repo!r})
import harness_cli
from core.utils.subprocess_group import run_isolated

# Install the handler the way the shipped CLI does — through main(). `--help`
# is the cheapest real path through it; argparse exits before any subcommand
# runs, and the handler is installed before argparse is reached.
sys.argv = ["harness_cli.py", "--help"]
with contextlib.redirect_stdout(io.StringIO()):
    try:
        harness_cli.main()
    except SystemExit:
        pass
assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN), \\
    "harness_cli.main() did not install a SIGTERM handler"

SPAWNER = (
    "import pathlib, subprocess, sys, time\\n"
    "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\\n"
    "pathlib.Path({pidfile!r}).write_text(str(gc.pid))\\n"
    "time.sleep(120)\\n"
)

def _run(_args):
    run_isolated([sys.executable, "-c", SPAWNER], timeout=120)
    return 0

args = argparse.Namespace(func=_run, project={project!r}, command="probe")
# argv carries --project so that a crash bundle, if this ever reaches its own
# timeout instead of the signal, is written under the tmp project and not into
# the harness repo (core.errors._project_from_argv falls back to cwd).
sys.exit(harness_cli._dispatch(args, ["--project", {project!r}]))
"""


def test_a_terminated_run_reaps_the_tree_it_started(tmp_path):
    """`kill <PID>` from outside must not be a third way to abandon a tree.

    The workflow prompts hand a background PID to an agent and, at the poll
    cap, tell it to walk away. Telling it to kill instead only helps if the
    harness handles the signal: SIGTERM's default disposition terminates the
    process outright, so no `finally` runs, `run_isolated`'s
    `except BaseException` never fires — and after Round 65 the agent's group
    is a NEW session, unreachable from the outside. Unhandled, the honest
    `kill` would leave a deeper orphan than the abandonment it replaced.

    Routed through the real `_dispatch`, whose `except KeyboardInterrupt` has
    returned EX_KEYBOARD_INTERRUPT since Round 13 — a terminated run is an
    interrupted run, not a [HARNESS-BUG] crash.
    """
    pidfile = tmp_path / "grandchild.pid"
    driver = tmp_path / "driver.py"
    driver.write_text(
        _SIGTERM_DRIVER.format(
            repo=str(REPO_ROOT), pidfile=str(pidfile), project=str(tmp_path),
        ),
        encoding="utf-8",
    )

    proc = subprocess.Popen(  # nosec B603
        [sys.executable, str(driver)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        try:
            grandchild = _read_pid(pidfile, seconds=30.0)
        except AssertionError:
            if proc.poll() is not None:
                _, err = proc.communicate()
                pytest.fail(f"the driver died before it started anything:\n{err}")
            raise
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("the terminated run never exited")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    try:
        assert _died_within(grandchild, seconds=10.0), (
            f"grandchild {grandchild} survived SIGTERM to the harness process. "
            f"Terminating without handling the signal skips every reaper the "
            f"run had"
        )
        assert proc.returncode == 130, (
            f"a terminated run exited {proc.returncode}; EX_KEYBOARD_INTERRUPT "
            f"(130) is the code _dispatch already assigns to a run somebody "
            f"stopped"
        )
    finally:
        _reap(grandchild)
