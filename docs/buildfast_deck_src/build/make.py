#!/usr/bin/env python3
"""Wrap slide-body fragments into full HTML and render to 2560x1440 PNGs via Chromium."""
import os, re, sys, subprocess, base64, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT/"build"; SLIDES = ROOT/"slides"; REND = ROOT/"render"
MEDIA = ROOT/"media"
REND.mkdir(exist_ok=True)
CHROME = "/opt/pw-browsers/chromium"

FONTS = (BUILD/"fonts.css").read_text()
BASE  = (BUILD/"base.css").read_text()

# ---- feather/lucide-style stroke icons, 24x24 viewBox ----
ICONS = {
 "globe":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/>',
 "file":'<path class="st" d="M6 3h8l4 4v14H6z"/><path class="st" d="M14 3v4h4"/>',
 "filetext":'<path class="st" d="M6 3h8l4 4v14H6z"/><path class="st" d="M14 3v4h4M9 12h6M9 16h6"/>',
 "filediff":'<path class="st" d="M4 4h6l3 3v11H4z"/><path class="st" d="M10 4v3h3M6 10h5M6 13h5"/><path class="st" d="M20 6l-3 3 3 3" stroke-dasharray="0"/>',
 "database":'<ellipse class="st" cx="12" cy="6" rx="8" ry="3"/><path class="st" d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path class="st" d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
 "refresh":'<path class="st" d="M20 11a8 8 0 0 0-14-4M4 6v4h4"/><path class="st" d="M4 13a8 8 0 0 0 14 4M20 18v-4h-4"/>',
 "activity":'<path class="st" d="M3 12h4l3 8 4-16 3 8h4"/>',
 "hash":'<path class="st" d="M5 9h14M5 15h14M9 4l-1.6 16M16.6 4L15 20"/>',
 "mail":'<rect class="st" x="3" y="5" width="18" height="14"/><path class="st" d="M3 6l9 7 9-7"/>',
 "mailcheck":'<path class="st" d="M3 5h18v9H3z"/><path class="st" d="M3 6l9 6 9-6"/><path class="st" d="M14 19l2.2 2 4-4.5"/>',
 "pencil":'<path class="st" d="M4 20l1-4L16 5l3 3L8 19z"/><path class="st" d="M14 7l3 3"/>',
 "branch":'<circle class="st" cx="6" cy="6" r="2.4"/><circle class="st" cx="6" cy="18" r="2.4"/><circle class="st" cx="18" cy="9" r="2.4"/><path class="st" d="M6 8.4v7.2M6 12h6c2 0 4-1 4-3"/>',
 "target":'<circle class="st" cx="12" cy="12" r="9"/><circle class="st" cx="12" cy="12" r="5"/><circle class="stf" cx="12" cy="12" r="1.6" fill="currentColor"/>',
 "cpu":'<rect class="st" x="6" y="6" width="12" height="12"/><rect class="st" x="9.5" y="9.5" width="5" height="5"/><path class="st" d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
 "book":'<path class="st" d="M12 6C10 4.5 6.5 4 4 4.5v14C6.5 18 10 18.5 12 20M12 6c2-1.5 5.5-2 8-1.5v14c-2.5-.5-6 0-8 1.5M12 6v14"/>',
 "rss":'<path class="st" d="M5 11a8 8 0 0 1 8 8M5 5a14 14 0 0 1 14 14"/><circle class="stf" cx="6" cy="18" r="1.6" fill="currentColor"/>',
 "layers":'<path class="st" d="M12 3l9 5-9 5-9-5z"/><path class="st" d="M3 13l9 5 9-5M3 17l9 5 9-5" opacity=".85"/>',
 "sliders":'<path class="st" d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle class="st" cx="15" cy="8" r="2"/><circle class="st" cx="9" cy="16" r="2"/>',
 "alert":'<path class="st" d="M12 3l10 18H2z"/><path class="st" d="M12 10v5M12 18v.5"/>',
 "alertc":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M12 7v6M12 16.5v.5"/>',
 "brain":'<path class="st" d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 1 5 3 3 0 0 0 3 3 2.5 2.5 0 0 0 3-1V5a2.5 2.5 0 0 0-3-1zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-1 5 3 3 0 0 1-3 3 2.5 2.5 0 0 1-3-1"/>',
 "finger":'<path class="st" d="M8 11a4 4 0 0 1 8 0v2M6 13c0-4 2.5-7 6-7M12 13v3a3 3 0 0 1-3 3M16 14a8 8 0 0 1-1 4"/>',
 "clock":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M12 7v5l3.5 2"/>',
 "share":'<circle class="st" cx="6" cy="12" r="2.6"/><circle class="st" cx="17" cy="6" r="2.6"/><circle class="st" cx="17" cy="18" r="2.6"/><path class="st" d="M8.3 10.8l6.4-3.6M8.3 13.2l6.4 3.6"/>',
 "check":'<path class="st" d="M4 12l5 5L20 6"/>',
 "checkc":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M8 12l3 3 5-6"/>',
 "x":'<path class="st" d="M6 6l12 12M18 6L6 18"/>',
 "xc":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M9 9l6 6M15 9l-6 6"/>',
 "help":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M9.2 9.3a2.8 2.8 0 0 1 5.4 1c0 1.9-2.6 2.2-2.6 4M12 17.5v.4"/>',
 "rocket":'<path class="st" d="M5 15c-1 2-1 4-1 4s2 0 4-1M9 13l-3 3M11 15l3-3M14 4c3 0 6 3 6 6-2 4-6 7-9 8l-5-5c1-3 4-7 8-9z"/><circle class="st" cx="14.5" cy="9.5" r="1.6"/>',
 "wrench":'<path class="st" d="M15 3a5 5 0 0 0-4.5 7l-6 6a2 2 0 0 0 3 3l6-6A5 5 0 0 0 20 8l-3 3-2-2 3-3a5 5 0 0 0-3-3z"/>',
 "play":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M10 8.5l6 3.5-6 3.5z"/>',
 "github":'<path class="st" d="M9 19c-4 1.3-4-2-6-2m12 4v-3.5c0-1 .1-1.4-.5-2 2.8-.3 4.5-1.4 4.5-5 0-1.1-.4-2-1-2.7.3-.9.2-2-.2-2.8 0 0-1-.3-3 1a10 10 0 0 0-5 0c-2-1.3-3-1-3-1-.4.8-.5 1.9-.2 2.8-.6.7-1 1.6-1 2.7 0 3.6 1.7 4.7 4.5 5-.4.4-.5.9-.5 1.7V21"/>',
 "search":'<circle class="st" cx="11" cy="11" r="6"/><path class="st" d="M20 20l-4.5-4.5"/>',
 "eye":'<path class="st" d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle class="st" cx="12" cy="12" r="2.6"/>',
 "bell":'<path class="st" d="M6 10a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6zM10 20a2 2 0 0 0 4 0"/>',
 "link":'<path class="st" d="M10 13a3 3 0 0 0 4 .3l3-3a3 3 0 0 0-4-4l-1 1M14 11a3 3 0 0 0-4-.3l-3 3a3 3 0 0 0 4 4l1-1"/>',
 "calendar":'<rect class="st" x="4" y="5" width="16" height="16"/><path class="st" d="M4 9h16M8 3v4M16 3v4"/>',
 "filter":'<path class="st" d="M4 5h16l-6 7v6l-4 2v-8z"/>',
 "arrow":'<path class="st" d="M4 12h15M13 6l6 6-6 6"/>',
 "chevr":'<path class="st" d="M9 6l6 6-6 6"/>',
 "shield":'<path class="st" d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path class="st" d="M8.5 12l2.5 2.5 4.5-5"/>',
 "flask":'<path class="st" d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/><path class="st" d="M7.5 15h9"/>',
 "code":'<path class="st" d="M8 8l-4 4 4 4M16 8l4 4-4 4M13 5l-2 14"/>',
 "list":'<path class="st" d="M8 6h12M8 12h12M8 18h12M4 6v.01M4 12v.01M4 18v.01"/>',
 "tag":'<path class="st" d="M3 12l8-8h7v7l-8 8z"/><circle class="stf" cx="14.5" cy="9.5" r="1.4" fill="currentColor"/>',
 "users":'<circle class="st" cx="9" cy="8" r="3"/><path class="st" d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5a3 3 0 0 1 0 6M17 14c2.5.5 4 2.6 4 6"/>',
 "zap":'<path class="st" d="M13 3L5 13h6l-1 8 8-10h-6z"/>',
 "lock":'<rect class="st" x="5" y="11" width="14" height="9"/><path class="st" d="M8 11V8a4 4 0 0 1 8 0v3"/>',
 "map":'<path class="st" d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14"/>',
 "scan":'<path class="st" d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16"/>',
 "route":'<circle class="st" cx="6" cy="19" r="2.4"/><circle class="st" cx="18" cy="5" r="2.4"/><path class="st" d="M8 19h6a4 4 0 0 0 0-8H9a4 4 0 0 1 0-8h3"/>',
 "quote":'<path class="st" d="M7 7C5 8 4 10 4 13v4h6v-6H6c0-2 .5-3 2-4zM17 7c-2 1-3 3-3 6v4h6v-6h-4c0-2 .5-3 2-4z"/>',
 "compass":'<circle class="st" cx="12" cy="12" r="9"/><path class="st" d="M16 8l-2 6-6 2 2-6z"/>',
 "gauge":'<path class="st" d="M4 15a8 8 0 1 1 16 0"/><path class="st" d="M12 15l4-4"/><circle class="stf" cx="12" cy="15" r="1.4" fill="currentColor"/>',
 "doc-scan":'<path class="st" d="M7 4h7l4 4v6M7 4v16h11v-6"/><path class="st" d="M13 4v4h4"/><circle class="st" cx="10" cy="15" r="3"/><path class="st" d="M14 19l-2-2"/>',
 "dollar":'<path class="st" d="M12 3v18M16 7c0-2-2-3-4-3s-4 1-4 3 2 3 4 3 4 1 4 3-2 3-4 3-4-1-4-3"/>',
 "spark":'<path class="st" d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
}

