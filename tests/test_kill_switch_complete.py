"""
tests/test_kill_switch_complete.py — Complete kill_switch/ coverage (W2).

Covers: CircuitBreaker, StateManager, HealthMonitor, InterruptEngine, KillSwitch facade.
"""
import pytest
from unittest.mock import MagicMock, patch

from kill_switch.enums import CircuitState, KillReason
from kill_switch.exceptions import AgentNotFoundError, StatePersistenceError, InterruptInProgressError
from kill_switch.models import (
    CircuitBreakerState, HealthMetrics, MonitorConfig
)

pytestmark = pytest.mark.mutation_oracle


# ===========================================================================
# CircuitBreaker
# ===========================================================================

class TestCircuitBreaker:
    def _cb(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        return CircuitBreaker()

    def test_default_failure_threshold(self):
        cb = self._cb()
        assert cb.failure_threshold == 5

    def test_initialize_circuit_returns_state(self):
        cb = self._cb()
        state = cb.initialize_circuit("a1")
        assert state.agent_id == "a1"
        assert state.state == CircuitState.CLOSED

    def test_get_state_unknown_agent_returns_closed(self):
        cb = self._cb()
        assert cb.get_state("unknown") == CircuitState.CLOSED

    def test_get_failure_count_unknown_agent_zero(self):
        cb = self._cb()
        assert cb.get_failure_count("unknown") == 0

    def test_is_open_unknown_agent_false(self):
        cb = self._cb()
        assert cb.is_open("unknown") is False

    def test_record_success_raises_if_not_initialized(self):
        cb = self._cb()
        with pytest.raises(AgentNotFoundError):
            cb.record_success("ghost")

    def test_record_failure_raises_if_not_initialized(self):
        cb = self._cb()
        with pytest.raises(AgentNotFoundError):
            cb.record_failure("ghost")

    def test_record_failure_increments_count(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb.record_failure("a1")
        assert cb.get_failure_count("a1") == 1

    def test_record_failure_exceeds_threshold(self):
        cb = self._cb()
        cb.failure_threshold = 3
        cb.initialize_circuit("a1")
        cb.record_failure("a1")
        cb.record_failure("a1")
        assert cb.get_state("a1") == CircuitState.CLOSED  # count=2
        from kill_switch.exceptions import CircuitBreakerError
        with pytest.raises(CircuitBreakerError):
            cb.record_failure("a1")  # count=3 (>= 3) triggers threshold
        assert cb.get_state("a1") == CircuitState.OPEN

    def test_record_success_clears_half_open(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb.open_circuit("a1", cooldown_seconds=0)
        # Force to HALF_OPEN
        cb._circuits["a1"].state = CircuitState.HALF_OPEN
        
        from datetime import datetime, timezone
        before = datetime.now(timezone.utc)
        cb.record_success("a1")
        after = datetime.now(timezone.utc)
        
        assert cb.get_state("a1") == CircuitState.CLOSED
        assert cb.get_failure_count("a1") == 0
        state = cb._circuits["a1"]
        assert state.last_success_time is not None
        assert before <= state.last_success_time <= after
        assert state.closed_at is not None
        assert before <= state.closed_at <= after

    def test_record_failure_in_half_open_reopens(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb._circuits["a1"].state = CircuitState.HALF_OPEN
        cb.record_failure("a1")
        assert cb.get_state("a1") == CircuitState.OPEN

    def test_open_circuit_sets_open_state(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb.open_circuit("a1", cooldown_seconds=300)
        assert cb.get_state("a1") == CircuitState.OPEN

    def test_open_circuit_creates_if_not_initialized(self):
        cb = self._cb()
        cb.open_circuit("new_agent", cooldown_seconds=60)
        assert cb.get_state("new_agent") == CircuitState.OPEN

    def test_open_circuit_default_cooldown(self):
        cb = self._cb()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        cb.open_circuit("default_agent")
        state = cb._circuits["default_agent"]
        diff = (state.cooldown_end - now).total_seconds()
        assert 59 <= diff <= 61

    def test_is_open_true_while_in_cooldown(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb.open_circuit("a1", cooldown_seconds=300)
        assert cb.is_open("a1") is True

    def test_is_open_transitions_to_half_open_after_cooldown(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb.open_circuit("a1", cooldown_seconds=0)
        import time
        time.sleep(0.01)
        # After cooldown, should be HALF_OPEN (not OPEN)
        result = cb.is_open("a1")
        assert result is False  # half-open means allow probe
        assert cb.get_state("a1") == CircuitState.HALF_OPEN

    def test_is_open_false_when_state_closed(self):
        cb = self._cb()
        cb.initialize_circuit("a1")  # state defaults to CLOSED
        assert cb.is_open("a1") is False

    def test_is_open_true_when_open_no_cooldown(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        cb._circuits["a1"].state = CircuitState.OPEN
        cb._circuits["a1"].cooldown_end = None
        assert cb.is_open("a1") is True

    def test_get_state_known_agent(self):
        cb = self._cb()
        cb.initialize_circuit("a1")
        assert cb.get_state("a1") == CircuitState.CLOSED

    def test_should_trigger_error_rate(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone
        cb = CircuitBreaker()
        cb.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        metrics = HealthMetrics("a1", error_rate=0.5, latency_p99_ms=100.0,
                                memory_usage_percent=50.0, output_rate_kbps=10.0,
                                last_health_check=now, timestamp=now)
        config = MonitorConfig("a1", error_rate_threshold=0.10)
        assert cb.should_trigger("a1", metrics, config) is True

    def test_should_trigger_false_when_healthy(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone
        cb = CircuitBreaker()
        cb.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        metrics = HealthMetrics("a1", error_rate=0.01, latency_p99_ms=100.0,
                                memory_usage_percent=50.0, output_rate_kbps=10.0,
                                last_health_check=now, timestamp=now)
        config = MonitorConfig("a1")
        assert cb.should_trigger("a1", metrics, config) is False

    def test_should_trigger_boundaries(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone
        cb = CircuitBreaker()
        now = datetime.now(timezone.utc)
        
        # Test error_rate exactly at threshold
        metrics = HealthMetrics("a1", error_rate=0.10, latency_p99_ms=100.0,
                                memory_usage_percent=50.0, output_rate_kbps=10.0,
                                last_health_check=now, timestamp=now)
        config = MonitorConfig("a1", error_rate_threshold=0.10)
        assert cb.should_trigger("a1", metrics, config) is False  # Must strictly exceed
        
        metrics.error_rate = 0.10001
        assert cb.should_trigger("a1", metrics, config) is True

    def test_should_trigger_latency(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone
        cb = CircuitBreaker()
        cb.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        metrics = HealthMetrics("a1", error_rate=0.01, latency_p99_ms=9999.0,
                                memory_usage_percent=50.0, output_rate_kbps=10.0,
                                last_health_check=now, timestamp=now)
        config = MonitorConfig("a1", latency_p99_threshold_ms=5000.0)
        assert cb.should_trigger("a1", metrics, config) is True

    def test_should_trigger_memory(self):
        from kill_switch.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone
        cb = CircuitBreaker()
        cb.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        metrics = HealthMetrics("a1", error_rate=0.01, latency_p99_ms=100.0,
                                memory_usage_percent=99.0, output_rate_kbps=10.0,
                                last_health_check=now, timestamp=now)
        config = MonitorConfig("a1", memory_usage_threshold=90.0)
        assert cb.should_trigger("a1", metrics, config) is True


# ===========================================================================
# StateManager
# ===========================================================================

class TestStateManager:
    def _sm(self, tmp_path):
        from kill_switch.state_manager import StateManager
        return StateManager(state_path=tmp_path / "ks_state")

    def test_save_and_load_state(self, tmp_path):
        sm = self._sm(tmp_path)
        state = CircuitBreakerState(agent_id="a1", state=CircuitState.OPEN, failure_count=3)
        sm.save_state("a1", state)
        loaded = sm.load_state("a1")
        assert loaded is not None
        assert loaded.agent_id == "a1"
        assert loaded.state == CircuitState.OPEN
        assert loaded.failure_count == 3

    def test_load_state_nonexistent_returns_none(self, tmp_path):
        sm = self._sm(tmp_path)
        assert sm.load_state("ghost") is None

    def test_is_agent_killed_true_when_open(self, tmp_path):
        sm = self._sm(tmp_path)
        state = CircuitBreakerState(agent_id="a1", state=CircuitState.OPEN)
        sm.save_state("a1", state)
        assert sm.is_agent_killed("a1") is True

    def test_is_agent_killed_false_when_closed(self, tmp_path):
        sm = self._sm(tmp_path)
        state = CircuitBreakerState(agent_id="a1", state=CircuitState.CLOSED)
        sm.save_state("a1", state)
        assert sm.is_agent_killed("a1") is False

    def test_is_agent_killed_false_when_no_state(self, tmp_path):
        sm = self._sm(tmp_path)
        assert sm.is_agent_killed("unknown") is False

    def test_clear_state_removes_file(self, tmp_path):
        sm = self._sm(tmp_path)
        state = CircuitBreakerState(agent_id="a1", state=CircuitState.OPEN)
        sm.save_state("a1", state)
        sm.clear_state("a1")
        assert sm.load_state("a1") is None

    def test_clear_state_nonexistent_no_error(self, tmp_path):
        sm = self._sm(tmp_path)
        sm.clear_state("ghost")  # should not raise

    def test_load_state_from_cache(self, tmp_path):
        sm = self._sm(tmp_path)
        state = CircuitBreakerState(agent_id="a2", state=CircuitState.CLOSED)
        sm.save_state("a2", state)
        # Second load should come from cache
        loaded = sm.load_state("a2")
        assert loaded is not None

    def test_save_raises_on_bad_path(self, tmp_path):
        from kill_switch.state_manager import StateManager
        state_dir = tmp_path / "ks_state2"
        state_dir.mkdir(parents=True)
        (state_dir / "a1.json").mkdir()  # make the path a directory, not a file
        sm = StateManager(state_path=state_dir)
        state = CircuitBreakerState(agent_id="a1")
        with pytest.raises(StatePersistenceError):
            sm.save_state("a1", state)

    def test_load_corrupted_raises(self, tmp_path):
        sm = self._sm(tmp_path)
        state_dir = tmp_path / "ks_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "bad.json").write_text("NOT JSON", encoding="utf-8")
        with pytest.raises(StatePersistenceError):
            sm.load_state("bad")

    def test_state_to_dict_round_trip(self, tmp_path):
        sm = self._sm(tmp_path)
        from datetime import datetime, timezone
        state = CircuitBreakerState(
            agent_id="x", state=CircuitState.OPEN, failure_count=2,
            last_failure_time=datetime.now(timezone.utc),
        )
        d = sm._state_to_dict(state)
        restored = sm._dict_to_state(d)
        assert restored.agent_id == "x"
        assert restored.state == CircuitState.OPEN
        assert restored.failure_count == 2


# ===========================================================================
# HealthMonitor
# ===========================================================================

class TestHealthMonitor:
    def _hm(self):
        from kill_switch.health_monitor import HealthMonitor
        return HealthMonitor()

    def test_start_monitoring_registers_agent(self):
        hm = self._hm()
        config = MonitorConfig("a1")
        hm.start_monitoring("a1", config)
        assert hm.is_monitoring("a1") is True

    def test_stop_monitoring_deregisters(self):
        hm = self._hm()
        config = MonitorConfig("a1")
        hm.start_monitoring("a1", config)
        hm.stop_monitoring("a1")
        assert hm.is_monitoring("a1") is False

    def test_get_metrics_raises_not_monitored(self):
        from kill_switch.exceptions import AgentNotFoundError
        hm = self._hm()
        with pytest.raises(AgentNotFoundError):
            hm.get_metrics("not_monitored")

    def test_get_metrics_returns_health_metrics(self):
        hm = self._hm()
        hm.start_monitoring("a1", MonitorConfig("a1"))
        metrics = hm.get_metrics("a1")
        assert metrics.agent_id == "a1"
        assert 0.0 <= metrics.error_rate <= 1.0
        assert metrics.latency_p99_ms >= 0.0

    def test_start_empty_id_raises(self):
        from kill_switch.exceptions import AgentNotFoundError
        hm = self._hm()
        with pytest.raises(AgentNotFoundError):
            hm.start_monitoring("", MonitorConfig(""))

    def test_record_metrics_stored(self):
        from datetime import datetime, timezone
        hm = self._hm()
        hm.start_monitoring("a1", MonitorConfig("a1"))
        now = datetime.now(timezone.utc)
        m = HealthMetrics("a1", error_rate=0.1, latency_p99_ms=200.0,
                          memory_usage_percent=60.0, output_rate_kbps=50.0,
                          last_health_check=now, timestamp=now)
        hm.record_metrics("a1", m)
        assert len(hm._metrics_buffer["a1"]) == 1

    def test_record_metrics_caps_at_100(self):
        from datetime import datetime, timezone
        hm = self._hm()
        hm.start_monitoring("a1", MonitorConfig("a1"))
        now = datetime.now(timezone.utc)
        for _ in range(105):
            m = HealthMetrics("a1", error_rate=0.0, latency_p99_ms=100.0,
                              memory_usage_percent=50.0, output_rate_kbps=10.0,
                              last_health_check=now, timestamp=now)
            hm.record_metrics("a1", m)
        assert len(hm._metrics_buffer["a1"]) == 100

    def test_is_monitoring_false_for_unknown(self):
        hm = self._hm()
        assert hm.is_monitoring("unknown") is False

    def test_get_metrics_raises_when_no_buffer(self):
        from kill_switch.exceptions import MetricsUnavailableError
        hm = self._hm()
        hm.start_monitoring("a1", MonitorConfig("a1"))
        hm._metrics_buffer.pop("a1", None)  # remove buffer
        with pytest.raises(MetricsUnavailableError):
            hm.get_metrics("a1")

    def test_record_metrics_creates_buffer_for_new_agent(self):
        from datetime import datetime, timezone
        hm = self._hm()
        now = datetime.now(timezone.utc)
        m = HealthMetrics("new_agent", error_rate=0.1, latency_p99_ms=200.0,
                          memory_usage_percent=60.0, output_rate_kbps=50.0,
                          last_health_check=now, timestamp=now)
        hm.record_metrics("new_agent", m)
        assert "new_agent" in hm._metrics_buffer
        assert len(hm._metrics_buffer["new_agent"]) == 1


# ===========================================================================
# InterruptEngine
# ===========================================================================

class TestInterruptEngine:
    def _engine(self, audit_logger=None, state_manager=None):
        from kill_switch.interrupt_engine import InterruptEngine
        return InterruptEngine(audit_logger=audit_logger, state_manager=state_manager)

    def test_trigger_interrupt_returns_event(self):
        engine = self._engine()
        event = engine.trigger_interrupt("a1", "test reason", "operator1")
        assert event.agent_id == "a1"
        assert event.reason == "test reason"
        assert event.triggered_by == "operator1"

    def test_trigger_interrupt_stores_in_history(self):
        engine = self._engine()
        engine.trigger_interrupt("a1", "reason", "op")
        history = engine.get_interrupt_history()
        assert len(history) == 1

    def test_trigger_interrupt_custom_reason_type(self):
        engine = self._engine()
        event = engine.trigger_interrupt("a1", "r", "op", reason_type=KillReason.ERROR_RATE_EXCEEDED)
        assert event.reason_type == KillReason.ERROR_RATE_EXCEEDED

    def test_trigger_interrupt_raises_if_in_progress(self):
        engine = self._engine()
        # Simulate in-progress by acquiring the lock manually
        engine.trigger_interrupt("a1", "first", "op")
        # second call to same agent after first completes — should NOT raise (lock released)
        # Instead test the lock mechanism via _acquire_lock
        from threading import Lock
        lock = Lock()
        lock.acquire()
        engine._interrupt_locks["a2"] = lock
        with pytest.raises(InterruptInProgressError):
            engine.trigger_interrupt("a2", "second", "op")
        lock.release()

    def test_get_interrupt_history_filtered_by_agent(self):
        engine = self._engine()
        engine.trigger_interrupt("a1", "r", "op")
        engine.trigger_interrupt("a2", "r", "op")
        history_a1 = engine.get_interrupt_history(agent_id="a1")
        assert all(e.agent_id == "a1" for e in history_a1)

    def test_get_interrupt_history_respects_limit(self):
        engine = self._engine()
        for _ in range(5):
            engine.trigger_interrupt("a1", "r", "op")
        history = engine.get_interrupt_history(limit=3)
        assert len(history) == 3

    def test_is_interrupt_in_progress_false_after_complete(self):
        engine = self._engine()
        engine.trigger_interrupt("a1", "r", "op")
        # After completion lock is released
        assert engine.is_interrupt_in_progress("a1") is False

    def test_trigger_with_audit_logger(self):
        audit = MagicMock()
        audit.log_event = MagicMock()
        engine = self._engine(audit_logger=audit)
        engine.trigger_interrupt("a1", "r", "op")
        assert audit.log_event.call_count >= 1

    def test_audit_log_event_exception_handled(self):
        audit = MagicMock()
        audit.log_event = MagicMock(side_effect=RuntimeError("audit failed"))
        engine = self._engine(audit_logger=audit)
        # Should not raise despite audit failure
        event = engine.trigger_interrupt("a1", "r", "op")
        assert event.agent_id == "a1"

    def test_release_lock_runtime_error_handled(self):
        from threading import Lock
        engine = self._engine()
        lock = Lock()  # un-acquired lock → release raises RuntimeError
        engine._release_lock("a1", lock)  # should not raise

    def test_execute_interrupt_agent_not_found(self):
        engine = self._engine()
        outcome, msg = engine._execute_interrupt_sequence("unknown")
        assert outcome is not None
        assert msg == "Agent process not found"

    def test_execute_interrupt_agent_found(self):
        engine = self._engine()
        with patch.object(engine, '_get_agent_pid', return_value=12345):
            outcome, msg = engine._execute_interrupt_sequence("a1")
        assert outcome is not None
        assert msg is None

    def test_trigger_with_state_manager(self, tmp_path):
        from kill_switch.state_manager import StateManager
        sm = StateManager(state_path=tmp_path / "state")
        engine = self._engine(state_manager=sm)
        engine.trigger_interrupt("a1", "r", "op")
        assert sm.is_agent_killed("a1") is True

    def test_trigger_event_has_latency(self):
        engine = self._engine()
        event = engine.trigger_interrupt("a1", "r", "op")
        assert event.interrupt_latency_ms is not None
        assert event.interrupt_latency_ms >= 0


# ===========================================================================
# KillSwitch (facade)
# ===========================================================================

class TestKillSwitchFacade:
    def _ks(self, tmp_path=None):
        from kill_switch.kill_switch import KillSwitch
        from kill_switch.state_manager import StateManager
        if tmp_path:
            sm = StateManager(state_path=tmp_path / "state")
            return KillSwitch(state_manager=sm)
        return KillSwitch()

    def test_start_monitoring_and_is_healthy(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1"))
        health = ks.get_agent_health("a1")
        assert health is not None

    def test_stop_monitoring(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1"))
        ks.stop_monitoring("a1")
        # After stop, get_metrics would raise
        from kill_switch.exceptions import AgentNotFoundError
        with pytest.raises(AgentNotFoundError):
            ks.get_agent_health("a1")

    def test_is_agent_circuit_open_default_false(self, tmp_path):
        ks = self._ks(tmp_path)
        assert ks.is_agent_circuit_open("unknown") is False

    def test_get_agent_state_default_closed(self, tmp_path):
        ks = self._ks(tmp_path)
        assert ks.get_agent_state("unknown") == CircuitState.CLOSED

    def test_manual_trigger_returns_event(self, tmp_path):
        ks = self._ks(tmp_path)
        event = ks.manual_trigger("a1", "manual test", "operator")
        assert event.agent_id == "a1"

    def test_re_enable_closed_circuit_returns_true(self, tmp_path):
        ks = self._ks(tmp_path)
        result = ks.re_enable("a1", "admin", "ack")
        assert result is True

    def test_re_enable_after_manual_trigger(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.manual_trigger("a1", "reason", "op")
        result = ks.re_enable("a1", "admin", "ack")
        assert result is True

    def test_get_interrupt_history_empty(self, tmp_path):
        ks = self._ks(tmp_path)
        history = ks.get_interrupt_history()
        assert isinstance(history, list)

    def test_get_interrupt_history_after_trigger(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.manual_trigger("a1", "r", "op")
        history = ks.get_interrupt_history("a1")
        assert len(history) == 1

    def test_check_alias_works(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1"))
        result = ks.check("a1", MonitorConfig("a1"))
        assert isinstance(result, bool)

    def test_evaluate_and_trigger_returns_false_no_breach(self, tmp_path):
        from datetime import datetime, timezone
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1"))
        # Inject safe metrics
        now = datetime.now(timezone.utc)
        safe_m = HealthMetrics("a1", error_rate=0.0, latency_p99_ms=100.0,
                               memory_usage_percent=50.0, output_rate_kbps=10.0,
                               last_health_check=now, timestamp=now)
        ks.health_monitor.record_metrics("a1", safe_m)
        with patch.object(ks.health_monitor, 'get_metrics', return_value=safe_m):
            with patch.object(ks.circuit_breaker, 'should_trigger', return_value=False):
                result = ks.evaluate_and_trigger("a1", MonitorConfig("a1"))
        assert result is False

    def test_evaluate_and_trigger_handles_metrics_error(self, tmp_path):
        ks = self._ks(tmp_path)
        # a1 not monitored -> get_metrics raises -> evaluate_and_trigger returns False
        result = ks.evaluate_and_trigger("unmonitored", MonitorConfig("unmonitored"))
        assert result is False

    def test_evaluate_and_trigger_opens_circuit_on_threshold(self, tmp_path):
        from datetime import datetime, timezone
        from unittest.mock import patch
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1", failure_threshold=1))
        ks.circuit_breaker.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        bad_m = HealthMetrics("a1", error_rate=0.5, latency_p99_ms=100.0,
                              memory_usage_percent=50.0, output_rate_kbps=10.0,
                              last_health_check=now, timestamp=now)
        with patch.object(ks.health_monitor, 'get_metrics', return_value=bad_m):
            with patch.object(ks.circuit_breaker, 'should_trigger', return_value=True):
                result = ks.evaluate_and_trigger("a1", MonitorConfig("a1", failure_threshold=1))
        assert result is True

    def test_start_monitoring_default_config(self, tmp_path):
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1")
        assert ks.health_monitor.is_monitoring("a1") is True

    def test_evaluate_and_trigger_interrupt_in_progress_handled(self, tmp_path):
        from datetime import datetime, timezone
        from kill_switch.exceptions import InterruptInProgressError
        ks = self._ks(tmp_path)
        ks.start_monitoring("a1", MonitorConfig("a1", failure_threshold=1))
        ks.circuit_breaker.initialize_circuit("a1")
        now = datetime.now(timezone.utc)
        bad_m = HealthMetrics("a1", error_rate=0.5, latency_p99_ms=100.0,
                              memory_usage_percent=50.0, output_rate_kbps=10.0,
                              last_health_check=now, timestamp=now)
        with patch.object(ks.health_monitor, 'get_metrics', return_value=bad_m):
            with patch.object(ks.circuit_breaker, 'should_trigger', return_value=True):
                with patch.object(ks.interrupt_engine, 'trigger_interrupt',
                                  side_effect=InterruptInProgressError("in progress")):
                    result = ks.evaluate_and_trigger("a1", MonitorConfig("a1", failure_threshold=1))
        assert result is False


# ===========================================================================
# IssueTrackerExt
# ===========================================================================

class TestIssueTrackerExt:
    def test_add_finding_without_fr_id(self):
        from harness.issue_tracker_ext import IssueTrackerExt
        tracker = IssueTrackerExt()
        fid = tracker.add_finding("quality", "high", "foo.py", 1, "msg", "evidence")
        assert fid is not None
        assert len(tracker.open_issues()) == 1

    def test_add_finding_with_fr_id(self):
        from harness.issue_tracker_ext import IssueTrackerExt
        tracker = IssueTrackerExt()
        fid = tracker.add_finding("quality", "high", "foo.py", 1, "msg", "ev", fr_id="FR-01")
        issues = tracker.get_findings_by_fr("FR-01")
        assert len(issues) == 1
        assert issues[0]["id"] == fid

    def test_get_findings_by_fr_empty(self):
        from harness.issue_tracker_ext import IssueTrackerExt
        tracker = IssueTrackerExt()
        assert tracker.get_findings_by_fr("FR-99") == []



    def test_fr_coverage_summary(self):
        from harness.issue_tracker_ext import IssueTrackerExt
        tracker = IssueTrackerExt()
        tracker.add_finding("q", "h", "f.py", 1, "m", "e", fr_id="FR-01")
        tracker.add_finding("q", "h", "f.py", 2, "m", "e", fr_id="FR-01")
        tracker.add_finding("q", "h", "f.py", 3, "m", "e", fr_id="FR-02")
        summary = tracker.fr_coverage_summary()
        assert summary["FR-01"] == 2
        assert summary["FR-02"] == 1
        assert "FR-03" not in summary  # no findings → not included

    def test_multiple_fr_tags_on_same_issue(self):
        from harness.issue_tracker_ext import IssueTrackerExt
        tracker = IssueTrackerExt()
        fid = tracker.add_finding("q", "h", "f.py", 1, "m", "e", fr_id="FR-01")
        # Manually add second FR tag
        for issue in tracker.open_issues():
            if issue["id"] == fid:
                issue["fr_ids"].append("FR-02")
        assert len(tracker.get_findings_by_fr("FR-01")) == 1
        assert len(tracker.get_findings_by_fr("FR-02")) == 1


# ===========================================================================
# CRGBridge
# ===========================================================================

class TestCRGBridge:
    """CRG is mandatory — tests verify core bridge behavior with mcp_tools mocked."""

    def test_refresh_graph_calls_build_with_incremental(self):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.reset_mock()
        bridge = CRGBridge()
        bridge.refresh_graph("/tmp/project")
        mock_mcp.mcp__code_review_graph__build_or_update_graph_tool.assert_called_once_with(
            repo_root="/tmp/project", full_rebuild=False
        )

    def test_run_reconnaissance_when_available(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.reset_mock()
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_reconnaissance.json").write_text('{"modules": 5}', encoding="utf-8")
        result = CRGBridge().run_reconnaissance(str(tmp_path))
        assert result == {"modules": 5}
        mock_mcp.mcp__code_review_graph__build_or_update_graph_tool.assert_called_once_with(
            repo_root=str(tmp_path), full_rebuild=True
        )

    def test_get_minimal_context_parses_json_output(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.mcp__code_review_graph__get_minimal_context_tool.return_value = {
            "hint": "use lazy loading"
        }
        result = CRGBridge().get_minimal_context(str(tmp_path), "architecture")
        assert result["hint"] == "use lazy loading"

    def test_check_impact_true_when_high_risk(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.85
        }
        result = CRGBridge().check_impact(str(tmp_path))
        assert result is True

    def test_check_drift_true_when_high_risk(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.85
        }
        assert CRGBridge().check_drift(str(tmp_path), threshold=0.4) is True

    def test_check_drift_false_when_low_risk(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        import sys
        mock_mcp = sys.modules["mcp_tools"]
        mock_mcp.mcp__code_review_graph__detect_changes_tool.return_value = {
            "risk_score": 0.1
        }
        assert CRGBridge().check_drift(str(tmp_path), threshold=0.4) is False

    def test_load_metrics_returns_data(self, tmp_path):
        from harness.crg_bridge import CRGBridge
        sessi_work = tmp_path / ".sessi-work"
        sessi_work.mkdir()
        (sessi_work / "crg_metrics.json").write_text(
            '{"score": 0.85}', encoding="utf-8"
        )
        bridge = CRGBridge()
        data = bridge.load_metrics(str(tmp_path))
        assert data["score"] == 0.85
