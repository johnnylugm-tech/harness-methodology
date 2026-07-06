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


class TestCommunityMinSizeExemption:
    """Tiny communities (size < COMMUNITY_MIN_SIZE=5) must not be penalised for
    low cohesion — they have too few nodes to produce meaningful structural edges."""

    def _score(self, communities):
        from harness.ssi.scripts.crg_analysis import compute_community_cohesion_score
        return compute_community_cohesion_score(communities)

    def test_tiny_community_low_cohesion_not_penalised(self):
        # size=3 < min(5), cohesion below threshold → exempted → counts as healthy
        result = self._score([{"name": "micro", "cohesion": 0.1, "size": 3}])
        assert result["healthy"] == 1
        assert result["score"] == 100.0

    def test_border_community_low_cohesion_penalised(self):
        # size=5 == min(5) → threshold applies → low cohesion → unhealthy
        result = self._score([{"name": "border", "cohesion": 0.1, "size": 5}])
        assert result["healthy"] == 0
        assert len(result["unhealthy"]) == 1
        assert "low_cohesion" in result["unhealthy"][0]["issues"][0]

    def test_mix_tiny_and_normal(self):
        # 1 tiny (exempt) + 1 large low-cohesion → score 50%
        result = self._score([
            {"name": "micro", "cohesion": 0.1, "size": 2},
            {"name": "big", "cohesion": 0.1, "size": 100},
        ])
        assert result["healthy"] == 1
        assert result["score"] == 50.0

    def test_min_size_reported_in_result(self):
        result = self._score([{"name": "c", "cohesion": 0.9, "size": 10}])
        assert "_community_min_size" in result
        assert result["_community_min_size"] == 5


class TestPerProjectCrgCalibration:
    """crg_cohesion_healthy / crg_excludes from harness_config.json calibrate
    the framework-owned architecture score (small packages over-fragmented by
    Leiden; project tooling communities are not product code)."""

    def _score(self, communities, **kw):
        from harness.ssi.scripts.crg_analysis import compute_community_cohesion_score
        return compute_community_cohesion_score(communities, **kw)

    def test_cohesion_healthy_param_overrides_default(self):
        # 0.28 < default 0.3 → unhealthy; with param 0.25 → healthy
        comm = [{"name": "small-pkg", "cohesion": 0.28, "size": 8}]
        assert self._score(comm)["healthy"] == 0
        result = self._score(comm, cohesion_healthy=0.25)
        assert result["healthy"] == 1
        assert result["_cohesion_threshold"] == 0.25

    def test_default_threshold_reported_when_no_param(self):
        result = self._score([{"name": "c", "cohesion": 0.9, "size": 10}])
        assert result["_cohesion_threshold"] == 0.3
        assert result["_extra_excludes"] == []

    def test_extra_excludes_majority_match_excludes_community(self):
        comm = [
            {"name": "workflows", "cohesion": 0.1, "size": 10,
             "files": ["/repo/.claude/workflows/a.js", "/repo/.claude/workflows/b.js",
                       "/repo/src/x.py"]},
            {"name": "core", "cohesion": 0.9, "size": 10,
             "files": ["/repo/src/a.py", "/repo/src/b.py"]},
        ]
        result = self._score(comm, extra_excludes=[".claude/*"], project_root="/repo")
        # workflows: 2/3 files match → excluded from scoring entirely
        assert result["total"] == 1
        assert result["healthy"] == 1
        assert result["score"] == 100.0
        assert result["_extra_excludes"] == [".claude/*"]

    def test_extra_excludes_half_match_keeps_community(self):
        # exactly 50% is NOT a majority → community stays scored
        comm = [{"name": "mixed", "cohesion": 0.1, "size": 10,
                 "files": ["/repo/.claude/workflows/a.js", "/repo/src/x.py"]}]
        result = self._score(comm, extra_excludes=[".claude/*"], project_root="/repo")
        assert result["total"] == 1
        assert result["healthy"] == 0

    def test_absolute_paths_relativized_against_project_root(self):
        # root-level glob "*.mjs": fnmatch's * crosses "/", but the pattern
        # still only matches after correct relativization
        comm = [{"name": "verify", "cohesion": 0.1, "size": 6,
                 "files": ["/repo/harness-e2e.mjs", "/repo/phase1-workflow.mjs"]}]
        result = self._score(comm, extra_excludes=["*.mjs"], project_root="/repo")
        assert result["total"] == 0
        assert result["score"] == 100

    def test_no_project_root_falls_back_to_lstrip(self):
        comm = [{"name": "verify", "cohesion": 0.1, "size": 6,
                 "files": ["/a.mjs", "/b.mjs"]}]
        result = self._score(comm, extra_excludes=["*.mjs"])
        assert result["total"] == 0

    def test_run_independent_crg_threads_settings(self, tmp_path):
        """End-to-end: harness_config.json values reach the cohesion formula.

        Communities carry no `files` key here: pytest's tmp_path contains
        `/test_`, which would trip the legacy path-based exclusion and mask
        the assertion (glob matching itself is unit-tested above with a
        clean /repo root).
        """
        communities = {"communities": [
            {"name": "core", "cohesion": 0.28, "size": 8},
        ]}

        def fake_run(cmd, **kw):
            if any("crg_dump" in str(c) for c in cmd):
                return _proc(stdout=json.dumps(communities))
            return _proc()

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(json.dumps({
            "crg_cohesion_healthy": 0.25,
            "crg_excludes": [".claude/*"],
        }))

        with patch("harness.crg_independent.shutil.which", return_value="/bin/code-review-graph"), \
             patch("harness.crg_independent._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_independent.subprocess.run", side_effect=fake_run):
            metrics = run_independent_crg(str(tmp_path), str(tmp_path / ".sessi-work"))

        # core healthy at configured threshold 0.25 (unhealthy at default 0.3)
        cohesion = metrics["community_cohesion"]
        assert cohesion["total"] == 1
        assert cohesion["score"] == 100.0
        assert cohesion["_cohesion_threshold"] == 0.25
        assert cohesion["_extra_excludes"] == [".claude/*"]
        assert metrics["architecture_score"] == 100.0
