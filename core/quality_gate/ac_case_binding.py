"""Which TEST_SPEC case verifies which acceptance criterion.

Round 117. `check_ac_test_spec_coverage` asked only whether an AC id appeared
anywhere in TEST_SPEC.md, prose included, while its own message and Agent B's
checklist said "cited by a TEST_SPEC case". With no readable binding, the
trace dimension's 4c credited any passing test whose body contained the
string `NFR-XX` — written by the implementer, reviewed by nobody — and the P3
prompt sent agents to an FR↔NFR table (TRACEABILITY_MATRIX §5, SRS §2's
`NFR Association` column) that no project had.

A case cites an AC in one of the two shapes the corpus already writes, both
bound to one declared test: in its declaration row (any cell — taskq-wow's
Derivation, taskq-sol's Inputs, taskq-done's `[AC-1.1]` beside the name), or
in a sub-assertion whose `applies_to` names the case's `#` (taskq-final,
taskq-new, taskq-open, taskq-cc-new). A case number belongs to the most
recent declaration table: taskq-api writes its NFR sub-assertions under their
own heading below the table they refer to, and across the corpus that rule
leaves no `applies_to` reference without its case.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate.artifact_consistency import (
    _AC_ID,
    _AC_ID_CITED,
    _with_dash,
    srs_acceptance_criteria,
)


def cited_acs(text: str) -> set:
    return {_with_dash(t) for t in _AC_ID_CITED.findall(text)}


def subassertion_columns(header: str) -> dict:
    """`{"rule_id": i, "applies_to": j}` for a sub-assertion table header, else {}."""
    cols = [c.strip().lower() for c in header.split("|")[1:-1]]
    out: dict = {}
    for i, col in enumerate(cols):
        key = "rule_id" if col.startswith("rule") else "applies_to" if col.startswith("applies") else None
        if key and key not in out:
            out[key] = i
    return out if len(out) == 2 else {}


def resolve(token: str, declared: set) -> str:
    """The declared criterion a citation names.

    `_AC_BODY` accepts trailing `-\\d+` segments because taskq-renew numbers
    its criteria `AC-04-3`, so taskq-new's rule_id `AC10.5-422-status` reads
    as `AC-10.5-422`. Trailing segments are dropped only while that reaches
    a declared id; a token that reaches none is returned unchanged.
    """
    candidate = token
    while candidate not in declared:
        shorter = re.sub(r"-\d+$", "", candidate)
        if shorter == candidate:
            return token
        candidate = shorter
    return candidate


def declared_acs(project) -> dict:
    """`{requirement id: [AC ids]}` from SRS.md."""
    return {req: sorted({a for b in bullets for a in _AC_ID.findall(b)})
            for req, bullets in srs_acceptance_criteria(Path(project)).items()}


def ac_case_bindings(project) -> dict:
    """`{AC id: {declared test functions whose case cites it}}`."""
    from core.quality_gate.spec_coverage import _parse_test_spec
    from core.utils.project_layout import ProjectLayout

    declared = {a for acs in declared_acs(project).values() for a in acs}
    out: dict = {}
    for row in _parse_test_spec(ProjectLayout(Path(project)).test_spec_path):
        for ac in row.get("acs", ()):
            out.setdefault(resolve(ac, declared), set()).add(row["test_fn"])
    return out
