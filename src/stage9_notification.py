"""
src/stage9_notification.py

Stage 9 — Notification (Section 3.9)

Purpose: send one consolidated email per run to the content owner, summarising
all amendment suggestions from Stage 8.

Email structure:
  • Subject: "Tripwire — {date} — {N} IPFR page(s) flagged"
  • One section per CHANGE_REQUIRED page:
      - IPFR page identifier and title
      - Source(s) that triggered the alert (with URLs)
      - Normalised diff text
      - LLM reasoning
      - Full suggested_changes entries
      - Scoring evidence (fused relevance, bi-encoder max, cross-encoder)
      - Four mailto feedback links
  • "Items requiring human review" section for UNCERTAIN verdicts
  • "Candidates rejected at deep analysis" section (pages that failed
    cross-encoder or LLM gate — supplied by the pipeline as rejected_candidates)

No-alert policy: if no pages are flagged (no CHANGE_REQUIRED or UNCERTAIN),
the email is not sent.

Email delivery: Python smtplib with a Gmail app password stored in
SMTP_PASSWORD environment variable.

Feedback mailto format:
  Subject: [TRIPWIRE] Feedback — {run_id} — {page_id}
  Body:    pre-formatted text with run_id, page_id, source_id, category
  Reply-To: feedback email address (from config or FEEDBACK_EMAIL env var)
"""

from __future__ import annotations

import logging
import os
import smtplib
import urllib.parse
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from src.errors import RetryableError
from src.stage7_aggregation import TriggerBundle
from src.stage8_llm import LLMAssessment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feedback category labels
# ---------------------------------------------------------------------------

_FEEDBACK_CATEGORIES: list[tuple[str, str]] = [
    ("useful", "Useful — the alert was accurate and the suggestion was helpful"),
    (
        "not_significant",
        "Not a significant trigger event — the change was real but not important "
        "enough to warrant an alert",
    ),
    (
        "wrong_amendment",
        "Noteworthy trigger event but incorrect amendment — the change was important, "
        "but the suggested amendment was wrong",
    ),
    (
        "wrong_page",
        "Noteworthy trigger event but content influenced was incorrect — the change "
        "was important, but the wrong IPFR page was flagged",
    ),
]


# ---------------------------------------------------------------------------
# Rejected candidate record (supplied by the pipeline from Stage 6 output)
# ---------------------------------------------------------------------------


@dataclass
class RejectedCandidate:
    """A page that was rejected at the cross-encoder or LLM stage."""

    source_id: str
    source_url: str
    ipfr_page_id: str
    rejection_stage: str
    """'crossencoder' | 'llm_schema' | 'llm_permanent'"""
    crossencoder_score: float | None = None
    reranked_score: float | None = None


# ---------------------------------------------------------------------------
# Page metadata helper (loaded from DB by the pipeline)
# ---------------------------------------------------------------------------


@dataclass
class PageMeta:
    """Minimal page metadata needed by Stage 9."""

    page_id: str
    title: str
    url: str


# ---------------------------------------------------------------------------
# Notification result
# ---------------------------------------------------------------------------


