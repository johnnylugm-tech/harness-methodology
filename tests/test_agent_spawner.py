"""
Unit tests for AgentSpawner.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.agent_spawner import (
    _UNATTENDED_PREAMBLE,
    AgentSpawner,
    _classify_dispatch_error,
    _denoise_cli_stderr,
    _extract_dispatch_error,
    is_structurally_broken,
)


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

    # ── Inner-JSON semantic validator (P3 2026-07-15 FR-03) ─────────────
    # Bug class: sub-agent exits 0 with semantic no-op JSON
    # ({"status":"AWAITING_CONFIRMATION","commit":""}) — transport success
    # but no real progress. spawn() used to return status="complete" and
    # silently wasted the per-FR slot. Validator must re-classify as ERROR.

    def test_spawn_inner_json_awaiting_confirmation_returns_error(self):
        """Inner JSON status=AWAITING_CONFIRMATION → ERROR (P3 FR-03 case)."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({
                "status": "AWAITING_CONFIRMATION",
                "commit": "",
                "summary": "等待老闆確認",
            }),
            "session_id": "00c21a73-99e5-4195-9111-8f616d334f4e",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task", context={"step": "TDD-RED"},
                    model="claude",
                )
        assert result["status"] == "ERROR"
        assert "AWAITING_CONFIRMATION" in result["output"]
        assert result.get("exit_code") == 0

    def test_spawn_inner_json_nothing_to_do_returns_error(self):
        """Inner JSON status=NOTHING_TO_DO → ERROR (conservative default)."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({"status": "NOTHING_TO_DO", "commit": ""}),
            "session_id": "x",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task", context={"step": "TDD-RED"},
                    model="claude",
                )
        assert result["status"] == "ERROR"

    def test_spawn_commit_required_step_with_empty_commit_returns_error(self):
        """TDD-RED step with empty commit + status='complete' → still ERROR.

        Sub-agent may report done when not. The validator catches this
        by checking commit presence on commit-required steps.
        """
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({"status": "complete", "commit": ""}),
            "session_id": "x",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "TDD-RED"}, model="claude",
                )
        assert result["status"] == "ERROR"
        assert "empty commit" in result["output"]

    def test_spawn_commit_required_step_with_real_commit_passes(self):
        """TDD-RED step with non-empty commit + status='complete' → 'complete'."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({"status": "complete", "commit": "abcd1234"}),
            "session_id": "x",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "TDD-RED"}, model="claude",
                )
        assert result["status"] == "complete"
        assert result.get("commit") == "abcd1234"

    def test_spawn_gate1_blocked_with_empty_commit_passes(self):
        """Fix H regression: GATE1 pass=false + commit=null is a legitimate
        BLOCKED verdict (finalize-gate only commits on pass), not a no-op —
        must NOT be reclassified as ERROR (e6f8b90 2026-07-15 over-generalised
        commit-required to GATE1/GATE1-DELTA and broke exactly this case)."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({
                "status": "DONE",
                "pass": False,
                "failing_dims": ["test_coverage"],
                "commit": None,
                "summary": "GATE1 BLOCKED: test_coverage 64 < 90",
            }),
            "session_id": "x",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "GATE1"}, model="claude",
                )
        assert result["status"] == "complete"

    def test_spawn_gate1_pass_with_empty_commit_still_errors(self):
        """Fix H must not weaken the original no-op protection: a GATE1
        verdict claiming pass=true with no commit is still suspicious
        (finalize-gate should have committed) and stays an ERROR."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({
                "status": "DONE",
                "pass": True,
                "failing_dims": [],
                "commit": "",
                "summary": "GATE1 PASS",
            }),
            "session_id": "x",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "GATE1"}, model="claude",
                )
        assert result["status"] == "ERROR"
        assert "empty commit" in result["output"]

    def test_spawn_non_commit_required_step_with_empty_commit_passes(self):
        """LINT-FIX has no commit requirement — empty commit is fine."""
        from core.agent_spawner import _COMMIT_REQUIRED_STEPS
        assert "LINT-FIX" not in _COMMIT_REQUIRED_STEPS
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "lint fixed",
            "session_id": "x",
            "status": "complete",
            "commit": "",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "LINT-FIX"}, model="claude",
                )
        assert result["status"] == "complete"

    def test_commit_required_steps_shape_is_all_uppercase(self):
        """Regression (fix/round-18-dispatch-ssot, Bug A): every entry in
        _COMMIT_REQUIRED_STEPS must be uppercase. Pre-fix had "amend-sab"
        in lowercase which silently broke 5 consumer-site `if step in
        _COMMIT_REQUIRED_STEPS` checks (argparse type=str.upper at
        cli/fr_cmds.py:2529 always uppercases the comparison string).
        Pin the shape here so future contributors adding new step names
        see the convention and follow it."""
        from core.agent_spawner import _COMMIT_REQUIRED_STEPS
        for entry in _COMMIT_REQUIRED_STEPS:
            assert entry == entry.upper(), (
                f"_COMMIT_REQUIRED_STEPS entry {entry!r} must be uppercase; "
                f"all entries ({sorted(_COMMIT_REQUIRED_STEPS)}) should match "
                "the argparse type=str.upper convention."
            )
        assert "AMEND-SAB" in _COMMIT_REQUIRED_STEPS, (
            "AMEND-SAB must be a first-class member of _COMMIT_REQUIRED_STEPS "
            "so the post-dispatch dirty-tree guard and the inner-JSON "
            "commit-required check at agent_spawner.py:288 both fire."
        )

    def test_spawn_inner_json_complete_without_step_context_passes(self):
        """When context={'step': None}, commit-required check is skipped."""
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done",
            "session_id": "x",
            "status": "complete",
            "commit": "",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        assert result["status"] == "complete"


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

    def test_nonzero_returncode_records_error_class(self):
        """ERROR from an API/model failure is labelled INFRA_ERROR (observability):
        status stays ERROR (control flow unchanged) but error_class distinguishes
        an environment failure from a real agent-logic error in sessions_spawn.log."""
        spawner = self._spawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "API error: Connection closed by remote host"
        mock_proc.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", return_value=mock_proc):
                result = spawner.spawn(role="developer", prompt="Do task",
                                       context={}, model="claude")
        assert result["status"] == "ERROR"
        assert result["error_class"] == "INFRA_ERROR"

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


