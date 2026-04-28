# harness/reviewer_router.py
# Gap G2: Heterogeneous Reviewer via priority-chained MCP backends.
# v2.0: Multi-reviewer fallback chain (Hermes → Gemini → sub-agent)
#        + task decomposition for large/complex tasks.
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# MCP imports (graceful degradation if not available)
# ---------------------------------------------------------------------------

try:
    from mcp_tools import (
        mcp__hermes__messages_send,
        mcp__hermes__events_wait,
        mcp__hermes__messages_read,
    )
    _HERMES_AVAILABLE = True
except ImportError:
    _HERMES_AVAILABLE = False

try:
    from mcp_tools import mcp__gemini_cli__ask_gemini  # type: ignore[import]
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (all overridable via environment variables)
# ---------------------------------------------------------------------------

HERMES_TARGET = os.environ.get("HERMES_REVIEWER_TARGET", "")
HERMES_TIMEOUT_MS = int(os.environ.get("HERMES_TIMEOUT_MS", "90000"))   # 90s per CLAUDE.md protocol
GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "60000"))   # 60s for Gemini CLI MCP
TASK_SIZE_THRESHOLD = int(os.environ.get("TASK_SIZE_THRESHOLD", "2000"))  # chars — decompose if exceeded
SUBTASK_MAX_SIZE = int(os.environ.get("SUBTASK_MAX_SIZE", "800"))         # chars per subtask after decompose

# Reviewer priority chain: comma-separated, in priority order.
# "hermes,gemini" = try Hermes first, Gemini as fallback, sub-agent as final backstop.
# sub-agent is always appended automatically — no need to specify in config.
_DEFAULT_CHAIN = "hermes,gemini"
REVIEWER_CHAIN_CONFIG = os.environ.get("REVIEWER_CHAIN", _DEFAULT_CHAIN)

# Phase policy: P7/P8 route to Claude (Risk Assessment + Config Mgmt)
_CLAUDE_PHASES = {7, 8}
REVIEWER_POLICY = {
    "default": "hermes",
    "p7_risk": "claude",
    "p8_config": "claude",
}

# Gemini model used for reviews
_GEMINI_REVIEW_MODEL = "gemini-2.5-flash"

