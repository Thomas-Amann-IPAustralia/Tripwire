"""
tests/test_retry.py

Tests for src/retry.py — exponential backoff retry logic.
Uses monkeypatch to eliminate real sleeps and validate call counts.
"""

import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.errors import RetryableError, PermanentError, TripwireError
from src.retry import retry_call, with_retry, with_retry_from_config, _backoff_delay


# ---------------------------------------------------------------------------
# retry_call — success on first attempt
# ---------------------------------------------------------------------------


def test_retry_call_success_first_attempt():
    func = MagicMock(return_value="ok")
    result = retry_call(func, "arg1", max_retries=3, base_delay=0)
    assert result == "ok"
    func.assert_called_once_with("arg1")


def test_retry_call_passes_args_and_kwargs():
    func = MagicMock(return_value=42)
    result = retry_call(func, 1, 2, max_retries=0, base_delay=0, kw="val")
    assert result == 42
    func.assert_called_once_with(1, 2, kw="val")


# ---------------------------------------------------------------------------
# retry_call — retryable failures
# ---------------------------------------------------------------------------


def test_retry_call_retries_on_retryable_error():
    outcomes = [RetryableError("fail 1"), RetryableError("fail 2"), "success"]

    def func():
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("src.retry.time.sleep"):
        result = retry_call(func, max_retries=3, base_delay=0.001)

    assert result == "success"


def test_retry_call_exhausts_retries_and_raises_last():
    func = MagicMock(side_effect=RetryableError("always fails"))

    with patch("src.retry.time.sleep"):
        with pytest.raises(RetryableError, match="always fails"):
            retry_call(func, max_retries=2, base_delay=0.001)

    # Called max_retries + 1 times total.
    assert func.call_count == 3


def test_retry_call_zero_retries_raises_immediately():
    func = MagicMock(side_effect=RetryableError("fail"))

    with pytest.raises(RetryableError):
        retry_call(func, max_retries=0, base_delay=0)

    func.assert_called_once()


# ---------------------------------------------------------------------------
# retry_call — permanent failures never retry
# ---------------------------------------------------------------------------


def test_retry_call_does_not_retry_permanent_error():
    func = MagicMock(side_effect=PermanentError("permanent"))

    with pytest.raises(PermanentError, match="permanent"):
        retry_call(func, max_retries=5, base_delay=0)

    func.assert_called_once()


def test_retry_call_does_not_retry_generic_exception():
    func = MagicMock(side_effect=ValueError("unexpected"))

    with pytest.raises(ValueError, match="unexpected"):
        retry_call(func, max_retries=5, base_delay=0)

    func.assert_called_once()


# ---------------------------------------------------------------------------
# retry_call — sleep timing
# ---------------------------------------------------------------------------


def test_retry_call_sleeps_between_retries():
    func = MagicMock(side_effect=[RetryableError("fail"), "ok"])
    sleep_calls = []

    with patch("src.retry.time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        result = retry_call(func, max_retries=3, base_delay=2.0)

    assert result == "ok"
    assert len(sleep_calls) == 1
    # First retry delay should be approximately base_delay * 2**0 = 2.0 s (plus jitter ≤ 0.2)
    assert 2.0 <= sleep_calls[0] <= 2.3


def test_retry_call_backoff_increases():
    """Verify that delay roughly doubles each attempt."""
    func = MagicMock(side_effect=[
        RetryableError("1"), RetryableError("2"), RetryableError("3"), "ok"
    ])
    sleep_calls = []

    with patch("src.retry.time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        retry_call(func, max_retries=5, base_delay=1.0)

    assert len(sleep_calls) == 3
    # Each delay should be larger than the previous.
    assert sleep_calls[1] > sleep_calls[0]
    assert sleep_calls[2] > sleep_calls[1]


# ---------------------------------------------------------------------------
# with_retry decorator
# ---------------------------------------------------------------------------


def test_with_retry_decorator_success():
    @with_retry(max_retries=3, base_delay=0)
    def always_succeeds():
        return "done"

    assert always_succeeds() == "done"


def test_with_retry_decorator_retries():
    call_count = {"n": 0}

    @with_retry(max_retries=2, base_delay=0.001)
    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RetryableError("not yet")
        return "finally"

    with patch("src.retry.time.sleep"):
        result = flaky()

    assert result == "finally"
    assert call_count["n"] == 3


def test_with_retry_decorator_preserves_function_name():
    @with_retry(max_retries=1, base_delay=0)
    def my_function():
        pass

    assert my_function.__name__ == "my_function"


def test_with_retry_decorator_raises_on_exhaustion():
    @with_retry(max_retries=1, base_delay=0.001)
    def always_fails():
        raise RetryableError("always")

    with patch("src.retry.time.sleep"):
        with pytest.raises(RetryableError):
            always_fails()


# ---------------------------------------------------------------------------
# with_retry_from_config decorator
# ---------------------------------------------------------------------------


def test_with_retry_from_config_reads_params():
    config = {
        "pipeline": {
            "max_retries": 2,
            "retry_base_delay_seconds": 0.001,
        }
    }
    call_count = {"n": 0}

    @with_retry_from_config(config)
    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise RetryableError("not yet")
        return "ok"

    with patch("src.retry.time.sleep"):
        result = flaky()

    assert result == "ok"


def test_with_retry_from_config_uses_defaults_when_keys_missing():
    """Decorator should not crash when pipeline section is absent."""
    config = {}

    @with_retry_from_config(config)
    def func():
        return "default"

    assert func() == "default"


# ---------------------------------------------------------------------------
# _backoff_delay helper
# ---------------------------------------------------------------------------


def test_backoff_delay_increases_with_attempt():
    with patch("src.retry.random.uniform", return_value=0):
        d0 = _backoff_delay(2.0, 0)
        d1 = _backoff_delay(2.0, 1)
        d2 = _backoff_delay(2.0, 2)
    assert d0 < d1 < d2
    assert d0 == pytest.approx(2.0)
    assert d1 == pytest.approx(4.0)
    assert d2 == pytest.approx(8.0)


def test_backoff_delay_jitter_within_bounds():
    delays = [_backoff_delay(2.0, 0) for _ in range(100)]
    # All delays should be in [2.0, 2.0 + 2.0 * 0.1]
    for d in delays:
        assert 2.0 <= d <= 2.21  # 2.0 * 0.1 = 0.2, slight tolerance for float
