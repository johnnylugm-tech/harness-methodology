"""
HealthMonitor Component.

Continuously collects and buffers agent health metrics (error rate, latency,
memory, output rate, responsiveness).
"""

import random
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List

from .exceptions import AgentNotFoundError, MetricsUnavailableError
from .models import HealthMetrics, MonitorConfig


class HealthMonitor:
    """
    Continuously collect and buffer agent health metrics.

    For testing purposes, this implementation generates simulated metrics.
    In production, this would read from actual agent process metrics.
    """

    def __init__(self) -> None:
        self._metrics_buffer: Dict[str, List[HealthMetrics]] = {}
        self._monitoring_config: Dict[str, MonitorConfig] = {}
        self._active_monitors: set = set()
        self._lock = Lock()

    def start_monitoring(self, agent_id: str, config: MonitorConfig) -> None:
        """
        Start monitoring an agent with given config.

        Args:
            agent_id: ID of the agent to monitor.
            config: Monitoring configuration with thresholds.

        Raises:
            AgentNotFoundError: If agent_id is invalid.
        """
        if not agent_id:
            raise AgentNotFoundError("Agent ID cannot be empty")

        with self._lock:
            self._monitoring_config[agent_id] = config
            self._active_monitors.add(agent_id)
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []

    def stop_monitoring(self, agent_id: str) -> None:
        """
        Stop monitoring an agent.

        Args:
            agent_id: ID of the agent to stop monitoring.
        """
        with self._lock:
            self._active_monitors.discard(agent_id)
            self._monitoring_config.pop(agent_id, None)

    def get_metrics(self, agent_id: str) -> HealthMetrics:
        """
        Get current health metrics for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            HealthMetrics: Current health metrics.

        Raises:
            MetricsUnavailableError: If metrics cannot be retrieved.
            AgentNotFoundError: If agent is not being monitored.
        """
        if agent_id not in self._active_monitors:
            raise AgentNotFoundError(f"Agent {agent_id} is not being monitored")

        if agent_id not in self._metrics_buffer:
            raise MetricsUnavailableError(f"No metrics available for {agent_id}")

        # Generate simulated metrics for testing
        return self._generate_simulated_metrics(agent_id)

    def is_monitoring(self, agent_id: str) -> bool:
        """
        Check if agent is being monitored.

        Args:
            agent_id: ID of the agent.

        Returns:
            bool: True if agent is being monitored.
        """
        return agent_id in self._active_monitors

    def _generate_simulated_metrics(self, agent_id: str) -> HealthMetrics:
        """
        Generate simulated metrics for testing purposes.

        Args:
            agent_id: ID of the agent.

        Returns:
            HealthMetrics: Simulated health metrics.
        """
        now = datetime.now(timezone.utc)
        return HealthMetrics(
            agent_id=agent_id,
            error_rate=random.uniform(0.0, 0.15),
            latency_p99_ms=random.uniform(100.0, 6000.0),
            memory_usage_percent=random.uniform(30.0, 95.0),
            output_rate_kbps=random.uniform(10.0, 150.0),
            last_health_check=now,
            timestamp=now,
        )

    def record_metrics(self, agent_id: str, metrics: HealthMetrics) -> None:
        """
        Record health metrics for an agent (for external metric sources).

        Args:
            agent_id: ID of the agent.
            metrics: Health metrics to record.
        """
        with self._lock:
            if agent_id not in self._metrics_buffer:
                self._metrics_buffer[agent_id] = []
            self._metrics_buffer[agent_id].append(metrics)

            # Keep only recent metrics (last 100)
            if len(self._metrics_buffer[agent_id]) > 100:
                self._metrics_buffer[agent_id] = \
                    self._metrics_buffer[agent_id][-100:]
