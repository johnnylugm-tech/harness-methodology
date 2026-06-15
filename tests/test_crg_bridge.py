"""Tests for CRGBridge — mandatory CRG integration (no graceful degradation)."""

import sys
from unittest.mock import MagicMock

import pytest

from harness.crg_bridge import CRGBridge

_mock_mcp = sys.modules["mcp_tools"]


@pytest.fixture(autouse=True)
def _reset_mcp_mocks():
    """Reset all MCP tool mocks between tests."""
    _mock_mcp.reset_mock()


class TestCRGBridgeCore:
    """Core graph lifecycle and context methods."""

    def test_refresh_graph_calls_build(self):
        bridge = CRGBridge()
        bridge.refresh_graph("/tmp/project")
        _mock_mcp.mcp__code_review_graph__build_or_update_graph_tool.assert_called_once_with(
            repo_root="/tmp/project", full_rebuild=False
        )

    def test_run_reconnaissance_calls_build_then_reads_file(self, tmp_path):
        recon_data = {"risk_score": 0.5, "untested_hotspots": []}
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_reconnaissance.json").write_text(
            '{"risk_score": 0.5, "untested_hotspots": []}', encoding="utf-8"
        )
        bridge = CRGBridge()
        result = bridge.run_reconnaissance(str(tmp_path))
        _mock_mcp.mcp__code_review_graph__build_or_update_graph_tool.assert_called_once_with(
            repo_root=str(tmp_path), full_rebuild=True
        )
        assert result == recon_data

    def test_run_reconnaissance_raises_when_no_recon_file(self, tmp_path):
        bridge = CRGBridge()
        with pytest.raises(FileNotFoundError, match="CRG reconnaissance data not found"):
            bridge.run_reconnaissance(str(tmp_path))

    def test_get_minimal_context_calls_mcp(self):
        _mock_mcp.mcp__code_review_graph__get_minimal_context_tool.return_value = {
            "hint": "use lazy loading"
        }
        bridge = CRGBridge()
        result = bridge.get_minimal_context("/tmp/project", "architecture")
        _mock_mcp.mcp__code_review_graph__get_minimal_context_tool.assert_called_once_with(
            task="architecture", repo_root="/tmp/project"
        )
        assert result == {"hint": "use lazy loading"}

    def test_check_impact_detects_high_risk(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.85
        }
        bridge = CRGBridge()
        result = bridge.check_impact("/tmp/project")
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.assert_called_once_with(
            base="HEAD", repo_root="/tmp/project", detail_level="standard"
        )
        assert result is True

    def test_check_impact_low_risk_returns_false(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.3
        }
        bridge = CRGBridge()
        result = bridge.check_impact("/tmp/project")
        assert result is False


class TestCRGBridgeFileIO:
    """Drift and metrics file I/O."""

    def test_check_drift_true_when_high_risk(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.85
        }
        bridge = CRGBridge()
        assert bridge.check_drift("/tmp/project", threshold=0.4) is True

    def test_check_drift_false_when_low_risk(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.1
        }
        bridge = CRGBridge()
        assert bridge.check_drift("/tmp/project", threshold=0.4) is False

    def test_check_drift_false_when_risk_score_none(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": None
        }
        bridge = CRGBridge()
        assert bridge.check_drift("/tmp/project", threshold=0.4) is False

    def test_check_drift_passes_base_parameter(self):
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.1
        }
        bridge = CRGBridge()
        bridge.check_drift("/tmp/project", base="fix-start-sha")
        _mock_mcp.mcp__code_review_graph__detect_changes_tool.assert_called_with(
            base="fix-start-sha", repo_root="/tmp/project", detail_level="minimal"
        )

    def test_load_metrics_raises_when_no_file(self, tmp_path):
        bridge = CRGBridge()
        with pytest.raises(FileNotFoundError, match="CRG metrics not found"):
            bridge.load_metrics(str(tmp_path))

    def test_load_metrics_returns_data(self, tmp_path):
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_metrics.json").write_text(
            '{"score": 0.85}', encoding="utf-8"
        )
        bridge = CRGBridge()
        assert bridge.load_metrics(str(tmp_path))["score"] == 0.85


