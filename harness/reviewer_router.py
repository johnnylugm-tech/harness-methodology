# harness/reviewer_router.py
# Gap G2: Heterogeneous Reviewer via Hermes MCP.
# Rewritten from adapters/wave2_features.py LLMCascadeWrapper.
# v1.3: Hermes MCP is the sole reviewer path. Gemini exits reviewer chain.
from __future__ import annotations
import json
import os
import re

try:
    from mcp_tools import (
        mcp__hermes__messages_send,
        mcp__hermes__events_wait,
        mcp__hermes__messages_read,
    )
    _HERMES_AVAILABLE = True
except ImportError:
    _HERMES_AVAILABLE = False

HERMES_TARGET = os.environ.get("HERMES_REVIEWER_TARGET", "")
HERMES_TIMEOUT_MS = int(os.environ.get("HERMES_TIMEOUT_MS", "120000"))
_CLAUDE_PHASES = {7, 8}   # Risk Assessment + Config Mgmt stay on Claude

REVIEWER_POLICY = {
    "default": "hermes",
    "p7_risk": "claude",
    "p8_config": "claude",
}


def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    return "claude" if phase in _CLAUDE_PHASES else REVIEWER_POLICY.get(role, "hermes")


class ReviewerRouter:
    """Routes review requests to heterogeneous backend via Hermes MCP.
    Backend LLM configured on Hermes side — decoupled from framework code.
    """

    def __init__(self, target: str = HERMES_TARGET):
        if not target:
            raise ValueError(
                "HERMES_REVIEWER_TARGET not set. "
                "e.g. export HERMES_REVIEWER_TARGET=telegram:6308981865"
            )
        self.target = target

    def review(self, role: str, prompt: str, phase: int, fr_id: str | None = None) -> dict:
        """Send → long-poll wait → read. Returns parsed JSON response."""
        if not _HERMES_AVAILABLE:
            raise RuntimeError("Hermes MCP tools not available in this Claude Code session.")

        full_prompt = self._build_prompt(role, prompt, phase, fr_id)
        mcp__hermes__messages_send(target=self.target, message=full_prompt)
        mcp__hermes__events_wait(session_key=self.target, timeout_ms=HERMES_TIMEOUT_MS)
        msgs = mcp__hermes__messages_read(session_key=self.target, limit=1)
        raw = msgs[-1]["content"] if msgs else ""
        return self._parse_response(raw)

    def _build_prompt(self, role: str, prompt: str, phase: int, fr_id: str | None) -> str:
        fr_tag = f" | FR {fr_id}" if fr_id else ""
        header = f"[Harness Reviewer | Phase {phase}{fr_tag}]\nRole: {role}\n\n"
        footer = (
            '\n\nOutput JSON: {"review_status": "APPROVE|REJECT", '
            '"confidence": 0-1, "violations": [], "summary": ""}'
        )
        return header + prompt + footer

    def _parse_response(self, raw: str) -> dict:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"review_status": "REJECT", "confidence": 0.0,
                "violations": ["parse_error"], "summary": raw[:200]}
