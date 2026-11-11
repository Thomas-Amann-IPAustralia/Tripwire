"""
src/email_html.py

HTML rendering for Tripwire notification emails (Stage 9 per-run emails and
the weekly digest).

Email clients are far stricter than browsers. The main recipients read mail in
Outlook, whose desktop app renders HTML with Microsoft Word: it ignores most of
a ``<style>`` block, ``<div>`` padding and borders, ``border-radius``,
``max-height``/``overflow``, ``white-space: pre-wrap`` and ``<details>``.
Everything here therefore follows the usual email-safe conventions:

  • layout with nested ``role="presentation"`` tables, not divs;
  • every style inline on the element it applies to (``bgcolor`` attributes
    as well, for Outlook);
  • a fixed 680 px content column centred on a light grey canvas, which
    shrinks to the screen width on phones;
  • "buttons" are table cells with a background colour wrapping a link;
  • nothing depends on CSS that a client may strip — the email still reads
    correctly if the small ``<style>`` block in the head is dropped.

All functions return HTML strings. Callers pass raw text; this module escapes
it.
"""

from __future__ import annotations

import html
import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Palette and type
# ---------------------------------------------------------------------------

FONT = "'Segoe UI', -apple-system, Helvetica, Arial, sans-serif"
MONO = "Consolas, 'SFMono-Regular', Menlo, 'Courier New', monospace"

CANVAS = "#f2f4f7"
SURFACE = "#ffffff"
BORDER = "#e4e7ec"
INK = "#101828"
TEXT = "#344054"
MUTED = "#667085"
LINK = "#1d4ed8"
BRAND = "#1b2a4a"
BRAND_SUB = "#c7d2e6"

# (accent, tint, text-on-tint) per tone.
TONES: dict[str, tuple[str, str, str]] = {
    "red": ("#d92d20", "#fef3f2", "#b42318"),
    "amber": ("#f79009", "#fffaeb", "#b54708"),
    "blue": ("#2e90fa", "#eff8ff", "#175cd3"),
    "green": ("#12b76a", "#ecfdf3", "#067647"),
    "grey": ("#98a2b3", "#f2f4f7", "#475467"),
}


def esc(text: object) -> str:
    """Escape text (and attribute values) for HTML."""
    return html.escape("" if text is None else str(text), quote=True)


# ---------------------------------------------------------------------------
# Document shell
# ---------------------------------------------------------------------------


def document(
    *,
    title: str,
    preheader: str,
    eyebrow: str,
    heading: str,
    subheading: str,
    body: str,
    footer: str,
) -> str:
    """Wrap rendered body HTML in the full email document."""
    return (
        "<!DOCTYPE html>"
        '<html lang="en" xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">'
        "<head>"
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        f"<title>{esc(title)}</title>"
        "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings>"
        "<o:PixelsPerInch>96</o:PixelsPerInch>"
        "</o:OfficeDocumentSettings></xml></noscript><![endif]-->"
        "<style>"
        "body{margin:0;padding:0;-webkit-text-size-adjust:100%;}"
        "table{border-collapse:collapse;}"
        "a{color:" + LINK + ";}"
        "@media only screen and (max-width:620px){"
        ".tw-pad{padding-left:16px!important;padding-right:16px!important;}"
        ".tw-stat{display:block!important;width:100%!important;}"
        "}"
        "</style>"
        "</head>"
        f'<body style="margin:0;padding:0;background-color:{CANVAS};">'
        # Hidden preview text shown by inbox lists next to the subject.
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:{CANVAS};">{esc(preheader)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" bgcolor="{CANVAS}" style="background-color:{CANVAS};">'
        '<tr><td align="center" style="padding:24px 8px;">'
        '<!--[if mso]><table role="presentation" width="680" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="max-width:680px;width:100%;">'
        # Header band
        f'<tr><td class="tw-pad" bgcolor="{BRAND}" '
        f'style="background-color:{BRAND};padding:28px 32px;font-family:{FONT};">'
        f'<div style="font-size:12px;letter-spacing:2px;font-weight:700;'
        f'color:{BRAND_SUB};text-transform:uppercase;">{esc(eyebrow)}</div>'
        f'<div style="font-size:24px;line-height:32px;font-weight:700;color:#ffffff;'
        f'padding-top:6px;">{esc(heading)}</div>'
        f'<div style="font-size:14px;line-height:20px;color:{BRAND_SUB};'
        f'padding-top:4px;">{esc(subheading)}</div>'
        "</td></tr>"
        # Body
        f'<tr><td class="tw-pad" bgcolor="{SURFACE}" '
        f'style="background-color:{SURFACE};padding:28px 32px 12px 32px;'
        f'font-family:{FONT};font-size:14px;line-height:22px;color:{TEXT};">'
        f"{body}"
        "</td></tr>"
        # Footer
        f'<tr><td class="tw-pad" style="padding:20px 32px;font-family:{FONT};'
        f'font-size:12px;line-height:18px;color:{MUTED};text-align:center;">'
        f"{footer}"
        "</td></tr>"
        "</table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def paragraph(text_html: str, *, size: int = 14, color: str = TEXT,
              bold: bool = False, top: int = 0, bottom: int = 16) -> str:
    weight = "font-weight:600;" if bold else ""
    return (
        f'<p style="margin:{top}px 0 {bottom}px 0;font-family:{FONT};font-size:{size}px;'
        f'line-height:{round(size * 1.55)}px;color:{color};{weight}">{text_html}</p>'
    )


