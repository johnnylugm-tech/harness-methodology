"""Round 19 站3 — which enforcer produced a verdict.

A gate result records what was measured and what the thresholds were. It did
not record WHO measured it, so a verdict could change with no trace of why.

taskq's P3, from its own artifacts:

    11:24  Gate 2 BLOCK, composite 96.7   (.methodology/decision_logs)
    11:31  harness 7c60859 — traceability merged_pct was being compared against
           the wrong sub-threshold (a real bug; the fix is right)
    11:32  taskq bumps the harness submodule
    11:38  Gate 2 PASS,  composite 96.7   — the same number

Nothing in taskq's tree says the second run used a different enforcer. Reading
the artifacts alone, a gate flipped on identical evidence. The submodule bump
is in taskq's git log, but the gate result — the thing an auditor reads — has
no link to it.

Recording the harness SHA on each verdict costs one subprocess call at
finalize-gate and makes that question answerable from the artifact itself.

Deliberately NOT a version string: this framework is consumed as a git
submodule and has no VERSION file, so the commit IS the version. `-dirty` is
appended when the working tree has uncommitted changes, because a verdict
produced by modified-but-uncommitted enforcement is not reproducible from the
SHA alone — the exact situation during framework development, which is when
gate behaviour changes most often.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

__all__ = ["harness_root", "enforcer_sha"]

_UNKNOWN = "unknown"


def harness_root() -> Path:
    """This framework's repo root, by absolute path, independent of cwd.

    Same derivation as core.utils.script_loader.harness_scripts_dir(), whose
    docstring records two separate incidents of miscounting `.parent` levels.
    """
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def enforcer_sha() -> str:
    """The harness commit that is enforcing, `-dirty`-suffixed if uncommitted
    changes are present; ``"unknown"`` when git is unavailable or this is not a
    checkout.

    Cached: the answer cannot change within one process, and finalize-gate is
    not a place to spend repeated subprocess calls. Never raises — provenance
    metadata must not be able to block a gate.
    """
    root = harness_root()
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if sha.returncode != 0 or not sha.stdout.strip():
            return _UNKNOWN
        value = sha.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            value += "-dirty"
        return value
    except (OSError, subprocess.SubprocessError):
        return _UNKNOWN
