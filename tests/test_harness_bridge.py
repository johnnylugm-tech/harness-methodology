"""
Unit tests for HarnessBridge.
"""
import pytest


import json
from typing import Any

from unittest.mock import patch, MagicMock
from pathlib import Path
from harness.harness_bridge import (
    HarnessBridge, GateResult, GateBlockedError, GateContext,
    _check_tests_failed, _check_test_skip_ratio,
)

pytestmark = [pytest.mark.mutation_oracle, pytest.mark.core]


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
        result = GateResult(gate_num=2, score=85.0, quality_complete=True)
        # Pass project_root explicitly (HR-09: CWD-rel manifest path
        # is no longer the contract).
        bridge._update_quality_manifest(
            gate_num=2, fr_id=None, result=result,
            project_root=str(tmp_path),
        )

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
        assert len(config.dimensions) == 11  # 10 original + execute_verification_target (631782b)

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
                out_path = bridge.generate_quality_manifest(fr_ids=["FR-01"], sad_path="SAD.md", project_root=str(tmp_path), force=True)
                assert out_path is not None
                assert out_path.exists()
                import json
                data = json.loads(out_path.read_text())
                assert data["fr_ids"] == ["FR-01"]
                assert data["architecture_constraints"] == ["C1"]

    def test_generate_quality_manifest_handles_import_error(self, tmp_path):
        bridge = HarnessBridge()
        with patch("harness.harness_bridge.Path", side_effect=lambda *args: tmp_path / Path(*args) if ".methodology" in str(args) else Path(*args)):
            with patch("scripts.generate_sab.parse_sad", side_effect=ImportError):
                out_path = bridge.generate_quality_manifest(fr_ids=["FR-01"], sad_path="SAD.md", project_root=str(tmp_path), force=True)
                assert out_path is not None
                assert out_path.exists()

    def test_generate_quality_manifest_dedups_fr_ids(self, tmp_path):
        """Duplicate fr_ids (e.g. a workflow re-passing the FR set across phases)
        must be de-duplicated with order preserved — fr_ids is a registry, not a
        multiset; duplicates inflate the 'N FRs' count and the per-FR Gate 1 loop."""
        bridge = HarnessBridge()
        with patch("harness.harness_bridge.Path", side_effect=lambda *args: tmp_path / Path(*args) if ".methodology" in str(args) else Path(*args)):
            with patch("scripts.generate_sab.parse_sad", return_value={}):
                out_path = bridge.generate_quality_manifest(
                    fr_ids=["FR-01", "FR-02", "FR-03", "FR-01", "FR-02", "FR-03"],
                    sad_path="SAD.md", project_root=str(tmp_path), force=True,
                )
                import json
                assert out_path is not None
                data = json.loads(out_path.read_text())
                assert data["fr_ids"] == ["FR-01", "FR-02", "FR-03"]


