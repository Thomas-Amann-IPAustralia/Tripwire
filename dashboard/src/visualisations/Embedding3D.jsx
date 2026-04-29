import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as THREE from 'three';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { usePage } from '../hooks/useData.js';

// Map cluster index 0–6 to CSS variable names --stage-1 through --stage-6, cluster 6 → stage-6
const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];

function parseCSSColor(varName) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  const c = new THREE.Color(raw || '#5c5a52');
  return c;
}

function PageDetailOverlay({ pageId, onClose }) {
  const { data: raw, isLoading } = usePage(pageId);
  const page = raw?.data ?? raw ?? null;

  return (
    <div style={{
      position: 'absolute', top: 0, right: 0, bottom: 0,
      width: '320px',
      background: 'var(--bg-secondary)',
      borderLeft: '1px solid var(--rule)',
      display: 'flex', flexDirection: 'column',
      zIndex: 10,
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
          PAGE DETAIL
        </span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-tertiary)', fontSize: '16px', padding: '0 4px', lineHeight: 1,
        }}>✕</button>
      </div>

      {isLoading && (
        <div style={{ padding: '16px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>
          Loading…
        </div>
      )}

      {page && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '4px' }}>
              PAGE ID
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)' }}>{page.page_id}</div>
          </div>

          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '4px' }}>
              TITLE
            </div>
            <div style={{ fontFamily: 'var(--font-body)', fontSize: '12px', color: 'var(--text-primary)' }}>{page.title}</div>
          </div>

          <div style={{ display: 'flex', gap: '16px' }}>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em' }}>CLUSTER</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-primary)' }}>{page.cluster ?? '—'}</div>
            </div>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em' }}>ALERTS</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: page.alert_count > 0 ? 'var(--state-alert)' : 'var(--text-primary)' }}>
                {page.alert_count ?? 0}
              </div>
            </div>
          </div>

          {page.keyphrases?.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '6px' }}>
                TOP KEYPHRASES
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                {page.keyphrases.slice(0, 10).map((kp, i) => (
                  <span key={i} style={{
                    fontFamily: 'var(--font-mono)', fontSize: '10px',
                    padding: '2px 6px',
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-secondary)',
                    border: '1px solid var(--rule)',
                  }}>
                    {kp.keyphrase}
                  </span>
                ))}
              </div>
            </div>
          )}

          {page.entities?.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '6px' }}>
                NAMED ENTITIES
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {page.entities.slice(0, 12).map((e, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', minWidth: '48px' }}>
                      {e.entity_type}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)' }}>
                      {e.entity_text}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {page.neighbours?.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '6px' }}>
                TOP 5 GRAPH NEIGHBOURS
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {page.neighbours.slice(0, 5).map((n, i) => (
                  <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)' }}>
                    {n.page_id} <span style={{ color: 'var(--text-tertiary)' }}>({n.weight?.toFixed(3)})</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Embedding3D({ pages = [], isActive }) {
  const mountRef    = useRef(null);
  const sceneRef    = useRef(null);
  const rendererRef = useRef(null);
  const css2dRef    = useRef(null);
  const cameraRef   = useRef(null);
  const frameRef    = useRef(null);
  const meshesRef   = useRef([]);
  const raycasterRef = useRef(new THREE.Raycaster());
  const mouseRef    = useRef(new THREE.Vector2(-9999, -9999));
  const dragRef     = useRef({ dragging: false, lastX: 0, lastY: 0 });
  const orbitRef    = useRef({ theta: Math.PI / 4, phi: Math.PI / 3, radius: 3 });
  const animateRef  = useRef(false);
  const isActiveRef = useRef(isActive);

  const [tooltip, setTooltip]         = useState(null);
  const [selectedPageId, setSelectedPageId] = useState(null);
  const [animateMode, setAnimateMode] = useState(false);
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;

  useEffect(() => { isActiveRef.current = isActive; }, [isActive]);
  useEffect(() => { animateRef.current = animateMode; }, [animateMode]);

  // Build and tear down Three.js scene
  useEffect(() => {
    if (isMobile) return;
    const container = mountRef.current;
    if (!container) return;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setClearColor(0x111110);
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // CSS2D renderer
    const css2d = new CSS2DRenderer();
    css2d.domElement.style.position = 'absolute';
    css2d.domElement.style.top = '0';
    css2d.domElement.style.left = '0';
    css2d.domElement.style.pointerEvents = 'none';
    container.appendChild(css2d.domElement);
    css2dRef.current = css2d;

    // Scene
    const scene = new THREE.Scene();
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(60, 1, 0.01, 100);
    cameraRef.current = camera;

    // Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.4));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(1.5, 2, 1);
    scene.add(dirLight);

    // Ground grid
    const ruleBg = getComputedStyle(document.documentElement).getPropertyValue('--rule').trim() || '#2e2e28';
    const grid = new THREE.GridHelper(2.4, 20, ruleBg, ruleBg);
    grid.position.y = -1.2;
    scene.add(grid);

    // Axes
    const tertiary = parseCSSColor('--text-tertiary');
    const axisColour = tertiary.getHex();
    const axesDef = [
      [[-1.2, 0, 0], [1.2, 0, 0]],
      [[0, -1.2, 0], [0, 1.2, 0]],
      [[0, 0, -1.2], [0, 0, 1.2]],
    ];
    axesDef.forEach(([a, b]) => {
      const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]);
      scene.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: axisColour })));
    });

    // Axis labels (CSS2D)
    const labels = [
      ['PC1+', [1.0, 0, 0]], ['PC1-', [-1.0, 0, 0]],
      ['PC2+', [0, 1.0, 0]], ['PC2-', [0, -1.0, 0]],
      ['PC3+', [0, 0, 1.0]], ['PC3-', [0, 0, -1.0]],
    ];
    labels.forEach(([text, pos]) => {
      const div = document.createElement('div');
      div.textContent = text;
      div.style.cssText = 'font-family:"DM Mono",monospace;font-size:9px;color:#5c5a52;pointer-events:none;';
      const obj = new CSS2DObject(div);
      obj.position.set(...pos);
      scene.add(obj);
    });

    // Particle background
    const particleCount = 300;
    const pPos = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount * 3; i++) pPos[i] = (Math.random() - 0.5) * 4;
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
    const tertHex = `#${tertiary.getHexString()}`;
    scene.add(new THREE.Points(pGeo, new THREE.PointsMaterial({
      color: axisColour, size: 0.5, transparent: true, opacity: 0.12,
    })));

    // Page spheres
    const validPages = pages.filter(p => p.embedding_3d && p.embedding_3d.length === 3);
    const maxAlerts = Math.max(...validPages.map(p => p.alert_count ?? 0), 1);
    const meshes = [];

    validPages.forEach(page => {
      const cluster = page.cluster ?? 0;
      const stageIdx = CLUSTER_STAGE[cluster % CLUSTER_STAGE.length];
      const clusterColor = parseCSSColor(`--stage-${stageIdx}`);
      const emissiveIntensity = ((page.alert_count ?? 0) / maxAlerts) * 0.8;

      const geo  = new THREE.SphereGeometry(0.05, 12, 12);
      const mat  = new THREE.MeshStandardMaterial({
        color: clusterColor,
        emissive: clusterColor,
        emissiveIntensity,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...page.embedding_3d);
      mesh.userData = { page };
      scene.add(mesh);
      meshes.push(mesh);
    });
    meshesRef.current = meshes;

    // Resize observer
    let w = 0, h = 0;
    const ro = new ResizeObserver(entries => {
      const entry = entries[0];
      w = entry.contentRect.width;
      h = entry.contentRect.height;
      renderer.setSize(w, h);
      css2d.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    ro.observe(container);

    // Pointer helpers
    function updateCamera() {
      const { theta, phi, radius } = orbitRef.current;
      camera.position.set(
        radius * Math.sin(phi) * Math.cos(theta),
        radius * Math.cos(phi),
        radius * Math.sin(phi) * Math.sin(theta),
      );
      camera.lookAt(0, 0, 0);
    }

    // Animation loop
    function animate() {
      frameRef.current = requestAnimationFrame(animate);
      if (!isActiveRef.current) return;

      if (animateRef.current) {
        orbitRef.current.theta += 0.002;
      }
      updateCamera();

      // Hover raycasting
      raycasterRef.current.setFromCamera(mouseRef.current, camera);
      const intersects = raycasterRef.current.intersectObjects(meshesRef.current);
      if (intersects.length > 0) {
        const hit = intersects[0].object;
        const p = hit.userData.page;
        const pos = hit.position.clone().project(camera);
        const rect = container.getBoundingClientRect();
        setTooltip({
          x: ((pos.x + 1) / 2) * rect.width,
          y: ((-pos.y + 1) / 2) * rect.height,
          page_id: p.page_id,
          title: p.title,
          cluster: p.cluster,
          alert_count: p.alert_count ?? 0,
        });
      } else {
        setTooltip(null);
      }

      renderer.render(scene, camera);
      css2d.render(scene, camera);
    }
    updateCamera();
    animate();

    // Event listeners
    function onMouseMove(e) {
      const rect = container.getBoundingClientRect();
      mouseRef.current.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      if (dragRef.current.dragging) {
        const dx = e.clientX - dragRef.current.lastX;
        const dy = e.clientY - dragRef.current.lastY;
        dragRef.current.lastX = e.clientX;
        dragRef.current.lastY = e.clientY;
        orbitRef.current.theta -= dx * 0.005;
        orbitRef.current.phi   = Math.max(0.1, Math.min(Math.PI - 0.1, orbitRef.current.phi - dy * 0.005));
      }
    }

    function onMouseDown(e) {
      dragRef.current.dragging = true;
      dragRef.current.lastX = e.clientX;
      dragRef.current.lastY = e.clientY;
    }

    function onMouseUp() {
      dragRef.current.dragging = false;
    }

    function onWheel(e) {
      e.preventDefault();
      orbitRef.current.radius = Math.max(0.5, Math.min(8, orbitRef.current.radius + e.deltaY * 0.005));
    }

    function onClick(e) {
      const rect = container.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(x, y), camera);
      const hits = ray.intersectObjects(meshesRef.current);
      if (hits.length > 0) {
        setSelectedPageId(hits[0].object.userData.page.page_id);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, isMobile]);

  if (isMobile) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--font-display)', fontSize: '18px', color: 'var(--text-tertiary)',
      }}>
        3D VIEW NOT AVAILABLE ON MOBILE
      </div>
    );
  }

  return (
    <div style={{ height: '100%', position: 'relative', overflow: 'hidden' }}>
      {/* Animate toggle */}
      <div style={{ position: 'absolute', top: '12px', left: '12px', zIndex: 5 }}>
        <button
          onClick={() => setAnimateMode(m => !m)}
          style={{
            background: animateMode ? 'var(--text-primary)' : 'var(--bg-secondary)',
            color: animateMode ? 'var(--bg-primary)' : 'var(--text-secondary)',
            border: '1px solid var(--rule-accent)',
            padding: '4px 10px',
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            letterSpacing: '0.06em',
            cursor: 'pointer',
          }}
        >
          {animateMode ? '⏸ AUTO-ORBIT' : '▶ AUTO-ORBIT'}
        </button>
      </div>

      {/* Canvas mount */}
      <div ref={mountRef} style={{ width: '100%', height: '100%', cursor: 'grab' }} />

      {/* Hover tooltip */}
      {tooltip && (
        <div style={{
          position: 'absolute',
          left: tooltip.x + 12,
          top: tooltip.y - 8,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '6px 10px',
          pointerEvents: 'none',
          zIndex: 6,
          minWidth: '160px',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-primary)', marginBottom: '2px' }}>
            {tooltip.page_id}
          </div>
          <div style={{ fontFamily: 'var(--font-body)', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            {tooltip.title}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
            CLUSTER {tooltip.cluster} · {tooltip.alert_count} ALERTS
          </div>
        </div>
      )}

      {/* Page detail overlay */}
      {selectedPageId && (
        <PageDetailOverlay pageId={selectedPageId} onClose={() => setSelectedPageId(null)} />
      )}
    </div>
  );
}
