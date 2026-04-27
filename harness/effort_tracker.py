"""
Effort Tracker: Records and summarizes execution metrics.

Uses SQLite to persist duration and token usage per operation.
"""

from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EffortRecord:
    """Represents a single operation's effort metrics."""
    phase: int
    agent_id: str
    operation: str          # gate_run | tier1_eval | tier3_eval | fix_round | review
    duration_s: float
    gate_num: int | None = None
    token_in: int = 0
    token_out: int = 0
    fr_id: str | None = None


class EffortTracker:
    """Persists and queries effort metrics from a local SQLite database."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS effort (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase INTEGER, gate_num INTEGER, agent_id TEXT, operation TEXT,
            duration_s REAL, token_in INTEGER DEFAULT 0, token_out INTEGER DEFAULT 0,
            fr_id TEXT, created_at TEXT DEFAULT (datetime('now'))
        )"""

    def __init__(self, db_path: str = ".methodology/effort_metrics.db"):
        """Initialize the tracker and ensure the schema exists."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as c:
            c.execute(self._SCHEMA)

    def record(self, r: EffortRecord) -> None:
        """Record a single effort entry to the database."""
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO effort (phase,gate_num,agent_id,operation,duration_s,token_in,token_out,fr_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (r.phase, r.gate_num, r.agent_id, r.operation,
                 r.duration_s, r.token_in, r.token_out, r.fr_id),
            )

    def summary(self, phase: int | None = None) -> dict:
        """Summarize all recorded effort, optionally filtered by phase."""
        if phase is not None:
            return self.query_phase_summary(phase)
        
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT COUNT(*), SUM(duration_s), SUM(token_in+token_out) FROM effort"
            ).fetchone()
        return {
            "total_operations": row[0] or 0,
            "total_duration_s": row[1] or 0.0,
            "total_tokens": row[2] or 0
        }

    def query_phase_summary(self, phase: int) -> dict:
        """Get per-operation summary for a specific phase."""
        with sqlite3.connect(self.db_path) as c:
            rows = c.execute(
                "SELECT operation,SUM(duration_s),SUM(token_in+token_out) "
                "FROM effort WHERE phase=? GROUP BY operation", (phase,)
            ).fetchall()
        return {r[0]: {"duration_s": r[1], "total_tokens": r[2]} for r in rows}

    def query_gate_summary(self, gate_num: int) -> dict:
        """Get summary metrics for a specific quality gate."""
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT COUNT(*),SUM(duration_s),SUM(token_in+token_out) "
                "FROM effort WHERE gate_num=?", (gate_num,)
            ).fetchone()
        return {"runs": row[0] or 0, "total_duration_s": row[1] or 0.0, "total_tokens": row[2] or 0}
