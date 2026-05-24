from __future__ import annotations
import os
import shutil
import subprocess
from typing import Union

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
