"""
Kill-Switch Circuit Breaker Custom Exceptions.

Defines all exception types raised by the Kill-Switch system.
"""


class KillSwitchError(Exception):
    """Base exception for all Kill-Switch errors."""
    pass


class InterruptInProgressError(KillSwitchError):
    """Raised when an interrupt is already in progress for an agent."""
    pass


class CircuitBreakerError(KillSwitchError):
    """Raised when a circuit breaker operation fails."""
    pass


class StatePersistenceError(KillSwitchError):
    """Raised when state persistence operations fail."""
    pass


class AgentNotFoundError(KillSwitchError):
    """Raised when an agent cannot be found."""
    pass


class MetricsUnavailableError(KillSwitchError):
    """Raised when health metrics cannot be retrieved."""
    pass
