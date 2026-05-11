#!/usr/bin/env python3
"""
Lifecycle Hooks System — Symphony-inspired hook phases for phase/gate/FR events.

Hook definitions are loaded from .methodology/hooks.json (or defaults).
Each hook has a timeout and required/optional failure semantics.
"""

from __future__ import annotations
import datetime
import json
import logging
import os
import subprocess  # nosec B404
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(Enum):
    """Lifecycle events that can trigger hooks."""
    BEFORE_PHASE = "before_phase"
    AFTER_GATE_PASS = "after_gate_pass"
    ON_GATE_FAIL = "on_gate_fail"
    ON_ESCALATE = "on_escalate"
    AFTER_FR_COMPLETE = "after_fr_complete"
    BEFORE_PHASE_ADVANCE = "before_phase_advance"


@dataclass
class HookDefinition:
    """A single hook definition."""
    name: str
    event: HookEvent
    command: str
    timeout_seconds: int = 60
    required: bool = False


@dataclass
class HookResult:
    """Result of executing a single hook."""
    hook: HookDefinition
    success: bool
    output: str = ""
    duration_ms: int = 0


class HookRunner:
    """Loads and executes lifecycle hooks with timeout enforcement."""

    HOOKS_LOG = ".methodology/hooks.log"
    DEFAULT_HOOKS_PATH = ".methodology/hooks.json"

    def __init__(self, project_root: Path, hooks_path: Path | None = None):
        self.project_root = project_root
        self.hooks_path = hooks_path or (project_root / self.DEFAULT_HOOKS_PATH)
        self.log_path = project_root / self.HOOKS_LOG
        self._definitions: list[HookDefinition] = []
        self._load()

    def _load(self) -> None:
        """Load hook definitions from hooks.json."""
        if not self.hooks_path.exists():
            return
        try:
            raw = json.loads(self.hooks_path.read_text(encoding="utf-8"))
            for entry in raw if isinstance(raw, list) else raw.get("hooks", []):
                try:
                    event = HookEvent(entry.get("event", ""))
                    self._definitions.append(HookDefinition(
                        name=entry.get("name", "unnamed"),
                        event=event,
                        command=entry.get("command", ""),
                        timeout_seconds=int(entry.get("timeout", 60)),
                        required=bool(entry.get("required", False)),
                    ))
                except (ValueError, KeyError) as e:
                    logger.warning("Skipping invalid hook definition: %s", e)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load hooks from %s: %s", self.hooks_path, e)

    def run_hooks(self, event: HookEvent, context: dict[str, Any] | None = None) -> list[HookResult]:
        """Execute all hooks registered for the given event. Returns results."""
        ctx = context or {}
        matching = [h for h in self._definitions if h.event == event]
        if not matching:
            return []

        results: list[HookResult] = []
        for hook in matching:
            result = self._execute_one(hook, ctx)
            results.append(result)
            if not result.success and hook.required:
                break  # required hook failed — stop chain
        return results

    def _execute_one(self, hook: HookDefinition, ctx: dict[str, Any]) -> HookResult:
        """Execute a single hook with timeout enforcement."""
        start = time.monotonic()
        env = {**os.environ, "PROJECT_ROOT": str(self.project_root), **ctx}
        try:
            r = subprocess.run(  # nosec B602
                hook.command, shell=True, cwd=self.project_root,
                capture_output=True, text=True, timeout=hook.timeout_seconds, env=env,
            )
            success = r.returncode == 0
            output = (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            success = False
            output = f"Timeout after {hook.timeout_seconds}s"
        except Exception as e:
            success = False
            output = str(e)

        duration_ms = int((time.monotonic() - start) * 1000)
        self._log(hook, success, output, duration_ms)
        return HookResult(hook=hook, success=success, output=output[:2048], duration_ms=duration_ms)

    def _log(self, hook: HookDefinition, success: bool, output: str, duration_ms: int) -> None:
        """Append hook execution result to hooks.log (JSONL)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "hook": hook.name,
            "event": hook.event.value,
            "success": success,
            "duration_ms": duration_ms,
            "output": output[:500],
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_definitions(self) -> list[HookDefinition]:
        """Return all loaded hook definitions."""
        return list(self._definitions)