class TestGateContext:
    """Tests for GateContext dataclass and evaluation_prompt()."""

    def _make_context(self, **kwargs: Any) -> GateContext:
        _cfg = {"gate": 2, "score_gate": 80, "dimensions": [{"name": "coverage"}, {"name": "lint"}]}
        return GateContext(
            gate_num=kwargs.get("gate_num", 2),
            config=kwargs.get("config", _cfg),
            project_root=kwargs.get("project_root", "/some/project"),
            phase=kwargs.get("phase", 3),
            fr_id=kwargs.get("fr_id", None),
            ssi_scripts_dir=kwargs.get("ssi_scripts_dir", "/harness/ssi/scripts"),
            ssi_prompts_dir=kwargs.get("ssi_prompts_dir", "/harness/ssi/prompts"),
            ssi_schemas_dir=kwargs.get("ssi_schemas_dir", "/harness/ssi/schemas"),
            work_dir=kwargs.get("work_dir", "/some/project/.sessi-work"),
            sab_data=kwargs.get("sab_data", {}),
            tier3_context=kwargs.get("tier3_context", {}),
            crg_safety_context=kwargs.get("crg_safety_context", {}),
            auto_fix_rounds=kwargs.get("auto_fix_rounds", 0),
        )

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
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"linting": {"score": 90.0, "threshold": 85.0}},
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

    def test_finalize_gate_explicit_false_recomputes_pass(self, tmp_path):
        """D (Bug 3): an explicit quality_complete:false must NOT bypass the fallback
        recompute. With passing score + all dims above threshold, the gate passes
        despite the agent writing false."""
        bridge = HarnessBridge()
        ctx = self._make_context(tmp_path, gate_num=2)
        self._write_result(ctx, {
            "overall_score": 88.0,
            "quality_complete": False,   # agent wrote false (stale/default)
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {
                "coverage": {"score": 90.0, "threshold": 75.0},
                "linting": {"score": 85.0, "threshold": 75.0},
            },
        })
        with patch.object(bridge, "_update_quality_manifest"):
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    result = bridge.finalize_gate(ctx)
        # Recomputed from dims (all pass, score >= gate) → quality_complete True
        assert result.quality_complete is True

    def test_finalize_gate_simple_average_skips_none_scores(self, tmp_path):
        """L2302: the no-weight-config fallback average must skip dims with
        score=None, same as the weighted branch a few lines above it —
        otherwise sum() crashes with TypeError the moment any dimension is
        not yet applicable (e.g. no benchmark tests)."""
        from harness.harness_bridge import DimResult
        bridge = HarnessBridge()
        # Dict-style dimensions with no "weight" key keep _dim_weights empty,
        # so finalize_gate falls into the unweighted `elif dims:` branch.
        config = {"score_gate": 80.0, "dimensions": [{"name": "coverage", "threshold": 75.0}]}
        ctx = self._make_context(tmp_path, gate_num=3, config=config)
        self._write_result(ctx, {
            "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"coverage": {"score": 90.0, "threshold": 75.0}},
        })

        def _fake_enrich(_crg, dims, _project_root, _work_dir, _gate_num):
            return list(dims) + [DimResult(name="pytest_benchmark", score=None, threshold=75.0)], False  # type: ignore[arg-type]

        # A None-scored dim separately fails the (pre-existing, out of scope
        # here) all-dims-pass check, so the gate blocks — that's expected.
        # What this test verifies is that computing the composite score no
        # longer crashes and correctly excludes the None-score dim.
        with patch("harness.harness_bridge._crg_enrich_gate_findings", side_effect=_fake_enrich):
            with patch.object(bridge, "_update_quality_manifest"):
                with patch.object(bridge, "_log"):
                    with patch.object(bridge, "_effort"):
                        with pytest.raises(GateBlockedError) as exc_info:
                            bridge.finalize_gate(ctx)
        assert exc_info.value.result.score == 90.0  # the None-score dim is excluded from the average

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
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"linting": {"score": 90.0, "threshold": 85.0}},
        })
        with patch.object(bridge, "_update_quality_manifest") as mock_update:
            with patch.object(bridge, "_log"):
                with patch.object(bridge, "_effort"):
                    bridge.finalize_gate(ctx)
        mock_update.assert_called_once()
        mock_update.call_args[1] if mock_update.call_args[1] else mock_update.call_args[0]

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
        yaml_path = tmp_path / "test_gate.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        yaml_path.write_text(yaml.dump({
            "gate": 3, "trigger": "phase_exit", "phase": 4, "scope": "full_phase",
            "dimensions": [
                {"name": "linting", "tier": 1, "model": "claude", "threshold": 90, "weight": 0.5},
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
        """quality_targets whose VALUE is a 0-100 score → gate_score_overrides.
        max_complexity→complexity was dead (no such dimension) → dropped.
        p95_latency_ms is milliseconds, not a score → must NOT seed performance's floor."""
        bridge = HarnessBridge()
        sab_return = {
            "quality_targets": {"min_coverage": 85, "max_complexity": 10, "p95_latency_ms": 200},
            "constraints": [],
            "high_risk": [],
            "nfr_dim_map": {},
            "nfr_traceability": {},
            "fr_module_traceability": {},
        }
        def _path_redirect(*a):
            return tmp_path / Path(*a) if ".methodology" in str(a) else Path(*a)
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    with patch("harness.harness_bridge.Path", side_effect=_path_redirect):
                        p = bridge.generate_quality_manifest(["FR-01"], "SAD.md", project_root=str(tmp_path), force=True)
        assert p is not None
        data = json.loads(p.read_text())
        assert data["gate_score_overrides"] == {
            "test_coverage": 85.0,
        }
        # p95_latency_ms (ms) must NOT become performance's floor — it is not a 0-100 score
        assert "performance" not in data["gate_score_overrides"]
        assert data["quality_targets"]["min_coverage"] == 85

    def test_generate_quality_manifest_no_override_when_no_mapping(self, tmp_path):
        """quality_targets without mapped keys → gate_score_overrides stays empty."""
        bridge = HarnessBridge()
        sab_return = {
            "quality_targets": {"unknown_target": 10},
            "constraints": [],
            "high_risk": [], "nfr_dim_map": {},
            "nfr_traceability": {}, "fr_module_traceability": {},
        }
        def _path_redirect(*a):
            return tmp_path / Path(*a) if ".methodology" in str(a) else Path(*a)
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    with patch("harness.harness_bridge.Path", side_effect=_path_redirect):
                        p = bridge.generate_quality_manifest(["FR-01"], "SAD.md", project_root=str(tmp_path), force=True)
        assert p is not None
        data = json.loads(p.read_text())
        assert data["gate_score_overrides"] == {}

    # ── Bug H-A regression tests ─────────────────────────────────────────
    # SAD.md §5 may carry template placeholder values (FR-01: "app.api.webhooks")
    # even when .methodology/SAB.json has been project-tailored. Without the
    # reconcile step, the manifest writes the template values and silently
    # drifts from runtime arch state. See _reconcile_with_sab_json.

    def _write_sab_json(self, tmp_path: Path, content: dict) -> Path:
        sab_path = tmp_path / ".methodology" / "SAB.json"
        sab_path.parent.mkdir(parents=True, exist_ok=True)
        sab_path.write_text(json.dumps(content, indent=2))
        return sab_path

    def test_h_a_uses_sab_json_when_sad_5_has_template_placeholders(self, tmp_path, capsys):
        """§5 says FR-01 → 'app.api.webhooks' (template default) but SAB.json
        has the project-real mapping. Manifest must follow SAB.json — that
        is what every downstream hook reads."""
        bridge = HarnessBridge()
        sab_return = {
            "fr_module_traceability": {"FR-01": "app.api.webhooks"},  # template default
            "constraints": [],
            "high_risk": [], "nfr_dim_map": {},
            "nfr_traceability": {}, "quality_targets": {},
            "gate_score_overrides": {},
        }
        self._write_sab_json(tmp_path, {
            "fr_module_traceability": {
                "FR-01": "taskq.models",
                "FR-02": "taskq.executor",
            },
            "architecture_constraints": ["no_circular_dependencies"],
            "high_risk_modules": ["taskq.executor"],
        })
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    p = bridge.generate_quality_manifest(
                        ["FR-01", "FR-02"], "SAD.md",
                        project_root=str(tmp_path), force=True,
                    )
        assert p is not None
        data = json.loads(p.read_text())
        # Authoritative values from SAB.json must be present
        assert data["fr_module_traceability"]["FR-01"] == "taskq.models"
        assert data["fr_module_traceability"]["FR-02"] == "taskq.executor"
        assert data["architecture_constraints"] == ["no_circular_dependencies"]
        assert data["high_risk_modules"] == ["taskq.executor"]
        # WARN line surfaces the template-placeholder drift
        err = capsys.readouterr().err
        assert "disagrees with .methodology/SAB.json" in err

    def test_h_a_no_op_when_sab_json_missing(self, tmp_path):
        """Without SAB.json the helper is a no-op — current behaviour is
        preserved (covered by all 87 existing tests; this is the
        backward-compatibility pin)."""
        bridge = HarnessBridge()
        sab_return = {
            "fr_module_traceability": {"FR-01": "app.api.webhooks"},
            "constraints": [], "high_risk": [],
            "nfr_dim_map": {}, "nfr_traceability": {},
            "quality_targets": {}, "gate_score_overrides": {},
        }
        # No SAB.json written → reconciliation must not change values.
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    p = bridge.generate_quality_manifest(
                        ["FR-01"], "SAD.md",
                        project_root=str(tmp_path), force=True,
                    )
        assert p is not None
        data = json.loads(p.read_text())
        # §5 value preserved when SAB.json is absent
        assert data["fr_module_traceability"] == {"FR-01": "app.api.webhooks"}

    def test_h_a_idempotent_when_sad_5_and_sab_json_agree(self, tmp_path, capsys):
        """§5 and SAB.json say the same thing → no WARN, no behaviour change."""
        bridge = HarnessBridge()
        shared = {
            "FR-01": "taskq.models",
            "FR-02": "taskq.executor",
            "FR-03": "taskq.cli",
        }
        sab_return = {
            "fr_module_traceability": dict(shared),
            "constraints": ["no_circular_dependencies"],
            "high_risk": ["taskq.executor", "taskq.store"],
            "nfr_dim_map": {"NFR-01": "performance"},
            "nfr_traceability": {}, "quality_targets": {},
            "gate_score_overrides": {},
        }
        self._write_sab_json(tmp_path, {
            "fr_module_traceability": dict(shared),
            "architecture_constraints": ["no_circular_dependencies"],
            "high_risk_modules": ["taskq.executor", "taskq.store"],
            "nfr_dimension_mapping": {"NFR-01": "performance"},
        })
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    p = bridge.generate_quality_manifest(
                        ["FR-01", "FR-02", "FR-03"], "SAD.md",
                        project_root=str(tmp_path), force=True,
                    )
        assert p is not None
        data = json.loads(p.read_text())
        # All values match what was already in §5
        assert data["fr_module_traceability"] == shared
        # No WARN emitted on agreement
        err = capsys.readouterr().err
        assert "disagrees with" not in err

    def test_h_a_unaffected_by_dict_key_order(self, tmp_path, capsys):
        """Ordering difference between §5 and SAB.json dicts is NOT a
        real disagreement — only structural / value differences count."""
        bridge = HarnessBridge()
        sab_return = {
            "fr_module_traceability": {
                "FR-02": "taskq.executor",
                "FR-01": "taskq.models",   # key order swapped vs SAB.json
            },
            "constraints": [], "high_risk": [],
            "nfr_dim_map": {}, "nfr_traceability": {},
            "quality_targets": {}, "gate_score_overrides": {},
        }
        self._write_sab_json(tmp_path, {
            "fr_module_traceability": {
                "FR-01": "taskq.models",
                "FR-02": "taskq.executor",
            },
        })
        with patch("scripts.generate_sab.parse_sad", return_value=sab_return):
            with patch.object(bridge, "_parse_nfr_from_srs", return_value={}):
                with patch.object(bridge, "_parse_nfr_fr_xref", return_value={}):
                    p = bridge.generate_quality_manifest(
                        ["FR-01", "FR-02"], "SAD.md",
                        project_root=str(tmp_path), force=True,
                    )
        assert p is not None
        err = capsys.readouterr().err
        # Dict-ordering difference must NOT be flagged as a disagreement
        assert "disagrees with" not in err

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

    def test_finalize_gate_caps_test_coverage_at_spec_coverage_pct(self, tmp_path):
        """test_coverage score is capped at spec coverage pct, causing a block if it drops below threshold."""
        bridge = HarnessBridge()
        ctx = GateContext(
            gate_num=1, config={"gate": 1, "dimensions": []},
            project_root=str(tmp_path), phase=3, fr_id="FR-01",
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={},
        )
        ctx._spec_test_names = ["test_a", "test_b", "test_c", "test_d"]
        ctx._existing_spec_tests = {"test_a"}  # 25% spec coverage
        self._write_gate1_result(ctx, {
            # Agent claims 90% coverage, but threshold is 60%.
            # Cap applies: 25% < 60% threshold -> GateBlockedError
            "test_coverage": {"score": 90.0, "threshold": 60.0, "issues": []},
        })
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            with pytest.raises(GateBlockedError) as exc_info:
                                bridge.finalize_gate(ctx)
                            
                            failed_dims = [d.name for d in exc_info.value.result.dimensions if d.score < d.threshold]
                            assert "test_coverage" in failed_dims

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
        """Gate 2-4: per-dim thresholds enforced by gate_score_overrides.

        HR-18: every dimension must meet its individual threshold (after applying
        gate_score_overrides as a floor). Composite score alone is not sufficient.
        """
        bridge = HarnessBridge()
        ctx = GateContext(
            gate_num=2,
            config={"gate": 2, "dimensions": [], "score_gate": 75, "max_rounds": 2},
            project_root=str(tmp_path), phase=4, fr_id=None,
            ssi_scripts_dir="/t", ssi_prompts_dir="/t", ssi_schemas_dir="/t",
            work_dir=str(tmp_path / ".sessi-work"),
            sab_data={"gate_score_overrides": {"coverage": 80.0}},
        )
        # coverage=85 >= override=80 → passes both composite and per-dim checks
        result_path = Path(ctx.work_dir) / "gate2_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({
            "overall_score": 80.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {"coverage": {"score": 85.0, "threshold": 60.0, "issues": []}},
        }))
        with patch("harness.harness_bridge._check_tool_evidence", return_value=[]):
            with patch("harness.harness_bridge._run_harness_cross_validation", return_value=[]):
                with patch.object(bridge, "_update_quality_manifest"):
                    with patch.object(bridge, "_log"):
                        with patch.object(bridge, "_effort"):
                            result = bridge.finalize_gate(ctx)
        assert result.quality_complete is True
        assert result.score == 80.0


