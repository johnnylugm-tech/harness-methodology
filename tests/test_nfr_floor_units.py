"""A number whose unit is unknown must not become a 0-100 score floor.

Round 46 站0. `derive_gate_score_overrides` reads the first `≥N` out of an
NFR's free-text `target` and raises that dimension's threshold floor to N. The
number's unit is never established. `harness/harness_bridge.py:3696` records
what that costs when it goes wrong — the sibling `quality_targets` path once
fed `p95_latency_ms` in and set performance's floor to **3000**, a floor no
0-100 score can ever clear, so the gate could only ever block, with a message
about a "performance threshold" that named a millisecond budget.

That path was fixed. This one was not.

Station 0's premise 2 measured the four live projects before deciding what to
do here, and it overturned the plan's default (delete the parse):

    taskq          no target-derived floor at all
    taskq-plus     test_assertion_quality 80 (standard 70)  ← target-derived
    taskq-renew    integration_coverage   80 (standard 75)  ← target-derived
    taskq-advance  integration_coverage   80 (standard 75)  ← target-derived

Every one of those is correct — `integration line coverage >= 80%`,
`MI >= 80`, `mutation score >= 70` are all genuinely 0-100. Deleting the parse
would *lower* three projects' floors, and a `%`-only rule would lower one and
discard five correct floors that omit the sign.

So the rule is not "trust less", it is "refuse the impossible": a matched
number above 100 cannot be a 0-100 dimension score, and admitting it produces
a floor nothing can satisfy. Below 100 the current behaviour stands, byte for
byte, on all four projects.
"""

from __future__ import annotations

import pytest

from core.quality_gate.sab_parser import derive_gate_score_overrides

pytestmark = [pytest.mark.core]


def test_a_latency_budget_does_not_become_a_performance_floor():
    """`p99 ≥ 500ms` would set performance's floor to 500 — unreachable."""
    overrides = derive_gate_score_overrides(
        {"NFR-01": "performance"},
        {"NFR-01": {"target": "list endpoint p99 ≥ 500ms is a hard failure"}},
    )
    assert overrides["performance"] <= 100.0, (
        "a floor above 100 can never be cleared — the gate would block forever "
        f"and say nothing useful about why (got {overrides['performance']})"
    )


def test_a_percentage_floor_above_the_standard_is_still_honoured():
    """taskq-renew / taskq-advance's real NFR-10, measured at station 0."""
    overrides = derive_gate_score_overrides(
        {"NFR-10": "integration_coverage"},
        {"NFR-10": {"target": "integration line coverage >= 80% using httpx"}},
    )
    assert overrides["integration_coverage"] == 80.0


def test_a_bare_number_below_100_is_still_honoured():
    """`mutation score >= 70` and `MI >= 80` omit the sign and are still scores."""
    overrides = derive_gate_score_overrides(
        {"NFR-08": "mutation_testing", "NFR-11": "readability"},
        {"NFR-08": {"target": "mutation score >= 70"},
         "NFR-11": {"target": "MI >= 80; CC <= 10"}},
    )
    assert overrides["mutation_testing"] == 70.0
    assert overrides["readability"] == 80.0


def test_the_dimension_standard_survives_a_refused_number():
    """Refusing the number must fall back to the standard, not to zero."""
    overrides = derive_gate_score_overrides(
        {"NFR-01": "performance"},
        {"NFR-01": {"target": "p99 ≥ 500ms"}},
    )
    assert overrides["performance"] == 75.0