def callout(text_html: str, tone: str = "blue") -> str:
    """A tinted box with a coloured left rule."""
    accent, tint, ink = TONES[tone]
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 24px 0;">'
        f'<tr><td bgcolor="{tint}" style="background-color:{tint};'
        f'border-left:4px solid {accent};padding:14px 18px;font-family:{FONT};'
        f'font-size:15px;line-height:23px;color:{ink};">{text_html}</td></tr></table>'
    )


def section_heading(text: str, tone: str = "grey", count: int | None = None) -> str:
    accent, tint, ink = TONES[tone]
    badge = ""
    if count is not None:
        badge = (
            f'&nbsp;<span style="display:inline-block;background-color:{tint};'
            f'color:{ink};font-size:12px;line-height:18px;font-weight:700;'
            f'padding:1px 9px;border-radius:10px;">{count}</span>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:8px 0 16px 0;">'
        f'<tr><td style="border-bottom:2px solid {accent};padding:0 0 8px 0;'
        f'font-family:{FONT};font-size:17px;line-height:24px;font-weight:700;'
        f'color:{INK};">{esc(text)}{badge}</td></tr></table>'
    )


def pill(text: str, tone: str = "grey", solid: bool = False) -> str:
    accent, tint, ink = TONES[tone]
    bg, fg = (accent, "#ffffff") if solid else (tint, ink)
    return (
        f'<span style="display:inline-block;background-color:{bg};color:{fg};'
        f'font-family:{FONT};font-size:11px;line-height:16px;font-weight:700;'
        f'letter-spacing:0.5px;text-transform:uppercase;padding:3px 8px;'
        f'border-radius:4px;mso-line-height-rule:exactly;">{esc(text)}</span>'
    )


def stat_tiles(stats: list[tuple[str, str, str | None]], per_row: int = 3) -> str:
    """Render (value, label, tone|None) tuples as a grid of number tiles."""
    rows: list[str] = []
    width = f"{100 // per_row}%"
    for i in range(0, len(stats), per_row):
        cells = []
        for value, label, tone in stats[i:i + per_row]:
            colour = TONES[tone][2] if tone else INK
            cells.append(
                f'<td class="tw-stat" width="{width}" valign="top" '
                f'style="padding:0 4px 8px 4px;">'
                '<table role="presentation" width="100%" cellpadding="0" '
                'cellspacing="0" border="0">'
                f'<tr><td bgcolor="#f9fafb" style="background-color:#f9fafb;'
                f'border:1px solid {BORDER};padding:12px 14px;font-family:{FONT};">'
                f'<div style="font-size:24px;line-height:30px;font-weight:700;'
                f'color:{colour};">{esc(value)}</div>'
                f'<div style="font-size:12px;line-height:16px;color:{MUTED};">'
                f"{esc(label)}</div></td></tr></table></td>"
            )
        cells += ['<td class="tw-stat" width="' + width + '"></td>'] * (
            per_row - len(stats[i:i + per_row])
        )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 16px 0;">' + "".join(rows) + "</table>"
    )


