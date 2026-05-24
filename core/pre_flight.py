from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
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

def run_phase3_pre_flight(_project_path: Path) -> tuple[bool, list[str]]:
    """
    Execute Phase 3 pre-flight environment checks.
    Returns (success, list_of_error_messages).

    Args:
        _project_path: Unused; kept for API compatibility. Reserved for future
            project-path-dependent checks (e.g. reading .env files).
    """
    errors = []

    # 1. Standard CLI tools required for Phase 3
    required_tools = ["pytest", "ruff"]
    missing_tools = check_cli_tools(required_tools)
    if missing_tools:
        errors.append(f"Missing required CLI tools: {', '.join(missing_tools)}. Please install them (e.g., pip install).")

    # 2. Database/Infrastructure variables typically required
    required_vars = ["DATABASE_URL"]
    missing_vars = check_env_vars(required_vars)
    if missing_vars:
        errors.append(f"Missing required environment variables: {', '.join(missing_vars)}. Please export them or set up .env.")

    # 3. If DATABASE_URL is present, try basic connectivity
    if "DATABASE_URL" not in missing_vars:
        url = os.environ.get("DATABASE_URL", "")
        if url.startswith("postgres"):
            ok, diag = check_database_connectivity(url)
            if not ok:
                if diag == "missing_psql":
                    errors.append("psql not installed — cannot verify DB connectivity. Install with: brew install libpq")
                elif diag == "timeout":
                    errors.append("Database connection timed out (5s) — check if DB is running.")
                else:
                    errors.append("Database connectivity failed for DATABASE_URL. Check host/credentials.")

    return len(errors) == 0, errors