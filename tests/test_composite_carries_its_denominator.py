"""Round 42 站0 — a composite score with no denominator is not comparable.

`harness/ssi/scripts/score.py:431` computes

    overall_score = weighted_sum / weight_sum

where `weight_sum` accumulates only the dimensions that were actually scored.
`harness/harness_bridge.filter_enabled_dimensions` (via `_DIM_TO_FEATURE`)
removes `mutation_testing`, `architecture` and `adversarial_review` from the
list before that loop whenever `.methodology/harness_config.json` says so —
the three dimensions the framework scores itself, and the three hardest to
pass. Removing one therefore *raises* the mean, and the file that removes it
is committed by the project being judged.

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
`quality_manifest.json` all carry `dimensions_disabled`. It did not make it
visible *next to the number it changes*, and `weight_covered` exists nowhere in
the repo. Round 37's rule was that the denominator travels with the number;
this is the same rule one level up.

This round does not zero a disabled dimension (Round 35: could-not-measure is
not zero) and does not forbid disabling one — a JS project with no mutmut is a
real case. It makes the score state its own scope.
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
        disabled=("mutation_testing",),
    )
    assert scope["weight_covered"] == 0.24
    assert scope["weight_total"] == 0.32
    assert scope["dimensions_scored"] == ["architecture", "linting", "type_safety"]
    assert scope["dimensions_disabled"] == ["mutation_testing"]


def test_a_dimension_that_could_not_be_measured_leaves_the_denominator_too():
    """A `None` score is out of the average, and the scope says so.

    Round 35 keeps it from being scored 0; this keeps it from being invisible.
    """
    scope = measurement_scope(
        _dims(linting=100.0, type_safety=None, mutation_testing=79.8, architecture=77.8),
        _WEIGHTS,
        disabled=(),
    )
    assert scope["weight_covered"] == 0.25
    assert scope["dimensions_scored"] == ["architecture", "linting", "mutation_testing"]
    assert scope["dimensions_unscored"] == ["type_safety"]


def test_disabling_a_dimension_cannot_hide_inside_the_number():
    """The taskq-plus/taskq-renew pair, reduced to its mechanism.

    Same four dimension scores; one run has mutation switched off. The
    composite rises — that is the weighted mean doing what a weighted mean
    does — and the scope is what tells the two numbers apart.
    """
    scored = dict(linting=100.0, type_safety=100.0, mutation_testing=79.8, architecture=100.0)
    full = measurement_scope(_dims(**scored), _WEIGHTS, disabled=())
    trimmed = measurement_scope(
        _dims(**{k: v for k, v in scored.items() if k != "mutation_testing"}),
        _WEIGHTS,
        disabled=("mutation_testing",),
    )
    assert full["weight_covered"] > trimmed["weight_covered"]
    assert trimmed["dimensions_disabled"] == ["mutation_testing"]
    assert full["dimensions_disabled"] == []
