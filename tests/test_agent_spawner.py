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


class TestGitHeadSha:
    """Unit tests for AgentSpawner._git_head_sha."""

    def test_returns_sha_on_success(self):
        with patch("core.agent_spawner.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="abc123def456\n")):
            result = AgentSpawner._git_head_sha(Path("/fake/repo"))
        assert result == "abc123def456"

    def test_returns_none_when_repo_root_is_none(self):
        assert AgentSpawner._git_head_sha(None) is None

    def test_returns_none_on_nonzero_returncode(self):
        with patch("core.agent_spawner.subprocess.run",
                   return_value=MagicMock(returncode=128, stdout="")):
            result = AgentSpawner._git_head_sha(Path("/fake/repo"))
        assert result is None

    def test_returns_none_on_oserror(self):
        with patch("core.agent_spawner.subprocess.run", side_effect=OSError("no git")):
            result = AgentSpawner._git_head_sha(Path("/fake/repo"))
        assert result is None


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

    def test_base_parameter_forwarded_to_git(self):
        """Custom base SHA must be passed to `git diff --numstat <base>`."""
        calls = []
        def fake_run(cmd, **_kw):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")
        with patch("core.agent_spawner.subprocess.run", side_effect=fake_run):
            AgentSpawner._git_diff_numstat(Path("/fake/repo"), base="abc123sha")
        assert calls[0] == ["git", "diff", "--numstat", "abc123sha"]

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

    def test_xx_marker_check_uses_pre_sha_as_base(self):
        """When pre_sha is provided the XX check must use it, not HEAD.

        This ensures XX markers injected inside a commit (not just the
        working tree) are caught — the guard's blind-to-commits fix.
        """
        pre = {"src/taskq/models.py": (0, 0)}
        post = {"src/taskq/models.py": (5, 0)}
        diff_cmds = []

        def fake_run(cmd, **_kw):
            diff_cmds.append(list(cmd))
            return MagicMock(returncode=0, stdout="")

        with patch("core.agent_spawner.subprocess.run", side_effect=fake_run):
            self._spawner()._dispatch_diff_budget(pre, post, pre_sha="deadbeef")

        assert any(
            cmd[0] == "git" and "deadbeef" in cmd
            for cmd in diff_cmds
        ), f"Expected git diff deadbeef in calls; got: {diff_cmds}"


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
            if cmd[0] == "git" and "show" in cmd:
                return MagicMock(returncode=1, stdout="")
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
            if cmd[0] == "git" and "show" in cmd:
                return MagicMock(returncode=1, stdout="")
            return claude_proc

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Improve FR-01",
                    context={}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "complete"
        assert "regression_flags" not in result

    def test_spawn_fires_guard_when_agent_commits_destructive_change(self):
        """Guard must fire even when the agent commits its changes.

        Before fix: both pre and post used `git diff HEAD`; HEAD moves
        after a commit, so post_diff was empty and net_removed = 0 — the
        guard never fired for committed edits.

        After fix: pre_sha is captured before spawn; post diff is measured
        against pre_sha so committed changes are visible.
        """
        spawner = AgentSpawner(project_path=Path("/fake/repo"))
        claude_proc = self._make_claude_proc()

        def side_effect(cmd, **kw):
            if cmd[0] == "git" and "rev-parse" in cmd:
                # Pre-spawn HEAD SHA
                return MagicMock(returncode=0, stdout="deadbeef\n")
            if cmd[0] == "git" and "--numstat" in cmd:
                base = cmd[-1]
                if base == "HEAD":
                    # Pre-spawn diff: clean working tree
                    return MagicMock(returncode=0, stdout="")
                else:
                    # Post-spawn diff vs pre_sha: agent committed 65 line removal
                    return MagicMock(
                        returncode=0,
                        stdout="5\t65\tsrc/taskq/models.py\n",
                    )
            if cmd[0] == "git" and "diff" in cmd:
                # XX-marker check — no markers
                return MagicMock(returncode=0, stdout="")
            if cmd[0] == "git" and "show" in cmd:
                return MagicMock(returncode=1, stdout="")
            return claude_proc

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Improve FR-01",
                    context={}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "REGRESSION_GUARD", (
            "Guard must fire for committed destructive edits, not just uncommitted ones"
        )
        assert "lines_removed>50" in result["regression_flags"]

    def test_spawn_exempts_docstring_mass_deletion(self, tmp_path):
        """REGRESSION_GUARD must NOT fire when >50 raw lines removed are a docstring.

        AST-based logical-line counting should determine that zero real code was
        removed and exempt the file, so spawn() returns 'complete' not REGRESSION_GUARD.
        """
        (tmp_path / ".git").mkdir()
        spawner = AgentSpawner(project_path=tmp_path)
        claude_proc = self._make_claude_proc()

        # Pre: 63-line module docstring + tiny function.  Post: docstring removed.
        pre_source = '"""\n' + "\n".join(["Long description line."] * 61) + '\n"""\ndef foo():\n    return 1\n'
        post_source = "def foo():\n    return 1\n"

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "models.py").write_text(post_source, encoding="utf-8")

        call_count = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git" and "--numstat" in cmd:
                call_count[0] += 1
                # pre: 5 added 0 removed; post: 5 added 63 removed (raw)
                out = "5\t0\tsrc/models.py\n" if call_count[0] == 1 else "5\t63\tsrc/models.py\n"
                return MagicMock(returncode=0, stdout=out)
            if cmd[0] == "git" and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            if cmd[0] == "git" and "show" in cmd:
                return MagicMock(returncode=0, stdout=pre_source)
            return claude_proc

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Improve FR-01",
                    context={}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "complete", (
            f"Docstring-only removal must not trigger REGRESSION_GUARD: {result.get('regression_flags')}"
        )

    def test_log_dispatch_receives_regression_guard_status(self):
        """_log_dispatch must be called AFTER status is overridden to REGRESSION_GUARD.

        Regression guard dispatches were logging status="complete" because
        _log_dispatch was called before the status override, making
        sessions_spawn.log entries indistinguishable from normal dispatches.
        """
        spawner = AgentSpawner(project_path=Path("/fake/repo"))
        claude_proc = self._make_claude_proc()

        # pre: (5, 2); post: (5, 65) → net_removed = 63 > 50 threshold
        pre_numstat = "5\t2\tsrc/taskq/models.py\n"
        post_numstat = "5\t65\tsrc/taskq/models.py\n"
        call_count = [0]
        logged_status = []

        def side_effect(cmd, **kw):
            if cmd[0] == "git" and "--numstat" in cmd:
                call_count[0] += 1
                out = pre_numstat if call_count[0] == 1 else post_numstat
                return MagicMock(returncode=0, stdout=out)
            if cmd[0] == "git" and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            if cmd[0] == "git" and "show" in cmd:
                return MagicMock(returncode=1, stdout="")
            return claude_proc

        mock_logger = MagicMock()

        def mock_log_spawn(**kwargs):
            logged_status.append(kwargs.get("status"))
        mock_logger.log_spawn = mock_log_spawn

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                with patch("core.sessions_spawn_logger.SessionsSpawnLogger", return_value=mock_logger):
                    result = spawner.spawn(
                        role="developer", prompt="Improve FR-01",
                        context={}, model="claude", fr_id="FR-01",
                    )
        assert result["status"] == "REGRESSION_GUARD"
        assert len(logged_status) == 1, f"Expected exactly one log_dispatch call, got {len(logged_status)}"
        assert logged_status[0] == "REGRESSION_GUARD", (
            f"_log_dispatch received status={logged_status[0]!r}, "
            f"expected 'REGRESSION_GUARD'. "
            f"This means _log_dispatch was called before the status override."
        )


