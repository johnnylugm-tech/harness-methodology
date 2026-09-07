"""Round 108 站C — one reader, two legal shapes, two different return values.

`core/quality_gate/artifact_consistency.acceptance_criteria_from_text` is the
framework's single definition of what an acceptance criterion is. The Phase 1
prompt authorises two spellings and it reads both — differently:

    bullets under `**Acceptance criteria**`  ->  the criterion's full text
    `#### AC-1.1` headings                   ->  the identifier, and nothing else

Measured 2026-09-08 over the five corpus projects that write the heading
shape — taskq, taskq-cc, taskq-final, taskq-new, taskq-sn:

    411 criteria
    2,706 characters returned
    166,157 characters those criteria occupy in the documents
    -> 163,451 characters (98.4%) never reach a consumer

Every check that reads a criterion's CONTENT is therefore structurally blind
on those five projects. `check_ac_verifier_is_nameable` (Round 87) is the
clearest case: it looks for "owned by the test harness" in a criterion, and on
a heading-shape SRS it is handed `AC-1.1` and can never find it.

This is Round 83 站3 one shape over. That round fixed exactly this for the
bullet branch — `(.+)$` under MULTILINE stopped at the first newline, so every
consumer got a criterion's first line only, and `check_ac_verifier_is_nameable`
saw zero of taskq-cc-new's 95 subjects. `tests/test_ac_traceability.py::
test_a_wrapped_acceptance_criterion_is_read_past_its_first_line` pins that
half. The heading branch kept the same defect in its most extreme form: not
the first line, the identifier.

CORPUS EFFECT, MEASURED BEFORE AND AFTER ON ALL FIFTEEN PROJECTS

    criteria per requirement   identical, 15/15
    declared identifiers       none lost; taskq-cc 92 -> 94, taskq-sn 94 -> 95
    new violations             none, from any of the three AC checks
    violations REMOVED         2 `ac_parse_gap` rows, naming three identifiers
                               between them — taskq-cc's ['AC-N1', 'AC-N2']
                               and taskq-sn's ['AC-C4.1'], which the framework
                               had itself reported as "unchecked, not clean":
                               it could see them in the file and could not
                               attribute them, because they sit inside
                               criterion bodies it was discarding

So the change removes two of the framework's own could-not-read reports and
adds no accusation to any project. What it fixes is the next project that
writes the heading shape and puts something in the criterion that a checker
was meant to see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

#: The heading shape, with the content Round 87's check exists to find sitting
#: where only the body can carry it.
_HEADING_SRS = """\
# SRS

### FR-01: Task CRUD

**Acceptance criteria**

#### AC-1.1
Creating a task returns 201 — measurement / interpretation boundary is owned
by the test harness per SPEC.md L40.

#### AC-1.2
A duplicate name returns 409, verified by `test_fr01_duplicate_name`.

### FR-02: Listing

**Acceptance criteria**

#### AC-2.1
Listing is cursor-paginated.
"""


def _project(root: Path, srs: str) -> Path:
    (root / "01-requirements").mkdir(parents=True, exist_ok=True)
    (root / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    return root


def test_a_heading_shape_criterion_is_read_past_its_identifier() -> None:
    """The defect itself. Before this round the answer was `["AC-1.1", ...]`."""
    from core.quality_gate.artifact_consistency import acceptance_criteria_from_text

    criteria = acceptance_criteria_from_text(_HEADING_SRS)

    assert len(criteria["FR-01"]) == 2, (
        f"the criterion boundary moved — two criteria must not become one: "
        f"{criteria['FR-01']}")
    assert "returns 201" in criteria["FR-01"][0], (
        f"the criterion's body was dropped and only its identifier survived, "
        f"so nothing reading its content can see what it says: "
        f"{criteria['FR-01'][0]!r}")
    assert "409" in criteria["FR-01"][1], criteria["FR-01"][1]


def test_the_identifier_still_comes_first() -> None:
    """Reverse control. Three consumers find the id with `_AC_ID.search`.

    `check_ac_identifiers`, `check_ac_deferral_targets` and every TEST_SPEC
    join read the identifier out of the returned string. Carrying the body is
    only safe while the identifier is still in there — and measured over the
    corpus, the declared-id set only ever GREW (taskq-cc 92 -> 94, taskq-sn
    94 -> 95, the rest unchanged) and lost nothing.
    """
    from core.quality_gate.artifact_consistency import (
        _AC_ID, acceptance_criteria_from_text,
    )

    criteria = acceptance_criteria_from_text(_HEADING_SRS)
    for req_id, items in criteria.items():
        for item in items:
            found = _AC_ID.search(item)
            assert found, f"{req_id}: no identifier in {item[:60]!r}"
            assert item.startswith(found.group(0)), (
                f"{req_id}: the identifier is no longer the first thing in "
                f"the criterion, which is what `_AC_ID.search` and every "
                f"`[:80]` message excerpt rely on: {item[:60]!r}")


def test_the_check_that_was_blind_on_this_shape_can_see(tmp_path: Path) -> None:
    """Round 87's check, on the shape five corpus projects use.

    It reports zero here before the fix — not because the SRS is clean, but
    because the sentence it looks for is in the part that was thrown away.
    """
    from core.quality_gate.artifact_consistency import check_ac_verifier_is_nameable

    violations = check_ac_verifier_is_nameable(_project(tmp_path, _HEADING_SRS))

    assert len(violations) == 1, (
        f"expected FR-01 to be reported for naming the harness as its own "
        f"verifier; got {[str(v) for v in violations]}")
    assert violations[0].rule_id == "FR-01"
    assert "1 of 2" in violations[0].message, violations[0].message


def test_the_bullet_shape_is_unchanged() -> None:
    """Negative control: the branch that already returned text keeps doing so."""
    from core.quality_gate.artifact_consistency import acceptance_criteria_from_text

    bullets = acceptance_criteria_from_text(
        "# SRS\n\n### FR-01: Task CRUD\n\n**Acceptance criteria**\n\n"
        "- AC-1.1: Creating a task returns 201.\n"
        "- AC-1.2: A duplicate name returns 409.\n"
    )
    assert bullets["FR-01"] == [
        "AC-1.1: Creating a task returns 201.",
        "AC-1.2: A duplicate name returns 409.",
    ], bullets
