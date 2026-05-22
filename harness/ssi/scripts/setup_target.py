#!/usr/bin/env python3
"""
Setup Target: Resolves folder or clones GitHub repo

Outputs TARGET_PATH to stdout for orchestrator capture.
Initializes git tracking if needed.
"""

import sys
import subprocess
from pathlib import Path
import tempfile
import json

# CRG integration (required — same as ruff/mypy/pytest)
from crg_integration import ensure_ready as _crg_ensure_ready


def clone_repo(github_url):
    """
    Clone GitHub repo to temporary directory

    Args:
        github_url: https://github.com/user/repo or git@github.com:user/repo

    Returns:
        Path to cloned directory
    """
    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix="harness-")

    try:
        # Clone repo
        subprocess.run(
            ["git", "clone", github_url, temp_dir],
            check=True,
            capture_output=True,
            timeout=300,
        )
        return temp_dir
    except subprocess.CalledProcessError as e:
        print(f"Error cloning repository: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def setup_git(repo_path):
    """
    Initialize git tracking if not already a git repo

    Args:
        repo_path: Path to repository

    Returns:
        True if already a git repo, False if just initialized
    """
    repo_path = Path(repo_path)

    # Check if already a git repo
    if (repo_path / ".git").exists():
        return True

    # Initialize git
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "init"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.email", "harness@local"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "config", "user.name", "Harness"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return False
    except subprocess.CalledProcessError as e:
        print(f"Error initializing git: {e}", file=sys.stderr)
        sys.exit(1)


def resolve_target(target):
    """
    Resolve target: clone GitHub repo or use local folder

    Args:
        target: GitHub URL or local path

    Returns:
        Absolute path to target directory
    """
    if target.startswith("http://") or target.startswith("https://"):
        # Clone GitHub repo
        print(f"Cloning repository: {target}", file=sys.stderr)
        target_path = clone_repo(target)
    else:
        # Use local folder
        target_path = str(Path(target).absolute())
        if not Path(target_path).exists():
            print(f"Error: path does not exist: {target_path}", file=sys.stderr)
            sys.exit(1)

    # Setup git tracking
    setup_git(target_path)

    return target_path


def init_crg(repo_path: str, work_dir: str) -> dict:
    """
    Initialize CRG (required — same as ruff/mypy/pytest).

    Writes status to .sessi-work/crg_status.json so all downstream steps
    can read it without re-checking. CRG unavailable → BLOCK (exit 1).
    """
    print("[CRG] Checking Code Review Graph…", file=sys.stderr)

    status = _crg_ensure_ready(repo_path)

    # Write status to work_dir for downstream steps
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)
    status_file = work_path / "crg_status.json"
    with open(status_file, "w") as f:
        json.dump(status, f, indent=2)

    if status.get("available"):
        nodes = status.get("node_count", "?")
        action = status.get("action", "")
        tag = " (auto-built)" if action == "auto_built" else ""
        print(f"[CRG] ✓ Ready — {nodes} nodes{tag}", file=sys.stderr)
        return status

    raise RuntimeError(
        f"CRG is required but not available.\n"
        f"  Reason: {status.get('reason', 'unknown')}\n"
        f"  Install: pipx install code-review-graph\n"
        f"  Then:    code-review-graph build --repo ."
    )


def main():
    if len(sys.argv) < 2:
        target = "."
    else:
        target = sys.argv[1]

    work_dir = sys.argv[2] if len(sys.argv) > 2 else ".sessi-work"

    try:
        target_path = resolve_target(target)

        # Auto-initialize CRG (required — raises RuntimeError if unavailable)
        init_crg(target_path, work_dir)

        # Output target path to stdout for orchestrator capture
        print(target_path)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