class TestTimeoutRegressionGuard:
    """Regression guard must run even when sub-agent times out (Bug: early return skipped guard)."""

    def _spawner(self):
        return AgentSpawner(project_path=Path("/fake/repo"))

    def test_timeout_triggers_regression_guard(self):
        """TimeoutExpired branch must still call _dispatch_diff_budget before returning."""
        import subprocess as _sp
        spawner = self._spawner()
        guard_called = []

        orig_guard = spawner._dispatch_diff_budget

        def tracking_guard(pre, post, pre_sha=None):
            guard_called.append((pre, post, pre_sha))
            return orig_guard(pre, post, pre_sha=pre_sha)

        spawner._dispatch_diff_budget = tracking_guard

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run") as mock_run:
                mock_run.side_effect = _sp.TimeoutExpired(["claude", "-p"], 60)
                result = spawner.spawn(
                    role="developer", prompt="Do task",
                    context={}, model="claude",
                )

        assert guard_called, (
            "_dispatch_diff_budget was not called for timeout — "
            "regression guard is being skipped on early return"
        )
        assert result["status"] == "TIMEOUT"
        # Guard result should be incorporated even on timeout
        assert guard_called[0][0] == {}  # pre_diff (pre-spawn snapshot)
        # post_diff is captured after timeout, git may report changed state

    def test_nonzero_returncode_triggers_regression_guard(self):
        """Non-zero exit branch must still call _dispatch_diff_budget before returning."""
        spawner = self._spawner()
        guard_called = []

        orig_guard = spawner._dispatch_diff_budget

        def tracking_guard(pre, post, pre_sha=None):
            guard_called.append((pre, post, pre_sha))
            return orig_guard(pre, post, pre_sha=pre_sha)

        spawner._dispatch_diff_budget = tracking_guard

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "internal error"
        mock_proc.stdout = ""

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Do task",
                    context={}, model="claude",
                )

        assert guard_called, (
            "_dispatch_diff_budget was not called for non-zero returncode — "
            "regression guard is being skipped on early return"
        )
        assert result["status"] == "ERROR"
        assert result["exit_code"] == 1


