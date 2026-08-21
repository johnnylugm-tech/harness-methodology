"""Round 67 站0 — the evidence has to contain the number it is cited for.

Measured on taskq-cc's committed Gate 4 (2026-08-21):

    breakdown.test_coverage.score        = 100.0
    breakdown.test_coverage.tool_output  = .methodology/gate_evidence/gate4/
                                           test_coverage.txt   (205 bytes)

and those 205 bytes are, in full:

      Outliers: 1 Standard Deviation from Mean; 1.5 IQR ...
      OPS: Operations Per Second, computed as 1 / Mean
    287 passed, 12 warnings in 88.80s (0:01:28)

That is the tail of a pytest-benchmark run. It contains no TOTAL row, no
percentage, no coverage report — nothing from which 100.0 could be derived.
It passed `_validate_tool_content` because the `pytest-cov` patterns are an
`any(...)`, and `\\d+ passed` is one of them.

Round 45 closed "the cited file does not exist". Round 32 closed "the cited
file is a stub". This is the third shape: the file exists, is real tool
output, and is the output of a different tool than the one whose number is
beside it.

The sibling dimension in the same finalize got it right —
`integration_coverage.txt` ends `TOTAL 1005 187 81%` — which is why this is a
guard and not a redesign: the rule is already satisfiable, it just is not
required.
"""

from __future__ import annotations

import pytest

from harness.harness_bridge import _validate_tool_content

_BENCHMARK_TAIL = (
    "  Outliers: 1 Standard Deviation from Mean; 1.5 IQR (InterQuartile "
    "Range) from 1st Quartile and 3rd Quartile.\n"
    "  OPS: Operations Per Second, computed as 1 / Mean\n"
    "287 passed, 12 warnings in 88.80s (0:01:28)\n"
)

_REAL_COVERAGE_TAIL = (
    "Name                                       Stmts   Miss  Cover\n"
    "------------------------------------------------------------\n"
    "TOTAL                                       1005    187    81%\n"
    "33 passed, 1 warning in 1.12s\n"
)


@pytest.mark.parametrize("tool", ["pytest-cov", "pytest-cov-integration"])
def test_a_run_with_no_coverage_in_it_is_not_coverage_evidence(tool):
    """`287 passed` proves a suite ran. It does not prove anything was measured."""
    violations = _validate_tool_content(
        _BENCHMARK_TAIL, tool, "test_coverage", inline=False,
    )
    assert violations, (
        f"a {len(_BENCHMARK_TAIL)}-byte pytest-benchmark tail was accepted as "
        f"evidence for {tool}. It carries no TOTAL row and no percentage, so "
        f"the score cited against it cannot have come from it"
    )
    joined = " ".join(violations).lower()
    assert "cover" in joined or "total" in joined or "%" in joined, (
        "the violation has to say what is missing — 'does not match any "
        f"expected output pattern' sends the reader looking for the wrong "
        f"thing. Got: {violations}"
    )


@pytest.mark.parametrize("tool", ["pytest-cov", "pytest-cov-integration"])
def test_a_real_coverage_report_is_still_accepted(tool):
    """The counterweight: this must not become a guard nothing can satisfy.

    Round 27 already recorded the failure mode — too strict manufactures the
    fabrication it is trying to prevent.
    """
    assert _validate_tool_content(
        _REAL_COVERAGE_TAIL, tool, "integration_coverage", inline=False,
    ) == [], "a genuine coverage report was rejected"


def test_the_other_tools_keep_their_current_rule():
    """Only the dimensions whose SCORE is read out of the evidence change.

    ruff's clean run is `All checks passed!` and proves what it needs to; a
    required-pattern layer over every tool would be this round's scope
    creeping into thirty of them.
    """
    assert _validate_tool_content(
        "All checks passed!\n" * 4, "ruff", "linting", inline=False,
    ) == []