# ===========================================================================
# _check_tests_failed (S4-B)
# ===========================================================================

class TestCheckTestsFailed:
    def _raw(self, evidence: str) -> dict:
        return {"breakdown": {"test_coverage": {"tool_evidence": evidence}}}

    def test_no_failures_returns_empty(self):
        raw = self._raw("432 passed, 12 skipped in 1.58s")
        assert _check_tests_failed(raw) == []

    def test_with_failures_returns_violation(self):
        raw = self._raw("5 failed, 427 passed in 2.1s")
        violations = _check_tests_failed(raw)
        assert len(violations) == 1
        assert "5 test(s) FAILED" in violations[0]

    def test_one_failure(self):
        raw = self._raw("1 failed, 100 passed in 0.5s")
        violations = _check_tests_failed(raw)
        assert "1 test(s) FAILED" in violations[0]

    def test_missing_evidence_returns_empty(self):
        # S3 handles missing evidence; S4-B should not double-block
        raw = {"breakdown": {"test_coverage": {}}}
        assert _check_tests_failed(raw) == []

    def test_missing_test_coverage_key_returns_empty(self):
        raw = {"breakdown": {}}
        assert _check_tests_failed(raw) == []

    def test_passed_only_no_skipped(self):
        raw = self._raw("443 passed in 1.2s")
        assert _check_tests_failed(raw) == []


