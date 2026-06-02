"""Tests for harness/crg_independent.py — framework-owned CRG metrics.

CRG is a hard dependency: a missing binary or a failed run raises
CrgIndependentError (no graceful degradation to agent scores). The CRG subprocess
calls are mocked so these tests never require code-review-graph to be installed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from harness.crg_independent import (
    run_independent_crg,
    crg_binary,
    CrgIndependentError,
)


def _proc(stdout: str = "", rc: int = 0, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestCrgBinary:
    def test_missing_binary_raises(self):
        with patch("harness.crg_independent.shutil.which", return_value=None):
            with pytest.raises(CrgIndependentError, match="not found"):
                crg_binary()

    def test_present_binary_returned(self):
        with patch("harness.crg_independent.shutil.which", return_value="/opt/bin/code-review-graph"):
            assert crg_binary() == "/opt/bin/code-review-graph"


class TestRunIndependentCrg:
    def test_writes_framework_owned_metrics(self, tmp_path):
        communities = {"communities": [
            {"name": "core", "cohesion": 0.9, "size": 10},    # healthy
            {"name": "big", "cohesion": 0.1, "size": 200},    # unhealthy
        ]}

        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout=json.dumps(communities))
            return _proc()  # build / postprocess

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            metrics = run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

        assert metrics["_source"] == "framework-independent"
        # 1 healthy / 2 communities → 50.0; no large_fn_penalty → architecture_score = 50.0
        assert metrics["community_cohesion"]["score"] == 50.0
        assert metrics["large_functions_penalty"] == 0
        assert metrics["architecture_score"] == 50.0
        written = json.loads((tmp_path / ".sessi-work" / "crg_metrics.json").read_text())
        assert written["community_cohesion"]["score"] == 50.0
        assert written["architecture_score"] == 50.0

    def test_large_function_penalty_applied(self, tmp_path):
        """Phase 1 gatekeeper: 2 critical functions (≥500 lines) → -10 penalty."""
        dump_data = {
            "communities": [
                {"name": "core", "cohesion": 0.8, "size": 5},
            ],
            "large_functions_critical": [
                {"name": "fn_a", "line_count": 550, "file_path": "a.py"},
                {"name": "fn_b", "line_count": 600, "file_path": "b.py"},
            ],
        }

        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout=json.dumps(dump_data))
            return _proc()

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            metrics = run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

        # 1 community (healthy) → cohesion 100.0; 2 critical fns → -10
        assert metrics["community_cohesion"]["score"] == 100.0
        assert metrics["large_functions_penalty"] == 10
        assert metrics["architecture_score"] == 90.0
        assert len(metrics["large_functions_critical"]) == 2

    def test_large_function_penalty_capped_at_20(self, tmp_path):
        """Penalty is capped at 20 regardless of how many critical functions exist."""
        dump_data = {
            "communities": [{"name": "c", "cohesion": 0.8, "size": 5}],
            "large_functions_critical": [
                {"name": f"fn_{i}", "line_count": 600, "file_path": f"f{i}.py"}
                for i in range(10)  # 10 × 5 = 50, capped at 20
            ],
        }

        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout=json.dumps(dump_data))
            return _proc()

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            metrics = run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

        assert metrics["large_functions_penalty"] == 20   # capped
        assert metrics["architecture_score"] == 80.0       # 100 - 20

    def test_backward_compat_no_large_fn_field(self, tmp_path):
        """Old dump without large_functions_critical → no penalty, architecture_score = cohesion."""
        dump_data = {"communities": [{"name": "c", "cohesion": 0.6, "size": 5}]}

        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout=json.dumps(dump_data))
            return _proc()

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            metrics = run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

        assert metrics["large_functions_penalty"] == 0
        assert metrics["architecture_score"] == metrics["community_cohesion"]["score"]

    def test_missing_binary_raises(self, tmp_path):
        with patch("harness.crg_independent.shutil.which", return_value=None):
            with pytest.raises(CrgIndependentError):
                run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

    def test_build_failure_blocks(self, tmp_path):
        """A non-zero build is a hard error — never a fallback to agent scores."""
        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent.subprocess.run", return_value=_proc(rc=1, stderr="boom")):
            with pytest.raises(CrgIndependentError, match="build failed"):
                run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

    def test_invalid_dump_json_blocks(self, tmp_path):
        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout="not json at all")
            return _proc()

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            with pytest.raises(CrgIndependentError, match="invalid JSON"):
                run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))
