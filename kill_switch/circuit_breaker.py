"""
CircuitBreaker Component.

Manages CLOSED/OPEN/HALF_OPEN state machine and triggers Kill-Switch
when thresholds are exceeded.
"""

from datetime import datetime, timezone
from threading import Lock
from typing import Dict

from .enums import CircuitState
from .exceptions import AgentNotFoundError, CircuitBreakerError
from .models import CircuitBreakerState, HealthMetrics, MonitorConfig


class CircuitBreaker:
    """
    Manage circuit state machine (CLOSED/OPEN/HALF_OPEN).

    The circuit breaker monitors agent health and opens the circuit
    (triggers Kill-Switch) when thresholds are exceeded.
    """

    def __init__(self, failure_threshold: int = 5) -> None:
        """Initialize instance."""
        self._circuits: Dict[str, CircuitBreakerState] = {}
        self._lock = Lock()
        self.failure_threshold = failure_threshold

    def record_success(self, agent_id: str) -> None:
        """Record success."""
        with self._lock:
            if agent_id not in self._circuits:
                raise AgentNotFoundError(f"No circuit state for agent {agent_id}")
            state = self._circuits[agent_id]
            state.last_success_time = datetime.now(timezone.utc)
            if state.state == CircuitState.HALF_OPEN:
                state.state = CircuitState.CLOSED
                state.failure_count = 0
                state.closed_at = datetime.now(timezone.utc)

    def record_failure(self, agent_id: str) -> None:
        """Record failure. Raises CircuitBreakerError when threshold exceeded."""
        with self._lock:
            if agent_id not in self._circuits:
                raise AgentNotFoundError(f"No circuit state for agent {agent_id}")
            state = self._circuits[agent_id]
            state.failure_count += 1
            state.last_failure_time = datetime.now(timezone.utc)
            # Check HALF_OPEN before threshold so both branches are reachable
            if state.state == CircuitState.HALF_OPEN:
                state.state = CircuitState.OPEN
            if state.failure_count >= self.failure_threshold:
                state.state = CircuitState.OPEN
                state.opened_at = datetime.now(timezone.utc)
                raise CircuitBreakerError(
                    f"Circuit OPEN for {agent_id}: "
                    f"failure_count={state.failure_count} >= "
                    f"threshold={self.failure_threshold}"
                )

    def is_open(self, agent_id: str) -> bool:
        """Is open."""
        with self._lock:
            if agent_id not in self._circuits:
                return False
            state = self._circuits[agent_id]
            if state.state != CircuitState.OPEN:
                return False
            if state.cooldown_end is not None:
                if datetime.now(timezone.utc) < state.cooldown_end:
                    return True
                else:
                    state.state = CircuitState.HALF_OPEN
                    return False
            return True

    def get_state(self, agent_id: str) -> CircuitState:
        """Get state."""
        with self._lock:
            if agent_id not in self._circuits:
                return CircuitState.CLOSED
            return self._circuits[agent_id].state

    def get_failure_count(self, agent_id: str) -> int:
        """Get failure count."""
        with self._lock:
            if agent_id not in self._circuits:
                return 0
            return self._circuits[agent_id].failure_count

    def initialize_circuit(self, agent_id: str) -> CircuitBreakerState:
        """Initialize circuit."""
        with self._lock:
            state = CircuitBreakerState(
                agent_id=agent_id,
                state=CircuitState.CLOSED,
                failure_count=0,
            )
            self._circuits[agent_id] = state
            return state

    def open_circuit(self, agent_id: str, cooldown_seconds: int = 60) -> CircuitBreakerState:
        """Open circuit."""
        with self._lock:
            if agent_id not in self._circuits:
                state = CircuitBreakerState(
                    agent_id=agent_id,
                    state=CircuitState.CLOSED,
                    failure_count=0,
                )
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

    def list_circuits(self) -> list[str]:
        """Return list of registered agent IDs."""
        with self._lock:
            return list(self._circuits.keys())

    def should_trigger(self, agent_id: str, metrics: HealthMetrics, config: MonitorConfig) -> bool:
        """Should trigger."""
        return (
            metrics.error_rate > config.error_rate_threshold or
            metrics.latency_p99_ms > config.latency_p99_threshold_ms or
            metrics.memory_usage_percent > config.memory_usage_threshold or
            metrics.output_rate_kbps > config.output_rate_threshold_kbps
        )
