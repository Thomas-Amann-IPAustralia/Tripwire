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

from src import email_html as eh
from src import email_security as sec
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
    recipient: str = sec.notification_address(config, "content_owner_email")
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

    if not recipient:
        # Fail closed: keep the content in a fallback file and report the error.
        fallback = _write_fallback(body_text, run_id)
        logger.error(
            "Stage 9: no recipient configured (set the CONTENT_OWNER_EMAIL secret) "
            "— FALLBACK email saved → %s", fallback,
        )
        return NotificationResult(
            sent=False,
            subject=subject,
            change_required_count=len(change_required),
            uncertain_count=len(uncertain),
            error_message="no recipient configured",
            fallback_file=fallback,
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
    html_parts.append(eh.stat_tiles([
        (str(len(change_required)), "Amendment required", "red" if change_required else None),
        (str(len(uncertain)), "Needs human review", "amber" if uncertain else None),
        (str(len(rejected_candidates)), "Rejected at deep analysis", None),
    ]))

    # ---- CHANGE_REQUIRED section -----------------------------------------
    if change_required:
        text_parts.append("=" * 70)
        text_parts.append("AMENDMENT REQUIRED")
        text_parts.append("=" * 70)
        html_parts.append(eh.section_heading("Amendment required", "red", len(change_required)))

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
            eh.section_heading("Items requiring human review", "amber", len(uncertain))
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
            eh.section_heading("Candidates rejected at deep analysis", "grey",
                               len(rejected_candidates))
            + eh.paragraph("Surfaced for calibration — potential false positives in "
                           "upstream stages.", size=13, color=eh.MUTED)
        )
        rejected_items: list[str] = []
        for rc in rejected_candidates:
            _txt_rejected_section(text_parts, rc)
            _html_rejected_section(rejected_items, rc)
        html_parts.append(eh.bullet_list(rejected_items))

    text_parts.append("")
    text_parts.append(
        f"Run ID: {run_id} | Generated by Tripwire"
    )
    body_html = eh.document(
        title=subject,
        preheader=(f"{total_flagged} IPFR page{'s' if total_flagged != 1 else ''} "
                   f"require attention."),
        eyebrow="Tripwire · Run report",
        heading=(f"{total_flagged} IPFR page{'s' if total_flagged != 1 else ''} "
                 f"flagged"),
        subheading=f"{run_date} · Run {run_id}",
        body="\n".join(html_parts),
        footer=_html_footer(f"Run ID: {run_id}"),
    )

    return subject, "\n".join(text_parts), body_html


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
#
# Layout and styling live in src/email_html.py (Outlook-safe tables with
# inline styles). These builders append rendered blocks to ``parts``; the
# composer wraps the result with ``email_html.document``.

# Short button text for each feedback category (the full label is shown
# beside the button and used verbatim in the plain-text part).
_FEEDBACK_BUTTONS: dict[str, str] = {
    "useful": "Useful",
    "not_significant": "Not significant",
    "wrong_amendment": "Wrong amendment",
    "wrong_page": "Wrong page",
}


def _html_page_title(meta: PageMeta) -> str:
    title = (
        f'<span style="color:{eh.MUTED};font-weight:600;">{eh.esc(meta.page_id)}</span>'
        f"&nbsp; {eh.esc(meta.title)}"
    )
    if meta.url:
        title = (
            f'<a href="{eh.esc(meta.url)}" style="color:{eh.INK};text-decoration:none;">'
            f"{title}</a>"
        )
    return (
        f'<div style="font-family:{eh.FONT};font-size:17px;line-height:24px;'
        f'font-weight:700;color:{eh.INK};padding:10px 0 2px 0;">{title}</div>'
    )


def _html_badges(verdict_text: str, tone: str, extras: list[str]) -> str:
    meta_line = " &nbsp;&middot;&nbsp; ".join(eh.esc(x) for x in extras if x)
    return (
        eh.pill(verdict_text, tone, solid=True)
        + (f'&nbsp;&nbsp;<span style="font-family:{eh.FONT};font-size:12px;'
           f'color:{eh.MUTED};">{meta_line}</span>' if meta_line else "")
    )