def bullet_list(items_html: Iterable[str], tone: str = "grey") -> str:
    dot = TONES[tone][0]
    rows = "".join(
        '<tr>'
        f'<td width="18" valign="top" style="font-family:{FONT};font-size:14px;'
        f'line-height:22px;color:{dot};">&#9679;</td>'
        f'<td valign="top" style="font-family:{FONT};font-size:14px;line-height:22px;'
        f'color:{TEXT};padding-bottom:6px;">{item}</td></tr>'
        for item in items_html
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:0 0 20px 0;">{rows}</table>'
    )


def label(text: str, top: int = 18) -> str:
    """Small uppercase sub-heading inside a card."""
    return (
        f'<div style="font-family:{FONT};font-size:11px;line-height:16px;'
        f'font-weight:700;letter-spacing:1px;text-transform:uppercase;color:{MUTED};'
        f'padding:{top}px 0 6px 0;">{esc(text)}</div>'
    )


def numbered_list(items: Iterable[str]) -> str:
    rows = "".join(
        "<tr>"
        f'<td width="28" valign="top" style="padding:0 0 10px 0;">'
        f'<div style="width:20px;height:20px;line-height:20px;text-align:center;'
        f'background-color:{TONES["blue"][1]};color:{TONES["blue"][2]};'
        f'font-family:{FONT};font-size:11px;font-weight:700;border-radius:10px;">'
        f"{n}</div></td>"
        f'<td valign="top" style="font-family:{FONT};font-size:14px;line-height:21px;'
        f'color:{TEXT};padding:0 0 10px 0;">{esc(item)}</td></tr>'
        for n, item in enumerate(items, 1)
    )
    # A table rather than <ol>: Outlook applies its own list margins and
    # indents, so the numbering is drawn explicitly.
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{rows}</table>'
    )


def card(inner_html: str, tone: str) -> str:
    """A white box with a coloured left edge, followed by spacing."""
    accent = TONES[tone][0]
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 20px 0;">'
        f'<tr><td bgcolor="{SURFACE}" style="background-color:{SURFACE};'
        f'border:1px solid {BORDER};border-left:4px solid {accent};'
        f'padding:20px 22px;font-family:{FONT};font-size:14px;line-height:22px;'
        f'color:{TEXT};">{inner_html}</td></tr></table>'
    )


def button_rows(buttons: list[tuple[str, str, str]]) -> str:
    """Render (href, button_text, description) as stacked button + caption rows."""
    rows = []
    for href, text, desc in buttons:
        rows.append(
            "<tr>"
            '<td width="190" valign="middle" style="padding:0 12px 8px 0;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'border="0"><tr>'
            f'<td align="center" bgcolor="#ffffff" style="background-color:#ffffff;'
            f'border:1px solid #d0d5dd;border-radius:6px;padding:7px 10px;">'
            f'<a href="{esc(href)}" style="font-family:{FONT};font-size:13px;'
            f'line-height:18px;font-weight:600;color:{TONES["blue"][2]};'
            f'text-decoration:none;display:block;">{esc(text)}</a>'
            "</td></tr></table></td>"
            f'<td valign="middle" style="padding:0 0 8px 0;font-family:{FONT};'
            f'font-size:12px;line-height:17px;color:{MUTED};">{esc(desc)}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0">' + "".join(rows) + "</table>"
    )


def divider(top: int = 18) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:{top}px 0 0 0;"><tr>'
        f'<td style="border-top:1px solid {BORDER};font-size:1px;line-height:1px;">'
        "&nbsp;</td></tr></table>"
    )


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------

