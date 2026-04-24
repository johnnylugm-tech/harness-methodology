"""
StateManager Component.

Persists Kill-Switch state across agent restarts — killed agents stay killed
until explicitly re-enabled.
"""

import json
import logging
import shutil
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

from .enums import CircuitState
from .exceptions import StatePersistenceError
from .models import CircuitBreakerState

logger = logging.getLogger(__name__)

# Default state storage path
DEFAULT_STATE_PATH = Path.home() / ".methodology" / "kill_switch" / "state"


class StateManager:
    """
    Persist circuit breaker state across agent restarts.

    State is stored in JSON files at:
    ~/.methodology/kill_switch/state/<agent_id>.json
    """

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._state_path = state_path or DEFAULT_STATE_PATH
        self._state_cache: Dict[str, CircuitBreakerState] = {}
        self._lock = Lock()
        self._ensure_state_dir()

    def _ensure_state_dir(self) -> None:
        """Ensure state directory exists."""
        try:
            self._state_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                f"Could not create state directory {self._state_path}: {e}"
            )

    def save_state(
        self,
        agent_id: str,
        state: CircuitBreakerState,
    ) -> None:
        """
        Persist circuit breaker state for agent.

        Args:
            agent_id: ID of the agent.
            state: Circuit breaker state to persist.

        Raises:
            StatePersistenceError: If state cannot be saved.
        """
        with self._lock:
            self._state_cache[agent_id] = state

            state_file = self._state_path / f"{agent_id}.json"
            try:
                with open(state_file, "w") as f:
                    json.dump(self._state_to_dict(state), f, indent=2)
            except OSError as e:
                raise StatePersistenceError(
                    f"Could not save state for {agent_id}: {e}"
                )

    def load_state(self, agent_id: str) -> Optional[CircuitBreakerState]:
        """
        Load persisted state for agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            Optional[CircuitBreakerState]: State if found, None otherwise.

        Raises:
            StatePersistenceError: If state file is corrupted.
        """
        with self._lock:
            # Check cache first
            if agent_id in self._state_cache:
                return self._state_cache[agent_id]

            state_file = self._state_path / f"{agent_id}.json"
            if not state_file.exists():
                return None

            try:
                with open(state_file) as f:
                    data = json.load(f)
                state = self._dict_to_state(data)
                self._state_cache[agent_id] = state
                return state
            except json.JSONDecodeError as e:
                # Backup corrupted file
                backup_path = state_file.with_suffix(".json.bak")
                try:
                    shutil.move(str(state_file), str(backup_path))
                except OSError:
                    pass
                logger.warning(
                    f"Corrupted state file for {agent_id}, "
                    f"moved to {backup_path}"
                )
                raise StatePersistenceError(
                    f"Corrupted state file for {agent_id}: {e}"
                )
            except OSError as e:
                raise StatePersistenceError(
                    f"Could not load state for {agent_id}: {e}"
                )

    def clear_state(self, agent_id: str) -> None:
        """
        Clear persisted state for agent.

        Args:
            agent_id: ID of the agent.
        """
        with self._lock:
            self._state_cache.pop(agent_id, None)

            state_file = self._state_path / f"{agent_id}.json"
            try:
                if state_file.exists():
                    state_file.unlink()
            except OSError as e:
                logger.warning(f"Could not clear state for {agent_id}: {e}")

    def is_agent_killed(self, agent_id: str) -> bool:
        """
        Check if agent is in KILLED state.

        Args:
            agent_id: ID of the agent.

        Returns:
            bool: True if agent is killed (circuit OPEN).
        """
        state = self.load_state(agent_id)
        if state is None:
            return False
        return state.state == CircuitState.OPEN

    def _state_to_dict(self, state: CircuitBreakerState) -> dict:
        """
        Convert CircuitBreakerState to dict for JSON serialization.

        Args:
            state: Circuit breaker state.

        Returns:
            dict: Serialized state.
        """
        return {
            "agent_id": state.agent_id,
            "state": state.state.value,
            "failure_count": state.failure_count,
            "last_failure_time": (
                state.last_failure_time.isoformat()
                if state.last_failure_time else None
            ),
            "cooldown_end": (
                state.cooldown_end.isoformat()
                if state.cooldown_end else None
            ),
            "last_success_time": (
                state.last_success_time.isoformat()
                if state.last_success_time else None
            ),
            "opened_at": (
                state.opened_at.isoformat()
                if state.opened_at else None
            ),
            "closed_at": (
                state.closed_at.isoformat()
                if state.closed_at else None
            ),
        }

    def _dict_to_state(self, data: dict) -> CircuitBreakerState:
        """
        Convert dict back to CircuitBreakerState.

        Args:
            data: Serialized state dict.

        Returns:
            CircuitBreakerState: Deserialized state.
        """
        from datetime import datetime

        def parse_dt(value: Optional[str]) -> Optional[datetime]:
            if value is None:
                return None
            # Parse ISO format without dateutil - use fromisoformat
            # Handle timezone suffix
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            return datetime.fromisoformat(value)

        return CircuitBreakerState(
            agent_id=data["agent_id"],
            state=CircuitState(data.get("state", CircuitState.CLOSED.value)),
            failure_count=data.get("failure_count", 0),
            last_failure_time=parse_dt(data.get("last_failure_time")),
            cooldown_end=parse_dt(data.get("cooldown_end")),
            last_success_time=parse_dt(data.get("last_success_time")),
            opened_at=parse_dt(data.get("opened_at")),
            closed_at=parse_dt(data.get("closed_at")),
        )
