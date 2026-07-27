"""Devil's Advocate score-threshold waivers: which dimensions may be waived,
and whether a requested waiver is actually needed.

Round 21 root cause — the adjudication ran before the truth existed.
``_collect_da_waivers`` (cli/gate_cmds.py) decided both "is this waiver
permitted?" and "is this waiver needed?" at gate-prerequisite time, which is
*before* ``finalize_gate`` runs the framework's own independent CRG pass. The
only dimension a waiver has ever targeted — ``architecture`` — is CRG-only:
the agent does not score it, and writes JSON ``null``. So the necessity check
compared the gate's threshold against a value that did not exist yet.

taskq's P6 is the worked example: the recorded waiver's stated premise ("CRG
reports 0 for this hub-and-spoke layout") was false — the framework's own CRG
run scored the same tree **100.0** — yet the waiver was granted and
``da_waiver_needs_human_review`` was raised over a waiver nobody needed.

The split this module encodes:

  * **collection** — the agent declares ``da_waiver.<dim>`` and supplies DA
    evidence; permission is checked against :data:`WAIVABLE_DIMENSIONS`. Runs
    pre-finalize, on agent-authored input.
  * **adjudication** — :func:`adjudicate_waivers` decides whether each
    permitted request is *needed*, using the scores and thresholds the
    framework itself computed. Runs post-scoring, on framework-authored input.

A waiver's only documented justification is CRG's Leiden community detection
misreading an intentional hub-and-spoke layout as low cohesion, so the set of
waivable dimensions is exactly the set of CRG-scored dimensions — one name,
one definition, used by both the permission check and the CRG override path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

# Dimensions scored by the framework's own independent CRG run
# (harness/crg_independent.py), never by the agent.
CRG_ONLY_DIMENSIONS: frozenset[str] = frozenset({"architecture"})

# Dimensions a DA waiver may target. Identical to CRG_ONLY_DIMENSIONS by
# construction, not by coincidence: the waiver exists to absorb a known CRG
# false positive, so a dimension CRG does not score has no waiver rationale.
# Every other dimension is scored by a tool whose output is the finding — a
# low score there is the defect, not an artifact of the measurement.
WAIVABLE_DIMENSIONS: frozenset[str] = CRG_ONLY_DIMENSIONS


@dataclass(frozen=True)
class WaiverAdjudication:
    """Outcome of adjudicating waiver requests against framework scores.

    applied: dimensions whose threshold should actually be zeroed.
    blocked: a request could not be adjudicated — the gate must not proceed.
    notes:   human-readable lines explaining each decision (printed by callers).
    """

    applied: frozenset[str]
    blocked: bool
    notes: tuple[str, ...]


def adjudicate_waivers(
    requested: Iterable[str],
    scored: Sequence[tuple[str, "float | None", float]],
) -> WaiverAdjudication:
    """Decide which requested waivers are needed, from framework-computed scores.

    :param requested: dimension names the agent asked to waive (already
        permission-checked against :data:`WAIVABLE_DIMENSIONS` at collection).
    :param scored: ``(name, score, threshold)`` per dimension, where both values
        are the framework's — ``score`` after any CRG override, ``threshold``
        resolved from the gate config. ``score`` is ``None`` when the dimension
        has no applicable measurement.

    Decision table:

    ==========================  ==================================
    condition                   outcome
    ==========================  ==================================
    dimension absent from gate  not applied (nothing to waive)
    ``score is None``           **blocked** — necessity unknowable
    ``score >= threshold``      not applied — waiver not needed
    ``score < threshold``       applied
    ==========================  ==================================

    The ``None`` case blocks rather than silently applying: zeroing a threshold
    for a dimension the framework has not scored would waive an unknown, which
    is exactly the failure this module exists to prevent.
    """
    by_name = {name: (score, threshold) for name, score, threshold in scored}
    applied: set[str] = set()
    blocked = False
    notes: list[str] = []

    for dim in sorted(set(requested)):
        if dim not in by_name:
            notes.append(
                f"da_waiver '{dim}': not applied — dimension not scored at this gate."
            )
            continue
        score, threshold = by_name[dim]
        if score is None:
            blocked = True
            notes.append(
                f"da_waiver '{dim}': BLOCKED — the framework produced no score for this "
                f"dimension, so whether the waiver is needed cannot be determined. "
                f"Fix: re-run finalize-gate once the framework scores '{dim}' "
                f"(for CRG-scored dimensions, confirm code-review-graph runs cleanly), "
                f"then request the waiver again."
            )
            continue
        if score >= threshold:
            notes.append(
                f"da_waiver '{dim}': not applied — framework score {score:.1f} "
                f"≥ threshold {threshold:.1f} (waiver not needed)."
            )
            continue
        applied.add(dim)
        notes.append(
            f"da_waiver '{dim}': applied — framework score {score:.1f} "
            f"< threshold {threshold:.1f} (score threshold bypassed; flagged for human review)."
        )

    return WaiverAdjudication(
        applied=frozenset(applied), blocked=blocked, notes=tuple(notes)
    )