@dataclass
class NotificationResult:
    """Output of Stage 9."""

    sent: bool
    recipient: str | None = None
    subject: str | None = None
    change_required_count: int = 0
    uncertain_count: int = 0
    error_message: str | None = None
    fallback_file: str | None = None
    """Path to the saved email file when SMTP failed."""
    observation_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_notification(
    assessments: list[LLMAssessment],
    bundles_by_page: dict[str, TriggerBundle],
    page_meta_by_id: dict[str, PageMeta],
    rejected_candidates: list[RejectedCandidate],
    run_id: str,
    run_date: str,
    config: dict[str, Any],
    smtp_client: Any | None = None,
) -> NotificationResult:
    """Compose and send the Stage 9 consolidated notification email.

    Parameters
    ----------
    assessments:
        Validated LLM assessments from Stage 8.
    bundles_by_page:
        Mapping page_id → TriggerBundle (from Stage 7).
    page_meta_by_id:
        Mapping page_id → PageMeta (title + URL).
    rejected_candidates:
        Pages rejected at Stage 6 or Stage 8 (for the calibration section).
    run_id:
        Current run identifier (e.g. '2026-04-05-001').
    run_date:
        Human-readable run date (e.g. '6 April 2026').
    config:
        Validated pipeline configuration.
    smtp_client:
        Injected SMTP connection for testing.  If None, a real connection is
        created from environment variables.

    Returns
    -------
    NotificationResult
    """
    notif_cfg = config.get("notifications", {})
    recipient: str = notif_cfg.get("content_owner_email", "")
    feedback_email: str = os.environ.get(
        "FEEDBACK_EMAIL",
        notif_cfg.get("feedback_email", "tripwire-feedback@gmail.com"),
    )

    change_required = [a for a in assessments if a.verdict == "CHANGE_REQUIRED"]
    uncertain = [a for a in assessments if a.verdict == "UNCERTAIN"]

    if not change_required and not uncertain:
        no_change_count = sum(1 for a in assessments if a.verdict == "NO_CHANGE")
        logger.info(
            "Stage 9: no pages flagged (%d NO_CHANGE, %d total assessments) — email not sent.",
            no_change_count,
            len(assessments),
        )
        return NotificationResult(
            sent=False,
            change_required_count=0,
            uncertain_count=0,
            observation_data={"reason": "no_alerts"},
        )

    subject, body_text, body_html = _compose_email(
        change_required=change_required,
        uncertain=uncertain,
        rejected_candidates=rejected_candidates,
        bundles_by_page=bundles_by_page,
        page_meta_by_id=page_meta_by_id,
        run_id=run_id,
        run_date=run_date,
        feedback_email=feedback_email,
    )

    msg = _build_mime(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        sender=feedback_email,
        recipient=recipient,
        reply_to=feedback_email,
    )

    # Attempt delivery with retries.
    smtp_cfg = notif_cfg.get("smtp", {})
    smtp_host: str = smtp_cfg.get("host", "smtp.gmail.com")
    smtp_port: int = int(smtp_cfg.get("port", 587))
    smtp_user: str = os.environ.get("SMTP_USER", feedback_email)
    smtp_password: str = os.environ.get("SMTP_PASSWORD", "")

    last_error: str | None = None
    max_retries = int(config.get("pipeline", {}).get("max_retries", 3))
    base_delay = float(config.get("pipeline", {}).get("retry_base_delay_seconds", 2.0))

    import time

    for attempt in range(max_retries + 1):
        try:
            if smtp_client is not None:
                smtp_client.sendmail(feedback_email, [recipient], msg.as_string())
            else:
                _send_via_smtp(
                    msg=msg,
                    host=smtp_host,
                    port=smtp_port,
                    user=smtp_user,
                    password=smtp_password,
                    sender=feedback_email,
                    recipient=recipient,
                )
            logger.info(
                "Stage 9: email sent → %s | subject: %s | %d CHANGE_REQUIRED, %d UNCERTAIN",
                recipient, subject, len(change_required), len(uncertain),
            )
            return NotificationResult(
                sent=True,
                recipient=recipient,
                subject=subject,
                change_required_count=len(change_required),
                uncertain_count=len(uncertain),
                observation_data={
                    "change_required": len(change_required),
                    "uncertain": len(uncertain),
                    "rejected_candidates": len(rejected_candidates),
                },
            )
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Stage 9: SMTP error (attempt %d/%d): %s — retrying in %.1f s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)

    # All retries exhausted — save to fallback file.
    fallback = _write_fallback(body_text, run_id)
    logger.error(
        "Stage 9: SMTP FAILED after %d attempt(s) — FALLBACK email saved → %s | error: %s",
        max_retries + 1,
        fallback,
        last_error,
    )
    return NotificationResult(
        sent=False,
        recipient=recipient,
        subject=subject,
        change_required_count=len(change_required),
        uncertain_count=len(uncertain),
        error_message=last_error,
        fallback_file=fallback,
        observation_data={
            "change_required": len(change_required),
            "uncertain": len(uncertain),
        },
    )


# ---------------------------------------------------------------------------
# Email composition
# ---------------------------------------------------------------------------


