"""
HealthMonitor Component.

Continuously collects and buffers agent health metrics.
"""

import random
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List

from .exceptions import AgentNotFoundError, MetricsUnavailableError
from .models import HealthMetrics, MonitorConfig


class HealthMonitor:
    """Continuously collect and buffer agent health metrics."""

    def __init__(self) -> None:
        self._metrics_buffer: Dict[str, List[HealthMetrics]] = {}
        self._monitoring_config: Dict[str, MonitorConfig] = {}
        self._active_monitors: set = set()
        self._lock = Lock()

    def start_monitoring(self, agent_id: str, config: MonitorConfig) -> None:
        if not agent_id:
            raise AgentNotFoundError("Agent ID cannot be empty")
        with self._lock:
            self._monitoring_config[agent_id] = config
            self._active_monitors.add(agent_id)
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []

    def stop_monitoring(self, agent_id: str) -> None:
        with self._lock:
            self._active_monitors.discard(agent_id)
            self._monitoring_config.pop(agent_id, None)

    def get_metrics(self, agent_id: str) -> HealthMetrics:
        if agent_id not in self._active_monitors:
            raise AgentNotFoundError(f"Agent {agent_id} is not being monitored")
        if agent_id not in self._metrics_buffer:
            raise MetricsUnavailableError(f"No metrics available for {agent_id}")
        return self._generate_simulated_metrics(agent_id)

    def is_monitoring(self, agent_id: str) -> bool:
        return agent_id in self._active_monitors

    def _generate_simulated_metrics(self, agent_id: str) -> HealthMetrics:
        now = datetime.now(timezone.utc)
        return HealthMetrics(
            agent_id=agent_id,
            error_rate=random.uniform(0.0, 0.15),  # nosec B311
            latency_p99_ms=random.uniform(100.0, 6000.0),  # nosec B311
            memory_usage_percent=random.uniform(30.0, 95.0),  # nosec B311
            output_rate_kbps=random.uniform(10.0, 150.0),  # nosec B311
            last_health_check=now,
            timestamp=now,
        )

    def record_metrics(self, agent_id: str, metrics: HealthMetrics) -> None:
        with self._lock:
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []
            self._metrics_buffer[agent_id].append(metrics)
            if len(self._metrics_buffer[agent_id]) > 100:
                self._metrics_buffer[agent_id] = self._metrics_buffer[agent_id][-100:]
