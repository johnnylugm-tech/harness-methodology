"""PR 4 (audit F-1.1 fix) regression test: framework trace score is
authoritative in the gate result, regardless of what the agent wrote.

Tests target the extracted helper `_override_traceability_dim_score`
in harness_bridge. The helper mirrors the architecture CRG override:
runs `compute_trace_dimension` and replaces the agent's `traceability`
score in-place.

Returns (new_dims, changed: bool) — changed=True when the score was
actually modified. Tests verify both the new_dims content and the flag.

Cases covered:
  1. Agent reports optimistic score (100%) → framework 50% wins, changed=True
  2. Agent reports pessimistic score (0%) → framework 80% wins, changed=True
  3. Agent's score matches framework's → no churn, changed=False
  4. compute_trace_dimension errors → agent's score preserved (no crash), changed=False
  5. compute_trace_dimension returns error key → agent preserved, changed=False
  6. Non-traceability dims are passed through unchanged
  7. Input dims are not mutated (returns a new list)
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import patch

import pytest  # noqa: F401 (test infra import)


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@dataclass
class _Dim:
    name: str
    score: float
    threshold: float
    issues: List[Dict[str, Any]] = field(default_factory=list)


def _dims_with_traceability(trace_score: float) -> List[_Dim]:
    return [
        _Dim(name="linting", score=95.0, threshold=90),
        _Dim(name="traceability", score=trace_score, threshold=100),
        _Dim(name="security", score=80.0, threshold=80),
    ]


def _trace_dim_result(merged_pct: float, **kwargs) -> dict:
    base = {
        "merged_pct": merged_pct,
        "4a_fr_to_test_pct": merged_pct,
        "4b_test_spec_pct": merged_pct,
        "passed": merged_pct >= 60.0,
        "threshold_4a": 100,
        "threshold_4b": 60.0,
        "threshold_effective": 100,
        "active_uncoded": [],
        "active_untested": [],
        "blocking": True,
        "error": None,
    }
    base.update(kwargs)
    return base


def test_bridge_overrides_optimistic_agent_score():
    """Agent claims 100%; framework says 50% → framework wins, changed=True."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=100.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(50.0)):
        out, changed = _override_traceability_dim_score(dims, "/fake", 2)
    trace_dim = next(d for d in out if d.name == "traceability")
    assert trace_dim.score == 50.0
    assert changed is True


def test_bridge_overrides_pessimistic_agent_score():
    """Agent claims 0%; framework says 80% → framework wins, changed=True."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=0.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(80.0)):
        out, changed = _override_traceability_dim_score(dims, "/fake", 2)
    trace_dim = next(d for d in out if d.name == "traceability")
    assert trace_dim.score == 80.0
    assert changed is True


def test_bridge_no_op_when_scores_already_match():
    """If the agent happens to write the framework's exact score, changed=False."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=75.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(75.0)):
        out, changed = _override_traceability_dim_score(dims, "/fake", 2)
    trace_dim = next(d for d in out if d.name == "traceability")
    assert trace_dim.score == 75.0
    assert changed is False


def test_bridge_falls_back_to_agent_when_compute_errors():
    """If compute_trace_dimension raises, agent's score is preserved, changed=False."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=80.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               side_effect=RuntimeError("scanner crashed")):
        out, changed = _override_traceability_dim_score(dims, "/fake", 2)
    trace_dim = next(d for d in out if d.name == "traceability")
    assert trace_dim.score == 80.0
    assert changed is False


def test_bridge_keeps_input_when_compute_returns_error_key():
    """If compute_trace_dimension returns error key, keep input unchanged, changed=False."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=42.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(0.0, error="scanner unavailable")):
        out, changed = _override_traceability_dim_score(dims, "/fake", 2)
    trace_dim = next(d for d in out if d.name == "traceability")
    assert trace_dim.score == 42.0  # unchanged from input
    assert changed is False


def test_bridge_passes_through_non_traceability_dims_unchanged():
    """Non-traceability dims are passed through with their scores intact."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=100.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(50.0)):
        out, _ = _override_traceability_dim_score(dims, "/fake", 2)
    linting = next(d for d in out if d.name == "linting")
    security = next(d for d in out if d.name == "security")
    assert linting.score == 95.0
    assert security.score == 80.0


def test_bridge_does_not_mutate_input_dims():
    """Input dims must not be mutated; a new list is returned."""
    sys_path = str(Path(__file__).resolve().parent.parent).replace("\\", "/")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from harness.harness_bridge import _override_traceability_dim_score
    dims = _dims_with_traceability(trace_score=100.0)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=_trace_dim_result(50.0)):
        _override_traceability_dim_score(dims, "/fake", 2)
    # Input untouched
    assert dims[1].score == 100.0
