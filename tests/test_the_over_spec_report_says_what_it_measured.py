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

ROUND 106 站B — the same defect, one layer up

Round 105 answered "the report says one word for two events" by adding a
SECOND field beside the word. That is this repository's own recurring shape:
one contract, two statements. Round 106 puts the distinction in the verdict
and retires `verdict_basis` — `transcribed` / `overlaps_canonical` are what
the framework measured, `cites_canonical` is what the project declared, and
`unmatched_and_uncited` replaces `invention`, which was a claim about the
author's intent that token overlap cannot support.

The declaration is then handed to the reviewer instead of being absorbed:
`citation` carries the `DERIVED:` tag verbatim and `citation_resolves` says
whether the location it names is in the canonical text — True / False /
**None**, where None is "not measured" and not "broken". Measured over 412
corpus citations: 408 resolve, 0 do not, 4 name no resolvable location. So it
is a reading for Agent B, never a threshold; requiring a resolvable citation
would change no verdict, which is the second measurement in two rounds to
veto that idea. A/B over fifteen corpus projects: `over_spec_score`,
`best_match_ratio`, `total_ac`, `total_acceptance_criteria` and
`high_score_count` are unchanged in every record.
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
    party being judged. Agent B's job is to check the second kind; before
    Round 105 the report gave it no way to tell them apart.

    Round 106 站B moved the distinction into the verdict itself and retired
    the `verdict_basis` field that had carried it for one round: two fields
    naming one event is the shape this repository keeps paying for. The
    assertions below are the same three cases, asked of the name.
    """
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    canonical = _split_canonical_units(_SPEC)
    invented = "The scheduler must always prefer the oldest queued job."

    declared = compute_over_spec_score(invented, canonical, derived_present=True)
    assert declared["verdict"] == "cites_canonical", (
        "an AC with no measurable overlap passed on the project's own tag and "
        "the report did not say so")

    measured = compute_over_spec_score(invented, canonical, derived_present=False)
    assert measured["verdict"] == "unmatched_and_uncited"

    transcribed = compute_over_spec_score(
        "The service accepts a command string and returns an identifier.",
        canonical, derived_present=True)
    assert transcribed["verdict"] == "transcribed", (
        "a clause the framework DID measure must not be reported as resting "
        "on the project's tag just because the tag is also present")


def test_the_tag_does_not_change_a_verdict_the_overlap_already_decided() -> None:
    """Reverse control. The declared verdict is the branch that fires when the
    measurement decided nothing, not a new rule: adding or removing the tag
    must not move a verdict the measured ratio already settled."""
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    canonical = _split_canonical_units(_SPEC)
    text = "The service accepts a command string and returns an identifier."
    with_tag = compute_over_spec_score(text, canonical, derived_present=True)
    without = compute_over_spec_score(text, canonical, derived_present=False)
    assert with_tag["verdict"] == without["verdict"] == "transcribed"


def test_no_field_survives_the_verdict_it_was_a_second_statement_of() -> None:
    """`verdict_basis` was Round 105's answer and Round 106 is its retirement.

    A rename that leaves the old field in place is how a report comes to have
    two vocabularies, and the next reader has to know which one is current.
    """
    from scripts.canonical_diff import (
        VERDICTS, _split_canonical_units, compute_over_spec_score,
    )

    canonical = _split_canonical_units(_SPEC)
    score = compute_over_spec_score("anything at all", canonical)
    assert "verdict_basis" not in score
    assert score["verdict"] in VERDICTS


# ── the declaration is handed on, not absorbed ──────────────────────────────

#: `_SPEC` above has no numbered sections, so every `§n` would fail to resolve
#: and a "does not resolve" assertion over it would pass for the wrong reason.
#: These carry the shapes the corpus actually cites: a numbered section and a
#: requirement heading.
_SECTIONED_SPEC = """\
# Canonical

## 1. Overview

The service accepts a command string and returns an identifier.

## 4. Non-functional

### NFR-02: Latency