class TestClassifyDispatchError:
    """_classify_dispatch_error — infra (env/API/model/network) vs execution."""

    @pytest.mark.parametrize("output", [
        "Connection closed by remote host",
        "could not connect to api.anthropic.com",
        "API error 404: model 'MiniMax-M3' not found",
        "Authentication failed: invalid x-api-key",
        "HTTP 401 Unauthorized",
        "rate limit exceeded, retry later",
        "Overloaded (529)",
        "Your credit balance is too low",
        "claude.ai connector is disabled",
        "ECONNRESET",
    ])
    def test_infra_signatures(self, output):
        assert _classify_dispatch_error(output) == "INFRA_ERROR"

    @pytest.mark.parametrize("output", [
        "AssertionError: test_fr01 expected 3 got 4",
        "GATE1: FAIL — coverage 80% < 100%",
        "Traceback (most recent call last): KeyError 'x'",
        "internal error",
        "",
    ])
    def test_execution_defaults(self, output):
        assert _classify_dispatch_error(output) == "EXECUTION_ERROR"

    def test_connectors_banner_is_not_structural(self):
        """Round 12 站0c semantic flip: the connectors banner is startup
        noise, not deterministic breakage. Production evidence — 76/461
        entries on the 2026-07-16 P3 run carried the banner as their ONLY
        error output while spawns kept succeeding around them, and Fix
        H-G's own data (4/5 next-dispatches succeed) already contradicted
        the fatal-env theory. The banner must no longer classify
        STRUCTURAL; if it ever reaches the classifier raw (it is normally
        stripped by _extract_dispatch_error first), the `connector`
        substring in _INFRA_ERROR_RE classifies it INFRA_ERROR."""
        output = (
            "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY "
            "or another auth source is set and takes precedence over your "
            "claude.ai login"
        )
        assert is_structurally_broken(output) is False
        assert _classify_dispatch_error(output) == "INFRA_ERROR"

    def test_structural_mechanism_wins_over_infra(self, monkeypatch):
        """The registry-driven mechanism is intact (registry is just empty
        in production): a registered signature must classify STRUCTURAL
        even when _INFRA_ERROR_RE would also match the text."""
        import core.agent_spawner as mod
        monkeypatch.setattr(mod, "_STRUCTURAL_FAILURE_SIGNATURES",
                            ("SYNTHETIC_STRUCTURAL_BREAKAGE",))
        output = "SYNTHETIC_STRUCTURAL_BREAKAGE: connection refused (401)"
        assert is_structurally_broken(output) is True
        assert _classify_dispatch_error(output) == "STRUCTURAL"

    def test_structural_predicate_rejects_ordinary_failures(self):
        assert is_structurally_broken("HTTP 401 Unauthorized") is False
        assert is_structurally_broken("") is False


