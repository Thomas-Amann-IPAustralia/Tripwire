"""
src/validation.py

Content validation for scraped influencer sources (Section 6.2).

After every web scrape, the returned content is validated before being
accepted by the pipeline.  Four checks are applied:

  1. Minimum length  — content shorter than 200 characters is rejected.
  2. CAPTCHA detection — common bot-detection phrases trigger rejection.
  3. Dramatic size change — new length outside [30%, 300%] of previous.

Checks 1 and 2 raise PermanentError (retrying will not fix them).
Check 3 raises PermanentError (suspicious content should not be silently accepted).

The module also provides a soft validate_content() path that returns
warnings without raising, for callers that want to inspect issues
before deciding how to handle them.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_CONTENT_LENGTH: int = 200

_CAPTCHA_PHRASES: list[str] = [
    "captcha",
    "verify you are human",
    "robot check",
    # "access denied" is intentionally absent: it appears in legitimate
    # government content (customs enforcement, FOI, IP seizure notices).
    # Real block pages are already caught by the minimum-length check above.
    "please enable javascript",
    "enable cookies",
    "checking your browser",
]

# Size-change bounds.
_SIZE_CHANGE_MIN_RATIO: float = 0.30
_SIZE_CHANGE_MAX_RATIO: float = 3.00


# ---------------------------------------------------------------------------
# Hard validation (raises on failure)
# ---------------------------------------------------------------------------


def validate_scraped_content(
    content: str,
    url: str,
    previous_length: int | None = None,
) -> list[str]:
    """Validate scraped content and raise PermanentError on hard failures.

    Parameters
    ----------
    content:
        Normalised plain text returned by the scraper.
    url:
        Source URL (used in error messages and logging).
    previous_length:
        Length in characters of the previous snapshot, or None on first run.

    Returns
    -------
    list[str]
        Empty list on success.  Hard failures raise rather than appearing
        in this list.

    Raises
    ------
    src.errors.PermanentError
        If content is too short, contains CAPTCHA phrases, or shows a
        dramatic size change relative to the previous snapshot.
    """
    from src.errors import (
        captcha_error,
        content_too_short_error,
        dramatic_size_change_error,
    )

    # Check 1: minimum length.
    if len(content) < _MIN_CONTENT_LENGTH:
        raise content_too_short_error(url, len(content), _MIN_CONTENT_LENGTH)

    # Check 2: CAPTCHA / bot-detection.
    lower = content.lower()
    for phrase in _CAPTCHA_PHRASES:
        if phrase in lower:
            raise captcha_error(url)

    # Check 4: dramatic size change (hard failure).
    if previous_length is not None and previous_length > 0:
        ratio = len(content) / previous_length
        if ratio < _SIZE_CHANGE_MIN_RATIO or ratio > _SIZE_CHANGE_MAX_RATIO:
            raise dramatic_size_change_error(url, previous_length, len(content))

    return []


def validate_content(
    content: str,
    url: str,
    previous_length: int | None = None,
) -> list[str]:
    """Soft validation — return all issues as warning strings without raising.

    Useful in test/observation contexts where the caller wants to inspect
    problems rather than having the pipeline abort.

    Parameters
    ----------
    content:
        Normalised plain text.
    url:
        Source URL.
    previous_length:
        Length of the previous snapshot, or None.

    Returns
    -------
    list[str]
        All validation warnings (may be empty if everything passes).
    """
    warnings: list[str] = []

    if len(content) < _MIN_CONTENT_LENGTH:
        warnings.append(
            f"Content too short: {len(content)} chars (minimum {_MIN_CONTENT_LENGTH})."
        )

    lower = content.lower()
    for phrase in _CAPTCHA_PHRASES:
        if phrase in lower:
            warnings.append(f"Possible CAPTCHA/bot-detection phrase found: '{phrase}'.")
            break

    if previous_length is not None and previous_length > 0:
        ratio = len(content) / previous_length
        if ratio < _SIZE_CHANGE_MIN_RATIO:
            warnings.append(
                f"Dramatic content shrinkage: {previous_length} → {len(content)} chars "
                f"({ratio:.0%} of previous)."
            )
        elif ratio > _SIZE_CHANGE_MAX_RATIO:
            warnings.append(
                f"Dramatic content growth: {previous_length} → {len(content)} chars "
                f"({ratio:.0%} of previous)."
            )

    return warnings
