# tests/test_retry_utils.py
import time
import pytest
from harness.retry_utils import retry_with_backoff, _compute_delay


# ── _compute_delay ──────────────────────────────────────────────────────────

class TestComputeDelay:
    def test_first_attempt_uses_base_delay(self):
        # attempt=1 → base * 2^0 = base (before jitter)
        delay = _compute_delay(1, base=4.0, cap=60.0, jitter=0.0)
        assert delay == pytest.approx(4.0)

    def test_second_attempt_doubles(self):
        delay = _compute_delay(2, base=4.0, cap=60.0, jitter=0.0)
        assert delay == pytest.approx(8.0)

    def test_capped_at_max_delay(self):
        delay = _compute_delay(10, base=1.0, cap=5.0, jitter=0.0)
        assert delay == pytest.approx(5.0)

    def test_jitter_stays_within_bounds(self):
        for _ in range(50):
            delay = _compute_delay(1, base=4.0, cap=60.0, jitter=0.25)
            assert 3.0 <= delay <= 5.0


# ── retry_with_backoff ───────────────────────────────────────────────────────

class TestRetryWithBackoff:
    def test_success_on_first_attempt(self):
        calls = []
        result = retry_with_backoff(lambda: calls.append(1) or "ok", max_attempts=3)
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_transient_exception(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("transient")
            return "success"

        result = retry_with_backoff(
            flaky,
            max_attempts=3,
            base_delay=0.0,
            jitter=0.0,
        )
        assert result == "success"
        assert len(calls) == 3

    def test_raises_after_max_attempts(self):
        def always_fails():
            raise OSError("boom")

        with pytest.raises(OSError, match="boom"):
            retry_with_backoff(always_fails, max_attempts=3, base_delay=0.0, jitter=0.0)

    def test_non_retryable_exception_propagates_immediately(self):
        calls = []

        def raises_value_error():
            calls.append(1)
            raise ValueError("not transient")

        with pytest.raises(ValueError):
            retry_with_backoff(raises_value_error, max_attempts=5, base_delay=0.0, jitter=0.0)

        assert len(calls) == 1  # must not retry

    def test_custom_retryable_predicate(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("custom transient")
            return "done"

        result = retry_with_backoff(
            flaky,
            max_attempts=3,
            base_delay=0.0,
            jitter=0.0,
            retryable=lambda exc: isinstance(exc, RuntimeError),
        )
        assert result == "done"
        assert len(calls) == 2

    def test_on_retry_callback_invoked(self):
        retries = []

        def flaky():
            if len(retries) < 1:
                raise OSError("transient")
            return "ok"

        retry_with_backoff(
            flaky,
            max_attempts=3,
            base_delay=0.0,
            jitter=0.0,
            on_retry=lambda attempt, exc, wait: retries.append((attempt, str(exc))),
        )
        assert retries == [(1, "transient")]

    def test_max_attempts_one_does_not_retry(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise OSError("boom")

        with pytest.raises(OSError):
            retry_with_backoff(always_fails, max_attempts=1, base_delay=0.0, jitter=0.0)
        assert len(calls) == 1

    def test_invalid_max_attempts_raises(self):
        with pytest.raises(ValueError):
            retry_with_backoff(lambda: None, max_attempts=0)
