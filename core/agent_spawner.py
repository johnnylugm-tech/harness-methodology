"""
Agent Spawner: Orchestrates agent invocations for Developer and Reviewer roles.

Handles routing between Claude Code's Task tool and Hermes MCP for heterogeneous
reviewing, adhering to the 'Need-to-know' principle for prompt construction.
"""

from __future__ import annotations

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

    - Developer/Primary agents -> Claude Code Task tool.
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
        phase: int = 0,
        fr_id: str | None = None,
    ) -> dict:
        """
        Spawn an agent with a specific role and prompt.

        Args:
            role: The agent's persona role (e.g., 'developer', 'reviewer').
            prompt: The specific task description.
            context: Additional metadata and state.
            model: Preferred backend ('claude' or 'hermes').
            task_timeout: Max execution time in seconds.
            phase: Current methodology phase.
            fr_id: Optional Functional Requirement ID.

        Returns:
            A dictionary containing the agent's output and status.
        """
        full_prompt = self._build_prompt(role, prompt, context, phase)

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
            # effective == "claude" for P7/P8 — fall through to Task tool

        # Claude Code Task tool (replaces OpenClaw sessions_spawn)
        try:
            from claude_code_sdk import Task  # type: ignore[import]
            result = Task(
                description=f"{role}: {prompt[:80]}",
                prompt=full_prompt,
                timeout=task_timeout,
            )
        except ImportError:
            raise RuntimeError(
                "claude_code_sdk not available. "
                "Ensure running inside Claude Code environment."
            )

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

    def _build_prompt(self, role: str, prompt: str, context: dict, phase: int) -> str:
        """Construct the prompt following the need-to-know principle."""
        persona = _load_persona(role)
        sop = _load_phase_sop(context.get("phase", phase))
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
