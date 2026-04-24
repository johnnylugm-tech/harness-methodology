"""
Kill-Switch Circuit Breaker.

A critical safety mechanism that provides emergency interruption capability
for AI agents. Implements a circuit breaker pattern that detects anomalous
agent behavior and can interrupt a runaway agent within 5 seconds.

Usage:
    from implement.kill_switch import KillSwitch

    ks = KillSwitch()
    ks.start_monitoring("agent-1")
    ks.manual_trigger("agent-1", "Suspicious behavior", "operator-1")
"""

from .audit_logger import AuditLogger
from .circuit_breaker import CircuitBreaker
from .enums import (
    CircuitState,
    InterruptOutcome,
    KillReason,
    KillSwitchEventType,
)
from .exceptions import (
    AgentNotFoundError,
    CircuitBreakerError,
    InterruptInProgressError,
    KillSwitchError,
    MetricsUnavailableError,
    StatePersistenceError,
)
from .health_monitor import HealthMonitor
from .interrupt_engine import InterruptEngine
from .kill_switch import KillSwitch
from .models import (
    CircuitBreakerState,
    HealthMetrics,
    InterruptEvent,
    MonitorConfig,
)
from .state_manager import StateManager

__all__ = [
    # Re-exported from Feature #3
    "AuditLogger",
    # Enums
    "CircuitState",
    "InterruptOutcome",
    "KillReason",
    "KillSwitchEventType",
    # Exceptions
    "AgentNotFoundError",
    "CircuitBreakerError",
    "InterruptInProgressError",
    "KillSwitchError",
    "MetricsUnavailableError",
    "StatePersistenceError",
    # Models
    "CircuitBreakerState",
    "HealthMetrics",
    "InterruptEvent",
    "MonitorConfig",
    # Components
    "CircuitBreaker",
    "HealthMonitor",
    "InterruptEngine",
    "StateManager",
    # Facade
    "KillSwitch",
]
