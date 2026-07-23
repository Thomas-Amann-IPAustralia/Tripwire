# BuildFast — InnovationMonth deck (source)

Editable source for `docs/260610_BUILDFAST_InnoMonth.pptx`.

Each slide is authored as a self-contained HTML fragment, rendered to a
2560×1440 image with headless Chromium, and packed one-image-per-slide into the
`.pptx`. This keeps the deck **pixel-identical everywhere** (no font
dependencies on the presenting machine) and matches the dark "Tripwire dashboard"
aesthetic — Bebas Neue display, DM Mono labels, Lora serif body, thin rules,
sharp corners.

## Layout

```
slides/NN.html     one fragment per slide (just the <div class="slide">…</div>)
build/base.css     design system: palette, type scale, components
build/fonts.css    Bebas Neue / DM Mono / Lora, embedded as data-URIs
build/make.py      wraps a fragment in <head> + renders it to render/NN.png
build/montage.py   tiles rendered slides into 2×2 QA sheets
build/assemble.py  packs render/*.png into the final .pptx (+ speaker notes)
media/             the handful of screenshots / assets slides embed
```

## Edit a slide

1. Edit `slides/NN.html`. Handy tokens the builder expands:
   - `%%ICON name colour size%%` — inline stroke icon (see `ICONS` in `make.py`)
   - `%%IMG file.png%%` — embeds `media/file.png` as a data-URI
   - `%%FOOT slug | NN%%` — the bottom-left slug + bottom-right page number
2. Re-render just that slide:
   ```bash
   cd build && python3 make.py 06        # or: python3 make.py all
   ```
3. Eyeball it (`render/06.png`), then rebuild the deck:
   ```bash
   python3 assemble.py                    # writes ../260610_BUILDFAST_InnoMonth.pptx
   ```

## Requirements

- Python: `python-pptx`, `Pillow`
- Headless Chromium for rendering. `make.py` points `CHROME` at
  `/opt/pw-browsers/chromium`; change that one line to your local Chrome/Chromium.
- The renderer shoots a tall window and crops to 2560×1440 because headless
  viewport height ≈ 0.87× the requested window height — see `render_one()`.

## Notes

- `assemble.py` copies speaker notes from the existing `.pptx`, so rebuilds keep
  them. Delete a slide fragment + its render to drop a slide (and update the
  `range(1, 46)` bounds).
- Colours live once, in `:root` of `base.css`. Accent meaning across the deck:
  blue = Tripwire, purple = Octavius, green = success/kept, red = alert,
  amber = caution/paid, warm-neutral = external sources.
