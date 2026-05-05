"""
Unit tests for HarnessBridge.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from harness.harness_bridge import HarnessBridge, GateResult, GateBlockedError, GateContext


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

    def test_load_config_returns_dict(self):
        bridge = HarnessBridge()
        config = bridge._load_config(gate_num=2)
        assert isinstance(config, dict)
        assert config["gate"] == 2

    def test_run_gate_passes_with_good_score(self):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 80, "max_rounds": 3}), \
             patch.object(bridge, "_invoke_harness") as mock_invoke, \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"):
            mock_invoke.return_value = GateResult(
                gate_num=2, score=90.0, quality_complete=True,
                dimensions=[], open_critical=0, open_high=0,
            )
            result = bridge.run_gate(gate_num=2, project_root=".", phase=3)
            assert result.score == 90.0
            assert result.quality_complete is True

    def test_gate_blocked_error_attributes(self):
        result = GateResult(gate_num=3, score=60.0, open_critical=2, open_high=3)
        err = GateBlockedError(gate_num=3, result=result)
        assert err.gate_num == 3
        assert err.result.score == 60.0
        assert "Gate 3 BLOCKED" in str(err)

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

    def test_generate_quality_manifest_handles_import_error(self, tmp_path):
        bridge = HarnessBridge()
        with patch("harness.harness_bridge.Path", side_effect=lambda *args: tmp_path / Path(*args) if ".methodology" in str(args) else Path(*args)):
            with patch("scripts.generate_sab.parse_sad", side_effect=ImportError):
                out_path = bridge.generate_quality_manifest(fr_ids=["FR-01"], sad_path="SAD.md")
                assert out_path.exists()

    def test_run_gate_1_dimension_threshold_fails(self):
        bridge = HarnessBridge()
        from harness.harness_bridge import DimResult
        with patch.object(bridge, "_load_config", return_value={"gate": 1}), \
             patch.object(bridge, "_invoke_harness") as mock_invoke, \
             patch.object(bridge, "_update_quality_manifest"):
            mock_invoke.return_value = GateResult(
                gate_num=1, score=70.0, quality_complete=True,
                dimensions=[DimResult(name="cov", score=50.0, threshold=80.0, issues=[])],
            )
            with pytest.raises(GateBlockedError):
                bridge.run_gate(gate_num=1, project_root=".", phase=3)

    def test_require_hermes_approve_init_failure(self):
        bridge = HarnessBridge()
        result = GateResult(gate_num=4, score=90.0, quality_complete=True)
        with patch("harness.reviewer_router.ReviewerRouter", side_effect=ValueError):
            bridge._require_hermes_approve(result, phase=6, fr_id=None)


class TestGateContext:
    """Tests for GateContext dataclass and evaluation_prompt()."""

    def _make_context(self, **kwargs):
        defaults = dict(
            gate_num=2,
            config={"gate": 2, "score_gate": 80, "dimensions": [{"name": "coverage"}, {"name": "lint"}]},
            project_root="/some/project",
            phase=3,
            fr_id=None,
            ssi_scripts_dir="/harness/ssi/scripts",
            ssi_prompts_dir="/harness/ssi/prompts",
            ssi_schemas_dir="/harness/ssi/schemas",
            work_dir="/some/project/.sessi-work",
        )
        defaults.update(kwargs)
        return GateContext(**defaults)

    def test_gate_context_attributes(self):
        ctx = self._make_context()
        assert ctx.gate_num == 2
        assert ctx.phase == 3
        assert ctx.fr_id is None

    def test_evaluation_prompt_contains_gate_num(self):
        ctx = self._make_context()
        prompt = ctx.evaluation_prompt()
        assert "Gate 2" in prompt

    def test_evaluation_prompt_contains_dimensions(self):
        ctx = self._make_context()
        prompt = ctx.evaluation_prompt()
        assert "coverage" in prompt
        assert "lint" in prompt

    def test_evaluation_prompt_contains_work_dir(self):
        ctx = self._make_context()
        prompt = ctx.evaluation_prompt()
        assert ".sessi-work" in prompt

    def test_evaluation_prompt_contains_score_gate(self):
        ctx = self._make_context()
        prompt = ctx.evaluation_prompt()
        assert "80" in prompt

    def test_evaluation_prompt_with_fr_id(self):
        ctx = self._make_context(fr_id="FR-03")
        prompt = ctx.evaluation_prompt()
        assert "FR-03" in prompt


class TestPrepareGate:
    """Tests for HarnessBridge.prepare_gate()."""

    def test_prepare_gate_returns_gate_context(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 80}):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert isinstance(ctx, GateContext)
        assert ctx.gate_num == 2
        assert ctx.project_root == str(tmp_path)
        assert ctx.phase == 3

    def test_prepare_gate_sets_ssi_dirs(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2}):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert "ssi" in ctx.ssi_scripts_dir
        assert "ssi" in ctx.ssi_prompts_dir

    def test_prepare_gate_triggers_crg_reconnaissance(self, tmp_path):
        bridge = HarnessBridge()
        config_with_crg = {"gate": 3, "crg": {"reconnaissance": True}}
        with patch.object(bridge, "_load_config", return_value=config_with_crg):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=3, project_root=str(tmp_path), phase=4)
        mock_recon.assert_called_once_with(str(tmp_path))

    def test_prepare_gate_skips_crg_when_not_configured(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2}):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        mock_recon.assert_not_called()

    def test_prepare_gate_passes_fr_id(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 1}):
            ctx = bridge.prepare_gate(gate_num=1, project_root=str(tmp_path), phase=2, fr_id="FR-01")
        assert ctx.fr_id == "FR-01"

    def test_prepare_gate_creates_work_dir(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2}):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert Path(ctx.work_dir).exists()


class TestFinalizeGate:
    """Tests for HarnessBridge.finalize_gate()."""

    def _make_context(self, tmp_path, gate_num=2, config=None, fr_id=None):
        if config is None:
            config = {"gate": gate_num, "score_gate": 80, "max_rounds": 3}
        ssi_dir = Path(__file__).parent.parent / "harness" / "ssi"
        work_dir = tmp_path / ".sessi-work"
        work_dir.mkdir()
        return GateContext(
            gate_num=gate_num, config=config,
            project_root=str(tmp_path), phase=3, fr_id=fr_id,
            ssi_scripts_dir=str(ssi_dir / "scripts"),
            ssi_prompts_dir=str(ssi_dir / "prompts"),
            ssi_schemas_dir=str(ssi_dir / "schemas"),
            work_dir=str(work_dir),
        )

    def _write_result(self, ctx, data):
        result_path = Path(ctx.work_dir) / f"gate{ctx.gate_num}_result.json"
        result_path.write_text(json.dumps(data), encoding="utf-8")

    def test_finalize_gate_returns_gate_result_on_pass(self, tmp_path):
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        self._write_result(ctx, {
            "overall_score": 85.0, "meets_target": True, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0, "breakdown": {},
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    result = bridge.finalize_gate(ctx)
        assert isinstance(result, GateResult)
        assert result.score == 85.0
        assert result.quality_complete is True

    def test_finalize_gate_raises_when_result_file_missing(self, tmp_path):
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        # No result file written
        with pytest.raises(FileNotFoundError, match="gate2_result.json"):
            bridge.finalize_gate(ctx)

    def test_finalize_gate_raises_blocked_on_low_score(self, tmp_path):
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        self._write_result(ctx, {
            "overall_score": 70.0, "meets_target": False, "quality_complete": False,
            "open_critical_count": 0, "open_high_count": 0, "breakdown": {},
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    with pytest.raises(GateBlockedError, match="Gate 2 BLOCKED"):
                        bridge.finalize_gate(ctx)

    def test_finalize_gate_raises_blocked_on_open_critical(self, tmp_path):
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        self._write_result(ctx, {
            "overall_score": 90.0, "meets_target": True, "quality_complete": False,
            "open_critical_count": 2, "open_high_count": 0, "breakdown": {},
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    with pytest.raises(GateBlockedError):
                        bridge.finalize_gate(ctx)

    def test_finalize_gate_updates_manifest(self, tmp_path):
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        self._write_result(ctx, {
            "overall_score": 85.0, "meets_target": True, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0, "breakdown": {},
        })
        with patch.object(bridge, "_update_quality_manifest") as mock_update:
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    bridge.finalize_gate(ctx)
        mock_update.assert_called_once()
        call_args = mock_update.call_args[1] if mock_update.call_args[1] else mock_update.call_args[0]

    def test_finalize_gate_gate1_dimension_threshold(self, tmp_path):
        """Gate 1 uses per-dimension thresholds, not composite score_gate."""
        bridge = HarnessBridge()
        config = {"gate": 1, "dimensions": [{"name": "cov", "threshold": 80}]}
        ctx = self._make_context(tmp_path, gate_num=1, config=config)
        self._write_result(ctx, {
            "overall_score": 70.0, "meets_target": False, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"cov": {"score": 50.0, "threshold": 80.0, "passed": False}},
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    with pytest.raises(GateBlockedError):
                        bridge.finalize_gate(ctx)

    def test_finalize_gate_gate4_calls_hermes(self, tmp_path):
        """Gate 4 must call _require_hermes_approve when passing."""
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=4,
                                  config={"gate": 4, "score_gate": 85})
        self._write_result(ctx, {
            "overall_score": 90.0, "meets_target": True, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0, "breakdown": {},
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    with patch.object(bridge, "_require_hermes_approve") as mock_hermes:
                        bridge.finalize_gate(ctx)
        mock_hermes.assert_called_once()
