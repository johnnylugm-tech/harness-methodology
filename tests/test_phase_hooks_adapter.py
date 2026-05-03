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