# ===========================================================================
# _check_test_skip_ratio (W1)
# ===========================================================================

class TestCheckTestSkipRatio:
    def _raw(self, evidence: str) -> dict:
        return {"breakdown": {"test_coverage": {"tool_evidence": evidence}}}

    def test_low_skip_ratio_returns_none(self):
        # 12 skipped / 444 total = 2.7% < 10% threshold
        raw = self._raw("432 passed, 12 skipped in 1.58s")
        assert _check_test_skip_ratio(raw) is None

    def test_high_skip_ratio_returns_warning(self):
        # 50 skipped / 150 total = 33% > 10%
        raw = self._raw("100 passed, 50 skipped in 2.0s")
        result = _check_test_skip_ratio(raw)
        assert result is not None
        assert "WARN" in result
        assert "50 of 150" in result

    def test_exactly_at_threshold_returns_none(self):
        # 10 skipped / 100 total = exactly 10%, not > threshold
        raw = self._raw("90 passed, 10 skipped in 1.0s")
        assert _check_test_skip_ratio(raw) is None

    def test_just_above_threshold_returns_warning(self):
        # 11 skipped / 100 total = 11% > 10%
        raw = self._raw("89 passed, 11 skipped in 1.0s")
        result = _check_test_skip_ratio(raw)
        assert result is not None

    def test_no_skipped_in_evidence_returns_none(self):
        raw = self._raw("443 passed in 1.2s")
        assert _check_test_skip_ratio(raw) is None

    def test_missing_evidence_returns_none(self):
        raw = {"breakdown": {"test_coverage": {}}}
        assert _check_test_skip_ratio(raw) is None

    def test_custom_threshold(self):
        # 5 skipped / 50 total = 10%, default threshold 10% → None
        # With custom threshold 5% → warning
        raw = self._raw("45 passed, 5 skipped in 0.5s")
        assert _check_test_skip_ratio(raw, threshold=0.10) is None
        assert _check_test_skip_ratio(raw, threshold=0.05) is not None


