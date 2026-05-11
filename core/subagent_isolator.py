#!/usr/bin/env python3
"""
Subagent Isolator — enforces On-Demand / Need-to-Know isolation for subagent spawning.

Principles:
- Each subagent gets a FRESH message context (no memory bleed)
- Subagents receive ONLY the artifacts relevant to their task
- Artifact paths are declared and validated BEFORE spawn
- No shared mutable state between subagent invocations
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from pathlib import Path
import json
import hashlib
import copy


@dataclass
class ArtifactSpec:
    """Declared artifact for a subagent invocation."""
    path: str
    role: str          # "input" | "output" | "reference"
    required: bool = True
    description: str = ""

    def exists(self) -> bool:
        """Check whether the isolated subagent worktree exists."""
        return Path(self.path).exists()


@dataclass
class SubagentContext:
    """
    Isolated context for a single subagent invocation.

    Contains ONLY what the subagent needs — nothing more.
    """
    task: str
    role: str
    artifacts: List[ArtifactSpec] = field(default_factory=list)
    persona_prompt: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, str]] = field(default_factory=list)  # Always fresh
    isolation_id: str = ""
    workspace_path: str = ""  # Per-FR workspace directory (Item 3)

    def __post_init__(self):
        if not self.isolation_id:
            content = json.dumps({"task": self.task, "role": self.role}, sort_keys=True)
            self.isolation_id = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_spawn_config(self) -> Dict[str, Any]:
        """Serialize to spawn config for agent SDK."""
        return {
            "isolation_id": self.isolation_id,
            "task": self.task,
            "role": self.role,
            "persona_prompt": self.persona_prompt,
            "artifact_paths": [
                {"path": a.path, "role": a.role, "required": a.required}
                for a in self.artifacts
            ],
            "messages": copy.deepcopy(self.messages),  # Fresh copy
            "metadata": copy.deepcopy(self.metadata),
        }


class ArtifactValidationError(Exception):
    """Raised when required artifacts are missing before subagent spawn."""
    pass


class IsolationViolationError(Exception):
    """Raised when context contains cross-task contamination."""
    pass


class SubagentIsolator:
    """
    Enforces subagent isolation: On-Demand, Need-to-Know.

    Usage::

        isolator = SubagentIsolator()

        ctx = isolator.create_context(
            task="Implement login endpoint",
            role="developer",
            artifacts=[
                ArtifactSpec("specs/login.md",  role="input",  required=True),
                ArtifactSpec("src/auth/login.py", role="output", required=False),
            ]
        )

        # Validate before spawn (raises ArtifactValidationError if inputs missing)
        isolator.validate(ctx)

        spawn_config = ctx.to_spawn_config()
        # Pass spawn_config to agent SDK spawn call
    """

    def __init__(self, project_root: Optional[str] = None, workspace_manager = None):
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._active_contexts: Dict[str, SubagentContext] = {}
        self.workspace_manager = workspace_manager  # Optional WorkspaceManager (Item 3)

    def create_context(
        self,
        task: str,
        role: str,
        artifacts: Optional[List[ArtifactSpec]] = None,
        persona_prompt: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SubagentContext:
        """
        Create a fresh, isolated subagent context.

        Args:
            task: Task description (what the subagent must do)
            role: Agent role ("developer", "reviewer", "qa", etc.)
            artifacts: Declared input/output artifact specs
            persona_prompt: Pre-built persona prompt string
            metadata: Optional read-only task metadata

        Returns:
            SubagentContext with empty messages[] — always fresh
        """
        ctx = SubagentContext(
            task=task,
            role=role,
            artifacts=artifacts or [],
            persona_prompt=persona_prompt,
            metadata=copy.deepcopy(metadata or {}),
            messages=[],  # Enforced fresh — no prior conversation history
        )
        self._active_contexts[ctx.isolation_id] = ctx
        return ctx

    def set_workspace(self, ctx: SubagentContext, fr_id: str) -> None:
        """Assign a per-FR workspace to the context (Item 3)."""
        if self.workspace_manager is None:
            return
        ws = self.workspace_manager.create_workspace(fr_id)
        ctx.workspace_path = str(ws)

    def validate(self, ctx: SubagentContext) -> None:
        """
        Validate all required input artifacts exist before spawning.
        Also validates workspace containment if workspace_path is set.

        Raises:
            ArtifactValidationError: if any required input artifact is missing
        """
        missing = [
            a.path
            for a in ctx.artifacts
            if a.required and a.role == "input" and not a.exists()
        ]
        if missing:
            raise ArtifactValidationError(
                f"[{ctx.isolation_id}] Missing required input artifacts before spawn:\n"
                + "\n".join(f"  - {p}" for p in missing)
            )
        # Workspace containment check (Item 3)
        if ctx.workspace_path and self.workspace_manager:
            for a in ctx.artifacts:
                if a.role == "output":
                    out_path = Path(a.path)
                    if not out_path.is_absolute():
                        out_path = self.project_root / out_path
                    self.workspace_manager.validate_path(out_path, a.path.split("/")[-1].split(".")[0] or "unknown")

    def validate_outputs(self, ctx: SubagentContext) -> Dict:
        """
        After subagent completes, verify declared output artifacts were produced.

        Returns:
            dict: {complete, produced, missing}
        """
        output_specs = [a for a in ctx.artifacts if a.role == "output"]
        produced = [a.path for a in output_specs if a.exists()]
        missing = [a.path for a in output_specs if a.required and not a.exists()]
        return {
            "complete": not missing,
            "produced": produced,
            "missing": missing,
        }

    def verify_isolation(self, ctx: SubagentContext) -> None:
        """
        Check that the context has not been contaminated with cross-task data.

        Raises:
            IsolationViolationError: if messages[] is non-empty (cross-context bleed)
        """
        if ctx.messages:
            raise IsolationViolationError(
                f"[{ctx.isolation_id}] Context isolation violated: "
                f"messages[] is non-empty ({len(ctx.messages)} entries). "
                "Create a fresh SubagentContext per spawn."
            )

    def spawn(
        self,
        task: str,
        role: str,
        artifacts: Optional[List[ArtifactSpec]] = None,
        persona_prompt: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """
        High-level helper: create context, validate, return spawn config.

        Args:
            task: Task description
            role: Agent role
            artifacts: Artifact specs
            persona_prompt: Persona prompt string
            metadata: Optional metadata
            validate: If True, validate input artifacts before returning

        Returns:
            Spawn config dict ready for agent SDK

        Raises:
            ArtifactValidationError: if required inputs are missing
        """
        ctx = self.create_context(task, role, artifacts, persona_prompt, metadata)
        if validate:
            self.validate(ctx)
        return ctx.to_spawn_config()

    def get_context(self, isolation_id: str) -> Optional[SubagentContext]:
        """Retrieve an active context by isolation_id."""
        return self._active_contexts.get(isolation_id)

    def release(self, isolation_id: str) -> None:
        """Release context after subagent completes (free reference)."""
        self._active_contexts.pop(isolation_id, None)

    def active_count(self) -> int:
        """Number of currently active (unreleased) contexts."""
        return len(self._active_contexts)


# ---- Convenience factory ------------------------------------------------

def create_isolated_spawn(
    task: str,
    role: str,
    input_paths: Optional[List[str]] = None,
    output_paths: Optional[List[str]] = None,
    persona_prompt: str = "",
) -> Dict[str, Any]:
    """
    One-shot helper: build + validate + return spawn config.

    Args:
        task: Task description
        role: Agent role string
        input_paths: List of required input artifact paths
        output_paths: List of expected output artifact paths
        persona_prompt: Pre-built persona prompt

    Returns:
        Spawn config dict
    """
    artifacts = []
    for p in (input_paths or []):
        artifacts.append(ArtifactSpec(path=p, role="input", required=True))
    for p in (output_paths or []):
        artifacts.append(ArtifactSpec(path=p, role="output", required=False))

    isolator = SubagentIsolator()
    return isolator.spawn(task=task, role=role, artifacts=artifacts, persona_prompt=persona_prompt)


# ══════════════════════════════════════════════════════════════════════════════
# Turn-based Continuation (Item 7) — Symphony-inspired turn loop
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TurnContext:
    """Context for a single turn in a turn-based agent execution loop."""
    turn_number: int
    fr_id: str
    previous_output: Optional[Dict[str, Any]] = None
    state_snapshot: Optional[Dict[str, Any]] = None
    continuation_prompt: str = ""
    remaining_items: list = field(default_factory=list)


@dataclass
class TurnResult:
    """Result of executing a single turn."""
    turn_number: int
    output: Dict[str, Any] = field(default_factory=dict)
    state_changes: Optional[Dict[str, Any]] = None
    next_actions: list[str] = field(default_factory=list)
    should_continue: bool = True


class TurnBasedExecutor:
    """
    Symphony-inspired turn-based agent executor (Item 7).

    Turns:
      Turn 1: Full prompt (SRS + SAD + task specification)
      Turn 2..N: Continuation guidance only (delta from previous turn, remaining items)

    HR-12 enforcement: max 5 turns before escalation.
    """

    def __init__(self, isolator: SubagentIsolator, max_turns: int = 5):
        self.isolator = isolator
        self.max_turns = max_turns
        self._turn_history: list[TurnResult] = []

    def execute_turn(self, turn: TurnContext) -> TurnResult:
        """Execute a single turn via subagent dispatch."""
        prompt = self._build_prompt(turn)
        spawn_config = self.isolator.spawn(
            task=prompt,
            role="developer",
            validate=turn.turn_number == 1,  # validate inputs only on first turn
        )
        # In practice, the actual dispatch is done by the caller;
        # the spawn_config serves as the input payload.
        result = TurnResult(
            turn_number=turn.turn_number,
            output={"spawn_config": spawn_config},
            state_changes={},
            next_actions=turn.remaining_items if turn.remaining_items else [],
            should_continue=bool(turn.remaining_items) and turn.turn_number < self.max_turns,
        )
        self._turn_history.append(result)
        return result

    def generate_continuation(
        self, prev_result: TurnResult, checklist: list[str]
    ) -> TurnContext:
        """Build next turn context from previous result (delta only)."""
        remaining = list(prev_result.next_actions or [])
        return TurnContext(
            turn_number=prev_result.turn_number + 1,
            fr_id="",
            previous_output=prev_result.output,
            state_snapshot=prev_result.state_changes or {},
            remaining_items=remaining,
            continuation_prompt=(
                f"Continuation turn. Previous changes: {prev_result.state_changes}. "
                f"Remaining items: {remaining}"
            ),
        )

    def should_terminate(self, turn: TurnResult, turns_used: int) -> bool:
        """Check HR-12: max 5 turns, or explicit stop signal."""
        if turns_used >= self.max_turns:
            return True
        if not turn.should_continue:
            return True
        return False

    @staticmethod
    def get_state_diff(before: dict, after: dict) -> dict:
        """Compute which keys changed between two state snapshots."""
        diff = {}
        all_keys = set(before.keys()) | set(after.keys())
        for k in all_keys:
            if before.get(k) != after.get(k):
                diff[k] = {"from": before.get(k), "to": after.get(k)}
        return diff

    def _build_prompt(self, turn: TurnContext) -> str:
        if turn.turn_number == 1:
            return turn.continuation_prompt or "Execute the assigned task."
        return (
            f"[Turn {turn.turn_number}/{self.max_turns}] Continuation guidance — "
            f"do NOT re-execute completed work. Focus on remaining items: "
            f"{turn.remaining_items or 'none'}"
        )

    def history(self) -> list[TurnResult]:
        return list(self._turn_history)
