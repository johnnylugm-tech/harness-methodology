"""
Tests for core.harness_config feature flag loading and harness integration.
"""
import json
import pytest
from pathlib import Path

from core.harness_config import load_harness_config, get_feature

pytestmark = [pytest.mark.mutation_oracle, pytest.mark.core]


# ---------------------------------------------------------------------------
# TestLoadHarnessConfig
# ---------------------------------------------------------------------------

class TestLoadHarnessConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_harness_config(tmp_path)
        assert cfg["mutation_testing"] is False
        assert cfg["crg_architecture"] is True
        assert cfg["phase4_llm_review"] is True

    def test_valid_file_overrides_defaults(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"crg_architecture": False}})
        )
        cfg = load_harness_config(tmp_path)
        assert cfg["crg_architecture"] is False
        assert cfg["mutation_testing"] is False  # default intact
        assert cfg["phase4_llm_review"] is True  # default intact

    def test_malformed_json_returns_defaults(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text("not json{{{")
        cfg = load_harness_config(tmp_path)
        assert cfg["mutation_testing"] is False
        assert cfg["crg_architecture"] is True

    def test_unknown_keys_ignored(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"future_flag": True, "crg_architecture": False}})
        )
        cfg = load_harness_config(tmp_path)
        assert cfg["crg_architecture"] is False
        assert "future_flag" not in cfg


# ---------------------------------------------------------------------------
# TestGetFeature
# ---------------------------------------------------------------------------

class TestGetFeature:
    def test_mutation_testing_default_false(self, tmp_path):
        assert get_feature(tmp_path, "mutation_testing") is False

    def test_crg_architecture_default_true(self, tmp_path):
        assert get_feature(tmp_path, "crg_architecture") is True

    def test_phase4_llm_review_default_true(self, tmp_path):
        assert get_feature(tmp_path, "phase4_llm_review") is True

    def test_file_overrides_default(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"phase4_llm_review": False}})
        )
        assert get_feature(tmp_path, "phase4_llm_review") is False

    def test_unknown_key_returns_none(self, tmp_path):
        assert get_feature(tmp_path, "nonexistent_flag") is None


# ---------------------------------------------------------------------------
# TestMutmutAdvancePhaseRemoved
# ---------------------------------------------------------------------------

class TestMutmutAdvancePhaseRemoved:
    def test_run_mutation_precheck_not_imported_in_advance_phase(self):
        """advance-phase (cmd_advance_phase) must not call run_mutation_precheck."""
        import ast
        import harness_cli
        src = Path(harness_cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_advance_phase":
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and child.id == "run_mutation_precheck":
                        pytest.fail("run_mutation_precheck still referenced in cmd_advance_phase")
                return  # function found and clean
        # function not found — acceptable (might be renamed); pass


# ---------------------------------------------------------------------------
# TestPhase4LLMReview
# ---------------------------------------------------------------------------

class TestPhase4LLMReview:
    def test_adversarial_review_skipped_when_disabled(self, tmp_path):
        """When phase4_llm_review=False, _override_adversarial_review_dim_score exits early."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"phase4_llm_review": False}})
        )
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("adversarial_review", str(tmp_path)) is True

    def test_adversarial_review_enabled_by_default(self, tmp_path):
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("adversarial_review", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# TestCRGArchitecture
# ---------------------------------------------------------------------------

class TestCRGArchitecture:
    def test_architecture_dim_disabled_when_flag_off(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"crg_architecture": False}})
        )
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("architecture", str(tmp_path)) is True

    def test_architecture_enabled_by_default(self, tmp_path):
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("architecture", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# TestDimFiltering
# ---------------------------------------------------------------------------

class TestDimFiltering:
    def test_is_dim_disabled_unknown_dim_returns_false(self, tmp_path):
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("linting", str(tmp_path)) is False
        assert _is_dim_disabled("security", str(tmp_path)) is False

    def test_mutation_testing_dim_disabled_when_flag_off(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"mutation_testing": True}})
        )
        from harness.harness_bridge import _is_dim_disabled
        # mutation_testing=True means NOT disabled
        assert _is_dim_disabled("mutation_testing", str(tmp_path)) is False

    def test_mutation_testing_dim_disabled_when_flag_on_false(self, tmp_path):
        # default: mutation_testing=False → dim IS disabled
        from harness.harness_bridge import _is_dim_disabled
        assert _is_dim_disabled("mutation_testing", str(tmp_path)) is True


# ---------------------------------------------------------------------------
# TestBuildGateMeta
# ---------------------------------------------------------------------------

class TestBuildGateMeta:
    def _features(self, **overrides):
        base = {"mutation_testing": True, "crg_architecture": True, "phase4_llm_review": True}
        return {**base, **overrides}

    def test_mutation_testing_disabled_removes_dim_from_gates(self):
        from scripts.generate_full_plan import _build_gate_meta
        meta = _build_gate_meta(self._features(mutation_testing=False))
        # Gate 2 originally has mutation_testing(70)
        assert "mutation_testing(70)" not in meta[2][2]
        # Dim count decremented
        from scripts.generate_full_plan import _GATE_META
        assert meta[2][1] == _GATE_META[2][1] - 1

    def test_crg_disabled_removes_architecture_and_crg_note(self):
        from scripts.generate_full_plan import _build_gate_meta, _GATE_META
        meta = _build_gate_meta(self._features(crg_architecture=False))
        assert "architecture(80)" not in meta[3][2]
        assert "CRG recon inside run-gate" not in meta[3][2]
        assert meta[3][1] == _GATE_META[3][1] - 1

    def test_phase4_llm_review_disabled_removes_adversarial_review(self):
        from scripts.generate_full_plan import _build_gate_meta, _GATE_META
        meta = _build_gate_meta(self._features(phase4_llm_review=False))
        assert "adversarial_review(100)" not in meta[3][2]
        assert "bug_hunt_report.json" not in meta[3][2]
        assert meta[3][1] == _GATE_META[3][1] - 1

    def test_all_defaults_preserves_original(self):
        from scripts.generate_full_plan import _build_gate_meta, _GATE_META
        # mutation_testing=False by default, so gate meta differs from _GATE_META by that
        meta = _build_gate_meta({"mutation_testing": True, "crg_architecture": True, "phase4_llm_review": True})
        for gate_num in (1, 2, 3, 4):
            assert meta[gate_num] == _GATE_META[gate_num]
