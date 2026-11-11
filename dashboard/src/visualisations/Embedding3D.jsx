import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import * as THREE from 'three';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { useEmbeddings } from '../hooks/useData.js';
import { Icon, Loading, EmptyState, Callout } from '../components/ui.jsx';
import { CANVAS_BG } from './KnowledgeGraph.jsx';

// 12 distinct, earthy colours chosen to sit with the warm palette and to stay
// legible on both the light canvas and the dark theme.
const CLUSTER_PALETTE = [
  '#2f7d6d', // teal
  '#c0603d', // terracotta
  '#3b6fa0', // slate blue
  '#c4952a', // ochre
  '#7a62b0', // violet
  '#5b9a52', // sage
  '#d07a3c', // copper
  '#b2557c', // plum
  '#2f93a8', // lagoon
  '#8a6d3b', // umber
  '#6f8f2f', // olive
  '#4a5ea8', // indigo
];

function hexToRGB(hex) {
  const c = new THREE.Color(hex);
  return [c.r, c.g, c.b];
}

function readCSSVar(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

// Soft filled-circle sprite — renders round data points instead of gl squares
function createCircleSprite() {
  const sz = 64;
  const canvas = document.createElement('canvas');
  canvas.width = sz; canvas.height = sz;
  const ctx = canvas.getContext('2d');
  const r = sz / 2;
  const grd = ctx.createRadialGradient(r, r, 0, r, r, r - 1);
  grd.addColorStop(0,   'rgba(255,255,255,1.0)');
  grd.addColorStop(0.6, 'rgba(255,255,255,0.95)');
  grd.addColorStop(1.0, 'rgba(255,255,255,0.0)');
  ctx.fillStyle = grd;
  ctx.beginPath();
  ctx.arc(r, r, r - 1, 0, Math.PI * 2);
  ctx.fill();
  return new THREE.CanvasTexture(canvas);
}

// Ring sprite for cluster centroids — visually distinct from data points
function createRingSprite() {
  const sz = 64;
  const canvas = document.createElement('canvas');
  canvas.width = sz; canvas.height = sz;
  const ctx = canvas.getContext('2d');
  const r = sz / 2;
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  ctx.arc(r, r, r - 2, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = 'destination-out';
  ctx.beginPath();
  ctx.arc(r, r, r - 10, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = 'source-over';
  return new THREE.CanvasTexture(canvas);
}

// ─── Error boundary ──────────────────────────────────────────────────────────
class Embedding3DErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(err) {
    console.error('[Embedding3D error boundary]', err);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '24px' }}>
          <Callout tone="error" icon="alert" title="The 3D view could not be displayed">
            {this.state.error?.message}
          </Callout>
        </div>
      );
    }
    return this.props.children;
  }
}

