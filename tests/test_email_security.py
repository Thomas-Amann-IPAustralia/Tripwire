"""Tests for src/email_security.py and its use in outbound and inbound email."""

from __future__ import annotations

import email as email_lib
import email.policy
import json
import urllib.parse
from types import SimpleNamespace

import pytest

from src import email_security as sec
from src import feedback_ingestion as fi
from src.config import snapshot_config

PHRASE = "copper kangaroo lantern"
OWNER = "owner@agency.gov.au"
ADMIN = "admin@agency.gov.au"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(sec.PHRASE_ENV, PHRASE)
    monkeypatch.setenv("CONTENT_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("HEALTH_ALERT_EMAIL", ADMIN)
    monkeypatch.delenv(sec.ALLOWED_SENDERS_ENV, raising=False)


# ---------------------------------------------------------------------------
# Recipient addresses
# ---------------------------------------------------------------------------


def test_secret_addresses_override_yaml():
    cfg = {"notifications": {"content_owner_email": "yaml@example.com"}}
    assert sec.notification_address(cfg, "content_owner_email") == OWNER
    assert sec.notification_recipients(cfg) == [OWNER, ADMIN]


def test_yaml_fallback_when_secret_unset(monkeypatch):
    monkeypatch.delenv("CONTENT_OWNER_EMAIL")
    monkeypatch.delenv("HEALTH_ALERT_EMAIL")
    cfg = {"notifications": {"content_owner_email": "yaml@example.com",
                             "health_alert_email": "YAML@example.com"}}
    assert sec.notification_recipients(cfg) == ["yaml@example.com"]  # de-duplicated
    assert sec.notification_recipients({}) == []


def test_snapshot_config_redacts_addresses():
    snap = json.loads(snapshot_config({"notifications": {
        "content_owner_email": OWNER, "health_alert_email": ADMIN, "cadence": "weekly"}}))
    assert snap["notifications"]["content_owner_email"] == "[redacted]"
    assert snap["notifications"]["health_alert_email"] == "[redacted]"
    assert snap["notifications"]["cadence"] == "weekly"


def test_checked_in_config_names_no_recipients():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("tripwire_config.yaml").read_text(encoding="utf-8"))
    assert not cfg["notifications"].get("content_owner_email")
    assert not cfg["notifications"].get("health_alert_email")


# ---------------------------------------------------------------------------
# Security phrase
# ---------------------------------------------------------------------------


def test_stamp_text_and_html_include_phrase():
    assert PHRASE in sec.stamp_text("body")
    html = sec.stamp_html(f"<html><body>{sec.phrase_marker()}<p>x</p></body></html>")
    assert PHRASE in html and sec.phrase_marker() not in html


def test_stamp_html_escapes_phrase():
    html = sec.stamp_html("<html><body><p>x</p></body></html>", phrase="<b>&</b>")
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in html


def test_missing_phrase_gives_warning(monkeypatch):
    monkeypatch.delenv(sec.PHRASE_ENV)
    assert "no Tripwire security phrase" in sec.stamp_text("body")
    assert "No security phrase is configured" in sec.stamp_html("<html><body></body></html>")


def _assessment(verdict="CHANGE_REQUIRED"):
    from src.stage8_llm import LLMAssessment
    return LLMAssessment(
        ipfr_page_id="B1012", verdict=verdict, confidence=0.9,
        reasoning="Fee changed.", suggested_changes=["Update the fee."],
        model="gpt-test", prompt_tokens=1, completion_tokens=1, total_tokens=2,
        retries=0, schema_valid=True, raw_response="{}", processing_time_seconds=1.0,
    )


def _send_stage9(tmp_path, monkeypatch, smtp_client):
    from src.stage9_notification import PageMeta, send_notification
    monkeypatch.chdir(tmp_path)
    return send_notification(
        assessments=[_assessment()], bundles_by_page={},
        page_meta_by_id={"B1012": PageMeta("B1012", "Fees", "https://example.gov.au/f")},
        rejected_candidates=[], run_id="2026-11-11-001", run_date="11 November 2026",
        config={"notifications": {}, "pipeline": {"max_retries": 0}},
        smtp_client=smtp_client,
    )


