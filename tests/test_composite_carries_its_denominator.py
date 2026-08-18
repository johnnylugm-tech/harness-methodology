"""Round 42 站0 — a composite score with no denominator is not comparable.

`harness/ssi/scripts/score.py:431` computes

    overall_score = weighted_sum / weight_sum

where `weight_sum` accumulates only the dimensions that were actually scored,
so a dimension that produced no number *raises* the mean.

When this round was written there were two ways for a dimension to leave that
sum: it was not measured, or a per-project feature flag removed it from the
gate's list before scoring saw it. Round 60 站2 retired the flags, so only the
first remains — but the reason the denominator has to travel with the number
is unchanged, and the measurement below is what established it.

Measured on the two projects that ran the same 494-line SPEC.md:

    taskq-plus   composite 98.707  over weight 0.86  (13 dimensions;
                 mutation_testing disabled by flag, performance N/A)
    taskq-renew  composite 93.166  over weight 1.00  (all measured)

    renew's own numbers with plus's config (mutation dropped): 94.328

Recomputing plus's 98.707 from `gate4_p6_full.yaml`'s weights reproduces the
committed value to the last digit, so the arithmetic is not in question. What
is in question is that both numbers are published — in `gate{N}_result.json`
and in `templates/QUALITY_REPORT.md:42`'s `| Gate 4 composite score | ≥ 85 |
{value} |` — with nothing beside them saying what they were averaged over. A
reader comparing the two, which is exactly what happened, is comparing 0.86 of
the quality surface against 1.00 of it.

Round 39 站2 made the disabling *visible* — the ledger, `gate_verify.jsonl` and
`quality_manifest.json` all carried `dimensions_disabled`. It did not make it
visible *next to the number it changes*, and `weight_covered` existed nowhere
in the repo. Round 37's rule was that the denominator travels with the number;
this is the same rule one level up.

This round does not zero an unmeasured dimension (Round 35: could-not-measure
is not zero). It makes the score state its own scope. Round 60 站2 went the
rest of the way and removed the switch itself.
"""

from __future__ import annotations

from harness.harness_bridge import DimResult, measurement_scope


_WEIGHTS = {
    "linting": 0.07,
    "type_safety": 0.07,
    "mutation_testing": 0.08,
    "architecture": 0.10,
}


def _dims(**scores: "float | None") -> list[DimResult]:
    return [
        DimResult(name=n, score=s, threshold=0.0) for n, s in scores.items()
    ]


def test_a_published_composite_names_the_weight_it_covered():
    """Three of four dimensions scored — the scope says 0.24 of 0.32."""
    scope = measurement_scope(
        _dims(linting=100.0, type_safety=100.0, architecture=77.8),
        _WEIGHTS,
    )
    assert scope["weight_covered"] == 0.24
    assert scope["weight_total"] == 0.32
    assert scope["dimensions_scored"] == ["architecture", "linting", "type_safety"]


def test_a_dimension_that_could_not_be_measured_leaves_the_denominator_too():
    """A `None` score is out of the average, and the scope says so.

    Round 35 keeps it from being scored 0; this keeps it from being invisible.
    """
    scope = measurement_scope(
        _dims(linting=100.0, type_safety=None, mutation_testing=79.8, architecture=77.8),
        _WEIGHTS,
    )
    assert scope["weight_covered"] == 0.25
    assert scope["dimensions_scored"] == ["architecture", "linting", "mutation_testing"]
    assert scope["dimensions_unscored"] == ["type_safety"]


