"""Tests for gate-internal architecture regression hard-block (item a).

_architecture_regression_reason gates finalize_gate at Gate 4 (P6): a structural
drift >= threshold vs the P4 baseline hard-blocks even if the absolute score
still passes. structural_drift is mocked.

Round 111 站F3 changed what the mock returns: the drift function now hands
back the weight it could cover alongside the number, because three of its
four components are absent from every crg_metrics.json this tree produces
and were being read as 'unchanged'. The assertions below are the same
assertions — a mock returning the old bare float would silently drop out of
the call path, which tests/test_structural_drift_denominator.py::
test_no_caller_uses_the_float_only_drift exists to stop.
"""

import json
from unittest.mock import patch

from harness import harness_bridge


def _measured(drift: float) -> dict:
    """What structural_drift returns, with only cohesion present."""
    return {"drift": drift, "weight_covered": 0.4, "weight_total": 1.0,
            "absent": ["flow_coverage", "dead_code", "hub_risk_map"]}


def _baseline(tmp_path):
    m = tmp_path / ".methodology"
    m.mkdir(parents=True, exist_ok=True)
    (m / "crg_baseline_p4.json").write_text(
        json.dumps({"architecture_score": 100, "_baseline_sha": "abc12345def"}),
        encoding="utf-8",
    )


def test_none_when_not_gate4(tmp_path):
    _baseline(tmp_path)
    assert harness_bridge._architecture_regression_reason(
        str(tmp_path), 3, {"crg": {"drift_threshold": 0.4}}, {}
    ) is None


def test_none_when_no_baseline(tmp_path):
    (tmp_path / ".methodology").mkdir()
    assert harness_bridge._architecture_regression_reason(
        str(tmp_path), 4, {"crg": {"drift_threshold": 0.4}}, {}
    ) is None


def test_blocks_when_drift_exceeds_threshold(tmp_path):
    _baseline(tmp_path)
    with patch("harness.ssi.scripts.crg_analysis.structural_drift",
               return_value=_measured(0.8)):
        reason = harness_bridge._architecture_regression_reason(
            str(tmp_path), 4, {"crg": {"drift_threshold": 0.4}},
            {"architecture_score": 90},
        )
    assert reason is not None
    assert "0.80" in reason and "P4 baseline" in reason and "abc12345" in reason


def test_passes_when_drift_within_threshold(tmp_path):
    _baseline(tmp_path)
    with patch("harness.ssi.scripts.crg_analysis.structural_drift",
               return_value=_measured(0.1)):
        assert harness_bridge._architecture_regression_reason(
            str(tmp_path), 4, {"crg": {"drift_threshold": 0.4}}, {}
        ) is None


def test_uses_default_threshold_when_config_missing_crg(tmp_path):
    _baseline(tmp_path)
    with patch("harness.ssi.scripts.crg_analysis.structural_drift",
               return_value=_measured(0.5)):
        # default 0.4 → 0.5 regresses
        assert harness_bridge._architecture_regression_reason(
            str(tmp_path), 4, {}, {}
        ) is not None