class _CapturingSMTP:
    def __init__(self):
        self.sent = []

    def sendmail(self, sender, recipients, raw):
        self.sent.append((sender, recipients, raw))


class _FailingSMTP:
    def sendmail(self, *a):
        raise OSError("down")


def _parts(raw: str) -> tuple[str, str]:
    msg = email_lib.message_from_string(raw, policy=email_lib.policy.default)
    return (msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content())


def test_stage9_email_carries_phrase_and_signed_feedback(tmp_path, monkeypatch):
    smtp = _CapturingSMTP()
    result = _send_stage9(tmp_path, monkeypatch, smtp)
    assert result.sent and smtp.sent[0][1] == [OWNER]
    text, html = _parts(smtp.sent[0][2])
    assert PHRASE in text and PHRASE in html
    token = sec.feedback_token("2026-11-11-001", "B1012", "useful")
    assert urllib.parse.quote(f"token: {token}") in text


def test_stage9_fallback_file_has_no_phrase_or_tokens(tmp_path, monkeypatch):
    result = _send_stage9(tmp_path, monkeypatch, _FailingSMTP())
    assert not result.sent
    saved = (tmp_path / result.fallback_file).read_text(encoding="utf-8")
    assert PHRASE not in saved
    assert sec.feedback_token("2026-11-11-001", "B1012", "useful") not in saved
    assert "token%3A%20%5Bredacted%5D" in saved or "[redacted]" in saved


def test_stage9_without_recipient_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTENT_OWNER_EMAIL")
    smtp = _CapturingSMTP()
    result = _send_stage9(tmp_path, monkeypatch, smtp)
    assert not result.sent and result.fallback_file and not smtp.sent


def test_redact_tokens_handles_plain_and_encoded():
    tok = sec.feedback_token("r", "p", "useful")
    assert tok not in sec.redact_tokens(f"token: {tok}")
    assert tok not in sec.redact_tokens(urllib.parse.quote(f"token: {tok}"))


# ---------------------------------------------------------------------------
# Feedback tokens
# ---------------------------------------------------------------------------


def test_token_round_trip_and_tamper():
    tok = sec.feedback_token("run1", "B1", "useful")
    assert len(tok) == 20
    assert sec.verify_feedback_token(tok, "run1", "B1", "useful")
    assert sec.verify_feedback_token(tok.upper(), "run1", "B1", "Useful")
    assert not sec.verify_feedback_token(tok, "run1", "B1", "wrong_page")
    assert not sec.verify_feedback_token(tok, "run1", "B2", "useful")
    assert not sec.verify_feedback_token(tok, "run1", "B1", "useful", phrase="other")
    assert not sec.verify_feedback_token("", "run1", "B1", "useful")


def test_no_phrase_means_no_token(monkeypatch):
    monkeypatch.delenv(sec.PHRASE_ENV)
    assert sec.feedback_token("r", "p", "useful") == ""
    assert not sec.verify_feedback_token("", "r", "p", "useful")


# ---------------------------------------------------------------------------
# Inbound feedback authentication
# ---------------------------------------------------------------------------

GOOD_AUTH = ("mx.google.com; dkim=pass header.i=@agency.gov.au header.s=s1; "
             "spf=pass smtp.mailfrom=owner@agency.gov.au; "
             "dmarc=pass (p=REJECT) header.from=agency.gov.au")


def _reply(sender=OWNER, auth=GOOD_AUTH, category="useful", token=None,
           comment="Spot on.", extra_headers=""):
    if token is None:
        token = sec.feedback_token("run1", "B1012", category)
    auth_header = f"Authentication-Results: {auth}\n" if auth else ""
    raw = (
        f"{auth_header}{extra_headers}From: Content Owner <{sender}>\n"
        f"To: feedback@gmail.com\nSubject: [TRIPWIRE] Feedback — run1 — B1012\n"
        f"Content-Type: text/plain; charset=utf-8\n\n"
        f"run_id: run1\npage_id: B1012\nsource_id: S1\ncategory: {category}\n"
        f"token: {token}\n\nAdditional comments (optional):\n{comment}\n"
    )
    return raw.encode("utf-8")