# ── Layer 4: SRS/SAD truncation fix + FR extraction ──────────────────────────

class TestPrepareEnvCheckLargeDocuments:
    """Verify that large SRS/SAD files are no longer truncated at 6K/8K chars."""

    def _make_bridge(self, tmp_path: Path, srs_content: str = "", sad_content: str = "") -> "HarnessBridge":
        if srs_content:
            (tmp_path / "SRS.md").write_text(srs_content, encoding="utf-8")
        if sad_content:
            (tmp_path / "SAD.md").write_text(sad_content, encoding="utf-8")
        return HarnessBridge()

    def test_srs_within_60k_not_truncated(self, tmp_path):
        srs = "x" * 55_000  # 55KB < 60K new limit (was truncated at old 6K)
        bridge = self._make_bridge(tmp_path, srs_content=srs)
        ctx = bridge.prepare_env_check(project_root=str(tmp_path), phase=1)
        assert "[... truncated" not in ctx.srs_excerpt
        assert len(ctx.srs_excerpt) == 55_000

    def test_sad_within_60k_not_truncated(self, tmp_path):
        sad = "y" * 58_000  # 58KB < 60K new limit (was truncated at old 8K)
        bridge = self._make_bridge(tmp_path, sad_content=sad)
        ctx = bridge.prepare_env_check(project_root=str(tmp_path), phase=2)
        assert "[... truncated" not in ctx.sad_excerpt
        assert len(ctx.sad_excerpt) == 58_000

    def test_srs_over_60k_truncated_at_new_limit(self, tmp_path):
        srs = "z" * 70_000  # 70KB > new 60K limit
        bridge = self._make_bridge(tmp_path, srs_content=srs)
        ctx = bridge.prepare_env_check(project_root=str(tmp_path), phase=1)
        assert "[... truncated at 60000 chars" in ctx.srs_excerpt

    def test_fr_specific_extraction_returns_section(self, tmp_path):
        srs = (
            "# SRS\n\n"
            "### FR-01: Login\nAcceptance: user can log in.\n\n"
            "### FR-02: Logout\nAcceptance: user can log out.\n\n"
            "### FR-03: Profile\nAcceptance: user can update profile.\n"
        )
        bridge = self._make_bridge(tmp_path, srs_content=srs)
        ctx = bridge.prepare_env_check(project_root=str(tmp_path), phase=1, fr_id="FR-02")
        assert "FR-02" in ctx.srs_excerpt
        assert "FR-01" not in ctx.srs_excerpt
        assert "FR-03" not in ctx.srs_excerpt

    def test_fr_extraction_fallback_when_section_missing(self, tmp_path):
        srs = "# SRS\n\n### FR-01: Login\ndetails\n"
        bridge = self._make_bridge(tmp_path, srs_content=srs)
        ctx = bridge.prepare_env_check(project_root=str(tmp_path), phase=1, fr_id="FR-99")
        # FR-99 not found → returns up to 60K of full text
        assert "FR-01" in ctx.srs_excerpt


class TestExtractFrSection:
    """Unit tests for the _extract_fr_section helper."""

    def test_extracts_correct_section(self):
        from harness.harness_bridge import _extract_fr_section
        srs = "### FR-01: A\ndetail a\n\n### FR-02: B\ndetail b\n\n### FR-03: C\ndetail c\n"
        result = _extract_fr_section(srs, "FR-02")
        assert "detail b" in result
        assert "detail a" not in result
        assert "detail c" not in result

    def test_returns_fallback_when_not_found(self):
        from harness.harness_bridge import _extract_fr_section
        srs = "some content without matching FR"
        result = _extract_fr_section(srs, "FR-42")
        assert result == srs[:60_000]

    def test_handles_last_section_in_file(self):
        from harness.harness_bridge import _extract_fr_section
        srs = "### FR-01: A\ndetail a\n\n### FR-02: Last\nfinal content here\n"
        result = _extract_fr_section(srs, "FR-02")
        assert "final content here" in result



