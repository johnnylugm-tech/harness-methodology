"""
HealthMonitor Component.

Continuously collects and buffers agent health metrics.
"""

from threading import Lock
from typing import Dict, List

from .exceptions import AgentNotFoundError, MetricsUnavailableError
from .models import HealthMetrics, MonitorConfig


class HealthMonitor:
    """Continuously collect and buffer agent health metrics."""

    def __init__(self) -> None:
        """Initialize instance."""
        self._metrics_buffer: Dict[str, List[HealthMetrics]] = {}
        self._monitoring_config: Dict[str, MonitorConfig] = {}
        self._active_monitors: set = set()
        self._lock = Lock()

    def start_monitoring(self, agent_id: str, config: MonitorConfig) -> None:
        """Start monitoring."""
        if not agent_id:
            raise AgentNotFoundError("Agent ID cannot be empty")
        with self._lock:
            self._monitoring_config[agent_id] = config
            self._active_monitors.add(agent_id)
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []

    def stop_monitoring(self, agent_id: str) -> None:
        """Stop monitoring."""
        with self._lock:
            self._active_monitors.discard(agent_id)
            self._monitoring_config.pop(agent_id, None)

    def get_metrics(self, agent_id: str) -> HealthMetrics:
        """Get most recent recorded metric. Raises if not monitored or no data."""
        with self._lock:
            if agent_id not in self._active_monitors:
                raise AgentNotFoundError(f"Agent {agent_id} is not being monitored")
            buf = self._metrics_buffer.get(agent_id, [])
            if not buf:
                raise MetricsUnavailableError(f"No metrics available for {agent_id}")
            return buf[-1]

    def is_monitoring(self, agent_id: str) -> bool:
        """Is monitoring."""
        with self._lock:
            return agent_id in self._active_monitors

    def record_metrics(self, agent_id: str, metrics: HealthMetrics) -> None:
        """Record metrics."""
        with self._lock:
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []
            self._metrics_buffer[agent_id].append(metrics)
            if len(self._metrics_buffer[agent_id]) > 100:
                self._metrics_buffer[agent_id] = self._metrics_buffer[agent_id][-100:]
