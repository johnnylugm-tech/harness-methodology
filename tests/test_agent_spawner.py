"""
Unit tests for AgentSpawner.
"""

import pytest
from unittest.mock import patch, MagicMock
from core.agent_spawner import AgentSpawner


class TestAgentSpawner:
    """Tests for the AgentSpawner class."""

    def test_build_prompt_basic(self):
        """Verify prompt construction with basic persona and task."""
        spawner = AgentSpawner()
        with patch("core.agent_spawner._load_persona", return_value="Test Persona"):
            with patch("core.agent_spawner._load_phase_sop", return_value="Test SOP"):
                prompt = spawner._build_prompt("developer", "Write code", {"phase": 3}, phase=0)
                assert "[PERSONA]\nTest Persona" in prompt
                assert "[SOP]\nTest SOP" in prompt
                assert "[TASK]\nWrite code" in prompt

    def test_spawn_routes_to_hermes(self):
        """Verify routing to Hermes reviewer for appropriate phases."""
        spawner = AgentSpawner()
        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = {"review_status": "APPROVE"}
        spawner._reviewer = mock_reviewer

        with patch("harness.reviewer_router.get_reviewer_model", return_value="hermes"):
            result = spawner.spawn(
                role="reviewer",
                prompt="Check this",
                context={"phase": 3},
                model="hermes",
                phase=3
            )
            assert result["review_status"] == "APPROVE"
            mock_reviewer.review.assert_called_once()

    def test_spawn_routes_to_claude_on_p7(self):
        """Verify routing to Claude for phase 7 even if hermes requested."""
        spawner = AgentSpawner()
        with patch("harness.reviewer_router.get_reviewer_model", return_value="claude"):
            mock_sdk = MagicMock()
            with patch.dict("sys.modules", {"claude_code_sdk": mock_sdk}):
                result = spawner.spawn(
                    role="reviewer",
                    prompt="Assess risk",
                    context={"phase": 7},
                    model="hermes",
                    phase=7
                )
                assert result["status"] == "complete"
                mock_sdk.Task.assert_called_once()

    def test_spawn_raises_error_outside_claude(self):
        """Verify RuntimeError when claude_code_sdk is missing."""
        spawner = AgentSpawner()
        # Mock sys.modules to simulate missing package
        with patch.dict("sys.modules", {"claude_code_sdk": None}):
            with pytest.raises(RuntimeError, match="Ensure running inside Claude Code environment"):
                spawner.spawn(
                    role="developer",
                    prompt="Do task",
                    context={},
                    model="claude"
                )

    def test_parse_result_dict(self):
        """Verify parsing of dictionary results."""
        spawner = AgentSpawner()
        res = {"output": "ok", "status": "done"}
        assert spawner._parse_result(res) == res

    def test_parse_result_string(self):
        """Verify parsing of string results into dict."""
        spawner = AgentSpawner()
        res = "completed task"
        parsed = spawner._parse_result(res)
        assert parsed["output"] == "completed task"
        assert parsed["status"] == "complete"
