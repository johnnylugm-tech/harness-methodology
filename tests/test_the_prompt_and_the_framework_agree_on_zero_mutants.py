"""Round 110 站1 — the framework changed its answer; the sentence teaching it did not.

Round 80 站2 (`6e3abf17`) made a mutation run that produced no mutants stop
scoring 0.0:

    total == 0 (mutmut)   used to `return True, 0.0, msg`
    total == 0 (Stryker)  used to `return True, 0.0, "Stryker produced 0 mutants."`
    both now              `return False, None, "... nothing was measured, so
                          there is no score"`

    0% means the tests killed nothing and the remedy is assertions; zero
    mutants means nothing was mutated and the remedy is the scope or the tool.

That commit touched exactly one file — `core/quality_gate/mutation_enforcer.py`.
`harness/ssi/prompts/evaluate_dimension.md`, which is what an agent reads,
kept teaching `score = 0 (not 100) when no mutants were produced` for thirty
rounds afterwards.

Round 35's own ledger entry (docs/PROPOSAL_ADJUDICATIONS.md, the unstructured
section at 1246-1253) is about exactly this pairing and said what it needed:

    零分母該不該算「未量測」與這句寫下的規則直接衝突 …
    **再開條件**:下一輪若處理分母保護,這兩條必須放在一起裁決,不能只改一邊。

The ruling was made. Only one side moved.

WHY THIS FILE ASSERTS ON PROSE

The same document already answers this question correctly for the two other
tool-scored dimensions whose tool can come back with nothing to measure —
`### readability` ("the harness returns *no score* (not 100)") and
`### performance` ("score is *None* … not a free 100"). So the wording is not
invented here; it is this document's own, in the two places it got it right.

This is the shape `tests/test_hr16_text_matches_mechanism.py` already uses for
HR-16: pin the text against the mechanism it describes, and keep a check on
the mechanism in the same file so that changing the mechanism forces the text
to be re-read rather than silently outliving it.

WHAT THIS FILE CANNOT DO — measured, not guessed

It reads for words, so a sentence carrying the right words and the opposite
meaning passes. Counter-proved: replacing the paragraph with

    "there is no score problem — record 100 and move on"

leaves all five assertions green. That is the same limit
`test_hr16_text_matches_mechanism.py` has, and naming it here is the point —
the guard makes the drift that actually happened (a formula that kept teaching
0 for thirty rounds) impossible to repeat silently; it does not make the
section true. A reviewer still has to read it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
PROMPT = REPO / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"

#: The dimensions whose tool can legitimately come back with nothing to score.
#: Named, not derived: the property is about what the framework returns for
#: each, and that is a fact about three specific code paths, not a pattern.
_NOTHING_TO_MEASURE_DIMENSIONS = ("mutation_testing", "readability", "performance")


def _section(name: str) -> str:
    """The `### <name>` block, up to the next heading of the same or higher level."""
    text = PROMPT.read_text(encoding="utf-8")
    start = re.search(rf"^### {re.escape(name)}\b.*$", text, re.M)
    assert start, (
        f"evaluate_dimension.md has no `### {name}` section. Renaming it also "
        f"breaks core/quality_gate/srs_nfr_validate.py, which reads the "
        f"dimension names off these headings."
    )
    rest = text[start.end():]
    end = re.search(r"^#{2,3} ", rest, re.M)
    return rest[: end.start()] if end else rest


def test_the_mutation_section_does_not_teach_a_zero_for_an_unmeasured_run():
    """`score = 0` is the answer Round 80 站2 removed from the framework.

    `tool_score=0` is excluded, and the exclusion is an instance rather than a
    guess: the same section already explains it four paragraphs above — the
    framework writes `score: null` plus `could_not_measure`, and the agent
    writes `tool_score=0` "only because score.py R8 rejects a null one; it is
    a placeholder that nothing reads as a measurement". That paragraph is what
    a section which HAD kept up with the framework looks like, which is why
    this one being wrong is a drift between two statements in one file rather
    than a document that was never updated.
    """
    section = _section("mutation_testing")

    offenders = [
        m.group(0)
        for m in re.finditer(r"(?<![\w_])score\s*=\s*0\b(?!\.\d)|score\s+of\s+0\b",
                             section)
    ]
    assert not offenders, (
        f"`### mutation_testing` still teaches {offenders} for a run that "
        f"produced no mutants. `_compute_mutation_score` returns "
        f"`(False, None, ...)` for that case — a 0 is what a suite that killed "
        f"none of the mutants scores, and its remedy (write assertions) points "
        f"the opposite way from this one's (fix the mutate scope or the tool)."
    )


@pytest.mark.parametrize("dimension", _NOTHING_TO_MEASURE_DIMENSIONS)
def test_each_tool_dimension_says_an_unmeasured_run_has_no_score(dimension):
    """All three answer the same way, because the framework does.

    `readability` and `performance` have said this since they were written;
    `mutation_testing` is the one that drifted. Parametrised so that a fourth
    dimension gaining a tool that can measure nothing lands here rather than in
    a new file nobody remembers to write.
    """
    section = _section(dimension).lower()
    assert "no score" in section or "score is *none*" in section, (
        f"`### {dimension}` does not tell the agent that a run which measured "
        f"nothing has NO score. Every one of these dimensions can have its "
        f"tool come back with nothing to score, and the framework answers "
        f"`None` for all three — a dimension whose prompt is silent about it "
        f"invites the agent to fill the gap with a 0 or a 100."
    )


def test_the_framework_still_answers_none_for_a_run_that_mutated_nothing():
    """Sanity check on the mechanism the text above describes.

    Same reason `test_hr16_text_matches_mechanism.py` keeps
    `test_sab_parser_mechanism_is_threshold_floor` beside its text assertions:
    if the mechanism ever goes back to scoring 0, the prompt has to be
    re-evaluated against it, and that re-evaluation should start here rather
    than from a green test suite.
    """
    source = (REPO / "core" / "quality_gate" / "mutation_enforcer.py").read_text(
        encoding="utf-8"
    )
    assert "nothing was measured, so there is " in source, (
        "core/quality_gate/mutation_enforcer.py no longer says a run that "
        "mutated nothing has no score. If that was deliberate, the "
        "`### mutation_testing` prompt section has to be re-read against the "
        "new behaviour — the two drifted apart for thirty rounds the last time "
        "only one of them moved."
    )
