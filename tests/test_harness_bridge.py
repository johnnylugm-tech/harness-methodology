"""
Unit tests for HarnessBridge.
"""

import json
import pytest

pytestmark = pytest.mark.core
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

    def test_load_config_returns_gate_config(self):
        from core.quality_gate.constitution.profile import GateConfig
        bridge = HarnessBridge()
        config = bridge._load_config(gate_num=2)
        assert isinstance(config, GateConfig)
        assert config.gate_num == 2
        assert config.score_gate == 75.0
        assert len(config.dimensions) == 9

    def test_gate_blocked_error_attributes(self):
        result = GateResult(gate_num=3, score=60.0, open_critical=2, open_high=3)
        err = GateBlockedError(gate_num=3, result=result)
        assert err.gate_num == 3
        assert err.result.score == 60.0
        assert "Gate 3 BLOCKED" in str(err)


class TestHarnessBridgeIntegration:
    """Integration tests for HarnessBridge artifact generation and Hermes integration."""

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

    @staticmethod
    def _mock_config(gate_num=2, **kw):
        from core.quality_gate.constitution.profile import GateConfig, DimensionConfig
        return GateConfig(
            gate_num=gate_num,
            score_gate=float(kw.get("score_gate", 80.0)),
            dimensions=kw.get("dimensions", [DimensionConfig(name="coverage", threshold=75.0)]),
            crg=kw.get("crg", {}),
        )

    def test_prepare_gate_returns_gate_context(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(2)):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert isinstance(ctx, GateContext)
        assert ctx.gate_num == 2
        assert ctx.project_root == str(tmp_path)
        assert ctx.phase == 3

    def test_prepare_gate_sets_ssi_dirs(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(2)):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert "ssi" in ctx.ssi_scripts_dir
        assert "ssi" in ctx.ssi_prompts_dir

    def test_prepare_gate_triggers_crg_reconnaissance(self, tmp_path):
        bridge = HarnessBridge()
        config_with_crg = self._mock_config(3, crg={"reconnaissance": True})
        with patch.object(bridge, "_load_config", return_value=config_with_crg):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=3, project_root=str(tmp_path), phase=4)
        mock_recon.assert_called_once_with(str(tmp_path))

    def test_prepare_gate_skips_crg_when_not_configured(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(2)):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        mock_recon.assert_not_called()

    def test_prepare_gate_passes_fr_id(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(1)):
            ctx = bridge.prepare_gate(gate_num=1, project_root=str(tmp_path), phase=2, fr_id="FR-01")
        assert ctx.fr_id == "FR-01"

    def test_prepare_gate_creates_work_dir(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(2)):
            ctx = bridge.prepare_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        assert Path(ctx.work_dir).exists()


class TestFinalizeGate:
    """Tests for HarnessBridge.finalize_gate()."""

    def _make_context(self, tmp_path, gate_num=2, config=None, fr_id=None):
        if config is None:
            from core.quality_gate.constitution.profile import GateConfig, DimensionConfig
            config = GateConfig(
                gate_num=gate_num, score_gate=80.0, max_rounds=3,
                dimensions=[DimensionConfig(name="coverage", threshold=75.0),
                            DimensionConfig(name="linting", threshold=75.0)],
            )
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


