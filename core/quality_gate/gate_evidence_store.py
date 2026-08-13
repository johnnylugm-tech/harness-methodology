"""Keep the files a gate verdict cites, where the verdict itself is kept.

Round 45 站1. The framework already establishes that a dimension's evidence
is real and already records what it read:

  * ``harness/harness_bridge.py:1211`` refuses a ``tool_output`` that does not
    exist, for every ``requires_tool_execution`` dimension;
  * ``:1227`` fingerprints the file into ``evidence_digest`` (Round 27 站3).

What no one does is keep the file. Every agent-written ``tool_output`` lands
under ``.sessi-work/``, and ``.sessi-work/`` is the first line the harness
itself writes into every project's ``.gitignore``
(``harness/git_strategy.py::_GITIGNORE_ENTRIES``). Measured 2026-08-11 across
five projects' committed gate results: 162 cited files, 13 still present.
taskq, taskq-renew and taskq-api have none at all.

``harness_bridge.py:2650`` states the property this module finishes:

    the verdict and the proof of what it read then live in one file and one
    commit, and cannot be separated by a cleanup of the gitignored work
    directory

The fingerprints survived. The proof did not. A sha256 of a file nobody has
is not proof — it is a claim that cannot be checked, and taskq-advance's
release verdict carries fourteen of them.

This module adds no judgement. It runs BEFORE S3, so the checks that already
exist keep running — on the copy, which is the file a later reader can
actually open.
"""

from __future__ import annotations

import shutil
from pathlib import Path

__all__ = [
    "EVIDENCE_DIR_RELPATH",
    "evidence_dir",
    "persist_cited_evidence",
]

# Under `.methodology/` on purpose: that is the directory a clone gets. The
# whole defect is evidence living somewhere a clone does not.
EVIDENCE_DIR_RELPATH = ".methodology/gate_evidence"


def evidence_dir(project: Path, gate: int) -> Path:
    return Path(project) / EVIDENCE_DIR_RELPATH / f"gate{gate}"


def persist_cited_evidence(project: Path, gate: int, raw: dict) -> list[str]:
    """Copy every cited ``tool_output`` under `.methodology/` and re-point it.

    Mutates ``raw["breakdown"][dim]["tool_output"]`` in place and returns the
    dimensions whose citation moved, so the caller knows whether the result
    file on disk still matches what it is holding. Silent about
    dimensions that cite nothing — a dimension backed by inline
    ``tool_evidence`` has nothing to keep, and S3 already has an opinion about
    a dimension that offers neither.

    Two files are deliberately left where they are:

    * a path that escapes the project root — copying it would launder an
      escape into a citation that resolves, and containment is already S3's
      refusal to make (``harness_bridge.py:1199``);
    * a file over ``values.gate_evidence_max_bytes`` — the citation stays
      pointed at the original and the degradation ledger says why, because a
      thing the framework chose not to do is not the same as a thing that did
      not happen (Round 32/35).
    """
    from harness.harness_bridge import path_escapes_root

    root = Path(project)
    breakdown = raw.get("breakdown")
    if not isinstance(breakdown, dict):
        return []

    max_bytes = _max_bytes(root)
    out_dir = evidence_dir(root, gate)
    moved: list[str] = []

    for dim_name, dim_data in breakdown.items():
        if not isinstance(dim_data, dict):
            continue
        cited = dim_data.get("tool_output")
        if not cited or not isinstance(cited, str):
            continue

        src = root / cited
        try:
            if path_escapes_root(src, root):
                continue
        except (OSError, RuntimeError):
            # Unresolvable path — S3 reports it with the message that call
            # site owns. Copying a path we cannot resolve is not an option.
            continue
        if not src.is_file():
            continue  # S3's "does not exist" violation, unchanged.

        size = src.stat().st_size
        if size > max_bytes:
            from core.degradation_ledger import record_degradation
            record_degradation(
                root, "gate:evidence-too-large",
                f"{dim_name}: '{cited}' is {size} bytes, over the "
                f"{max_bytes}-byte gate_evidence_max_bytes ceiling — not "
                f"copied into {EVIDENCE_DIR_RELPATH}/",
                why=(f"the gate {gate} verdict keeps citing a path that does "
                     f"not survive a clone; raise values.gate_evidence_max_bytes "
                     f"or make the tool write less"), owner="project"
            )
            continue

        dst = out_dir / f"{dim_name}{src.suffix}"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.resolve() != src.resolve():
                shutil.copyfile(src, dst)
        except OSError as exc:
            from core.degradation_ledger import record_degradation
            record_degradation(
                root, "gate:evidence-not-persisted",
                f"{dim_name}: could not copy '{cited}' into "
                f"{EVIDENCE_DIR_RELPATH}/ ({exc})",
                why=(f"the gate {gate} verdict keeps citing a path that does "
                     f"not survive a clone"), owner="harness"
            )
            continue

        relocated = str(dst.relative_to(root))
        if relocated != cited:
            dim_data["tool_output"] = relocated
            moved.append(dim_name)

    return moved


def _max_bytes(project: Path) -> int:
    """The ceiling, stated once in the values registry and read from there.

    `get_value` already falls back to `_VALUE_DEFAULTS` when the project has no
    config or an invalid entry, so there is no second copy of the number here —
    a second copy is the shape this whole round is about.
    """
    from core.harness_config import get_value
    return int(get_value(project, "gate_evidence_max_bytes"))