class TestStructuralSignatureSingleSource:
    """The 5.4h FR-04 stall (c1bacf4) was fixed by teaching ONE loop one
    signature; the signature then lived in cli/fr_cmds.py while the dispatch
    failure classifier lived here — two half-classifiers, so the next
    deterministic signature would need remembering in two places (Round 8
    station 4). The registry now lives only in _STRUCTURAL_FAILURE_SIGNATURES
    and fr_cmds delegates; these tests keep it that way."""

    def test_fr_cmds_delegates_instead_of_owning_a_signature_copy(self):
        # Read source from disk rather than inspect.getsource: the latter goes
        # through linecache, which was observed to race on the FIRST full-suite
        # run right after the 站4 fr_prompts split landed (fr_cmds newly does
        # `from cli.fr_prompts import ...`; inspect.getsource of the module hit
        # a cold-linecache, order-dependent transient that self-healed once
        # pycache was warm — 3 subsequent full runs + a pycache-cleared run all
        # green). A direct file read is deterministic and cannot order-depend
        # on which test warmed linecache first.
        import cli.fr_cmds as fr_cmds

        fr_source = Path(fr_cmds.__file__).read_text(encoding="utf-8")
        assert "_CONNECTOR_DISABLED_SIGNATURE" not in fr_source, (
            "cli/fr_cmds.py re-grew its own signature constant — the "
            "detection registry is core.agent_spawner._STRUCTURAL_FAILURE_SIGNATURES"
        )
        _idx = fr_source.find("def _is_connector_disabled_failure")
        assert _idx != -1, "_is_connector_disabled_failure missing from cli/fr_cmds.py"
        _body = fr_source[_idx:].split("\ndef ", 1)[0]
        assert "is_structurally_broken" in _body, (
            "_is_connector_disabled_failure must delegate to is_structurally_"
            "broken, not re-implement the structural check"
        )

    def test_signature_literal_defined_only_in_agent_spawner(self):
        """No production module may define its own copy of a structural
        signature as a string literal (human-facing diagnostics that merely
        *mention* the phrase live inside f-string prose in fr_cmds' FATAL
        message — those match here too, so the scan checks assignment-style
        single-quoted/double-quoted literal LINES that bind a constant)."""
        repo = Path(__file__).resolve().parent.parent
        offenders = []
        for base in ("cli", "core", "harness", "scripts", "detection"):
            for path in sorted((repo / base).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                rel = path.relative_to(repo).as_posix()
                if rel == "core/agent_spawner.py":
                    continue
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    stripped = line.strip()
                    if (
                        "claude.ai connectors are disabled" in stripped
                        and "=" in stripped.split("claude.ai")[0]
                    ):
                        offenders.append(f"{rel}:{lineno}")
        assert not offenders, (
            f"structural-failure signature bound outside core/agent_spawner.py: {offenders}"
        )


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

    # ── Fix H-F (2026-07-15): duration_seconds + retry_round in sessions_spawn.log ─

    def test_spawn_writes_duration_seconds_to_log(self, tmp_path, monkeypatch):
        """spawn() records wallclock duration around subprocess.run()."""
        # Point project_path so _log_dispatch persists to tmp_path/.methodology/.
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done", "session_id": "x",
            "status": "complete", "commit": "abcd",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        # sessions_spawn.log should now have one JSONL entry with
        # duration_seconds populated.
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        assert log_path.exists(), "sessions_spawn.log should be created"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(entries) >= 1
        spawn_entry = entries[-1]
        assert "duration_seconds" in spawn_entry
        assert isinstance(spawn_entry["duration_seconds"], (int, float))
        assert spawn_entry["duration_seconds"] >= 0

    def test_spawn_writes_retry_round_to_log(self, tmp_path):
        """spawn(retry_round=N) surfaces the iteration number in sessions_spawn.log."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done", "session_id": "x",
            "status": "complete", "commit": "abcd",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                    retry_round=3,
                )
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        assert spawn_entry["retry_round"] == 3

    def test_spawn_omits_optional_fields_when_not_provided(self, tmp_path):
        """Without duration_seconds/retry_round the log entry omits both
        (backward-compat — pre-Fix-H-F log parsers don't see new fields)."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done", "session_id": "x",
            "status": "complete", "commit": "abcd",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        assert "retry_round" not in spawn_entry
        # duration_seconds is always populated (we always measure) — but only
        # the explicitly-optional retry_round is what we omit-by-default here.

    # ── Round 14 站0: claude envelope cost/turns/usage in sessions_spawn.log ─

    def test_spawn_writes_envelope_metrics_to_log(self, tmp_path):
        """A full envelope (cost/turns/duration_api_ms/usage) surfaces as
        flat fields in the spawn log entry, keyed exactly as claude -p emits
        them (confirmed live 2026-07-17 against installed claude 2.1.206)."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done", "session_id": "x", "commit": "abcd",
            "total_cost_usd": 0.0123,
            "num_turns": 4,
            "duration_api_ms": 5678,
            "duration_ms": 9999,  # deliberately NOT captured (duplicates duration_seconds)
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "server_tool_use": {"web_search_requests": 0},  # not in our allowlist
            },
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        assert spawn_entry["total_cost_usd"] == 0.0123
        assert spawn_entry["num_turns"] == 4
        assert spawn_entry["duration_api_ms"] == 5678
        assert "duration_ms" not in spawn_entry
        assert spawn_entry["usage"] == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        }

    def test_spawn_omits_envelope_fields_when_absent(self, tmp_path):
        """An envelope missing cost/turns/usage entirely (older/incompatible
        claude CLI version) must not crash and must not pad the log entry
        with nulls."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": "done", "session_id": "x", "commit": "abcd",
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        for key in ("total_cost_usd", "num_turns", "duration_api_ms", "usage"):
            assert key not in spawn_entry

    def test_spawn_envelope_absent_when_failure_has_no_stdout(self, tmp_path):
        """A non-zero exit with EMPTY stdout carries no envelope — nothing to
        parse, so no cost/turns/usage fields.

        Round 19 站2 narrowed this test's claim. It used to say a non-zero exit
        "never produced an envelope ... regardless of what the (unused) stdout
        happens to contain", which described the code rather than the CLI: a
        failing run very often still writes a complete envelope, and treating
        stdout as unused is what made failed dispatches cost-invisible (2 of
        taskq's 19 failures had a cost figure, versus 50 of 50 successes). The
        empty-stdout case asserted here is still exactly right; the sibling
        test below covers the case this docstring used to deny.
        """
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "some real failure"
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        assert result["status"] == "ERROR"
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        for key in ("total_cost_usd", "num_turns", "duration_api_ms", "usage"):
            assert key not in spawn_entry

    def test_spawn_captures_envelope_when_a_failure_still_wrote_one(self, tmp_path):
        """Round 19 站2 — the shape taskq's P3 run actually produced.

        `claude -p` exited non-zero, yet stdout held a complete envelope: the
        run's own log records error_output "subtype=success API Error: Stream
        idle timeout - no chunks received", and BOTH of those substrings come
        out of _extract_dispatch_error json.loads()-ing this same stdout. The
        cost and token counts sat in that dict the whole time and were never
        read, so 12 identical stream-idle failures logged zero cost.

        The failure text must stay untouched — this is an observability field,
        not a reclassification.
        """
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "API Error: Stream idle timeout - no chunks received",
            "session_id": "abc123",
            "total_cost_usd": 0.42,
            "num_turns": 7,
            "duration_api_ms": 5100,
            "usage": {"input_tokens": 11, "output_tokens": 222},
        })
        mock_proc.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        assert result["status"] == "ERROR"
        assert "Stream idle timeout" in result["output"]
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        assert spawn_entry["total_cost_usd"] == 0.42
        assert spawn_entry["num_turns"] == 7
        assert spawn_entry["duration_api_ms"] == 5100
        assert spawn_entry["usage"]["output_tokens"] == 222

    def test_spawn_envelope_capture_never_masks_a_failure(self, tmp_path):
        """Malformed stdout on the failure path must degrade to "no metrics",
        never to a crash and never to an altered verdict. An observability
        field is not worth turning a dispatch failure into a harness crash."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = '{"truncated": "json"'  # unparseable
        mock_proc.stderr = "the real cause"
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3}, model="claude",
                )
        assert result["status"] == "ERROR"
        assert result["exit_code"] == 1
        assert "the real cause" in result["output"]
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        spawn_entry = [
            json.loads(line) for line in log_path.read_text().splitlines() if line.strip()
        ][-1]
        for key in ("total_cost_usd", "num_turns", "duration_api_ms", "usage"):
            assert key not in spawn_entry

    def test_spawn_envelope_captured_on_semantic_noop(self, tmp_path):
        """A semantic no-op (_validate_inner_json rejects it) still parsed a
        real envelope — the wasted turn incurred real cost, so it must still
        be captured (not just the final-success branch)."""
        spawner = AgentSpawner(project_path=tmp_path)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({
            "result": json.dumps({"status": "AWAITING_CONFIRMATION", "commit": ""}),
            "session_id": "x",
            "total_cost_usd": 0.005,
            "num_turns": 1,
            "duration_api_ms": 200,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"phase": 3, "step": "run-fr-step"}, model="claude",
                )
        assert result["status"] == "ERROR"
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        spawn_entry = entries[-1]
        assert spawn_entry["total_cost_usd"] == 0.005
        assert spawn_entry["usage"] == {"input_tokens": 10, "output_tokens": 2}


class TestExtractEnvelopeMetrics:
    """Unit tests for the module-level _extract_envelope_metrics helper —
    isolated from spawn()'s subprocess plumbing."""

    def test_extracts_present_top_level_keys(self):
        from core.agent_spawner import _extract_envelope_metrics
        metrics = _extract_envelope_metrics({
            "total_cost_usd": 1.5, "num_turns": 3, "duration_api_ms": 42,
            "result": "ignored", "session_id": "ignored",
        })
        assert metrics == {"total_cost_usd": 1.5, "num_turns": 3, "duration_api_ms": 42}

    def test_empty_dict_yields_empty_metrics(self):
        from core.agent_spawner import _extract_envelope_metrics
        assert _extract_envelope_metrics({}) == {}

    def test_usage_present_but_no_known_subkeys_omits_usage_entirely(self):
        from core.agent_spawner import _extract_envelope_metrics
        metrics = _extract_envelope_metrics({"usage": {"server_tool_use": {}}})
        assert "usage" not in metrics

    def test_usage_non_dict_is_ignored(self):
        from core.agent_spawner import _extract_envelope_metrics
        metrics = _extract_envelope_metrics({"usage": "not-a-dict"})
        assert "usage" not in metrics


