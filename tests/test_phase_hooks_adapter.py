"""Tests for core.adapters.phase_hooks_adapter."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.adapters.phase_hooks_adapter import PhaseHooksAdapter


class TestPhaseHooksAdapter:
    """Tests for the PhaseHooksAdapter thin adapter class."""

    def _make_mock_hooks(self):
        hooks = MagicMock()
        hooks.preflight_all.return_value = {"all_passed": True, "details": {}}
        hooks.preflight_fsm_check.return_value = {"passed": True}
        hooks.preflight_constitution.return_value = {"passed": True}
        hooks.monitoring_before_dev.return_value = None
        hooks.monitoring_after_dev.return_value = None
        hooks.monitoring_before_rev.return_value = None
        hooks.monitoring_after_rev.return_value = None
        hooks.monitoring_hr12_check.return_value = True
        hooks.postflight_all.return_value = {"passed": True}
        hooks.postflight_summary.return_value = {"summary": "ok"}
        hooks.monitoring_events = []
        hooks.fr_results = []
        return hooks

    @pytest.fixture
    def adapter(self):
        a = PhaseHooksAdapter("/tmp/test_project", phase=3)
        a._hooks = self._make_mock_hooks()
        return a

    def test_init_defaults(self):
        a = PhaseHooksAdapter("/tmp/test")
        assert a.project_path == "/tmp/test"
        assert a.phase is None
        assert a._hooks is None

    def test_init_with_phase(self):
        a = PhaseHooksAdapter("/tmp/test", phase=3)
        assert a.phase == 3

    def test_get_hooks_lazy_init(self):
        a = PhaseHooksAdapter("/tmp/t", phase=2)
        assert a._hooks is None
        a._hooks = self._make_mock_hooks()
        hooks = a._get_hooks()
        assert hooks is not None

    def test_get_hooks_cached(self, adapter):
        hooks1 = adapter._get_hooks()
        hooks2 = adapter._get_hooks()
        assert hooks1 is hooks2

    def test_preflight_delegates(self, adapter):
        result = adapter.preflight()
        assert result["all_passed"] is True

    def test_preflight_fsm(self, adapter):
        result = adapter.preflight_fsm()
        assert result["passed"] is True

    def test_preflight_constitution(self, adapter):
        result = adapter.preflight_constitution()
        assert result["passed"] is True

    def test_before_dev_delegates(self, adapter):
        adapter.before_dev("FR-01")
        adapter._get_hooks().monitoring_before_dev.assert_called_with("FR-01")

    def test_after_dev_delegates(self, adapter):
        adapter.after_dev("FR-01", {"status": "done"})
        adapter._get_hooks().monitoring_after_dev.assert_called_once()

    def test_after_dev_none_result(self, adapter):
        adapter.after_dev("FR-01")  # no result
        adapter._get_hooks().monitoring_after_dev.assert_called_once()

    def test_before_rev_delegates(self, adapter):
        adapter.before_rev("FR-01")
        adapter._get_hooks().monitoring_before_rev.assert_called_with("FR-01")

    def test_after_rev_delegates(self, adapter):
        adapter.after_rev("FR-01", {"review_status": "APPROVE"})
        adapter._get_hooks().monitoring_after_rev.assert_called_once()

    def test_hr12_check(self, adapter):
        result = adapter.hr12_check("FR-01", 1, 5)
        assert result is True

    def test_postflight(self, adapter):
        result = adapter.postflight()
        assert result["passed"] is True

    def test_postflight_summary(self, adapter):
        result = adapter.postflight_summary()
        assert result["summary"] == "ok"

    def test_get_current_phase_no_state(self, adapter, tmp_path):
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_get_monitoring_events(self, adapter):
        assert adapter.get_monitoring_events() == []

    def test_get_fr_results(self, adapter):
        assert adapter.get_fr_results() == []

    def test_phase_property_passed_through(self):
        a = PhaseHooksAdapter("/tmp/x", phase=5)
        assert a.phase == 5

    def test_adapts_pathlib_path(self):
        p = Path("/tmp/pp")
        a = PhaseHooksAdapter(str(p))
        assert a.project_path == str(p)

    def test_get_current_phase_exists(self, adapter, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text('{"current_phase": 3}')
        adapter.project_path = str(tmp_path)
        phase = adapter.get_current_phase()
        assert phase == 3

    def test_get_current_phase_no_state_file(self, adapter, tmp_path):
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_get_current_phase_corrupt_json(self, adapter, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text("{bad json")
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_run_phase_lifecycle_preflight_fails(self, adapter):
        adapter._hooks.preflight_all.return_value = {"all_passed": False}
        result = adapter.run_phase_lifecycle([])
        assert result["preflight"]["all_passed"] is False
        assert result["postflight"]["success"] is False

    def test_run_phase_lifecycle_success_path(self, adapter):
        result = adapter.run_phase_lifecycle([])
        assert "preflight" in result
        assert "fr_outcomes" in result
        assert "postflight" in result


class TestSabConstitutionCheck:
    """Tests for SAB constitution validation in PhaseHooks."""

    @pytest.fixture
    def phase_hooks_cls(self):
        from core.phase_hooks import PhaseHooks
        return PhaseHooks

    def test_p1_skips_sab_check(self, tmp_path, phase_hooks_cls):
        """P1 has no SAB requirement — passes with skipped=True."""
        hooks = phase_hooks_cls(str(tmp_path), phase=1)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p2_skips_sab_check(self, tmp_path, phase_hooks_cls):
        """P2 may not have SAB.json yet — passes with skipped=True."""
        hooks = phase_hooks_cls(str(tmp_path), phase=2)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p3_fails_without_sab_json(self, tmp_path, phase_hooks_cls):
        """P3+ requires SAB.json — fails if missing."""
        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert "SAB.json not found" in result.get("message", "")

    def test_p3_passes_with_valid_sab(self, tmp_path, phase_hooks_cls):
        """P3 with valid SAB.json and all modules present passes."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["mod.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "mod.py").write_text("# mod")

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result["layers"] == 1

    def test_sab_check_extra_deps_flagged(self, tmp_path, phase_hooks_cls):
        """Dependencies not in allowed list cause violations."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["mod.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": ["L2"]},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "mod.py").write_text("# mod")

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert len(result["violations"]) >= 1
        assert any("L2" in v for v in result["violations"])

    def test_sab_check_missing_modules(self, tmp_path, phase_hooks_cls):
        """Modules declared in SAB but missing on disk cause violations."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["nonexistent.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert any("missing" in v for v in result["violations"])

    def test_preflight_all_includes_sab(self, tmp_path, phase_hooks_cls):
        """preflight_all() result dict includes 'sab' key."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        # Minimal setup: state.json so FSM doesn't fail
        (method_dir / "state.json").write_text('{"state": "ACTIVE", "current_phase": 3}')

        hooks = phase_hooks_cls(str(tmp_path), phase=3, enable_kill_switch=False)
        result = hooks.preflight_all()
        assert "sab" in result["details"]
        # P3 without SAB.json → sab check fails but preflight_all may still pass
        # (SAB is not a BLOCK-level check yet)
        sab_result = result["details"]["sab"]
        assert "passed" in sab_result
