"""evidence_retention.py — a verdict may not cite a directory that gets deleted.

Round 50 站6. Round 45 站1 established the rule and moved the agent's cited
``tool_output`` under ``.methodology/gate_evidence/``. This is the case that
station did not reach: the file the FRAMEWORK writes about its own run.

S4 cross-validation runs each dimension's tool itself and writes what it saw
to an audit file, then names that file in the message the operator is meant to
act on ("see the audit file in ..."). The file was written under
``.sessi-work/``, and ``cli/phase_cmds.py`` clears ``.sessi-work/`` at every
phase transition — deliberately, because stale artifacts there made the next
phase's gate skip re-computation.

Both behaviours are individually correct. Together they mean the audit trail
for a gate verdict is gone one advance later.

Measured 2026-08-13: taskq-api's Gate 4 recorded a cross-validation gap for
`performance` at 06:19 UTC and published PASS at 06:29. Asked ten days later
which S4 branch that second run took, the answer is unavailable — the project
has no ``harness_verification/`` directory, so the question cannot be settled
from the record. The investigation into this defect was stopped by it.

Scope, stated so it is not mistaken for more: this makes the audit file
survive the phase transition. A second run of the SAME gate still overwrites
its predecessor's file, exactly as before. That is a different question
(per-run retention) and this module does not answer it.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ADVANCE_CLEARED_DIRS", "cited_evidence_dir"]

# Project-relative directories `advance-phase` deletes at every phase
# transition. The cleanup reads this list; so does the rule above it. Adding a
# scratch directory here is what makes it cleared, which is what keeps the two
# from drifting.
ADVANCE_CLEARED_DIRS: tuple[str, ...] = (
    ".sessi-work",
)


def cited_evidence_dir(project: "str | Path") -> Path:
    """Where the framework writes evidence a verdict's message points at.

    Under ``.methodology/gate_evidence/`` because that is the directory a
    clone gets and the one Round 45 站1 already named as where a verdict's
    proof lives — one place, not a second one alongside it.
    """
    from core.quality_gate.gate_evidence_store import EVIDENCE_DIR_RELPATH
    return Path(project) / EVIDENCE_DIR_RELPATH / "harness_verification"
