"""One-off live check of Tripwire's email send (SMTP) and receive (IMAP) paths.

Run by .github/workflows/email_smoke_test.yml with the real repository secrets.

1. SEND:    logs in to smtp.gmail.com:587 with SMTP_USER / SMTP_PASSWORD (the
            same STARTTLS + login sequence as Stage 9 and health alerts) and
            sends one clearly labelled test message to the configured
            notification recipients plus the feedback inbox.
2. RECEIVE: logs in to imap.gmail.com:993 with FEEDBACK_GMAIL_USER /
            FEEDBACK_GMAIL_APP_PASSWORD (as feedback_ingestion does) and waits
            for that message to arrive, then marks it read and deletes it.

The subject deliberately omits "[TRIPWIRE]" so the scheduled feedback
ingestion never treats the test message as a feedback reply.
"""

from __future__ import annotations

import imaplib
import os
import smtplib
import sys
import time
import uuid
from email.mime.text import MIMEText

import yaml


def main() -> int:
    cfg = yaml.safe_load(open("tripwire_config.yaml", encoding="utf-8"))
    notif = cfg.get("notifications", {})
    smtp_cfg = notif.get("smtp", {})
    host = smtp_cfg.get("host", "smtp.gmail.com")
    port = int(smtp_cfg.get("port", 587))

    env = {k: os.environ.get(k, "") for k in (
        "SMTP_USER", "SMTP_PASSWORD", "FEEDBACK_EMAIL",
        "FEEDBACK_GMAIL_USER", "FEEDBACK_GMAIL_APP_PASSWORD",
    )}
    print("Secrets present:")
    for k, v in env.items():
        print(f"  {k:<28} {'set' if v else 'MISSING'}")

    recipients = sorted({
        r for r in (
            notif.get("content_owner_email"),
            notif.get("health_alert_email"),
            env["FEEDBACK_GMAIL_USER"],
        ) if r
    })
    token = uuid.uuid4().hex[:12]
    subject = f"Tripwire email smoke test {token}"

    ok = True

    # ---- SEND --------------------------------------------------------------
    if not (env["SMTP_USER"] and env["SMTP_PASSWORD"]):
        print("SEND: FAIL - SMTP_USER / SMTP_PASSWORD not set")
        return 1
    sender = env["FEEDBACK_EMAIL"] or env["SMTP_USER"]
    msg = MIMEText(
        "This is an automated test of Tripwire's email delivery.\n"
        "No action is needed. It confirms notifications and health alerts can "
        "reach this address.\n\n"
        f"Token: {token}\nRun: {os.environ.get('GITHUB_RUN_ID', 'local')}\n",
        "plain", "utf-8",
    )
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Reply-To"] = sender
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            refused = smtp.sendmail(sender, recipients, msg.as_string())
        accepted = [r for r in recipients if r not in refused]
        print(f"SEND: OK - {host}:{port} accepted {len(accepted)}/{len(recipients)} "
              f"recipient(s): {accepted}")
        if refused:
            print(f"SEND: refused recipients: {refused}")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"SEND: FAIL - {type(exc).__name__}: {exc}")
        return 1

    # ---- RECEIVE -----------------------------------------------------------
    if not (env["FEEDBACK_GMAIL_USER"] and env["FEEDBACK_GMAIL_APP_PASSWORD"]):
        print("RECEIVE: FAIL - FEEDBACK_GMAIL_USER / FEEDBACK_GMAIL_APP_PASSWORD not set")
        return 1
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(env["FEEDBACK_GMAIL_USER"], env["FEEDBACK_GMAIL_APP_PASSWORD"])
        print("RECEIVE: IMAP login OK")
        found: list[bytes] = []
        for attempt in range(12):  # up to ~2 minutes
            mail.select("inbox")
            _, data = mail.search(None, f'SUBJECT "{token}"')
            found = data[0].split() if data and data[0] else []
            if found:
                break
            time.sleep(10)
        if found:
            print(f"RECEIVE: OK - test message arrived in feedback inbox "
                  f"after ~{attempt * 10}s")
            for mid in found:
                mail.store(mid, "+FLAGS", "(\\Seen \\Deleted)")
            mail.expunge()
            print("RECEIVE: test message cleaned up")
        else:
            print("RECEIVE: FAIL - test message not found in inbox after 2 minutes")
            ok = False
        mail.logout()
    except Exception as exc:  # noqa: BLE001
        print(f"RECEIVE: FAIL - {type(exc).__name__}: {exc}")
        return 1

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