def icon(name, color="var(--ink)", size=24, sw=1.7):
    body = ICONS[name]
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
            f'style="color:{color};stroke-width:{sw}" fill="none">{body}</svg>')

def media_uri(fname):
    p = MEDIA/fname
    data = base64.b64encode(p.read_bytes()).decode()
    ext = p.suffix.lstrip('.').lower(); mt = 'jpeg' if ext in ('jpg','jpeg') else ext
    return f"data:image/{mt};base64,{data}"

def foot(m):
    slug, nn = [x.strip() for x in m.group(1).split('|')]
    return (f'<div class="slugtag">{slug}</div>'
            f'<div class="pageno">{nn} / 45</div>')

def expand(body):
    body = re.sub(r'%%FOOT\s+([^%]+?)%%', foot, body)
    # %%ICON name color size sw%%
    def ic(m):
        parts = m.group(1).split()
        name = parts[0]
        color = parts[1] if len(parts)>1 else "var(--ink)"
        size = int(parts[2]) if len(parts)>2 else 24
        sw = float(parts[3]) if len(parts)>3 else 1.7
        color = color.replace('~','var(--').replace('!',')') if color.startswith('~') else color
        return icon(name, color, size, sw)
    body = re.sub(r'%%ICON\s+([^%]+?)%%', ic, body)
    body = re.sub(r'%%IMG\s+([^%\s]+)%%', lambda m: media_uri(m.group(1)), body)
    return body

