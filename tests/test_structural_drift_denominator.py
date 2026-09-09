"""Three of the four things drift measures are never in the file it reads.

Round 111 站F3. `compute_structural_drift` weights four components:

    drift = 0.4*cohesion + 0.3*flow_coverage + 0.2*dead_code + 0.1*hub_risk

It was written against `compute_metrics`' own snapshot, which does supply all
four. But the two GATE consumers read `.sessi-work/crg_metrics.json`, written
by `harness/crg_independent.py`, and that producer emits `community_cohesion`,
`large_functions_*` and `architecture_score` — nothing else. Scanned all 39
baseline/metrics files in the corpus: `flow_coverage`, `dead_code` and
`hub_risk_map` appear zero times.

`_score`'s `default=100` and `.get("ratio", 0)` read all three absences as
"identical to the baseline", so 60% of the weight is permanently zero and
drift lives in [0, 0.4] against a 0.4 threshold — reachable only by cohesion
falling from 100 to 0. Round 35: a component that could not be measured is
not a component that did not move.

Measured against the 13 corpus projects that have both a p4 and a p6
baseline: taskq-renew's cohesion falls 100.0 → 77.8 and reads as 0.0888.
Renormalised over the weight actually present it is 0.2220. No project
crosses 0.4 either way — the number becomes honest without any verdict
changing.

Blood: `harness_bridge._architecture_regression_reason` turns it into a Gate
4 hard block and `cli/checks/gates.py` into a CI hard FAIL.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness.ssi.scripts.crg_analysis import structural_drift

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

#: taskq-renew's real p4 → p6 movement, the largest in the corpus.
_BASELINE = {"community_cohesion": {"score": 100.0}, "architecture_score": 100.0}
_CURRENT = {"community_cohesion": {"score": 77.8}, "architecture_score": 77.8}


def test_absent_drift_components_are_not_read_as_no_change():
    """A component neither side carries leaves the denominator, not the score."""
    result = structural_drift(_BASELINE, _CURRENT)

    assert result["drift"] == pytest.approx(0.2220, abs=1e-4), (
        "a 22.2-point cohesion fall is the whole of what was measured, so it is "
        "the whole of the drift; 0.0888 is that fall diluted by three "
        "components no producer in this tree emits"
    )
    assert result["weight_covered"] == pytest.approx(0.4)
    assert result["weight_total"] == pytest.approx(1.0)
    assert set(result["absent"]) == {"flow_coverage", "dead_code", "hub_risk_map"}


def test_a_snapshot_carrying_all_four_is_unchanged():
    """`compute_metrics` builds all four, so renormalisation is the identity there.

    Without this the fix would be indistinguishable from one that scales every
    reading up — including the one path whose denominator was never wrong.
    """
    baseline = {
        "community_cohesion": {"score": 100.0},
        "flow_coverage": {"score": 100.0},
        "dead_code": {"ratio": 0.0},
        "hub_risk_map": {"critical_count": 0, "total_hubs": 10},
    }
    current = {
        "community_cohesion": {"score": 50.0},
        "flow_coverage": {"score": 100.0},
        "dead_code": {"ratio": 0.0},
        "hub_risk_map": {"critical_count": 0, "total_hubs": 10},
    }
    result = structural_drift(baseline, current)

    assert result["weight_covered"] == pytest.approx(1.0)
    assert result["absent"] == []
    assert result["drift"] == pytest.approx(0.4 * 0.5, abs=1e-4)


def test_nothing_measurable_is_not_zero_drift():
    """No shared component at all means no reading, not a reading of zero."""
    result = structural_drift({"architecture_score": 90}, {"architecture_score": 10})

    assert result["drift"] is None, (
        "returning 0.0 here would publish 'the architecture did not move' about "
        "a comparison that never happened (Round 35)"
    )
    assert result["weight_covered"] == 0.0


def test_no_caller_uses_the_float_only_drift():
    """`compute_structural_drift` has no callers left in production code.

    The four call sites are patched by six mocks across two test files. A
    caller left behind would keep returning the diluted float while the mocks
    and the guards above all pass — which is how the old function stayed
    correct-looking for as long as it did.
    """
    offenders: list[str] = []
    for rel in ("core", "cli", "harness", "scripts"):
        for path in (REPO / rel).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "compute_structural_drift" in text:
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "these still call the float-only drift, whose three absent components "
        "read as no change:\n    " + "\n    ".join(sorted(offenders))
    )


def test_both_gates_print_the_weight_they_covered():
    """The two blocking consumers show the denominator beside the number.

    A hard block on 0.22 means something different when 40% of the weight was
    measurable than when all of it was, and the reader cannot tell from the
    number alone.
    """
    for rel, func in (
        ("harness/harness_bridge.py", "_architecture_regression_reason"),
        ("cli/checks/gates.py", "cmd_crg_arch_check"),
    ):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        node = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == func
        )
        source = ast.unparse(node)
        assert "weight_covered" in source, (
            f"{rel}::{func} blocks on the drift number without saying how much "
            f"of the weight it could measure"
        )
