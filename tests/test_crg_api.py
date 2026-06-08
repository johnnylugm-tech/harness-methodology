"""Tests for harness/crg_api.py — universal subprocess backend for CRG tools.

These verify the payload assembly / JSON parsing of call_crg_tool and the
graceful no-op contract of make_tool, all with the subprocess mocked (no real
code-review-graph invocation).
"""

import json
from unittest.mock import patch

import pytest

from harness.crg_api import call_crg_tool, make_tool
from harness.crg_independent import CrgIndependentError


class TestCallCrgTool:
    def test_returns_parsed_json_and_builds_payload(self, tmp_path):
        with patch("harness.crg_api.crg_binary", return_value="/bin/code-review-graph"), \
             patch("harness.crg_api._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_api._run", return_value='{"communities": [1, 2]}') as m_run:
            out = call_crg_tool(str(tmp_path), "list_communities_func", min_size=2)

        assert out == {"communities": [1, 2]}
        cmd = m_run.call_args[0][0]
        assert cmd[0] == "/bin/py"            # CRG interpreter
        assert cmd[1].endswith("crg_tool_runner.py")
        payload = json.loads(cmd[2])
        assert payload["func"] == "list_communities_func"
        assert payload["kwargs"] == {"min_size": 2}
        assert payload["repo_root"] == str(tmp_path.resolve())

    def test_invalid_json_raises(self, tmp_path):
        with patch("harness.crg_api.crg_binary", return_value="/bin/crg"), \
             patch("harness.crg_api._crg_interpreter", return_value="/bin/py"), \
             patch("harness.crg_api._run", return_value="not-json"):
            with pytest.raises(CrgIndependentError, match="invalid JSON"):
                call_crg_tool(str(tmp_path), "list_communities_func")

    def test_missing_binary_raises(self, tmp_path):
        with patch("harness.crg_api.crg_binary", side_effect=CrgIndependentError("no crg")):
            with pytest.raises(CrgIndependentError):
                call_crg_tool(str(tmp_path), "list_communities_func")


class TestMakeTool:
    def test_graceful_empty_on_failure(self, tmp_path):
        with patch("harness.crg_api.crg_binary", side_effect=CrgIndependentError("no crg")):
            tool = make_tool("get_review_context")
            assert tool(repo_root=str(tmp_path)) == {}

    def test_forwards_kwargs_to_call_crg_tool(self):
        with patch("harness.crg_api.call_crg_tool", return_value={"ok": True}) as m_call:
            tool = make_tool("get_impact_radius")
            out = tool(repo_root="/r", base="HEAD~1", max_depth=2)

        assert out == {"ok": True}
        m_call.assert_called_once_with("/r", "get_impact_radius", base="HEAD~1", max_depth=2)
