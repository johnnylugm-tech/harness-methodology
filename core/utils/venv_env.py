"""core/utils/venv_env.py — resolve subprocess env/PATH from a project's own venv.

Bug #129/#128/#123 class: orchestrated harness subprocesses (tool_runners.run_tool,
tool_checks.run_tool_check) inherit the calling shell's ambient PATH, which may not
have the target project's `.venv/bin` positioned before a stray system-installed
tool of the same name — so the harness silently cross-validates against the wrong
tool/interpreter instead of the project's own pinned one. `core.agent_spawner._child_env`
and `core.quality_gate.env_verify._found_on_path_or_venv` each independently solved
this for their own call paths; this module is the shared `.venv`/`venv` bin-dir probe
both were duplicating, so a third call site does not duplicate it again.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_venv_bin_dir(project_root: Path) -> Path | None:
    """<project_root>/.venv/bin (or venv/bin; Scripts on Windows), if present."""
    bin_name = "Scripts" if os.name == "nt" else "bin"
    for venv_dir in (".venv", "venv"):
        candidate = project_root / venv_dir / bin_name
        if candidate.is_dir():
            return candidate
    return None


def venv_scoped_env(
    project_root: Path, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """*base_env* (default os.environ) copy with the project's venv bin dir
    prepended to PATH and VIRTUAL_ENV set, when a venv is found under
    *project_root*. A plain copy of *base_env* when no venv exists — this
    never narrows the caller's existing PATH, only prepends to it."""
    env = dict(base_env) if base_env is not None else os.environ.copy()
    bin_dir = find_venv_bin_dir(project_root)
    if bin_dir is not None:
        existing_path = env.get("PATH", "")
        env["PATH"] = (
            str(bin_dir) + os.pathsep + existing_path if existing_path
            else str(bin_dir)
        )
        env.setdefault("VIRTUAL_ENV", str(bin_dir.parent))
    return env
