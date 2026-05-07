# Dashboard Changes

## 1. Light Mode

- Added `[data-theme="light"]` CSS custom property block in `globals.css` overriding
  every `--` variable with a white/near-white palette (#F5F5F5 page bg, #FFFFFF panels,
  #111111 primary text, #CCCCCC borders). Accent and state colours are darkened/saturated
  versions of the dark-mode hues so they read on white.
- Added an inline `<script>` to `index.html` (executes before first paint) that reads
  `localStorage['tw-theme']` and sets `data-theme="light"` on `<html>` if needed —
  eliminates flash-of-wrong-theme.
- Added a `ThemeToggle` button (☀ / ☽) to the Topbar. Clicking toggles the
  `data-theme` attribute on `<html>` and persists the choice in `localStorage['tw-theme']`.
- The Three.js canvas background colour is now read from `--bg-primary` via
  `getComputedStyle` on every animation frame, so it responds instantly to theme changes.

## 2. Dark Mode Colour and Contrast Improvements

- `--text-primary` raised from `#e8e2d4` → `#e8e8e8` (near-pure white).
- `--text-secondary` raised from `#9e9888` → `#999999`.
- `--text-tertiary` raised from `#5c5a52` → `#777777`.
- `--rule` tightened from `#2e2e28` → `#333333`; `--rule-accent` from `#4a4a40` → `#444444`.
- Panel backgrounds pushed darker: `--bg-primary` `#0e0e0d`, `--bg-secondary` `#161614`,
  `--bg-tertiary` `#1e1e1b` — clearly separated from each other.
- All state and stage colours updated to full-saturation hues:
  - `--state-ok` `#4ADE80`, `--state-alert` `#F87171`, `--state-warn` `#FBBF24`, `--state-error` `#EF4444`.
  - Stage colours updated to match (vivid green, red, amber, blue, purple, emerald).

## 3. Global Text Size Increase

- `html, body { font-size: 15px }` added — all `rem`-based sizes scale automatically.
- Hardcoded `10px` DM Mono labels in `Topbar.jsx` raised to `12px`.
- Wordmark font size raised from `18px` → `22px`.
- Verdict pill font sizes raised from `10px` → `12px` in `globals.css`.
- Corpus section header raised from `42px` → `50px`; tab labels from `14px` → `17px`;
  stats strip from `11px` → `13px`.

## 4. 3D Embedding View — Full Rework

### Backend (`server/routes/embeddings.js`)

- Completely replaced the old page-level embeddings endpoint with a chunk-level one.
- Queries `chunks` (joined to active `pages`), loads `chunk_embedding` BLOBs (falls back
  to `doc_embedding` per the spec's TODO note if chunk embedding is absent).
- Runs server-side PCA (3 components via `ml-pca`) and K-Means (k=8, 3D) over all chunk
  embeddings. Result is cached keyed on SQLite file mtime — same invalidation strategy as
  the existing page-level cache.
- Returns: `chunk_id`, `document_id`, `document_title`, `chunk_text` (first 200 chars),
  `x`, `y`, `z`, `cluster_id`.

### Frontend (`src/visualisations/Embedding3D.jsx`)

- **Point cloud rendering** — replaced sphere `THREE.Mesh` objects with a single
  `THREE.Points` object using `BufferGeometry` (`Float32Array` for positions and per-vertex
  colours). `PointsMaterial` with `vertexColors: true`, `size: 4.0`, `sizeAttenuation: true`.
  No fake/noise background particles; every point is a real chunk.
- **Cluster colours** — 12-colour mid-saturation palette that works on both dark and
  light backgrounds, assigned by `cluster_id`.
- **Screen-space hover raycasting** — on each animation frame, all visible chunk positions
  are projected to screen space and the nearest point within an 8 px threshold is identified.
  Hover tooltip shows: document title, cluster id, first 150 chars of chunk text.
- **Click to select document** — clicking a point highlights all chunks belonging to the
  same document (brightened ×1.6) and dims all others (×0.25). Clicking empty space or a
  "CLEAR SELECTION" button resets.
- **Auto-orbit toggle** — top-left button, off by default.
- **Document filter sidebar** — right-side panel listing every unique document title with
  checkboxes (all checked by default). "SELECT ALL" / "CLEAR ALL" buttons. Toggling a
  document rebuilds the position and colour buffers to show only visible chunks.
- **Cluster legend** — below the document filter; one row per cluster showing colour swatch
  and live chunk count (updates when document filters change).
- **Axes helper** — `THREE.AxesHelper(1.4)` plus CSS2D labels `PC1`, `PC2`, `PC3` at
  the positive axis ends.
- **Cluster centroids** — centroid of each visible cluster rendered as a larger point
  (size 10) in the cluster colour. CSS2D label `C{n}` displayed above each centroid.
- **Zoom to cluster** — double-clicking a centroid label smoothly animates the camera
  to the cluster's centroid via a requestAnimationFrame ease-in-out quadratic lerp (800 ms,
  no extra library).
- **Error boundary** — `Embedding3DErrorBoundary` class component wraps the inner
  component; a Three.js crash renders an error message without taking down the rest of
  the dashboard.
- **Theme response** — canvas background colour read from `--bg-primary` CSS variable on
  every frame.

## 5. General

- `Corpus.jsx` updated: removed the `pages` prop from `<Embedding3D>` (the component now
  fetches its own chunk data via `useEmbeddings()`).
- Build confirmed: `npm run build` passes with 0 errors.
