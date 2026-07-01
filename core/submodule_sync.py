"""submodule_sync.py — one-shot `harness sync` for harness/ submodule consumers.

Root cause (J of 5-meta-pattern convergence plan): submodule bump is a 4-step
manual process — commit harness → push harness → pull consumer → commit
consumer → push consumer. Friction is high; developers tend to work around
missing features instead of bumping the submodule. Workarounds accumulate
as patches, masking the real bug.

This module consolidates the 4 steps into one function:

    sync_submodule(submodule_path, push=True)

Pre-conditions (asserted):
  - Working tree is clean (else: SubmoduleSyncError)
  - Submodule is a valid git repo with origin remote

CLI:
  python3 -m core.submodule_sync --submodule harness [--no-push]

Commonality: framework-level. Any consumer repo using harness/ as a git
submodule can use `harness sync` to update.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional


class SubmoduleSyncError(RuntimeError):
    """Raised when sync_submodule cannot complete."""


# ---------------------------------------------------------------------------
# Helpers — thin wrappers around `git` CLI
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Optional[Path] = None,
         check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run subprocess with text mode + sane defaults."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
        timeout=60,
    )


def _git(cwd: Path, *args: str) -> str:
    """Run `git <args>` in cwd, return stdout (stripped). Raise on non-zero exit."""
    result = _run(["git", "-C", str(cwd), *args])
    return (result.stdout or "").strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_remote(submodule_path: Path, remote: str = "origin") -> bool:
    """Fetch from remote. Return True on success, False if offline."""
    try:
        _git(submodule_path, "fetch", remote)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def behind_count(submodule_path: Path, remote: str = "origin",
                  branch: str = "main", fetch: bool = True) -> int:
    """Return commit count behind `remote/branch`.

    Returns -1 if offline (cannot fetch) or remote branch unknown.
    Returns 0 if up-to-date.

    When fetch=True (default), fetches from remote before counting.
    Pass fetch=False if the caller already fetched to avoid a double fetch.
    """
    if fetch:
        if not fetch_remote(submodule_path, remote=remote):
            return -1

    try:
        result = _run(
            ["git", "-C", str(submodule_path), "rev-list", "--count",
             f"HEAD..{remote}/{branch}"]
        )
        return int((result.stdout or "0").strip())
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired):
        return -1


def is_working_tree_clean(repo_path: Path) -> bool:
    """Return True if repo_path has no uncommitted changes."""
    try:
        result = _run(["git", "-C", str(repo_path), "status", "--porcelain"])
        return (result.stdout or "").strip() == ""
    except subprocess.CalledProcessError:
        return False


def current_sha(submodule_path: Path) -> str:
    """Return current HEAD SHA (short form)."""
    return _git(submodule_path, "rev-parse", "--short", "HEAD")


def sync_submodule(
    submodule_path: Path,
    message_template: str = "chore(harness): bump submodule to v {short_sha}",
    push: bool = True,
    remote: str = "origin",
    branch: str = "main",
) -> dict:
    """Pull ff-only + commit + push harness submodule in one shot.

    Pre-conditions (asserted):
      - submodule_path is a valid git repo
      - working tree is clean
      - remote branch exists

    Returns a dict with keys: {short_sha, behind_count, pushed, message}.

    Raises SubmoduleSyncError on any failure.
    """
    if not submodule_path.is_dir():
        raise SubmoduleSyncError(f"submodule path does not exist: {submodule_path}")

    # Step 1: check working tree
    if not is_working_tree_clean(submodule_path):
        raise SubmoduleSyncError(
            f"working tree is not clean in {submodule_path}. "
            "Commit or stash changes before syncing."
        )

    # Step 2: fetch
    if not fetch_remote(submodule_path, remote=remote):
        raise SubmoduleSyncError(
            f"failed to fetch {remote} in {submodule_path}. "
            "Check network connectivity."
        )

    # Step 3: compute behind count (fetch already done at Step 2)
    n_behind = behind_count(submodule_path, remote=remote, branch=branch, fetch=False)
    if n_behind < 0:
        raise SubmoduleSyncError(
            f"failed to determine commit count behind {remote}/{branch}"
        )

    if n_behind == 0:
        return {
            "short_sha": current_sha(submodule_path),
            "behind_count": 0,
            "pushed": False,
            "message": "already up-to-date",
        }

    # Step 4: pull ff-only
    try:
        _git(submodule_path, "merge", "--ff-only", f"{remote}/{branch}")
    except subprocess.CalledProcessError as e:
        raise SubmoduleSyncError(
            f"failed to ff-merge {remote}/{branch}: {e.stderr or e.stdout}"
        )

    # Step 5: get new SHA
    new_sha = current_sha(submodule_path)
    commit_msg = message_template.format(short_sha=new_sha)

    # Step 6: in the parent repo (caller), commit + push
    # We don't know the parent repo path from here, so the CLI wrapper handles
    # this. Return the metadata needed.
    return {
        "short_sha": new_sha,
        "behind_count": n_behind,
        "pushed": push,
        "message": commit_msg,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Sync harness submodule (pull ff-only + commit + push)."
    )
    parser.add_argument(
        "--submodule",
        type=Path,
        default=Path("harness"),
        help="Path to the harness submodule (default: harness).",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Remote name (default: origin).",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Branch name (default: main).",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip the push step (for offline / dry-run).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check behind count, do not sync.",
    )
    parser.add_argument(
        "--message",
        default="chore(harness): bump submodule to v {short_sha}",
        help="Commit message template (uses {short_sha}).",
    )
    args = parser.parse_args()

    submodule = args.submodule.resolve()
    if not submodule.is_dir():
        print(f"[harness-sync] ERROR: submodule not found: {submodule}",
              file=sys.stderr)
        return 2

    if args.check_only:
        n = behind_count(submodule, remote=args.remote, branch=args.branch)
        if n == -1:
            print("[harness-sync] ERROR: fetch failed (offline?)", file=sys.stderr)
            return 3
        sha = current_sha(submodule)
        print(f"[harness-sync] {sha} is {n} commits behind {args.remote}/{args.branch}")
        return 0 if n == 0 else 1

    push = not args.no_push
    try:
        result = sync_submodule(
            submodule,
            message_template=args.message,
            push=push,
            remote=args.remote,
            branch=args.branch,
        )
    except SubmoduleSyncError as e:
        print(f"[harness-sync] FAILED: {e}", file=sys.stderr)
        return 19  # exit code 19 = sync failure
    except KeyError as e:
        raise SubmoduleSyncError(f"Unknown placeholder in message template: {e}") from e

    n = result["behind_count"]
    sha = result["short_sha"]
    if n == 0:
        print(f"[harness-sync] OK — already up-to-date ({sha})")
        return 0

    print(f"[harness-sync] OK — pulled {n} commit(s); new SHA: {sha}")

    # Commit the submodule pointer bump in the parent repo
    parent_repo = submodule.parent
    submodule_name = submodule.name
    commit_msg = result["message"]
    try:
        _run(["git", "-C", str(parent_repo), "commit", "-m", commit_msg, "--", submodule_name])
    except subprocess.CalledProcessError as e:
        print(f"[harness-sync] FAILED: parent-repo commit failed: {e.stderr or e.stdout}",
              file=sys.stderr)
        return 19

    if push:
        try:
            _run(["git", "-C", str(parent_repo), "push", args.remote, "HEAD"])
        except subprocess.CalledProcessError as e:
            print(f"[harness-sync] FAILED: parent-repo push failed: {e.stderr or e.stdout}",
                  file=sys.stderr)
            return 19
        print(f"[harness-sync] Pushed: {commit_msg}")
    else:
        print(f"[harness-sync] (--no-push) Committed locally: {commit_msg}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())