_FILE_HEADER_RE = re.compile(r"^\s*---\s*[^\s+]+\s*\+\+\+\s*[^\s@]+\s*")
_HUNK_RE = re.compile(r"(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@)")
_HUNK_NUMS_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# Stage 3 collapses whitespace in diff text (which the scoring stages need),
# so the newlines of a unified diff are gone and "+line" / "-line" markers
# survive only as " +" / " -" tokens. Split before a +/- that follows a space
# and is directly followed by a non-space character — prose dashes (" - ")
# and "A+" are left alone.
_LINE_MARK_RE = re.compile(r"\s(?=[+-]\S)")


def _diff_lines(diff_text: str) -> list[tuple[str, str]]:
    """Return (kind, text) pairs; kind is 'hunk', 'add', 'del' or 'ctx'."""
    if "@@" not in diff_text:
        return []
    out: list[tuple[str, str]] = []
    if "\n" in diff_text:
        raw_lines = diff_text.splitlines()
    else:
        raw_lines = []
        body = _FILE_HEADER_RE.sub("", diff_text)
        for piece in _HUNK_RE.split(body):
            if _HUNK_RE.fullmatch(piece):
                raw_lines.append(piece)
            elif piece.strip():
                raw_lines.extend(p for p in _LINE_MARK_RE.split(piece.strip()) if p)
    for line in raw_lines:
        if line.startswith(("---", "+++")) and not out:
            continue
        m = _HUNK_NUMS_RE.match(line)
        if m:
            start = int(m.group(3))
            length = int(m.group(4) or 1)
            out.append(("hunk", f"Around line {start}" if length <= 1
                        else f"Lines {start}–{start + length - 1}"))
            rest = line[m.end():].strip()
            if rest:
                out.append(("ctx", rest))
        elif line.startswith("+"):
            out.append(("add", line[1:].strip()))
        elif line.startswith("-"):
            out.append(("del", line[1:].strip()))
        elif line.strip():
            out.append(("ctx", line.strip()))
    return out


def diff_block(diff_text: str) -> str:
    """Render diff text as coloured added / removed / context rows.

    Falls back to a plain monospace box when the text is not a unified diff
    (FRL explainers, RSS items, first-run captures).
    """
    lines = _diff_lines(diff_text)
    if not lines:
        paras = "<br><br>".join(esc(p.strip()) for p in diff_text.split("\n\n") if p.strip())
        return (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'border="0" style="margin:0 0 4px 0;">'
            f'<tr><td bgcolor="#f9fafb" style="background-color:#f9fafb;'
            f'border:1px solid {BORDER};padding:12px 14px;font-family:{FONT};'
            f'font-size:13px;line-height:20px;color:{TEXT};">{paras}</td></tr></table>'
        )
    styles = {
        "add": (TONES["green"][1], TONES["green"][2], "+", TONES["green"][0]),
        "del": (TONES["red"][1], TONES["red"][2], "&minus;", TONES["red"][0]),
        "ctx": ("#ffffff", MUTED, "", "#ffffff"),
    }
    rows: list[str] = []
    for kind, text in lines:
        if kind == "hunk":
            rows.append(
                f'<tr><td colspan="2" bgcolor="#f2f4f7" style="background-color:#f2f4f7;'
                f'padding:5px 12px;font-family:{FONT};font-size:11px;line-height:16px;'
                f'font-weight:700;letter-spacing:0.5px;color:{MUTED};'
                f'border-top:1px solid {BORDER};">{esc(text)}</td></tr>'
            )
            continue
        bg, fg, mark, edge = styles[kind]
        rows.append(
            f'<tr><td width="22" valign="top" align="center" bgcolor="{bg}" '
            f'style="background-color:{bg};border-left:3px solid {edge};'
            f'padding:4px 0;font-family:{MONO};font-size:12px;line-height:18px;'
            f'font-weight:700;color:{fg};">{mark}</td>'
            f'<td valign="top" bgcolor="{bg}" style="background-color:{bg};'
            f'padding:4px 12px 4px 6px;font-family:{FONT};font-size:13px;'
            f'line-height:19px;color:{fg if kind != "ctx" else MUTED};">'
            f"{esc(text)}</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border:1px solid {BORDER};margin:0 0 4px 0;">'
        + "".join(rows) + "</table>"
    )
