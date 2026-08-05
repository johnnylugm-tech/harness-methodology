"""Delivery scope — the one answer to "which files belong to this project".

Round 37. Every scorer that walks a consumer project's tree needs to know
what is part of the project and what is scratch. Before this module that
question was answered independently in five places, each with its own
denylist:

  core/utils/lang_patterns.py       SKIP_DIRS (node_modules, dist, build, …)
  core/traceability/scanner.py      SKIP_DIRS + a Python-only extras set
  harness/lang_scanners/treesitter_js.py   SKIP_DIRS
  core/traceability/auto_fix_propose.py    "node_modules" only
  scripts/spec_logic_checker.py            "venv"/"__pycache__" only

and a sixth, `harness/git_strategy.py`'s _GITIGNORE_ENTRIES, answered it for
git. A denylist is structurally one directory behind: `.venv/` was added
after the first incident, `.claude/worktrees/` after the second (taskq-renew,
2026-08-05), and the scanner side of that second fix was never made.

Measured cost of the gap: taskq-renew's trace attestation carried 32 FR->code
links pointing into `.claude/worktrees/agent-<id>/` — a Claude Code Agent-tool
scratch worktree present on the developer's disk and absent from a CI
checkout. Local re-derivation produced 80 links / content_sha256 94a71dc…;
CI produced 48 links / 3013d0f… and the ASPICE Traceability Check failed on
every push from Phase 3 onward.

The fix is not a longer denylist. CI checks out exactly what git tracks, so
git's own notion of the project is the only definition that cannot drift from
the delivered tree:

    git ls-files --cached --others --exclude-standard

  --cached                   already delivered
  --others --exclude-standard  written but not committed yet — Phase 3 TDD
                             writes the implementation before it commits it,
                             and a scope that could not see it would report
                             the FR as uncoded and block on work that is
                             sitting right there
  (everything .gitignore excludes is excluded)  .venv, .claude/worktrees,
                             and every future directory nobody has thought of

One line in .gitignore now drives git and every scanner at once.

A project that is not a git repository keeps the pre-Round-37 behaviour: the
SKIP_DIRS denylist. That path is unchanged on purpose — narrowing it would
alter projects that have nothing to do with the defect this closes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

from core.utils.lang_patterns import SKIP_DIRS

__all__ = ["iter_delivered_files", "delivered_file_set", "is_git_repo"]

_LS_FILES = ("git", "ls-files", "--cached", "--others", "--exclude-standard",
             "-z")
_TIMEOUT = 30


def is_git_repo(root: Path) -> bool:
    """True when *root* is inside a git work tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_listing(root_str: str) -> tuple[str, ...] | None:
    """`git ls-files` output for *root_str*, or None when git cannot answer.

    NUL-separated so a path containing a newline (legal on POSIX) cannot be
    read as two files.

    Deliberately NOT cached. The first draft of this module memoised the
    listing per root; core/auto_fix/strategies.py writes a new test file and
    immediately re-scans to verify the fix, and the cached answer — taken
    before the file existed — hid it. A stale view of the tree is the exact
    defect this module was written to remove, so it must not reintroduce one
    at a shorter timescale. `git ls-files` costs a few milliseconds.
    """
    try:
        proc = subprocess.run(
            [_LS_FILES[0], "-C", root_str, *_LS_FILES[1:]],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return tuple(p for p in proc.stdout.split("\0") if p)


def _denylist_walk(root: Path) -> Iterator[Path]:
    """Pre-Round-37 behaviour, kept for non-git directories."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.parts) & SKIP_DIRS):
            yield path


def iter_delivered_files(root: Path) -> Iterator[Path]:
    """Yield every file *root* delivers, sorted, as absolute paths.

    Extension filtering is deliberately NOT done here — callers know their
    own language (see core.utils.lang_patterns.source_extensions). Keeping
    this module extension-agnostic is also what keeps lang_patterns free to
    import it without a cycle.
    """
    root = Path(root)
    # One subprocess, not two: `git ls-files` already fails outside a work
    # tree, so a separate is_git_repo probe would only double the cost.
    listing = _git_listing(str(root))
    if listing is None:
        yield from _denylist_walk(root)
        return
    for rel in sorted(listing):
        path = root / rel
        # A gitlink (submodule) is listed as a single entry that is a
        # directory on disk; its contents belong to the submodule, not here.
        if path.is_file():
            yield path


def delivered_file_set(root: Path) -> set[str]:
    """`iter_delivered_files` as resolved absolute path strings."""
    return {str(p.resolve()) for p in iter_delivered_files(root)}
