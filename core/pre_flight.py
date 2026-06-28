from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from typing import Union

def check_submodule_safety(submodule_path: Path = Path("harness")) -> tuple[bool, str]:
    """Step 0 of pre-flight: detect uncommitted edits in the harness/ submodule
    that `git submodule update --remote` would silently clobber.

    Returns (ok, diagnostic). On failure, diagnostic is a remediation message
    that includes the count of uncommitted files and the exact paths.

    This is a hard-fail (returns False) when edits exist; callers should raise.
    Silent skip when the path is not a submodule (project-side harness CLI).
    """
    from core.submodule_guard import check_uncommitted_edits, is_submodule
    if not is_submodule(submodule_path):
        return True, "not-a-submodule-skip"
    edits = check_uncommitted_edits(submodule_path)
    if not edits:
        return True, "ok"
    sample = ", ".join(str(p) for p in edits[:5])
    more = f" (+{len(edits) - 5} more)" if len(edits) > 5 else ""
    return False, (
        f"harness/ submodule has {len(edits)} uncommitted edit(s): "
        f"{sample}{more}. "
        f"Commit submodule changes first, or use "
        f"`git submodule update --remote --no-fetch` to preserve."
    )

def check_env_vars(required_keys: list[str]) -> list[str]:
    """Check if required environment variables are set. Returns list of missing keys."""
    missing = []
    for key in required_keys:
        if not os.environ.get(key):
            missing.append(key)
    return missing

def check_cli_tools(tools: list[str]) -> list[str]:
    """Check if required CLI tools are available in PATH. Returns list of missing tools."""
    missing = []
    for tool in tools:
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing

def check_database_connectivity(url: str) -> tuple[bool, Union[str, None]]:
    """Attempt a basic psql connection if URL is a postgres URL.

    Returns (success, diagnostic). diagnostic is "missing_psql" when psql is
    not installed (caller treats differently from "cannot connect"), None on success.
    """
    if not url.startswith("postgres"):
        return True, None
    if shutil.which("psql") is None:
        return False, "missing_psql"

    try:
        proc = subprocess.run(
            ["psql", url, "-c", "SELECT 1"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0, None
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception:
        return False, "connect_error"
