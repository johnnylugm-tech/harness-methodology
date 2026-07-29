"""Submodule guard — detect uncommitted edits and remote drift in git submodules.

Root cause (from E2E 2026-06-28): `git submodule update --remote` silently clobbers
uncommitted edits in a submodule that lives on a detached HEAD. The check for the
OPPOSITE problem (local behind remote) is `core.doctor._check_submodule_behind` —
non-blocking, and since Round 25 站3b it runs on demand in `doctor` rather than on
every phase advance, where it was the only network call on the critical path.
This module provides:

  - `check_uncommitted_edits(submodule_path)`: detect files modified, staged, or
    untracked-but-not-ignored. Returns list of relative paths.
  - `check_behind_remote(submodule_path)`: extract from old `_check_submodule_drift`,
    returns commit count behind origin/main, or -1 if offline / unknown.
  - `assert_safe_to_update(submodule_path)`: raise SubmoduleGuardError if any
    uncommitted edit would be lost on `git submodule update --remote`.

Both `check_*` functions are non-blocking (return values); `assert_safe_to_update`
is the blocking form used by pre-flight and the pre-commit hook.

Commonality: phase-agnostic. Called by pre_flight.check_submodule_safety (Step 0),
by harness_cli._check_submodule_drift (refactor delegate), and by the optional
pre-commit hook installed via setup-git-hooks.sh.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


class SubmoduleGuardError(RuntimeError):
    """Raised when a submodule state would be clobbered by `git submodule update --remote`."""


def _run_git(args: list[str], cwd: Path, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run git in `cwd`, return CompletedProcess; never raises (errors via returncode)."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + args,
        capture_output=True, text=True, timeout=timeout,
    )


def is_submodule(submodule_path: Path) -> bool:
    """Return True if the path looks like a git submodule (has its own .git)."""
    sub = submodule_path
    if not (sub / ".git").exists() and not sub.joinpath(".git").is_file():
        return False
    # Sanity: must be inside a parent repo with .gitmodules
    parent = sub.parent
    while parent != parent.parent:
        if (parent / ".gitmodules").exists():
            return True
        parent = parent.parent
    return False


def check_uncommitted_edits(submodule_path: Path) -> list[Path]:
    """Detect any uncommitted edit in the submodule.

    Includes: modified files (` M`), staged files (`M ` / `MM`), and untracked
    files not matched by .gitignore. Returns relative paths from submodule root.

    Silent skip (returns []) when submodule is not a git repo or git fails —
    the caller decides whether that is acceptable (pre-flight: blocking;
    phase-advance advisory: non-blocking).
    """
    sub = Path(submodule_path)
    if not is_submodule(sub):
        return []

    proc = _run_git(["status", "--porcelain", "--untracked-files=normal"], cwd=sub)
    if proc.returncode != 0:
        return []

    edits: list[Path] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # Porcelain format: XY filename — X=index status, Y=worktree status.
        # Both empty and `??` are untracked (need explicit handling).
        if line.startswith("?? "):
            rel = line[3:].strip()
        else:
            # First two chars are XY, then a space, then the path
            rel = line[3:].strip() if len(line) >= 4 and line[2] == " " else line[2:].strip()
        if rel:
            edits.append(Path(rel))
    return edits


def check_behind_remote(submodule_path: Path) -> int:
    """Return commit count behind origin/main; -1 if offline / unknown.

    Refactor of `_check_submodule_drift`'s HEAD-comparison logic. Non-blocking;
    silent skip on any git failure (offline, no creds, no remote).
    """
    sub = Path(submodule_path)
    if not is_submodule(sub):
        return -1

    fetch = _run_git(["fetch", "origin"], cwd=sub, timeout=30)
    if fetch.returncode != 0:
        return -1

    local = _run_git(["rev-parse", "HEAD"], cwd=sub)
    remote = _run_git(["rev-parse", "origin/main"], cwd=sub)
    if local.returncode != 0 or remote.returncode != 0:
        return -1

    local_sha = local.stdout.strip()
    remote_sha = remote.stdout.strip()
    if local_sha == remote_sha:
        return 0

    rev_list = _run_git(
        ["rev-list", "--left-right", "--count", f"{local_sha}...{remote_sha}"],
        cwd=sub,
    )
    if rev_list.returncode != 0:
        return -1
    parts = rev_list.stdout.strip().split()
    if len(parts) < 2:
        return -1
    try:
        return int(parts[1])
    except ValueError:
        return -1


def assert_safe_to_update(submodule_path: Path) -> None:
    """Raise SubmoduleGuardError if any uncommitted edit would be lost.

    Use before running `git submodule update --remote`. The error message
    includes the remediation steps the developer must take.
    """
    edits = check_uncommitted_edits(submodule_path)
    if not edits:
        return

    sample = ", ".join(str(p) for p in edits[:5])
    more = f" (+{len(edits) - 5} more)" if len(edits) > 5 else ""
    raise SubmoduleGuardError(
        f"harness/ submodule has {len(edits)} uncommitted edit(s): "
        f"{sample}{more}. "
        f"Commit submodule changes first, or use "
        f"`git submodule update --remote --no-fetch` to preserve."
    )


def _cli() -> int:
    """Entry point for the pre-commit hook. Exits 0 if safe, 1 if unsafe."""
    parser = argparse.ArgumentParser(
        description="Submodule guard — pre-flight check before commit / push."
    )
    parser.add_argument(
        "--submodule",
        default="harness",
        help="Path to submodule (default: harness/).",
    )
    parser.add_argument(
        "--mode",
        choices=["assert-safe", "check-edits", "check-behind"],
        default="assert-safe",
    )
    args = parser.parse_args()

    sub = Path(args.submodule).resolve()

    if args.mode == "assert-safe":
        try:
            assert_safe_to_update(sub)
            return 0
        except SubmoduleGuardError as e:
            print(f"[submodule-guard] BLOCKED: {e}", file=sys.stderr)
            return 1
    elif args.mode == "check-edits":
        edits = check_uncommitted_edits(sub)
        for edit_path in edits:
            print(str(edit_path))
        return 0
    else:  # check-behind
        behind = check_behind_remote(sub)
        print(behind)
        return 0


if __name__ == "__main__":
    sys.exit(_cli())
