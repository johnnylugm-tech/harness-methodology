"""
KillSwitch Audit Logger.

Provides audit logging for kill switch events.
"""
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class AuditEntry:
    """KillSwitch audit log entry."""
    event_id: str
    event_type: str
    agent_id: Optional[str]
    action: str
    outcome: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for JSONL persistence."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "action": self.action,
            "outcome": self.outcome,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "metadata": self.metadata,
        }


class AuditLogger:
    """
    KillSwitch-specific audit logger.

    Persists interrupt events to a JSONL file at ``{log_dir}/audit.log``.
    One JSON object per line — easy to tail / grep / replay.

    Accepts both call styles for backward compatibility with external callers
    that pass an ``AuditEntry`` object directly:

        logger.log_event(entry)              # object form
        logger.log_event(event_type=..., agent_id=..., reason=...,
                         actor=..., metadata=...)   # kwarg form (used by InterruptEngine)
    """

    def __init__(self, log_dir: Optional[str] = None, log_file: str = "audit.log") -> None:
        """Initialize instance with default configuration."""
        if log_dir is None:
            log_dir = os.path.join(tempfile.gettempdir(), "kill_switch_logs")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / log_file
        self._in_memory: List[AuditEntry] = []  # last-N ring kept for in-process query

    def log_event(
        self,
        entry: Optional[AuditEntry] = None,
        *,
        event_type: Optional[Union[str, Any]] = None,
        agent_id: Optional[str] = None,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an audit event.

        Two call forms are accepted (kwarg form is what ``InterruptEngine._log_event``
        uses — see ``kill_switch/interrupt_engine.py``):

        1. Object form:  ``log_event(entry: AuditEntry)``
        2. Kwarg form:   ``log_event(event_type=..., agent_id=..., reason=...,
                                      actor=..., metadata=...)``
        """
        if entry is None:
            # Kwarg form — synthesise an AuditEntry.
            entry = AuditEntry(
                event_id=str(uuid.uuid4()),
                event_type=str(getattr(event_type, "value", event_type)),
                agent_id=agent_id,
                action=str(actor) if actor is not None else "unknown",
                outcome="recorded",
                reason=reason,
                metadata=metadata or {},
            )
        else:
            # Object form — backfill any missing fields from the kwarg form so the
            # synthesised entry is complete (e.g. caller passed an AuditEntry
            # without `action` set, but also passed `actor=` as a kwarg).
            if event_type is not None and not entry.event_type:
                entry.event_type = str(getattr(event_type, "value", event_type))
            if agent_id is not None and entry.agent_id is None:
                entry.agent_id = agent_id
            if reason is not None and entry.reason is None:
                entry.reason = reason
            if actor is not None:
                entry.action = str(actor)
            if metadata:
                entry.metadata.update(metadata)

        # Persist to JSONL (append + fsync for crash safety).
        try:
            line = json.dumps(entry.to_dict(), ensure_ascii=False)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            # Audit logging must never break the interrupt flow — degrade to in-memory.
            # Re-raise only on programmer errors (TypeError, etc.).
            if isinstance(exc, (TypeError, ValueError)):
                raise
            # For OS errors, keep the in-memory copy and let the caller carry on.

        # Keep the last 1024 entries in memory for synchronous query().
        self._in_memory.append(entry)
        if len(self._in_memory) > 1024:
            self._in_memory = self._in_memory[-1024:]

    def log_escalation(self, **kwargs: Any) -> None:
        """Alias used by some external governance loggers; forwards to log_event."""
        # Normalise: external callers may pass `event_type=` already, but
        # `actor` is the field name used in InterruptEngine; map it.
        if "actor" not in kwargs and "triggered_by" in kwargs:
            kwargs["actor"] = kwargs.pop("triggered_by")
        self.log_event(**kwargs)

    def query(self, filters: dict) -> list:
        """
        Query the in-memory ring buffer.

        Supported filters (all AND-ed):
          - ``event_type``: exact match on ``entry.event_type``
          - ``agent_id``:   exact match on ``entry.agent_id``
          - ``since``:      datetime — keep entries with ``entry.timestamp >= since``
          - ``limit``:      int — cap on returned list (default 100)
        """
        limit = int(filters.get("limit", 100))
        results = list(self._in_memory)

        et_filter = filters.get("event_type")
        if et_filter is not None:
            results = [e for e in results if e.event_type == et_filter]

        agent_filter = filters.get("agent_id")
        if agent_filter is not None:
            results = [e for e in results if e.agent_id == agent_filter]

        since = filters.get("since")
        if since is not None:
            results = [e for e in results if e.timestamp >= since]

        return results[-limit:]

    def read_log_file(self) -> List[Dict[str, Any]]:
        """Read back the persisted JSONL audit log (for post-mortem / replay)."""
        if not self.log_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines; never raise from a reader.
                    continue
        return entries
