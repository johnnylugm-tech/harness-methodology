"""Round 19 站3 — compute_trace_dimension's two verdicts must agree.

The function computes `passed` by AND-ing each component against its own
threshold. Its consumers do not see that: cli/gate_cmds.py and
harness/harness_bridge.py persist `merged_pct` as a single dimension score and
compare it against a single `threshold`, the generic "score >= threshold" shape
every other dimension uses. So the same verdict is derived twice, by two
different formulas, and only one of them was ever tested.

That divergence is not hypothetical — it blocked taskq's Gate 2 for 3 rounds
(7c60859: consumers compared merged_pct against the flat 4a threshold of 100,
so a legitimately-passing 4b of 76% ≥ its own 60% bar read as FAIL). That fix
introduced `threshold_effective` and the eight tests in
test_harness_bridge_trace_override.py, none of which assert the equivalence the
fix exists to establish. This module asserts it directly, over the component
combinations that can actually arise.
"""
from __future__ import annotations

import pytest

from core.quality_gate.spec_tracking_checker import resolve_threshold_effective

# (4a, 4b, 4c) percentages x per-gate thresholds. 4a's bar is always 100; 4b
# and 4c share the 60/80/90 ladder at G2/G3/G4.
GATE_LADDER = {2: 60.0, 3: 80.0, 4: 90.0}


def _passed(pct_4a: float, pct_4b: float, pct_4c: float, ladder: float) -> bool:
    """The AND-of-components verdict, mirroring compute_trace_dimension."""
    return pct_4a >= 100.0 and pct_4b >= ladder and pct_4c >= ladder


@pytest.mark.parametrize("gate,ladder", sorted(GATE_LADDER.items()))
@pytest.mark.parametrize("pct_4a", [0.0, 50.0, 95.0, 99.9, 100.0])
@pytest.mark.parametrize("pct_4b", [0.0, 59.9, 60.0, 76.1, 80.0, 90.0, 100.0])
@pytest.mark.parametrize("pct_4c", [0.0, 60.0, 79.9, 90.0, 100.0])
def test_score_vs_threshold_reproduces_passed_exactly(
    gate: int, ladder: float, pct_4a: float, pct_4b: float, pct_4c: float
):
    """`merged >= threshold_effective` must equal `passed`, always.

    This is the contract every consumer of the persisted score relies on. If it
    can be violated for any reachable combination, then a gate's traceability
    verdict depends on which of the two formulas the reader happened to use.
    """
    merged = min(pct_4a, pct_4b, pct_4c)
    effective = resolve_threshold_effective(
        pct_4a=pct_4a, pct_4b=pct_4b, pct_4c=pct_4c,
        threshold_4a=100.0, threshold_4b=ladder, threshold_4c=ladder,
    )
    assert (merged >= effective) == _passed(pct_4a, pct_4b, pct_4c, ladder), (
        f"gate {gate}: 4a={pct_4a} 4b={pct_4b} 4c={pct_4c} -> merged={merged} "
        f"threshold_effective={effective}; score-vs-threshold says "
        f"{merged >= effective} but passed says {_passed(pct_4a, pct_4b, pct_4c, ladder)}"
    )


def test_the_combination_that_broke_the_previous_fix():
    """Round 19 站3's counterexample, pinned on its own.

    4a=95 fails its 100 bar. 4b=60 passes its 60 bar and is the min, so
    "the threshold of whichever component binds the min" (7c60859's rule)
    yields 60 — and 60 >= 60 reports PASS while `passed` is False. The failing
    component was not the binding one, so its bar never entered the comparison.
    """
    effective = resolve_threshold_effective(
        pct_4a=95.0, pct_4b=60.0, pct_4c=90.0,
        threshold_4a=100.0, threshold_4b=60.0, threshold_4c=60.0,
    )
    merged = 60.0
    assert not _passed(95.0, 60.0, 90.0, 60.0)
    assert merged < effective, (
        "a run with an unmet 4a must not report PASS through the merged score "
        f"(merged={merged}, threshold_effective={effective})"
    )


def test_all_components_passing_keeps_the_binding_components_threshold():
    """The everyday case must stay unchanged: when nothing fails, the effective
    threshold is still the bar of whichever component binds the min, so the
    reported number remains meaningful to a human reading the gate output."""
    effective = resolve_threshold_effective(
        pct_4a=100.0, pct_4b=76.1, pct_4c=80.0,
        threshold_4a=100.0, threshold_4b=60.0, threshold_4c=60.0,
    )
    assert effective == 60.0
    assert min(100.0, 76.1, 80.0) >= effective


def test_compute_trace_dimension_uses_the_same_resolver():
    """The resolver must not become a second implementation of the rule — the
    exact defect class this station exists to close. compute_trace_dimension's
    own output has to agree with it."""
    import inspect

    from core.quality_gate import spec_tracking_checker
    source = inspect.getsource(spec_tracking_checker.compute_trace_dimension)
    assert "resolve_threshold_effective(" in source, (
        "compute_trace_dimension must delegate to resolve_threshold_effective, "
        "not re-derive threshold_effective inline"
    )
