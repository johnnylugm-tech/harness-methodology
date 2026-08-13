"""Evidence a verdict cites must outlive the verdict (Round 50 站0).

Round 45 站1 established the rule; this is the case it did not reach.

S4 cross-validation writes the raw tool output it judged to
`.sessi-work/harness_verification/<dim>_harness.txt`, and its block message
tells the operator to go read that file. `cli/phase_cmds.py` deletes
`.sessi-work/` wholesale at every phase transition — deliberately, because
stale artifacts there caused the next phase's gate to skip re-computation.

Both behaviours are individually correct. Together they mean the audit trail
for a gate verdict is gone one advance later.

Measured 2026-08-13: a Gate 4 recorded a cross-validation gap for the
performance dimension at 06:19 UTC and published PASS at 06:29. Asked ten
days later which S4 branch that second run took, the answer is unavailable —
`.sessi-work/harness_verification/` no longer exists on that project, so the
question cannot be settled from the record. The investigation into this
defect was itself blocked by it.

The rule these tests encode: a directory that advance clears is not a place
a verdict may cite.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.evidence_retention import (
    ADVANCE_CLEARED_DIRS,
    cited_evidence_dir,
)

REPO = Path(__file__).resolve().parents[1]


def test_cited_evidence_is_not_in_a_directory_advance_clears(tmp_path):
    """The one invariant. Everything else here defends it."""
    rel = cited_evidence_dir(tmp_path).relative_to(tmp_path).as_posix()
    for cleared in ADVANCE_CLEARED_DIRS:
        assert not (rel == cleared or rel.startswith(cleared + "/")), (
            f"a verdict cites {rel!r}, and advance-phase deletes {cleared!r} "
            f"at every phase transition"
        )


def test_the_cleared_list_is_not_empty():
    """A guard whose input set is empty passes by vacuum (Round 46)."""
    assert ADVANCE_CLEARED_DIRS
    assert ".sessi-work" in ADVANCE_CLEARED_DIRS


def test_advance_clears_exactly_what_the_list_says():
    """phase_cmds must delete via the constant, not via its own string.

    Otherwise the list above becomes documentation of a behaviour rather than
    the behaviour itself, and the two drift the first time someone adds a
    second scratch directory.
    """
    src = (REPO / "cli" / "phase_cmds.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert ".sessi-work" not in literals, (
        "cli/phase_cmds.py still names the scratch directory as a bare "
        "string literal; the cleanup and the retention rule must read the "
        "same constant or they will disagree"
    )


def test_s4_writes_where_it_says_it_writes():
    """The block message and the write must name one directory.

    Round 24's rule: a [BLOCKED] carries the remediation, not a pointer to a
    place the remediation might be.
    """
    src = (REPO / "harness" / "harness_bridge.py").read_text(encoding="utf-8")
    assert ".sessi-work/harness_verification" not in src, (
        "harness_bridge still points operators at a path inside a directory "
        "advance-phase deletes"
    )