def _compose_email(
    change_required: list[LLMAssessment],
    uncertain: list[LLMAssessment],
    rejected_candidates: list[RejectedCandidate],
    bundles_by_page: dict[str, TriggerBundle],
    page_meta_by_id: dict[str, PageMeta],
    run_id: str,
    run_date: str,
    feedback_email: str,
) -> tuple[str, str, str]:
    """Return (subject, plain_text_body, html_body)."""
    total_flagged = len(change_required) + len(uncertain)
    subject = (
        f"Tripwire — {run_date} — "
        f"{total_flagged} IPFR page{'s' if total_flagged != 1 else ''} flagged"
    )

    text_parts: list[str] = []
    html_parts: list[str] = []

    _txt_header(text_parts, subject, run_id, run_date, total_flagged)
    _html_header(html_parts, subject, run_id, run_date, total_flagged)

    # ---- CHANGE_REQUIRED section -----------------------------------------
    if change_required:
        text_parts.append("=" * 70)
        text_parts.append("AMENDMENT REQUIRED")
        text_parts.append("=" * 70)
        html_parts.append('<h2 style="color:#c0392b;">Amendment Required</h2>')

        for assessment in change_required:
            page_id = assessment.ipfr_page_id
            meta = page_meta_by_id.get(page_id, PageMeta(page_id, page_id, ""))
            bundle = bundles_by_page.get(page_id)
            _txt_change_required_section(
                text_parts, assessment, meta, bundle, run_id, feedback_email
            )
            _html_change_required_section(
                html_parts, assessment, meta, bundle, run_id, feedback_email
            )

    # ---- UNCERTAIN section -----------------------------------------------
    if uncertain:
        text_parts.append("")
        text_parts.append("=" * 70)
        text_parts.append("ITEMS REQUIRING HUMAN REVIEW")
        text_parts.append("=" * 70)
        html_parts.append(
            '<h2 style="color:#e67e22;">Items Requiring Human Review</h2>'
        )

        for assessment in uncertain:
            page_id = assessment.ipfr_page_id
            meta = page_meta_by_id.get(page_id, PageMeta(page_id, page_id, ""))
            bundle = bundles_by_page.get(page_id)
            _txt_uncertain_section(
                text_parts, assessment, meta, bundle, run_id, feedback_email
            )
            _html_uncertain_section(
                html_parts, assessment, meta, bundle, run_id, feedback_email
            )

    # ---- Rejected candidates section -------------------------------------
    if rejected_candidates:
        text_parts.append("")
        text_parts.append("=" * 70)
        text_parts.append("CANDIDATES REJECTED AT DEEP ANALYSIS")
        text_parts.append(
            "(Surfaced for calibration — potential false positives in upstream stages)"
        )
        text_parts.append("=" * 70)
        html_parts.append(
            "<h2>Candidates Rejected at Deep Analysis</h2>"
            "<p><em>Surfaced for calibration — potential false positives in "
            "upstream stages.</em></p>"
        )
        for rc in rejected_candidates:
            _txt_rejected_section(text_parts, rc)
            _html_rejected_section(html_parts, rc)

    text_parts.append("")
    text_parts.append(
        f"Run ID: {run_id} | Generated by Tripwire"
    )
    html_parts.append(
        f'<hr><p style="color:#888;font-size:0.85em;">'
        f"Run ID: {run_id} | Generated by Tripwire</p>"
    )
    html_parts.append("</body></html>")

    return subject, "\n".join(text_parts), "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Plain-text section builders
# ---------------------------------------------------------------------------


def _txt_header(
    parts: list[str], subject: str, run_id: str, run_date: str, total: int
) -> None:
    parts.append(subject)
    parts.append(f"Run ID: {run_id}  |  Date: {run_date}")
    parts.append(f"{total} IPFR page{'s' if total != 1 else ''} require attention.")
    parts.append("")


def _txt_change_required_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    parts.append("")
    parts.append("-" * 70)
    parts.append(f"Page: {meta.page_id} — {meta.title}")
    if meta.url:
        parts.append(f"URL: {meta.url}")
    parts.append(
        f"Confidence: {assessment.confidence:.0%}  |  Model: {assessment.model}"
    )
    parts.append("")
    if bundle:
        parts.append("Triggered by:")
        for trig in bundle.triggers:
            parts.append(
                f"  • {trig.source_id} ({trig.source_type}) — {trig.source_url}"
            )
            parts.append(
                f"    Scores: Stage4={trig.stage4_final_score:.4f}  "
                f"BiEnc={trig.biencoder_max_chunk_score:.4f}  "
                f"CE={trig.crossencoder_final_score:.4f}"
            )
        parts.append("")
        parts.append("Change document(s):")
        for trig in bundle.triggers:
            parts.append(f"  Source: {trig.source_id}")
            diff_preview = trig.diff_text[:1500]
            if len(trig.diff_text) > 1500:
                diff_preview += "\n  ... [truncated]"
            for line in diff_preview.splitlines():
                parts.append(f"  {line}")
        parts.append("")
    parts.append("LLM Assessment:")
    parts.append(f"  Reasoning: {assessment.reasoning}")
    parts.append("")
    parts.append("Suggested Changes:")
    for i, change in enumerate(assessment.suggested_changes, 1):
        parts.append(f"  {i}. {change}")
    parts.append("")
    parts.append("Feedback:")
    for category, label in _FEEDBACK_CATEGORIES:
        mailto = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        parts.append(f"  [{label}]")
        parts.append(f"  {mailto}")
    parts.append("")


