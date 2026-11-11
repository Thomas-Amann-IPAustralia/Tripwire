"""
src/weekly_digest.py

Weekly digest notification — one email per week instead of one per run.

When ``notifications.cadence`` is ``"weekly"`` the daily pipeline does not
email anything. Instead, at the end of each live run it queues that run's
Stage 9 material (flagged LLM assessments with their trigger bundles,
rejected candidates, health alerts and run statistics) in the
``notification_queue`` table via :func:`enqueue_run`.

Once a week (default Monday 09:30 Australia/Sydney) ``weekly_digest.yml``
runs ``python -m src.weekly_digest``, which gathers every queued run not yet
covered by a digest and sends ONE email:

  • If any page was CHANGE_REQUIRED or UNCERTAIN, the email has the same
    sections as the per-run Stage 9 email (amendment required, human review,
    rejected candidates), each item labelled with the run it came from and
    with feedback links carrying that run's ID.
  • Otherwise it is a short "No noteworthy changes identified" email.

Both versions include a summary of the week's monitoring activity and the
source health warnings from the most recent run.

Fail-closed: queued runs are only marked as sent once SMTP accepts the email.
Runs older than the 7-day window are never dropped; they are included in the
next digest that goes out.

Scheduling: GitHub cron is UTC-only and Sydney moves between UTC+11 (AEDT)
and UTC+10 (AEST), so the workflow fires at both 22:30 and 23:30 UTC on
Sunday. This module sends only when the local time has reached the
configured send time on the configured day, and only once per local week
(tracked in ``digest_log``), so exactly one of the two firings sends.

Usage:
    python -m src.weekly_digest                 # send if due
    python -m src.weekly_digest --force         # send now regardless of schedule
    python -m src.weekly_digest --dry-run       # compose only, print, send nothing
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sqlite3
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src import email_html as eh
from src import email_security as sec
from src.stage7_aggregation import TriggerBundle, TriggerSource
from src.stage8_llm import LLMAssessment
from src.stage9_notification import (
    PageMeta,
    RejectedCandidate,
    _build_mime,
    _html_change_required_section,
    _html_footer,
    _html_rejected_section,
    _html_uncertain_section,
    _send_via_smtp,
    _txt_change_required_section,
    _txt_rejected_section,
    _txt_uncertain_section,
)

logger = logging.getLogger(__name__)

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS notification_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    digest_id   INTEGER
);
CREATE TABLE IF NOT EXISTS digest_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    week_key      TEXT NOT NULL,
    sent_at       TEXT NOT NULL,
    recipients    TEXT NOT NULL,
    subject       TEXT NOT NULL,
    run_count     INTEGER NOT NULL,
    flagged_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_queue_digest ON notification_queue(digest_id);
"""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def is_weekly(config: dict[str, Any]) -> bool:
    """True when notifications are batched into a weekly digest."""
    return config.get("notifications", {}).get("cadence", "per_run") == "weekly"


def _digest_cfg(config: dict[str, Any]) -> dict[str, Any]:
    d = config.get("notifications", {}).get("weekly_digest", {}) or {}
    return {
        "day": str(d.get("day", "monday")).lower(),
        "time": str(d.get("time", "09:30")),
        "timezone": str(d.get("timezone", "Australia/Sydney")),
        "lookback_days": int(d.get("lookback_days", 7)),
    }


def _recipients(config: dict[str, Any]) -> list[str]:
    # Addresses come from the CONTENT_OWNER_EMAIL / HEALTH_ALERT_EMAIL secrets
    # (YAML fallback for local runs); see src/email_security.py.
    return sec.notification_recipients(config)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_QUEUE_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Queueing (called by the daily pipeline)
# ---------------------------------------------------------------------------