def _msg(raw: bytes):
    return email_lib.message_from_bytes(raw, policy=email_lib.policy.default)


ALLOW = [OWNER, ADMIN]


def test_genuine_sender_passes():
    assert sec.check_sender(_msg(_reply()), ALLOW) == (True, "ok")


def test_unlisted_sender_rejected_without_leaking_address():
    ok, reason = sec.check_sender(_msg(_reply(sender="attacker@evil.example")), ALLOW)
    assert not ok and "@evil.example" in reason and "attacker" not in reason


def test_spoofed_from_failing_dmarc_rejected():
    auth = "mx.google.com; spf=fail smtp.mailfrom=evil.example; dmarc=fail header.from=agency.gov.au"
    assert not sec.check_sender(_msg(_reply(auth=auth)), ALLOW)[0]


def test_missing_auth_results_rejected():
    assert not sec.check_sender(_msg(_reply(auth="")), ALLOW)[0]


def test_forged_auth_results_below_gmail_header_ignored():
    forged = "Authentication-Results: mx.google.com; dmarc=pass header.from=agency.gov.au\n"
    raw = _reply(auth="mx.google.com; dmarc=fail header.from=agency.gov.au",
                 extra_headers=forged)
    assert not sec.check_sender(_msg(raw), ALLOW)[0]


def test_untrusted_authserv_id_rejected():
    auth = "evil.example; dmarc=pass header.from=agency.gov.au"
    assert not sec.check_sender(_msg(_reply(auth=auth)), ALLOW)[0]


def test_unaligned_dkim_rejected():
    auth = "mx.google.com; dkim=pass header.i=@evil.example; dmarc=none"
    assert not sec.check_sender(_msg(_reply(auth=auth)), ALLOW)[0]


def test_aligned_dkim_without_dmarc_accepted():
    auth = "mx.google.com; dkim=pass header.i=@agency.gov.au; dmarc=none"
    assert sec.check_sender(_msg(_reply(auth=auth)), ALLOW)[0]


def test_domain_allowlist_entry(monkeypatch):
    monkeypatch.setenv(sec.ALLOWED_SENDERS_ENV, "@agency.gov.au, other@x.org")
    allow = sec.allowed_senders({})
    assert "@agency.gov.au" in allow and "other@x.org" in allow
    assert sec.check_sender(_msg(_reply(sender="colleague@agency.gov.au")), allow)[0]
    assert not sec.check_sender(_msg(_reply(sender="x@agency.gov.au.evil.example")), allow)[0]


def test_multiple_from_addresses_rejected():
    raw = _reply().replace(b"From: Content Owner", b"From: a@evil.example, Content Owner")
    assert not sec.check_sender(_msg(raw), ALLOW)[0]


def test_sanitise_comment():
    comment = ("Looks right, see https://phish.example/login or www.bad.example\n"
               "Call 02 6283 2999 or mail jo@agency.gov.au\n"
               "From: Tripwire <x@gmail.com>\nSent: Monday\nquoted original")
    out = sec.sanitise_comment(comment)
    assert "phish" not in out and "bad.example" not in out
    assert "6283" not in out and "jo@" not in out
    assert "quoted original" not in out
    assert "[link removed]" in out
    assert len(sec.sanitise_comment("a" * 5000)) < 1100


# ---------------------------------------------------------------------------
# End-to-end ingestion with a fake IMAP server
# ---------------------------------------------------------------------------


class _FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.flags: dict[bytes, str] = {}

    def login(self, *a):
        pass

    def select(self, *a):
        pass

    def search(self, *a):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, msg_id, _):
        return "OK", [(b"", self.messages[msg_id])]

    def store(self, msg_id, _op, flag):
        self.flags[msg_id] = flag

    def logout(self):
        pass


