"""
Unit tests for AgentSpawner.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
                    # call_args is last call (git numstat); find the claude invocation
                    claude_cmd = next(
                        c[0][0] for c in mock_run.call_args_list
                        if c[0][0][0] != "git"
                    )
                    assert "--strict-mcp-config" in claude_cmd
                    assert any("harness/.mcp.json" in str(a) for a in claude_cmd)

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


class TestGitDiffNumstat:
    """Unit tests for AgentSpawner._git_diff_numstat."""

    def _make_proc(self, stdout="", returncode=0):
        p = MagicMock()
        p.returncode = returncode
        p.stdout = stdout
        return p

    def test_parses_normal_output(self):
        output = "10\t3\tsrc/foo.py\n5\t0\tsrc/bar.py\n"
        with patch("core.agent_spawner.subprocess.run", return_value=self._make_proc(output)):
            result = AgentSpawner._git_diff_numstat(Path("/fake/repo"))
        assert result == {"src/foo.py": (10, 3), "src/bar.py": (5, 0)}

    def test_returns_empty_when_repo_root_is_none(self):
        assert AgentSpawner._git_diff_numstat(None) == {}

    def test_returns_empty_on_nonzero_returncode(self):
        with patch("core.agent_spawner.subprocess.run", return_value=self._make_proc("", returncode=128)):
            result = AgentSpawner._git_diff_numstat(Path("/fake/repo"))
        assert result == {}

    def test_returns_empty_on_oserror(self):
        with patch("core.agent_spawner.subprocess.run", side_effect=OSError("no git")):
            result = AgentSpawner._git_diff_numstat(Path("/fake/repo"))
        assert result == {}

    def test_handles_binary_file_dashes(self):
        # git --numstat shows `-` for binary files
        output = "-\t-\timages/logo.png\n"
        with patch("core.agent_spawner.subprocess.run", return_value=self._make_proc(output)):
            result = AgentSpawner._git_diff_numstat(Path("/fake/repo"))
        assert result == {"images/logo.png": (-1, -1)}

    def test_skips_malformed_lines(self):
        output = "10\tsrc/missing_tab.py\n5\t2\tsrc/ok.py\n"
        with patch("core.agent_spawner.subprocess.run", return_value=self._make_proc(output)):
            result = AgentSpawner._git_diff_numstat(Path("/fake/repo"))
        assert result == {"src/ok.py": (5, 2)}


class TestDispatchDiffBudget:
    """Unit tests for AgentSpawner._dispatch_diff_budget (Bug #28/#32/#38/#39 guard)."""

    def _spawner(self):
        return AgentSpawner(project_path=Path("/fake/repo"))

    def test_no_change_returns_empty_flags(self):
        pre = {"src/foo.py": (10, 3)}
        post = {"src/foo.py": (10, 3)}
        flags = self._spawner()._dispatch_diff_budget(pre, post)
        assert flags == {}

    def test_safe_refactor_does_not_fire(self):
        # 20 lines added, 10 lines removed — well under threshold
        pre = {"src/foo.py": (5, 2)}
        post = {"src/foo.py": (25, 12)}
        with patch("core.agent_spawner.subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            flags = self._spawner()._dispatch_diff_budget(pre, post)
        assert "lines_removed>50" not in flags
        assert "xx_markers_introduced" not in flags

    def test_flags_when_lines_removed_exceeds_threshold(self):
        # net_removed = 80 - 5 = 75 → exceeds 50
        pre = {"src/taskq/models.py": (10, 5)}
        post = {"src/taskq/models.py": (10, 80)}
        with patch("core.agent_spawner.subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            flags = self._spawner()._dispatch_diff_budget(pre, post)
        assert "lines_removed>50" in flags
        assert any("models.py" in str(entry) for entry in flags["lines_removed>50"])

    def test_flags_xx_markers_introduced_in_added_lines(self):
        # net_added > 0 and diff output contains +XXRUNNING_STATUSXX = None
        pre = {"src/taskq/models.py": (0, 0)}
        post = {"src/taskq/models.py": (5, 0)}
        diff_output = (
            "diff --git a/src/taskq/models.py b/src/taskq/models.py\n"
            "--- a/src/taskq/models.py\n"
            "+++ b/src/taskq/models.py\n"
            "@@ -1,3 +1,4 @@\n"
            " class TaskStatus:\n"
            "+    XXRUNNING_STATUSXX = None\n"
            "+    XXDONE_STATUSXX = 'done'\n"
            " pass\n"
        )
        with patch("core.agent_spawner.subprocess.run", return_value=MagicMock(returncode=0, stdout=diff_output)):
            flags = self._spawner()._dispatch_diff_budget(pre, post)
        assert "xx_markers_introduced" in flags
        assert "src/taskq/models.py" in flags["xx_markers_introduced"]

    def test_xx_markers_in_removed_lines_do_not_fire(self):
        # `-XXFOOXX` lines (removed) must NOT trigger the flag — only `+` lines matter
        pre = {"src/taskq/models.py": (5, 0)}
        post = {"src/taskq/models.py": (5, 2)}
        diff_output = (
            "@@ -1,3 +1,3 @@\n"
            "-    XXSTATUS = None\n"
            "+    STATUS = 'pending'\n"
        )
        with patch("core.agent_spawner.subprocess.run", return_value=MagicMock(returncode=0, stdout=diff_output)):
            flags = self._spawner()._dispatch_diff_budget(pre, post)
        assert "xx_markers_introduced" not in flags

    def test_non_py_files_skip_xx_marker_check(self):
        # Only .py files are scanned for XX markers
        pre = {"README.md": (0, 0)}
        post = {"README.md": (10, 0)}
        with patch("core.agent_spawner.subprocess.run") as mock_run:
            flags = self._spawner()._dispatch_diff_budget(pre, post)
        # subprocess.run should NOT have been called for XX-marker check
        # (only called for numstat, not for .md files)
        mock_run.assert_not_called()
        assert "xx_markers_introduced" not in flags


class TestRegressionGuardEndToEnd:
    """Integration tests: spawn() fires REGRESSION_GUARD when guard conditions met."""

    def _make_claude_proc(self, result_text="ok"):
        p = MagicMock()
        p.returncode = 0
        p.stdout = json.dumps({"result": result_text, "session_id": "test"})
        return p

    def _make_diff_proc(self, numstat_out="", diff_out=""):
        p = MagicMock()
        p.returncode = 0
        p.stdout = numstat_out or diff_out
        return p

    def test_spawn_fires_guard_on_destructive_removal(self):
        """spawn() returns REGRESSION_GUARD when agent removes 60+ lines from a file."""
        spawner = AgentSpawner(project_path=Path("/fake/repo"))
        claude_proc = self._make_claude_proc()

        # pre: file has (5, 2); post: (5, 65) → net_removed = 63 > 50
        pre_numstat = "5\t2\tsrc/taskq/models.py\n"
        post_numstat = "5\t65\tsrc/taskq/models.py\n"
        call_count = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git" and "--numstat" in cmd:
                call_count[0] += 1
                out = pre_numstat if call_count[0] == 1 else post_numstat
                return MagicMock(returncode=0, stdout=out)
            if cmd[0] == "git" and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return claude_proc

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Improve FR-01",
                    context={}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "REGRESSION_GUARD"
        assert "lines_removed>50" in result["regression_flags"]

    def test_spawn_does_not_fire_guard_on_safe_refactor(self):
        """spawn() returns complete (not REGRESSION_GUARD) for ≤50 lines removed."""
        spawner = AgentSpawner(project_path=Path("/fake/repo"))
        claude_proc = self._make_claude_proc()

        pre_numstat = "10\t0\tsrc/taskq/models.py\n"
        post_numstat = "15\t8\tsrc/taskq/models.py\n"  # net_removed = 8 ≤ 50
        call_count = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git" and "--numstat" in cmd:
                call_count[0] += 1
                out = pre_numstat if call_count[0] == 1 else post_numstat
                return MagicMock(returncode=0, stdout=out)
            if cmd[0] == "git" and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return claude_proc

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Improve FR-01",
                    context={}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "complete"
        assert "regression_flags" not in result