class TestCalculateLogicalRemoval:
    """Unit tests for AgentSpawner._calculate_logical_removal."""

    def test_docstring_removal_produces_near_zero_logical_delta(self, tmp_path):
        """Removing a 60-line module docstring should yield a logical delta near 0."""
        spawner = AgentSpawner(project_path=tmp_path)

        pre_source = '"""\n' + "\n".join(["Description."] * 60) + '\n"""\ndef foo():\n    return 1\n'
        post_source = "def foo():\n    return 1\n"

        post_file = tmp_path / "src" / "foo.py"
        post_file.parent.mkdir()
        post_file.write_text(post_source, encoding="utf-8")

        with patch("core.agent_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=pre_source)
            result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")

        assert result is not None
        assert result <= 0, "Docstring lines must not count as logical code removal"

    def test_syntax_error_in_pre_source_returns_none(self, tmp_path):
        """SyntaxError in the historical file must return None (skip exemption)
        rather than falling back to raw line count, which could cause false negatives."""
        spawner = AgentSpawner(project_path=tmp_path)

        invalid_source = "def foo(\n    # unclosed — syntactically invalid\n"
        post_file = tmp_path / "src" / "foo.py"
        post_file.parent.mkdir()
        post_file.write_text("def foo(): return 1\n", encoding="utf-8")

        with patch("core.agent_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=invalid_source)
            result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")

        assert result is None, "SyntaxError must yield None, not a raw fallback count"

    def test_timeout_returns_none(self, tmp_path):
        """subprocess.TimeoutExpired on git show must return None gracefully."""
        import subprocess as _sp
        spawner = AgentSpawner(project_path=tmp_path)

        with patch("core.agent_spawner.subprocess.run") as mock_run:
            mock_run.side_effect = _sp.TimeoutExpired(["git", "show"], 10)
            result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")

        assert result is None

    def test_unicode_error_in_pre_source_returns_none(self, tmp_path):
        """UnicodeDecodeError from git show must return None gracefully."""
        spawner = AgentSpawner(project_path=tmp_path)

        with patch("core.agent_spawner.subprocess.run") as mock_run:
            mock_run.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
            result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")

        assert result is None

    def test_no_project_path_returns_none(self):
        spawner = AgentSpawner(project_path=None)
        result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")
        assert result is None

    def test_git_show_failure_returns_none(self, tmp_path):
        """git show returncode != 0 (file not in that commit) must return None."""
        spawner = AgentSpawner(project_path=tmp_path)

        with patch("core.agent_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = spawner._calculate_logical_removal("src/foo.py", "deadbeef")

        assert result is None
