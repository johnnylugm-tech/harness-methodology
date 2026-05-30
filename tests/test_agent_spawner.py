"""
Unit tests for AgentSpawner.
"""

import json
from pathlib import Path

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

    def test_spawn_routes_to_claude_cli(self):
        """All spawns route to the Claude headless CLI (sole backend)."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "risk assessed",
            "session_id": "abc123",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                result = spawner.spawn(
                    role="reviewer",
                    prompt="Assess risk",
                    context={"phase": 7},
                    model="claude",
                    phase=7
                )
                assert result["status"] == "complete"
                assert "risk assessed" in str(result["output"])
                # Verify CLI flags for need-to-know isolation
                mock_run.assert_called_once()
                cmd = mock_run.call_args[0][0]
                assert "--setting-sources" in cmd
                assert "" in cmd
                assert "--disable-slash-commands" in cmd
                assert "--strict-mcp-config" in cmd
                assert "--max-turns" in cmd
                assert "20" in cmd
                assert "--no-session-persistence" in cmd
                assert "--output-format" in cmd
                assert "json" in cmd

    def test_spawn_raises_error_when_cli_not_found(self):
        """Verify RuntimeError when claude CLI is not on PATH."""
        spawner = AgentSpawner()
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="claude CLI not found"):
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

    # ── MCP config / setting_sources tests ──────────────────────────────

    def test_spawn_defaults_isolate_completely(self):
        """Default params (no MCP, empty setting_sources) = current behavior."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                result = spawner.spawn(
                    role="developer", prompt="Task", context={}, model="claude",
                )
                assert result["status"] == "complete"
                cmd = mock_run.call_args[0][0]
                # Default: strict isolation
                assert "--setting-sources" in cmd
                assert "" in cmd  # empty string = no settings
                assert "--strict-mcp-config" in cmd
                assert '{"mcpServers":{}}' in cmd

    def test_spawn_with_mcp_config_file(self):
        """mcp_config path resolves relative to project_path."""
        spawner = AgentSpawner(project_path=Path("/fake/project"))
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        # Only make the resolved MCP config path appear to exist — not all paths.
        def _exists_only_mcp(self: Path) -> bool:
            return str(self).endswith(".mcp.json")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                with patch.object(Path, "exists", _exists_only_mcp):
                    result = spawner.spawn(
                        role="developer", prompt="Task", context={}, model="claude",
                        mcp_config="harness/.mcp.json",
                        phase_sop_override="", persona_override="",
                    )
                    assert result["status"] == "complete"
                    cmd = mock_run.call_args[0][0]
                    assert "--strict-mcp-config" in cmd
                    assert any("harness/.mcp.json" in str(a) for a in cmd)

    def test_spawn_with_mcp_config_not_found_falls_back(self, capsys):
        """Missing .mcp.json → warning + fallback to empty MCP."""
        spawner = AgentSpawner(project_path=Path("/fake/project"))
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        # Return False for all paths: SOP files are skipped, MCP path triggers warning.
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                with patch.object(Path, "exists", return_value=False):
                    result = spawner.spawn(
                        role="developer", prompt="Task", context={}, model="claude",
                        mcp_config="nonexistent/.mcp.json",
                    )
                    assert result["status"] == "complete"
        stderr = capsys.readouterr().err
        assert "[AgentSpawner] WARNING" in stderr

    def test_spawn_with_inline_mcp_json(self):
        """Inline JSON string is passed directly to --mcp-config."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        inline = '{"mcpServers":{"crg":{"command":"uvx"}}}'
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                result = spawner.spawn(
                    role="developer", prompt="Task", context={}, model="claude",
                    mcp_config=inline,
                )
                assert result["status"] == "complete"
                cmd = mock_run.call_args[0][0]
                assert inline in cmd

    def test_spawn_with_setting_sources_project(self):
        """setting_sources='project' is passed to --setting-sources."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                result = spawner.spawn(
                    role="developer", prompt="Task", context={}, model="claude",
                    setting_sources="project",
                )
                assert result["status"] == "complete"
                cmd = mock_run.call_args[0][0]
                idx = cmd.index("--setting-sources")
                assert cmd[idx + 1] == "project"

    def test_spawn_mcp_config_none_uses_empty_mcp(self):
        """Explicit mcp_config=None still uses empty MCP."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                result = spawner.spawn(
                    role="developer", prompt="Task", context={}, model="claude",
                    mcp_config=None,
                )
                assert result["status"] == "complete"
                cmd = mock_run.call_args[0][0]
                assert '{"mcpServers":{}}' in cmd
