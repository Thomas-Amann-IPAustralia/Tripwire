"""
src/stage8_llm.py

Stage 8 — LLM Assessment (Section 3.8)

Purpose: for each IPFR page with grouped triggers, make a single LLM call to
determine whether the page should be amended, and if so, produce specific,
actionable suggestions.

One LLM call is made per TriggerBundle (per IPFR page) — not per trigger.

LLM call inputs:
  • Cached system prompt (authored below)
  • All relevant diffs for this IPFR page
  • The full IPFR page content (loaded from SQLite)
  • Bi-encoder cosine scores per chunk
  • Relevance scores (lexical, semantic, reranked) per trigger

Output JSON schema (validated before processing):
  {
    "verdict":          "CHANGE_REQUIRED" | "NO_CHANGE" | "UNCERTAIN",
    "confidence":       <float in [0.0, 1.0]>,
    "reasoning":        "<string>",
    "suggested_changes": ["<string>", ...]   // populated only for CHANGE_REQUIRED
  }

Retry policy:
  1. Call LLM.
  2. Validate response against schema.
  3. If validation fails, retry once.
  4. If second attempt also fails, log raw output, skip page, record failure
     in health log, and write trigger to deferred_triggers table.

Deferred triggers (Section 6.5): when the LLM API is unavailable (RetryableError
exhausted), the trigger bundle is written to the deferred_triggers table and
processed at the start of the next run.

Observation mode: if pipeline.observation_mode is true, Stage 8 is skipped
entirely (as per Section 2.3) — the function returns immediately with an empty
result and a note in observation_data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.errors import PermanentError, RetryableError
from src.stage7_aggregation import TriggerBundle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an IP (Intellectual Property) content accuracy reviewer for the IP First Response (IPFR) website. Your role is to assess whether recent changes to authoritative IP sources — such as Australian legislation, WIPO documents, or government agency webpages — require amendments to specific IPFR content pages.

You will be given:
1. One or more change documents (diffs, legislative explainers, or RSS extracts) describing what has changed in external IP sources.
2. The full content of one IPFR page that has been flagged as potentially affected.
3. Relevance scores (lexical, semantic, cross-encoder) indicating how strongly each change is related to the IPFR page.

Your task is to determine whether the IPFR page requires amendment as a result of the external change(s).

## Output format

Respond with a single JSON object conforming exactly to this schema:

{
  "verdict": "<CHANGE_REQUIRED | NO_CHANGE | UNCERTAIN>",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<your reasoning here>",
  "suggested_changes": ["<change 1>", "<change 2>", ...]
}

The `suggested_changes` array must be populated only when verdict is CHANGE_REQUIRED. For NO_CHANGE and UNCERTAIN verdicts, set it to an empty array [].

## Verdict definitions

**CHANGE_REQUIRED**: You are confident (confidence >= 0.70) that the external change directly affects the accuracy or currency of the IPFR page content. The page contains specific information — a date, a dollar amount, a time period, a statutory reference, a procedural step, or a legal threshold — that is now incorrect or outdated as a result of the change.

**NO_CHANGE**: The external change, while potentially related in subject matter, does not affect the accuracy of any specific statement in the IPFR page. The IPFR page may discuss the same topic area, but its content remains correct.

**UNCERTAIN**: The relevance of the external change to the IPFR page is genuinely ambiguous — for example, because the change is tangentially related, the implications are unclear, the change is to implementation detail that may or may not affect the IPFR guidance, or you cannot determine whether the specific content in the IPFR page is affected without deeper domain knowledge. Output UNCERTAIN with your reasoning. Do not infer a change recommendation unless you are confident the IPFR page requires update.

## Important constraints

- Do not hallucinate legal references, section numbers, or statutory citations that are not present in the provided documents.
- Do not recommend changes to IPFR content based solely on the fact that the subject areas overlap — require a specific, identifiable impact on specific IPFR content.
- If in doubt, output UNCERTAIN rather than CHANGE_REQUIRED. UNCERTAIN is a responsible and expected output, not a failure mode.
- Each entry in suggested_changes must be a complete, self-contained action item (e.g. "Update the processing timeframe from '12 months' to '6 months' in Section 3.2 to reflect the amended s.44 of the Trade Marks Act 1995."). Do not use vague language like "update references" or "review the section".
- Keep suggested_changes entries factual and grounded in the provided change documents.
"""


