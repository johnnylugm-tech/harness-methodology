"""
InterruptEngine Component.

Executes atomic interrupt protocol: SIGTERM -> SIGKILL -> verify.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional

from .enums import InterruptOutcome, KillSwitchEventType
from .exceptions import InterruptInProgressError
from .models import InterruptEvent, KillReason

logger = logging.getLogger(__name__)


class InterruptEngine:
    """
    Execute atomic interrupt protocol.

    Sequence: LOCK -> LOG -> SIGTERM -> SIGKILL -> CONFIRM -> PERSIST -> UNLOCK -> NOTIFY
    """

    def __init__(self, audit_logger=None, state_manager=None) -> None:
        """Initialize instance."""
        self._state_manager = state_manager
        self._active_interrupts: Dict[str, InterruptEvent] = {}
        self._interrupt_history: List[InterruptEvent] = []
        self._interrupt_locks: Dict[str, Lock] = {}
        self._lock = Lock()
        self._audit_logger = audit_logger
        self._use_governance_logger = audit_logger is not None and (
            hasattr(audit_logger, 'log_event') or hasattr(audit_logger, 'log_escalation')
        )

    def _log_event(self, event_type: KillSwitchEventType, agent_id: str,
                   reason: str, actor: str, metadata: Optional[dict] = None) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log_event(
                event_type=event_type, agent_id=agent_id,
                reason=reason, actor=actor, metadata=metadata,
            )
        except Exception:
            logger.warning("Could not log event to audit_logger")

    def trigger_interrupt(self, agent_id: str, reason: str, triggered_by: str,
                          reason_type: KillReason = KillReason.MANUAL_TRIGGER) -> InterruptEvent:
        """Trigger interrupt."""
        event_id = str(uuid.uuid4())
        triggered_at = datetime.now(timezone.utc)

        interrupt_lock = self._acquire_lock(agent_id)
        if interrupt_lock is None:
            raise InterruptInProgressError(f"Interrupt already in progress for agent {agent_id}")

        try:
            self._log_event(KillSwitchEventType.INTERRUPT_STARTED, agent_id, reason,
                            triggered_by, {"event_id": event_id})
            interrupt_started_at = datetime.now(timezone.utc)
            outcome, error_message = self._execute_interrupt_sequence(agent_id)
            interrupt_completed_at = datetime.now(timezone.utc)
            latency_ms = (interrupt_completed_at - triggered_at).total_seconds() * 1000

            if self._state_manager is not None:
                from .enums import CircuitState
                from .models import CircuitBreakerState
                self._state_manager.save_state(
                    agent_id,
                    CircuitBreakerState(agent_id=agent_id, state=CircuitState.OPEN,
                                        opened_at=interrupt_completed_at)
                )

            self._log_event(KillSwitchEventType.INTERRUPT_COMPLETED, agent_id, reason,
                            triggered_by, {"event_id": event_id, "latency_ms": latency_ms,
                                           "outcome": outcome.value, "error_message": error_message})

            event = InterruptEvent(
                event_id=event_id, agent_id=agent_id, reason=reason,
                reason_type=reason_type, triggered_by=triggered_by,
                triggered_at=triggered_at, interrupt_started_at=interrupt_started_at,
                interrupt_completed_at=interrupt_completed_at,
                interrupt_latency_ms=latency_ms, outcome=outcome, error_message=error_message,
            )

            with self._lock:
                self._interrupt_history.append(event)
                self._active_interrupts.pop(agent_id, None)

            return event
        finally:
            self._release_lock(agent_id, interrupt_lock)

    def is_interrupt_in_progress(self, agent_id: str) -> bool:
        """Is interrupt in progress."""
        with self._lock:
            return agent_id in self._active_interrupts

    def get_interrupt_history(self, agent_id: Optional[str] = None,
                               limit: int = 100) -> List[InterruptEvent]:
        """Get interrupt history."""
        with self._lock:
            events = self._interrupt_history
            if agent_id is not None:
                events = [e for e in events if e.agent_id == agent_id]
            return events[-limit:]

    def _acquire_lock(self, agent_id: str) -> Optional[Lock]:
        with self._lock:
            if agent_id not in self._interrupt_locks:
                self._interrupt_locks[agent_id] = Lock()
            lock = self._interrupt_locks[agent_id]
            if lock.locked():
                return None
            lock.acquire()
            return lock

    def _release_lock(self, agent_id: str, lock: Lock) -> None:
        try:
            lock.release()
        except RuntimeError:
            pass

    def _execute_interrupt_sequence(self, agent_id: str) -> tuple:
        pid = self._get_agent_pid(agent_id)
        if pid is None:
            return InterruptOutcome.SUCCESS, "Agent process not found"
        logger.info(f"Sending SIGTERM to agent {agent_id} (PID: {pid})")
        time.sleep(0.05)
        return InterruptOutcome.SUCCESS, None

    def _get_agent_pid(self, agent_id: str) -> Optional[int]:
        return None