class TestStructuralRetry:
    """Fix H-G (P3 2026-07-15 round 4): bounded retry for STRUCTURAL
    failures inside spawn() itself. Production sessions_spawn.log evidence
    (362 dispatches, 7 STRUCTURAL occurrences, 4/5 traceable same-FR next-
    dispatches succeeding) showed the pre-H-G "detect once, abort forever"
    reaction was based on a false premise — the signature is transient most
    of the time within the same run/env."""

    def _make_proc(self, returncode=0, stdout="", stderr=""):
        p = MagicMock()
        p.returncode = returncode
        p.stdout = stdout
        p.stderr = stderr
        return p

    # Round 12 站0c: the connectors banner was retired from the production
    # registry (it is startup noise — see TestClassifyDispatchError), so the
    # bounded-retry MECHANISM is driven by a synthetic signature injected
    # into the real registry via the _structural_signature fixture pattern.
    _SYNTHETIC_STDERR = "SYNTHETIC_STRUCTURAL_BREAKAGE: env permanently dead\n"

    def _register_synthetic_signature(self, monkeypatch):
        import core.agent_spawner as mod
        monkeypatch.setattr(mod, "_STRUCTURAL_FAILURE_SIGNATURES",
                            ("SYNTHETIC_STRUCTURAL_BREAKAGE",))

    def test_spawn_retries_structural_failure_and_recovers(self, tmp_path, monkeypatch):
        """2 STRUCTURAL failures followed by a success must still return
        complete — matches the FR-02 GATE1 production case (2026-07-15
        09:47-09:49) where the very next dispatch succeeded 87s later."""
        self._register_synthetic_signature(monkeypatch)
        spawner = AgentSpawner(project_path=tmp_path)
        claude_calls = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="")
            claude_calls[0] += 1
            if claude_calls[0] < 3:
                return self._make_proc(returncode=1, stderr=self._SYNTHETIC_STDERR)
            return self._make_proc(returncode=0, stdout=json.dumps({
                "result": json.dumps({"status": "complete", "commit": "abc123"}),
                "session_id": "x",
            }))

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                with patch("core.agent_spawner.time.sleep"):
                    result = spawner.spawn(
                        role="developer", prompt="Task",
                        context={"step": "TDD-RED"}, model="claude", fr_id="FR-03",
                    )
        assert result["status"] == "complete"
        assert claude_calls[0] == 3

    def test_spawn_exhausts_structural_retries_and_reports_error(self, tmp_path, monkeypatch):
        """All attempts STRUCTURAL → spawn() still returns ERROR/STRUCTURAL
        after exactly _STRUCTURAL_RETRY_ATTEMPTS tries — the 2026-07-12
        5.4h-stall protection (abort on a genuinely dead env) is preserved,
        just with a higher (bounded) confirmation threshold than before."""
        self._register_synthetic_signature(monkeypatch)
        spawner = AgentSpawner(project_path=tmp_path)
        claude_calls = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="")
            claude_calls[0] += 1
            return self._make_proc(returncode=1, stderr=self._SYNTHETIC_STDERR)

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                with patch("core.agent_spawner.time.sleep"):
                    result = spawner.spawn(
                        role="developer", prompt="Task",
                        context={"step": "TDD-RED"}, model="claude", fr_id="FR-05",
                    )
        assert result["status"] == "ERROR"
        assert result["error_class"] == "STRUCTURAL"
        assert claude_calls[0] == 3

    def test_spawn_does_not_retry_non_structural_error(self, tmp_path):
        """A plain (non-STRUCTURAL) dispatch error must NOT be retried at
        the transport layer — that class of failure is Fix H-H's job at the
        step-dispatch layer in fr_cmds.py, not AgentSpawner.spawn()'s."""
        spawner = AgentSpawner(project_path=tmp_path)
        claude_calls = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="")
            claude_calls[0] += 1
            return self._make_proc(returncode=1, stderr="some unrelated tool crash")

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                result = spawner.spawn(
                    role="developer", prompt="Task",
                    context={"step": "TDD-RED"}, model="claude", fr_id="FR-01",
                )
        assert result["status"] == "ERROR"
        assert result["error_class"] != "STRUCTURAL"
        assert claude_calls[0] == 1

    def test_spawn_logs_dispatch_attempt_per_retry(self, tmp_path, monkeypatch):
        """Each attempt in the retry loop writes its own sessions_spawn.log
        entry (dispatch_attempt=1, 2, 3, ...) so operators can see the retry
        sequence instead of one entry silently overwriting the timeline."""
        self._register_synthetic_signature(monkeypatch)
        spawner = AgentSpawner(project_path=tmp_path)
        claude_calls = [0]

        def side_effect(cmd, **kw):
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="")
            claude_calls[0] += 1
            if claude_calls[0] < 2:
                return self._make_proc(returncode=1, stderr=self._SYNTHETIC_STDERR)
            return self._make_proc(returncode=0, stdout=json.dumps({
                "result": json.dumps({"status": "complete", "commit": "abc123"}),
                "session_id": "x",
            }))

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("core.agent_spawner.subprocess.run", side_effect=side_effect):
                with patch("core.agent_spawner.time.sleep"):
                    spawner.spawn(
                        role="developer", prompt="Task",
                        context={"step": "TDD-RED"}, model="claude", fr_id="FR-03",
                    )
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        # attempt 1 (STRUCTURAL failure) + attempt 2 (success) each get their
        # own log line, tagged with which attempt produced them.
        assert [e.get("dispatch_attempt") for e in entries] == [1, 2]
        assert entries[0]["status"] == "ERROR"
        assert entries[1]["status"] == "complete"


