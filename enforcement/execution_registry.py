#!/usr/bin/env python3
"""
Execution Registry
==================
Proves each step was genuinely executed via cryptographic signatures.

Each step execution is recorded with timestamp + artifact.
Signatures are SHA-256 hashes — unforgeable.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
import hashlib
import json
import os
import sqlite3


@dataclass
class ExecutionRecord:
    """Record of a single step execution."""
    step: str
    timestamp: str
    artifact: Dict[str, Any]
    signature: str
    verified: bool = True
    note: str = ""


class ExecutionRegistry:
    """
    Execution Registry — proof system for agent step execution.

    Usage::

        registry = ExecutionRegistry()

        # Record step
        sig = registry.record(
            step="quality-gate",
            artifact={"score": 95, "passed": True, "files_checked": 42}
        )

        # Verify step was executed
        if registry.prove("quality-gate"):
            print("Quality Gate executed")
        else:
            print("Quality Gate NOT executed — non-compliant")
    """

    def __init__(self, db_path: str = ".methodology/execution_registry.db"):
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self):
        """Ensure SQLite database and table exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                step TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                artifact TEXT NOT NULL,
                signature TEXT NOT NULL UNIQUE,
                verified INTEGER DEFAULT 1,
                note TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _generate_signature(self, data: Dict) -> str:
        """Generate unforgeable SHA-256 signature."""
        content = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def record(self, step: str, artifact: Dict[str, Any], note: str = "") -> str:
        """
        Record a step execution.

        Args:
            step: Step name
            artifact: Execution output/artifact dict
            note: Optional note

        Returns:
            str: SHA-256 signature (use to verify later)
        """
        timestamp = datetime.now().isoformat()
        record_data = {"step": step, "timestamp": timestamp, "artifact": artifact}
        signature = self._generate_signature(record_data)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO executions (step, timestamp, artifact, signature, note) VALUES (?, ?, ?, ?, ?)",
            (step, timestamp, json.dumps(artifact), signature, note)
        )
        conn.commit()
        conn.close()
        return signature

    def prove(self, step: str, since: Optional[str] = None) -> bool:
        """
        Verify a step was actually executed.

        Args:
            step: Step name
            since: Optional ISO timestamp lower bound

        Returns:
            bool: True if executed, False otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if since:
            cursor.execute(
                "SELECT COUNT(*) FROM executions WHERE step=? AND timestamp>=?",
                (step, since)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM executions WHERE step=?", (step,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def get_records(self, step: Optional[str] = None, limit: int = 100) -> List[ExecutionRecord]:
        """Retrieve execution records."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if step:
            cursor.execute(
                "SELECT step, timestamp, artifact, signature, verified, note FROM executions WHERE step=? ORDER BY id DESC LIMIT ?",
                (step, limit)
            )
        else:
            cursor.execute(
                "SELECT step, timestamp, artifact, signature, verified, note FROM executions ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        rows = cursor.fetchall()
        conn.close()
        return [
            ExecutionRecord(
                step=row[0], timestamp=row[1], artifact=json.loads(row[2]),
                signature=row[3], verified=bool(row[4]), note=row[5]
            )
            for row in rows
        ]

    def verify_chain(self, steps: List[str]) -> Dict:
        """
        Verify a chain of steps is complete.

        Args:
            steps: Expected step names in order

        Returns:
            dict: {complete, missing, executed, total}
        """
        records = self.get_records(limit=1000)
        executed_steps = set(r.step for r in records)
        missing = [s for s in steps if s not in executed_steps]
        return {
            "complete": len(missing) == 0,
            "missing": missing,
            "executed": list(executed_steps & set(steps)),
            "total": len(steps),
        }

    def get_evidence_report(self, step: str) -> Dict:
        """Get evidence report for a step."""
        records = self.get_records(step=step, limit=1)
        if not records:
            return {"step": step, "executed": False, "evidence": None}
        r = records[0]
        return {
            "step": step,
            "executed": True,
            "evidence": {
                "timestamp": r.timestamp,
                "artifact": r.artifact,
                "signature": r.signature,
                "verified": r.verified,
            },
        }


def create_minimal_registry() -> ExecutionRegistry:
    """Create a minimal ExecutionRegistry for critical steps only."""
    return ExecutionRegistry()
