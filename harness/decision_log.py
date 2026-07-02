"""Structured YAML decision log per agent/gate invocation."""
# harness/decision_log.py
# Extracted from feature-13-observability — PyYAML + stdlib only (no Langfuse).
from __future__ import annotations
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
    _YAML = True
except ImportError:  # pragma: no cover
    _YAML = False
    yaml = None  # pyright: ignore[reportAssignmentType]

@dataclass
class DecisionContext:
    """Grouped context for a decision."""
    agent_id: str
    phase: int
    fr_id: str | None = None
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class DecisionLogEntry:
    """Decision log entry with reduced direct attribute count."""
    ctx: DecisionContext
    decision: str           # APPROVE | REJECT | GATE_PASS | GATE_BLOCK
    reasoning: str
    scores: dict[str, float] = field(default_factory=dict) # uaf_score, gate_score
    metadata: dict = field(default_factory=dict)

    @property
    def agent_id(self): return self.ctx.agent_id
    @property
    def phase(self): return self.ctx.phase

class DecisionLogWriter:
    """Writes .methodology/decision_logs/{date}/{agent}_{phase}_{seq}.yaml"""

    def __init__(self, log_root: str = ".methodology/decision_logs"):
        self.log_root = Path(log_root)

    def write(self, entry: DecisionLogEntry) -> Path:
        d = self.log_root / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        # Sanitize agent_id to prevent glob metacharacter injection (e.g. "AGENT*x").
        # Filename uniqueness comes from uuid4, NOT a glog-based sequence counter,
        # so concurrent writers cannot collide (no TOCTOU read-then-write window).
        safe_agent = re.sub(r"[^A-Za-z0-9_-]", "_", entry.agent_id)
        file_id = uuid.uuid4().hex[:8]
        p = d / f"{safe_agent}_{entry.phase}_{file_id}.yaml"
        data = asdict(entry)
        payload = (
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False)  # pyright: ignore[reportOptionalMemberAccess]
            if _YAML else __import__('json').dumps(data, indent=2, ensure_ascii=False)
        )
        # Atomic create-exclusive: two writers with the same uuid never overwrite
        # each other; the loser gets FileExistsError, the winner's bytes are intact.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(p), flags, 0o644)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return p

    def read_phase(self, phase: int) -> list[dict]:
        entries = []
        for f in sorted(self.log_root.rglob(f"*_{phase}_*.yaml")):
            try:
                entries.append(
                    yaml.safe_load(f.read_text()) if _YAML  # pyright: ignore[reportOptionalMemberAccess]
                    else __import__('json').loads(f.read_text())
                )
            except Exception:  # nosec B110
                pass
        return entries