def _html_triggers(bundle: TriggerBundle) -> str:
    rows = []
    for trig in bundle.triggers:
        name = (
            f'<a href="{eh.esc(trig.source_url)}" style="color:{eh.LINK};'
            f'font-weight:600;text-decoration:none;">{eh.esc(trig.source_id)}</a>'
            if trig.source_url else f"<strong>{eh.esc(trig.source_id)}</strong>"
        )
        scores = (
            f"Relevance {trig.stage4_final_score:.4f} &nbsp;&middot;&nbsp; "
            f"Bi-encoder {trig.biencoder_max_chunk_score:.4f} &nbsp;&middot;&nbsp; "
            f"Cross-encoder {trig.crossencoder_final_score:.4f}"
        )
        rows.append(
            "<tr>"
            f'<td valign="top" style="padding:8px 12px;border-bottom:1px solid {eh.BORDER};'
            f'font-family:{eh.FONT};font-size:14px;line-height:20px;">'
            f"{name}&nbsp; {eh.pill(trig.source_type, 'grey')}"
            f'<div style="font-family:{eh.FONT};font-size:12px;line-height:18px;'
            f'color:{eh.MUTED};padding-top:2px;">{scores}</div></td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" bgcolor="#f9fafb" style="background-color:#f9fafb;'
        f'border:1px solid {eh.BORDER};border-bottom:0;">' + "".join(rows) + "</table>"
    )


def _html_feedback(
    meta: PageMeta, bundle: TriggerBundle | None, run_id: str, feedback_email: str
) -> str:
    buttons = []
    for category, label in _FEEDBACK_CATEGORIES:
        href = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        desc = label.split(" — ", 1)[1] if " — " in label else label
        buttons.append((href, _FEEDBACK_BUTTONS.get(category, category), desc[:1].upper() + desc[1:]))
    return (
        eh.divider(top=20)
        + eh.label("Was this alert right? One click opens a pre-filled reply", top=14)
        + eh.button_rows(buttons)
    )


def _html_change_required_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
    run_label: str | None = None,
) -> None:
    inner = [
        _html_badges(
            "Amendment required", "red",
            [f"Confidence {assessment.confidence:.0%}", assessment.model,
             f"Run {run_label}" if run_label else ""],
        ),
        _html_page_title(meta),
    ]
    if bundle:
        inner.append(eh.label("Triggered by"))
        inner.append(_html_triggers(bundle))

    inner.append(eh.label("Why it matters"))
    inner.append(eh.paragraph(eh.esc(assessment.reasoning), bottom=0))

    if assessment.suggested_changes:
        inner.append(eh.label("Suggested changes", top=20))
        inner.append(eh.numbered_list(assessment.suggested_changes))

    if bundle:
        for trig in bundle.triggers:
            diff_preview = trig.diff_text[:3000]
            if len(trig.diff_text) > 3000:
                diff_preview += " ... [truncated]"
            heading = "What changed" if len(bundle.triggers) == 1 else (
                f"What changed — {trig.source_id}")
            inner.append(eh.label(heading, top=12))
            inner.append(eh.diff_block(diff_preview))

    inner.append(_html_feedback(meta, bundle, run_id, feedback_email))
    parts.append(eh.card("".join(inner), "red"))


def _html_uncertain_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
    run_label: str | None = None,
) -> None:
    inner = [
        _html_badges(
            "Needs human review", "amber",
            [f"Confidence {assessment.confidence:.0%}", assessment.model,
             f"Run {run_label}" if run_label else ""],
        ),
        _html_page_title(meta),
    ]
    if bundle:
        inner.append(eh.label("Triggered by"))
        inner.append(_html_triggers(bundle))
    inner.append(eh.label("LLM reasoning"))
    inner.append(eh.paragraph(eh.esc(assessment.reasoning), bottom=0))
    inner.append(_html_feedback(meta, bundle, run_id, feedback_email))
    parts.append(eh.card("".join(inner), "amber"))


def _html_rejected_section(parts: list[str], rc: RejectedCandidate) -> None:
    score_str = ""
    if rc.crossencoder_score is not None:
        reranked = (f", reranked {rc.reranked_score:.4f}"
                    if rc.reranked_score is not None else "")
        score_str = (
            f'<span style="color:{eh.MUTED};font-size:12px;"> &nbsp;cross-encoder '
            f"{rc.crossencoder_score:.4f}{reranked}</span>"
        )
    parts.append(
        f"<strong>{eh.esc(rc.source_id)}</strong> &rarr; "
        f"<strong>{eh.esc(rc.ipfr_page_id)}</strong> &nbsp;"
        f"{eh.pill(rc.rejection_stage, 'grey')}{score_str}"
    )


def _html_footer(runs_text: str) -> str:
    return (
        f"{eh.esc(runs_text)}<br>"
        "Generated automatically by Tripwire &middot; IP First Response change monitoring"
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
        f"token: {sec.feedback_token(run_id, page_id, category)}\n"
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
    """Build the MIME message, stamping the security phrase into both parts.

    Stamping happens here (not at composition) so the phrase never reaches
    fallback files or dry-run output, which can become public.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = reply_to
    msg.attach(MIMEText(sec.stamp_text(body_text), "plain", "utf-8"))
    msg.attach(MIMEText(sec.stamp_html(body_html), "html", "utf-8"))
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
    path.write_text(sec.redact_tokens(body_text), encoding="utf-8")
    return str(path)


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for user-provided content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
