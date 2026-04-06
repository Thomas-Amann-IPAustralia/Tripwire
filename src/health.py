"""
src/health.py

Health alerting for the Tripwire pipeline (Section 6.6, Phase 5 task 5.1).

Evaluates four alert conditions after every run:

  1. Error rate > 30% in a single run → health alert email.
  2. Same source fails N consecutive runs (default: 3) → health alert email.
  3. LLM produces malformed output ≥ 2 times in a run → health alert email.
  4. Cross-encoder truncation occurs ≥ 3 times in a run → health alert email.

Condition 3 (pipeline timeout) is enforced by the GitHub Actions
``timeout-minutes`` setting rather than in Python code.

Health alerts are sent to the ``notifications.health_alert_email`` address
configured in ``tripwire_config.yaml``, which is separate from the
content-owner notification email.

Usage (called by pipeline.py after every run):

    from src.health import evaluate_and_alert

    evaluate_and_alert(
        run_id=run_id,
        run_log_rows=run_log_rows,
        llm_failed_count=llm_result.failed_count,
        crossencoder_truncation_pairs=truncation_pairs,
        conn=conn,
        config=config,
    )
"""

from __future__ import annotations

import logging
import os
import smtplib
import sqlite3
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert condition thresholds (overridden by config)
# ---------------------------------------------------------------------------

_DEFAULT_ERROR_RATE_THRESHOLD = 0.30
_DEFAULT_CONSECUTIVE_FAILURES_THRESHOLD = 3
_DEFAULT_LLM_MALFORMED_THRESHOLD = 2
_DEFAULT_CROSSENCODER_TRUNCATION_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class HealthAlert:
    """Represents a single triggered alert condition."""

    condition: str   # e.g. "error_rate", "consecutive_failures", "llm_malformed", "truncation"
    severity: str    # "warning" | "critical"
    summary: str     # One-line human-readable description
    detail: str      # Extended markdown detail block


