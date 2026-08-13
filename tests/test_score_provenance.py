"""Who produced each dimension's number, and what the denominator counts.

Round 50 站0. Measured on a real Gate 4 that published
``composite_score: 95.2776`` with ``measurement_scope.weight_covered: 1.0`` —
"every unit of quality weight was measured". One of the sixteen dimensions in
that verdict was `performance: 100.0`, a number the agent wrote and the
framework never reproduced: it ran pytest-benchmark, could not read a score
out of the output, and filed the gap in the degradation ledger.

Two separate defects made that number indistinguishable from a measured one.

1. THE VOCABULARY HAS NO WORD FOR IT. `harness/harness_bridge.py` defines
   SCORE_SOURCE_FRAMEWORK ("the framework computed this") and
   SCORE_SOURCE_FRAMEWORK_NA ("the framework confirmed there is nothing to
   compute"). There is no constant for "the agent claimed this and the
   framework could not check it", so nothing could write it down and nothing
   downstream could read it.

2. THE DENOMINATOR COUNTS FIELDS, NOT MEASUREMENTS. `measurement_scope`
   selects `d.score is not None`. An agent-authored float is not None, so it
   raises weight_covered exactly as much as a framework-computed one — which
   is the opposite of what Round 42 站4 built that field to expose.

These tests do not re-judge any recorded verdict (Round 38 / 老闆's ruling for
this round). They constrain what future verdicts are allowed to claim.
"""

from __future__ import annotations

import pytest

from harness.harness_bridge import (
    SCORE_SOURCE_AGENT_UNVERIFIED,
    SCORE_SOURCE_FRAMEWORK,
    SCORE_SOURCE_FRAMEWORK_NA,
    DimResult,
    measurement_scope,
)

_WEIGHTS = {"linting": 0.25, "performance": 0.25, "security": 0.25,
            "test_coverage": 0.25}


def _dim(name: str, score: float | None, source: str | None = None) -> DimResult:
    return DimResult(name=name, score=score, threshold=75.0, score_source=source)


def test_the_three_provenances_are_distinct():
    """A verdict must be able to say which of three things happened."""
    assert len({SCORE_SOURCE_FRAMEWORK,
                SCORE_SOURCE_FRAMEWORK_NA,
                SCORE_SOURCE_AGENT_UNVERIFIED}) == 3


def test_an_unverified_agent_score_is_not_covered_weight():
    """The measured case and the unverified case must not weigh the same."""
    scope = measurement_scope(
        [
            _dim("linting", 100.0, SCORE_SOURCE_FRAMEWORK),
            _dim("performance", 100.0, SCORE_SOURCE_AGENT_UNVERIFIED),
            _dim("security", 100.0, SCORE_SOURCE_FRAMEWORK),
            _dim("test_coverage", 100.0, SCORE_SOURCE_FRAMEWORK),
        ],
        _WEIGHTS,
    )
    assert scope["weight_covered"] == pytest.approx(0.75), (
        "an agent-authored score the framework could not cross-validate was "
        "counted as measured quality surface"
    )
    assert "performance" in scope["dimensions_unscored"]
    assert "performance" not in scope["dimensions_scored"]


def test_a_framework_verified_na_is_still_unscored():
    """Round 35's rule, unchanged: not-applicable is not zero and not covered."""
    scope = measurement_scope(
        [
            _dim("linting", 100.0, SCORE_SOURCE_FRAMEWORK),
            _dim("performance", None, SCORE_SOURCE_FRAMEWORK_NA),
        ],
        _WEIGHTS,
    )
    assert scope["weight_covered"] == pytest.approx(0.25)
    assert "performance" in scope["dimensions_unscored"]


def test_a_dimension_with_no_recorded_provenance_still_counts():
    """Records written before this round carry no score_source.

    They must keep the old meaning rather than silently losing their weight —
    老闆's ruling for Round 50 is that no existing verdict is re-judged. The
    absence of a field is not evidence that the number was unverified.
    """
    scope = measurement_scope(
        [_dim("linting", 100.0, None), _dim("security", 90.0, None)],
        _WEIGHTS,
    )
    assert scope["weight_covered"] == pytest.approx(0.5)