def run_stats(run_log_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Summarise one run's per-source log rows for the digest."""
    detected_stages = {"stage3", "stage4", "stage5", "stage6", "stage6_complete"}
    checked = changed = errors = 0
    for row in run_log_rows:
        stages = (row.get("details") or {}).get("stages", {})
        if stages.get("metadata_probe", {}).get("decision") != "not_due":
            checked += 1
        if row.get("stage_reached") in detected_stages:
            changed += 1
        if row.get("outcome") == "error":
            errors += 1
    return {
        "sources_total": len(run_log_rows),
        "sources_checked": checked,
        "changes_detected": changed,
        "errors": errors,
    }


def enqueue_run(
    conn: sqlite3.Connection,
    run_id: str,
    run_date: str,
    assessments: list[LLMAssessment],
    bundles_by_page: dict[str, TriggerBundle],
    page_meta_by_id: dict[str, PageMeta],
    rejected_candidates: list[RejectedCandidate],
    health_alerts: list[Any] | None = None,
    stats: dict[str, int] | None = None,
) -> None:
    """Store one run's Stage 9 material for the next weekly digest."""
    ensure_tables(conn)
    flagged = []
    for a in assessments:
        if a.verdict not in ("CHANGE_REQUIRED", "UNCERTAIN"):
            continue
        meta = page_meta_by_id.get(a.ipfr_page_id)
        bundle = bundles_by_page.get(a.ipfr_page_id)
        flagged.append({
            "assessment": dataclasses.asdict(a),
            "bundle": dataclasses.asdict(bundle) if bundle else None,
            "meta": dataclasses.asdict(meta) if meta else None,
        })
    payload = {
        "run_id": run_id,
        "run_date": run_date,
        "flagged": flagged,
        "assessed_count": len(assessments),
        "no_change_count": sum(1 for a in assessments if a.verdict == "NO_CHANGE"),
        "rejected": [dataclasses.asdict(r) for r in rejected_candidates],
        "health_alerts": [
            {"condition": h.condition, "severity": h.severity, "summary": h.summary}
            for h in (health_alerts or [])
        ],
        "stats": stats or {},
    }
    conn.execute(
        "INSERT INTO notification_queue (run_id, created_at, payload) VALUES (?, ?, ?)",
        (run_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
    )
    conn.commit()
    logger.info(
        "Weekly digest: queued run %s (%d flagged page(s), %d health alert(s)).",
        run_id, len(flagged), len(payload["health_alerts"]),
    )


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def _week_key(local_now: datetime, send_day: int) -> str:
    """Identify the digest week: the local date of the most recent send day."""
    delta = (local_now.weekday() - send_day) % 7
    return (local_now.date() - timedelta(days=delta)).isoformat()


def in_send_window(config: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """Day/time part of the schedule only (no database needed)."""
    cfg = _digest_cfg(config)
    local = now.astimezone(ZoneInfo(cfg["timezone"]))
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    if local.weekday() != _WEEKDAYS.index(cfg["day"]):
        return False, (f"today is {local:%A} in {cfg['timezone']}, "
                       f"digest goes out on {cfg['day'].title()}")
    if local.time() < dtime(hh, mm):
        return False, f"local time {local:%H:%M} is before the {cfg['time']} send time"
    return True, f"local time {local:%a %d %b %H:%M %Z}"


def is_due(conn: sqlite3.Connection, config: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """Return (due, reason). Due once per week, at/after the configured local time."""
    ok, reason = in_send_window(config, now)
    if not ok:
        return False, reason
    cfg = _digest_cfg(config)
    local = now.astimezone(ZoneInfo(cfg["timezone"]))
    send_day = _WEEKDAYS.index(cfg["day"])
    week = _week_key(local, send_day)
    row = conn.execute("SELECT sent_at FROM digest_log WHERE week_key = ?", (week,)).fetchone()
    if row:
        return False, f"digest for week {week} already sent at {row[0]}"
    return True, f"due ({reason})"


# ---------------------------------------------------------------------------
# Deserialisation
# ---------------------------------------------------------------------------


def _assessment_from(d: dict[str, Any]) -> LLMAssessment:
    fields = {f.name for f in dataclasses.fields(LLMAssessment)}
    return LLMAssessment(**{k: v for k, v in d.items() if k in fields})


def _bundle_from(d: dict[str, Any] | None) -> TriggerBundle | None:
    if not d:
        return None
    fields = {f.name for f in dataclasses.fields(TriggerSource)}
    return TriggerBundle(
        ipfr_page_id=d["ipfr_page_id"],
        triggers=[TriggerSource(**{k: v for k, v in t.items() if k in fields})
                  for t in d.get("triggers", [])],
    )


def _meta_from(d: dict[str, Any] | None, page_id: str) -> PageMeta:
    return PageMeta(**d) if d else PageMeta(page_id, page_id, "")


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _period_label(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start.day} {start:%B} – {end.day} {end:%B %Y}" if start.month != end.month \
            else f"{start.day}–{end.day} {end:%B %Y}"
    return f"{start.day} {start:%B %Y} – {end.day} {end:%B %Y}"


def compose_digest(
    payloads: list[dict[str, Any]],
    period_start: date,
    period_end: date,
    feedback_email: str,
    activity: dict[str, int] | None = None,
) -> tuple[str, str, str, int]:
    """Return (subject, text, html, flagged_count) for a list of queued runs."""
    period = _period_label(period_start, period_end)

    change_required: list[tuple[str, LLMAssessment, PageMeta, TriggerBundle | None]] = []
    uncertain: list[tuple[str, LLMAssessment, PageMeta, TriggerBundle | None]] = []
    rejected: list[tuple[str, RejectedCandidate]] = []
    for p in payloads:
        for item in p.get("flagged", []):
            a = _assessment_from(item["assessment"])
            entry = (p["run_id"], a, _meta_from(item.get("meta"), a.ipfr_page_id),
                     _bundle_from(item.get("bundle")))
            (change_required if a.verdict == "CHANGE_REQUIRED" else uncertain).append(entry)
        for r in p.get("rejected", []):
            rejected.append((p["run_id"], RejectedCandidate(**r)))

    flagged = len(change_required) + len(uncertain)
    if flagged:
        subject = (f"Tripwire weekly digest — {period} — "
                   f"{flagged} IPFR page{'s' if flagged != 1 else ''} flagged")
    else:
        subject = f"Tripwire weekly digest — {period} — no noteworthy changes identified"

    # Week activity (sum over runs) and latest health snapshot.
    totals = {"runs": len(payloads), "sources_checked": 0, "changes_detected": 0,
              "assessed": 0, "no_change": 0, "errors": 0}
    for p in payloads:
        s = p.get("stats", {})
        totals["sources_checked"] += int(s.get("sources_checked", 0))
        totals["changes_detected"] += int(s.get("changes_detected", 0))
        totals["errors"] += int(s.get("errors", 0))
        totals["assessed"] += int(p.get("assessed_count", 0))
        totals["no_change"] += int(p.get("no_change_count", 0))
    if activity:
        totals.update(activity)
    latest = payloads[-1] if payloads else {}
    health = latest.get("health_alerts", [])

    activity_lines = [
        f"Pipeline runs: {totals['runs']}",
        f"Source checks performed: {totals['sources_checked']}",
        f"Upstream changes detected: {totals['changes_detected']}",
        f"IPFR pages assessed by the LLM: {totals['assessed']} "
        f"({totals['no_change']} judged no change needed)",
        f"Candidates rejected at deep analysis: {len(rejected)}",
        f"Source fetch errors: {totals['errors']}",
    ]

    t: list[str] = [subject, f"Period: {period}", ""]
    h: list[str] = []

    if not flagged:
        msg = ("No noteworthy changes identified. Tripwire found no upstream changes "
               "in the past week that require an amendment to, or human review of, "
               "any IP First Response page.")
        t += [msg, ""]
        h.append(eh.callout(
            "<strong>No noteworthy changes identified.</strong> Tripwire found no "
            "upstream changes in the past week that require an amendment to, or human "
            "review of, any IP First Response page.", "green"))
    else:
        t.append(f"{flagged} IPFR page{'s' if flagged != 1 else ''} require attention.")
        t.append("")
        parts_txt = []
        if change_required:
            parts_txt.append(f"<strong>{len(change_required)}</strong> need"
                             f"{'s' if len(change_required) == 1 else ''} an amendment")
        if uncertain:
            parts_txt.append(f"<strong>{len(uncertain)}</strong> need"
                             f"{'s' if len(uncertain) == 1 else ''} human review")
        h.append(eh.callout(
            f"<strong>{flagged} IPFR page{'s' if flagged != 1 else ''} require "
            f"attention</strong> this week: {' and '.join(parts_txt)}.",
            "red" if change_required else "amber"))

    t += ["THIS WEEK'S MONITORING", *[f"  • {line}" for line in activity_lines], ""]
    h.append(eh.section_heading("This week's monitoring"))
    h.append(eh.stat_tiles([
        (str(totals["runs"]), "Pipeline runs", None),
        (str(totals["sources_checked"]), "Source checks", None),
        (str(totals["changes_detected"]), "Upstream changes detected", None),
        (str(totals["assessed"]), f"Pages assessed by the LLM ({totals['no_change']} "
                                  f"no change needed)", None),
        (str(len(rejected)), "Rejected at deep analysis", None),
        (str(totals["errors"]), "Source fetch errors", "amber" if totals["errors"] else None),
    ]))

    if change_required:
        t += ["=" * 70, "AMENDMENT REQUIRED", "=" * 70]
        h.append(eh.section_heading("Amendment required", "red", len(change_required)))
        for run_id, a, meta, bundle in change_required:
            t.append(f"[Run {run_id}]")
            _txt_change_required_section(t, a, meta, bundle, run_id, feedback_email)
            _html_change_required_section(h, a, meta, bundle, run_id, feedback_email,
                                          run_label=run_id)

    if uncertain:
        t += ["", "=" * 70, "ITEMS REQUIRING HUMAN REVIEW", "=" * 70]
        h.append(eh.section_heading("Items requiring human review", "amber", len(uncertain)))
        for run_id, a, meta, bundle in uncertain:
            t.append(f"[Run {run_id}]")
            _txt_uncertain_section(t, a, meta, bundle, run_id, feedback_email)
            _html_uncertain_section(h, a, meta, bundle, run_id, feedback_email,
                                    run_label=run_id)

    if rejected:
        t += ["", "=" * 70, "CANDIDATES REJECTED AT DEEP ANALYSIS",
              "(Surfaced for calibration — potential false positives in upstream stages)",
              "=" * 70]
        h.append(eh.section_heading("Candidates rejected at deep analysis", "grey",
                                    len(rejected)))
        h.append(eh.paragraph("Surfaced for calibration — potential false positives in "
                              "upstream stages.", size=13, color=eh.MUTED))
        items: list[str] = []
        for run_id, rc in rejected:
            _txt_rejected_section(t, rc)
            _html_rejected_section(items, rc)
        h.append(eh.bullet_list(items))

    if health:
        t += ["", "=" * 70, f"SOURCE HEALTH ({len(health)} warning(s) at the latest run, "
              f"{latest.get('run_id', '')})", "=" * 70]
        h.append(eh.section_heading("Source health", "amber", len(health)))
        h.append(eh.paragraph(f"Warnings at the latest run "
                              f"({eh.esc(latest.get('run_id', ''))}):",
                              size=13, color=eh.MUTED, bottom=10))
        for a in health:
            t.append(f"  • {a['summary']}")
        h.append(eh.bullet_list((eh.esc(a["summary"]) for a in health), "amber"))

    runs = ", ".join(p["run_id"] for p in payloads) or "none"
    t += ["", f"Runs covered: {runs} | Generated by Tripwire"]

    if flagged:
        heading = f"{flagged} IPFR page{'s' if flagged != 1 else ''} flagged"
        preheader = f"{flagged} IPFR page{'s' if flagged != 1 else ''} require attention."
    else:
        heading = "No noteworthy changes"
        preheader = "No IPFR pages need attention this week."
    html_doc = eh.document(
        title=subject,
        preheader=preheader,
        eyebrow="Tripwire · Weekly digest",
        heading=heading,
        subheading=period,
        body="\n".join(h),
        footer=_html_footer(f"Runs covered: {runs}"),
    )

    return subject, "\n".join(t), html_doc, flagged


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def send_weekly_digest(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    now: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    smtp_client: Any | None = None,
) -> dict[str, Any]:
    """Send the weekly digest if due. Returns a result dict (``sent``, ``reason``, ...)."""
    ensure_tables(conn)
    now = now or datetime.now(timezone.utc)
    cfg = _digest_cfg(config)
    tz = ZoneInfo(cfg["timezone"])
    local = now.astimezone(tz)
    send_day = _WEEKDAYS.index(cfg["day"])

    if config.get("pipeline", {}).get("observation_mode", False):
        return {"sent": False, "reason": "observation mode — no emails are sent"}

    if not force:
        due, reason = is_due(conn, config, now)
        if not due:
            logger.info("Weekly digest not sent: %s.", reason)
            return {"sent": False, "reason": reason}

    rows = conn.execute(
        "SELECT id, payload, created_at FROM notification_queue "
        "WHERE digest_id IS NULL ORDER BY created_at ASC"
    ).fetchall()
    payloads = [json.loads(r[1]) for r in rows]

    period_end = local.date() - timedelta(days=1)
    period_start = local.date() - timedelta(days=cfg["lookback_days"])
    if rows:
        earliest = datetime.fromisoformat(rows[0][2]).astimezone(tz).date()
        period_start = min(period_start, earliest)

    notif = config.get("notifications", {})
    feedback_email = os.environ.get(
        "FEEDBACK_EMAIL", notif.get("feedback_email", "tripwire-feedback@gmail.com")
    )
    subject, body_text, body_html, flagged = compose_digest(
        payloads, period_start, period_end, feedback_email
    )
    recipients = _recipients(config)
    if not recipients and not dry_run:
        logger.error("Weekly digest not sent: no recipients. Set the CONTENT_OWNER_EMAIL "
                     "and HEALTH_ALERT_EMAIL secrets. Queue left unsent.")
        return {"sent": False, "reason": "no recipients configured",
                "subject": subject, "recipients": []}
    result: dict[str, Any] = {"sent": False, "subject": subject, "recipients": recipients,
                              "run_count": len(payloads), "flagged_count": flagged,
                              "body_text": body_text,
                              "body_html": sec.strip_marker(body_html)}
    if dry_run:
        result["reason"] = "dry run"
        return result

    msg: MIMEMultipart = _build_mime(subject, body_text, body_html, feedback_email,
                                     ", ".join(recipients), feedback_email)
    smtp_cfg = notif.get("smtp", {})
    try:
        if smtp_client is not None:
            smtp_client.sendmail(feedback_email, recipients, msg.as_string())
        else:
            for r in recipients:
                _send_via_smtp(
                    msg, host=smtp_cfg.get("host", "smtp.gmail.com"),
                    port=int(smtp_cfg.get("port", 587)),
                    user=os.environ.get("SMTP_USER", feedback_email),
                    password=os.environ.get("SMTP_PASSWORD", ""),
                    sender=feedback_email, recipient=r,
                )
    except Exception as exc:  # noqa: BLE001 — fail closed: leave queue untouched
        fallback = Path("data/logs") / f"weekly_digest_{local:%Y-%m-%d}_FAILED.txt"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(f"Subject: {subject}\n\n{sec.redact_tokens(body_text)}",
                            encoding="utf-8")
        logger.error("Weekly digest SMTP failed (%s); saved to %s. Queue left unsent.",
                     exc, fallback)
        result.update(reason=f"smtp error: {exc}", fallback_file=str(fallback))
        return result

    cur = conn.execute(
        "INSERT INTO digest_log (week_key, sent_at, recipients, subject, run_count, "
        "flagged_count) VALUES (?, ?, ?, ?, ?, ?)",
        (_week_key(local, send_day), now.isoformat(),
         # Domains only: ipfr.sqlite is published in the public data-* releases.
         ", ".join(sec.redacted_address(r) for r in recipients), subject,
         len(payloads), flagged),
    )
    digest_id = cur.lastrowid
    conn.executemany("UPDATE notification_queue SET digest_id = ? WHERE id = ?",
                     [(digest_id, r[0]) for r in rows])
    conn.commit()
    logger.info("Weekly digest sent to %d recipient(s): %s (%d run(s)).",
                len(recipients), subject, len(payloads))
    result.update(sent=True, reason="sent")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the Tripwire weekly digest email.")
    parser.add_argument("--config", default="tripwire_config.yaml")
    parser.add_argument("--force", action="store_true",
                        help="Send now, ignoring the day/time schedule and once-per-week check.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compose and print the email without sending or marking anything.")
    parser.add_argument("--schedule-gate", action="store_true",
                        help="Only report whether now is inside the send window "
                             "(day/time); used by the workflow to skip the database "
                             "pull on the firing that is not due.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from src.config import load_config, get as cfg_get
    config = load_config(args.config)

    if args.schedule_gate:
        if not is_weekly(config):
            due, reason = False, "notifications.cadence is not 'weekly'"
        elif args.force or args.dry_run:
            due, reason = True, "manual run"
        else:
            due, reason = in_send_window(config, datetime.now(timezone.utc))
        print(f"Schedule gate: {'due' if due else 'not due'} — {reason}")
        _github_output("due", "true" if due else "false")
        return 0
    db_path = Path(cfg_get(config, "paths", "sqlite_db", default="data/ipfr_corpus/ipfr.sqlite"))
    if not is_weekly(config) and not (args.force or args.dry_run):
        print("Weekly digest: notifications.cadence is not 'weekly' — nothing to do.")
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        result = send_weekly_digest(conn, config, force=args.force, dry_run=args.dry_run)
    finally:
        conn.close()

    if args.dry_run:
        # Dry-run output lands in public workflow logs: show recipient domains
        # only and blank out feedback tokens.
        to = ", ".join(sec.redacted_address(r) for r in result.get("recipients", []))
        print(f"Subject: {result.get('subject')}\nTo: {to}\n")
        print(sec.redact_tokens(result.get("body_text", result.get("reason", ""))))
        return 0
    print(f"Weekly digest: {result['reason']}")
    _github_output("sent", "true" if result.get("sent") else "false")
    failed = result.get("fallback_file") or result.get("reason") == "no recipients configured"
    return 1 if failed else 0


def _github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


if __name__ == "__main__":
    sys.exit(main())
