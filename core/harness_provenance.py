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

import re
import subprocess
from functools import lru_cache
from pathlib import Path

__all__ = [
    "harness_root", "enforcer_sha", "enforcer_surface",
    "ENFORCER_SURFACE_PATHS", "phase_verdict_staleness",
    "phase_record_defects",
]

_UNKNOWN = "unknown"

# What a git object id looks like written down. A `phase_completed` entry whose
# `sha` is not one of these is not a commit anybody failed to find — it is not a
# commit at all, and that distinction is the whole of Round 72 站1.
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

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


def phase_record_defects(project: "str | Path", entry: object) -> list[str]:
    """What is wrong with one `phase_completed[N]` entry, as sentences.

    Round 72 站1. `_verify_entry_gate` asked whether the entry EXISTS, which a
    project can satisfy by writing anything at all. taskq-new's phase 8 record
    is, verbatim:

        {"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_ADVANCE",
         "delivered_tree_sha256": "PLACEHOLDER"}

    and every check in this repository accepted it. `doctor`'s
    `_check_milestone_tree_matches_verdict` came closest and still passed it:
    `committed_tree_digest(project, "PLACEHOLDER…")` returns "" because git
    cannot resolve that string, and an empty digest is treated as
    could-not-measure, which Rounds 32/35 established is not a finding. That
    rule is right for a sha git has lost. It is not about a value that was
    never a sha.

    So this asks the question that rule cannot reach: is each recorded value
    the SHAPE of the thing it claims to be, and does the commit it names exist
    in this history.

    SHAPE ONLY, and the boundary is deliberate. Three neighbouring questions
    each already have an owner, and asking them a second time here would be
    Round 38's one-fact-three-enforcers defect:

    * does `delivered_tree_sha256` MATCH the tree of `sha` — `doctor`'s
      `_check_milestone_tree_matches_verdict`;
    * is `sha` an ancestor of HEAD — `_verify_entry_gate`'s P2/P3 branch, which
      pairs the question with `try_recover_dangling_phase_completed` (Round 38).
      A hard failure on ancestry with no self-heal beside it would strand a
      project whose branch was reset, and the first draft of this function did
      exactly that: it turned 22 existing tests red, all of them recording a
      placeholder sha of the right shape, which is the measurement that this
      check does not belong here;
    * is a field absent — records predating Round 44 站2 do not carry
      `delivered_tree_sha256`, and a record predating a field is not a
      violation (Round 39/40).

    Returns [] for a clean record. Never raises.
    """
    _ = Path(project)  # signature parity with the rest of this module
    if not isinstance(entry, dict):
        return [f"the record is a {type(entry).__name__}, not a mapping"]

    defects: list[str] = []
    sha = entry.get("sha")
    if not isinstance(sha, str) or not _SHA1_RE.match(sha):
        defects.append(
            f"sha={sha!r} is not a 40-character git object id — the entry names "
            f"no commit, so `git merge-base --is-ancestor`, `doctor`'s verdict "
            f"re-derivation and `_fr_step_lineage_boundary` all read a value "
            f"that cannot answer them"
        )

    digest = entry.get("delivered_tree_sha256")
    if digest is not None and (
        not isinstance(digest, str) or not _SHA256_RE.match(digest)
    ):
        defects.append(
            f"delivered_tree_sha256={digest!r} is not a 64-character sha256 — "
            f"Round 44 站2 records WHICH TREE the phase's checks read, and this "
            f"value names no tree"
        )
    return defects