@dataclass
class HealthCheckResult:
    """Aggregated output of a health check evaluation."""

    run_id: str
    alerts: list[HealthAlert] = field(default_factory=list)
    alert_sent: bool = False
    alert_email: str | None = None

    @property
    def has_alerts(self) -> bool:
        return bool(self.alerts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_and_alert(
    run_id: str,
    run_log_rows: list[dict[str, Any]],
    llm_failed_count: int,
    crossencoder_truncation_pairs: list[tuple[str, str]],
    conn: sqlite3.Connection,
    config: dict[str, Any],
) -> HealthCheckResult:
    """Evaluate all health conditions and send an alert email if any fire.

    Parameters
    ----------
    run_id:
        Identifier for the current run.
    run_log_rows:
        Per-source log entries produced by the pipeline for this run.
    llm_failed_count:
        Number of LLM calls that failed schema validation after retries.
    crossencoder_truncation_pairs:
        List of (source_id, page_id) tuples where cross-encoder input was
        truncated due to token-budget constraints.
    conn:
        Open SQLite connection (used to query historical consecutive failures).
    config:
        Loaded tripwire_config.yaml dict.
    """
    result = HealthCheckResult(run_id=run_id)

    notif_cfg = config.get("notifications", {})
    alert_cfg = notif_cfg.get("health_alert_conditions", {})

    error_rate_threshold: float = float(
        alert_cfg.get("error_rate_threshold", _DEFAULT_ERROR_RATE_THRESHOLD)
    )
    consecutive_threshold: int = int(
        alert_cfg.get("consecutive_failures_threshold", _DEFAULT_CONSECUTIVE_FAILURES_THRESHOLD)
    )
    health_email: str | None = notif_cfg.get("health_alert_email")

    # ------------------------------------------------------------------
    # Condition 1: Error rate
    # ------------------------------------------------------------------
    alert = _check_error_rate(run_log_rows, error_rate_threshold, run_id)
    if alert:
        result.alerts.append(alert)

    # ------------------------------------------------------------------
    # Condition 2: Consecutive source failures
    # ------------------------------------------------------------------
    for alert in _check_consecutive_failures(run_log_rows, consecutive_threshold, conn):
        result.alerts.append(alert)

    # ------------------------------------------------------------------
    # Condition 3: LLM malformed output
    # ------------------------------------------------------------------
    alert = _check_llm_malformed(llm_failed_count, _DEFAULT_LLM_MALFORMED_THRESHOLD, run_id)
    if alert:
        result.alerts.append(alert)

    # ------------------------------------------------------------------
    # Condition 4: Cross-encoder truncation
    # ------------------------------------------------------------------
    alert = _check_crossencoder_truncation(
        crossencoder_truncation_pairs, _DEFAULT_CROSSENCODER_TRUNCATION_THRESHOLD, run_id
    )
    if alert:
        result.alerts.append(alert)

    # ------------------------------------------------------------------
    # Send email if any alert fired
    # ------------------------------------------------------------------
    if result.alerts:
        result.alert_email = health_email
        _send_health_alert(result, config)

    return result


# ---------------------------------------------------------------------------
# Individual condition checkers
# ---------------------------------------------------------------------------


def _check_error_rate(
    run_log_rows: list[dict[str, Any]],
    threshold: float,
    run_id: str,
) -> HealthAlert | None:
    """Return an alert if the proportion of errored sources exceeds threshold."""
    if not run_log_rows:
        return None

    total = len(run_log_rows)
    error_count = sum(1 for r in run_log_rows if r.get("outcome") == "error")
    rate = error_count / total

    if rate > threshold:
        return HealthAlert(
            condition="error_rate",
            severity="warning",
            summary=f"High error rate in run {run_id}: {error_count}/{total} sources failed ({rate:.0%})",
            detail=(
                f"**Run ID:** {run_id}\n\n"
                f"**Error count:** {error_count} of {total} sources ({rate:.1%})\n\n"
                f"**Threshold:** {threshold:.0%}\n\n"
                "**Failed sources:**\n"
                + "\n".join(
                    f"- `{r['source_id']}`: {r.get('error_message', 'unknown error')}"
                    for r in run_log_rows
                    if r.get("outcome") == "error"
                )
            ),
        )
    return None


def _check_consecutive_failures(
    run_log_rows: list[dict[str, Any]],
    threshold: int,
    conn: sqlite3.Connection,
) -> list[HealthAlert]:
    """Return alerts for any source that has failed >= threshold consecutive runs."""
    alerts: list[HealthAlert] = []

    # Gather source IDs that errored in this run.
    errored_now = {r["source_id"] for r in run_log_rows if r.get("outcome") == "error"}
    if not errored_now:
        return alerts

    try:
        for source_id in errored_now:
            streak = _consecutive_failure_streak(conn, source_id)
            if streak >= threshold:
                alerts.append(HealthAlert(
                    condition="consecutive_failures",
                    severity="warning",
                    summary=(
                        f"Source `{source_id}` has failed {streak} consecutive run(s) "
                        f"(threshold: {threshold})"
                    ),
                    detail=(
                        f"**Source:** `{source_id}`\n\n"
                        f"**Consecutive failures:** {streak}\n\n"
                        f"**Threshold:** {threshold}\n\n"
                        "Check the source URL, authentication, and network connectivity. "
                        "If the source has moved or been taken down, update `source_registry.csv`."
                    ),
                ))
    except sqlite3.Error as exc:
        logger.warning("Could not query consecutive failure streak: %s", exc)

    return alerts


def _check_llm_malformed(
    failed_count: int,
    threshold: int,
    run_id: str,
) -> HealthAlert | None:
    """Return an alert if the LLM returned malformed output >= threshold times."""
    if failed_count >= threshold:
        return HealthAlert(
            condition="llm_malformed",
            severity="warning",
            summary=(
                f"LLM returned malformed output {failed_count} time(s) in run {run_id} "
                f"(threshold: {threshold})"
            ),
            detail=(
                f"**Run ID:** {run_id}\n\n"
                f"**Malformed LLM responses:** {failed_count}\n\n"
                f"**Threshold:** {threshold}\n\n"
                "Review the LLM system prompt and check whether the model API is returning "
                "unexpected content. Affected bundles have been written to the "
                "`deferred_triggers` table for retry."
            ),
        )
    return None


def _check_crossencoder_truncation(
    truncation_pairs: list[tuple[str, str]],
    threshold: int,
    run_id: str,
) -> HealthAlert | None:
    """Return an alert if cross-encoder truncation occurred >= threshold times."""
    count = len(truncation_pairs)
    if count >= threshold:
        pair_lines = "\n".join(
            f"- source `{src}` → page `{page}`" for src, page in truncation_pairs
        )
        return HealthAlert(
            condition="crossencoder_truncation",
            severity="warning",
            summary=(
                f"Cross-encoder input truncated {count} time(s) in run {run_id} "
                f"(threshold: {threshold})"
            ),
            detail=(
                f"**Run ID:** {run_id}\n\n"
                f"**Truncation count:** {count}\n\n"
                f"**Threshold:** {threshold}\n\n"
                "**Affected (source, page) pairs:**\n"
                + pair_lines + "\n\n"
                "Consider chunking long documents before cross-encoder scoring, or "
                "increasing the `max_context_tokens` budget if the model supports it."
            ),
        )
    return None


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _consecutive_failure_streak(conn: sqlite3.Connection, source_id: str) -> int:
    """Return the number of consecutive failing runs for source_id (most recent first)."""
    try:
        rows = conn.execute(
            """
            SELECT outcome
            FROM pipeline_runs
            WHERE source_id = ?
            ORDER BY timestamp DESC
            LIMIT 20
            """,
            (source_id,),
        ).fetchall()
    except sqlite3.Error:
        return 0

    streak = 0
    for (outcome,) in rows:
        if outcome == "error":
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------


def _send_health_alert(result: HealthCheckResult, config: dict[str, Any]) -> None:
    """Compose and send a health alert email."""
    to_addr = result.alert_email
    if not to_addr:
        logger.warning(
            "Health alerts triggered but no health_alert_email configured — "
            "printing to log instead."
        )
        for alert in result.alerts:
            logger.warning("HEALTH ALERT [%s]: %s", alert.condition, alert.summary)
        return

    smtp_host: str = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_password: str = os.environ.get("SMTP_PASSWORD", "")
    from_addr: str = smtp_user or "tripwire@noreply"

    severity_label = (
        "CRITICAL"
        if any(a.severity == "critical" for a in result.alerts)
        else "WARNING"
    )
    subject = (
        f"[Tripwire] {severity_label} — {len(result.alerts)} health alert(s) "
        f"in run {result.run_id}"
    )

    plain_body = _build_plain_body(result)
    html_body = _build_html_body(result)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if not smtp_user or not smtp_password:
        _fallback_write(result, subject, plain_body)
        return

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(from_addr, [to_addr], msg.as_string())
            logger.info(
                "Health alert email sent to %s (run %s, %d alert(s)).",
                to_addr,
                result.run_id,
                len(result.alerts),
            )
            result.alert_sent = True
            return
        except (smtplib.SMTPException, OSError) as exc:
            last_exc = exc
            logger.warning("Health alert send attempt %d failed: %s", attempt, exc)

    logger.error("All health alert send attempts failed. Writing to file.")
    _fallback_write(result, subject, plain_body)


def _fallback_write(result: HealthCheckResult, subject: str, plain_body: str) -> None:
    """Write the health alert to a local file when SMTP is unavailable."""
    import pathlib

    fallback_path = pathlib.Path("data/logs") / f"health_alert_{result.run_id}.txt"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.write_text(
        f"Subject: {subject}\n\n{plain_body}", encoding="utf-8"
    )
    logger.info("Health alert written to %s", fallback_path)


# ---------------------------------------------------------------------------
# Email body builders
# ---------------------------------------------------------------------------


def _build_plain_body(result: HealthCheckResult) -> str:
    lines: list[str] = [
        f"Tripwire Health Alert — Run {result.run_id}",
        f"{len(result.alerts)} condition(s) triggered\n",
    ]
    for i, alert in enumerate(result.alerts, 1):
        lines += [
            f"--- Alert {i}: {alert.condition.upper()} [{alert.severity.upper()}] ---",
            alert.summary,
            "",
            alert.detail.replace("**", "").replace("`", "'"),
            "",
        ]
    return "\n".join(lines)


def _build_html_body(result: HealthCheckResult) -> str:
    import html

    sections: list[str] = []
    for alert in result.alerts:
        colour = "#c0392b" if alert.severity == "critical" else "#e67e22"
        detail_html = html.escape(alert.detail).replace("\n\n", "</p><p>").replace("\n", "<br>")
        sections.append(
            f'<div style="border-left:4px solid {colour};padding:10px 16px;margin:16px 0;">'
            f'<strong style="color:{colour}">[{alert.condition.upper()}]</strong> '
            f'{html.escape(alert.summary)}'
            f'<p style="margin-top:8px;color:#555">{detail_html}</p>'
            f"</div>"
        )

    body_content = "\n".join(sections)
    return (
        "<html><body>"
        f'<h2 style="font-family:sans-serif">Tripwire Health Alert — Run {result.run_id}</h2>'
        f'<p style="font-family:sans-serif">{len(result.alerts)} condition(s) triggered.</p>'
        f"{body_content}"
        "</body></html>"
    )