_BANNER = (
    "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or "
    "another auth source is set and takes precedence over your claude.ai "
    "login · Unset it to load your organization's connectors"
)


class TestDenoiseAndExtract:
    """Round 12 站0c: error-capture denoise. On the 2026-07-16 P3 run,
    76/461 sessions_spawn.log entries had error_output consisting of ONLY
    the CLI startup banner — the real failure cause was invisible because
    `stderr or stdout` put the banner (always first on stderr) into the
    truncated log field."""

    def test_denoise_strips_banner_keeps_real_error(self):
        stderr = _BANNER + "\nTraceback: something real broke\n"
        cleaned = _denoise_cli_stderr(stderr)
        assert "connectors are disabled" not in cleaned
        assert "Traceback: something real broke" in cleaned

    def test_denoise_banner_only_becomes_empty(self):
        assert _denoise_cli_stderr(_BANNER + "\n") == ""

    def test_denoise_strips_stdin_warning(self):
        stderr = ("Warning: no stdin data received in 3s, proceeding without it\n"
                  "actual error here\n")
        assert _denoise_cli_stderr(stderr) == "actual error here"

    def test_extract_prefers_result_json_error(self):
        stdout = json.dumps({
            "type": "result", "subtype": "error_max_turns", "is_error": True,
            "result": "Agent exceeded max turns while running pytest",
        })
        extracted = _extract_dispatch_error(stdout, _BANNER)
        assert "error_max_turns" in extracted
        assert "exceeded max turns" in extracted
        assert "connectors are disabled" not in extracted

    def test_extract_falls_back_to_denoised_stderr(self):
        stderr = _BANNER + "\nENOENT: claude binary corrupted\n"
        extracted = _extract_dispatch_error("not json {", stderr)
        assert extracted == "ENOENT: claude binary corrupted"

    def test_extract_banner_only_falls_through_to_stdout(self):
        """Banner-only stderr must not shadow a non-JSON stdout payload."""
        extracted = _extract_dispatch_error("plain stdout failure text", _BANNER)
        assert extracted == "plain stdout failure text"

    def test_extract_never_empty_when_output_exists(self):
        """Worst case (banner-only stderr, empty stdout): raw stderr is
        still returned so the log entry is not blank."""
        extracted = _extract_dispatch_error("", _BANNER)
        assert extracted  # non-empty
        assert "connectors" in extracted


