"""
src/errors.py

Error hierarchy for the Tripwire pipeline.

All Tripwire-specific exceptions derive from TripwireError.  The two leaf
classes — RetryableError and PermanentError — control how the retry layer
(src/retry.py) responds to failures:

  RetryableError  — transient failure; a subsequent attempt may succeed.
                    Examples: HTTP 5xx, connection timeout, DNS failure,
                    LLM rate-limit, SMTP connection failure.

  PermanentError  — retrying will not help; skip this source for this run.
                    Examples: HTTP 404/403, CAPTCHA detected, repeated LLM
                    schema validation failure, content too short.
"""

from __future__ import annotations

from typing import Any


class TripwireError(Exception):
    """Base class for all Tripwire pipeline errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        ctx = f", context={self.context!r}" if self.context else ""
        return f"{type(self).__name__}({str(self)!r}{ctx})"


class RetryableError(TripwireError):
    """Transient failure — the operation should be retried with backoff.

    Raised for:
    - HTTP 5xx responses
    - Connection timeouts / DNS resolution failures
    - LLM API rate limits (HTTP 429)
    - SMTP connection failures
    """


class PermanentError(TripwireError):
    """Non-transient failure — retrying will not help; skip this source.

    Raised for:
    - HTTP 404 (page not found) or 403 (access denied)
    - CAPTCHA / bot-detection page detected
    - Content validation failures (too short, dramatic size change)
    - Repeated LLM schema validation failure (two consecutive invalid responses)
    - Any other failure where the underlying cause cannot resolve on retry
    """


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def http_error(status_code: int, url: str) -> TripwireError:
    """Return the appropriate error type for an HTTP error response."""
    ctx = {"status_code": status_code, "url": url}
    if status_code in (429,) or status_code >= 500:
        return RetryableError(
            f"HTTP {status_code} fetching {url}", context=ctx
        )
    return PermanentError(
        f"HTTP {status_code} fetching {url} (non-retryable)", context=ctx
    )


def captcha_error(url: str) -> PermanentError:
    """Raised when CAPTCHA / bot-detection content is detected."""
    return PermanentError(
        f"CAPTCHA or bot-detection page detected at {url}",
        context={"url": url},
    )


def content_too_short_error(url: str, length: int, minimum: int) -> PermanentError:
    return PermanentError(
        f"Content at {url} is too short ({length} chars < minimum {minimum})",
        context={"url": url, "length": length, "minimum": minimum},
    )


def dramatic_size_change_error(url: str, previous: int, current: int) -> PermanentError:
    return PermanentError(
        f"Dramatic content size change at {url}: {previous} → {current} chars",
        context={"url": url, "previous_length": previous, "current_length": current},
    )


def llm_schema_error(page_id: str, attempt: int) -> PermanentError:
    return PermanentError(
        f"LLM response failed schema validation for page {page_id} after {attempt} attempt(s)",
        context={"page_id": page_id, "attempts": attempt},
    )
