#!/usr/bin/env python3
"""
Execution Logger — collects structured execution logs from sessions_spawn.log.

Converts SubagentIsolator structured output into BVS-consumable format.

Usage:
    from constitution.execution_logger import ExecutionLogger

    logger = ExecutionLogger(project_path)
    logs = logger.collect_from_sessions_spawn_log()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


@dataclass
class ExecutionLogEntry:
    """Standardized execution log entry."""
    timestamp: str
    phase: int
    role: str              # developer / reviewer / architect / qa_engineer
    task: str              # e.g., "FR-01"
    session_id: str
    session_key: str
    status: str            # success / error / unable_to_proceed
    confidence: int        # 1-10
    citations: List[str]   # ["FR-01", "SRS.md#L23"]
    summary: str           # 50-char max
    duration_seconds: float
    error: Optional[str] = None
    artifacts_read: List[str] = field(default_factory=list)
    artifacts_produced: List[str] = field(default_factory=list)


class ExecutionLogger:
    """Collect execution logs from multiple sources.

    Sources:
    1. .methodology/sessions_spawn.log — SubagentIsolator dispatch log
    2. SubagentResult objects — direct from SubagentIsolator.results
    3. Phase execution records — phase execution metadata
    """

    def __init__(self, project_path: str) -> None:
        self.project_path = Path(project_path)

    def collect_from_sessions_spawn_log(self) -> List[Dict[str, Any]]:
        """Collect execution logs from sessions_spawn.log.

        sessions_spawn.log format (HR-10):
        {"timestamp": "...", "role": "...", "task": "...", "session_id": "...", ...}
        """
        from core.utils.project_layout import ProjectLayout
        log_path = ProjectLayout(self.project_path).sessions_spawn_log
        if not log_path.exists():
            return []

        entries = []
        for line in log_path.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = json.loads(line)
                entries.append({
                    "timestamp": entry.get("timestamp", ""),
                    "phase": self._infer_phase_from_task(entry.get("task", "")),
                    "role": entry.get("role", "unknown"),
                    "task": entry.get("task", ""),
                    "session_id": entry.get("session_id", ""),
                    "session_key": entry.get("session_key", entry.get("session_id", "")),
                    "status": entry.get("status", "unknown"),
                    "confidence": entry.get("confidence", 0),
                    "citations": entry.get("citations", []),
                    "summary": entry.get("summary", ""),
                    "duration_seconds": entry.get("duration_seconds", 0),
                    "error": entry.get("error"),
                })
            except json.JSONDecodeError:
                continue

        return entries

    def collect_from_subagent_results(self, results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Collect from SubagentIsolator.results (direct SubagentResult consumption).

        Args:
            results: SubagentIsolator.results (session_key -> SubagentResult).
        """
        entries = []
        for session_key, result in results.items():
            entry = {
                "timestamp": datetime.now().isoformat(),
                "phase": self._infer_phase_from_task(
                    result.task if hasattr(result, "task") else ""
                ),
                "role": result.role.value if hasattr(result, "role") else "unknown",
                "task": result.task if hasattr(result, "task") else "",
                "session_id": session_key,
                "session_key": session_key,
                "status": result.status if hasattr(result, "status") else "unknown",
                "confidence": result.confidence if hasattr(result, "confidence") else 0,
                "citations": result.citations if hasattr(result, "citations") else [],
                "summary": result.summary if hasattr(result, "summary") else "",
                "duration_seconds": (
                    result.duration_seconds if hasattr(result, "duration_seconds") else 0
                ),
                "error": result.error if hasattr(result, "error") else None,
            }
            entries.append(entry)
        return entries

    @staticmethod
    def _infer_phase_from_task(task: str) -> int:
        """Infer phase number from task description."""
        task_lower = task.lower()
        for pn in range(1, 9):
            if f"phase {pn}" in task_lower or f"p{pn}" in task_lower:
                return pn
        return 1

    def get_phase_context(self, phase: int) -> Dict[str, Any]:
        """Get execution context for a given phase."""
        context: Dict[str, Any] = {
            "phase": phase,
            "max_allowed_phase": phase,
            "parent_session_id": None,
            "review_iterations": 0,
            "estimated_duration": 3600,
        }
        context["artifact_contents"] = self._load_artifacts_for_phase(phase)
        return context

    def _load_artifacts_for_phase(self, phase: int) -> Dict[str, str]:
        """Load artifact contents for the given phase.

        Phase 1: SRS.md
        Phase 2: SAD.md
        Phase 3-8: SRS.md + SAD.md + TEST_PLAN.md
        """
        artifacts = {}

        artifact_map: Dict[int, List[str]] = {
            1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
            2: ["02-architecture/SAD.md"],
        }

        phase_artifacts = artifact_map.get(phase, [])
        if phase >= 3:
            phase_artifacts = [
                "01-requirements/SRS.md",
                "02-architecture/SAD.md",
                "04-testing/TEST_PLAN.md",
            ]

        for artifact_path in phase_artifacts:
            full_path = self.project_path / artifact_path
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    artifacts[artifact_path] = content[:100000]
                except Exception:
                    continue

        return artifacts


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Execution Logger")
    parser.add_argument("--project-path", required=True, help="Project path")
    parser.add_argument("--output", help="Output JSON path")
    args = parser.parse_args()

    logger = ExecutionLogger(args.project_path)
    logs = logger.collect_from_sessions_spawn_log()

    output = {"total": len(logs), "logs": logs}

    if args.output:
        Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False))
        print(f"Saved {len(logs)} entries to {args.output}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
