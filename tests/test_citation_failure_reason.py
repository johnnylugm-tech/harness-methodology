"""Round 33 站0/站4 — a citation the validator could not parse is not a file
that does not exist.

`unresolvable_citations` has one fallback for every input `_CITATION` does not
match: treat the whole string as a path, fail to resolve it, and report
`"<the whole string> (no such file)"`. So a reviewer who writes a perfectly
good citation in a shape the regex has not learned yet is told the file is
missing — about a file that is right there.

That is the incident `4bdc0fb` describes in its own commit message:

    SRS.md:972 (FR-05 §10 verification array missing AC-05-6)

parsed as a path, resolved as nothing, and reported as "no such file even
though the file exists and the line is in range". All 4/4 Phase 1 approvals
on taskq-full's run-all were blocked on it.

4bdc0fb taught the regex that one shape. It is a correct change and this file
keeps it. What it did not touch is the branch that turned an unrecognised
shape into a false claim about the filesystem — so the next variant
(`SRS.md:972 — note`, a comma-separated pair, a nested paren) produces the
identical wrong sentence and the identical debugging session. Round 26 fixed
the dash-range shape the same way, one shape at a time, which is what
4bdc0fb's own message calls "a whack-a-mole".

Round 24 站1's rule applies here verbatim: a BLOCK must name the reason it
actually has. "I could not parse this" and "this file is not on disk" are
different reasons with different fixes, and only one of them is true.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.core]


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "\n".join(f"line {n}" for n in range(1, 51)) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_an_unparseable_citation_is_not_reported_as_a_missing_file(project):
    """The shape 4bdc0fb did not add: an unparenthesised trailing note.

    Rejecting it is a defensible contract decision. Reporting it as "no such
    file" is not — SRS.md exists, line 12 exists, and the reviewer is sent to
    look for a file that was never missing.
    """
    from core.quality_gate.agent_b_approvals import unresolvable_citations

    bad = unresolvable_citations(project, ["SRS.md:12 — FR-05 note"])
    assert bad, "the citation should still be refused"
    joined = " ".join(bad)
    assert "no such file" not in joined, (
        "an unparseable citation was reported as a missing file; SRS.md is on "
        "disk and line 12 is in range, so the message sends the reviewer after "
        f"a defect that does not exist: {bad}"
    )
    assert "SRS.md:12 — FR-05 note" in joined, (
        "the reason must quote what was actually received, or the reviewer "
        "cannot tell which citation to rewrite"
    )


def test_a_genuinely_missing_file_still_says_so(project):
    """The discriminating half. `no such file` must keep meaning what it says,
    or this change trades one wrong message for another."""
    from core.quality_gate.agent_b_approvals import unresolvable_citations

    bad = unresolvable_citations(project, ["01-requirements/NOPE.md:3"])
    assert any("no such file" in b for b in bad), bad


def test_a_whole_file_citation_without_a_line_is_still_a_path(project):
    """A citation with no `:line` is documented as a whole-file reference. It
    parses; it is not the unparseable case, and a real file must pass."""
    from core.quality_gate.agent_b_approvals import unresolvable_citations

    assert unresolvable_citations(project, ["01-requirements/SRS.md"]) == []


def test_the_shapes_4bdc0fb_and_round_26_added_still_parse(project):
    """Regression cover for both prior one-shape-at-a-time fixes, so station 4
    cannot fix the reason-reporting by narrowing the regex again."""
    from core.quality_gate.agent_b_approvals import unresolvable_citations

    assert unresolvable_citations(project, [
        "SRS.md:12",                       # plain
        "SRS.md:12-20",                    # Round 26, dash range
        "SRS.md:12:4",                     # legacy line:column
        "SRS.md:12 (FR-05 §10 note)",      # 4bdc0fb, parenthesised annotation
    ]) == []


def test_an_out_of_range_line_is_reported_as_out_of_range(project):
    """Third distinct reason, already correct — pinned so the split between
    reasons stays a split."""
    from core.quality_gate.agent_b_approvals import unresolvable_citations

    bad = unresolvable_citations(project, ["01-requirements/SRS.md:900"])
    assert any("50 lines" in b for b in bad), bad
