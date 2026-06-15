"""
Agent Spawner: Orchestrates agent invocations for Developer and Reviewer roles.

Handles routing between Claude Code headless CLI (claude -p) and Hermes MCP for heterogeneous
reviewing, adhering to the 'Need-to-know' principle for prompt construction.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Optional

from pathlib import Path


# Env markers that tell a nested `claude -p` to fetch OAuth tokens from the
# parent Agent-SDK stream. A headless child has no such stream, so auth fails
# with "SDK getOAuthToken callback failed: Stream closed" → API 401. Always
# strip them; the child must authenticate on its own (keychain OAuth/API key).
_SDK_STREAM_MARKERS = (
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
)


def _child_env() -> dict[str, str]:
    """os.environ copy safe to pass to a spawned `claude -p` subprocess."""
    env = os.environ.copy()
    for key in _SDK_STREAM_MARKERS:
        env.pop(key, None)
    return env


def _load_persona(role: str) -> str:
    """Load the persona markdown file for a given role."""
    p = Path("agent_personas") / f"{role.upper()}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_phase_sop(phase: int) -> str:
    """Load the Standard Operating Procedure (SOP) for a specific phase."""
    p = Path("docs") / f"P{phase}_SOP.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class AgentSpawner:
    """
    Spawns developer and reviewer agents via the Claude Code headless CLI.

    All agents use Claude sub-agent for review — no MCP backend configuration required.
    Only the claude CLI must be installed.
    """

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = Path(project_path) if project_path else None

    def spawn(
        self,
        role: str,
        prompt: str,
        context: dict,
        model: str = "claude",
        task_timeout: int = 300,
        max_turns: int = 20,
        phase: int = 0,
        fr_id: str | None = None,
        phase_sop_override: str | None = None,
        persona_override: str | None = None,
        mcp_config: str | None = None,
        setting_sources: str = "",
        permission_mode: str = "acceptEdits",
    ) -> dict:
        """
        Spawn an agent with a specific role and prompt.

        Args:
            role: The agent's persona role (e.g., 'developer', 'reviewer').
            prompt: The specific task description.
            context: Additional metadata and state.
            model: Backend model (always 'claude' — only Claude CLI supported).
            task_timeout: Max execution time in seconds.
            max_turns: Max tool-using turns (default 20).
            phase: Current methodology phase.
            fr_id: Optional Functional Requirement ID.
            mcp_config: Path to .mcp.json (relative to project_path), inline
                JSON string, or None. None = empty MCP (current default).
                File not found → stderr warning + fallback to empty MCP.
            setting_sources: Passed to --setting-sources. "" (default) blocks
                all CLAUDE.md. "project" loads project-level CLAUDE.md only.
            permission_mode: Claude Code --permission-mode. "acceptEdits"
                (default) auto-approves file edits, blocks Bash. "bypassPermissions"
                auto-approves all tools including shell commands.

        Returns:
            A dictionary containing the agent's output and status.
        """
        full_prompt = self._build_prompt(role, prompt, context, phase,
                                         phase_sop_override=phase_sop_override,
                                         persona_override=persona_override)

        # Claude Code headless CLI (replaces deprecated claude_code_sdk.Task).
        # Sub-agent isolation (need-to-know): by default, blocks CLAUDE.md,
        # hooks, skills, and MCP. Callers can opt into MCP tools and
        # project-level CLAUDE.md via mcp_config / setting_sources params.
        # OAuth auth works (unlike --bare which forces API key).
        # The spawned agent sees what _build_prompt() packs into the prompt
        # (persona + SOP + task + context) plus any MCP tools granted.
        cli = shutil.which("claude")
        if not cli:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code: "
                "https://code.claude.com/docs/en/installation"
            )
        # Resolve MCP config
        if mcp_config is None:
            mcp_args = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        elif mcp_config.strip().startswith("{"):
            mcp_args = ["--strict-mcp-config", "--mcp-config", mcp_config]
        else:
            resolved = (
                str((self.project_path / mcp_config).resolve())
                if self.project_path else mcp_config
            )
            if not Path(resolved).exists():
                import sys
                print(
                    f"[AgentSpawner] WARNING: MCP config '{resolved}'"
                    f" not found — falling back to isolated mode",
                    file=sys.stderr,
                )
                mcp_args = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            else:
                mcp_args = ["--strict-mcp-config", "--mcp-config", resolved]
        cmd = [
            cli, "-p", full_prompt,
            "--output-format", "json",
            "--setting-sources", setting_sources,
            "--disable-slash-commands",
            *mcp_args,
            "--max-turns", str(max_turns),
            "--permission-mode", permission_mode,
            "--no-session-persistence",
        ]

        # Sub-agent regression guard: snapshot pre-spawn diff so we can
        # compute (post - pre) net changes after the agent finishes.
        # Capture HEAD SHA now so post-spawn diff is measured against the
        # same fixed point even if the agent commits (git diff HEAD would
        # move with the agent's commits, making them invisible).
        pre_sha = self._git_head_sha(self.project_path)
        pre_diff = self._git_diff_numstat(self.project_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=task_timeout,
                cwd=str(self.project_path.resolve()) if self.project_path else None,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired:
            return {
                "output": f"Agent timed out after {task_timeout}s",
                "status": "TIMEOUT",
            }
        if proc.returncode != 0:
            return {
                "output": proc.stderr or proc.stdout,
                "status": "ERROR",
                "exit_code": proc.returncode,
            }
        try:
            data = json.loads(proc.stdout)
            result = {
                "output": data.get("result", ""),
                "status": "complete",
                "session_id": data.get("session_id", ""),
            }
        except (json.JSONDecodeError, AttributeError):
            import sys
            sys.stderr.write(
                f"[AgentSpawner] claude -p returned non-JSON stdout "
                f"(stderr={proc.stderr[:200]!r})\n"
            )
            result = {
                "output": proc.stdout,
                "status": "complete",
            }

        parsed = self._parse_result(result)
        # Sub-agent regression guard: capture post-spawn diff and emit
        # regression flags if the agent made suspicious destructive edits.
        # Use pre_sha as base so the diff covers both committed and
        # uncommitted changes the agent made (HEAD may have moved).
        post_diff = self._git_diff_numstat(self.project_path, base=pre_sha or "HEAD")
        regression_flags = self._dispatch_diff_budget(pre_diff, post_diff, pre_sha=pre_sha)
        self._log_dispatch(
            role, prompt, parsed, phase, fr_id,
            regression_flags=regression_flags,
        )
        # If the guard fired, surface in the parsed result so the caller
        # can treat it as ERROR/REJECT status (Bug #28/#32/#38/#39 pattern).
        if regression_flags and parsed.get("status") == "complete":
            parsed = {**parsed, "status": "REGRESSION_GUARD", "regression_flags": regression_flags}
        return parsed

    def _log_dispatch(self, role: str, task: str, result: dict,
                      phase: int, fr_id: str | None,
                      regression_flags: dict | None = None) -> None:
        """Auto-record agent dispatch to .methodology/sessions_spawn.log as a
        non-blocking debug trail. (The HR-10 entry-count audit that consumed this
        log was removed — it was agent-writable / not tamper-evident. This stays as
        a dispatch trace for debugging; nothing gates on it.)"""
        if not self.project_path:
            return
        try:
            from core.sessions_spawn_logger import SessionsSpawnLogger
            logger = SessionsSpawnLogger(self.project_path)
            session_id = result.get("session_id", "")
            logger.log_spawn(
                role=role, task=task[:200], session_id=session_id,
                status=result.get("status", "SPAWNED"),
                phase=phase, fr_id=fr_id,
                regression_flags=regression_flags or {},
            )
        except Exception as e:
            import sys
            sys.stderr.write(f"[AgentSpawner] log_dispatch failed: {e}\n")

    def _build_prompt(self, role: str, prompt: str, context: dict, phase: int,
                      phase_sop_override: str | None = None,
                      persona_override: str | None = None) -> str:
        """Construct the prompt following the need-to-know principle.

        Args:
            phase_sop_override: If None, load full phase SOP from docs/P{phase}_SOP.md.
                If provided (including ""), use this string instead — "" skips SOP entirely
                (used by run-fr-step where context is already self-contained in the prompt).
            persona_override: If None, load persona from agent_personas/{ROLE}.md.
                If provided (including ""), use this string instead — "" skips persona entirely
                (used for STATELESS Agent B reviewer dispatches that must return JSON directly).
        """
        persona = _load_persona(role) if persona_override is None else persona_override
        if phase_sop_override is None:
            sop = _load_phase_sop(context.get("phase", phase))
        else:
            sop = phase_sop_override  # "" → no SOP section added
        parts = []
        if persona:
            parts.append(f"[PERSONA]\n{persona}")
        if sop:
            parts.append(f"[SOP]\n{sop}")
        parts.append(f"[TASK]\n{prompt}")
        ctx_str = "\n".join(
            f"  {k}: {v}" for k, v in context.items() if k != "phase"
        )
        if ctx_str:
            parts.append(f"[CONTEXT]\n{ctx_str}")
        return "\n\n".join(parts)

    def _parse_result(self, result: Any) -> dict:
        """Parse the raw agent result into a standard format."""
        if isinstance(result, dict):
            return result
        return {"output": str(result), "status": "complete"}

    # Sub-agent regression guard (B1 follow-up to Bug #28/#32/#38/#39).
    # TDD-IMPROVE sub-agents made 4 distinct destructive edits in the
    # integration-test E2E (set enum values to None, change sys.exit
    # codes, inject XX...XX markers). The same sub-agent also rewrote
    # tests to match the broken implementation, so Agent B review didn't
    # catch it. To prevent recurrence we capture pre/post git diff
    # statistics on every dispatch and surface them in sessions_spawn.log.
    # A future post-dispatch budget check (Tier 2.1 in
    # HARNESS_IMPROVEMENT_PLAN.md) consumes these fields to fire
    # REGRESSION_GUARD status on suspicious diffs.
    @staticmethod
    def _git_head_sha(repo_root: Path | None) -> Optional[str]:
        """Return the current HEAD SHA, or None on failure."""
        if repo_root is None:
            return None
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_root),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    @staticmethod
    def _git_diff_numstat(
        repo_root: Path | None, base: str = "HEAD"
    ) -> dict[str, tuple[int, int]]:
        """Snapshot of `git diff --numstat <base>` at this moment.

        Returns a dict mapping "<filename>" -> (lines_added, lines_removed)
        for every file that differs from *base* in the working tree (and
        any commits made after *base*). Empty dict on failure — callers
        must handle empty as "no information".

        Pass a SHA captured before a sub-agent runs as *base* so that
        commits made by the agent are included in the post-spawn snapshot
        (``git diff HEAD`` would track the moving HEAD and miss them).
        """
        if repo_root is None:
            return {}
        try:
            r = subprocess.run(
                ["git", "diff", "--numstat", base],
                capture_output=True, text=True, timeout=15,
                cwd=str(repo_root),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if r.returncode != 0:
            return {}
        out: dict[str, tuple[int, int]] = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added_s, removed_s, path = parts
            try:
                # `--numstat` outputs `-` `-` for binary files.
                added = -1 if added_s == "-" else int(added_s)
                removed = -1 if removed_s == "-" else int(removed_s)
            except ValueError:
                continue
            out[path] = (added, removed)
        return out

    def _dispatch_diff_budget(
        self, pre: dict, post: dict, pre_sha: Optional[str] = None
    ) -> dict:
        """Compute regression flags from pre/post dispatch diff snapshots.

        *pre_sha* is the HEAD SHA captured before the sub-agent ran.
        Pass it so the XX-marker check uses the same fixed base ref as
        *post* (committed XX markers are only visible via
        ``git diff <pre_sha>``, not ``git diff HEAD``).

        Returns a dict suitable for inclusion in sessions_spawn.log entry
        as the `regression_flags` field. Flags are warnings only; the
        authoritative gate is the explicit check in cmd_run_fr_step.
        """
        flags: dict[str, object] = {}
        # Compute net change per file: (added, removed) under post
        # MINUS (added, removed) under pre. Then classify.
        suspect_lines_removed: list[tuple[str, int]] = []
        suspect_xx_markers: list[str] = []
        diff_base = pre_sha or "HEAD"
        all_paths = set(pre) | set(post)
        for path in all_paths:
            pre_a, pre_r = pre.get(path, (0, 0))
            post_a, post_r = post.get(path, (0, 0))
            net_removed = (post_r - pre_r)
            net_added = (post_a - pre_a)
            # Lines-removed > 30% of pre-existing line count → suspicious.
            # We don't have line counts without re-reading; use a softer
            # absolute threshold: 50+ lines removed in a single file in
            # a 30-min TDD step is almost certainly a destructive edit.
            if net_removed > 50:
                suspect_lines_removed.append((path, net_removed))
            # Look for XX...XX mutator markers left in source — this is
            # the exact pattern TDD-IMPROVE introduced in Bug #39.
            # Use diff_base (= pre_sha when available) so markers in
            # committed changes are also scanned.
            if path.endswith(".py") and net_added > 0:
                try:
                    r = subprocess.run(
                        ["git", "diff", diff_base, "--", path],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(self.project_path.resolve()) if self.project_path else ".",
                    )
                    if re.search(r"^\+.*XX[a-zA-Z_]+XX", r.stdout, re.MULTILINE):
                        suspect_xx_markers.append(path)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if suspect_lines_removed:
            flags["lines_removed>50"] = suspect_lines_removed[:5]
        if suspect_xx_markers:
            flags["xx_markers_introduced"] = suspect_xx_markers
        return flags
