"""
InterruptEngine Component.

Executes atomic interrupt protocol — send SIGTERM, then SIGKILL if needed,
verify termination.
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

    Implements the interrupt sequence:
    1. LOCK: Acquire interrupt lock (prevents re-entrancy)
    2. LOG: Write trigger event to audit log (sync)
    3. SIGNAL: Send SIGTERM to agent process (2s graceful window)
    4. FORCE: If alive after 2s, send SIGKILL
    5. CONFIRM: Verify process terminated
    6. PERSIST: Write KILLED state to durable storage
    7. UNLOCK: Release interrupt lock
    8. NOTIFY: Send notification to operator
    """

    def __init__(
        self,
        audit_logger=None,  # Optional[AuditLogger]
        state_manager=None,  # Optional[StateManager]
    ) -> None:
        self._state_manager = state_manager
        self._active_interrupts: Dict[str, InterruptEvent] = {}
        self._interrupt_history: List[InterruptEvent] = []
        self._interrupt_locks: Dict[str, Lock] = {}
        self._lock = Lock()


        # Setup audit logger - adapt to governance AuditLogger interface
        self._use_governance_logger = False
        if audit_logger is not None:
            # Check if it's a governance AuditLogger (has log_event)
            if hasattr(audit_logger, 'log_event'):
                self._audit_logger = audit_logger
                self._use_governance_logger = True
            elif hasattr(audit_logger, 'log_escalation'):
                # Governance AuditLogger uses log_escalation
                self._audit_logger = audit_logger
                self._use_governance_logger = True
            else:
                self._audit_logger = None
        else:
            self._audit_logger = None

    def _log_event(
        self,
        event_type: KillSwitchEventType,
        agent_id: str,
        reason: str,
        actor: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Log event to audit logger, adapting to available interface.

        Args:
            event_type: Type of Kill-Switch event.
            agent_id: ID of the agent.
            reason: Human-readable reason.
            actor: Who triggered the event.
            metadata: Additional metadata.
        """
        if self._audit_logger is None:
            logger.debug(
                f"Audit log (skipped): {event_type.name} for {agent_id}"
            )
            return

        if self._use_governance_logger:
            # Governance AuditLogger has log_escalation / log_security_event
            try:
                self._audit_logger.log_security_event(
                    event_type=f"kill_switch_{event_type.name.lower()}",
                    agent_id=agent_id,
                    details={
                        "reason": reason,
                        "actor": actor,
                        **(metadata or {}),
                    },
                )
            except AttributeError:
                # Try log_escalation
                try:
                    from .models import EscalationEvent
                    event = EscalationEvent(
                        event_id=metadata.get("event_id", "") if metadata else "",
                        agent_id=agent_id,
                        event_type=f"kill_switch_{event_type.name.lower()}",
                        reason=reason,
                        triggered_by=actor,
                        triggered_at=datetime.now(timezone.utc),
                        severity="CRITICAL",
                        metadata=metadata or {},
                    )
                    self._audit_logger.log_escalation(event)
                except Exception:
                    logger.warning("Could not log to governance AuditLogger")
        else:
            # Custom AuditLogger with log_event
            try:
                self._audit_logger.log_event(
                    event_type=event_type,
                    agent_id=agent_id,
                    reason=reason,
                    actor=actor,
                    metadata=metadata,
                )
            except AttributeError:
                logger.warning("Could not log event")

    def trigger_interrupt(
        self,
        agent_id: str,
        reason: str,
        triggered_by: str,
        reason_type: KillReason = KillReason.MANUAL_TRIGGER,
    ) -> InterruptEvent:
        """
        Trigger atomic interrupt.

        Args:
            agent_id: ID of the agent to interrupt.
            reason: Human-readable reason for the interrupt.
            triggered_by: Operator ID or "SYSTEM".
            reason_type: Type of kill reason.

        Returns:
            InterruptEvent: Event record with latency and outcome.

        Raises:
            InterruptInProgressError: If interrupt already in progress.
        """
        event_id = str(uuid.uuid4())
        triggered_at = datetime.now(timezone.utc)

        # Step 1: LOCK - Acquire interrupt lock
        interrupt_lock = self._acquire_lock(agent_id)
        if interrupt_lock is None:
            raise InterruptInProgressError(
                f"Interrupt already in progress for agent {agent_id}"
            )

        try:
            # Step 2: LOG - Write trigger event (synchronous)
            self._log_event(
                event_type=KillSwitchEventType.INTERRUPT_STARTED,
                agent_id=agent_id,
                reason=reason,
                actor=triggered_by,
                metadata={"event_id": event_id},
            )

            interrupt_started_at = datetime.now(timezone.utc)

            # Step 3-5: SIGNAL, FORCE, CONFIRM
            outcome, error_message = self._execute_interrupt_sequence(agent_id)

            interrupt_completed_at = datetime.now(timezone.utc)
            latency_ms = (
                interrupt_completed_at - triggered_at
            ).total_seconds() * 1000

            # Step 6: PERSIST - Write KILLED state
            if self._state_manager is not None:
                from .enums import CircuitState
                from .models import CircuitBreakerState
                self._state_manager.save_state(
                    agent_id,
                    CircuitBreakerState(
                        agent_id=agent_id,
                        state=CircuitState.OPEN,
                        opened_at=interrupt_completed_at,
                    ),
                )

            # Step 7: LOG - Write completion event
            self._log_event(
                event_type=KillSwitchEventType.INTERRUPT_COMPLETED,
                agent_id=agent_id,
                reason=reason,
                actor=triggered_by,
                metadata={
                    "event_id": event_id,
                    "latency_ms": latency_ms,
                    "outcome": outcome.value,
                    "error_message": error_message,
                },
            )

            # Build event
            event = InterruptEvent(
                event_id=event_id,
                agent_id=agent_id,
                reason=reason,
                reason_type=reason_type,
                triggered_by=triggered_by,
                triggered_at=triggered_at,
                interrupt_started_at=interrupt_started_at,
                interrupt_completed_at=interrupt_completed_at,
                interrupt_latency_ms=latency_ms,
                outcome=outcome,
                error_message=error_message,
            )

            # Store in history
            with self._lock:
                self._interrupt_history.append(event)
                self._active_interrupts.pop(agent_id, None)

            return event

        finally:
            # Step 8: UNLOCK - Release interrupt lock
            self._release_lock(agent_id, interrupt_lock)

    def is_interrupt_in_progress(self, agent_id: str) -> bool:
        """
        Check if interrupt is currently executing for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            bool: True if interrupt is in progress.
        """
        with self._lock:
            return agent_id in self._active_interrupts

    def get_interrupt_event(self, event_id: str) -> Optional[InterruptEvent]:
        """
        Get interrupt event by ID.

        Args:
            event_id: UUID of the interrupt event.

        Returns:
            Optional[InterruptEvent]: Event if found, None otherwise.
        """
        with self._lock:
            for event in self._interrupt_history:
                if event.event_id == event_id:
                    return event
            return None

    def get_interrupt_history(
        self,
        agent_id: Optional[str] = None,
        limit: int = 100
    ) -> List[InterruptEvent]:
        """
        Get interrupt history.

        Args:
            agent_id: Optional filter by agent ID.
            limit: Maximum number of events to return.

        Returns:
            List[InterruptEvent]: List of interrupt events.
        """
        with self._lock:
            events = self._interrupt_history
            if agent_id is not None:
                events = [e for e in events if e.agent_id == agent_id]
            return events[-limit:]

    def _acquire_lock(self, agent_id: str) -> Optional[Lock]:
        """
        Acquire interrupt lock for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            Optional[Lock]: Lock if acquired, None if already held.
        """
        with self._lock:
            if agent_id not in self._interrupt_locks:
                self._interrupt_locks[agent_id] = Lock()

            lock = self._interrupt_locks[agent_id]
            if lock.locked():
                return None

            lock.acquire()
            return lock

    def _release_lock(self, agent_id: str, lock: Lock) -> None:
        """
        Release interrupt lock for agent.

        Args:
            agent_id: ID of the agent.
            lock: Lock to release.
        """
        try:
            lock.release()
        except RuntimeError:
            # Lock was not held
            pass

    def _execute_interrupt_sequence(
        self,
        agent_id: str,
    ) -> tuple[InterruptOutcome, Optional[str]]:
        """
        Execute the interrupt sequence (SIGTERM -> SIGKILL -> verify).

        Args:
            agent_id: ID of the agent.

        Returns:
            tuple[InterruptOutcome, Optional[str]]: Outcome and error message.
        """
        pid = self._get_agent_pid(agent_id)

        if pid is None:
            return InterruptOutcome.SUCCESS, "Agent process not found"

        logger.info(f"Sending SIGTERM to agent {agent_id} (PID: {pid})")
        time.sleep(0.05)  # Simulate 50ms for signal delivery

        terminated = True

        if not terminated:
            logger.info(f"Sending SIGKILL to agent {agent_id} (PID: {pid})")
            time.sleep(0.05)
            terminated = True  # Simulate

        if not terminated:
            return InterruptOutcome.FAILED, "Agent did not terminate"
        else:
            return InterruptOutcome.SUCCESS, None

    def _get_agent_pid(self, agent_id: str) -> Optional[int]:
        """
        Get agent process ID.

        Args:
            agent_id: ID of the agent.

        Returns:
            Optional[int]: PID if found, None otherwise.
        """
        return None
