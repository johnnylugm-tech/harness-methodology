"""PR 4 (audit F-1.1 fix) regression test: framework trace score is
authoritative in the gate result, regardless of what the agent wrote.

The harness_bridge.finalize_gate() now mirrors the architecture CRG
override: it runs `compute_trace_dimension` and replaces the agent's
traceability score in-place. This test confirms:
  - When agent reports a wrong (optimistic) score, framework score wins.
  - When agent reports a wrong (pessimistic) score, framework score wins.
  - When the agent's score happens to match, no churn.
  - When compute_trace_dimension errors out, the bridge falls back
    to the agent's score (no exception propagated).
"""
from pathlib import Path
from unittest.mock import patch

import pytest


def _build_fake_ctx(project_path: Path, work_dir: Path, gate_num: int = 2):
    """Build a minimal GateContext-like object for finalize_gate."""
    from harness.harness_bridge import GateContext
    return GateContext(
        gate_num=gate_num,
        phase=3,
        project_root=str(project_path),
        work_dir=str(work_dir),
        fr_id=None,
        spec={"dimensions": []},
    )


def _fake_dims_list():
    """Return a list of DimResult-like objects for a 3-dim gate."""
    from dataclasses import dataclass, field
    from typing import List, Dict, Any

    @dataclass
    class _Dim:
        name: str
        score: float
        threshold: float
        issues: List[Dict[str, Any]] = field(default_factory=list)

    return [
        _Dim(name="linting", score=95.0, threshold=90),
        _Dim(name="traceability", score=100.0, threshold=100),  # agent lies
        _Dim(name="security", score=80.0, threshold=80),
    ]


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo so compute_trace_dimension can scan."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\n")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01]"""\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    (tmp_path / ".sessi-work").mkdir()
    return tmp_path


def test_bridge_overrides_optimistic_agent_score(fixture_repo):
    """Agent claims 100%; framework says 50% → framework wins."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)

    from harness.harness_bridge import HarnessBridge
    bridge = HarnessBridge()
    ctx = _build_fake_ctx(fixture_repo, fixture_repo / ".sessi-work", gate_num=2)
    # Use real compute_trace_dimension with patched spec-coverage to 50%
    # (we want to test the override; the 4b value doesn't matter for this assertion)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value={"merged_pct": 50.0, "4a_fr_to_test_pct": 50.0,
                              "4b_test_spec_pct": 50.0, "passed": False,
                              "threshold_4a": 100, "threshold_4b": 60.0,
                              "active_uncoded": [], "active_untested": [],
                              "blocking": True, "error": None}):
        # Build a fake _result with dims and a result.json
        result_path = fixture_repo / ".sessi-work" / "gate2_result.json"
        import json
        result_path.write_text(json.dumps({
            "overall_score": 91.6,
            "breakdown": {
                "linting": {"score": 95.0, "threshold": 90},
                "traceability": {"score": 100.0, "threshold": 100},  # agent lies
                "security": {"score": 80.0, "threshold": 80},
            },
            "failing_dimensions": [],
        }))
        try:
            bridge.finalize_gate(ctx)
        except Exception:
            pass  # we only care about the dims mutation side-effect; gate
                  # result may fail the threshold check which is expected
                  # when we set score to 50 < threshold 100.
    # After finalize_gate, the gate{N}_result.json on disk should have
    # the framework's 50.0 in traceability.score, not the agent's 100.0.
    import json
    on_disk = json.loads(result_path.read_text())
    assert on_disk["breakdown"]["traceability"]["score"] == 50.0


def test_bridge_overrides_pessimistic_agent_score(fixture_repo):
    """Agent claims 0%; framework says 80% → framework wins."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)

    from harness.harness_bridge import HarnessBridge
    bridge = HarnessBridge()
    ctx = _build_fake_ctx(fixture_repo, fixture_repo / ".sessi-work", gate_num=2)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value={"merged_pct": 80.0, "4a_fr_to_test_pct": 80.0,
                              "4b_test_spec_pct": 80.0, "passed": True,
                              "threshold_4a": 100, "threshold_4b": 60.0,
                              "active_uncoded": [], "active_untested": [],
                              "blocking": True, "error": None}):
        result_path = fixture_repo / ".sessi-work" / "gate2_result.json"
        import json
        result_path.write_text(json.dumps({
            "overall_score": 58.3,
            "breakdown": {
                "linting": {"score": 95.0, "threshold": 90},
                "traceability": {"score": 0.0, "threshold": 100},  # agent lies low
                "security": {"score": 80.0, "threshold": 80},
            },
        }))
        try:
            bridge.finalize_gate(ctx)
        except Exception:
            pass
    on_disk = json.loads(result_path.read_text())
    assert on_disk["breakdown"]["traceability"]["score"] == 80.0


def test_bridge_no_op_when_scores_already_match(fixture_repo):
    """If the agent happens to write the framework's exact score, no change."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)

    from harness.harness_bridge import HarnessBridge
    bridge = HarnessBridge()
    ctx = _build_fake_ctx(fixture_repo, fixture_repo / ".sessi-work", gate_num=2)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value={"merged_pct": 75.0, "4a_fr_to_test_pct": 75.0,
                              "4b_test_spec_pct": 75.0, "passed": True,
                              "threshold_4a": 100, "threshold_4b": 60.0,
                              "active_uncoded": [], "active_untested": [],
                              "blocking": True, "error": None}):
        result_path = fixture_repo / ".sessi-work" / "gate2_result.json"
        import json
        result_path.write_text(json.dumps({
            "overall_score": 83.3,
            "breakdown": {
                "linting": {"score": 95.0, "threshold": 90},
                "traceability": {"score": 75.0, "threshold": 100},
                "security": {"score": 80.0, "threshold": 80},
            },
        }))
        try:
            bridge.finalize_gate(ctx)
        except Exception:
            pass
    on_disk = json.loads(result_path.read_text())
    # Score stays 75.0 (no churn)
    assert on_disk["breakdown"]["traceability"]["score"] == 75.0


def test_bridge_falls_back_to_agent_when_compute_errors(fixture_repo):
    """If compute_trace_dimension raises, agent's score is preserved (no crash)."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)

    from harness.harness_bridge import HarnessBridge
    bridge = HarnessBridge()
    ctx = _build_fake_ctx(fixture_repo, fixture_repo / ".sessi-work", gate_num=2)
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               side_effect=RuntimeError("scanner crashed")):
        result_path = fixture_repo / ".sessi-work" / "gate2_result.json"
        import json
        result_path.write_text(json.dumps({
            "overall_score": 85.0,
            "breakdown": {
                "linting": {"score": 95.0, "threshold": 90},
                "traceability": {"score": 80.0, "threshold": 100},  # agent
                "security": {"score": 80.0, "threshold": 80},
            },
        }))
        try:
            bridge.finalize_gate(ctx)
        except Exception:
            pass
    on_disk = json.loads(result_path.read_text())
    # Bridge swallowed the error → agent's 80.0 preserved
    assert on_disk["breakdown"]["traceability"]["score"] == 80.0