def _txt_uncertain_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    parts.append("")
    parts.append("-" * 70)
    parts.append(f"Page: {meta.page_id} — {meta.title}  [UNCERTAIN]")
    if meta.url:
        parts.append(f"URL: {meta.url}")
    if bundle:
        source_summary = ", ".join(
            f"{t.source_id} ({t.source_type})" for t in bundle.triggers
        )
        parts.append(f"Triggered by: {source_summary}")
        for trig in bundle.triggers:
            parts.append(
                f"  Scores: Stage4={trig.stage4_final_score:.4f}  "
                f"BiEnc={trig.biencoder_max_chunk_score:.4f}  "
                f"CE={trig.crossencoder_final_score:.4f}"
            )
    parts.append("")
    parts.append(f"LLM Reasoning: {assessment.reasoning}")
    parts.append("")
    parts.append("Feedback:")
    for category, label in _FEEDBACK_CATEGORIES:
        mailto = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        parts.append(f"  [{label}]")
        parts.append(f"  {mailto}")
    parts.append("")


def _txt_rejected_section(parts: list[str], rc: RejectedCandidate) -> None:
    parts.append(
        f"  • Source: {rc.source_id}  Page: {rc.ipfr_page_id}  "
        f"Rejected at: {rc.rejection_stage}"
    )
    if rc.crossencoder_score is not None:
        parts.append(
            f"    CE score: {rc.crossencoder_score:.4f}  "
            f"Reranked: {rc.reranked_score:.4f}"
        )


# ---------------------------------------------------------------------------
# HTML section builders
# ---------------------------------------------------------------------------


def _html_header(
    parts: list[str], subject: str, run_id: str, run_date: str, total: int
) -> None:
    parts.append(
        '<!DOCTYPE html><html><head>'
        '<meta charset="UTF-8">'
        '<style>'
        'body{font-family:Arial,sans-serif;font-size:14px;color:#222;}'
        'h2{border-bottom:2px solid #ccc;padding-bottom:4px;}'
        'h3{margin-bottom:4px;}'
        '.page-block{border:1px solid #ddd;padding:16px;margin:16px 0;'
        'border-radius:4px;}'
        '.scores{font-size:0.85em;color:#555;}'
        '.diff{background:#f6f6f6;padding:8px;font-family:monospace;'
        'font-size:0.8em;white-space:pre-wrap;max-height:300px;overflow-y:auto;}'
        '.feedback a{display:inline-block;margin:4px 2px;padding:5px 10px;'
        'background:#f0f0f0;border-radius:3px;text-decoration:none;color:#333;'
        'font-size:0.85em;border:1px solid #ccc;}'
        '.feedback a:hover{background:#e0e0e0;}'
        '</style>'
        '</head><body>'
    )
    parts.append(f"<h1>{subject}</h1>")
    parts.append(
        f"<p>Run ID: <code>{run_id}</code> &nbsp;|&nbsp; Date: {run_date}</p>"
    )
    parts.append(
        f"<p>{total} IPFR page{'s' if total != 1 else ''} require attention.</p>"
    )


def _html_change_required_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    page_link = (
        f'<a href="{meta.url}">{meta.page_id} — {meta.title}</a>'
        if meta.url
        else f"{meta.page_id} — {meta.title}"
    )
    parts.append('<div class="page-block">')
    parts.append(f"<h3>{page_link}</h3>")
    parts.append(
        f'<p class="scores">Confidence: {assessment.confidence:.0%} '
        f"&nbsp;|&nbsp; Model: {assessment.model}</p>"
    )
    if bundle:
        parts.append("<p><strong>Triggered by:</strong></p><ul>")
        for trig in bundle.triggers:
            parts.append(
                f'<li><a href="{trig.source_url}">{trig.source_id}</a> '
                f"({trig.source_type}) &mdash; "
                f'<span class="scores">Stage4={trig.stage4_final_score:.4f} '
                f"BiEnc={trig.biencoder_max_chunk_score:.4f} "
                f"CE={trig.crossencoder_final_score:.4f}</span></li>"
            )
        parts.append("</ul>")

        parts.append("<details><summary>Change document(s)</summary>")
        for trig in bundle.triggers:
            diff_preview = trig.diff_text[:3000]
            if len(trig.diff_text) > 3000:
                diff_preview += "\n... [truncated]"
            parts.append(
                f'<p><strong>{trig.source_id}:</strong></p>'
                f'<div class="diff">{_html_escape(diff_preview)}</div>'
            )
        parts.append("</details>")

    parts.append(f"<p><strong>LLM Reasoning:</strong> {_html_escape(assessment.reasoning)}</p>")
    parts.append("<p><strong>Suggested Changes:</strong></p><ol>")
    for change in assessment.suggested_changes:
        parts.append(f"<li>{_html_escape(change)}</li>")
    parts.append("</ol>")

    parts.append('<div class="feedback"><strong>Feedback:</strong><br>')
    for category, label in _FEEDBACK_CATEGORIES:
        href = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        parts.append(f'<a href="{href}">{label}</a>')
    parts.append("</div>")
    parts.append("</div>")


