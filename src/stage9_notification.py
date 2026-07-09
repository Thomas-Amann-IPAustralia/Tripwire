"""
src/stage9_notification.py

Stage 9 — Notification (Section 3.9)

Purpose: send one consolidated email per run to the content owner, summarising
all amendment suggestions from Stage 8.

Email structure:
  • Subject: "Tripwire — {date} — {N} IPFR page(s) flagged"
  • A summary banner with per-category counts.
  • One card per CHANGE_REQUIRED page:
      - IPFR page identifier and title
      - Source(s) that triggered the alert (with URLs) and their scores
      - Normalised diff text
      - LLM reasoning and full suggested_changes entries
      - Four mailto feedback links
  • "Items requiring human review" section for UNCERTAIN verdicts.
  • "Didn't make the cut" section — a single compact table of everything that
    was considered but rejected, WITH the numeric rationale (the score it
    achieved, the threshold it needed, and how close it came).  This covers:
      - pages dropped at the bi-encoder gate (Stage 5)
      - pages dropped at the cross-encoder gate (Stage 6)
      - pages the LLM assessed as NO_CHANGE (Stage 8)
    Rows are sorted closest-to-passing first, so the near-misses that matter
    for threshold calibration are at the top.

No-alert policy: if no pages are flagged (no CHANGE_REQUIRED or UNCERTAIN),
the email is not sent — the "didn't make the cut" context only rides along
with an email that is already being sent for a real alert.

Email delivery: Python smtplib with a Gmail app password stored in
SMTP_PASSWORD environment variable.  All styling is inline (no <head> CSS
block) because Outlook and several webmail clients strip <style> blocks;
the layout is table-based for the same reason.

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

from src.stage7_aggregation import TriggerBundle
from src.stage8_llm import LLMAssessment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette (inline colours — kept restrained for a government audience)
# ---------------------------------------------------------------------------

_C_NAVY = "#1a2b4a"
_C_RED = "#c0392b"
_C_AMBER = "#e67e22"
_C_GREY = "#6b7280"
_C_GREEN = "#2e7d32"
_C_BG = "#f4f5f7"
_C_CARD = "#ffffff"
_C_BORDER = "#e2e5ea"
_C_TEXT = "#222222"
_C_MUTED = "#6b7280"

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
# Human-readable labels for each rejection stage
# ---------------------------------------------------------------------------

_REJECTION_STAGE_LABELS: dict[str, str] = {
    "stage4": "Relevance (Stage 4)",
    "biencoder": "Bi-encoder (Stage 5)",
    "crossencoder": "Cross-encoder (Stage 6)",
    "llm_no_change": "LLM: no change (Stage 8)",
    "llm_schema": "LLM: invalid output (Stage 8)",
    "llm_permanent": "LLM: error (Stage 8)",
}

# Order in which rejection stages appear in the table (earliest gate first).
_REJECTION_STAGE_ORDER: list[str] = [
    "stage4",
    "biencoder",
    "crossencoder",
    "llm_no_change",
    "llm_schema",
    "llm_permanent",
]


# ---------------------------------------------------------------------------
# Rejected candidate record (supplied by the pipeline from Stage 6 output)
# ---------------------------------------------------------------------------


@dataclass
class RejectedCandidate:
    """A page that was considered but did not make the cut.

    Carries the numeric rationale for the rejection so the content team can
    see how close each near-miss came to the threshold.
    """

    source_id: str
    source_url: str
    ipfr_page_id: str
    rejection_stage: str
    """'stage4' | 'biencoder' | 'crossencoder' | 'llm_no_change' | 'llm_schema' | 'llm_permanent'"""

    # Unified numeric rationale ------------------------------------------------
    score: float | None = None
    """The decisive score this candidate achieved at the rejecting stage."""
    threshold: float | None = None
    """The bar the score needed to clear (None when the stage has no fixed bar)."""
    score_label: str = "Score"
    """Human label for `score` (e.g. 'Bi-encoder max-chunk')."""
    note: str | None = None
    """Free-text supplementary rationale (e.g. chunk counts, LLM reasoning)."""
    page_title: str | None = None

    # Legacy / supplementary cross-encoder detail (kept for back-compat) -------
    crossencoder_score: float | None = None
    reranked_score: float | None = None

    def __post_init__(self) -> None:
        # Back-compat: callers that only supplied the old cross-encoder fields
        # still get a populated unified `score`/`threshold`.
        if self.score is None and self.reranked_score is not None:
            self.score = self.reranked_score

    @property
    def stage_label(self) -> str:
        return _REJECTION_STAGE_LABELS.get(self.rejection_stage, self.rejection_stage)

    @property
    def gap(self) -> float | None:
        """How far short of the threshold (positive = short, negative = cleared)."""
        if self.score is None or self.threshold is None:
            return None
        return self.threshold - self.score

    def _sort_key(self) -> tuple[float, float]:
        """Sort closest-to-passing first, then by raw score descending."""
        gap = self.gap
        gap_key = gap if gap is not None else 1e9
        score_key = -(self.score if self.score is not None else -1e9)
        return (gap_key, score_key)


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
        Pages rejected at Stage 5/6 (for the "didn't make the cut" section).
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
    no_change = [a for a in assessments if a.verdict == "NO_CHANGE"]

    if not change_required and not uncertain:
        logger.info(
            "Stage 9: no pages flagged (%d NO_CHANGE, %d total assessments) — email not sent.",
            len(no_change),
            len(assessments),
        )
        return NotificationResult(
            sent=False,
            change_required_count=0,
            uncertain_count=0,
            observation_data={"reason": "no_alerts"},
        )

    # Fold NO_CHANGE assessments into the "didn't make the cut" list so the
    # team can see the pages that reached the LLM but were judged not to need a
    # change — the numeric context (scores that got them there + LLM verdict)
    # is the most valuable calibration signal.
    ce_threshold = float(
        config.get("semantic_scoring", {})
        .get("crossencoder", {})
        .get("threshold", 0.60)
    )
    llm_change_conf = 0.70  # confidence bar for CHANGE_REQUIRED (Stage 8 prompt)
    all_rejected = list(rejected_candidates) + _no_change_rows(
        no_change, bundles_by_page, page_meta_by_id, ce_threshold, llm_change_conf
    )

    subject, body_text, body_html = _compose_email(
        change_required=change_required,
        uncertain=uncertain,
        rejected_candidates=all_rejected,
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
                    "no_change": len(no_change),
                    "rejected_candidates": len(all_rejected),
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
            "no_change": len(no_change),
        },
    )


def _no_change_rows(
    no_change: list[LLMAssessment],
    bundles_by_page: dict[str, TriggerBundle],
    page_meta_by_id: dict[str, PageMeta],
    ce_threshold: float,
    change_conf: float,
) -> list[RejectedCandidate]:
    """Convert NO_CHANGE assessments into 'didn't make the cut' rows.

    These pages cleared every upstream gate and reached the LLM, which then
    judged that no amendment was needed.  We surface the cross-encoder score
    (why it reached the LLM) plus the LLM's confidence and reasoning.
    """
    rows: list[RejectedCandidate] = []
    for a in no_change:
        bundle = bundles_by_page.get(a.ipfr_page_id)
        meta = page_meta_by_id.get(a.ipfr_page_id)
        ce_final = bundle.max_crossencoder_score if bundle else None
        source_ids = ", ".join(bundle.source_ids) if bundle else ""
        note = f"LLM confidence {a.confidence:.0%}. {a.reasoning}"
        rows.append(
            RejectedCandidate(
                source_id=source_ids,
                source_url="",
                ipfr_page_id=a.ipfr_page_id,
                rejection_stage="llm_no_change",
                score=ce_final,
                threshold=ce_threshold,
                score_label="Cross-encoder",
                note=note,
                page_title=meta.title if meta else None,
            )
        )
    return rows


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
    _html_header(
        html_parts,
        subject,
        run_id,
        run_date,
        n_change=len(change_required),
        n_uncertain=len(uncertain),
        n_rejected=len(rejected_candidates),
    )

    # ---- CHANGE_REQUIRED section -----------------------------------------
    if change_required:
        text_parts.append("=" * 70)
        text_parts.append("AMENDMENT REQUIRED")
        text_parts.append("=" * 70)
        html_parts.append(
            _html_section_heading("Amendment Required", _C_RED)
        )

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
            _html_section_heading("Items Requiring Human Review", _C_AMBER)
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

    # ---- Didn't make the cut section -------------------------------------
    if rejected_candidates:
        ordered = _sort_rejected(rejected_candidates)
        text_parts.append("")
        text_parts.append("=" * 70)
        text_parts.append("CANDIDATES REJECTED — DIDN'T MAKE THE CUT")
        text_parts.append(
            "(Considered but filtered out — score achieved vs. threshold needed. "
            "Sorted closest-to-passing first; surfaced for calibration.)"
        )
        text_parts.append("=" * 70)
        _txt_rejected_table(text_parts, ordered)
        _html_rejected_table(html_parts, ordered)

    text_parts.append("")
    text_parts.append(f"Run ID: {run_id} | Generated by Tripwire")
    _html_footer(html_parts, run_id)

    return subject, "\n".join(text_parts), "\n".join(html_parts)


def _sort_rejected(rejected: list[RejectedCandidate]) -> list[RejectedCandidate]:
    """Group by rejection stage (pipeline order), closest-to-passing first."""
    return sorted(
        rejected,
        key=lambda rc: (
            _REJECTION_STAGE_ORDER.index(rc.rejection_stage)
            if rc.rejection_stage in _REJECTION_STAGE_ORDER
            else len(_REJECTION_STAGE_ORDER),
            *rc._sort_key(),
        ),
    )


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


def _txt_rejected_table(parts: list[str], rejected: list[RejectedCandidate]) -> None:
    """Aligned plain-text table of rejected candidates."""
    parts.append("")
    header = (
        f"  {'Page':<12} {'Stage':<26} {'Score':>8} {'Needs':>8} {'Gap':>8}  Source"
    )
    parts.append(header)
    parts.append("  " + "-" * (len(header) - 2))
    for rc in rejected:
        score_s = f"{rc.score:.4f}" if rc.score is not None else "—"
        thr_s = f"{rc.threshold:.4f}" if rc.threshold is not None else "—"
        gap = rc.gap
        if gap is None:
            gap_s = "—"
        elif gap > 0:
            gap_s = f"-{gap:.4f}"   # short of the bar
        else:
            gap_s = f"+{-gap:.4f}"  # cleared the bar (e.g. NO_CHANGE at LLM)
        parts.append(
            f"  {rc.ipfr_page_id:<12} {rc.rejection_stage:<26} "
            f"{score_s:>8} {thr_s:>8} {gap_s:>8}  {rc.source_id}"
        )
        if rc.note:
            note = rc.note if len(rc.note) <= 200 else rc.note[:197] + "..."
            parts.append(f"      ↳ {note}")
    parts.append("")


# ---------------------------------------------------------------------------
# HTML section builders (all styling inline; table-based layout)
# ---------------------------------------------------------------------------


def _html_header(
    parts: list[str],
    subject: str,
    run_id: str,
    run_date: str,
    n_change: int,
    n_uncertain: int,
    n_rejected: int,
) -> None:
    parts.append(
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        "</head>"
        f'<body style="margin:0;padding:0;background:{_C_BG};'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'color:{_C_TEXT};">'
    )
    # Outer wrapper table (full-width background) → centred 600px container.
    parts.append(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{_C_BG};"><tr><td align="center" '
        'style="padding:24px 12px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'border="0" style="width:600px;max-width:600px;">'
    )
    # Banner
    parts.append(
        f'<tr><td style="background:{_C_NAVY};border-radius:8px 8px 0 0;'
        'padding:20px 24px;">'
        '<div style="font-size:12px;letter-spacing:1px;text-transform:uppercase;'
        'color:#9fb0cc;font-weight:600;">Tripwire · IPFR Monitoring</div>'
        f'<div style="font-size:20px;font-weight:700;color:#ffffff;'
        f'margin-top:4px;">{_html_escape(subject)}</div>'
        f'<div style="font-size:13px;color:#c3ccdb;margin-top:6px;">'
        f'Run <code style="color:#e6ebf3;">{_html_escape(run_id)}</code> '
        f'&nbsp;·&nbsp; {_html_escape(run_date)}</div>'
        "</td></tr>"
    )
    # Stat tiles row
    parts.append(
        f'<tr><td style="background:{_C_CARD};padding:16px 12px;'
        f'border-left:1px solid {_C_BORDER};border-right:1px solid {_C_BORDER};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0"><tr>'
    )
    parts.append(_html_stat_tile(n_change, "Amendment required", _C_RED))
    parts.append(_html_stat_tile(n_uncertain, "Needs human review", _C_AMBER))
    parts.append(_html_stat_tile(n_rejected, "Didn't make the cut", _C_GREY))
    parts.append("</tr></table></td></tr>")
    # Body container open
    parts.append(
        f'<tr><td style="background:{_C_CARD};padding:8px 24px 24px 24px;'
        f'border-left:1px solid {_C_BORDER};border-right:1px solid {_C_BORDER};">'
    )


def _html_stat_tile(count: int, label: str, colour: str) -> str:
    return (
        '<td align="center" style="padding:6px;">'
        f'<div style="font-size:28px;font-weight:700;line-height:1;'
        f'color:{colour};">{count}</div>'
        f'<div style="font-size:11px;color:{_C_MUTED};margin-top:4px;'
        'text-transform:uppercase;letter-spacing:0.5px;">'
        f'{_html_escape(label)}</div></td>'
    )


def _html_section_heading(text: str, colour: str) -> str:
    return (
        f'<h2 style="font-size:16px;color:{colour};margin:20px 0 8px 0;'
        f'padding-bottom:6px;border-bottom:2px solid {colour};">'
        f'{_html_escape(text)}</h2>'
    )


def _html_score_chips(bundle: TriggerBundle | None) -> str:
    """Render a compact per-source score row as inline chips."""
    if not bundle:
        return ""
    rows = []
    for trig in bundle.triggers:
        chips = (
            _html_chip("Stage 4", f"{trig.stage4_final_score:.4f}")
            + _html_chip("Bi-enc", f"{trig.biencoder_max_chunk_score:.4f}")
            + _html_chip("Cross-enc", f"{trig.crossencoder_final_score:.4f}")
        )
        rows.append(
            f'<div style="margin:4px 0;font-size:12px;color:{_C_MUTED};">'
            f'<strong style="color:{_C_TEXT};">{_html_escape(trig.source_id)}</strong> '
            f"&nbsp;{chips}</div>"
        )
    return "".join(rows)


def _html_chip(label: str, value: str) -> str:
    return (
        f'<span style="display:inline-block;background:{_C_BG};'
        f'border:1px solid {_C_BORDER};border-radius:3px;padding:1px 6px;'
        f'margin:0 2px;font-size:11px;color:{_C_TEXT};">'
        f'{_html_escape(label)} <strong>{_html_escape(value)}</strong></span>'
    )


def _html_change_required_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    title = f"{meta.page_id} — {meta.title}"
    page_link = (
        f'<a href="{_html_attr(meta.url)}" style="color:{_C_NAVY};'
        f'text-decoration:none;">{_html_escape(title)}</a>'
        if meta.url
        else _html_escape(title)
    )
    parts.append(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border:1px solid {_C_BORDER};border-left:4px solid '
        f'{_C_RED};border-radius:4px;margin:12px 0;"><tr><td style="padding:16px;">'
    )
    parts.append(
        f'<div style="font-size:15px;font-weight:700;">{page_link}</div>'
        f'<div style="font-size:12px;color:{_C_MUTED};margin-top:4px;">'
        f'Confidence {assessment.confidence:.0%} &nbsp;·&nbsp; '
        f'Model {_html_escape(assessment.model)}</div>'
    )
    if bundle:
        parts.append(
            f'<div style="font-size:12px;color:{_C_MUTED};margin-top:10px;'
            'font-weight:600;">Triggered by</div>'
        )
        parts.append(_html_score_chips(bundle))
        # Diff block (always visible, bordered, monospace).
        for trig in bundle.triggers:
            diff_preview = trig.diff_text[:3000]
            if len(trig.diff_text) > 3000:
                diff_preview += "\n... [truncated]"
            parts.append(
                f'<div style="font-size:11px;color:{_C_MUTED};margin-top:8px;">'
                f'Change document · {_html_escape(trig.source_id)}</div>'
                f'<div style="background:{_C_BG};border:1px solid {_C_BORDER};'
                'border-radius:3px;padding:8px;font-family:Consolas,Menlo,monospace;'
                'font-size:11px;white-space:pre-wrap;word-break:break-word;'
                f'color:{_C_TEXT};margin-top:2px;">{_html_escape(diff_preview)}</div>'
            )
    parts.append(
        f'<div style="margin-top:12px;font-size:13px;"><strong>Reasoning:</strong> '
        f'{_html_escape(assessment.reasoning)}</div>'
    )
    parts.append(
        '<div style="margin-top:10px;font-size:13px;font-weight:600;">'
        "Suggested changes</div><ol style=\"margin:4px 0 0 0;padding-left:20px;"
        'font-size:13px;">'
    )
    for change in assessment.suggested_changes:
        parts.append(f"<li style=\"margin:3px 0;\">{_html_escape(change)}</li>")
    parts.append("</ol>")
    _html_feedback_buttons(parts, meta, bundle, run_id, feedback_email)
    parts.append("</td></tr></table>")


def _html_uncertain_section(
    parts: list[str],
    assessment: LLMAssessment,
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    title = f"{meta.page_id} — {meta.title}"
    page_link = (
        f'<a href="{_html_attr(meta.url)}" style="color:{_C_NAVY};'
        f'text-decoration:none;">{_html_escape(title)}</a>'
        if meta.url
        else _html_escape(title)
    )
    parts.append(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border:1px solid {_C_BORDER};border-left:4px solid '
        f'{_C_AMBER};border-radius:4px;margin:12px 0;"><tr><td style="padding:16px;">'
    )
    parts.append(
        f'<div style="font-size:15px;font-weight:700;">{page_link} '
        f'<span style="font-size:11px;color:{_C_AMBER};font-weight:600;">'
        "[UNCERTAIN]</span></div>"
    )
    if bundle:
        parts.append(
            f'<div style="font-size:12px;color:{_C_MUTED};margin-top:10px;'
            'font-weight:600;">Triggered by</div>'
        )
        parts.append(_html_score_chips(bundle))
    parts.append(
        f'<div style="margin-top:12px;font-size:13px;"><strong>Reasoning:</strong> '
        f'{_html_escape(assessment.reasoning)}</div>'
    )
    _html_feedback_buttons(parts, meta, bundle, run_id, feedback_email)
    parts.append("</td></tr></table>")


def _html_feedback_buttons(
    parts: list[str],
    meta: PageMeta,
    bundle: TriggerBundle | None,
    run_id: str,
    feedback_email: str,
) -> None:
    parts.append(
        f'<div style="margin-top:14px;font-size:12px;color:{_C_MUTED};'
        'font-weight:600;">Feedback (one click opens a pre-filled reply)</div>'
        '<div style="margin-top:6px;">'
    )
    for category, label in _FEEDBACK_CATEGORIES:
        href = _mailto_link(
            to=feedback_email,
            subject=f"[TRIPWIRE] Feedback — {run_id} — {meta.page_id}",
            body=_feedback_body(run_id, meta.page_id, bundle, category),
        )
        short = label.split(" — ")[0]
        parts.append(
            f'<a href="{_html_attr(href)}" title="{_html_attr(label)}" '
            f'style="display:inline-block;margin:3px 4px 3px 0;padding:6px 12px;'
            f'background:{_C_BG};border:1px solid {_C_BORDER};border-radius:4px;'
            f'text-decoration:none;color:{_C_NAVY};font-size:12px;">'
            f'{_html_escape(short)}</a>'
        )
    parts.append("</div>")


def _html_rejected_table(parts: list[str], rejected: list[RejectedCandidate]) -> None:
    parts.append(_html_section_heading("Didn't Make the Cut", _C_GREY))
    parts.append(
        f'<div style="font-size:12px;color:{_C_MUTED};margin:-4px 0 10px 0;">'
        "Considered but filtered out — the score each candidate achieved versus "
        "the threshold it needed. Sorted closest-to-passing first for calibration."
        "</div>"
    )
    parts.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border:1px solid {_C_BORDER};border-radius:4px;'
        'border-collapse:collapse;font-size:12px;">'
    )
    parts.append(
        f'<tr style="background:{_C_BG};color:{_C_MUTED};text-align:left;">'
        '<th style="padding:8px 10px;font-weight:600;">Page</th>'
        '<th style="padding:8px 10px;font-weight:600;">Rejected at</th>'
        '<th style="padding:8px 10px;font-weight:600;">Score → needs</th>'
        '<th style="padding:8px 10px;font-weight:600;text-align:right;">Gap</th>'
        "</tr>"
    )
    for rc in rejected:
        page_cell = _html_escape(rc.ipfr_page_id)
        if rc.page_title:
            page_cell += (
                f'<br><span style="color:{_C_MUTED};font-size:11px;">'
                f'{_html_escape(rc.page_title)}</span>'
            )
        if rc.source_id:
            page_cell += (
                f'<br><span style="color:{_C_MUTED};font-size:11px;">via '
                f'{_html_escape(rc.source_id)}</span>'
            )
        gap = rc.gap
        if gap is None:
            gap_html = f'<span style="color:{_C_MUTED};">—</span>'
        elif gap > 0:
            gap_html = f'<span style="color:{_C_RED};font-weight:600;">-{gap:.3f}</span>'
        else:
            gap_html = (
                f'<span style="color:{_C_GREEN};font-weight:600;">+{-gap:.3f}</span>'
            )
        score_cell = _html_score_vs_threshold(rc)
        note_html = ""
        if rc.note:
            note = rc.note if len(rc.note) <= 240 else rc.note[:237] + "..."
            note_html = (
                f'<div style="color:{_C_MUTED};font-size:11px;margin-top:3px;">'
                f'{_html_escape(note)}</div>'
            )
        parts.append(
            f'<tr style="border-top:1px solid {_C_BORDER};vertical-align:top;">'
            f'<td style="padding:8px 10px;">{page_cell}</td>'
            f'<td style="padding:8px 10px;">{_html_escape(rc.stage_label)}</td>'
            f'<td style="padding:8px 10px;">{score_cell}{note_html}</td>'
            f'<td style="padding:8px 10px;text-align:right;">{gap_html}</td>'
            "</tr>"
        )
    parts.append("</table>")


def _html_score_vs_threshold(rc: RejectedCandidate) -> str:
    """Score → threshold with a colour reflecting how close it came."""
    score_s = f"{rc.score:.3f}" if rc.score is not None else "—"
    thr_s = f"{rc.threshold:.3f}" if rc.threshold is not None else "—"
    gap = rc.gap
    if gap is None:
        colour = _C_MUTED
    elif gap <= 0:
        colour = _C_GREEN
    elif gap <= 0.10:
        colour = _C_AMBER
    else:
        colour = _C_RED
    return (
        f'<span style="color:{_C_MUTED};font-size:11px;">{_html_escape(rc.score_label)}</span> '
        f'<strong style="color:{colour};">{score_s}</strong>'
        f'<span style="color:{_C_MUTED};"> → {thr_s}</span>'
    )


def _html_footer(parts: list[str], run_id: str) -> None:
    # Close body container cell, then footer row, then wrapper tables.
    parts.append("</td></tr>")
    parts.append(
        f'<tr><td style="background:{_C_CARD};border:1px solid {_C_BORDER};'
        'border-top:none;border-radius:0 0 8px 8px;padding:14px 24px;'
        f'font-size:11px;color:{_C_MUTED};">'
        f'Run ID <code>{_html_escape(run_id)}</code> · Generated automatically by '
        "Tripwire. Reply to this email to send feedback."
        "</td></tr>"
    )
    parts.append("</table></td></tr></table></body></html>")


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
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_attr(text: str) -> str:
    """Escape a value destined for an HTML attribute (href/title)."""
    return _html_escape(text).replace("'", "&#39;")