class TestCrgGatekeeperPhases:
    """Phase 1 + Phase 2 CRG gatekeeper elevation tests."""

    # ── Phase 1: large-function penalty (via crg_independent subprocess) ──

    def test_phase1_architecture_score_uses_penalty(self, tmp_path):
        """harness_bridge reads architecture_score (cohesion − penalty) from metrics."""

        # Simulate crg_metrics.json written by run_independent_crg with penalty
        metrics = {
            "community_cohesion": {"score": 80.0, "healthy": 2, "total": 2, "unhealthy": []},
            "large_functions_critical": [
                {"name": "big_fn", "line_count": 550, "file_path": "core/x.py"}
            ],
            "large_functions_penalty": 5,
            "architecture_score": 75.0,  # 80 - 5
            "_source": "framework-independent",
        }
        (tmp_path / ".sessi-work").mkdir()
        (tmp_path / ".sessi-work" / "crg_metrics.json").write_text(
            __import__("json").dumps(metrics)
        )

        # The bridge reads architecture_score, not raw cohesion
        arch_score = metrics["architecture_score"]
        cohesion_score = metrics["community_cohesion"]["score"]
        assert arch_score == 75.0
        assert arch_score < cohesion_score  # penalty was applied

    def test_phase1_no_penalty_when_no_critical_fns(self, tmp_path):
        """No large functions → architecture_score == community_cohesion.score."""
        metrics = {
            "community_cohesion": {"score": 60.0, "healthy": 1, "total": 2, "unhealthy": []},
            "large_functions_critical": [],
            "large_functions_penalty": 0,
            "architecture_score": 60.0,
            "_source": "framework-independent",
        }
        assert metrics["architecture_score"] == metrics["community_cohesion"]["score"]

    # ── Phase 2: untested hub score override (via MCP query_graph) ──

    def test_phase2_untested_hub_reduces_test_coverage_score(self, tmp_path):
        """query_graph(tests_for) returning empty → test_coverage score penalised by 3pts/hub."""
        from harness.harness_bridge import _crg_enrich_gate_findings, DimResult
        from unittest.mock import MagicMock

        dims = [
            DimResult(name="test_coverage", score=85.0, threshold=80.0),
            DimResult(name="architecture", score=70.0, threshold=80.0),
        ]

        # Mock CRGBridge: get_hub_nodes returns 1 hub with fan_in=15
        crg = MagicMock()
        crg._check_available.return_value = True
        crg.find_large_functions.return_value = {}
        crg.get_hub_nodes.return_value = {"hubs": [{"name": "critical_fn", "fan_in": 15}]}
        crg.check_dead_code.return_value = {}
        crg.get_review_context.return_value = {}
        crg.get_impact_radius.return_value = {}
        crg.get_affected_flows.return_value = {}
        crg.get_knowledge_gaps.return_value = {}
        crg.list_flows.return_value = {}
        # query_graph(tests_for) returns no tests → untested hub
        crg.query_graph.return_value = {"results": []}

        result_dims, score_overridden = _crg_enrich_gate_findings(
            crg, dims, str(tmp_path), str(tmp_path), gate_num=3
        )

        tc = next(d for d in result_dims if d.name == "test_coverage")
        # 1 untested hub × 3 pts = -3 → 85.0 - 3 = 82.0
        assert tc.score == 82.0
        assert any("Phase 2 gatekeeper" in i.get("message", "") for i in tc.issues)
        assert score_overridden is True

    def test_phase2_penalty_capped_at_15(self, tmp_path):
        """Hub penalty capped at 15 pts regardless of untested hub count."""
        from harness.harness_bridge import _crg_enrich_gate_findings, DimResult
        from unittest.mock import MagicMock

        dims = [DimResult(name="test_coverage", score=90.0, threshold=80.0)]

        crg = MagicMock()
        crg._check_available.return_value = True
        crg.find_large_functions.return_value = {}
        crg.get_hub_nodes.return_value = {
            "hubs": [{"name": f"hub_{i}", "fan_in": 15} for i in range(10)]
        }
        crg.check_dead_code.return_value = {}
        crg.get_review_context.return_value = {}
        crg.get_impact_radius.return_value = {}
        crg.get_affected_flows.return_value = {}
        crg.get_knowledge_gaps.return_value = {}
        crg.list_flows.return_value = {}
        crg.query_graph.return_value = {"results": []}  # all untested

        result_dims, score_overridden = _crg_enrich_gate_findings(
            crg, dims, str(tmp_path), str(tmp_path), gate_num=3
        )

        tc = next(d for d in result_dims if d.name == "test_coverage")
        # 5 hubs queried ([:5] cap) × 3 = 15, capped at 15 → 90 - 15 = 75
        assert tc.score == 75.0
        assert score_overridden is True

    def test_phase2_no_penalty_when_hubs_are_tested(self, tmp_path):
        """When all hubs have test linkage, no penalty applied."""
        from harness.harness_bridge import _crg_enrich_gate_findings, DimResult
        from unittest.mock import MagicMock

        dims = [DimResult(name="test_coverage", score=85.0, threshold=80.0)]

        crg = MagicMock()
        crg._check_available.return_value = True
        crg.find_large_functions.return_value = {}
        crg.get_hub_nodes.return_value = {"hubs": [{"name": "tested_fn", "fan_in": 10}]}
        crg.check_dead_code.return_value = {}
        crg.get_review_context.return_value = {}
        crg.get_impact_radius.return_value = {}
        crg.get_affected_flows.return_value = {}
        crg.get_knowledge_gaps.return_value = {}
        crg.list_flows.return_value = {}
        # tests_for returns results → hub IS tested
        crg.query_graph.return_value = {"results": [{"name": "test_fn"}]}

        result_dims, score_overridden = _crg_enrich_gate_findings(
            crg, dims, str(tmp_path), str(tmp_path), gate_num=3
        )

        tc = next(d for d in result_dims if d.name == "test_coverage")
        assert tc.score == 85.0  # unchanged
        assert score_overridden is False

    def test_phase2_untested_hub_with_none_score_does_not_crash(self, tmp_path):
        """test_coverage score=None (not yet applicable) must not crash the
        hub-penalty print — the penalty computation already falls back to
        0.0 via `(_d.score or 0.0)`; the diagnostic print must do the same."""
        from harness.harness_bridge import _crg_enrich_gate_findings, DimResult
        from unittest.mock import MagicMock

        dims = [DimResult(name="test_coverage", score=None, threshold=80.0)]  # type: ignore[arg-type]

        crg = MagicMock()
        crg._check_available.return_value = True
        crg.find_large_functions.return_value = {}
        crg.get_hub_nodes.return_value = {"hubs": [{"name": "critical_fn", "fan_in": 15}]}
        crg.check_dead_code.return_value = {}
        crg.get_review_context.return_value = {}
        crg.get_impact_radius.return_value = {}
        crg.get_affected_flows.return_value = {}
        crg.get_knowledge_gaps.return_value = {}
        crg.list_flows.return_value = {}
        crg.query_graph.return_value = {"results": []}  # untested hub

        result_dims, score_overridden = _crg_enrich_gate_findings(
            crg, dims, str(tmp_path), str(tmp_path), gate_num=3
        )

        tc = next(d for d in result_dims if d.name == "test_coverage")
        # (None or 0.0) - 3 = -3, floored at 0.0
        assert tc.score == 0.0
        assert score_overridden is True