def wrap(body):
    body = expand(body)
    return (f"<!doctype html><html><head><meta charset=utf8>"
            f"<style>{FONTS}</style><style>{BASE}</style></head>"
            f"<body>{body}</body></html>")

def build_one(sid):
    frag = (SLIDES/f"{sid}.html").read_text()
    html = wrap(frag)
    (REND/f"{sid}.html").write_text(html)
    return REND/f"{sid}.html"

def render_one(sid):
    from PIL import Image
    htmlp = build_one(sid)
    outp = REND/f"{sid}.png"
    raw = REND/f"_{sid}_raw.png"
    if outp.exists(): outp.unlink()
    if raw.exists(): raw.unlink()
    # headless viewport height ~0.87x window height, so shoot tall then crop to 2560x1440
    subprocess.run([CHROME,"--headless","--no-sandbox","--disable-gpu","--hide-scrollbars",
                    "--force-device-scale-factor=2","--window-size=1280,900",
                    f"--screenshot={raw}",f"file://{htmlp}"],
                   capture_output=True)
    if raw.exists():
        im = Image.open(raw)
        im.crop((0,0,2560,1440)).save(outp)
        raw.unlink()
    return outp

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args==["all"]:
        args = sorted(p.stem for p in SLIDES.glob("*.html"))
    for sid in args:
        p = render_one(sid)
        ok = p.exists() and p.stat().st_size>0
        print(("OK  " if ok else "FAIL")+f" {sid} -> {p}")