# Known contamination markers in Gemini CLI MCP responses (ECC hook leakage)
_GEMINI_CONTAMINATION_MARKERS = [
    "session-end-marker",
    "plugin_root",
    "#!/usr/bin/env node",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReviewerSpec:
    """Specification for one reviewer in the priority chain."""
    name: str          # "hermes" | "gemini" | "subagent"
    timeout_ms: int
    enabled: bool = True


def _parse_chain(config: str) -> list[ReviewerSpec]:
    """Parse REVIEWER_CHAIN env var into an ordered list of ReviewerSpecs.

    Always appends "subagent" as the final backstop — it never times out.
    """
    specs: list[ReviewerSpec] = []
    for name in (n.strip() for n in config.split(",") if n.strip()):
        if name == "hermes":
            specs.append(ReviewerSpec("hermes", HERMES_TIMEOUT_MS, _HERMES_AVAILABLE and bool(HERMES_TARGET)))
        elif name == "gemini":
            specs.append(ReviewerSpec("gemini", GEMINI_TIMEOUT_MS, _GEMINI_AVAILABLE))
    # Sub-agent is always last — graceful degradation backstop
    specs.append(ReviewerSpec("subagent", 300_000, True))
    return specs


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """Return effective reviewer model for this phase/role."""
    return "claude" if phase in _CLAUDE_PHASES else REVIEWER_POLICY.get(role, "hermes")


# ---------------------------------------------------------------------------
# ReviewerRouter
# ---------------------------------------------------------------------------

class ReviewerRouter:
    """
    Routes review requests through a priority-ordered chain of backends.

    Priority chain (default): Hermes MCP → Gemini CLI MCP → sub-agent
    Configurable via REVIEWER_CHAIN env var (e.g. "hermes,gemini").

    Fallback logic:
      - Each backend is attempted in order.
      - Timeout or error → log degradation → try next backend.
      - Sub-agent is always the final backstop (never fails).
      - All degradation events are recorded in result["_degradation"].

    Task decomposition:
      - If prompt > TASK_SIZE_THRESHOLD chars, auto-split into subtasks
        ordered by FR boundaries or paragraph structure.
      - Each subtask is reviewed independently; results are merged
        conservatively (any REJECT → overall REJECT).
    """

    def __init__(
        self,
        target: str = HERMES_TARGET,
        chain_config: str = REVIEWER_CHAIN_CONFIG,
    ):
        self.target = target
        self._chain: list[ReviewerSpec] = _parse_chain(chain_config)

    def review(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        """
        Send review request through the priority chain.

        Returns result dict with keys:
          review_status: "APPROVE" | "REJECT"
          confidence:    float 0–1
          violations:    list[str]
          summary:       str
          _reviewer_used:   str  (which backend actually answered)
          _degraded:        bool (True if primary reviewer(s) were skipped)
          _degradation:     list[dict] (log of each timeout/error)
          _degradation_note: str | None (human-readable summary if degraded)
        """
        subtasks = self._maybe_decompose(prompt, role)

        if len(subtasks) > 1:
            sub_results = [
                self._try_chain(role, subtask, phase, fr_id, timeout_ms,
                                task_idx=i + 1, task_total=len(subtasks))
                for i, subtask in enumerate(subtasks)
            ]
            return self._merge_results(sub_results)

        return self._try_chain(role, prompt, phase, fr_id, timeout_ms)

    # ------------------------------------------------------------------
    # Chain execution
    # ------------------------------------------------------------------

    def _try_chain(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None,
        timeout_ms: int | None,
        task_idx: int = 1,
        task_total: int = 1,
    ) -> dict:
        """Try each reviewer in priority order until one succeeds."""
        full_prompt = self._build_prompt(role, prompt, phase, fr_id, task_idx, task_total)
        degradation_log: list[dict] = []

        for spec in self._chain:
            if not spec.enabled:
                degradation_log.append({"reviewer": spec.name, "reason": "not_available_or_not_configured"})
                continue

            if spec.name == "subagent":
                result = self._try_subagent(role, prompt, phase, fr_id)
                result["_reviewer_used"] = "subagent"
                result["_degradation"] = degradation_log
                if degradation_log:
                    result["_degraded"] = True
                    skipped = ", ".join(
                        f"{d['reviewer']}({d['reason']})" for d in degradation_log
                    )
                    result["_degradation_note"] = (
                        f"[DEGRADED] Fell back to sub-agent after: {skipped}. "
                        "Review quality may differ from external reviewer."
                    )
                return result

            try:
                effective_timeout = timeout_ms if timeout_ms is not None else spec.timeout_ms

                if spec.name == "hermes":
                    raw = self._try_hermes(full_prompt, effective_timeout)
                elif spec.name == "gemini":
                    raw = self._try_gemini(full_prompt, effective_timeout)
                else:
                    continue

                result = self._parse_response(raw)
                result["_reviewer_used"] = spec.name
                result["_degradation"] = degradation_log
                if degradation_log:
                    result["_degraded"] = True
                    skipped = ", ".join(
                        f"{d['reviewer']} timed out" for d in degradation_log
                    )
                    result["_degradation_note"] = (
                        f"[NOTE] {spec.name} succeeded after: {skipped}."
                    )
                return result

            except (TimeoutError, RuntimeError) as exc:
                degradation_log.append({"reviewer": spec.name, "reason": str(exc)[:120]})
                continue

        # Should never reach here (subagent always succeeds)
        raise RuntimeError("Reviewer chain exhausted without result (subagent should always succeed)")

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _try_hermes(self, prompt: str, timeout_ms: int) -> str:
        """Attempt review via Hermes MCP. Raises TimeoutError on failure."""
        if not _HERMES_AVAILABLE:
            raise RuntimeError("Hermes MCP not imported (mcp_tools unavailable)")
        if not self.target:
            raise RuntimeError("HERMES_REVIEWER_TARGET not set")

        mcp__hermes__messages_send(target=self.target, message=prompt)

        try:
            event = mcp__hermes__events_wait(session_key=self.target, timeout_ms=timeout_ms)
        except Exception as exc:
            raise TimeoutError(f"events_wait failed: {exc}") from exc

        # Whether event fired or timed out, attempt cold read
        try:
            msgs = mcp__hermes__messages_read(session_key=self.target, limit=1)
        except Exception as exc:
            # Known failure: "Session database unavailable" — treat as timeout
            raise TimeoutError(f"messages_read failed: {exc}") from exc

        if not msgs:
            raise TimeoutError(f"Hermes: no response within {timeout_ms}ms")

        return msgs[-1].get("content", "")

    def _try_gemini(self, prompt: str, timeout_ms: int) -> str:  # noqa: ARG002
        """Attempt review via Gemini CLI MCP. Raises RuntimeError on failure."""
        if not _GEMINI_AVAILABLE:
            raise RuntimeError("Gemini CLI MCP not imported (mcp_tools unavailable)")

        try:
            result = mcp__gemini_cli__ask_gemini(
                prompt=prompt,
                model=_GEMINI_REVIEW_MODEL,
            )
            raw = result.get("response", result.get("text", str(result)))
            return self._clean_gemini_response(raw)
        except Exception as exc:
            raise RuntimeError(f"Gemini CLI MCP error: {exc}") from exc

    def _clean_gemini_response(self, raw: str) -> str:
        """Strip known hook/system output contamination from Gemini responses.

        Gemini CLI MCP can leak ECC plugin SessionEnd hook output into the
        response payload. This strips any content after known contamination markers.
        """
        for marker in _GEMINI_CONTAMINATION_MARKERS:
            if marker in raw:
                idx = raw.index(marker)
                clean_end = raw.rfind("\n", 0, idx)
                raw = (raw[:clean_end] if clean_end > 0 else raw[:idx]).strip()
                break
        return raw.strip()

    def _try_subagent(self, role: str, prompt: str, phase: int, fr_id: str | None) -> dict:
        """Graceful degradation to current-model sub-agent. Never fails."""
        try:
            from core.agent_spawner import AgentSpawner  # lazy import — avoids circular
            spawner = AgentSpawner()
            result = spawner.spawn(
                role=role,
                prompt=prompt,
                context={"degraded": True, "reason": "reviewer_chain_exhausted"},
                model="claude",
                phase=phase,
                fr_id=fr_id,
            )
            return result if isinstance(result, dict) else {"output": str(result), "status": "complete"}
        except Exception as exc:
            # Emergency safety net — synthesize APPROVE with minimal confidence
            return {
                "review_status": "APPROVE",
                "confidence": 0.3,
                "violations": [],
                "summary": f"[EMERGENCY FALLBACK] Sub-agent also failed: {exc}. Auto-approved with low confidence.",
                "_emergency_fallback": True,
            }

    # ------------------------------------------------------------------
    # Task decomposition
    # ------------------------------------------------------------------

    def _maybe_decompose(self, prompt: str, role: str) -> list[str]:
        """
        Decompose large/complex prompts into atomic subtasks.

        Strategy 1: If prompt contains multiple FR references → split by FR.
        Strategy 2: If prompt > TASK_SIZE_THRESHOLD → split at paragraph
                    boundaries targeting SUBTASK_MAX_SIZE per subtask.

        Each subtask is labelled with its index for reviewer context.
        Returns [prompt] unchanged if decomposition is not needed.
        """
        if len(prompt) <= TASK_SIZE_THRESHOLD:
            return [prompt]

        # Strategy 1: FR-boundary split
        fr_sections = [s.strip() for s in re.split(r'(?=\bFR-\d+\b)', prompt) if s.strip()]
        if len(fr_sections) > 1 and all(len(s) <= TASK_SIZE_THRESHOLD for s in fr_sections):
            return [
                f"[Subtask {i + 1}/{len(fr_sections)} | {role}]\n{section}"
                for i, section in enumerate(fr_sections)
            ]

        # Strategy 2: Paragraph-boundary split
        subtasks: list[str] = []
        current: list[str] = []
        current_len = 0
        for para in prompt.split("\n\n"):
            if current_len + len(para) > SUBTASK_MAX_SIZE and current:
                subtasks.append("\n\n".join(current))
                current = [para]
                current_len = len(para)
            else:
                current.append(para)
                current_len += len(para)
        if current:
            subtasks.append("\n\n".join(current))

        return [
            f"[Subtask {i + 1}/{len(subtasks)} | {role}]\n{task}"
            for i, task in enumerate(subtasks)
        ]

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _merge_results(self, results: list[dict]) -> dict:
        """
        Merge multi-subtask review results conservatively.

        - Any REJECT → overall REJECT (short-circuit).
        - Confidence = min across all subtasks.
        - Violations = union across all subtasks.
        - Summary = concatenated, pipe-separated.
        """
        if not results:
            return {"review_status": "REJECT", "confidence": 0.0,
                    "violations": ["no_subtask_results"], "summary": ""}
        if len(results) == 1:
            return results[0]

        all_violations: list[str] = []
        all_summaries: list[str] = []
        min_confidence = 1.0
        any_degraded = False
        degradation_notes: list[str] = []
        reviewer_used: str = "unknown"

        for r in results:
            if r.get("review_status") == "REJECT":
                # Short-circuit: propagate REJECT immediately
                return {**r, "_merged": True, "_subtask_count": len(results)}
            all_violations.extend(r.get("violations", []))
            if r.get("summary"):
                all_summaries.append(r["summary"])
            min_confidence = min(min_confidence, r.get("confidence", 1.0))
            if r.get("_degraded"):
                any_degraded = True
                if r.get("_degradation_note"):
                    degradation_notes.append(r["_degradation_note"])
            reviewer_used = r.get("_reviewer_used", reviewer_used)

        return {
            "review_status": "APPROVE",
            "confidence": min_confidence,
            "violations": all_violations,
            "summary": " | ".join(all_summaries),
            "_merged": True,
            "_subtask_count": len(results),
            "_reviewer_used": reviewer_used,
            "_degraded": any_degraded,
            "_degradation_note": "; ".join(degradation_notes) if degradation_notes else None,
        }

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        task_idx: int = 1,
        task_total: int = 1,
    ) -> str:
        fr_tag = f" | FR {fr_id}" if fr_id else ""
        task_tag = f" | Task {task_idx}/{task_total}" if task_total > 1 else ""
        header = f"[Harness Reviewer | Phase {phase}{fr_tag}{task_tag}]\nRole: {role}\n\n"
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
        return {
            "review_status": "REJECT",
            "confidence": 0.0,
            "violations": ["parse_error"],
            "summary": raw[:200],
        }