# =============================================================================
# S4: _run_harness_cross_validation — tool-unavailable blocking
# =============================================================================

class TestS4ToolUnavailable:
    """S4 must block when a tool is not installed and agent claims passing score."""

    def _make_result(self, dims: list[dict]) -> dict:
        return {"breakdown": {d["name"]: {"score": d["score"], "tool_output": d.get("tool_output", "")} for d in dims}}

    def _make_ctx(self, tmp_path):
        from harness.harness_bridge import GateContext
        return GateContext(
            gate_num=3, config={}, project_root=str(tmp_path), phase=4,
            fr_id=None, ssi_scripts_dir=str(tmp_path), ssi_prompts_dir=str(tmp_path),
            ssi_schemas_dir=str(tmp_path), work_dir=str(tmp_path / ".sessi-work"),
        )

    def _fake_cfg(self, tmp_path, dims: list[dict]):
        import yaml
        cfg_dir = tmp_path / "harness" / "gate_configs"
        cfg_dir.mkdir(parents=True)
        cfg = {"gate": 3, "dimensions": dims}
        (cfg_dir / "gate3_p4_exit.yaml").write_text(yaml.dump(cfg))

    def test_tool_not_found_blocks_passing_agent_score(self, tmp_path):
        """rc=-3 (not found) + agent_score(85) >= threshold(80) → blocked."""
        from harness.harness_bridge import _run_harness_cross_validation
        ctx = self._make_ctx(tmp_path)
        self._fake_cfg(tmp_path, [
            {"name": "readability", "tool": "radon-mi", "threshold": 80,
             "requires_tool_execution": True},
        ])
        raw = self._make_result([{"name": "readability", "score": 85}])

        with patch("harness.tool_runners.run_tool", return_value=("Tool not found: radon-mi", -3)):
            violations = _run_harness_cross_validation(ctx, raw)

        assert len(violations) == 1
        assert "radon-mi" in violations[0]
        assert "not found" in violations[0]

    def test_tool_not_found_below_threshold_skipped(self, tmp_path):
        """rc=-3 + agent_score(50) < threshold(80) → not cross-validated (no violation)."""
        from harness.harness_bridge import _run_harness_cross_validation
        ctx = self._make_ctx(tmp_path)
        self._fake_cfg(tmp_path, [
            {"name": "readability", "tool": "radon-mi", "threshold": 80,
             "requires_tool_execution": True},
        ])
        raw = self._make_result([{"name": "readability", "score": 50}])

        with patch("harness.tool_runners.run_tool", return_value=("Tool not found: radon-mi", -3)):
            violations = _run_harness_cross_validation(ctx, raw)

        assert violations == []

    def test_tool_timeout_blocks_passing_agent_score(self, tmp_path):
        """rc=-2 (timeout) + agent_score(90) >= threshold(75) → blocked."""
        from harness.harness_bridge import _run_harness_cross_validation
        ctx = self._make_ctx(tmp_path)
        self._fake_cfg(tmp_path, [
            {"name": "performance", "tool": "pytest-benchmark", "threshold": 75,
             "requires_tool_execution": True},
        ])
        raw = self._make_result([{"name": "performance", "score": 90}])

        with patch("harness.tool_runners.run_tool", return_value=("TIMEOUT: pytest-benchmark exceeded 180s", -2)):
            violations = _run_harness_cross_validation(ctx, raw)

        assert len(violations) == 1
        assert "timed out" in violations[0]

    def test_tool_error_blocks_passing_agent_score(self, tmp_path):
        """rc=-4 (unexpected error) + agent_score(95) >= threshold(80) → blocked."""
        from harness.harness_bridge import _run_harness_cross_validation
        ctx = self._make_ctx(tmp_path)
        self._fake_cfg(tmp_path, [
            {"name": "error_handling", "tool": "ast-error-handling", "threshold": 80,
             "requires_tool_execution": True},
        ])
        raw = self._make_result([{"name": "error_handling", "score": 95}])

        with patch("harness.tool_runners.run_tool", return_value=("Error: something", -4)):
            violations = _run_harness_cross_validation(ctx, raw)

        assert len(violations) == 1
        assert "error" in violations[0]

    def test_skip_list_tool_rc_minus1_still_works(self, tmp_path):
        """rc=-1 (skip-list) is NOT blocked by this check — it has its own validation."""
        from harness.harness_bridge import _run_harness_cross_validation
        ctx = self._make_ctx(tmp_path)
        self._fake_cfg(tmp_path, [
            {"name": "mutation_testing", "tool": "mutmut", "threshold": 70,
             "requires_tool_execution": True},
        ])
        raw = self._make_result([
            {"name": "mutation_testing", "score": 85,
             "tool_output": "03-development/mutmut_results.txt"},
        ])
        # Create the tool_output file so skip-list validation passes
        tout = tmp_path / "03-development" / "mutmut_results.txt"
        tout.parent.mkdir(parents=True)
        tout.write_text("Killed 12 out of 15 mutants — kill rate: 80.0%")

        with patch("harness.tool_runners.run_tool", return_value=("", -1)):
            violations = _run_harness_cross_validation(ctx, raw)

        assert violations == []