class TestUnattendedPreamble:
    """Round 12 站0d: every spawn appends the unattended-execution override
    at the system-prompt layer. Live probe matrix (2026-07-16) showed
    --setting-sources 'project' ALSO loads the user's global CLAUDE.md,
    whose interactive rules stalled headless agents awaiting confirmation."""

    def test_spawn_cmd_carries_append_system_prompt(self):
        spawner = AgentSpawner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "ok", "session_id": "x"})
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                spawner.spawn(role="developer", prompt="Task", context={},
                              model="claude")
                cmd = mock_run.call_args[0][0]
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == _UNATTENDED_PREAMBLE

    def test_preamble_neutralises_wait_for_confirmation_rules(self):
        assert "wait for user confirmation" in _UNATTENDED_PREAMBLE
        assert "never stop to wait for approval" in _UNATTENDED_PREAMBLE


class TestPreflightSubstrate:
    """Round 12 站0b: the substrate probe. One 90s spawn proving pytest/git
    execute in the spawned environment, instead of a per-FR loop burning
    140 dispatches discovering they don't (2026-07-16 FR-01)."""

    def _spawn_result(self, output, status="complete"):
        return {"output": output, "status": status, "session_id": "p",
                "commit": ""}

    _GOOD_OUTPUT = (
        "pytest 8.4.2\n"
        "On branch main\nnothing to commit, working tree clean\n"
        "PREFLIGHT_SUBSTRATE_CANARY_OK\n"
    )

    def test_probe_ok_on_full_marker_set(self, tmp_path):
        spawner = AgentSpawner(project_path=tmp_path)
        with patch.object(AgentSpawner, "spawn",
                          return_value=self._spawn_result(self._GOOD_OUTPUT)) as mock_spawn:
            probe = spawner.preflight_substrate(permission_mode="bypassPermissions")
        assert probe["ok"] is True
        assert probe["pytest_ok"] and probe["git_ok"] and probe["canary_ok"]
        # probe must be spawned with the pipeline's own substrate params
        assert mock_spawn.call_args.kwargs["permission_mode"] == "bypassPermissions"
        assert mock_spawn.call_args.kwargs["persona_override"] == ""

    def test_probe_fails_when_canary_missing(self, tmp_path):
        """An agent that merely echoes the prompt back cannot produce the
        joined canary marker (prompt only contains the two halves)."""
        spawner = AgentSpawner(project_path=tmp_path)
        output = "pytest 8.4.2\nOn branch main\n(I would run the canary but...)"
        with patch.object(AgentSpawner, "spawn",
                          return_value=self._spawn_result(output)):
            probe = spawner.preflight_substrate()
        assert probe["ok"] is False
        assert probe["canary_ok"] is False

    def test_probe_fails_on_permission_wall(self, tmp_path):
        """The production failure shape: agent replies that tools are
        blocked — no marker can be present."""
        spawner = AgentSpawner(project_path=tmp_path)
        output = "Sandbox blocked shell execution; pytest/ruff require approval"
        with patch.object(AgentSpawner, "spawn",
                          return_value=self._spawn_result(output)):
            probe = spawner.preflight_substrate()
        assert probe["ok"] is False
        assert probe["pytest_ok"] is False
        assert probe["git_ok"] is False

    def test_probe_fails_on_spawn_timeout(self, tmp_path):
        spawner = AgentSpawner(project_path=tmp_path)
        with patch.object(AgentSpawner, "spawn",
                          return_value={"output": "Agent timed out after 90s",
                                        "status": "TIMEOUT"}):
            probe = spawner.preflight_substrate()
        assert probe["ok"] is False
        assert probe["status"] == "TIMEOUT"

    def test_probe_writes_preflight_log_entry(self, tmp_path):
        spawner = AgentSpawner(project_path=tmp_path)
        with patch.object(AgentSpawner, "spawn",
                          return_value=self._spawn_result(self._GOOD_OUTPUT)):
            spawner.preflight_substrate()
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [json.loads(line) for line in log_path.read_text().splitlines()
                   if line.strip()]
        assert entries[-1]["status"] == "PREFLIGHT_OK"
        assert entries[-1]["role"] == "preflight-probe"

    def test_probe_prompt_never_contains_joined_canary(self, tmp_path):
        """Anti-echo property: the joined marker must not appear in the
        prompt itself, or a prompt-echoing agent would pass the check."""
        spawner = AgentSpawner(project_path=tmp_path)
        captured: dict = {}

        def fake_spawn(**kwargs):
            captured["prompt"] = kwargs["prompt"]
            return self._spawn_result("")

        with patch.object(AgentSpawner, "spawn", side_effect=fake_spawn):
            spawner.preflight_substrate()
        assert "PREFLIGHT_SUBSTRATE_CANARY_OK" not in captured["prompt"]
        assert "PREFLIGHT_SUBSTRATE_" in captured["prompt"]
