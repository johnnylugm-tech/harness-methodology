"""Structured YAML decision log per agent/gate invocation."""
# harness/decision_log.py
# Extracted from feature-13-observability — PyYAML + stdlib only (no Langfuse).
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
    _YAML = True
except ImportError:
    _YAML = False

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
        seq = len(list(d.glob(f"{entry.agent_id}_{entry.phase}_*.yaml"))) + 1
        p = d / f"{entry.agent_id}_{entry.phase}_{seq:03d}.yaml"
        data = asdict(entry)
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            if _YAML else __import__('json').dumps(data, indent=2, ensure_ascii=False)
        )
        return p

    def read_phase(self, phase: int) -> list[dict]:
        entries = []
        for f in sorted(self.log_root.rglob(f"*_{phase}_*.yaml")):
            try:
                entries.append(
                    yaml.safe_load(f.read_text()) if _YAML
                    else __import__('json').loads(f.read_text())
                )
            except Exception:  # nosec B110
                pass
        return entries