// ─── Filter sidebar ───────────────────────────────────────────────────────────
function FilterSidebar({ documents, hiddenDocs, onToggleDoc, onSelectAll, onClearAll, clusters, chunks }) {
  const clusterCounts = useMemo(() => {
    const counts = {};
    for (const chunk of chunks) {
      if (hiddenDocs.has(chunk.document_id)) continue;
      counts[chunk.cluster_id] = (counts[chunk.cluster_id] || 0) + 1;
    }
    return counts;
  }, [chunks, hiddenDocs]);

  return (
    <div style={{
      width: '260px',
      minWidth: '260px',
      display: 'flex',
      flexDirection: 'column',
      borderLeft: '1px solid var(--line)',
      background: 'var(--surface)',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
        <div style={{ fontSize: '13px', fontWeight: 650, color: 'var(--ink)', marginBottom: '8px' }}>
          Documents <span className="tw-muted" style={{ fontWeight: 500 }}>({documents.length - hiddenDocs.size} of {documents.length} shown)</span>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button type="button" className="tw-btn is-sm" onClick={onSelectAll}>Select all</button>
          <button type="button" className="tw-btn is-sm" onClick={onClearAll}>Clear all</button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {documents.map(doc => (
          <label key={doc.id} style={{
            display: 'flex', alignItems: 'flex-start', gap: '8px',
            padding: '5px 8px', cursor: 'pointer', borderRadius: '6px',
          }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-hover)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
          >
            <input
              type="checkbox"
              checked={!hiddenDocs.has(doc.id)}
              onChange={() => onToggleDoc(doc.id)}
              style={{ marginTop: '2px', flexShrink: 0 }}
            />
            <span style={{
              fontSize: '12.5px',
              color: hiddenDocs.has(doc.id) ? 'var(--faint)' : 'var(--body)',
              lineHeight: 1.4, wordBreak: 'break-word',
            }}>
              {doc.title}
            </span>
          </label>
        ))}
      </div>

      <div style={{ borderTop: '1px solid var(--line-soft)', padding: '12px 16px', flexShrink: 0, background: 'var(--surface-subtle)' }}>
        <div style={{ fontSize: '13px', fontWeight: 650, color: 'var(--ink)', marginBottom: '8px' }}>Clusters</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 10px' }}>
          {clusters.map(cid => (
            <div key={cid} style={{ display: 'flex', alignItems: 'center', gap: '7px', minWidth: 0 }}>
              <div style={{
                width: '10px', height: '10px', flexShrink: 0,
                background: CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length],
                borderRadius: '50%',
              }} />
              <span className="tabular" style={{ fontSize: '12px', color: 'var(--body)', whiteSpace: 'nowrap' }}>
                C{cid} <span className="tw-muted">· {clusterCounts[cid] ?? 0}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
function Embedding3DInner({ isActive }) {
  const { data: raw, isLoading } = useEmbeddings();
  const chunks = useMemo(() => (Array.isArray(raw?.data) ? raw.data : []), [raw]);

  const mountRef     = useRef(null);
  const rendererRef  = useRef(null);
  const css2dRef     = useRef(null);
  const sceneRef     = useRef(null);
  const cameraRef    = useRef(null);
  const frameRef     = useRef(null);
  const pointsRef    = useRef(null);   // main THREE.Points
  const centroidsRef = useRef(null);   // centroid THREE.Points
  const allPositions = useRef([]);     // world-space xyz per visible chunk
  const allChunkMeta = useRef([]);     // metadata per visible chunk (matches allPositions)
  const orbitRef     = useRef({ theta: Math.PI / 4, phi: Math.PI / 3, radius: 3.0 });
  const dragRef      = useRef({ dragging: false, hasDragged: false, lastX: 0, lastY: 0, startX: 0, startY: 0 });
  const tmpV3        = useRef(new THREE.Vector3()); // reused each frame to avoid GC
  const mouseRef     = useRef({ x: -9999, y: -9999 });
  const animateRef   = useRef(false);
  const isActiveRef  = useRef(isActive);
  const lerpRef      = useRef(null);   // camera lerp target

  const [tooltip, setTooltip]         = useState(null);
  const [selectedDocId, setSelectedDocId] = useState(null);
  const [animateMode, setAnimateMode] = useState(false);
  const [hiddenDocs, setHiddenDocs]   = useState(new Set());

  useEffect(() => { isActiveRef.current = isActive; }, [isActive]);
  useEffect(() => { animateRef.current = animateMode; }, [animateMode]);

  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;

  // Derived document list
  const documents = useMemo(() => {
    const seen = new Map();
    for (const c of chunks) {
      if (!seen.has(c.document_id)) seen.set(c.document_id, c.document_title || c.document_id);
    }
    return Array.from(seen.entries()).map(([id, title]) => ({ id, title }));
  }, [chunks]);

  // Derived cluster list
  const clusters = useMemo(() => {
    const s = new Set(chunks.map(c => c.cluster_id));
    return Array.from(s).sort((a, b) => a - b);
  }, [chunks]);

  // Filter callbacks
  const toggleDoc = useCallback(docId => {
    setHiddenDocs(prev => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId); else next.add(docId);
      return next;
    });
    setSelectedDocId(null);
  }, []);

  const selectAll = useCallback(() => {
    setHiddenDocs(new Set());
    setSelectedDocId(null);
  }, []);

  const clearAll = useCallback(() => {
    setHiddenDocs(new Set(documents.map(d => d.id)));
    setSelectedDocId(null);
  }, [documents]);

  // ── Rebuild geometry buffers ──────────────────────────────────────────────
  const rebuildBuffers = useCallback(() => {
    const pts = pointsRef.current;
    const ctr = centroidsRef.current;
    if (!pts || !ctr) return;

    const visible = chunks.filter(c => !hiddenDocs.has(c.document_id));
    // Flat Float32Array — reused by hover loop without per-frame allocation
    const flatPos = new Float32Array(visible.length * 3);
    visible.forEach((c, i) => { flatPos[i*3]=c.x; flatPos[i*3+1]=c.y; flatPos[i*3+2]=c.z; });
    allPositions.current = flatPos;
    allChunkMeta.current = visible;

    // Main point cloud
    const pos = new Float32Array(visible.length * 3);
    const col = new Float32Array(visible.length * 3);

    visible.forEach((c, i) => {
      pos[i * 3]     = c.x;
      pos[i * 3 + 1] = c.y;
      pos[i * 3 + 2] = c.z;

      const base = hexToRGB(CLUSTER_PALETTE[c.cluster_id % CLUSTER_PALETTE.length]);
      let r = base[0], g = base[1], b = base[2];

      if (selectedDocId != null) {
        if (c.document_id === selectedDocId) {
          r = Math.min(1, r * 1.6); g = Math.min(1, g * 1.6); b = Math.min(1, b * 1.6);
        } else {
          r *= 0.25; g *= 0.25; b *= 0.25;
        }
      }

      col[i * 3]     = r;
      col[i * 3 + 1] = g;
      col[i * 3 + 2] = b;
    });

    pts.geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    pts.geometry.setAttribute('color', new THREE.BufferAttribute(col, 3));
    pts.geometry.setDrawRange(0, visible.length);
    pts.geometry.attributes.position.needsUpdate = true;
    pts.geometry.attributes.color.needsUpdate = true;
    pts.geometry.computeBoundingSphere();

    // Cluster centroids from visible points
    const centroidMap = {};
    visible.forEach(c => {
      if (!centroidMap[c.cluster_id]) centroidMap[c.cluster_id] = { sx: 0, sy: 0, sz: 0, n: 0 };
      centroidMap[c.cluster_id].sx += c.x;
      centroidMap[c.cluster_id].sy += c.y;
      centroidMap[c.cluster_id].sz += c.z;
      centroidMap[c.cluster_id].n++;
    });

    const cids = Object.keys(centroidMap).map(Number);
    const cpos = new Float32Array(cids.length * 3);
    const ccol = new Float32Array(cids.length * 3);
    ctr.userData.cids = cids;
    ctr.userData.centroids = {};

    cids.forEach((cid, i) => {
      const m = centroidMap[cid];
      const cx = m.sx / m.n, cy = m.sy / m.n, cz = m.sz / m.n;
      cpos[i * 3]     = cx;
      cpos[i * 3 + 1] = cy;
      cpos[i * 3 + 2] = cz;
      ctr.userData.centroids[cid] = [cx, cy, cz];

      const base = hexToRGB(CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length]);
      ccol[i * 3]     = base[0];
      ccol[i * 3 + 1] = base[1];
      ccol[i * 3 + 2] = base[2];
    });

    ctr.geometry.setAttribute('position', new THREE.BufferAttribute(cpos, 3));
    ctr.geometry.setAttribute('color', new THREE.BufferAttribute(ccol, 3));
    ctr.geometry.setDrawRange(0, cids.length);
    ctr.geometry.attributes.position.needsUpdate = true;
    ctr.geometry.attributes.color.needsUpdate = true;
    ctr.geometry.computeBoundingSphere();

    // Update CSS2D centroid labels
    updateCentroidLabels(centroidMap, cids);
  }, [chunks, hiddenDocs, selectedDocId]);

  // CSS2D centroid label objects, keyed by cluster id
  const centroidLabelObjs = useRef({});

  function updateCentroidLabels(centroidMap, cids) {
    const scene = sceneRef.current;
    if (!scene) return;

    // Remove old labels no longer needed
    Object.keys(centroidLabelObjs.current).forEach(cid => {
      if (!centroidMap[cid]) {
        scene.remove(centroidLabelObjs.current[cid]);
        delete centroidLabelObjs.current[cid];
      }
    });

    cids.forEach(cid => {
      const m = centroidMap[cid];
      const cx = m.sx / m.n, cy = m.sy / m.n, cz = m.sz / m.n;

      if (!centroidLabelObjs.current[cid]) {
        const div = document.createElement('div');
        div.style.cssText = `
          font-family:var(--font-sans);
          font-size:11px;
          font-weight:700;
          color:${CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length]};
          background:var(--surface);
          border:1px solid var(--line);
          border-radius:99px;
          box-shadow:var(--shadow-sm);
          pointer-events:auto;
          cursor:pointer;
          user-select:none;
          padding:1px 7px;
        `;
        div.textContent = `C${cid}`;
        div.title = `Double-click to zoom to cluster ${cid}`;
        div.addEventListener('dblclick', () => zoomToCluster(cid));
        const obj = new CSS2DObject(div);
        scene.add(obj);
        centroidLabelObjs.current[cid] = obj;
      }
      centroidLabelObjs.current[cid].position.set(cx, cy + 0.06, cz);
    });
  }

  function zoomToCluster(cid) {
    const centroid = centroidsRef.current?.userData?.centroids?.[cid];
    if (!centroid) return;
    const [cx, cy, cz] = centroid;
    const targetRadius = 1.5;
    const targetTheta = Math.atan2(cz, cx);
    const targetPhi = Math.PI / 3;
    lerpRef.current = {
      startTheta: orbitRef.current.theta,
      startPhi: orbitRef.current.phi,
      startRadius: orbitRef.current.radius,
      targetTheta, targetPhi, targetRadius,
      startTime: performance.now(),
      duration: 800,
    };
  }

  // ── Scene setup ───────────────────────────────────────────────────────────
  // The canvas mount only exists once embeddings have loaded, so the scene is
  // (re)built when the data becomes ready, not just on first mount. Without
  // this, a cold visit to the page left the 3D view blank until the user
  // navigated away and back.
  const sceneReady = !isLoading && chunks.length > 0;
  useEffect(() => {
    if (isMobile || !sceneReady) return;
    const container = mountRef.current;
    if (!container) return;

    // WebGL renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // CSS2D overlay
    const css2d = new CSS2DRenderer();
    css2d.domElement.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;width:100%;height:100%;';
    container.appendChild(css2d.domElement);
    css2dRef.current = css2d;

    // Scene + camera
    const scene = new THREE.Scene();
    sceneRef.current = scene;
    const camera = new THREE.PerspectiveCamera(60, 1, 0.01, 100);
    cameraRef.current = camera;

    // Axes helper (small, in corner — positioned via scene add at origin)
    const axesHelper = new THREE.AxesHelper(1.4);
    const axisGrey = new THREE.Color('#8a9894');
    axesHelper.setColors(axisGrey, axisGrey, axisGrey);
    axesHelper.material.transparent = true;
    axesHelper.material.opacity = 0.55;
    scene.add(axesHelper);

    // Axis labels (CSS2D)
    const axisLabels = [
      ['PC1', [1.25, 0, 0]], ['PC2', [0, 1.25, 0]], ['PC3', [0, 0, 1.25]],
    ];
    axisLabels.forEach(([text, pos]) => {
      const div = document.createElement('div');
      div.textContent = text;
      div.style.cssText = 'font-family:var(--font-sans);font-size:11px;font-weight:600;color:var(--muted);pointer-events:none;';
      const obj = new CSS2DObject(div);
      obj.position.set(...pos);
      scene.add(obj);
    });

    // Sprites: soft circle for data points, ring for centroids
    const circleSprite = createCircleSprite();
    const ringSprite   = createRingSprite();
    const dpr = Math.min(window.devicePixelRatio, 2);

    // Main point cloud (geometry populated by rebuildBuffers)
    const ptsGeo = new THREE.BufferGeometry();
    ptsGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
    ptsGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(0), 3));
    const ptsMat = new THREE.PointsMaterial({
      vertexColors: true,
      size: 5 * dpr,          // fixed CSS-pixel size — not attenuated
      sizeAttenuation: false,
      map: circleSprite,
      alphaTest: 0.15,
      transparent: true,
      opacity: 0.88,
      depthWrite: false,      // prevents z-fighting between overlapping circles
    });
    const pts = new THREE.Points(ptsGeo, ptsMat);
    scene.add(pts);
    pointsRef.current = pts;

    // Centroid ring markers (larger, ring sprite so they're clearly distinct)
    const ctrGeo = new THREE.BufferGeometry();
    ctrGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
    ctrGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(0), 3));
    const ctrMat = new THREE.PointsMaterial({
      vertexColors: true,
      size: 18 * dpr,
      sizeAttenuation: false,
      map: ringSprite,
      alphaTest: 0.1,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    });
    const ctr = new THREE.Points(ctrGeo, ctrMat);
    ctr.userData = { cids: [], centroids: {} };
    scene.add(ctr);
    centroidsRef.current = ctr;

    // Resize observer
    let w = 0, h = 0;
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect;
      w = r.width; h = r.height;
      renderer.setSize(w, h);
      css2d.setSize(w, h);
      camera.aspect = w / h || 1;
      camera.updateProjectionMatrix();
    });
    ro.observe(container);

    function updateCamera() {
      const { theta, phi, radius } = orbitRef.current;
      camera.position.set(
        radius * Math.sin(phi) * Math.cos(theta),
        radius * Math.cos(phi),
        radius * Math.sin(phi) * Math.sin(theta),
      );
      camera.lookAt(0, 0, 0);
    }

    function animate() {
      frameRef.current = requestAnimationFrame(animate);
      if (!isActiveRef.current) return;

      // Background colour from CSS var (responds to theme changes)
      const bgHex = readCSSVar('--surface-subtle') || '#faf8f3';
      renderer.setClearColor(bgHex);

      // Auto-orbit
      if (animateRef.current) orbitRef.current.theta += 0.002;

      // Camera lerp (zoom to cluster)
      if (lerpRef.current) {
        const lr = lerpRef.current;
        const t = Math.min(1, (performance.now() - lr.startTime) / lr.duration);
        const e = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; // ease in-out quad
        orbitRef.current.theta  = lr.startTheta  + (lr.targetTheta  - lr.startTheta)  * e;
        orbitRef.current.phi    = lr.startPhi    + (lr.targetPhi    - lr.startPhi)    * e;
        orbitRef.current.radius = lr.startRadius + (lr.targetRadius - lr.startRadius) * e;
        if (t >= 1) lerpRef.current = null;
      }

      updateCamera();

      // Hover: screen-space nearest point (12px threshold).
      // Reuses tmpV3 to avoid per-frame Vector3 allocations.
      const positions = allPositions.current;
      const n = positions.length / 3;
      if (n > 0 && w > 0 && h > 0) {
        let nearestIdx = -1;
        let nearestDist = 144; // 12px threshold squared
        const mx = ((mouseRef.current.x + 1) / 2) * w;
        const my = ((-mouseRef.current.y + 1) / 2) * h;
        const v = tmpV3.current;

        for (let i = 0; i < n; i++) {
          v.fromArray(positions, i * 3).project(camera);
          const sx = ((v.x + 1) / 2) * w;
          const sy = ((-v.y + 1) / 2) * h;
          const dx = sx - mx, dy = sy - my;
          const dist2 = dx * dx + dy * dy;
          if (dist2 < nearestDist) { nearestDist = dist2; nearestIdx = i; }
        }

        if (nearestIdx >= 0) {
          const c = allChunkMeta.current[nearestIdx];
          v.fromArray(positions, nearestIdx * 3).project(camera);
          setTooltip({
            x: ((v.x + 1) / 2) * w,
            y: ((-v.y + 1) / 2) * h,
            title: c.document_title,
            cluster: c.cluster_id,
            text: c.chunk_text,
            docId: c.document_id,
          });
        } else {
          setTooltip(null);
        }
      } else {
        setTooltip(null);
      }

      renderer.render(scene, camera);
      css2d.render(scene, camera);
    }

    updateCamera();
    animate();

    // ── Event listeners ─────────────────────────────────────────────────────
    function onMouseMove(e) {
      const rect = container.getBoundingClientRect();
      mouseRef.current.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      if (dragRef.current.dragging) {
        const dx = e.clientX - dragRef.current.lastX;
        const dy = e.clientY - dragRef.current.lastY;
        dragRef.current.lastX = e.clientX;
        dragRef.current.lastY = e.clientY;
        const tdx = e.clientX - dragRef.current.startX;
        const tdy = e.clientY - dragRef.current.startY;
        if (Math.abs(tdx) > 3 || Math.abs(tdy) > 3) dragRef.current.hasDragged = true;
        orbitRef.current.theta -= dx * 0.005;
        orbitRef.current.phi = Math.max(0.1, Math.min(Math.PI - 0.1, orbitRef.current.phi - dy * 0.005));
      }
    }

    function onMouseDown(e) {
      dragRef.current = { dragging: true, hasDragged: false, lastX: e.clientX, lastY: e.clientY, startX: e.clientX, startY: e.clientY };
    }

    function onMouseUp() { dragRef.current.dragging = false; }

    function onWheel(e) {
      e.preventDefault();
      orbitRef.current.radius = Math.max(0.5, Math.min(10, orbitRef.current.radius + e.deltaY * 0.005));
    }

    function onClick(e) {
      if (dragRef.current.hasDragged) return; // orbit drag, not a point click
      const rect = container.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const my = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      // Find the nearest visible point at click position (16px threshold)
      const positions = allPositions.current;
      const cn = positions.length / 3;
      let nearestIdx = -1;
      let nearestDist = 256; // 16px squared
      const cw = rect.width, ch = rect.height;
      const sx0 = ((mx + 1) / 2) * cw;
      const sy0 = ((-my + 1) / 2) * ch;
      const cv = tmpV3.current;

      for (let i = 0; i < cn; i++) {
        cv.fromArray(positions, i * 3).project(cameraRef.current);
        const sx = ((cv.x + 1) / 2) * cw;
        const sy = ((-cv.y + 1) / 2) * ch;
        const dx = sx - sx0, dy = sy - sy0;
        const d2 = dx * dx + dy * dy;
        if (d2 < nearestDist) { nearestDist = d2; nearestIdx = i; }
      }

      if (nearestIdx >= 0) {
        const docId = allChunkMeta.current[nearestIdx].document_id;
        setSelectedDocId(prev => prev === docId ? null : docId);
      } else {
        setSelectedDocId(null);
      }
    }

    container.addEventListener('mousemove', onMouseMove);
    container.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mouseup', onMouseUp);
    container.addEventListener('wheel', onWheel, { passive: false });
    container.addEventListener('click', onClick);

    return () => {
      cancelAnimationFrame(frameRef.current);
      ro.disconnect();
      container.removeEventListener('mousemove', onMouseMove);
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mouseup', onMouseUp);
      container.removeEventListener('wheel', onWheel);
      container.removeEventListener('click', onClick);
      renderer.dispose();
      if (renderer.domElement.parentNode === container) container.removeChild(renderer.domElement);
      if (css2d.domElement.parentNode === container) container.removeChild(css2d.domElement);
    };
  // Runs once the mount exists; rebuildBuffers handles later data updates
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile, sceneReady]);

  // Rebuild buffers whenever data/filters/selection change
  useEffect(() => {
    if (pointsRef.current && centroidsRef.current) rebuildBuffers();
  }, [rebuildBuffers]);

  if (isMobile) {
    return (
      <EmptyState icon="grid" title="3D view is not available on small screens">
        Open the IPFR corpus on a larger display to explore the embedding space.
      </EmptyState>
    );
  }

  if (isLoading) {
    return <div style={{ padding: '24px' }}><Loading>Loading embeddings…</Loading></div>;
  }

  if (chunks.length === 0) {
    return (
      <EmptyState icon="grid" title="No chunk embeddings available">
        Embeddings appear after the IPFR corpus has been ingested.
      </EmptyState>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      {/* ── Canvas area ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', ...CANVAS_BG }}>
        {/* Controls row */}
        <div style={{
          position: 'absolute', top: '12px', left: '12px', zIndex: 5,
          display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap',
        }}>
          <button
            type="button"
            className={`tw-btn is-sm${animateMode ? ' is-primary' : ''}`}
            onClick={() => setAnimateMode(m => !m)}
            aria-pressed={animateMode}
          >
            {animateMode
              ? <><svg viewBox="0 0 24 24" fill="currentColor" stroke="none" style={{ width: 12, height: 12 }}><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg> Pause orbit</>
              : <><Icon name="play" /> Auto-orbit</>}
          </button>
          {selectedDocId && (
            <button type="button" className="tw-btn is-sm" onClick={() => setSelectedDocId(null)}>
              <Icon name="close" /> Clear selection
            </button>
          )}
          <span className="tw-small tw-muted" style={{ background: 'color-mix(in srgb, var(--surface) 80%, transparent)', padding: '3px 8px', borderRadius: '99px' }}>
            Drag to rotate, scroll to zoom, double-click a cluster label to fly to it
          </span>
        </div>

        {/* Three.js canvas mount */}
        <div
          ref={mountRef}
          style={{ width: '100%', height: '100%', cursor: 'grab' }}
        />

        {/* Hover tooltip */}
        {tooltip && (
          <div className="tw-chart-tip" style={{
            position: 'absolute',
            left: Math.min(tooltip.x + 14, (mountRef.current?.clientWidth ?? 800) - 280),
            top: Math.max(tooltip.y - 10, 8),
            zIndex: 6,
            maxWidth: '260px',
          }}>
            <div style={{ fontSize: '13px', color: 'var(--ink)', marginBottom: '2px', fontWeight: 650 }}>
              {tooltip.title}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11.5px', color: 'var(--muted)', marginBottom: '6px' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: CLUSTER_PALETTE[(tooltip.cluster ?? 0) % CLUSTER_PALETTE.length] }} />
              Cluster {tooltip.cluster}
            </div>
            {tooltip.text && (
              <div style={{
                fontSize: '12px',
                color: 'var(--body)',
                lineHeight: 1.5,
                overflow: 'hidden',
                display: '-webkit-box',
                WebkitLineClamp: 4,
                WebkitBoxOrient: 'vertical',
              }}>
                {tooltip.text.slice(0, 150)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Filter sidebar ───────────────────────────────────────────────── */}
      <FilterSidebar
        documents={documents}
        hiddenDocs={hiddenDocs}
        onToggleDoc={toggleDoc}
        onSelectAll={selectAll}
        onClearAll={clearAll}
        clusters={clusters}
        chunks={chunks}
      />
    </div>
  );
}

export default function Embedding3D(props) {
  return (
    <Embedding3DErrorBoundary>
      <Embedding3DInner {...props} />
    </Embedding3DErrorBoundary>
  );
}
