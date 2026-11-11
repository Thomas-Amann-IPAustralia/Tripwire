"""
src/email_security.py

Anti-phishing and anti-forgery controls for Tripwire's email traffic.

Tripwire is a public repository, so its email templates, subject lines,
schedule and feedback format are all visible to anyone. These controls rely
only on values held in GitHub secrets, never in the repository:

1. Security phrase (outbound).  ``TRIPWIRE_EMAIL_PHRASE`` is stamped at the
   top of every email Tripwire sends (Stage 9 alerts, the weekly digest and
   health alerts). Recipients learn the phrase and treat any "Tripwire" email
   without it as phishing. The phrase is added only when the MIME message is
   built, so it never reaches fallback files, dry-run output or the database,
   all of which can end up in public places (workflow artifacts, logs and the
   ``data-*`` GitHub Releases).

2. Signed feedback links (inbound).  Every feedback mailto body carries a
   ``token:`` line: an HMAC of the run, page and category keyed from the
   security phrase. ``feedback_ingestion`` rejects replies whose token does
   not match, so a forged reply needs a genuine email to copy from.

3. Sender authentication (inbound).  Feedback is only accepted from an
   allowlisted sender (the notification recipients plus the optional
   ``FEEDBACK_ALLOWED_SENDERS`` secret) whose message passed DMARC, or
   DKIM aligned with the From domain, according to the
   ``Authentication-Results`` header Gmail itself adds on receipt.

4. Recipient addresses come from the ``CONTENT_OWNER_EMAIL`` and
   ``HEALTH_ALERT_EMAIL`` secrets, so the repository no longer names the
   people to target. The YAML keys remain as an optional fallback for local
   runs.

Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from email.utils import getaddresses, parseaddr
from typing import Any

logger = logging.getLogger(__name__)

PHRASE_ENV = "TRIPWIRE_EMAIL_PHRASE"
ALLOWED_SENDERS_ENV = "FEEDBACK_ALLOWED_SENDERS"
RECIPIENT_ENV = {
    "content_owner_email": "CONTENT_OWNER_EMAIL",
    "health_alert_email": "HEALTH_ALERT_EMAIL",
}

# Gmail stamps its own verdict with this authserv-id. Headers further down a
# message (added before it reached Gmail) are attacker-controlled and ignored.
TRUSTED_AUTHSERV_ID = "mx.google.com"

_TOKEN_CONTEXT = b"tripwire-feedback-token-v1"
_TOKEN_HEX_LEN = 20

_PHRASE_MARKER = "<!--TRIPWIRE_SECURITY_PHRASE-->"


# ---------------------------------------------------------------------------
# Recipient addresses
# ---------------------------------------------------------------------------


def notification_address(config: dict[str, Any], key: str) -> str:
    """Return the address for ``content_owner_email`` or ``health_alert_email``.

    The GitHub secret (environment variable) wins; the YAML value is a
    fallback for local development. Returns "" when neither is set.
    """
    env_value = os.environ.get(RECIPIENT_ENV[key], "").strip()
    if env_value:
        return env_value
    value = config.get("notifications", {}).get(key) or ""
    return str(value).strip()


def notification_recipients(config: dict[str, Any]) -> list[str]:
    """Unique, ordered list of the content owner and health alert addresses."""
    out: list[str] = []
    for key in ("content_owner_email", "health_alert_email"):
        addr = notification_address(config, key)
        if addr and addr.lower() not in (a.lower() for a in out):
            out.append(addr)
    return out


# ---------------------------------------------------------------------------
# Security phrase (outbound)
# ---------------------------------------------------------------------------


def security_phrase() -> str:
    """Return the configured security phrase, or "" when unset."""
    return os.environ.get(PHRASE_ENV, "").strip()


def phrase_marker() -> str:
    """Placeholder that ``email_html.document`` emits for the phrase banner."""
    return _PHRASE_MARKER


def stamp_text(body_text: str, phrase: str | None = None) -> str:
    """Prefix a plain-text email body with the security phrase banner."""
    phrase = security_phrase() if phrase is None else phrase
    if phrase:
        banner = (
            f"Tripwire security phrase: {phrase}\n"
            "If this phrase is missing or different, do not click any links; "
            "report the email as phishing.\n"
        )
    else:
        banner = (
            "WARNING: no Tripwire security phrase is configured, so this email "
            "cannot be verified. Do not click links unless you are sure it is "
            "genuine.\n"
        )
    return banner + "-" * 70 + "\n\n" + body_text


def stamp_html(body_html: str, phrase: str | None = None) -> str:
    """Insert the security phrase banner into an HTML email body."""
    from src import email_html as eh  # local import avoids an import cycle

    phrase = security_phrase() if phrase is None else phrase
    banner = eh.security_banner(phrase)
    if _PHRASE_MARKER in body_html:
        return body_html.replace(_PHRASE_MARKER, banner, 1)
    banner = ('<table role="presentation" width="100%" cellpadding="0" '
              'cellspacing="0" border="0">' + banner + "</table>")
    match = re.search(r"<body[^>]*>", body_html, flags=re.IGNORECASE)
    if match:
        return body_html[: match.end()] + banner + body_html[match.end():]
    return banner + body_html


def strip_marker(body_html: str) -> str:
    """Remove the unfilled placeholder (used for previews and fallbacks)."""
    return body_html.replace(_PHRASE_MARKER, "")


# ---------------------------------------------------------------------------
# Feedback tokens
# ---------------------------------------------------------------------------


def _token_key(phrase: str) -> bytes:
    return hmac.new(phrase.encode("utf-8"), _TOKEN_CONTEXT, hashlib.sha256).digest()


def feedback_token(run_id: str, page_id: str, category: str,
                   phrase: str | None = None) -> str:
    """Return the signed token for a feedback mailto body ("" if no phrase)."""
    phrase = security_phrase() if phrase is None else phrase
    if not phrase:
        return ""
    message = "\x1f".join((run_id.strip(), page_id.strip(), category.strip().lower()))
    digest = hmac.new(_token_key(phrase), message.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:_TOKEN_HEX_LEN]


def verify_feedback_token(token: str, run_id: str, page_id: str, category: str,
                          phrase: str | None = None) -> bool:
    """Constant-time check of a feedback token. False when no phrase is set."""
    expected = feedback_token(run_id, page_id, category, phrase)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token.strip().lower())


_TOKEN_IN_TEXT_RE = re.compile(
    r"(token(?::|%3A)(?:\s|%20)*)[0-9a-f]{" + str(_TOKEN_HEX_LEN) + "}", re.IGNORECASE
)


def redact_tokens(text: str) -> str:
    """Blank out feedback tokens (plain or URL-encoded) before text is saved
    anywhere that may become public, such as SMTP fallback files."""
    return _TOKEN_IN_TEXT_RE.sub(r"\1[redacted]", text)


# ---------------------------------------------------------------------------
# Sender authentication (inbound)
# ---------------------------------------------------------------------------


def allowed_senders(config: dict[str, Any]) -> list[str]:
    """Addresses (or ``@domain`` entries) allowed to submit feedback."""
    entries = [a.lower() for a in notification_recipients(config)]
    extra = os.environ.get(ALLOWED_SENDERS_ENV, "")
    for item in re.split(r"[,;\s]+", extra):
        item = item.strip().lower()
        if item and item not in entries:
            entries.append(item)
    return entries


def sender_address(msg: Any) -> str:
    """Return the single From address of a message, lower-cased ("" if unclear)."""
    values = msg.get_all("From") or []
    addresses = [a for _, a in getaddresses([str(v) for v in values]) if a]
    if len(addresses) != 1:
        return ""
    return addresses[0].strip().lower()


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _is_allowed(address: str, allowlist: list[str]) -> bool:
    if not address:
        return False
    for entry in allowlist:
        if entry.startswith("@"):
            if _domain(address) == entry[1:]:
                return True
        elif address == entry:
            return True
    return False


def _aligned(domain: str, from_domain: str) -> bool:
    domain = domain.lower().lstrip("@")
    return bool(domain) and (domain == from_domain or from_domain.endswith("." + domain)
                             or domain.endswith("." + from_domain))


def _trusted_auth_results(msg: Any) -> str:
    """The topmost Authentication-Results header, if Gmail added it."""
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return ""
    top = " ".join(str(headers[0]).split())
    authserv_id = top.split(";", 1)[0].strip().lower()
    return top if authserv_id == TRUSTED_AUTHSERV_ID else ""


def authentication_passed(msg: Any, from_domain: str) -> bool:
    """True when Gmail recorded DMARC pass, or DKIM pass aligned to From."""
    results = _trusted_auth_results(msg)
    if not results:
        return False
    for clause in results.split(";")[1:]:
        clause = clause.strip().lower()
        if re.match(r"dmarc=pass\b", clause):
            hf = re.search(r"header\.from=([^\s;]+)", clause)
            if hf is None or _aligned(hf.group(1), from_domain):
                return True
        if re.match(r"dkim=pass\b", clause):
            for tag in ("header.d", "header.i"):
                m = re.search(rf"{re.escape(tag)}=([^\s;]+)", clause)
                if m and _aligned(_domain(m.group(1)) or m.group(1), from_domain):
                    return True
    return False


def check_sender(msg: Any, allowlist: list[str]) -> tuple[bool, str]:
    """Return (ok, reason). The reason never includes the full address."""
    address = sender_address(msg)
    if not address:
        return False, "missing or ambiguous From address"
    domain = _domain(address)
    if not _is_allowed(address, allowlist):
        return False, f"sender at @{domain} is not on the feedback allowlist"
    if not authentication_passed(msg, domain):
        return False, f"sender at @{domain} failed DMARC/DKIM authentication"
    return True, "ok"


# ---------------------------------------------------------------------------
# Comment sanitising (feedback.jsonl is committed to a public repository)
# ---------------------------------------------------------------------------

_MAX_COMMENT_CHARS = 1000
_QUOTE_MARKERS = re.compile(
    r"^\s*(>|-{2,}\s*original message|_{5,}|from:\s|sent:\s|on .+ wrote:|sent from my)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")


def sanitise_comment(comment: str) -> str:
    """Cut quoted replies and signatures, redact links and contact details."""
    kept: list[str] = []
    for line in comment.splitlines():
        if _QUOTE_MARKERS.match(line):
            break
        kept.append(line)
    text = "\n".join(kept).strip()
    text = _URL_RE.sub("[link removed]", text)
    text = _EMAIL_RE.sub("[email removed]", text)
    text = _PHONE_RE.sub("[number removed]", text)
    if len(text) > _MAX_COMMENT_CHARS:
        text = text[:_MAX_COMMENT_CHARS].rstrip() + " [truncated]"
    return text


def redacted_address(address: str) -> str:
    """Show only the domain of an address, for logs and records."""
    _, addr = parseaddr(address)
    return f"@{_domain(addr)}" if addr else ""
