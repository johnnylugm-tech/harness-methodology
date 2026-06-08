"""Tests for crg_integration.py CLI/subprocess fallback (Stage 1).

These cover the no-mcp_tools path that lets prompt-driven `crg_integration.py
ensure/context/blast/update` work in a plain Bash subprocess. Subprocess and the
CLI presence flag are mocked — no real code-review-graph is invoked.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SSI_DIR = Path(__file__).parent.parent / "harness" / "ssi" / "scripts"
sys.path.insert(0, str(SSI_DIR))

import crg_integration as ci  # noqa: E402


class TestEnsureReadyCli:
    def test_no_binary_unavailable(self):
        with patch.object(ci, "_CRG_CLI", None):
            out = ci._ensure_ready_cli("/repo")
        assert out["available"] is False
        assert "PATH" in out["reason"]

    def test_already_built(self, tmp_path):
        (tmp_path / ".code-review-graph").mkdir()
        (tmp_path / ".code-review-graph" / "graph.db").write_text("x")
        with patch.object(ci, "_CRG_CLI", "/bin/crg"), \
             patch.object(ci, "_cli_node_count", return_value=42):
            out = ci._ensure_ready_cli(str(tmp_path))
        assert out == {
            "available": True, "node_count": 42,
            "action": "already_built", "repo": str(tmp_path),
        }

    def test_builds_when_empty(self, tmp_path):
        with patch.object(ci, "_CRG_CLI", "/bin/crg"), \
             patch.object(ci, "_cli_node_count", side_effect=[0, 100]), \
             patch.object(ci, "_cli_run", return_value=True) as m_run:
            out = ci._ensure_ready_cli(str(tmp_path))
        assert out["available"] is True
        assert out["action"] == "auto_built"
        assert out["node_count"] == 100
        assert m_run.call_count >= 2  # build + postprocess

    def test_build_failure(self, tmp_path):
        with patch.object(ci, "_CRG_CLI", "/bin/crg"), \
             patch.object(ci, "_cli_node_count", return_value=0), \
             patch.object(ci, "_cli_run", return_value=False):
            out = ci._ensure_ready_cli(str(tmp_path))
        assert out["available"] is False
        assert out["action"] == "build_failed"


class TestCliNodeCount:
    def test_parses_status_nodes_line(self, tmp_path):
        (tmp_path / ".code-review-graph").mkdir()
        (tmp_path / ".code-review-graph" / "graph.db").write_text("x")
        fake = MagicMock(stdout="Nodes: 5834\nEdges: 100\n", returncode=0)
        with patch.object(ci, "_CRG_CLI", "/bin/crg"), \
             patch("crg_integration.subprocess.run", return_value=fake):
            assert ci._cli_node_count(str(tmp_path)) == 5834

    def test_no_db_returns_zero(self, tmp_path):
        with patch.object(ci, "_CRG_CLI", "/bin/crg"):
            assert ci._cli_node_count(str(tmp_path)) == 0

    def test_no_binary_returns_minus_one(self, tmp_path):
        with patch.object(ci, "_CRG_CLI", None):
            assert ci._cli_node_count(str(tmp_path)) == -1


class TestRunTool:
    def test_returns_parsed_json(self, tmp_path):
        fake = MagicMock(stdout='{"status": "ok", "summary": "x"}', returncode=0)
        with patch.object(ci, "_crg_interp", return_value="/bin/py"), \
             patch("crg_integration.subprocess.run", return_value=fake):
            out = ci._run_tool(str(tmp_path), "get_minimal_context", task="quality evaluation")
        assert out == {"status": "ok", "summary": "x"}

    def test_empty_on_nonzero_exit(self, tmp_path):
        fake = MagicMock(stdout="", returncode=5)
        with patch.object(ci, "_crg_interp", return_value="/bin/py"), \
             patch("crg_integration.subprocess.run", return_value=fake):
            assert ci._run_tool(str(tmp_path), "detect_changes_func") == {}

    def test_empty_when_no_interpreter(self, tmp_path):
        with patch.object(ci, "_crg_interp", return_value=None):
            assert ci._run_tool(str(tmp_path), "get_minimal_context") == {}
