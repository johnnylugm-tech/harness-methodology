"""core/utils/isolated_tools.py — environments a harness tool runs in on its own.

Why this exists. The harness installs its tools and the project under
measurement into ONE interpreter (`sys.executable -m pytest` is how the suite
runs, so the harness runs inside the project's venv). That is right for the
tools that must import the project (pytest, mypy, import-linter) and wrong for
a tool whose own dependency tree can contradict the project's. Measured on
taskq-open: fastapi 0.142.2 requires opentelemetry-api>=1.44.0; semgrep 1.165.0
and 1.166.0 both require ~=1.37.0. No version of either satisfies both, in any
install order, so the same `pip install` that made CI measure the project's
tests made semgrep crash on import and `reliability_lint` fail.

An isolated tool lives in its own venv under `tools_root()`. Installing it
there is not enough, and this is the part that is easy to get wrong:
semgrep's launcher finds `pysemgrep` through PATH, so an isolated `semgrep`
called by absolute path still ran the project venv's broken `pysemgrep`
whenever the project's bin/ came first on PATH (measured). `scoped_env` puts
the isolated bin/ first and drops PYTHONPATH, and every caller goes through it.

STDLIB ONLY: scripts/bootstrap_env.py imports this on whatever interpreter the
operator has, before any venv exists. The pins are NOT here — they live in
harness/toolchains/bootstrap.py, which passes the spec in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

__all__ = [
    "IsolatedInstallError",
    "default_tools_root",
    "tools_root",
    "env_dir",
    "bin_dir",
    "installed_spec",
    "executable",
    "find_executable",
    "scoped_env",
    "ensure",
]

_MARKER = ".harness-isolated.json"


class IsolatedInstallError(RuntimeError):
    """The isolated environment could not be built; the message says why."""


def default_tools_root() -> Path:
    """`$XDG_CACHE_HOME/harness-tools`, else `~/.cache/harness-tools`."""
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "harness-tools"


def tools_root() -> Path:
    """`$HARNESS_TOOLS_DIR`, else `default_tools_root()`."""
    override = os.environ.get("HARNESS_TOOLS_DIR")
    return Path(override) if override else default_tools_root()


def env_dir(name: str) -> Path:
    return tools_root() / name


def bin_dir(name: str) -> Path:
    return env_dir(name) / ("Scripts" if os.name == "nt" else "bin")


def installed_spec(name: str) -> Optional[str]:
    """The pip requirement the environment was built from, or None when it is
    absent or was not finished (the marker is written last)."""
    try:
        data = json.loads((env_dir(name) / _MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    spec = data.get("spec") if isinstance(data, dict) else None
    return spec if isinstance(spec, str) else None


def executable(name: str, exe: Optional[str] = None) -> Optional[Path]:
    """`<env>/bin/<exe>` (default: the environment's own name) when the
    environment is installed and the file exists; None otherwise. Never falls
    back to PATH — a PATH hit is exactly the shared-environment copy this
    module exists to avoid."""
    if installed_spec(name) is None:
        return None
    candidate = bin_dir(name) / (exe or name)
    if os.name == "nt" and not candidate.exists():
        candidate = candidate.with_suffix(".exe")
    return candidate if candidate.exists() else None


def find_executable(exe: str) -> Optional[str]:
    """*exe* inside whichever installed isolated environment holds it, or None.

    The one question the env-contract check asks ("is `semgrep` here?") without
    knowing which environment to look in. os.path only, deliberately: that
    caller runs in tests that patch `os.name`, under which pathlib refuses to
    construct a path at all.
    """
    root = os.environ.get("HARNESS_TOOLS_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "harness-tools")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    bin_name = "Scripts" if os.name == "nt" else "bin"
    for name in names:
        if not os.path.isfile(os.path.join(root, name, _MARKER)):
            continue
        candidate = os.path.join(root, name, bin_name, exe)
        if os.path.exists(candidate):
            return candidate
    return None


def scoped_env(name: str, base_env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """*base_env* (default os.environ) with the isolated bin/ first on PATH and
    the project's import path removed, so nothing of the project's leaks in."""
    env = dict(base_env) if base_env is not None else os.environ.copy()
    existing = env.get("PATH", "")
    env["PATH"] = str(bin_dir(name)) + (os.pathsep + existing if existing else "")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def ensure(
    name: str,
    spec: str,
    *,
    python: Optional[str] = None,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> Path:
    """Make `<tools_root>/<name>` a venv holding exactly *spec*; return its bin/.

    A no-op when the marker already records *spec*; a different spec (a pin
    bump) or an unfinished build rebuilds from scratch. Built in place: a venv
    cannot be moved after the fact (its scripts' shebangs name the path it was
    created at), so completeness is the marker, written last — an interrupted
    install leaves a directory that does not look installed.
    """
    if installed_spec(name) == spec:
        return bin_dir(name)
    # Two callers repairing at once (run-phase and env-check) used to delete
    # each other's half-built venv; the loser's cleanup could then delete the
    # winner's finished one (measured). One builder at a time; whoever waited
    # finds the environment built and returns.
    from core.atomic_io import file_lock

    with file_lock(tools_root() / f".{name}.lock"):
        if installed_spec(name) == spec:
            return bin_dir(name)
        return _build(name, spec, python=python, run=run)


def _build(name: str, spec: str, *, python: Optional[str], run: Callable[..., "subprocess.CompletedProcess[str]"]) -> Path:
    target = env_dir(name)
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)

    steps = (
        [python or sys.executable, "-m", "venv", str(target)],
        [str(bin_dir(name) / "python"),
         "-m", "pip", "install", "--disable-pip-version-check", "--quiet", spec],
    )
    for argv in steps:
        proc = run(argv, capture_output=True, text=True)
        if getattr(proc, "returncode", 1) == 0 and argv is steps[0] and not Path(steps[1][0]).exists():
            # "exited 0" is not "built": without its interpreter the pip step
            # cannot run, and writing the marker would claim a venv that is not there.
            proc = subprocess.CompletedProcess(argv, 1, "", f"venv created no interpreter at {steps[1][0]}")
        if getattr(proc, "returncode", 1) != 0:
            shutil.rmtree(target, ignore_errors=True)
            tail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")[-600:]
            raise IsolatedInstallError(
                f"could not build the isolated environment for {spec!r}: "
                f"`{' '.join(argv[:4])} ...` exited {getattr(proc, 'returncode', '?')}\n{tail}"
            )
    (target / _MARKER).write_text(json.dumps({"spec": spec}), encoding="utf-8")
    return bin_dir(name)
