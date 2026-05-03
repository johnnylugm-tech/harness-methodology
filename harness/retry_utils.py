"""
harness/retry_utils.py — Exponential backoff retry utilities.

Usage::

    from harness.retry_utils import retry_with_backoff

    result = retry_with_backoff(
        lambda: subprocess.run(["ruff", "check", "."], capture_output=True),
        max_attempts=3,
        base_delay=1.0,
        retryable=lambda exc: isinstance(exc, (OSError, subprocess.TimeoutExpired)),
    )
"""
from __future__ import annotations

import random
import time
from typing import Callable, Optional, Tuple, Type, TypeVar

T = TypeVar("T")

#: Default exceptions that trigger a retry.
_TRANSIENT_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    OSError,
    TimeoutError,
)


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    retryable: Optional[Callable[[Exception], bool]] = None,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> T:
    """
    Call *fn* up to *max_attempts* times with exponential back-off + jitter.

    Parameters
    ----------
    fn:
        Zero-argument callable to execute.
    max_attempts:
        Maximum number of total attempts (must be ≥ 1).
    base_delay:
        Seconds to wait before the *second* attempt.
        Subsequent waits follow ``base_delay * 2^(attempt-1)``.
    max_delay:
        Upper bound on the computed delay (before jitter).
    jitter:
        Fraction of the computed delay added/subtracted randomly
        (0.25 = ±25 %).  Use 0 to disable.
    retryable:
        Optional predicate ``(exc) -> bool``.  If provided, only exceptions
        for which it returns True trigger a retry; other exceptions propagate
        immediately.  Default: retries on ``OSError`` and ``TimeoutError``.
    on_retry:
        Optional callback invoked just before each retry:
        ``(attempt_number, exception, wait_seconds) -> None``.
        Useful for logging.

    Returns
    -------
    T
        Return value of *fn* on success.

    Raises
    ------
    Exception
        The last exception raised by *fn* after all attempts are exhausted,
        or the first non-retryable exception.

    Examples
    --------
    >>> import subprocess
    >>> result = retry_with_backoff(
    ...     lambda: subprocess.run(["true"], check=True),
    ...     max_attempts=2,
    ... )
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    _is_retryable = retryable or (lambda exc: isinstance(exc, _TRANSIENT_EXCEPTIONS))

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if not _is_retryable(exc):
                raise
            last_exc = exc
            if attempt == max_attempts:
                break  # no more retries
            delay = _compute_delay(attempt, base_delay, max_delay, jitter)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            else:
                print(
                    f"  [retry] attempt {attempt}/{max_attempts} failed "
                    f"({type(exc).__name__}: {exc}); retrying in {delay:.1f}s"
                )
            time.sleep(delay)

    assert last_exc is not None  # unreachable without exception
    raise last_exc


def _compute_delay(attempt: int, base: float, cap: float, jitter: float) -> float:
    """Compute capped exponential delay with uniform jitter."""
    raw = min(base * (2 ** (attempt - 1)), cap)
    spread = raw * jitter
    return raw + random.uniform(-spread, spread)  # noqa: S311