class TestSabManifestIntegration:
    """Tests for SAB data loading and injection into gate evaluation context."""

    def test_load_manifest_sab_empty(self, tmp_path):
        """Returns empty dict when quality_manifest.json doesn't exist."""
        bridge = HarnessBridge()
        # No .methodology dir at all
        sab = bridge._load_manifest_sab(str(tmp_path))
        assert sab == {}

    def test_load_manifest_sab_valid(self, tmp_path):
        """Returns SAB-derived fields from a valid manifest."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        manifest = {
            "fr_ids": ["FR-01"],
            "nfr_dimension_mapping": {"NFR-1": "security"},
            "architecture_constraints": ["no-circular-deps"],
            "high_risk_modules": ["core/auth.py"],
            "gate_score_overrides": {"coverage": 80.0},
        }
        (method_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        bridge = HarnessBridge()
        sab = bridge._load_manifest_sab(str(tmp_path))
        assert sab["nfr_dimension_mapping"] == {"NFR-1": "security"}
        assert sab["architecture_constraints"] == ["no-circular-deps"]
        assert sab["high_risk_modules"] == ["core/auth.py"]
        # New fields introduced by SAB closure
        assert sab["gate_score_overrides"] == {"coverage": 80.0}
        assert sab["nfr_traceability"] == {}
        assert sab["quality_targets"] == {}
        assert sab["fr_module_traceability"] == {}

    def test_load_manifest_sab_partial(self, tmp_path):
        """Manifest without SAB fields returns empty lists/dicts."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "quality_manifest.json").write_text(
            '{"fr_ids": ["FR-01"], "gate_results": {}}'
        )
        bridge = HarnessBridge()
        sab = bridge._load_manifest_sab(str(tmp_path))
        assert sab["nfr_dimension_mapping"] == {}
        assert sab["architecture_constraints"] == []
        assert sab["high_risk_modules"] == []

    def test_evaluation_prompt_includes_sab_data(self):
        """GateContext.evaluation_prompt() renders SAB baseline when present."""
        ctx = GateContext(
            gate_num=3, config={"dimensions": [{"name": "architecture"}],
                                "score_gate": 80},
            project_root="/tmp/test", phase=4, fr_id=None,
            ssi_scripts_dir="/tmp/ssi/scripts",
            ssi_prompts_dir="/tmp/ssi/prompts",
            ssi_schemas_dir="/tmp/ssi/schemas",
            work_dir="/tmp/.sessi-work",
            sab_data={
                "architecture_constraints": ["no-circular-deps"],
                "high_risk_modules": ["core/auth.py"],
                "nfr_dimension_mapping": {"NFR-1": "security"},
            },
        )
        prompt = ctx.evaluation_prompt()
        assert "SAB Baseline" in prompt
        assert "no-circular-deps" in prompt
        assert "core/auth.py" in prompt
        assert "architecture" in prompt

    def test_evaluation_prompt_without_sab_data(self):
        """GateContext.evaluation_prompt() renders cleanly without SAB data."""
        ctx = GateContext(
            gate_num=1, config={"dimensions": [{"name": "linting"}]}, project_root="/t",
            phase=3, fr_id="FR-01",
            ssi_scripts_dir="/t/ssi/scripts", ssi_prompts_dir="/t/ssi/prompts",
            ssi_schemas_dir="/t/ssi/schemas", work_dir="/t/.sessi-work",
            sab_data={},
        )
        prompt = ctx.evaluation_prompt()
        assert "SAB Baseline" not in prompt

    def test_crg_tier3_guidance_wired_in_prepare_gate(self, tmp_path):
        """prepare_gate() retrieves tier3_context when tier3_guidance is enabled."""
        bridge = HarnessBridge()
        bridge.crg.get_minimal_context = MagicMock(return_value={
            "task": "architecture", "summary": "12 modules, 3 communities",
        })
        bridge.crg.run_reconnaissance = MagicMock()
        bridge.crg.load_metrics = MagicMock(return_value={})
        bridge.check_pre_fix_safety = MagicMock(return_value={
            "safe": True, "message": "ok", "threshold": 0.7,
        })

        # Create minimal gate config with tier3_guidance
        gate_config_dir = Path(__file__).parent.parent / "harness" / "gate_configs"
        yaml_path = tmp_path / "test_gate.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        yaml_path.write_text(yaml.dump({
            "gate": 3, "trigger": "phase_exit", "phase": 4, "scope": "full_phase",
            "dimensions": [
                {"name": "linting", "tier": 1, "model": "gemini-flash", "threshold": 90, "weight": 0.5},
                {"name": "architecture", "tier": 3, "model": "claude", "threshold": 80, "weight": 0.5},
            ],
            "blocking": True, "score_gate": 80, "max_rounds": 3,
            "crg": {"enabled": True, "tier3_guidance": True, "impact_threshold": 0.7},
            "replaces": "test",
        }))
        from core.quality_gate.constitution.profile import GateConfig
        raw_config = yaml.safe_load(yaml_path.read_text())
        with patch.object(bridge, '_load_config', return_value=GateConfig.from_dict(raw_config, 3)):
            ctx = bridge.prepare_gate(gate_num=3, project_root=str(tmp_path), phase=4)

        assert "architecture" in ctx.tier3_context
        assert bridge.crg.get_minimal_context.called
        # linting is tier 1, should NOT trigger get_minimal_context
        assert "linting" not in ctx.tier3_context

    def test_crg_tier3_context_in_evaluation_prompt(self):
        """evaluation_prompt() surfaces tier3_context and crg_safety_context when available."""
        ctx = GateContext(
            gate_num=3, config={
                "dimensions": [{"name": "architecture", "tier": 3, "model": "claude", "threshold": 80, "weight": 1.0}],
                "score_gate": 80, "max_rounds": 3,
            },
            project_root="/t", phase=4, fr_id=None,
            ssi_scripts_dir="/t/ssi", ssi_prompts_dir="/t/ssi",
            ssi_schemas_dir="/t/ssi", work_dir="/t/.sessi-work",
            tier3_context={"architecture": {"task": "architecture", "summary": "high coupling detected"}},
            crg_safety_context={
                "pre_fix_safety": {"safe": True, "message": "ok", "threshold": 0.7},
            },
        )
        prompt = ctx.evaluation_prompt()
        assert "CRG Tier 3 Guidance" in prompt
        assert "architecture" in prompt
        assert "CRG Safety Context" in prompt
        assert "pre_fix_safety" in prompt

    def test_check_pre_fix_safety_delegates_to_crg(self, tmp_path):
        """check_pre_fix_safety() calls CRG and returns structured result."""
        bridge = HarnessBridge()
        bridge.crg.check_impact = MagicMock(return_value=False)  # not risky

        result = bridge.check_pre_fix_safety(str(tmp_path))
        assert result["safe"] is True
        assert "threshold" in result
        bridge.crg.check_impact.assert_called_once()

    def test_check_pre_fix_safety_defers_when_risky(self, tmp_path):
        """check_pre_fix_safety() reports unsafe when CRG detects risk."""
        bridge = HarnessBridge()
        bridge.crg.check_impact = MagicMock(return_value=True)  # risky

        result = bridge.check_pre_fix_safety(str(tmp_path))
        assert result["safe"] is False
        assert "DEFER" in result["message"]

    def test_check_post_round_drift_handles_missing_metrics(self, tmp_path):
        """check_post_round_drift() handles missing crg_metrics.json gracefully."""
        bridge = HarnessBridge()
        bridge.crg.check_drift = MagicMock(return_value=False)
        bridge.crg.load_metrics = MagicMock(return_value={})

        result = bridge.check_post_round_drift(str(tmp_path))
        assert result["drifted"] is False
        assert result["structural_drift"] == 0.0