# ---------------------------------------------------------------------------
# JSON schema definition and validator
# ---------------------------------------------------------------------------

_VALID_VERDICTS = {"CHANGE_REQUIRED", "NO_CHANGE", "UNCERTAIN"}


def validate_llm_response(raw: str) -> dict[str, Any]:
    """Parse and validate the raw LLM output string.

    Parameters
    ----------
    raw:
        The raw string content of the LLM response.

    Returns
    -------
    dict
        Validated and parsed response.

    Raises
    ------
    ValueError
        If parsing fails or the schema is violated.
    """
    # Strip markdown code fences if the model wrapped the JSON.
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop opening fence (```json or ```) and closing fence
        inner_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner_lines.append(line)
        text = "\n".join(inner_lines).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError("LLM response must be a JSON object.")

    # verdict
    verdict = obj.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise ValueError(
            f"LLM verdict {verdict!r} is not one of {sorted(_VALID_VERDICTS)}."
        )

    # confidence
    confidence = obj.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        raise ValueError(
            f"LLM confidence must be a float in [0.0, 1.0], got {confidence!r}."
        )

    # reasoning
    if not isinstance(obj.get("reasoning"), str) or not obj["reasoning"].strip():
        raise ValueError("LLM reasoning must be a non-empty string.")

    # suggested_changes
    sc = obj.get("suggested_changes", [])
    if not isinstance(sc, list):
        raise ValueError("LLM suggested_changes must be a list.")
    if verdict == "CHANGE_REQUIRED" and not sc:
        raise ValueError(
            "LLM suggested_changes must be non-empty when verdict is CHANGE_REQUIRED."
        )
    if verdict != "CHANGE_REQUIRED" and sc:
        # Coerce to empty list — model may have populated it spuriously.
        obj["suggested_changes"] = []

    # Normalise types
    obj["confidence"] = float(confidence)
    return obj


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LLMAssessment:
    """Validated LLM output for one IPFR page."""

    ipfr_page_id: str
    verdict: str
    """'CHANGE_REQUIRED' | 'NO_CHANGE' | 'UNCERTAIN'"""
    confidence: float
    reasoning: str
    suggested_changes: list[str]
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    retries: int
    schema_valid: bool
    raw_response: str
    processing_time_seconds: float
    deferred: bool = False
    """True if the trigger was written to deferred_triggers and skipped."""
    error_message: str | None = None


@dataclass
class LLMStageResult:
    """Output of Stage 8."""

    assessments: list[LLMAssessment]
    """One assessment per IPFR page (excluding skipped/deferred pages)."""
    deferred_count: int
    failed_count: int
    observation_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deferred trigger helpers
# ---------------------------------------------------------------------------


