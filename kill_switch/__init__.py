"""
Kill-Switch Circuit Breaker.

A critical safety mechanism that provides emergency interruption capability
for AI agents. Implements a circuit breaker pattern that detects anomalous
agent behavior and can interrupt a runaway agent within 5 seconds.

Usage:
    from kill_switch import KillSwitch

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
    "AuditLogger",
    "CircuitState",
    "InterruptOutcome",
    "KillReason",
    "KillSwitchEventType",
    "AgentNotFoundError",
    "CircuitBreakerError",
    "InterruptInProgressError",
    "KillSwitchError",
    "MetricsUnavailableError",
    "StatePersistenceError",
    "CircuitBreakerState",
    "HealthMetrics",
    "InterruptEvent",
    "MonitorConfig",
    "CircuitBreaker",
    "HealthMonitor",
    "InterruptEngine",
    "StateManager",
    "KillSwitch",
]
