import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import * as THREE from 'three';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { useEmbeddings } from '../hooks/useData.js';

// 12 visually distinct colours — mid-range saturation/lightness so they read
// on both dark and light backgrounds
const CLUSTER_PALETTE = [
  '#4ADE80', // green
  '#F87171', // red
  '#60A5FA', // blue
  '#FBBF24', // amber
  '#A78BFA', // purple
  '#34D399', // emerald
  '#FB923C', // orange
  '#F472B6', // pink
  '#38BDF8', // sky
  '#E879F9', // fuchsia
  '#A3E635', // lime
  '#2DD4BF', // teal
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
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          height: '100%', flexDirection: 'column', gap: '8px',
          color: 'var(--state-error)', fontFamily: 'var(--font-mono)', fontSize: '13px',
        }}>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '22px' }}>3D VIEW ERROR</div>
          {this.state.error?.message}
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
      width: '240px',
      minWidth: '240px',
      display: 'flex',
      flexDirection: 'column',
      borderLeft: '1px solid var(--rule)',
      background: 'var(--bg-secondary)',
      overflow: 'hidden',
    }}>
      {/* Document filter */}
      <div style={{
        padding: '10px 12px 6px',
        borderBottom: '1px solid var(--rule)',
        flexShrink: 0,
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '12px',
          color: 'var(--text-tertiary)', letterSpacing: '0.08em', marginBottom: '8px',
        }}>
          DOCUMENTS
        </div>
        <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
          <button onClick={onSelectAll} style={btnStyle}>SELECT ALL</button>
          <button onClick={onClearAll} style={btnStyle}>CLEAR ALL</button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {documents.map(doc => (
          <label key={doc.id} style={{
            display: 'flex', alignItems: 'flex-start', gap: '8px',
            padding: '4px 12px', cursor: 'pointer',
          }}>
            <input
              type="checkbox"
              checked={!hiddenDocs.has(doc.id)}
              onChange={() => onToggleDoc(doc.id)}
              style={{ marginTop: '2px', flexShrink: 0, accentColor: 'var(--stage-4)' }}
            />
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '12px',
              color: hiddenDocs.has(doc.id) ? 'var(--text-tertiary)' : 'var(--text-secondary)',
              lineHeight: 1.35, wordBreak: 'break-word',
            }}>
              {doc.title}
            </span>
          </label>
        ))}
      </div>

      {/* Cluster legend */}
      <div style={{
        borderTop: '1px solid var(--rule)',
        padding: '10px 12px',
        flexShrink: 0,
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '12px',
          color: 'var(--text-tertiary)', letterSpacing: '0.08em', marginBottom: '8px',
        }}>
          CLUSTER LEGEND
        </div>
        {clusters.map(cid => (
          <div key={cid} style={{
            display: 'flex', alignItems: 'center', gap: '8px',
            marginBottom: '5px',
          }}>
            <div style={{
              width: '12px', height: '12px', flexShrink: 0,
              background: CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length],
              borderRadius: '2px',
            }} />
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '12px',
              color: 'var(--text-secondary)',
            }}>
              C{cid} · {clusterCounts[cid] ?? 0} chunks
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const btnStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '11px',
  letterSpacing: '0.06em',
  color: 'var(--text-secondary)',
  background: 'none',
  border: '1px solid var(--rule-accent)',
  padding: '2px 6px',
  cursor: 'pointer',
  borderRadius: '2px',
};

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
          font-family:'DM Mono',monospace;
          font-size:12px;
          color:${CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length]};
          pointer-events:auto;
          cursor:pointer;
          user-select:none;
          text-shadow:0 0 4px rgba(0,0,0,0.8);
          padding:2px 4px;
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
  useEffect(() => {
    if (isMobile) return;
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
    scene.add(axesHelper);

    // Axis labels (CSS2D)
    const axisLabels = [
      ['PC1', [1.25, 0, 0]], ['PC2', [0, 1.25, 0]], ['PC3', [0, 0, 1.25]],
    ];
    axisLabels.forEach(([text, pos]) => {
      const div = document.createElement('div');
      div.textContent = text;
      div.style.cssText = 'font-family:"DM Mono",monospace;font-size:12px;color:#777777;pointer-events:none;text-shadow:0 0 4px rgba(0,0,0,0.7);';
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
      const bgHex = readCSSVar('--bg-primary') || '#0e0e0d';
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
  // Intentionally run once on mount; rebuildBuffers handles data updates
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile]);

  // Rebuild buffers whenever data/filters/selection change
  useEffect(() => {
    if (pointsRef.current && centroidsRef.current) rebuildBuffers();
  }, [rebuildBuffers]);

  if (isMobile) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--font-display)', fontSize: '22px', color: 'var(--text-tertiary)',
      }}>
        3D VIEW NOT AVAILABLE ON MOBILE
      </div>
    );
  }

  if (isLoading) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--font-mono)', fontSize: '13px', color: 'var(--text-tertiary)',
      }}>
        LOADING EMBEDDINGS…
      </div>
    );
  }

  if (chunks.length === 0) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--font-display)', fontSize: '22px', color: 'var(--text-tertiary)',
      }}>
        NO CHUNK EMBEDDINGS AVAILABLE
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      {/* ── Canvas area ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {/* Controls row */}
        <div style={{
          position: 'absolute', top: '12px', left: '12px', zIndex: 5,
          display: 'flex', gap: '8px',
        }}>
          <button
            onClick={() => setAnimateMode(m => !m)}
            style={{
              background: animateMode ? 'var(--text-primary)' : 'var(--bg-secondary)',
              color: animateMode ? 'var(--bg-primary)' : 'var(--text-secondary)',
              border: '1px solid var(--rule-accent)',
              padding: '4px 10px',
              fontFamily: 'var(--font-mono)',
              fontSize: '12px',
              letterSpacing: '0.06em',
              cursor: 'pointer',
            }}
          >
            {animateMode ? '⏸ AUTO-ORBIT' : '▶ AUTO-ORBIT'}
          </button>
          {selectedDocId && (
            <button
              onClick={() => setSelectedDocId(null)}
              style={{
                background: 'var(--bg-secondary)',
                color: 'var(--state-warn)',
                border: '1px solid var(--state-warn)',
                padding: '4px 10px',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                letterSpacing: '0.06em',
                cursor: 'pointer',
              }}
            >
              CLEAR SELECTION
            </button>
          )}
        </div>

        {/* Three.js canvas mount */}
        <div
          ref={mountRef}
          style={{ width: '100%', height: '100%', cursor: 'grab' }}
        />

        {/* Hover tooltip */}
        {tooltip && (
          <div style={{
            position: 'absolute',
            left: Math.min(tooltip.x + 14, (mountRef.current?.clientWidth ?? 800) - 280),
            top: Math.max(tooltip.y - 10, 8),
            background: 'var(--bg-secondary)',
            border: '1px solid var(--rule-accent)',
            padding: '8px 12px',
            pointerEvents: 'none',
            zIndex: 6,
            maxWidth: '260px',
          }}>
            <div style={{
              fontFamily: 'var(--font-body)', fontSize: '13px',
              color: 'var(--text-primary)', marginBottom: '4px',
              fontWeight: 500,
            }}>
              {tooltip.title}
            </div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '12px',
              color: 'var(--text-tertiary)', marginBottom: '6px',
            }}>
              CLUSTER {tooltip.cluster}
            </div>
            {tooltip.text && (
              <div style={{
                fontFamily: 'var(--font-body)', fontSize: '12px',
                color: 'var(--text-secondary)',
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
