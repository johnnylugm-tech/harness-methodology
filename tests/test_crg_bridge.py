"""Tests for CRGBridge — MCP-first CRG integration."""

from unittest.mock import patch


class TestCRGBridgeAvailability:
    def test_is_available_when_mcp_not_importable(self):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", False):
            from harness.crg_bridge import CRGBridge
            assert CRGBridge().is_available() is False

    def test_is_available_when_mcp_importable(self):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True):
            from harness.crg_bridge import CRGBridge
            assert CRGBridge().is_available() is True


class TestCRGBridgeUnavailable:
    def test_run_reconnaissance_returns_empty(self):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", False):
            from harness.crg_bridge import CRGBridge
            assert CRGBridge().run_reconnaissance("/tmp") == {}

    def test_get_minimal_context_returns_empty(self):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", False):
            from harness.crg_bridge import CRGBridge
            assert CRGBridge().get_minimal_context("/tmp", "quality") == {}

    def test_check_impact_returns_false(self):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", False):
            from harness.crg_bridge import CRGBridge
            assert CRGBridge().check_impact("/tmp") is False


class TestCRGBridgeMCPIntegration:
    def test_run_reconnaissance_calls_build(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_build", create=True) as mock_build:
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().run_reconnaissance(str(tmp_path))
            mock_build.assert_called_once_with(
                repo_root=str(tmp_path), full_rebuild=True
            )
            assert result == {}

    def test_run_reconnaissance_handles_build_error(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_build", create=True,
                   side_effect=RuntimeError("boom")):
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().run_reconnaissance(str(tmp_path))
            assert result == {}

    def test_get_minimal_context_calls_mcp(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_minimal_context", create=True) as mock_ctx:
            mock_ctx.return_value = {"hint": "use lazy loading"}
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().get_minimal_context(str(tmp_path), "architecture")
            mock_ctx.assert_called_once_with(
                task="architecture", repo_root=str(tmp_path)
            )
            assert result == {"hint": "use lazy loading"}

    def test_get_minimal_context_handles_error(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_minimal_context", create=True,
                   side_effect=RuntimeError("boom")):
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().get_minimal_context(str(tmp_path), "quality")
            assert result == {}

    def test_check_impact_detects_high_risk(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_detect_changes", create=True) as mock_detect:
            mock_detect.return_value = {"risk_score": 0.85}
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().check_impact(str(tmp_path))
            mock_detect.assert_called_once_with(
                base="HEAD", repo_root=str(tmp_path), detail_level="standard"
            )
            assert result is True

    def test_check_impact_low_risk_returns_false(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_detect_changes", create=True) as mock_detect:
            mock_detect.return_value = {"risk_score": 0.3}
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().check_impact(str(tmp_path))
            assert result is False

    def test_check_impact_handles_error(self, tmp_path):
        with patch("harness.crg_bridge._CRG_MCP_AVAILABLE", True), \
             patch("harness.crg_bridge._crg_detect_changes", create=True,
                   side_effect=RuntimeError("boom")):
            from harness.crg_bridge import CRGBridge
            result = CRGBridge().check_impact(str(tmp_path))
            assert result is False


class TestCRGBridgeFileIO:
    def test_check_drift_false_when_no_metrics_file(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        assert CRGBridge().check_drift(str(tmp_path)) is False

    def test_check_drift_true_when_high_drift(self, tmp_path):
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_metrics.json").write_text(
            '{"structural_drift": 0.9}', encoding="utf-8"
        )
        from harness.crg_bridge import CRGBridge
        assert CRGBridge().check_drift(str(tmp_path), threshold=0.4) is True

    def test_check_drift_false_when_low_drift(self, tmp_path):
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_metrics.json").write_text(
            '{"structural_drift": 0.1}', encoding="utf-8"
        )
        from harness.crg_bridge import CRGBridge
        assert CRGBridge().check_drift(str(tmp_path), threshold=0.4) is False

    def test_load_metrics_empty_when_no_file(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        assert CRGBridge().load_metrics(str(tmp_path)) == {}

    def test_load_metrics_returns_data(self, tmp_path):
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_metrics.json").write_text(
            '{"score": 0.85}', encoding="utf-8"
        )
        from harness.crg_bridge import CRGBridge
        assert CRGBridge().load_metrics(str(tmp_path))["score"] == 0.85