def _write_deferred_trigger(
    conn: sqlite3.Connection,
    run_id: str,
    bundle: TriggerBundle,
) -> None:
    """Write a trigger bundle to the deferred_triggers table."""
    trigger_data = {
        "ipfr_page_id": bundle.ipfr_page_id,
        "triggers": [
            {
                "source_id": t.source_id,
                "source_url": t.source_url,
                "source_importance": t.source_importance,
                "source_type": t.source_type,
                "diff_text": t.diff_text,
                "significance": t.significance,
                "stage4_final_score": t.stage4_final_score,
                "biencoder_max_chunk_score": t.biencoder_max_chunk_score,
                "crossencoder_final_score": t.crossencoder_final_score,
                "graph_propagated": t.graph_propagated,
            }
            for t in bundle.triggers
        ],
    }
    conn.execute(
        """
        INSERT INTO deferred_triggers
            (run_id, source_id, ipfr_page_id, trigger_data, created_at, processed)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            run_id,
            ",".join(bundle.source_ids),
            bundle.ipfr_page_id,
            json.dumps(trigger_data),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def load_pending_deferred_triggers(
    conn: sqlite3.Connection,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Load unprocessed deferred triggers younger than max_age_days.

    Returns a list of dicts with keys: id, run_id, source_id, ipfr_page_id,
    trigger_data (already parsed from JSON).
    """
    cutoff = datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT id, run_id, source_id, ipfr_page_id, trigger_data, created_at
        FROM deferred_triggers
        WHERE processed = 0
        ORDER BY created_at ASC
        """,
    ).fetchall()

    results = []
    for row in rows:
        created_at_str = row[5]
        try:
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = (cutoff - created_at).total_seconds() / 86400
            if age_days > max_age_days:
                # Mark stale triggers as processed (discarded).
                conn.execute(
                    "UPDATE deferred_triggers SET processed = 1 WHERE id = ?",
                    (row[0],),
                )
                conn.commit()
                logger.info(
                    "Discarded stale deferred trigger id=%d (age=%.1f days > %d days)",
                    row[0],
                    age_days,
                    max_age_days,
                )
                continue
        except (ValueError, TypeError):
            pass  # If we can't parse the date, include the trigger anyway.

        results.append(
            {
                "id": row[0],
                "run_id": row[1],
                "source_id": row[2],
                "ipfr_page_id": row[3],
                "trigger_data": json.loads(row[4]),
            }
        )

    return results


def mark_deferred_trigger_processed(
    conn: sqlite3.Connection, trigger_id: int
) -> None:
    conn.execute(
        "UPDATE deferred_triggers SET processed = 1 WHERE id = ?", (trigger_id,)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------


def _call_openai(
    client: Any,
    model: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, str]],
) -> tuple[str, int, int, int]:
    """Invoke the OpenAI chat-completions endpoint.

    Returns (content_str, prompt_tokens, completion_tokens, total_tokens).
    Raises RetryableError on rate-limit / 5xx, PermanentError on 4xx.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except (RetryableError, PermanentError):
        raise  # already classified — pass through directly
    except Exception as exc:
        exc_type = type(exc).__name__
        exc_str = str(exc)
        # Rate-limit or server error → retryable
        if any(
            marker in exc_str
            for marker in ("rate_limit", "rate limit", "RateLimitError", "APIStatusError",
                           "APIConnectionError", "Timeout", "timeout")
        ) or "429" in exc_str or "5" in exc_str[:3]:
            raise RetryableError(
                f"LLM API transient error ({exc_type}): {exc_str}"
            ) from exc
        raise PermanentError(
            f"LLM API permanent error ({exc_type}): {exc_str}"
        ) from exc

    content = response.choices[0].message.content or ""
    usage = response.usage
    prompt_tokens = getattr(usage, "prompt_tokens", 0)
    completion_tokens = getattr(usage, "completion_tokens", 0)
    total_tokens = getattr(usage, "total_tokens", 0)
    return content, prompt_tokens, completion_tokens, total_tokens


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_user_message(
    bundle: TriggerBundle,
    page_content: str,
    page_title: str,
) -> str:
    """Compose the user message for the LLM."""
    parts: list[str] = []

    parts.append(f"## IPFR Page: {bundle.ipfr_page_id} — {page_title}\n")
    parts.append("### IPFR Page Content\n")
    parts.append(page_content.strip())
    parts.append("")

    parts.append("---\n")
    parts.append(
        f"## Upstream Change(s) — {bundle.trigger_count} trigger(s) for this page\n"
    )

    for i, trig in enumerate(bundle.triggers, start=1):
        parts.append(f"### Change {i}: Source `{trig.source_id}`")
        parts.append(f"- **URL:** {trig.source_url}")
        parts.append(f"- **Source type:** {trig.source_type}")
        parts.append(f"- **Significance:** {trig.significance}")
        if trig.graph_propagated:
            parts.append(
                "- **Note:** This page was reached via graph propagation "
                "(indirect signal — treat with lower confidence)."
            )
        parts.append(f"- **Relevance scores:**")
        parts.append(f"  - Stage 4 final score: {trig.stage4_final_score:.4f}")
        parts.append(
            f"  - Bi-encoder max chunk cosine: {trig.biencoder_max_chunk_score:.4f}"
        )
        parts.append(
            f"  - Cross-encoder final score: {trig.crossencoder_final_score:.4f}"
        )
        parts.append("")
        parts.append("#### Change document")
        parts.append("```")
        # Truncate very long diffs to keep the prompt within token limits.
        diff_text = trig.diff_text
        if len(diff_text) > 6000:
            diff_text = diff_text[:6000] + "\n... [truncated for token budget]"
        parts.append(diff_text)
        parts.append("```")
        parts.append("")

    parts.append("---\n")
    parts.append(
        "Based on the above, determine whether the IPFR page requires amendment. "
        "Respond with a single JSON object as specified in your instructions."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core Stage 8 function
# ---------------------------------------------------------------------------


def assess_bundles(
    bundles: list[TriggerBundle],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    run_id: str,
    client: Any | None = None,
) -> LLMStageResult:
    """Run Stage 8 LLM assessment for all trigger bundles.

    Parameters
    ----------
    bundles:
        List of TriggerBundles from Stage 7.
    conn:
        Open SQLite connection to the IPFR corpus database.
    config:
        Validated pipeline configuration.
    run_id:
        Current pipeline run identifier (e.g. '2026-04-05-001').
    client:
        Pre-constructed OpenAI client, or None to construct one from the
        environment variable OPENAI_API_KEY.  Injecting the client is
        preferred in tests.

    Returns
    -------
    LLMStageResult
    """
    observation_mode: bool = config.get("pipeline", {}).get(
        "observation_mode", True
    )
    if observation_mode:
        logger.info("Stage 8: observation mode active — skipping LLM calls.")
        return LLMStageResult(
            assessments=[],
            deferred_count=0,
            failed_count=0,
            observation_data={"skipped": True, "reason": "observation_mode"},
        )

    if not bundles:
        return LLMStageResult(
            assessments=[],
            deferred_count=0,
            failed_count=0,
            observation_data={"bundles_received": 0},
        )

    pipeline_cfg = config.get("pipeline", {})
    model: str = str(pipeline_cfg.get("llm_model", "gpt-4o"))
    temperature: float = float(pipeline_cfg.get("llm_temperature", 0.2))
    max_tokens: int = 1000
    max_retries: int = int(pipeline_cfg.get("max_retries", 3))
    base_delay: float = float(pipeline_cfg.get("retry_base_delay_seconds", 2.0))
    deferred_max_age: int = int(
        pipeline_cfg.get("deferred_trigger_max_age_days", 7)
    )

    if client is None:
        client = _make_openai_client()

    assessments: list[LLMAssessment] = []
    deferred_count = 0
    failed_count = 0

    for bundle in bundles:
        page_content, page_title = _load_page(conn, bundle.ipfr_page_id)
        if page_content is None:
            logger.warning(
                "Stage 8: page %s not found in database — skipping.",
                bundle.ipfr_page_id,
            )
            failed_count += 1
            continue

        user_message = _build_user_message(bundle, page_content, page_title)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        assessment = _assess_single_bundle(
            bundle=bundle,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            client=client,
            conn=conn,
            run_id=run_id,
            base_delay=base_delay,
        )

        if assessment.deferred:
            deferred_count += 1
        elif not assessment.schema_valid:
            failed_count += 1
        else:
            assessments.append(assessment)

    observation_data: dict[str, Any] = {
        "bundles_processed": len(bundles),
        "assessments_produced": len(assessments),
        "deferred_count": deferred_count,
        "failed_count": failed_count,
        "verdict_distribution": _verdict_distribution(assessments),
    }

    logger.info(
        "Stage 8: %d assessment(s) produced, %d deferred, %d failed.",
        len(assessments),
        deferred_count,
        failed_count,
    )

    return LLMStageResult(
        assessments=assessments,
        deferred_count=deferred_count,
        failed_count=failed_count,
        observation_data=observation_data,
    )


def _assess_single_bundle(
    bundle: TriggerBundle,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    client: Any,
    conn: sqlite3.Connection,
    run_id: str,
    base_delay: float,
) -> LLMAssessment:
    """Attempt the LLM call for one bundle with one retry on schema failure."""
    retries = 0
    raw_response = ""
    last_error: str | None = None
    prompt_tokens = completion_tokens = total_tokens = 0

    for attempt in range(2):  # initial call + one retry
        t0 = time.monotonic()
        try:
            raw_response, prompt_tokens, completion_tokens, total_tokens = (
                _call_openai(client, model, temperature, max_tokens, messages)
            )
        except RetryableError as exc:
            # LLM API unavailable — defer the trigger.
            logger.warning(
                "Stage 8: LLM API unavailable for page %s: %s — deferring.",
                bundle.ipfr_page_id,
                exc,
            )
            _write_deferred_trigger(conn, run_id, bundle)
            return LLMAssessment(
                ipfr_page_id=bundle.ipfr_page_id,
                verdict="",
                confidence=0.0,
                reasoning="",
                suggested_changes=[],
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                retries=attempt,
                schema_valid=False,
                raw_response="",
                processing_time_seconds=time.monotonic() - t0,
                deferred=True,
                error_message=str(exc),
            )
        except PermanentError as exc:
            last_error = str(exc)
            logger.error(
                "Stage 8: permanent LLM error for page %s: %s",
                bundle.ipfr_page_id,
                exc,
            )
            break

        elapsed = time.monotonic() - t0

        # Validate schema.
        try:
            parsed = validate_llm_response(raw_response)
            return LLMAssessment(
                ipfr_page_id=bundle.ipfr_page_id,
                verdict=parsed["verdict"],
                confidence=parsed["confidence"],
                reasoning=parsed["reasoning"],
                suggested_changes=parsed.get("suggested_changes", []),
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                retries=attempt,
                schema_valid=True,
                raw_response=raw_response,
                processing_time_seconds=elapsed,
            )
        except ValueError as exc:
            last_error = str(exc)
            retries = attempt + 1
            if attempt == 0:
                logger.warning(
                    "Stage 8: schema validation failed for page %s "
                    "(attempt %d): %s — retrying.",
                    bundle.ipfr_page_id,
                    attempt + 1,
                    exc,
                )
                time.sleep(base_delay)
            else:
                logger.error(
                    "Stage 8: schema validation failed for page %s after "
                    "%d attempt(s): %s — skipping.",
                    bundle.ipfr_page_id,
                    attempt + 1,
                    exc,
                )

    # Both attempts failed.
    return LLMAssessment(
        ipfr_page_id=bundle.ipfr_page_id,
        verdict="",
        confidence=0.0,
        reasoning="",
        suggested_changes=[],
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        retries=retries,
        schema_valid=False,
        raw_response=raw_response,
        processing_time_seconds=0.0,
        error_message=last_error,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_page(
    conn: sqlite3.Connection, page_id: str
) -> tuple[str | None, str]:
    """Return (content, title) for the given page_id, or (None, '') if missing."""
    row = conn.execute(
        "SELECT content, title FROM pages WHERE page_id = ?", (page_id,)
    ).fetchone()
    if row is None:
        return None, ""
    return row[0], row[1]


def _verdict_distribution(assessments: list[LLMAssessment]) -> dict[str, int]:
    dist: dict[str, int] = {
        "CHANGE_REQUIRED": 0,
        "NO_CHANGE": 0,
        "UNCERTAIN": 0,
    }
    for a in assessments:
        if a.verdict in dist:
            dist[a.verdict] += 1
    return dist


def _make_openai_client() -> Any:
    """Construct an OpenAI client from the environment.

    Raises PermanentError if the openai package is unavailable or the API
    key is not set.
    """
    try:
        import openai  # type: ignore[import-untyped]
    except ImportError as exc:
        raise PermanentError(
            "openai package is not installed. Run: pip install openai"
        ) from exc

    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise PermanentError(
            "OPENAI_API_KEY environment variable is not set."
        )
    return openai.OpenAI(api_key=api_key)
