"""
KillSwitch Facade Class.

Main Kill-Switch facade that coordinates all components.
"""

import logging
from typing import List, Optional

from .circuit_breaker import CircuitBreaker
from .enums import CircuitState, KillReason
from .exceptions import CircuitBreakerError, InterruptInProgressError
from .health_monitor import HealthMonitor
from .interrupt_engine import InterruptEngine
from .models import InterruptEvent, MonitorConfig
from .state_manager import StateManager

logger = logging.getLogger(__name__)


class KillSwitch:
    """Main Kill-Switch facade class."""

    def __init__(self, audit_logger=None, state_manager: Optional[StateManager] = None) -> None:
        """Initialize instance."""
        self.health_monitor = HealthMonitor()
        self.circuit_breaker = CircuitBreaker()
        self.state_manager = state_manager or StateManager()
        self.interrupt_engine = InterruptEngine(
            audit_logger=audit_logger,
            state_manager=self.state_manager,
        )

    def start_monitoring(self, agent_id: str, config: Optional[MonitorConfig] = None) -> None:
        """Start monitoring."""
        if config is None:
            config = MonitorConfig(agent_id=agent_id)
        self.health_monitor.start_monitoring(agent_id, config)
        self.circuit_breaker.initialize_circuit(agent_id)

    def stop_monitoring(self, agent_id: str) -> None:
        """Stop monitoring."""
        self.health_monitor.stop_monitoring(agent_id)

    def get_agent_health(self, agent_id: str):
        """Get agent health."""
        return self.health_monitor.get_metrics(agent_id)

    def is_agent_circuit_open(self, agent_id: str) -> bool:
        """Is agent circuit open."""
        if self.state_manager.is_agent_killed(agent_id):
            return True
        return self.circuit_breaker.is_open(agent_id)

    def get_agent_state(self, agent_id: str) -> CircuitState:
        """Get agent state."""
        return self.circuit_breaker.get_state(agent_id)

    def get_registered_agents(self) -> list[str]:
        """Return list of registered agent IDs."""
        return self.circuit_breaker.list_circuits()

    def manual_trigger(self, agent_id: str, reason: str, operator_id: str) -> InterruptEvent:
        """Manual trigger."""
        logger.info(f"Manual Kill-Switch triggered for {agent_id} by {operator_id}: {reason}")
        return self.interrupt_engine.trigger_interrupt(
            agent_id=agent_id, reason=reason,
            triggered_by=operator_id, reason_type=KillReason.MANUAL_TRIGGER,
        )

    def re_enable(self, agent_id: str, operator_id: str, acknowledgment: str) -> bool:
        """Re enable."""
        if not self.is_agent_circuit_open(agent_id):
            return True
        logger.info(f"Re-enabling agent {agent_id} by {operator_id}. Ack: {acknowledgment}")
        self.state_manager.clear_state(agent_id)
        self.circuit_breaker.initialize_circuit(agent_id)
        self.health_monitor.stop_monitoring(agent_id)
        return True

    def get_interrupt_history(self, agent_id: Optional[str] = None,
                               limit: int = 100) -> List[InterruptEvent]:
        """Get interrupt history."""
        return self.interrupt_engine.get_interrupt_history(agent_id=agent_id, limit=limit)

    def check(self, agent_id: str, config: MonitorConfig) -> bool:
        """Alias for evaluate_and_trigger (contract API)."""
        return self.evaluate_and_trigger(agent_id, config)

    def evaluate_and_trigger(self, agent_id: str, config: MonitorConfig) -> bool:
        """Evaluate and trigger."""
        try:
            metrics = self.health_monitor.get_metrics(agent_id)
        except Exception as e:
            logger.warning(f"Could not get metrics for {agent_id}: {e}")
            return False

        if self.circuit_breaker.should_trigger(agent_id, metrics, config):
            try:
                self.circuit_breaker.record_failure(agent_id)
            except CircuitBreakerError:
                pass  # threshold exceeded; circuit already OPEN
            if self.circuit_breaker.get_failure_count(agent_id) >= config.failure_threshold:
                self.circuit_breaker.open_circuit(agent_id, cooldown_seconds=config.cooldown_seconds)
                try:
                    self.interrupt_engine.trigger_interrupt(
                        agent_id=agent_id, reason=f"Threshold exceeded: {config}",
                        triggered_by="SYSTEM", reason_type=KillReason.ERROR_RATE_EXCEEDED,
                    )
                    return True
                except InterruptInProgressError:
                    return False
        else:
            self.circuit_breaker.record_success(agent_id)
        return False
