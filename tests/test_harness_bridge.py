"""
Unit tests for HarnessBridge.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from harness.harness_bridge import HarnessBridge, DimResult, GateResult, GateBlockedError


class TestHarnessBridge:
    """Tests for the HarnessBridge class."""

    def test_update_quality_manifest_basic(self, tmp_path):
        """Verify quality manifest update with gate results."""
        # Create dummy manifest
        methodology_dir = tmp_path / ".methodology"
        methodology_dir.mkdir()
        manifest_path = methodology_dir / "quality_manifest.json"
        manifest_path.write_text('{"gate_results": {"gate2": null}}')

        bridge = HarnessBridge()
        # Mock path in the bridge
        with patch("harness.harness_bridge.Path", side_effect=lambda *args: tmp_path / Path(*args) if ".methodology" in str(args) else Path(*args)):
            result = GateResult(gate_num=2, score=85.0, quality_complete=True)
            bridge._update_quality_manifest(gate_num=2, fr_id=None, result=result)
            
            import json
            updated = json.loads(manifest_path.read_text())
            assert updated["gate_results"]["gate2"]["score"] == 85.0
            assert updated["gate_results"]["gate2"]["quality_complete"] is True

    def test_run_gate_raises_blocked_error(self):
        """Verify GateBlockedError when score is below threshold."""
        bridge = HarnessBridge()
        # Mock config and harness invocation
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 80}):
            with patch.object(bridge, "_invoke_harness") as mock_invoke:
                # Score 70 < threshold 80
                mock_invoke.return_value = GateResult(gate_num=2, score=70.0, quality_complete=True)
                with patch.object(bridge, "_update_quality_manifest"):
                    with pytest.raises(GateBlockedError, match="Gate 2 BLOCKED"):
                        bridge.run_gate(gate_num=2, project_root=".", phase=3)

    def test_require_hermes_approve_blocks_on_reject(self):
        """Verify Gate 4 blocks if Hermes returns REJECT."""
        bridge = HarnessBridge()
        result = GateResult(gate_num=4, score=90.0, quality_complete=True)
        
        mock_router = MagicMock()
        mock_router.review.return_value = {"review_status": "REJECT", "summary": "Code smell"}
        
        with patch("harness.reviewer_router.ReviewerRouter", return_value=mock_router):
            with patch.object(bridge, "_log") as mock_log:
                with pytest.raises(GateBlockedError, match="Gate 4 BLOCKED"):
                    bridge._require_hermes_approve(result, phase=6, fr_id=None)
                mock_log.write.assert_called_once()
                entry = mock_log.write.call_args[0][0]
                assert entry.decision == "REVIEWER_REJECT"

    def test_generate_quality_manifest_creates_file(self, tmp_path):
        """Verify generation of initial quality manifest."""
        bridge = HarnessBridge()
        with patch("harness.harness_bridge.Path", side_effect=lambda *args: tmp_path / Path(*args) if ".methodology" in str(args) else Path(*args)):
            with patch("scripts.generate_sab.parse_sad", return_value={"constraints": ["C1"]}):
                out_path = bridge.generate_quality_manifest(fr_ids=["FR-01"], sad_path="SAD.md")
                assert out_path.exists()
                import json
                data = json.loads(out_path.read_text())
                assert data["fr_ids"] == ["FR-01"]
                assert data["architecture_constraints"] == ["C1"]
