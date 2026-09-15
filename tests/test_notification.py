"""
tests/test_notification.py

Tests for src/stage9_notification.py — email composition and Stage 9 output.
No real SMTP connections are made.
"""

from __future__ import annotations

import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from src.stage7_aggregation import TriggerBundle, TriggerSource
from src.stage8_llm import LLMAssessment
from src.stage9_notification import (
    NotificationResult,
    PageMeta,
    RejectedCandidate,
    _compose_email,
    _feedback_body,
    _html_escape,
    _mailto_link,
    send_notification,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_assessment(
    page_id: str = "B1012",
    verdict: str = "CHANGE_REQUIRED",
    confidence: float = 0.85,
    reasoning: str = "The examination period changed from 12 to 6 months.",
    suggested_changes: list[str] | None = None,
) -> LLMAssessment:
    return LLMAssessment(
        ipfr_page_id=page_id,
        verdict=verdict,
        confidence=confidence,
        reasoning=reasoning,
        suggested_changes=suggested_changes or ["Update period to 6 months."],
        model="gpt-4o",
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
        retries=0,
        schema_valid=True,
        raw_response="{}",
        processing_time_seconds=1.2,
    )


def _make_bundle(page_id: str = "B1012") -> TriggerBundle:
    bundle = TriggerBundle(ipfr_page_id=page_id)
    bundle.triggers.append(
        TriggerSource(
            source_id="TMAA2026",
            source_url="https://legislation.gov.au/TMAA2026",
            source_importance=0.9,
            source_type="frl",
            diff_text="Examination period reduced from 12 to 6 months.",
            significance="high",
            stage4_final_score=0.041,
            stage4_rrf_score=0.048,
            stage4_bm25_rank=1,
            stage4_semantic_rank=2,
            biencoder_max_chunk_score=0.81,
            biencoder_chunks_above_threshold=5,
            crossencoder_score=0.72,
            crossencoder_reranked_score=0.74,
            crossencoder_final_score=0.75,
        )
    )
    return bundle


def _simple_config(smtp_host: str = "smtp.gmail.com") -> dict:
    return {
        "pipeline": {
            "observation_mode": False,
            "max_retries": 1,
            "retry_base_delay_seconds": 0.01,
        },
        "notifications": {
            "content_owner_email": "owner@example.gov.au",
            "health_alert_email": "admin@example.gov.au",
            "feedback_email": "tripwire-feedback@gmail.com",
            "smtp": {"host": smtp_host, "port": 587},
        },
    }


# ---------------------------------------------------------------------------
# _mailto_link
# ---------------------------------------------------------------------------


def test_mailto_link_format():
    link = _mailto_link(
        to="feedback@example.com",
        subject="[TRIPWIRE] Feedback — run-001 — B1012",
        body="run_id: run-001\npage_id: B1012\ncategory: useful\n",
    )
    assert link.startswith("mailto:feedback@example.com?")
    # Decode and check subject is present.
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    assert "subject" in params
    assert "[TRIPWIRE]" in params["subject"][0]


def test_mailto_link_body_contains_fields():
    body = _feedback_body("run-001", "B1012", None, "useful")
    assert "run_id: run-001" in body
    assert "page_id: B1012" in body
    assert "category: useful" in body


def test_mailto_link_body_includes_source_ids():
    bundle = _make_bundle()
    body = _feedback_body("run-001", "B1012", bundle, "not_significant")
    assert "TMAA2026" in body


# ---------------------------------------------------------------------------
# _html_escape
# ---------------------------------------------------------------------------


def test_html_escape_ampersand():
    assert _html_escape("AT&T") == "AT&amp;T"


def test_html_escape_angle_brackets():
    assert _html_escape("<script>") == "&lt;script&gt;"


def test_html_escape_quotes():
    assert _html_escape('"test"') == "&quot;test&quot;"


def test_html_escape_no_change():
    assert _html_escape("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _compose_email
# ---------------------------------------------------------------------------


def _compose(
    change_required=None,
    uncertain=None,
    rejected=None,
    run_id="run-001",
    run_date="6 April 2026",
    feedback_email="tripwire-feedback@gmail.com",
):
    change_required = change_required or []
    uncertain = uncertain or []
    rejected = rejected or []
    bundles_by_page = {}
    page_meta_by_id = {}
    for a in change_required + uncertain:
        bundle = _make_bundle(a.ipfr_page_id)
        bundles_by_page[a.ipfr_page_id] = bundle
        page_meta_by_id[a.ipfr_page_id] = PageMeta(
            page_id=a.ipfr_page_id,
            title=f"Page {a.ipfr_page_id}",
            url=f"https://ipfr.example/{a.ipfr_page_id.lower()}",
        )
    return _compose_email(
        change_required=change_required,
        uncertain=uncertain,
        rejected_candidates=rejected,
        bundles_by_page=bundles_by_page,
        page_meta_by_id=page_meta_by_id,
        run_id=run_id,
        run_date=run_date,
        feedback_email=feedback_email,
    )


def test_compose_subject_includes_date_and_count():
    subject, _, _ = _compose(change_required=[_make_assessment()])
    assert "6 April 2026" in subject
    assert "1" in subject


def test_compose_subject_plural():
    subject, _, _ = _compose(
        change_required=[
            _make_assessment("B1012"),
            _make_assessment("B1013"),
        ]
    )
    assert "2" in subject
    assert "pages" in subject


def test_compose_change_required_in_text():
    _, text, _ = _compose(change_required=[_make_assessment()])
    assert "AMENDMENT REQUIRED" in text
    assert "B1012" in text
    assert "Update period to 6 months." in text


def test_compose_reasoning_in_text():
    _, text, _ = _compose(change_required=[_make_assessment()])
    assert "examination period" in text.lower()


def test_compose_uncertain_section():
    uncertain = _make_assessment(verdict="UNCERTAIN", suggested_changes=[])
    _, text, _ = _compose(uncertain=[uncertain])
    assert "ITEMS REQUIRING HUMAN REVIEW" in text
    assert "B1012" in text


def test_compose_rejected_candidates_section():
    rc = RejectedCandidate(
        source_id="SRC001",
        source_url="https://example.com",
        ipfr_page_id="B1099",
        rejection_stage="crossencoder",
        crossencoder_score=0.45,
        reranked_score=0.42,
    )
    _, text, _ = _compose(rejected=[rc])
    assert "CANDIDATES REJECTED" in text
    assert "B1099" in text
    assert "crossencoder" in text


def test_compose_feedback_mailto_links_in_text():
    _, text, _ = _compose(change_required=[_make_assessment()])
    assert "mailto:" in text
    # [TRIPWIRE] is URL-encoded as %5BTRIPWIRE%5D in the mailto href.
    assert "TRIPWIRE" in text


def test_compose_four_feedback_links():
    """Each page section must have exactly 4 feedback mailto links."""
    _, text, _ = _compose(change_required=[_make_assessment()])
    assert text.count("mailto:") == 4


def test_compose_html_contains_page_id():
    _, _, html = _compose(change_required=[_make_assessment()])
    assert "B1012" in html


def test_compose_html_suggested_changes_in_list():
    _, _, html = _compose(change_required=[_make_assessment()])
    assert "<ol>" in html
    assert "<li>" in html


def test_compose_no_alerts_returns_empty():
    """When there are no alerts, _compose_email should still produce output
    — the no-alert guard is in send_notification, not _compose_email."""
    # We just verify it doesn't crash.
    subject, text, html = _compose(change_required=[], uncertain=[])
    # Subject mentions 0 pages.
    assert "0" in subject


def test_compose_run_id_in_footer():
    _, text, _ = _compose(run_id="run-2026-001")
    assert "run-2026-001" in text


# ---------------------------------------------------------------------------
# send_notification — no alerts → no email
# ---------------------------------------------------------------------------


def test_send_notification_no_alerts():
    result = send_notification(
        assessments=[_make_assessment(verdict="NO_CHANGE", suggested_changes=[])],
        bundles_by_page={},
        page_meta_by_id={},
        rejected_candidates=[],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
    )
    assert result.sent is False
    assert result.observation_data.get("reason") == "no_alerts"


def test_send_notification_empty_assessments():
    result = send_notification(
        assessments=[],
        bundles_by_page={},
        page_meta_by_id={},
        rejected_candidates=[],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
    )
    assert result.sent is False


# ---------------------------------------------------------------------------
# send_notification — successful send via injected client
# ---------------------------------------------------------------------------


def test_send_notification_success(monkeypatch):
    smtp_client = MagicMock()
    smtp_client.sendmail.return_value = {}

    assessment = _make_assessment()
    bundle = _make_bundle()

    result = send_notification(
        assessments=[assessment],
        bundles_by_page={"B1012": bundle},
        page_meta_by_id={"B1012": PageMeta("B1012", "Trade Marks", "https://x/b1012")},
        rejected_candidates=[],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
        smtp_client=smtp_client,
    )

    assert result.sent is True
    assert result.change_required_count == 1
    assert result.uncertain_count == 0
    smtp_client.sendmail.assert_called_once()


def test_send_notification_counts_uncertain(monkeypatch):
    smtp_client = MagicMock()

    uncertain = _make_assessment("B1013", verdict="UNCERTAIN", suggested_changes=[])

    result = send_notification(
        assessments=[uncertain],
        bundles_by_page={"B1013": _make_bundle("B1013")},
        page_meta_by_id={"B1013": PageMeta("B1013", "Patents", "https://x/b1013")},
        rejected_candidates=[],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
        smtp_client=smtp_client,
    )
    assert result.sent is True
    assert result.uncertain_count == 1
    assert result.change_required_count == 0


# ---------------------------------------------------------------------------
# send_notification — SMTP failure with fallback
# ---------------------------------------------------------------------------


def test_send_notification_smtp_failure_writes_fallback(tmp_path, monkeypatch):
    """When SMTP fails, the email should be saved to a fallback file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "logs").mkdir(parents=True)

    smtp_client = MagicMock()
    smtp_client.sendmail.side_effect = ConnectionRefusedError("connection refused")

    assessment = _make_assessment()

    result = send_notification(
        assessments=[assessment],
        bundles_by_page={"B1012": _make_bundle()},
        page_meta_by_id={"B1012": PageMeta("B1012", "Trade Marks", "https://x")},
        rejected_candidates=[],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
        smtp_client=smtp_client,
    )

    assert result.sent is False
    assert result.fallback_file is not None
    assert (tmp_path / result.fallback_file).exists()


# ---------------------------------------------------------------------------
# Rejected candidates rendering
# ---------------------------------------------------------------------------


def test_rejected_candidates_in_notification(monkeypatch):
    import email as _email
    import base64

    smtp_client = MagicMock()
    rc = RejectedCandidate(
        source_id="SRC001",
        source_url="https://example.com",
        ipfr_page_id="B9999",
        rejection_stage="crossencoder",
        crossencoder_score=0.40,
        reranked_score=0.38,
    )
    assessment = _make_assessment()

    result = send_notification(
        assessments=[assessment],
        bundles_by_page={"B1012": _make_bundle()},
        page_meta_by_id={"B1012": PageMeta("B1012", "Trade Marks", "https://x")},
        rejected_candidates=[rc],
        run_id="run-001",
        run_date="6 April 2026",
        config=_simple_config(),
        smtp_client=smtp_client,
    )
    assert result.sent is True
    # Parse the MIME message and decode the parts to find B9999.
    call_args = smtp_client.sendmail.call_args
    raw_mime = call_args[0][2] if call_args[0] else call_args[1].get("msg", "")
    msg = _email.message_from_string(raw_mime)
    full_text = ""
    for part in msg.walk():
        payload = part.get_payload(decode=True)
        if payload:
            full_text += payload.decode("utf-8", errors="replace")
    assert "B9999" in full_text


# ---------------------------------------------------------------------------
# Page metadata — missing entry falls back to page_id as title
# ---------------------------------------------------------------------------


def test_compose_missing_meta_falls_back_to_page_id():
    assessment = _make_assessment("B9999")
    subject, text, _ = _compose_email(
        change_required=[assessment],
        uncertain=[],
        rejected_candidates=[],
        bundles_by_page={"B9999": _make_bundle("B9999")},
        page_meta_by_id={},   # no meta — should fall back gracefully
        run_id="run-001",
        run_date="6 April 2026",
        feedback_email="fb@example.com",
    )
    assert "B9999" in text