The p95 is under 200ms.
"""


def test_the_citation_reaches_the_reviewer_verbatim() -> None:
    """Agent B is asked to check A's derivation and was shown a boolean.

    Round 105 measured that 378 of 479 `interpreted` verdicts existed only
    because a tag was present. The tag's own text — the location A says the
    clause comes from — never left `_split_ac_clauses`.
    """
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    tag = "DERIVED: SPEC.md §1 — the queue is FIFO per that section."
    score = compute_over_spec_score(
        f"{tag}\nThe scheduler prefers the oldest queued job.",
        _split_canonical_units(_SECTIONED_SPEC), derived_present=True,
        canonical_text=_SECTIONED_SPEC)
    assert score["citation"] == tag
    assert score["citation_resolves"] is True


def test_a_citation_naming_a_place_that_is_not_there_says_so() -> None:
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    score = compute_over_spec_score(
        "DERIVED: SPEC.md §97 — nothing is there.\nThe scheduler prefers old jobs.",
        _split_canonical_units(_SECTIONED_SPEC), derived_present=True,
        canonical_text=_SECTIONED_SPEC)
    assert score["citation_resolves"] is False


def test_a_citation_the_framework_cannot_check_is_not_reported_as_broken() -> None:
    """Round 32/35 and Round 46 in one field.

    Three states, and the third is the point: no canonical text to look in,
    and a citation naming no resolvable location, are both *unmeasured*. A
    framework that writes False there accuses the project of a broken
    reference it never opened.
    """
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    canonical = _split_canonical_units(_SPEC)
    no_locator = compute_over_spec_score(
        "DERIVED: the canonical spec says jobs are queued.\nJobs are queued.",
        canonical, derived_present=True, canonical_text=_SPEC)
    assert no_locator["citation"] is not None
    assert no_locator["citation_resolves"] is None

    no_canonical = compute_over_spec_score(
        "DERIVED: SPEC.md §1 — FIFO.\nThe scheduler prefers old jobs.",
        [], derived_present=True, canonical_text="")
    assert no_canonical["citation_resolves"] is None

    untagged = compute_over_spec_score("Jobs are queued.", canonical,
                                       canonical_text=_SPEC)
    assert untagged["citation"] is None and untagged["citation_resolves"] is None


def test_the_rationale_half_of_the_tag_is_not_read_as_a_citation() -> None:
    """The direct pin on a measurement that was wrong before it was fixed.

    Scanning the whole `DERIVED:` line for ids made 42 of 412 corpus
    citations unresolvable — every one of them because the RATIONALE half
    mentioned an id ("…the rest is deferred to NFR-99") that the citation
    never claimed was in SPEC.md.
    """
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    score = compute_over_spec_score(
        "DERIVED: SPEC.md §1 — the remainder is deferred to NFR-99.\n"
        "The service accepts a command string.",
        _split_canonical_units(_SECTIONED_SPEC), derived_present=True,
        canonical_text=_SECTIONED_SPEC)
    assert score["citation_resolves"] is True, (
        "NFR-99 appears in the rationale, not the citation, and SPEC.md is "
        "not being asked to contain it")


def test_the_prompt_does_not_promise_a_mechanism_the_framework_lacks() -> None:
    """`spec_phase1.py:518` told Agent A, verbatim, that adding a `DERIVED:`
    tag makes the "framework downgrade evidence_type to over_interpretation".

    It never did. `derived_present` has no reader outside canonical_diff.py,
    and `evidence_type` is a field Agent B writes by hand — read by
    `review_quota` and `review_schema_validator`, written by no framework
    check. A shipped prompt promising a mechanism that does not exist is
    Round 30/43, and it is read by every Phase 1 run.

    Asserted on the RENDERED files, both of them: the sentence lives in one
    generator and inlines into phase1-requirements.js and run-all.js.
    """
    from scripts.workflowgen.generate_workflows import generate, generate_composite

    for name, text in (("phase1", generate(1)),
                       ("run-all", generate_composite("run-all"))):
        assert "downgrades evidence_type" not in text, (
            f"{name} still promises a downgrade no framework check performs")
        assert "cites_canonical" in text, (
            f"{name} does not tell the agent what the tag actually does")


def test_a_requirement_id_resolves_through_the_readers_that_already_answer_it(
) -> None:
    """`NFR-1` and `NFR-01` are one requirement, and this file does not get
    to have its own opinion about that.

    Written with a plain regex, the check charged taskq-new with 25
    unresolvable citations pointing at requirements SPEC.md really declares.
    """
    from scripts.canonical_diff import _split_canonical_units, compute_over_spec_score

    score = compute_over_spec_score(
        "DERIVED: SPEC.md NFR-2 — restated as a testable clause.\nLatency is bounded.",
        _split_canonical_units(_SECTIONED_SPEC), derived_present=True,
        canonical_text=_SECTIONED_SPEC)
    assert score["citation_resolves"] is True


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
