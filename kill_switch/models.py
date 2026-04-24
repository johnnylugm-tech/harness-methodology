"""
Kill-Switch Circuit Breaker Data Models.

Defines all dataclass models used by the Kill-Switch system.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import CircuitState, InterruptOutcome, KillReason


@dataclass
class HealthMetrics:
    """Current health metrics for an agent."""
    agent_id: str
    error_rate: float  # 0.0 - 1.0 (percentage of failed ops)
    latency_p99_ms: float  # milliseconds
    memory_usage_percent: float  # 0.0 - 100.0
    output_rate_kbps: float  # KB/s
    last_health_check: datetime
    timestamp: datetime


@dataclass
class CircuitBreakerState:
    """State of the circuit breaker for an agent."""
    agent_id: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    cooldown_end: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


@dataclass
class InterruptEvent:
    """Record of an interrupt event."""
    event_id: str  # UUID
    agent_id: str
    reason: str
    reason_type: KillReason
    triggered_by: str  # operator ID or "SYSTEM"
    triggered_at: datetime
    interrupt_started_at: Optional[datetime] = None
    interrupt_completed_at: Optional[datetime] = None
    interrupt_latency_ms: Optional[float] = None
    outcome: Optional[InterruptOutcome] = None
    error_message: Optional[str] = None


@dataclass
class MonitorConfig:
    """Configuration for agent health monitoring."""
    agent_id: str
    error_rate_threshold: float = 0.10  # > 10%
    error_rate_window_seconds: int = 60  # 1 minute
    latency_p99_threshold_ms: float = 5000.0  # > 5000ms
    latency_window_seconds: int = 300  # 5 minutes
    memory_usage_threshold: float = 90.0  # > 90%
    output_rate_threshold_kbps: float = 100.0  # > 100 KB/s
    output_window_seconds: int = 30  # 30 seconds
    no_response_threshold_seconds: int = 60  # > 60s
    failure_threshold: int = 5  # failures before OPEN
    cooldown_seconds: int = 60  # seconds before HALF_OPEN
