"""
tests/test_errors.py

Tests for src/errors.py — error hierarchy and convenience constructors.
"""

import pytest

from src.errors import (
    TripwireError,
    RetryableError,
    PermanentError,
    http_error,
    captcha_error,
    content_too_short_error,
    dramatic_size_change_error,
    llm_schema_error,
)


# ---------------------------------------------------------------------------
# Class hierarchy
# ---------------------------------------------------------------------------


def test_retryable_error_is_tripwire_error():
    exc = RetryableError("transient failure")
    assert isinstance(exc, TripwireError)
    assert isinstance(exc, Exception)


def test_permanent_error_is_tripwire_error():
    exc = PermanentError("permanent failure")
    assert isinstance(exc, TripwireError)
    assert isinstance(exc, Exception)


def test_retryable_and_permanent_are_distinct():
    assert not issubclass(RetryableError, PermanentError)
    assert not issubclass(PermanentError, RetryableError)


# ---------------------------------------------------------------------------
# Message and context
# ---------------------------------------------------------------------------


def test_error_message_preserved():
    msg = "Something went wrong"
    exc = RetryableError(msg)
    assert str(exc) == msg


def test_error_context_stored():
    exc = PermanentError("error", context={"url": "https://example.com", "code": 404})
    assert exc.context["url"] == "https://example.com"
    assert exc.context["code"] == 404


def test_error_context_defaults_to_empty_dict():
    exc = RetryableError("no context")
    assert exc.context == {}


def test_repr_includes_class_name_and_message():
    exc = PermanentError("bad thing")
    r = repr(exc)
    assert "PermanentError" in r
    assert "bad thing" in r


def test_repr_includes_context_when_present():
    exc = RetryableError("fail", context={"key": "value"})
    r = repr(exc)
    assert "key" in r


# ---------------------------------------------------------------------------
# http_error constructor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_http_5xx_returns_retryable_error(status_code):
    exc = http_error(status_code, "https://example.com")
    assert isinstance(exc, RetryableError)
    assert str(status_code) in str(exc)


def test_http_429_returns_retryable_error():
    exc = http_error(429, "https://example.com")
    assert isinstance(exc, RetryableError)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 410])
def test_http_4xx_returns_permanent_error(status_code):
    exc = http_error(status_code, "https://example.com")
    assert isinstance(exc, PermanentError)
    assert str(status_code) in str(exc)


def test_http_error_context_contains_url_and_status():
    exc = http_error(404, "https://example.com/page")
    assert exc.context["url"] == "https://example.com/page"
    assert exc.context["status_code"] == 404


# ---------------------------------------------------------------------------
# captcha_error constructor
# ---------------------------------------------------------------------------


def test_captcha_error_is_permanent():
    exc = captcha_error("https://example.com")
    assert isinstance(exc, PermanentError)
    assert "captcha" in str(exc).lower() or "bot" in str(exc).lower()


def test_captcha_error_context_has_url():
    exc = captcha_error("https://example.com")
    assert exc.context["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# content_too_short_error constructor
# ---------------------------------------------------------------------------


def test_content_too_short_error_is_permanent():
    exc = content_too_short_error("https://example.com", 50, 200)
    assert isinstance(exc, PermanentError)
    assert "50" in str(exc)
    assert "200" in str(exc)


def test_content_too_short_error_context():
    exc = content_too_short_error("https://example.com", 50, 200)
    assert exc.context["length"] == 50
    assert exc.context["minimum"] == 200


# ---------------------------------------------------------------------------
# dramatic_size_change_error constructor
# ---------------------------------------------------------------------------


def test_dramatic_size_change_error_is_permanent():
    exc = dramatic_size_change_error("https://example.com", 5000, 100)
    assert isinstance(exc, PermanentError)


def test_dramatic_size_change_error_context():
    exc = dramatic_size_change_error("https://example.com", 5000, 100)
    assert exc.context["previous_length"] == 5000
    assert exc.context["current_length"] == 100


# ---------------------------------------------------------------------------
# llm_schema_error constructor
# ---------------------------------------------------------------------------


def test_llm_schema_error_is_permanent():
    exc = llm_schema_error("B1012", 2)
    assert isinstance(exc, PermanentError)
    assert "B1012" in str(exc)
    assert "2" in str(exc)


def test_llm_schema_error_context():
    exc = llm_schema_error("B1012", 2)
    assert exc.context["page_id"] == "B1012"
    assert exc.context["attempts"] == 2
