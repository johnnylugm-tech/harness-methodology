"""Round 105 站2 — the over-spec report published a verdict and not its basis.

`scripts/canonical_diff.py` hands Agent B a per-AC verdict of verbatim /
interpreted / invention. Measured across the thirteen corpus projects,
669 clauses:

    ratio >= 0.85 (verbatim)                         13   1.9%
    ratio >= 0.45 (cleared on measured overlap)     114    17%
    interpreted ONLY because a `DERIVED:` tag is present
                                               378 / 479    79%

taskq-new wrote 124 DERIVED tags and scored 0 inventions; taskq-wow 95 and 0;
taskq-sn wrote 2 in the whole file and scored 109. 658 of the corpus's 661
tags already name a canonical location, so requiring a resolvable citation
would change nothing — the tag IS the verdict. taskq-sn's 109 inventions are
not evidence that it invented 109 things; they are evidence that it did not
write the token.

The second half is the population. The Phase 1 prompt authorises TWO shapes
for an acceptance criterion — its own `#### AC-x.y` heading, or a bolded
bullet under an `**Acceptance criteria**` label — and `_split_ac_clauses`
slices on headings, so:

    taskq-sn    95 AC headings, 0 bullets  -> 117 units, one per criterion
    taskq-done   0 AC headings, 84 bullets ->  22 units, one per REQUIREMENT

Both texts are read (taskq-done's FR-01 body is 2,689 characters and contains
all eight of its criteria), but one project is scored per criterion and the
other per section — and `summary.total_ac` calls both numbers the count of
acceptance criteria. A longer unit has a higher token overlap and needs one
`DERIVED:` for the whole section, so coarser formatting scores better,
mechanically.

`core/quality_gate/artifact_consistency.py:369` has said the first half of
this since Round 87 ("The framework's own AC parser has never seen an AC") and
nothing acted on it.

What this round changes is what the report SAYS, not what it decides. No
threshold moves, no verdict flips, `_split_ac_clauses` is untouched — Round 42
站0's guard pins its contract and swapping it for the SSOT parser was measured
to invent fresh false accusations (taskq-done 0 -> 80 inventions, taskq-new
0 -> 100, taskq-super 0 -> 101). Three additive facts instead: which unit a
record covers, how many acceptance criteria the SRS actually has, and whether
the framework measured the verdict or the project declared it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_SPEC = """\
# Canonical

The service accepts a command string and returns an identifier.
Every rejected command is recorded with the reason it was rejected.
"""

# Criteria as bolded bullets under the labelled block — the shape taskq-done,
# taskq-redo, taskq-api and taskq-plus all use, and the one the heading-based
# splitter reads as a single requirement-sized unit.
_SRS_BULLETS = """\
# Software Requirements Specification

### FR-01: submission

DERIVED: SPEC.md line 3 — the canonical names an identifier, not its width.

**Acceptance criteria**

- **AC-1.1**: `submit` returns an 8-hex identifier.
- **AC-1.2**: a rejected command is recorded with its reason.
- **AC-1.3**: the identifier is stable across a restart.
"""

# The same three criteria as their own headings — taskq-sn's shape.
_SRS_HEADINGS = """\
# Software Requirements Specification

### FR-01: submission

#### AC-1.1

`submit` returns an 8-hex identifier.

#### AC-1.2

A rejected command is recorded with its reason.

#### AC-1.3