def _html_uncertain_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    page_link = (
        f'<a href="{meta.url}">{meta.page_id} — {meta.title}</a>'
        if meta.url
        else f"{meta.page_id} — {meta.title}"
    )
    parts.append('<div class="page-block" style="border-left:4px solid #e67e22;">')
    parts.append(f"<h3>{page_link} <em>[UNCERTAIN]</em></h3>")
    if bundle:
        parts.append("<p><strong>Triggered by:</strong> ")
        sources = ", ".join(
            f'<a href="{t.source_url}">{t.source_id}</a> ({t.source_type})'
            for t in bundle.triggers
        )
        parts.append(sources + "</p>")
        parts.append('<p class="scores">')
        for trig in bundle.triggers:
            parts.append(
                f"{trig.source_id}: Stage4={trig.stage4_final_score:.4f} "
                f"BiEnc={trig.biencoder_max_chunk_score:.4f} "
                f"CE={trig.crossencoder_final_score:.4f}<br>"
            )
        parts.append("</p>")
    parts.append(
        f"<p><strong>LLM Reasoning:</strong> {_html_escape(assessment.reasoning)}</p>"
    )
    parts.append('<div class="feedback"><strong>Feedback:</strong><br>')
    for category, label in _FEEDBACK_CATEGORIES:
        href = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        parts.append(f'<a href="{href}">{label}</a>')
    parts.append("</div>")
    parts.append("</div>")


def _html_rejected_section(parts: list[str], rc: RejectedCandidate) -> None:
    score_str = ""
    if rc.crossencoder_score is not None:
        score_str = (
            f" (CE={rc.crossencoder_score:.4f}, "
            f"reranked={rc.reranked_score:.4f})"
        )
    parts.append(
        f"<li>Source: <strong>{rc.source_id}</strong> &rarr; "
        f"Page: <strong>{rc.ipfr_page_id}</strong> — "
        f"rejected at <em>{rc.rejection_stage}</em>{score_str}</li>"
    )


# ---------------------------------------------------------------------------
# Mailto and feedback helpers
# ---------------------------------------------------------------------------


def _mailto_link(to: str, subject: str, body: str) -> str:
    """Return a mailto: URL string."""
    params = urllib.parse.urlencode(
        {"subject": subject, "body": body},
        quote_via=urllib.parse.quote,
    )
    return f"mailto:{to}?{params}"


def _feedback_body(
    run_id: str,
    page_id: str,
    bundle: TriggerBundle | None,
    category: str,
) -> str:
    source_ids = ",".join(bundle.source_ids) if bundle else ""
    return (
        f"run_id: {run_id}\n"
        f"page_id: {page_id}\n"
        f"source_id: {source_ids}\n"
        f"category: {category}\n"
        f"\n"
        f"Additional comments (optional):\n"
    )


# ---------------------------------------------------------------------------
# MIME and SMTP helpers
# ---------------------------------------------------------------------------


def _build_mime(
    subject: str,
    body_text: str,
    body_html: str,
    sender: str,
    recipient: str,
    reply_to: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = reply_to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def _send_via_smtp(
    msg: MIMEMultipart,
    host: str,
    port: int,
    user: str,
    password: str,
    sender: str,
    recipient: str,
) -> None:
    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.sendmail(sender, [recipient], msg.as_string())


def _write_fallback(body_text: str, run_id: str) -> str:
    """Write the email body to a local fallback file. Returns the file path."""
    import pathlib

    path = pathlib.Path("data/logs") / f"email_fallback_{run_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body_text, encoding="utf-8")
    return str(path)


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for user-provided content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
