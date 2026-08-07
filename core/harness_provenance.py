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

__all__ = [
    "harness_root", "enforcer_sha", "enforcer_surface",
    "ENFORCER_SURFACE_PATHS", "phase_verdict_staleness",
]

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


def phase_verdict_staleness(
    project: "str | Path", phase: int,
) -> "dict | None":
    """Has the enforcement surface moved since Phase N was accepted?

    Round 43 站4. `state.json::phase_completed[N]` already records the git
    object IDs of `core/quality_gate`, `harness/harness_bridge.py` and
    `harness/gate_configs` as they stood when the phase was accepted (Round 19
    站3, Round 29 站4). Nothing compared them to the present: `core/doctor.py`
    is the only reader and it asks whether the recorded SHA still resolves.

    So when Round 42 站3 turned a missing SRS FR Block from a warning into a
    P2+ block, taskq-api — whose Phase 1 was accepted at `c09fae1`, five
    rounds earlier — failed a check that did not exist when it passed, and
    nothing in the tooling could say so. The operator reads "your Phase 1
    artifact is wrong"; the truth is "the bar moved".

    The verdict does not change. Grandfathering a rule to artifacts accepted
    before it existed would mean the framework can never raise its own bar —
    Round 38's no-waivable-threshold rule, inverted. What this makes possible
    is for the recorded PASS to stop claiming to be current.
    `EX_ADVANCE_GATE_VERDICT_MISSING` already carries the sibling of this rule
    on the other axis: a verdict measured on a different TREE is not a verdict
    for this one; a verdict measured by a different ENFORCER is not a verdict
    under this one.

    Returns None when there is nothing to say — no recorded verdict for that
    phase, or no recorded surface, or the surface is unchanged. Otherwise
    ``{"moved": [path, ...], "recorded": {...}, "current": {...}}``, listing
    only the paths whose object ID differs. Paths recorded as ``"unknown"``
    (git was unavailable when the verdict was made) are skipped: an absent
    measurement is not a changed one (Round 32/35 — could-not-measure is not a
    finding). Never raises; provenance metadata must not be able to block.
    """
    from core.state_io import load_state
    try:
        state = load_state(project, lenient=True)
    except Exception:  # pylint: disable=broad-exception-caught
        # Same reasoning as enforcer_sha/enforcer_surface above: provenance
        # metadata must never be able to block. `lenient=True` already routes
        # a corrupt state.json to the degradation ledger, so anything reaching
        # here is unexpected — logged at DEBUG rather than swallowed silently.
        import logging
        logging.getLogger(__name__).debug(
            "phase_verdict_staleness: state.json unreadable for %s", project,
            exc_info=True,
        )
        return None
    entry = (state.get("phase_completed") or {}).get(str(phase))
    if not isinstance(entry, dict):
        return None
    recorded = entry.get("enforcer_surface")
    if not isinstance(recorded, dict) or not recorded:
        return None

    current = enforcer_surface()
    moved = [
        path for path, was in recorded.items()
        if was != _UNKNOWN
        and current.get(path, _UNKNOWN) != _UNKNOWN
        and current.get(path) != was
    ]
    if not moved:
        return None
    return {"moved": sorted(moved), "recorded": dict(recorded),
            "current": dict(current)}