The identifier is stable across a restart.
"""


def _report(tmp_path: Path, srs_text: str) -> dict:
    from scripts.canonical_diff import build_diff_report

    tmp_path.mkdir(parents=True, exist_ok=True)
    srs = tmp_path / "SRS.md"
    srs.write_text(srs_text, encoding="utf-8")
    spec = tmp_path / "SPEC.md"
    spec.write_text(_SPEC, encoding="utf-8")
    return build_diff_report(srs, spec)


# ── the basis of the verdict ────────────────────────────────────────────────

def test_a_verdict_says_whether_the_framework_or_the_project_decided_it() -> None:
    """`interpreted` means two different things and the report said one word.

    A clause that clears the overlap threshold was measured by this framework.
    A clause that clears nothing and carries `DERIVED:` was declared by the
    party being judged. Agent B's job is to check the second kind; today the
    report gives it no way to tell them apart.
    """
    from scripts.canonical_diff import _split_sentences, compute_over_spec_score

    canonical = _split_sentences(_SPEC)
    invented = "The scheduler must always prefer the oldest queued job."

    declared = compute_over_spec_score(invented, canonical, derived_present=True)
    assert declared["verdict"] == "interpreted"
    assert declared["verdict_basis"] == "derived_tag", (
        "an AC with no measurable overlap passed on the project's own tag and "
        "the report did not say so")

    measured = compute_over_spec_score(invented, canonical, derived_present=False)
    assert measured["verdict"] == "invention"
    assert measured["verdict_basis"] == "token_overlap"

    verbatim = compute_over_spec_score(
        "The service accepts a command string and returns an identifier.",
        canonical, derived_present=True)
    assert verbatim["verdict"] == "verbatim"
    assert verbatim["verdict_basis"] == "token_overlap", (
        "a clause the framework DID measure must not be reported as resting "
        "on the project's tag just because the tag is also present")


def test_the_tag_does_not_change_a_verdict_the_overlap_already_decided() -> None:
    """Reverse control. `verdict_basis` is a report of which branch fired, not
    a new rule: adding or removing the tag must not move a verdict that the
    measured ratio already settled in either direction."""
    from scripts.canonical_diff import _split_sentences, compute_over_spec_score

    canonical = _split_sentences(_SPEC)
    text = "The service accepts a command string and returns an identifier."
    with_tag = compute_over_spec_score(text, canonical, derived_present=True)
    without = compute_over_spec_score(text, canonical, derived_present=False)
    assert with_tag["verdict"] == without["verdict"] == "verbatim"
    assert with_tag["verdict_basis"] == without["verdict_basis"] == "token_overlap"


# ── the population ──────────────────────────────────────────────────────────

def test_the_criterion_count_is_criteria_and_not_requirements(tmp_path) -> None:
    """`total_ac` is the number of units the splitter produced. For a project
    that writes bullets that is the number of REQUIREMENTS, and the report
    called it the number of acceptance criteria."""
    report = _report(tmp_path, _SRS_BULLETS)
    assert report["summary"]["total_ac"] == 1, report["summary"]
    assert report["summary"]["total_acceptance_criteria"] == 3, (
        "the SRS declares three acceptance criteria and the report did not "
        f"say so: {report['summary']}")


def test_both_authorised_shapes_report_the_same_criterion_count(tmp_path) -> None:
    """The Phase 1 prompt permits either shape. The count of criteria is a
    property of the document, not of which shape the project picked."""
    bullets = _report(tmp_path / "b", _SRS_BULLETS)
    headings = _report(tmp_path / "h", _SRS_HEADINGS)
    assert (bullets["summary"]["total_acceptance_criteria"]
            == headings["summary"]["total_acceptance_criteria"] == 3), (
        f"bullets={bullets['summary']} headings={headings['summary']}")
    # And the unit really does differ — that is the fact being surfaced. One
    # requirement-sized score against three criterion-sized ones, over the
    # same three criteria.
    assert bullets["summary"]["total_ac"] == 1
    assert headings["summary"]["total_ac"] == 3


def test_each_record_says_which_unit_it_covers(tmp_path) -> None:
    """A score over one criterion and a score over a whole requirement are not
    comparable numbers, and both appear in `per_ac`."""
    bullets = _report(tmp_path / "b", _SRS_BULLETS)
    assert [r["unit"] for r in bullets["per_ac"]] == ["requirement"]

    # Three records, one per criterion. Asserted as a list, not keyed by
    # label: `_FR_HEADER_RE`'s label class stops at the dot, so `AC-1.1`,
    # `AC-1.2` and `AC-1.3` all report as `AC-1`. That is a separate defect in
    # the same splitter — a reader cannot tell which criterion a score belongs
    # to — and it is recorded rather than fixed here, because Round 42 站0's
    # guard pins this splitter and changing it is a round of its own.
    headings = _report(tmp_path / "h", _SRS_HEADINGS)
    assert [r["unit"] for r in headings["per_ac"]] == ["criterion"] * 3, (
        headings["per_ac"])


def test_the_criterion_count_comes_from_the_shared_parser(tmp_path, monkeypatch) -> None:
    """Counter-proof CP-8's shape, as a test.

    `artifact_consistency` already reads both authorised shapes and its own
    comment names `canonical_diff` as the reader that does not. A second
    faithful implementation here would pass every other test in this file and
    put the same document back on two parsers — the failure Round 97 CP-5b,
    Round 98 CP-11 and Round 99 CP-13b each found in turn. Replacing the one
    definition has to move this report's number.
    """
    import core.quality_gate.artifact_consistency as ac

    monkeypatch.setattr(
        ac, "acceptance_criteria_from_text",
        lambda _text: {"FR-99": ["AC-9.1", "AC-9.2", "AC-9.3", "AC-9.4"]})
    report = _report(tmp_path, _SRS_BULLETS)
    assert report["summary"]["total_acceptance_criteria"] == 4, (
        "canonical_diff did not go through "
        "core.quality_gate.artifact_consistency.acceptance_criteria_from_text "
        "— it is counting acceptance criteria with a parser of its own again")


def test_the_shared_parser_has_one_definition_and_two_entry_points(tmp_path) -> None:
    """`srs_acceptance_criteria(project)` is the path-taking half and must be
    the same answer: canonical_diff needs the text-taking half because its CLI
    accepts an arbitrary `--srs` path."""
    from core.quality_gate.artifact_consistency import (
        acceptance_criteria_from_text, srs_acceptance_criteria,
    )

    project = tmp_path / "proj"
    (project / "01-requirements").mkdir(parents=True)
    (project / "01-requirements" / "SRS.md").write_text(
        _SRS_BULLETS, encoding="utf-8")
    assert (srs_acceptance_criteria(project)
            == acceptance_criteria_from_text(_SRS_BULLETS))


def test_the_machine_readable_block_is_still_stripped_first(tmp_path) -> None:
    """Round 42 站1 removed the machine block before scoring; the criterion
    count must not put it back. A JSON payload is not an acceptance criterion
    and counting one would be this round reintroducing that round's bug."""
    srs = _SRS_BULLETS + (
        "\n## FR Block (machine-readable)\n\n"
        "<!-- JSON:START -->\n```json\n"
        '{"functional_requirements": [{"id": "FR-01"}]}\n'
        "```\n<!-- JSON:END -->\n"
    )
    report = _report(tmp_path, srs)
    assert report["summary"]["total_acceptance_criteria"] == 3, report["summary"]
    assert "functional_requirements" not in json.dumps(report)