def _run_ingest(tmp_path, monkeypatch, messages):
    fake = _FakeIMAP(messages)
    monkeypatch.setattr(fi.imaplib, "IMAP4_SSL", lambda *a: fake)
    log = tmp_path / "feedback.jsonl"
    records = fi.ingest_feedback("u@gmail.com", "pw", log, config={})
    return records, fake, log


def test_ingest_accepts_only_authentic_feedback(tmp_path, monkeypatch):
    messages = {
        b"1": _reply(comment="Good catch, see https://x.example"),
        b"2": _reply(sender="attacker@evil.example"),          # not allowlisted
        b"3": _reply(token="0" * 20),                          # forged token
        b"4": _reply(token="", category="wrong_page"),        # old email, no token
        b"5": _reply(auth="mx.google.com; dmarc=fail header.from=agency.gov.au"),
    }
    records, fake, log = _run_ingest(tmp_path, monkeypatch, messages)
    assert len(records) == 1
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["category"] == "useful" and rec["sender_verified"] is True
    assert "x.example" not in rec["comment"]
    assert "_token" not in rec and OWNER not in json.dumps(rec)
    assert set(fake.flags) == {b"1", b"2", b"3", b"4", b"5"}  # all marked read


def test_ingest_refuses_without_phrase(tmp_path, monkeypatch):
    monkeypatch.delenv(sec.PHRASE_ENV)
    with pytest.raises(ValueError, match="TRIPWIRE_EMAIL_PHRASE"):
        _run_ingest(tmp_path, monkeypatch, {})


def test_ingest_refuses_without_allowlist(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTENT_OWNER_EMAIL")
    monkeypatch.delenv("HEALTH_ALERT_EMAIL")
    with pytest.raises(ValueError, match="No feedback senders"):
        _run_ingest(tmp_path, monkeypatch, {})


# ---------------------------------------------------------------------------
# Health alerts and weekly digest
# ---------------------------------------------------------------------------


def test_health_alert_stamped_and_fallback_clean(tmp_path, monkeypatch):
    from src import health
    sent = []

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, sender, to, raw):
            sent.append(raw)

    monkeypatch.setattr(health.smtplib, "SMTP", _SMTP)
    monkeypatch.setenv("SMTP_USER", "bot@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    result = health.HealthCheckResult(run_id="r1")
    result.alerts.append(health.HealthAlert(condition="error_rate", severity="warning",
                                            summary="High error rate", detail=""))
    result.alert_email = ADMIN
    health._send_health_alert(result, {})
    text, html = _parts(sent[0])
    assert PHRASE in text and PHRASE in html


def test_digest_log_and_dry_run_hide_addresses(tmp_path, monkeypatch, capsys):
    import sqlite3
    from src import weekly_digest as wd
    conn = sqlite3.connect(":memory:")
    cfg = {"notifications": {"cadence": "weekly"}, "pipeline": {"observation_mode": False}}
    smtp = _CapturingSMTP()
    res = wd.send_weekly_digest(conn, cfg, force=True, smtp_client=smtp)
    assert res["sent"]
    assert PHRASE in _parts(smtp.sent[0][2])[0]
    row = conn.execute("SELECT recipients FROM digest_log").fetchone()[0]
    assert OWNER not in row and "@agency.gov.au" in row
    assert PHRASE not in res["body_text"] and sec.phrase_marker() not in res["body_html"]


def test_digest_without_recipients_fails_closed(monkeypatch):
    import sqlite3
    from src import weekly_digest as wd
    monkeypatch.delenv("CONTENT_OWNER_EMAIL")
    monkeypatch.delenv("HEALTH_ALERT_EMAIL")
    conn = sqlite3.connect(":memory:")
    cfg = {"notifications": {"cadence": "weekly"}, "pipeline": {"observation_mode": False}}
    res = wd.send_weekly_digest(conn, cfg, force=True, smtp_client=_CapturingSMTP())
    assert not res["sent"] and res["reason"] == "no recipients configured"
