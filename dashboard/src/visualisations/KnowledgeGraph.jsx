import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import * as d3 from 'd3';
import { useQueryClient } from '@tanstack/react-query';
import { usePage, acknowledgePageAlerts } from '../hooks/useData.js';
import { Icon, Badge, Loading, Callout } from '../components/ui.jsx';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];
const EDGE_TYPES = ['embedding_similarity', 'entity_overlap', 'internal_link'];
// Colours are passed to SVG as var() references so the graph follows the
// light / dark theme live, without rebuilding the simulation.
const EDGE_CSS_VARS = {
  embedding_similarity: '--stage-4',
  entity_overlap:       '--stage-3',
  internal_link:        '--muted',
};
const EDGE_LABELS = {
  embedding_similarity: 'Embedding similarity',
  entity_overlap:       'Entity overlap',
  internal_link:        'Internal link',
};

function getCSSColor(varName) {
  return `var(${varName})`;
}

// Shared dotted "canvas" backdrop for the corpus visualisations.
export const CANVAS_BG = {
  backgroundColor: 'var(--surface-subtle)',
  backgroundImage: 'radial-gradient(var(--line) 1px, transparent 1px)',
  backgroundSize: '22px 22px',
};

export function PageDetailPanel({ pageId, onClose }) {
  const { data: raw, isLoading } = usePage(pageId);
  const page = raw?.data ?? raw ?? null;
  const queryClient = useQueryClient();
  const [ackBusy, setAckBusy] = useState(false);
  const [ackError, setAckError] = useState(null);

  // "Mark reviewed" clears the page's outstanding CHANGE_REQUIRED flags in
  // every view that surfaces them (graph pulse, 3D glow, content map);
  // alerts generated afterwards re-flag the page automatically.
  const toggleAck = useCallback(async (undo) => {
    if (!pageId) return;
    setAckBusy(true);
    setAckError(null);
    try {
      await acknowledgePageAlerts(pageId, { undo });
      await queryClient.invalidateQueries({ queryKey: ['pages'] });
      await queryClient.invalidateQueries({ queryKey: ['graph'] });
      await queryClient.invalidateQueries({ queryKey: ['embeddings'] });
    } catch (err) {
      setAckError(err.message);
    } finally {
      setAckBusy(false);
    }
  }, [pageId, queryClient]);

  const label = text => <div className="tw-small tw-muted" style={{ marginBottom: '3px' }}>{text}</div>;

  return (
    <div style={{
      position: 'absolute', top: '12px', right: '12px', bottom: '12px',
      width: '320px',
      background: 'var(--surface)',
      border: '1px solid var(--line)',
      borderRadius: 'var(--radius-lg)',
      boxShadow: 'var(--shadow-lg)',
      zIndex: 10,
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '14px 16px',
        borderBottom: '1px solid var(--line-soft)',
        background: 'var(--surface-subtle)',
        display: 'flex', alignItems: 'flex-start', gap: '10px',
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="tw-overline">IPFR page</div>
          <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 700, fontSize: '18px', lineHeight: 1.25, color: 'var(--ink)', marginTop: '4px' }}>
            {page?.title ?? (isLoading ? 'Loading…' : pageId)}
          </div>
        </div>
        <button type="button" className="tw-icon-btn" onClick={onClose} aria-label="Close page detail" style={{ width: 28, height: 28 }}>
          <Icon name="close" />
        </button>
      </div>

      {isLoading && <div style={{ padding: '4px 16px' }}><Loading /></div>}
      {page && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div>
            {label('Page ID')}
            <div className="tw-code" style={{ fontSize: '12px', color: 'var(--ink)' }}>{page.page_id}</div>
          </div>
          <div className="tw-kv-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
            <div className="tw-kv"><div className="tw-kv-label">Cluster</div><div className="tw-kv-value" style={{ fontSize: '15px' }}>{page.cluster ?? '—'}</div></div>
            <div className="tw-kv"><div className="tw-kv-label">Outstanding</div><div className="tw-kv-value" style={{ fontSize: '15px', color: page.alert_count > 0 ? 'var(--alert)' : undefined }}>{page.alert_count ?? 0}</div></div>
            <div className="tw-kv"><div className="tw-kv-label">All time</div><div className="tw-kv-value" style={{ fontSize: '15px' }}>{page.alert_count_total ?? page.alert_count ?? 0}</div></div>
          </div>

          {(page.alert_count ?? 0) > 0 && (
            <button
              type="button"
              className="tw-btn is-primary"
              disabled={ackBusy}
              onClick={() => toggleAck(false)}
              style={{ cursor: ackBusy ? 'wait' : undefined }}
            >
              <Icon name="check" />
              {ackBusy ? 'Saving…' : 'Mark as reviewed and clear flag'}
            </button>
          )}
          {(page.alert_count ?? 0) === 0 && page.acknowledged_at && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <Badge tone="ok" dot>
                Reviewed {new Date(page.acknowledged_at).toLocaleDateString('en-AU', { day: '2-digit', month: 'short', year: 'numeric' })}
              </Badge>
              <button type="button" className="tw-link" disabled={ackBusy} onClick={() => toggleAck(true)} style={{ cursor: ackBusy ? 'wait' : undefined }}>
                Undo review
              </button>
            </div>
          )}
          {ackError && <Callout tone="error" icon="alert">{ackError}</Callout>}
          {page.keyphrases?.length > 0 && (
            <div>
              {label('Keyphrases')}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
                {page.keyphrases.slice(0, 10).map((kp, i) => (
                  <Badge key={i} tone="muted" style={{ fontWeight: 500 }}>{kp.keyphrase}</Badge>
                ))}
              </div>
            </div>
          )}
          {page.neighbours?.length > 0 && (
            <div>
              {label('Graph neighbours')}
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {page.neighbours.slice(0, 5).map((n, i) => (
                  <li key={i} title={n.page_id} style={{
                    display: 'flex', justifyContent: 'space-between', gap: '10px',
                    fontSize: '12.5px', color: 'var(--body)', padding: '6px 0',
                    borderBottom: i < Math.min(5, page.neighbours.length) - 1 ? '1px solid var(--line-soft)' : 'none',
                  }}>
                    <span style={{ minWidth: 0 }}>{n.title ?? n.page_id}</span>
                    <span className="tabular tw-muted" style={{ flexShrink: 0 }}>{(n.weight ?? 0).toFixed(3)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function KnowledgeGraph({ nodes = [], edges = [], isActive }) {
  const edgeCounts = useMemo(() => {
    const counts = { embedding_similarity: 0, entity_overlap: 0, internal_link: 0 };
    for (const e of edges) {
      if (e.edge_type in counts) counts[e.edge_type] += 1;
    }
    return counts;
  }, [edges]);

  const svgRef      = useRef(null);
  const simRef      = useRef(null);
  const zoomRef     = useRef(null);
  const resizeRef   = useRef(null);
  const [edgeVisible, setEdgeVisible] = useState({ embedding_similarity: true, entity_overlap: true, internal_link: true });
  const [selectedPageId, setSelectedPageId] = useState(null);
  const [pendingHighlight, setPendingHighlight] = useState(null);

  const toggleEdgeType = useCallback(type => {
    setEdgeVisible(prev => ({ ...prev, [type]: !prev[type] }));
  }, []);

  // Listen for OBSERVE → CORPUS graph node highlight
  useEffect(() => {
    const handler = (e) => setPendingHighlight(e.detail);
    window.addEventListener('tripwire:highlight-graph-node', handler);
    return () => window.removeEventListener('tripwire:highlight-graph-node', handler);
  }, []);

  // When the tab becomes active and there's a pending highlight, pan & pulse
  useEffect(() => {
    if (!isActive || !pendingHighlight || !simRef.current || !svgRef.current || !zoomRef.current) return;

    const pageId = pendingHighlight;
    setPendingHighlight(null);

    const simNodes = simRef.current.nodes();
    const nodeData = simNodes.find(n => n.page_id === pageId);
    if (!nodeData) return;

    const svg = d3.select(svgRef.current);
    const container = svgRef.current.parentElement;
    if (!container) return;
    const { width, height } = container.getBoundingClientRect();

    // Pan the viewport to centre on the node
    svg.transition().duration(600).call(
      zoomRef.current.transform,
      d3.zoomIdentity.translate(width / 2 - nodeData.x, height / 2 - nodeData.y)
    );

    // Add a temporary alert-style pulse ring to that node
    const nodeGroup = svg.selectAll('.node-group').filter(d => d.page_id === pageId);
    if (!nodeGroup.empty()) {
      const r = 6 + ((nodeData.alert_count ?? 0) / Math.max(...simNodes.map(n => n.alert_count ?? 0), 1)) * 14;
      const ring = nodeGroup.append('circle')
        .attr('class', 'highlight-ring')
        .attr('r', r + 4)
        .attr('fill', 'none')
        .attr('stroke', getCSSColor('--ochre-ink'))
        .attr('stroke-width', 2.5)
        .style('animation', 'graphNodePulse 1s ease-out infinite')
        .style('transform-box', 'fill-box')
        .style('transform-origin', 'center');
      setTimeout(() => ring.remove(), 3000);
    }
  }, [isActive, pendingHighlight]);

  useEffect(() => {
    if (simRef.current) {
      if (isActive) simRef.current.restart();
      else simRef.current.stop();
    }
  }, [isActive]);

  useEffect(() => {
    const container = svgRef.current?.parentElement;
    if (!container || !nodes.length) return;

    const maxAlerts = Math.max(...nodes.map(n => n.alert_count ?? 0), 1);

    // Cluster → colour
    function clusterColor(cluster) {
      const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
      return getCSSColor(`--stage-${stageIdx}`);
    }

    // Stop previous simulation
    if (simRef.current) simRef.current.stop();

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const { width, height } = container.getBoundingClientRect();
    svg.attr('width', width).attr('height', height);

    const root = svg.append('g').attr('class', 'root');

    // Zoom
    const zoom = d3.zoom().scaleExtent([0.2, 8]).on('zoom', e => {
      root.attr('transform', e.transform);
      // Show labels when zoomed in enough
      root.selectAll('.node-label').attr('display', e.transform.k > 1.5 ? 'block' : 'none');
    });
    svg.call(zoom);
    zoomRef.current = zoom;

    // Filter edges by visibility
    const visibleEdges = edges.filter(e => edgeVisible[e.edge_type]);
    const nodeIds = new Set(nodes.map(n => n.page_id));

    // Build link/node arrays for simulation (clone to avoid mutation).
    // Seed positions in a phyllotaxis around the viewport centre — d3's
    // default initialisation centres on (0,0), and without forceCenter the
    // weak x/y tethers won't pull the layout on-screen before alpha decays.
    const simNodes = nodes.map((n, i) => {
      const angle = i * 2.399963;
      const radius = 16 * Math.sqrt(i);
      return {
        ...n,
        id: n.page_id,
        x: width / 2 + radius * Math.cos(angle),
        y: height / 2 + radius * Math.sin(angle),
      };
    });
    const nodeMap = new Map(simNodes.map(n => [n.id, n]));
    const simLinks = visibleEdges
      .filter(e => nodeMap.has(e.source_page_id) && nodeMap.has(e.target_page_id))
      .map(e => ({ ...e, source: e.source_page_id, target: e.target_page_id }));

    // Draw edges
    const linkG = root.append('g').attr('class', 'links');
    const linkSel = linkG.selectAll('line')
      .data(simLinks)
      .join('line')
      .attr('stroke', d => getCSSColor(EDGE_CSS_VARS[d.edge_type] || '--muted'))
      .attr('stroke-width', d => 0.5 + (d.weight ?? 0) * 2.5)
      .attr('stroke-opacity', 0.45);

    // Draw nodes
    const nodeG = root.append('g').attr('class', 'nodes');
    const nodeSel = nodeG.selectAll('g')
      .data(simNodes)
      .join('g')
      .attr('class', 'node-group')
      .style('cursor', 'pointer');

    // Alert pulse rings
    nodeSel.filter(d => (d.alert_count ?? 0) > 0)
      .append('circle')
      .attr('class', 'pulse-ring')
      .attr('r', d => 6 + ((d.alert_count ?? 0) / maxAlerts) * 14)
      .attr('fill', 'none')
      .attr('stroke', getCSSColor('--alert'))
      .attr('stroke-width', 1.5)
      .style('animation', 'graphNodePulse 2s ease-out infinite');

    // Main circles
    nodeSel.append('circle')
      .attr('r', d => 6 + ((d.alert_count ?? 0) / maxAlerts) * 14)
      .attr('fill', d => clusterColor(d.cluster))
      .attr('stroke', 'var(--surface)')
      .attr('stroke-width', 2);

    // Labels (hidden by default)
    nodeSel.append('text')
      .attr('class', 'node-label')
      .attr('display', 'none')
      .attr('dy', d => -(6 + ((d.alert_count ?? 0) / maxAlerts) * 14 + 4))
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--ink)')
      .attr('stroke', 'var(--surface-subtle)')
      .attr('stroke-width', 3)
      .attr('paint-order', 'stroke')
      .style('font-family', 'var(--font-sans)')
      .style('font-size', '11px')
      .style('font-weight', '600')
      .style('pointer-events', 'none')
      .text(d => {
        const t = d.title || d.page_id;
        return t.length > 42 ? `${t.slice(0, 40)}…` : t;
      });

    // Hover
    nodeSel
      .on('mouseover', function(event, d) {
        d3.select(this).select('circle:not(.pulse-ring)').attr('stroke', 'var(--ink)').attr('stroke-width', 2.5);
        d3.select(this).select('.node-label').attr('display', 'block');
      })
      .on('mouseout', function(event, d) {
        d3.select(this).select('circle:not(.pulse-ring)').attr('stroke', 'var(--surface)').attr('stroke-width', 2);
        const currentK = d3.zoomTransform(svgRef.current).k;
        if (currentK <= 1.5) d3.select(this).select('.node-label').attr('display', 'none');
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        setSelectedPageId(d.page_id);
      });

    // D3 drag — a low alphaTarget keeps the reheat gentle so dragging one
    // node nudges its neighbourhood instead of re-energising the whole layout.
    const drag = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.08).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      });
    nodeSel.call(drag);

    // Simulation. Charge repulsion is capped in range and strength, and every
    // node is individually tethered to the viewport centre with weak x/y
    // forces — otherwise unconnected nodes (no link force holding them) are
    // repelled indefinitely and fly off-screen whenever the user interacts.
    const sim = d3.forceSimulation(simNodes)
      .force('link', d3.forceLink(simLinks).id(d => d.id).distance(80).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-40).distanceMax(280))
      .force('x', d3.forceX(width / 2).strength(0.05))
      .force('y', d3.forceY(height / 2).strength(0.05))
      .force('collision', d3.forceCollide().radius(d => 6 + ((d.alert_count ?? 0) / maxAlerts) * 14 + 6))
      .velocityDecay(0.6)
      .on('tick', () => {
        linkSel
          .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      });

    simRef.current = sim;
    if (!isActive) sim.stop();

    // Resize
    let prevCx = width / 2;
    let prevCy = height / 2;
    function onResize() {
      const { width: w, height: h } = container.getBoundingClientRect();
      if (!w || !h) return; // hidden tab — keep the last real geometry
      svg.attr('width', w).attr('height', h);
      // Translate the whole layout to the new centre. The tethers are too
      // weak to migrate it themselves — critically so on first reveal, when
      // the initial mount measured a display:none container as 0×0.
      const dx = w / 2 - prevCx;
      const dy = h / 2 - prevCy;
      prevCx = w / 2;
      prevCy = h / 2;
      for (const n of simNodes) {
        n.x += dx; n.y += dy;
        if (n.fx != null) n.fx += dx;
        if (n.fy != null) n.fy += dy;
      }
      sim.force('x', d3.forceX(w / 2).strength(0.05));
      sim.force('y', d3.forceY(h / 2).strength(0.05));
      sim.alpha(0.3).restart();
    }
    const ro = new ResizeObserver(onResize);
    ro.observe(container);
    resizeRef.current = ro;

    return () => {
      sim.stop();
      ro.disconnect();
      svg.selectAll('*').remove();
    };
  }, [nodes, edges, edgeVisible]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
      {/* Edge type toggles */}
      <div style={{
        display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap',
        padding: '10px 16px',
        borderBottom: '1px solid var(--line-soft)',
        background: 'var(--surface)',
        flexShrink: 0,
      }}>
        <span className="tw-small tw-muted" style={{ marginRight: '4px' }}>Edges</span>
        {EDGE_TYPES.map(type => {
          const count = edgeCounts[type] ?? 0;
          const empty = count === 0;
          // An edge type with zero rows in graph_edges isn't "toggled off":
          // there is nothing to draw. Internal-link extraction is implemented
          // in the ingestion pipeline; edges appear here after the next corpus
          // ingestion run that reaches the live site, so say that honestly
          // instead of a live-looking toggle that does nothing.
          const title = empty
            ? (type === 'internal_link'
                ? 'No internal-link edges yet; populated on the next corpus ingestion run'
                : 'No edges of this type in the corpus')
            : `${count} edge${count !== 1 ? 's' : ''}`;
          const on = !empty && edgeVisible[type];
          return (
            <button
              key={type}
              type="button"
              className={`tw-chip${on ? ' active' : ''}`}
              aria-pressed={on}
              onClick={() => !empty && toggleEdgeType(type)}
              disabled={empty}
              title={title}
              style={{ borderStyle: empty ? 'dashed' : 'solid' }}
            >
              <span style={{ width: 14, height: 3, borderRadius: 2, background: getCSSColor(EDGE_CSS_VARS[type]), opacity: on ? 1 : 0.4 }} />
              {EDGE_LABELS[type]}
              <span className="tabular" style={{ color: 'var(--muted)', fontWeight: 500 }}>{empty ? 'none yet' : count.toLocaleString()}</span>
            </button>
          );
        })}
        <span className="tw-small tw-faint" style={{ marginLeft: 'auto' }}>Scroll to zoom, drag to pan; node size shows outstanding alerts</span>
      </div>

      {/* SVG */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', ...CANVAS_BG }}>
        <svg ref={svgRef} style={{ width: '100%', height: '100%', display: 'block' }} />
        {selectedPageId && (
          <PageDetailPanel pageId={selectedPageId} onClose={() => setSelectedPageId(null)} />
        )}
      </div>

      <style>{`
        @keyframes graphNodePulse {
          0%   { transform: scale(1);   opacity: 0.8; }
          100% { transform: scale(2.5); opacity: 0; }
        }
        .pulse-ring {
          transform-box: fill-box;
          transform-origin: center;
          animation: graphNodePulse 2s ease-out infinite;
        }
      `}</style>
    </div>
  );
}
