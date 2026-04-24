"""
CircuitBreaker Component.

Manages CLOSED/OPEN/HALF_OPEN state machine and triggers Kill-Switch
when thresholds are exceeded.
"""

from datetime import datetime, timezone
from threading import Lock
from typing import Dict

from .enums import CircuitState
from .exceptions import AgentNotFoundError
from .models import CircuitBreakerState, HealthMetrics, MonitorConfig


class CircuitBreaker:
    """
    Manage circuit state machine (CLOSED/OPEN/HALF_OPEN).

    The circuit breaker monitors agent health and opens the circuit
    (triggers Kill-Switch) when thresholds are exceeded.
    """

    def __init__(self) -> None:
        self._circuits: Dict[str, CircuitBreakerState] = {}
        self._lock = Lock()

    def record_success(self, agent_id: str) -> None:
        """
        Record successful operation, reset failure count.

        Args:
            agent_id: ID of the agent.

        Raises:
            AgentNotFoundError: If agent has no circuit state.
        """
        with self._lock:
            if agent_id not in self._circuits:
                raise AgentNotFoundError(f"No circuit state for agent {agent_id}")

            state = self._circuits[agent_id]
            state.last_success_time = datetime.now(timezone.utc)

            if state.state == CircuitState.HALF_OPEN:
                # Successful operation in HALF_OPEN -> close the circuit
                state.state = CircuitState.CLOSED
                state.failure_count = 0
                state.closed_at = datetime.now(timezone.utc)

    def record_failure(self, agent_id: str) -> None:
        """
        Record failed operation, increment failure count.

        Args:
            agent_id: ID of the agent.

        Raises:
            AgentNotFoundError: If agent has no circuit state.
        """
        with self._lock:
            if agent_id not in self._circuits:
                raise AgentNotFoundError(f"No circuit state for agent {agent_id}")

            state = self._circuits[agent_id]
            state.failure_count += 1
            state.last_failure_time = datetime.now(timezone.utc)

            if state.state == CircuitState.HALF_OPEN:
                # Failed operation in HALF_OPEN -> reopen the circuit
                state.state = CircuitState.OPEN

    def is_open(self, agent_id: str) -> bool:
        """
        Check if circuit is OPEN (Kill-Switch should fire).

        Args:
            agent_id: ID of the agent.

        Returns:
            bool: True if circuit is OPEN.
        """
        with self._lock:
            if agent_id not in self._circuits:
                return False

            state = self._circuits[agent_id]
            if state.state != CircuitState.OPEN:
                return False

            # Check if cooldown has elapsed
            if state.cooldown_end is not None:
                if datetime.now(timezone.utc) < state.cooldown_end:
                    return True
                else:
                    # Cooldown elapsed -> move to HALF_OPEN
                    state.state = CircuitState.HALF_OPEN
                    return False

            return True

    def get_state(self, agent_id: str) -> CircuitState:
        """
        Get current circuit state for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            CircuitState: Current circuit state (CLOSED if no state exists).
        """
        with self._lock:
            if agent_id not in self._circuits:
                return CircuitState.CLOSED
            return self._circuits[agent_id].state

    def get_failure_count(self, agent_id: str) -> int:
        """
        Get failure count for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            int: Current failure count (0 if no state exists).
        """
        with self._lock:
            if agent_id not in self._circuits:
                return 0
            return self._circuits[agent_id].failure_count

    def initialize_circuit(self, agent_id: str) -> CircuitBreakerState:
        """
        Initialize circuit state for a new agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            CircuitBreakerState: Initial circuit state.
        """
        with self._lock:
            state = CircuitBreakerState(
                agent_id=agent_id,
                state=CircuitState.CLOSED,
                failure_count=0,
            )
            self._circuits[agent_id] = state
            return state

    def open_circuit(
        self,
        agent_id: str,
        cooldown_seconds: int = 60
    ) -> CircuitBreakerState:
        """
        Open the circuit (trigger Kill-Switch).

        Args:
            agent_id: ID of the agent.
            cooldown_seconds: Seconds before attempting HALF_OPEN.

        Returns:
            CircuitBreakerState: Updated circuit state.
        """
        with self._lock:
            if agent_id not in self._circuits:
                state = CircuitBreakerState(agent_id=agent_id)
                self._circuits[agent_id] = state
            else:
                state = self._circuits[agent_id]

            state.state = CircuitState.OPEN
            state.opened_at = datetime.now(timezone.utc)
            state.cooldown_end = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + cooldown_seconds,
                tz=timezone.utc
            )
            return state

    def should_trigger(
        self,
        agent_id: str,
        metrics: HealthMetrics,
        config: MonitorConfig
    ) -> bool:
        """
        Check if Kill-Switch should be triggered based on metrics.

        Args:
            agent_id: ID of the agent.
            metrics: Current health metrics.
            config: Monitoring configuration.

        Returns:
            bool: True if any threshold is exceeded.
        """
        return (
            metrics.error_rate > config.error_rate_threshold or
            metrics.latency_p99_ms > config.latency_p99_threshold_ms or
            metrics.memory_usage_percent > config.memory_usage_threshold or
            metrics.output_rate_kbps > config.output_rate_threshold_kbps
        )