class TestCRGBridgeExtended:
    """Extended structural analysis tools (optional MCP imports)."""

    # Module-level variables that tests mutate — saved/restored per test
    _EXTENDED_ATTRS = [
        "_crg_hub_nodes", "_crg_list_communities", "_crg_knowledge_gaps",
        "_crg_semantic_search", "_crg_query_graph", "_crg_large_funcs",
        "_crg_list_flows", "_crg_refactor",
    ]

    @pytest.fixture(autouse=True)
    def _save_restore_extended_attrs(self):
        import harness.crg_bridge as crg_mod
        saved = {a: getattr(crg_mod, a, None) for a in self._EXTENDED_ATTRS}
        yield
        for attr, val in saved.items():
            setattr(crg_mod, attr, val)

    def test_get_hub_nodes_when_available(self):
        _mock_mcp.mcp__code_review_graph__get_hub_nodes_tool = MagicMock(
            return_value={"hubs": [{"name": "main", "fan_in": 15}]}
        )
        # Re-import after adding the mock
        import harness.crg_bridge as crg_mod
        crg_mod._crg_hub_nodes = _mock_mcp.mcp__code_review_graph__get_hub_nodes_tool
        bridge = CRGBridge()
        result = bridge.get_hub_nodes("/tmp/project", min_fan_in=5)
        assert len(result["hubs"]) == 1

    def test_get_hub_nodes_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_hub_nodes = None
        bridge = CRGBridge()
        result = bridge.get_hub_nodes("/tmp/project")
        assert result == {}

    def test_list_communities_when_available(self):
        _mock_mcp.mcp__code_review_graph__list_communities_tool = MagicMock(
            return_value={"communities": [{"name": "core", "cohesion": 0.6}]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_list_communities = (
            _mock_mcp.mcp__code_review_graph__list_communities_tool
        )
        bridge = CRGBridge()
        result = bridge.list_communities("/tmp/project")
        assert len(result["communities"]) == 1

    def test_list_communities_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_list_communities = None
        bridge = CRGBridge()
        result = bridge.list_communities("/tmp/project")
        assert result == {}

    def test_semantic_search_when_available(self):
        _mock_mcp.mcp__code_review_graph__semantic_search_nodes_tool = MagicMock(
            return_value={"results": [{"name": "login"}]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_semantic_search = (
            _mock_mcp.mcp__code_review_graph__semantic_search_nodes_tool
        )
        bridge = CRGBridge()
        result = bridge.semantic_search("/tmp/project", "login")
        assert len(result["results"]) == 1

    def test_semantic_search_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_semantic_search = None
        bridge = CRGBridge()
        result = bridge.semantic_search("/tmp/project", "login")
        assert result == {}

    def test_query_graph_when_available(self):
        _mock_mcp.mcp__code_review_graph__query_graph_tool = MagicMock(
            return_value={"callers": ["a", "b"]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_query_graph = (
            _mock_mcp.mcp__code_review_graph__query_graph_tool
        )
        bridge = CRGBridge()
        result = bridge.query_graph("/tmp/project", "callers_of", "main")
        assert result == {"callers": ["a", "b"]}

    def test_query_graph_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_query_graph = None
        bridge = CRGBridge()
        result = bridge.query_graph("/tmp/project", "callers_of", "main")
        assert result == {}

    def test_find_large_functions_when_available(self):
        _mock_mcp.mcp__code_review_graph__find_large_functions_tool = MagicMock(
            return_value={"functions": [{"name": "big_fn", "lines": 200}]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_large_funcs = (
            _mock_mcp.mcp__code_review_graph__find_large_functions_tool
        )
        bridge = CRGBridge()
        result = bridge.find_large_functions("/tmp/project", min_lines=100)
        assert len(result["functions"]) == 1

    def test_find_large_functions_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_large_funcs = None
        bridge = CRGBridge()
        result = bridge.find_large_functions("/tmp/project")
        assert result == {}

    def test_list_flows_when_available(self):
        _mock_mcp.mcp__code_review_graph__list_flows_tool = MagicMock(
            return_value={"flows": [{"name": "auth_flow", "criticality": 0.9}]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_list_flows = (
            _mock_mcp.mcp__code_review_graph__list_flows_tool
        )
        bridge = CRGBridge()
        result = bridge.list_flows("/tmp/project")
        assert len(result["flows"]) == 1

    def test_list_flows_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_list_flows = None
        bridge = CRGBridge()
        result = bridge.list_flows("/tmp/project")
        assert result == {}

    def test_check_dead_code_when_available(self):
        _mock_mcp.mcp__code_review_graph__refactor_tool = MagicMock(
            return_value={"dead_symbols": [{"name": "unused_fn"}]}
        )
        import harness.crg_bridge as crg_mod
        crg_mod._crg_refactor = (
            _mock_mcp.mcp__code_review_graph__refactor_tool
        )
        bridge = CRGBridge()
        result = bridge.check_dead_code("/tmp/project")
        assert len(result["dead_symbols"]) == 1

    def test_check_dead_code_returns_empty_when_unavailable(self):
        import harness.crg_bridge as crg_mod
        crg_mod._crg_refactor = None
        bridge = CRGBridge()
        result = bridge.check_dead_code("/tmp/project")
        assert result == {}


class TestCRGBridgeSignatureFilter:
    """Bug #29 regression: CRG tools (mcp__code_review_graph__*) may not
    accept every kwarg. Introspect signature before calling; drop
    unsupported kwargs instead of letting TypeError silently kill the
    fallback path."""

    def test_get_hub_nodes_drops_min_fan_in_when_unsupported(self, monkeypatch):
        """Tool signature lacks min_fan_in — call must succeed, kwarg dropped."""
        import harness.crg_bridge as crg_mod
        def fake_tool(repo_root):
            return {"hubs": [{"name": "x", "fan_in": 1}]}
        monkeypatch.setattr(crg_mod, "_crg_hub_nodes", fake_tool)
        bridge = CRGBridge()
        result = bridge.get_hub_nodes("/tmp/project", min_fan_in=42)
        assert "hubs" in result

    def test_get_hub_nodes_passes_min_fan_in_when_supported(self, monkeypatch):
        def fake_tool(repo_root, min_fan_in):
            return {"hubs": [{"name": "x", "fan_in": min_fan_in}]}
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_hub_nodes", fake_tool)
        bridge = CRGBridge()
        result = bridge.get_hub_nodes("/tmp/project", min_fan_in=7)
        assert result["hubs"][0]["fan_in"] == 7

    def test_get_hub_nodes_returns_empty_when_unavailable(self, monkeypatch):
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_hub_nodes", None)
        bridge = CRGBridge()
        assert bridge.get_hub_nodes("/tmp/project") == {}

    def test_list_communities_drops_unsupported_kwargs(self, monkeypatch):
        def fake_tool(repo_root):
            return {"communities": []}
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_list_communities", fake_tool)
        bridge = CRGBridge()
        # min_size and sort_by are both dropped because fake_tool accepts only repo_root.
        result = bridge.list_communities("/tmp/project", min_size=5, sort_by="name")
        assert result == {"communities": []}

    def test_find_large_functions_drops_unsupported_kwargs(self, monkeypatch):
        def fake_tool(repo_root, limit):
            return {"functions": []}
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_large_funcs", fake_tool)
        bridge = CRGBridge()
        # min_lines and kind are dropped because fake_tool accepts only repo_root, limit.
        result = bridge.find_large_functions(
            "/tmp/project", min_lines=50, kind="Function", limit=10
        )
        assert result == {"functions": []}

    def test_get_knowledge_gaps_drops_unsupported_kwargs(self, monkeypatch):
        """Bug #29 regression: get_knowledge_gaps must go through _call_crg."""
        import harness.crg_bridge as crg_mod
        def fake_tool():
            return {"gaps": []}
        monkeypatch.setattr(crg_mod, "_crg_knowledge_gaps", fake_tool)
        bridge = CRGBridge()
        # repo_root is dropped because fake_tool accepts no kwargs.
        result = bridge.get_knowledge_gaps("/tmp/project")
        assert result == {"gaps": []}

    def test_get_knowledge_gaps_returns_empty_when_unavailable(self, monkeypatch):
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_knowledge_gaps", None)
        bridge = CRGBridge()
        assert bridge.get_knowledge_gaps("/tmp/project") == {}

    def test_list_flows_drops_unsupported_kwargs(self, monkeypatch):
        """Bug #29 regression: list_flows must go through _call_crg."""
        import harness.crg_bridge as crg_mod
        def fake_tool(repo_root):
            return {"flows": []}
        monkeypatch.setattr(crg_mod, "_crg_list_flows", fake_tool)
        bridge = CRGBridge()
        # limit and sort_by are dropped because fake_tool accepts only repo_root.
        result = bridge.list_flows("/tmp/project", limit=5, sort_by="name")
        assert result == {"flows": []}

    def test_list_flows_returns_empty_when_unavailable(self, monkeypatch):
        import harness.crg_bridge as crg_mod
        monkeypatch.setattr(crg_mod, "_crg_list_flows", None)
        bridge = CRGBridge()
        assert bridge.list_flows("/tmp/project") == {}
