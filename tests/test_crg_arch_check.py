"""Tests for `harness_cli.py crg-arch-check` (Stage 3).

The non-interactive CI architecture gate. run_independent_crg and the drift
function are mocked — no real code-review-graph is invoked.
"""

import argparse
import json
from unittest.mock import patch

import harness_cli
from harness.crg_independent import CrgIndependentError


def _args(**kw):
    base = {"project": ".", "threshold": 80.0, "baseline": None, "drift_threshold": 0.4}
    base.update(kw)
    return argparse.Namespace(**base)


class TestCrgArchCheck:
    def test_pass_above_threshold(self, tmp_path):
        with patch("harness.crg_independent.run_independent_crg",
                   return_value={"architecture_score": 95.0}):
            assert harness_cli.cmd_crg_arch_check(_args(project=str(tmp_path))) == 0

    def test_fail_below_threshold(self, tmp_path):
        with patch("harness.crg_independent.run_independent_crg",
                   return_value={"architecture_score": 50.0}):
            assert harness_cli.cmd_crg_arch_check(_args(project=str(tmp_path))) == 1

    def test_falls_back_to_cohesion_score(self, tmp_path):
        # No architecture_score key → use community_cohesion.score.
        with patch("harness.crg_independent.run_independent_crg",
                   return_value={"community_cohesion": {"score": 90.0}}):
            assert harness_cli.cmd_crg_arch_check(_args(project=str(tmp_path))) == 0

    def test_crg_unavailable_blocks(self, tmp_path):
        with patch("harness.crg_independent.run_independent_crg",
                   side_effect=CrgIndependentError("not on PATH")):
            assert harness_cli.cmd_crg_arch_check(_args(project=str(tmp_path))) == 1

    def test_drift_regression_hard_fails(self, tmp_path):
        baseline = tmp_path / "crg_baseline_p4.json"
        baseline.write_text(json.dumps({"architecture_score": 100}), encoding="utf-8")
        with patch("harness.crg_independent.run_independent_crg",
                   return_value={"architecture_score": 90.0}), \
             patch("harness.ssi.scripts.crg_analysis.compute_structural_drift",
                   return_value=0.8):
            rc = harness_cli.cmd_crg_arch_check(
                _args(project=str(tmp_path), baseline=str(baseline))
            )
        assert rc == 1  # drift 0.8 >= 0.4 → hard block even though score 90 >= 80

    def test_drift_within_threshold_passes(self, tmp_path):
        baseline = tmp_path / "crg_baseline_p4.json"
        baseline.write_text(json.dumps({"architecture_score": 100}), encoding="utf-8")
        with patch("harness.crg_independent.run_independent_crg",
                   return_value={"architecture_score": 100.0}), \
             patch("harness.ssi.scripts.crg_analysis.compute_structural_drift",
                   return_value=0.1):
            rc = harness_cli.cmd_crg_arch_check(
                _args(project=str(tmp_path), baseline=str(baseline))
            )
        assert rc == 0
