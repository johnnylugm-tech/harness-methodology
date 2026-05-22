"""
Agent Spawner: Orchestrates agent invocations for Developer and Reviewer roles.

Handles routing between Claude Code headless CLI (claude -p) and Hermes MCP for heterogeneous
reviewing, adhering to the 'Need-to-know' principle for prompt construction.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Optional

from pathlib import Path


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
    Routes agent invocations to heterogeneous backends.

    - Developer/Primary agents -> Claude Code headless CLI (claude -p).
    - Reviewer agents -> ReviewerRouter (Hermes MCP).

    Phase routing policy (via get_reviewer_model):
    - Phases 7, 8 -> Claude (for Risk Assessment + Config Mgmt).
    - All others  -> Hermes (default).
    """

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = Path(project_path) if project_path else None
        self._reviewer = None  # lazy-init: avoids crash when HERMES env not set

    def _get_reviewer(self):
        """Lazy-initialize the ReviewerRouter."""
        if self._reviewer is None:
            from harness.reviewer_router import ReviewerRouter
            self._reviewer = ReviewerRouter(project_path=self.project_path)
        return self._reviewer

    def spawn(
        self,
        role: str,
        prompt: str,
        context: dict,
        model: str = "claude",      # "claude" | "hermes"
        task_timeout: int = 300,
        max_turns: int = 20,
        phase: int = 0,
        fr_id: str | None = None,
        phase_sop_override: str | None = None,
    ) -> dict:
        """
        Spawn an agent with a specific role and prompt.

        Args:
            role: The agent's persona role (e.g., 'developer', 'reviewer').
            prompt: The specific task description.
            context: Additional metadata and state.
            model: Preferred backend ('claude' or 'hermes').
            task_timeout: Max execution time in seconds.
            max_turns: Max tool-using turns (default 10).
            phase: Current methodology phase.
            fr_id: Optional Functional Requirement ID.

        Returns:
            A dictionary containing the agent's output and status.
        """
        full_prompt = self._build_prompt(role, prompt, context, phase,
                                         phase_sop_override=phase_sop_override)

        if model == "hermes":
            # Honor phase-level routing policy: P7/P8 stay on Claude
            from harness.reviewer_router import get_reviewer_model
            effective = get_reviewer_model(phase, role)
            if effective == "hermes":
                result = self._get_reviewer().review(
                    role=role, prompt=full_prompt, phase=phase, fr_id=fr_id,
                )
                parsed = self._parse_result(result)
                # Surface degradation metadata to callers (for audit trail)
                if result.get("_degraded"):
                    parsed["_degraded"] = True
                    parsed["_reviewer_used"] = result.get("_reviewer_used", "unknown")
                    parsed["_degradation_note"] = result.get("_degradation_note")
                self._log_dispatch(role, prompt, parsed, phase, fr_id)
                return parsed
            # effective == "claude" for P7/P8 — fall through to Claude headless CLI

        # Claude Code headless CLI (replaces deprecated claude_code_sdk.Task).
        # Isolation flags replace --bare: --setting-sources "" blocks
        # CLAUDE.md + hooks; --disable-slash-commands blocks skills;
        # --strict-mcp-config --mcp-config '{}' blocks MCP.
        # OAuth auth works (unlike --bare which forces API key).
        # The spawned agent only sees what _build_prompt() packs into the
        # prompt (persona + SOP + task + context).
        cli = shutil.which("claude")
        if not cli:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code: "
                "https://code.claude.com/docs/en/installation"
            )
        cmd = [
            cli, "-p", full_prompt,
            "--output-format", "json",
            "--setting-sources", "",
            "--disable-slash-commands",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--max-turns", str(max_turns),
            "--permission-mode", "acceptEdits",
            "--no-session-persistence",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=task_timeout,
                cwd=str(self.project_path.resolve()) if self.project_path else None,
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
        self._log_dispatch(role, prompt, parsed, phase, fr_id)
        return parsed

    def _log_dispatch(self, role: str, task: str, result: dict,
                      phase: int, fr_id: str | None) -> None:
        """Auto-record agent dispatch to sessions_spawn.log (HR-10)."""
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
            )
        except Exception as e:
            import sys
            sys.stderr.write(f"[AgentSpawner] log_dispatch failed: {e}\n")

    def _build_prompt(self, role: str, prompt: str, context: dict, phase: int,
                      phase_sop_override: str | None = None) -> str:
        """Construct the prompt following the need-to-know principle.

        Args:
            phase_sop_override: If None, load full phase SOP from docs/P{phase}_SOP.md.
                If provided (including ""), use this string instead — "" skips SOP entirely
                (used by run-fr-step where context is already self-contained in the prompt).
        """
        persona = _load_persona(role)
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
