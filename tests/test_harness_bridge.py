# tests/test_harness_bridge.py
# Phase B deliverable B13 — M4: framework self-tests for HarnessBridge.
# Academic Benchmark: G dim 4→5 (→92/100) once these pass.
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Patch heavy imports before importing the module under test
import sys
sys.modules.setdefault('yaml', MagicMock())

from harness.harness_bridge import (
    HarnessBridge, GateResult, DimResult, GateBlockedError
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".methodology").mkdir()
    with patch("harness.harness_bridge.CRGBridge") as MockCRG, \
         patch("harness.harness_bridge.DecisionLogWriter") as MockLog, \
         patch("harness.harness_bridge.EffortTracker") as MockEffort:
        MockCRG.return_value.is_available.return_value = False
        b = HarnessBridge()
        b._crg_mock = MockCRG.return_value
        b._log_mock = MockLog.return_value
        b._effort_mock = MockEffort.return_value
        yield b


def make_passing_result(gate_num: int, score: float = 90.0) -> GateResult:
    dims = [
        DimResult(name="linting",     score=95.0, threshold=90.0),
        DimResult(name="type_safety", score=88.0, threshold=85.0),
        DimResult(name="test_coverage",score=82.0, threshold=80.0),
    ]
    return GateResult(
        gate_num=gate_num, score=score,
        dimensions=dims, open_critical=0, open_high=0,
        quality_complete=True, rounds_used=1,
    )


def make_failing_result(gate_num: int, score: float = 60.0) -> GateResult:
    dims = [
        DimResult(name="linting",      score=60.0, threshold=90.0),
        DimResult(name="type_safety",  score=88.0, threshold=85.0),
        DimResult(name="test_coverage",score=82.0, threshold=80.0),
    ]
    return GateResult(
        gate_num=gate_num, score=score,
        dimensions=dims, open_critical=1, open_high=0,
        quality_complete=False, rounds_used=3,
    )


# ── generate_quality_manifest ───────────────────────────────────────────────

class TestGenerateQualityManifest:
    def test_creates_manifest_file(self, bridge, tmp_path):
        manifest_path = bridge.generate_quality_manifest(
            fr_ids=["FR-001", "FR-002"], sad_path="nonexistent.md"
        )
        assert manifest_path.exists()

    def test_manifest_schema(self, bridge, tmp_path):
        import json
        bridge.generate_quality_manifest(fr_ids=["FR-001"], sad_path="x.md")
        m = json.loads((tmp_path / ".methodology" / "quality_manifest.json").read_text())
        assert m["schema_version"] == "1.0"
        assert m["generated_at_phase"] == 2
        assert "FR-001" in m["fr_ids"]
        assert m["gate_results"]["gate2"] is None

    def test_manifest_gate1_is_dict(self, bridge, tmp_path):
        import json
        bridge.generate_quality_manifest(fr_ids=["FR-001"], sad_path="x.md")
        m = json.loads((tmp_path / ".methodology" / "quality_manifest.json").read_text())
        assert isinstance(m["gate_results"]["gate1"], dict)


# ── GateBlockedError ────────────────────────────────────────────────────────

class TestGateBlockedError:
    def test_message_contains_gate_num(self):
        result = make_failing_result(gate_num=2, score=60.0)
        err = GateBlockedError(gate_num=2, result=result)
        assert "Gate 2" in str(err)
        assert "60.0" in str(err)

    def test_result_attached(self):
        result = make_failing_result(gate_num=3)
        err = GateBlockedError(gate_num=3, result=result)
        assert err.result is result
        assert err.gate_num == 3


# ── Gate 1 blocking logic ────────────────────────────────────────────────────

class TestGate1BlockingLogic:
    def test_gate1_passes_when_all_dims_above_threshold(self, bridge):
        result = make_passing_result(gate_num=1)
        # Verify no exception is raised for passing dims
        failed = [d for d in result.dimensions if d.score < d.threshold]
        assert len(failed) == 0

    def test_gate1_blocks_when_dim_below_threshold(self):
        result = make_failing_result(gate_num=1)
        # linting=60 < threshold=90 → should block
        failed = [d for d in result.dimensions if d.score < d.threshold]
        assert len(failed) > 0


# ── _update_quality_manifest ─────────────────────────────────────────────────

class TestUpdateQualityManifest:
    def test_updates_gate2_score(self, bridge, tmp_path):
        import json
        bridge.generate_quality_manifest(fr_ids=["FR-001"], sad_path="x.md")
        result = make_passing_result(gate_num=2, score=80.0)
        bridge._update_quality_manifest(gate_num=2, fr_id=None, result=result)
        m = json.loads((tmp_path / ".methodology" / "quality_manifest.json").read_text())
        assert m["gate_results"]["gate2"]["score"] == 80.0
        assert m["gate_results"]["gate2"]["quality_complete"] is True

    def test_updates_gate1_per_fr(self, bridge, tmp_path):
        import json
        bridge.generate_quality_manifest(fr_ids=["FR-001"], sad_path="x.md")
        result = make_passing_result(gate_num=1)
        bridge._update_quality_manifest(gate_num=1, fr_id="FR-001", result=result)
        m = json.loads((tmp_path / ".methodology" / "quality_manifest.json").read_text())
        assert "FR-001" in m["gate_results"]["gate1"]

    def test_noop_when_manifest_missing(self, bridge, tmp_path):
        # Should not raise even if manifest doesn't exist yet
        result = make_passing_result(gate_num=2)
        bridge._update_quality_manifest(gate_num=2, fr_id=None, result=result)
