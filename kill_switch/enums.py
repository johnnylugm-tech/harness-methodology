"""
Kill-Switch Circuit Breaker Enums.

Defines all enum types used throughout the Kill-Switch system.
"""

from enum import Enum, auto


class CircuitState(Enum):
    """Circuit breaker states for agent monitoring."""
    CLOSED = auto()      # Normal operation
    OPEN = auto()        # Kill-Switch triggered
    HALF_OPEN = auto()  # Testing recovery


class KillSwitchEventType(Enum):
    """Types of events logged by the Kill-Switch system."""
    TRIGGERED = auto()
    INTERRUPT_STARTED = auto()
    INTERRUPT_COMPLETED = auto()
    INTERRUPT_FAILED = auto()
    AGENT_KILLED = auto()
    AGENT_RE_ENABLED = auto()
    CIRCUIT_OPENED = auto()
    CIRCUIT_CLOSED = auto()
    CIRCUIT_HALF_OPEN = auto()


class KillReason(Enum):
    """Reasons for Kill-Switch triggering."""
    ERROR_RATE_EXCEEDED = auto()
    LATENCY_EXCEEDED = auto()
    MEMORY_EXCEEDED = auto()
    OUTPUT_EXCEEDED = auto()
    NO_RESPONSE = auto()
    MANUAL_TRIGGER = auto()


class InterruptOutcome(Enum):
    """Possible outcomes of an interrupt attempt."""
    SUCCESS = auto()
    FAILED = auto()
    TIMEOUT = auto()