class TestSabClosureGaps:
    """Tests for the 5 SAB closure gaps: new manifest fields and threshold enforcement."""

    # ── Gap 1+2: _load_manifest_sab returns all new fields ────────────────

    def test_load_manifest_sab_returns_nfr_traceability(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        manifest = {
            "nfr_traceability": {"NFR-01": {"type": "performance", "target": "p95<3s", "module": "app.pipeline"}},
            "nfr_fr_mapping": {"NFR-01": ["FR-19"]},
        }
        (method_dir / "quality_manifest.json").write_text(json.dumps(manifest))
        sab = HarnessBridge()._load_manifest_sab(str(tmp_path))
        assert sab["nfr_traceability"] == manifest["nfr_traceability"]
        assert sab["nfr_fr_mapping"] == {"NFR-01": ["FR-19"]}

    def test_load_manifest_sab_returns_quality_targets(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        manifest = {"quality_targets": {"min_coverage": 80, "max_complexity": 10}}
        (method_dir / "quality_manifest.json").write_text(json.dumps(manifest))
        sab = HarnessBridge()._load_manifest_sab(str(tmp_path))
        assert sab["quality_targets"] == {"min_coverage": 80, "max_complexity": 10}

    def test_load_manifest_sab_returns_fr_module_traceability(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        manifest = {"fr_module_traceability": {"FR-01": "app.models", "FR-14": "app.infrastructure.health"}}
        (method_dir / "quality_manifest.json").write_text(json.dumps(manifest))
        sab = HarnessBridge()._load_manifest_sab(str(tmp_path))
        assert sab["fr_module_traceability"]["FR-01"] == "app.models"

    def test_load_manifest_sab_returns_gate_score_overrides(self, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        manifest = {"gate_score_overrides": {"coverage": 80.0}}
        (method_dir / "quality_manifest.json").write_text(json.dumps(manifest))
        sab = HarnessBridge()._load_manifest_sab(str(tmp_path))
        assert sab["gate_score_overrides"] == {"coverage": 80.0}

    def test_load_manifest_sab_defaults_new_fields_to_empty(self, tmp_path):
        """Manifest without new fields returns empty dicts (backwards compat)."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "quality_manifest.json").write_text('{"fr_ids": ["FR-01"]}')
        sab = HarnessBridge()._load_manifest_sab(str(tmp_path))
        assert sab["nfr_traceability"] == {}
        assert sab["nfr_fr_mapping"] == {}
        assert sab["quality_targets"] == {}
        assert sab["fr_module_traceability"] == {}
        assert sab["gate_score_overrides"] == {}

    # ── Gap 2: generate_quality_manifest auto-populates gate_score_overrides ──

    def test_generate_quality_manifest_populates_gate_score_overrides(self, tmp_path):
        """min_coverage in quality_targets → gate_score_overrides.coverage populated."""
        bridge = HarnessBridge()
        sab_return = {
            "quality_targets": {"min_coverage": 85},
            "constraints": [],
            "high_risk": [],
            "nfr_dim_map": {},
            "nfr_traceability": {},
            "fr_module_traceability": {},
        }
        _path_redirect = lambda *a: tmp_path / Path(*a) if ".methodology" in str(a) else Path(*a)
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    with patch("harness.harness_bridge.Path", side_effect=_path_redirect):
                        p = bridge.generate_quality_manifest(["FR-01"], "SAD.md")
        data = json.loads(p.read_text())
        assert data["gate_score_overrides"] == {"test_coverage": 85.0}
        assert data["quality_targets"] == {"min_coverage": 85}

    def test_generate_quality_manifest_no_override_when_no_min_coverage(self, tmp_path):
        """quality_targets without min_coverage → gate_score_overrides stays empty."""
        bridge = HarnessBridge()
        sab_return = {
            "quality_targets": {"max_complexity": 10},
            "constraints": [], "high_risk": [], "nfr_dim_map": {},
            "nfr_traceability": {}, "fr_module_traceability": {},
        }
        _path_redirect = lambda *a: tmp_path / Path(*a) if ".methodology" in str(a) else Path(*a)
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    with patch("harness.harness_bridge.Path", side_effect=_path_redirect):
                        p = bridge.generate_quality_manifest(["FR-01"], "SAD.md")
        data = json.loads(p.read_text())
        assert data["gate_score_overrides"] == {}

    # ── Gap 4: evaluation_prompt injects fr_module_traceability ────────────

    def test_evaluation_prompt_injects_fr_module_for_gate1(self):
        ctx = GateContext(
            gate_num=1, config={"dimensions": [{"name": "coverage"}], "score_gate": 80},
            project_root="/t", phase=3, fr_id="FR-14",
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir="/t/.sessi-work",
            sab_data={"fr_module_traceability": {"FR-14": "app.infrastructure.health"}},
        )
        prompt = ctx.evaluation_prompt()
        assert "FR-14 responsible module: app.infrastructure.health" in prompt

    def test_evaluation_prompt_skips_fr_module_when_no_fr_id(self):
        """Gate 2–4 (fr_id=None) should not inject fr_module_traceability."""
        ctx = GateContext(
            gate_num=2, config={"dimensions": [], "score_gate": 80},
            project_root="/t", phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir="/t/.sessi-work",
            sab_data={"fr_module_traceability": {"FR-01": "app.models"}},
        )
        prompt = ctx.evaluation_prompt()
        assert "responsible module" not in prompt

    # ── Gap 2: evaluation_prompt quality_targets YAML format ───────────────

    def test_evaluation_prompt_quality_targets_yaml_format(self):
        """quality_targets rendered as indented key: value, not Python dict repr."""
        ctx = GateContext(
            gate_num=2, config={"dimensions": [], "score_gate": 80},
            project_root="/t", phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir="/t/.sessi-work",
            sab_data={"quality_targets": {"min_coverage": 80, "p95_latency_ms": 3000}},
        )
        prompt = ctx.evaluation_prompt()
        assert "    min_coverage: 80" in prompt
        assert "    p95_latency_ms: 3000" in prompt
        # Must NOT use Python dict repr
        assert "{'min_coverage'" not in prompt

    # ── Gap B: evaluation_prompt injects nfr_fr_mapping ───────────────────────

    def test_evaluation_prompt_injects_nfr_fr_mapping(self):
        """nfr_fr_mapping from sab_data is injected into the prompt."""
        ctx = GateContext(
            gate_num=2, config={"dimensions": [], "score_gate": 80},
            project_root="/t", phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir="/t/.sessi-work",
            sab_data={"nfr_fr_mapping": {"NFR-02": ["FR-04", "FR-05"], "NFR-03": ["FR-08"]}},
        )
        prompt = ctx.evaluation_prompt()
        assert "nfr_fr_mapping" in prompt
        assert "NFR-02" in prompt
        assert "FR-04" in prompt

    def test_evaluation_prompt_skips_nfr_fr_mapping_when_empty(self):
        """Empty nfr_fr_mapping does not add noise to the prompt."""
        ctx = GateContext(
            gate_num=2, config={"dimensions": [], "score_gate": 80},
            project_root="/t", phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir="/t/.sessi-work",
            sab_data={"nfr_fr_mapping": {}},
        )
        prompt = ctx.evaluation_prompt()
        assert "nfr_fr_mapping" not in prompt

    # ── Gap 5: finalize_gate applies gate_score_overrides as threshold floor ──

    def _write_gate1_result(self, ctx: GateContext, breakdown: dict) -> None:
        result_path = Path(ctx.work_dir) / f"gate{ctx.gate_num}_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({
            "overall_score": 90.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": breakdown,
        }))

    def test_finalize_gate_raises_when_override_exceeds_agent_threshold(self, tmp_path):
        """test_coverage override=80 with agent threshold=60 and score=70 → blocked."""
        bridge = HarnessBridge()
        ctx = GateContext(
            gate_num=1, config={"gate": 1, "dimensions": []},
            project_root=str(tmp_path), phase=3, fr_id="FR-01",
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={"gate_score_overrides": {"test_coverage": 80.0}},
        )
        self._write_gate1_result(ctx, {
            "test_coverage": {"score": 70.0, "threshold": 60.0, "issues": []},
        })
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            with pytest.raises(GateBlockedError):
                                bridge.finalize_gate(ctx)

    def test_finalize_gate_passes_when_score_meets_override(self, tmp_path):
        """test_coverage override=80, score=85 → passes even if agent threshold was 60."""
        bridge = HarnessBridge()
        ctx = GateContext(
            gate_num=1, config={"gate": 1, "dimensions": []},
            project_root=str(tmp_path), phase=3, fr_id="FR-01",
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={"gate_score_overrides": {"test_coverage": 80.0}},
        )
        self._write_gate1_result(ctx, {
            "test_coverage": {"score": 85.0, "threshold": 60.0, "issues": []},
        })
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            result = bridge.finalize_gate(ctx)
        assert result.quality_complete is True

    def test_finalize_gate_override_is_floor_not_ceiling(self, tmp_path):
        """Override=80, agent threshold=90 → effective threshold stays 90 (override never lowers)."""
        bridge = HarnessBridge()
        ctx = GateContext(
            gate_num=1, config={"gate": 1, "dimensions": []},
            project_root=str(tmp_path), phase=3, fr_id="FR-01",
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={"gate_score_overrides": {"test_coverage": 80.0}},
        )
        # score=85 passes override=80 but fails agent threshold=90
        self._write_gate1_result(ctx, {
            "test_coverage": {"score": 85.0, "threshold": 90.0, "issues": []},
        })
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            with pytest.raises(GateBlockedError):
                                bridge.finalize_gate(ctx)

    def test_finalize_gate2_ignores_dimension_overrides(self, tmp_path):
        """Gate 2–4 passes/fails on composite score_gate, not per-dimension overrides."""
        bridge = HarnessBridge()
        # Override says coverage must be ≥80, but Gate 2 checks score_gate only.
        ctx = GateContext(
            gate_num=2,
            config={"gate": 2, "dimensions": [], "score_gate": 75, "max_rounds": 2},
            project_root=str(tmp_path), phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={"gate_score_overrides": {"coverage": 80.0}},
        )
        # overall_score=80 >= score_gate=75, so gate should pass
        # coverage dim score=70 < override=80, but Gate 2 doesn't use per-dim thresholds
        result_path = Path(ctx.work_dir) / "gate2_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({
            "overall_score": 80.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"coverage": {"score": 70.0, "threshold": 60.0, "issues": []}},
        }))
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            result = bridge.finalize_gate(ctx)
        assert result.quality_complete is True
        assert result.score == 80.0
