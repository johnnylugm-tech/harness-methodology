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

__all__ = ["harness_root", "enforcer_sha", "enforcer_surface", "ENFORCER_SURFACE_PATHS"]

_UNKNOWN = "unknown"

# Round 29 Station 4: the three paths whose code is the producer of every gate
# verdict.  Recording their git object IDs (tree or blob hash) alongside
# enforcer_sha makes provenance survive rebase — while the commit SHA becomes
# unreachable, the subtree/blob hashes of the enforcement surface are unchanged
# (verified experimentally: 01bb3bb4 → 7154768 rebase, same three IDs).
# This list is deliberately explicit and short — adding a path requires
# justifying why its code materially changes gate verdicts.
ENFORCER_SURFACE_PATHS: tuple[str, str, str] = (
    "core/quality_gate",
    "harness/harness_bridge.py",
    "harness/gate_configs",
)


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
    except Exception:  # pylint: disable=broad-exception-caught
        # Deliberately broad — see docstring.
        return _UNKNOWN


@lru_cache(maxsize=1)
def enforcer_surface() -> dict[str, str]:
    """Return ``{path: git_object_id}`` for each enforcement-surface path.

    The object ID is the output of ``git rev-parse HEAD:<path>`` — a tree hash
    for directories, a blob hash for files.  These survive rebase (verified
    experimentally in Round 29 Station 4) and answer "did the enforcement code
    change?" even when the commit SHA is unreachable.

    Returns ``{"<path>": "unknown", ...}`` on any failure.  Never raises.
    """
    root = harness_root()
    result: dict[str, str] = {}
    for rel_path in ENFORCER_SURFACE_PATHS:
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "rev-parse", f"HEAD:{rel_path}"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                result[rel_path] = proc.stdout.strip()
            else:
                result[rel_path] = _UNKNOWN
        except Exception:
            # Same reasoning as enforcer_sha() above — provenance metadata
            # must never be able to block a gate.  Every failure mode
            # degrades to "unknown" for this path.  Logged at DEBUG so it
            # is visible when needed but silent in normal operation.
            result[rel_path] = _UNKNOWN
            import logging
            logging.getLogger(__name__).debug(
                "enforcer_surface: git rev-parse failed for %s", rel_path,
                exc_info=True,
            )
    return result
