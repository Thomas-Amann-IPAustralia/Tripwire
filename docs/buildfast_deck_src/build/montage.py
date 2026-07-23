#!/usr/bin/env python3
"""Tile rendered slide PNGs into 2x2 montages for QA viewing."""
import sys, pathlib
from PIL import Image, ImageDraw
REND = pathlib.Path(__file__).resolve().parent.parent/"render"
ids = sys.argv[1:]
cellw, cellh = 1280, 720
per = 4
groups = [ids[i:i+per] for i in range(0, len(ids), per)]
out=[]
for gi, g in enumerate(groups):
    sheet = Image.new('RGB',(cellw*2, cellh*2),(40,40,40))
    d=ImageDraw.Draw(sheet)
    for i, sid in enumerate(g):
        p = REND/f"{sid}.png"
        if not p.exists(): continue
        im = Image.open(p).resize((cellw,cellh))
        r,c = divmod(i,2)
        sheet.paste(im,(c*cellw, r*cellh))
        d.rectangle([c*cellw, r*cellh, c*cellw+70, r*cellh+26], fill=(0,0,0))
        d.text((c*cellw+6, r*cellh+7), f"s{sid}", fill=(255,255,0))
    o = REND/f"_mont_{gi}.png"
    sheet.save(o); out.append(str(o)); print(o)
