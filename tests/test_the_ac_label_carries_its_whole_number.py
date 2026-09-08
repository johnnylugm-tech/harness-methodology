"""Round 109 站1 — `AC-1.1` and `AC-1.2` both reported as `AC-1`.

`scripts/canonical_diff.py`'s `_FR_HEADER_RE` captured its label with the
class `[-\\w]`, which is `[-A-Za-z0-9_]` and does NOT contain a dot. Every
corpus SRS writes its criteria as `#### AC-<fr>.<n>` — the shape the Phase 1
prompt produces — so the label stopped at the dot and every criterion under
one requirement collapsed onto the same name. Measured 2026-09-08:

    project        clauses   unique labels before -> after
    taskq               69          30 -> 69
    taskq-cc           114          44 -> 114
    taskq-final        118          44 -> 118
    taskq-new          124          46 -> 124
    taskq-sn           117          45 -> 117

Two consequences, and the second is the one that matters. A reader of
`srs_vs_spec_diff.json` could not tell which criterion a score belonged to —
six rows reading `AC-1`, six different numbers. And the framework's OTHER
acceptance-criteria parser, `core.quality_gate.artifact_consistency`, names
the same criteria `AC-1.1`: across the fourteen corpus projects that publish
both, the intersection of the two parsers' AC identifiers was **zero**. One
document, two parsers, no id in common — Round 33 / Round 56's shape, hidden
behind the fact that both were individually plausible.

WHY THIS IS NOT THE CHANGE ROUND 42 站0 FORBADE

`tests/test_canonical_diff_phantom_ac.py` pins that a requirement label
carries a digit, and pins it that way because the splitter slices each
clause's body from one match to the NEXT: dropping a match merges its body
into the previous clause, and that file records taskq-renew's NFR-12 body
growing from 9,773 to 13,960 characters when it was tried. That is a change
which REMOVES matches. This one adds characters to what an existing match
captures and removes none: measured across seventeen corpus SRS documents,
match positions and match counts were identical, every clause body was
byte-for-byte the same (643,955 characters in total), and every
`over_spec_score`, `verdict`, `unit` and `summary` field was unchanged. The
only field that moved besides `label` was one `fr_id` — taskq-sn's `AC-C4`
heading, which precedes every FR in the file, has no parent to resolve to and
falls back to its own (now complete) label.

The digit requirement is untouched, and the tests below pin the merge
property directly rather than trusting that argument.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate.artifact_consistency import (
    _AC_ID,
    acceptance_criteria_from_text,
)
from scripts.canonical_diff import _split_ac_clauses

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

#: The shape the Phase 1 prompt produces and every corpus SRS writes. Two
#: criteria under one requirement, so a label that stops at the dot makes them
#: indistinguishable, and a second requirement after them so a dropped match
#: would have somewhere to merge into.
_SRS = """# SRS

### FR-01: Job submission

**Acceptance criteria**

#### AC-1.1
Submitting a job returns exit code 0.

#### AC-1.2
A duplicate job name returns exit code 2.

### FR-02: Worker pool

**Acceptance criteria**

#### AC-2.1
The pool defaults to four workers.
"""


def test_a_dotted_criterion_reports_its_whole_number() -> None:
    """The defect, stated as the identity of the thing being scored.

    Asserted as a set and not a count: a count would also pass if the labels
    were `AC-1`, `AC-1`, `AC-2` and one of them silently disappeared into a
    dict key somewhere downstream.
    """
    labels = [c["label"] for c in _split_ac_clauses(_SRS)]
    assert labels == ["FR-01", "AC-1.1", "AC-1.2", "FR-02", "AC-2.1"], labels


def test_no_body_swallowed_its_neighbour() -> None:
    """Round 42 站0's property, pinned directly instead of argued.

    That round's failure mode was a body GROWING: drop one heading match and
    the text under it merges into the clause before. Any future edit to
    `_FR_HEADER_RE` that loses a match shows up here as one body containing
    the next criterion's text.
    """
    bodies = {c["label"]: c["body"] for c in _split_ac_clauses(_SRS)}

    assert "duplicate job name" not in bodies["AC-1.1"], (
        "AC-1.1's body reaches into AC-1.2 — a heading match was dropped and "
        "its body merged into the clause before it, which is exactly the "
        "regression tests/test_canonical_diff_phantom_ac.py measured at "
        "9,773 -> 13,960 characters on taskq-renew's NFR-12")
    assert "defaults to four workers" not in bodies["AC-1.2"], bodies["AC-1.2"]
    assert bodies["AC-1.1"] == "Submitting a job returns exit code 0.", (
        bodies["AC-1.1"])


def test_the_widening_did_not_invent_a_clause() -> None:
    """Negative control, in the direction this change COULD have gone wrong.

    Adding characters to a capture group cannot drop a match, but a badly
    written addition can create one: `(?:\\.\\d[-\\w]*)*` requires a digit
    after each dot precisely so that a trailing full stop, a version-like
    `v1.2` or an ordinary sentence is not read as part of a requirement id.
    """
    text = (
        "# SRS\n\n"
        "### FR-01: Submission\n"
        "Body one.\n\n"
        "#### AC-1.\n"           # trailing dot, no digit after it
        "Body two.\n\n"
        "# FR — no digit at all\n"
        "Body three.\n"
    )
    labels = [c["label"] for c in _split_ac_clauses(text)]
    assert labels == ["FR-01", "AC-1"], (
        f"got {labels}. `AC-1.` must capture `AC-1` (the dot leads nowhere) "
        f"and a heading with no digit is not a requirement at all — the rule "
        f"tests/test_canonical_diff_phantom_ac.py exists for")


def test_both_parsers_name_the_same_criteria() -> None:
    """The property the defect actually broke, stated across the two readers.

    `canonical_diff` and `artifact_consistency` both answer "which acceptance
    criteria does this SRS declare". Before this round their answers had an
    empty intersection on all fourteen corpus projects that produce both —
    `AC-1` against `AC-1.1` — so any cross-check between them was comparing
    two disjoint sets and finding nothing, forever, without failing.
    """
    from_diff = {
        c["label"] for c in _split_ac_clauses(_SRS)
        if c["label"].upper().startswith("AC")
    }
    from_ssot = set()
    for criteria in acceptance_criteria_from_text(_SRS).values():
        for criterion in criteria:
            match = _AC_ID.search(criterion)
            if match:
                from_ssot.add(match.group(0))

    assert from_diff and from_ssot, (from_diff, from_ssot)
    assert from_diff == from_ssot, (
        f"canonical_diff says {sorted(from_diff)}, artifact_consistency says "
        f"{sorted(from_ssot)}. One document, two parsers, two vocabularies — "
        f"measured across fourteen corpus projects the intersection was zero")
