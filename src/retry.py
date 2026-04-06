"""
src/retry.py

Exponential backoff retry decorator and helper for the Tripwire pipeline.

Retries are only attempted for RetryableError exceptions.  PermanentError
and any other exception propagates immediately.

Configuration (from tripwire_config.yaml):
  pipeline.max_retries              — maximum retry attempts (default 3)
  pipeline.retry_base_delay_seconds — base delay in seconds (default 2.0)

Backoff formula:
  delay = base_delay * (2 ** attempt) + jitter
  jitter = random float in [0, base_delay * 0.1]

This gives delays of approximately:
  Attempt 1: ~2 s
  Attempt 2: ~4 s
  Attempt 3: ~8 s
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Any, Callable, TypeVar

from src.errors import RetryableError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Sentinel used when no config is provided to retry_call.
_UNSET = object()


# ---------------------------------------------------------------------------
# Core retry function
# ---------------------------------------------------------------------------


def retry_call(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call *func* with retries on RetryableError.

    Parameters
    ----------
    func:
        The callable to invoke.
    *args:
        Positional arguments forwarded to *func*.
    max_retries:
        Maximum number of retry attempts (not counting the initial attempt).
        Total calls = max_retries + 1.
    base_delay:
        Base delay in seconds. Each successive retry waits approximately
        ``base_delay * 2**attempt`` seconds.
    **kwargs:
        Keyword arguments forwarded to *func*.

    Returns
    -------
    Any
        The return value of *func* on success.

    Raises
    ------
    RetryableError
        If all retries are exhausted and the last attempt still failed.
    Any other exception
        Propagated immediately without retrying.
    """
    last_exc: RetryableError | None = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except RetryableError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = _backoff_delay(base_delay, attempt)
            logger.warning(
                "RetryableError on attempt %d/%d for %s: %s — retrying in %.1f s",
                attempt + 1,
                max_retries + 1,
                getattr(func, "__name__", repr(func)),
                exc,
                delay,
            )
            time.sleep(delay)

    logger.error(
        "All %d attempt(s) failed for %s: %s",
        max_retries + 1,
        getattr(func, "__name__", repr(func)),
        last_exc,
    )
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Decorator interface
# ---------------------------------------------------------------------------


def with_retry(
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> Callable[[F], F]:
    """Decorator that wraps a function with retry-on-RetryableError logic.

    Usage
    -----
    @with_retry(max_retries=3, base_delay=2.0)
    def fetch_page(url: str) -> str:
        ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return retry_call(func, *args, max_retries=max_retries,
                              base_delay=base_delay, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def with_retry_from_config(config: dict[str, Any]) -> Callable[[F], F]:
    """Decorator variant that reads retry parameters from the loaded config dict."""
    pipeline = config.get("pipeline", {})
    max_retries = int(pipeline.get("max_retries", 3))
    base_delay = float(pipeline.get("retry_base_delay_seconds", 2.0))
    return with_retry(max_retries=max_retries, base_delay=base_delay)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backoff_delay(base: float, attempt: int) -> float:
    """Compute the delay for *attempt* (0-indexed) with jitter."""
    delay = base * (2 ** attempt)
    jitter = random.uniform(0, base * 0.1)
    return delay + jitter
