"""Tests for kill_switch/state_manager.py — persistent circuit breaker state."""

from datetime import datetime, timezone
from kill_switch.enums import CircuitState
from kill_switch.models import CircuitBreakerState
from kill_switch.state_manager import StateManager, DEFAULT_STATE_PATH


class TestStateManagerInit:
    def test_default_path(self):
        sm = StateManager()
        assert sm._state_path == DEFAULT_STATE_PATH

    def test_custom_path(self, tmp_path):
        sm = StateManager(state_path=tmp_path / "custom")
        assert sm._state_path == tmp_path / "custom"


class TestStateToDict:
    def test_full_state(self):
        sm = StateManager()
        now = datetime.now(timezone.utc)
        state = CircuitBreakerState(
            agent_id="agent1", state=CircuitState.HALF_OPEN,
            failure_count=3, last_failure_time=now,
            cooldown_end=now, last_success_time=now,
            opened_at=now, closed_at=None,
        )
        d = sm._state_to_dict(state)
        assert d["agent_id"] == "agent1"
        assert d["state"] == 3  # HALF_OPEN.value
        assert d["failure_count"] == 3
        assert d["last_failure_time"] is not None
        assert d["closed_at"] is None

    def test_state_with_none_times(self):
        sm = StateManager()
        state = CircuitBreakerState(
            agent_id="agent1", state=CircuitState.CLOSED,
            failure_count=0, last_failure_time=None,
            cooldown_end=None, last_success_time=None,
            opened_at=None, closed_at=None,
        )
        d = sm._state_to_dict(state)
        assert d["last_failure_time"] is None


class TestDictToState:
    def test_roundtrip(self):
        sm = StateManager()
        now = datetime.now(timezone.utc)
        original = CircuitBreakerState(
            agent_id="agent1", state=CircuitState.OPEN,
            failure_count=5, last_failure_time=now,
            cooldown_end=now, last_success_time=None,
            opened_at=now, closed_at=None,
        )
        d = sm._state_to_dict(original)
        restored = sm._dict_to_state(d)
        assert restored.agent_id == original.agent_id
        assert restored.state == original.state
        assert restored.failure_count == original.failure_count

    def test_parse_z_suffix(self):
        sm = StateManager()
        d = {
            "agent_id": "a1", "state": 1, "failure_count": 0,  # CLOSED = 1
            "last_failure_time": "2025-01-01T00:00:00Z",
            "cooldown_end": None, "last_success_time": None,
            "opened_at": None, "closed_at": None,
        }
        state = sm._dict_to_state(d)
        assert state.last_failure_time is not None

    def test_minimal_data(self):
        sm = StateManager()
        d = {"agent_id": "a1"}
        state = sm._dict_to_state(d)
        assert state.agent_id == "a1"
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0


class TestSaveLoadClear:
    def test_save_and_load(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.CLOSED, failure_count=0)
        sm.save_state("agent1", state)
        loaded = sm.load_state("agent1")
        assert loaded is not None
        assert loaded.agent_id == "agent1"

    def test_load_nonexistent(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        assert sm.load_state("nonexistent") is None

    def test_clear_state(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.CLOSED, failure_count=0)
        sm.save_state("agent1", state)
        sm.clear_state("agent1")
        assert sm.load_state("agent1") is None

    def test_cache_hit(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.CLOSED, failure_count=0)
        sm.save_state("agent1", state)
        # Second load should hit cache
        loaded = sm.load_state("agent1")
        assert loaded is not None


class TestIsAgentKilled:
    def test_no_state(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        assert sm.is_agent_killed("agent1") is False

    def test_closed_not_killed(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.CLOSED, failure_count=0)
        sm.save_state("agent1", state)
        assert sm.is_agent_killed("agent1") is False

    def test_open_is_killed(self, tmp_path):
        sm = StateManager(state_path=tmp_path)
        state = CircuitBreakerState(agent_id="agent1", state=CircuitState.OPEN, failure_count=5)
        sm.save_state("agent1", state)
        assert sm.is_agent_killed("agent1") is True