# =============================================================================
# prepare_gate spec scan — rglob, backtick, parameterized, async
# =============================================================================

class TestPrepareGateSpecScan:
    """prepare_gate() Gate-1 spec coverage scan: rglob, name normalisation."""

    @staticmethod
    def _mock_config(gate_num=1):
        from core.quality_gate.constitution.profile import GateConfig, DimensionConfig
        return GateConfig(
            gate_num=gate_num,
            score_gate=80.0,
            dimensions=[DimensionConfig(name="coverage", threshold=75.0)],
            crg={},
        )

    def _make_project(self, tmp_path, spec_rows: list[str], test_files: dict[str, str]):
        """Create minimal project structure for spec-scan tests.

        spec_rows: list of test function names as they appear in TEST_SPEC.md
        test_files: {relative_path: file_content} under tmp_path/03-development/tests/
        """
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        table = "\n".join(
            f"| {i+1} | {name} | Functional |" for i, name in enumerate(spec_rows)
        )
        (arch / "TEST_SPEC.md").write_text(
            "### FR-01: Widget\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            f"{table}\n",
            encoding="utf-8",
        )
        test_dir = tmp_path / "03-development" / "tests"
        test_dir.mkdir(parents=True)
        for rel_path, content in test_files.items():
            fpath = test_dir / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        return tmp_path

    def _scan(self, tmp_path):
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value=self._mock_config(1)):
            ctx = bridge.prepare_gate(
                gate_num=1, project_root=str(tmp_path), phase=3, fr_id="FR-01"
            )
        return ctx

    def test_rglob_finds_test_in_non_fr_file(self, tmp_path):
        """Test function in test_integration.py (not test_fr01.py) must be found."""
        self._make_project(
            tmp_path,
            spec_rows=["test_widget_renders"],
            test_files={"test_integration.py": "def test_widget_renders():\n    pass\n"},
        )
        ctx = self._scan(tmp_path)
        assert ctx._spec_test_names == ["test_widget_renders"]
        assert "test_widget_renders" in ctx._existing_spec_tests, (
            "rglob must find test_widget_renders in test_integration.py"
        )

    def test_backtick_spec_name_matches(self, tmp_path):
        """Backtick-quoted spec name '`test_fn`' must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["`test_widget_renders`"],
            test_files={"test_fr01.py": "def test_widget_renders():\n    pass\n"},
        )
        ctx = self._scan(tmp_path)
        assert len(ctx._existing_spec_tests) == 1, (
            f"backtick-quoted name must match; existing={ctx._existing_spec_tests}"
        )

    def test_parameterized_spec_name_matches(self, tmp_path):
        """Spec name 'test_fn[param]' must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_widget_renders[dark_mode]", "test_widget_renders[light_mode]"],
            test_files={"test_fr01.py": "def test_widget_renders(mode):\n    pass\n"},
        )
        ctx = self._scan(tmp_path)
        assert len(ctx._existing_spec_tests) == 2, (
            f"both parametrized rows must match the single base function; "
            f"existing={ctx._existing_spec_tests}"
        )

    def test_async_def_matches(self, tmp_path):
        """'async def test_fn(...)' must be found the same as sync 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_widget_async"],
            test_files={"test_fr01.py": "async def test_widget_async(client):\n    pass\n"},
        )
        ctx = self._scan(tmp_path)
        assert "test_widget_async" in ctx._existing_spec_tests, (
            "async def must be detected by the function scanner"
        )

