# core/agent_spawner.py
# Rewritten for Claude Code: sessions_spawn -> Task tool + Hermes MCP reviewer.
# Gap G2: model="hermes" routes to ReviewerRouter (heterogeneous backend).
# Need-to-know principle: persona + current phase SOP + task only.
# Fix ③: P7/P8 phases auto-route to Claude per REVIEWER_POLICY (get_reviewer_model).
from __future__ import annotations
from pathlib import Path


def _load_persona(role: str) -> str:
    p = Path("agent_personas") / f"{role.upper()}.md"
    return p.read_text() if p.exists() else ""


def _load_phase_sop(phase: int) -> str:
    p = Path("docs") / f"P{phase}_SOP.md"
    return p.read_text() if p.exists() else ""


class AgentSpawner:
    """
    Routes agent invocations to:
    - Task tool (Claude Code) for Developer/primary agents
    - ReviewerRouter (Hermes MCP) for Reviewer agents (Gap G2)

    Phase routing policy (via get_reviewer_model):
    - Phases 7, 8 -> Claude  (Risk Assessment + Config Mgmt)
    - All others  -> Hermes  (default)
    """

    _reviewer = None   # lazy-init: avoids crash when HERMES env not set

    def _get_reviewer(self):
        if self._reviewer is None:
            from harness.reviewer_router import ReviewerRouter
            self._reviewer = ReviewerRouter()
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
        full_prompt = self._build_prompt(role, prompt, context, phase)

        if model == "hermes":
            # Honor phase-level routing policy: P7/P8 stay on Claude
            from harness.reviewer_router import get_reviewer_model
            effective = get_reviewer_model(phase, role)
            if effective == "hermes":
                result = self._get_reviewer().review(
                    role=role, prompt=full_prompt, phase=phase, fr_id=fr_id,
                )
                return self._parse_result(result)
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

        return self._parse_result(result)

    def _build_prompt(self, role: str, prompt: str, context: dict, phase: int) -> str:
        """Need-to-know: load only persona + current phase SOP + task."""
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

    def _parse_result(self, result) -> dict:
        if isinstance(result, dict):
            return result
        return {"output": str(result), "status": "complete"}
