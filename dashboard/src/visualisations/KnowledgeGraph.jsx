import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { usePage } from '../hooks/useData.js';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];
const EDGE_TYPES = ['embedding_similarity', 'entity_overlap', 'internal_link'];
const EDGE_CSS_VARS = {
  embedding_similarity: '--stage-4',
  entity_overlap:       '--stage-3',
  internal_link:        '--text-tertiary',
};
const EDGE_LABELS = {
  embedding_similarity: 'EMBED SIM',
  entity_overlap:       'ENTITY OVERLAP',
  internal_link:        'INTERNAL LINK',
};

function getCSSColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || '#5c5a52';
}

function PageDetailPanel({ pageId, onClose }) {
  const { data: raw, isLoading } = usePage(pageId);
  const page = raw?.data ?? raw ?? null;

  return (
    <div style={{
      position: 'absolute', top: 0, right: 0, bottom: 0,
      width: '300px',
      background: 'var(--bg-secondary)',
      borderLeft: '1px solid var(--rule)',
      zIndex: 10,
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        padding: '10px 14px',
        borderBottom: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
          PAGE DETAIL
        </span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-tertiary)', fontSize: '16px', lineHeight: 1, padding: '0 2px',
        }}>✕</button>
      </div>

      {isLoading && (
        <div style={{ padding: '16px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-tertiary)' }}>Loading…</div>
      )}
      {page && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '2px' }}>ID</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)' }}>{page.page_id}</div>
          </div>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '2px' }}>TITLE</div>
            <div style={{ fontFamily: 'var(--font-body)', fontSize: '12px', color: 'var(--text-primary)' }}>{page.title}</div>
          </div>
          <div style={{ display: 'flex', gap: '16px' }}>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em' }}>CLUSTER</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-primary)' }}>{page.cluster ?? '—'}</div>
            </div>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em' }}>ALERTS</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: page.alert_count > 0 ? 'var(--state-alert)' : 'var(--text-primary)' }}>
                {page.alert_count ?? 0}
              </div>
            </div>
          </div>
          {page.keyphrases?.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '4px' }}>
                KEYPHRASES
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                {page.keyphrases.slice(0, 10).map((kp, i) => (
                  <span key={i} style={{
                    fontFamily: 'var(--font-mono)', fontSize: '9px',
                    padding: '1px 5px', background: 'var(--bg-tertiary)',
                    color: 'var(--text-secondary)', border: '1px solid var(--rule)',
                  }}>{kp.keyphrase}</span>
                ))}
              </div>
            </div>
          )}
          {page.neighbours?.length > 0 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: '4px' }}>
                GRAPH NEIGHBOURS
              </div>
              {page.neighbours.slice(0, 5).map((n, i) => (
                <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', marginBottom: '2px' }}>
                  {n.page_id} <span style={{ color: 'var(--text-tertiary)' }}>({(n.weight ?? 0).toFixed(3)})</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function KnowledgeGraph({ nodes = [], edges = [], isActive }) {
  const svgRef      = useRef(null);
  const simRef      = useRef(null);
  const resizeRef   = useRef(null);
  const [edgeVisible, setEdgeVisible] = useState({ embedding_similarity: true, entity_overlap: true, internal_link: true });
  const [selectedPageId, setSelectedPageId] = useState(null);

  const toggleEdgeType = useCallback(type => {
    setEdgeVisible(prev => ({ ...prev, [type]: !prev[type] }));
  }, []);

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

    // Filter edges by visibility
    const visibleEdges = edges.filter(e => edgeVisible[e.edge_type]);
    const nodeIds = new Set(nodes.map(n => n.page_id));

    // Build link/node arrays for simulation (clone to avoid mutation)
    const simNodes = nodes.map(n => ({ ...n, id: n.page_id }));
    const nodeMap = new Map(simNodes.map(n => [n.id, n]));
    const simLinks = visibleEdges
      .filter(e => nodeMap.has(e.source_page_id) && nodeMap.has(e.target_page_id))
      .map(e => ({ ...e, source: e.source_page_id, target: e.target_page_id }));

    // Draw edges
    const linkG = root.append('g').attr('class', 'links');
    const linkSel = linkG.selectAll('line')
      .data(simLinks)
      .join('line')
      .attr('stroke', d => getCSSColor(EDGE_CSS_VARS[d.edge_type] || '--text-tertiary'))
      .attr('stroke-width', d => 0.5 + (d.weight ?? 0) * 2.5)
      .attr('stroke-opacity', 0.5);

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
      .attr('stroke', getCSSColor('--state-alert'))
      .style('animation', 'graphNodePulse 2s ease-out infinite');

    // Main circles
    nodeSel.append('circle')
      .attr('r', d => 6 + ((d.alert_count ?? 0) / maxAlerts) * 14)
      .attr('fill', d => clusterColor(d.cluster))
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 1.5);

    // Labels (hidden by default)
    nodeSel.append('text')
      .attr('class', 'node-label')
      .attr('display', 'none')
      .attr('dy', d => -(6 + ((d.alert_count ?? 0) / maxAlerts) * 14 + 4))
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text-secondary)')
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .style('pointer-events', 'none')
      .text(d => d.page_id);

    // Hover
    nodeSel
      .on('mouseover', function(event, d) {
        d3.select(this).select('circle:not(.pulse-ring)').attr('stroke', 'var(--text-primary)').attr('stroke-width', 2);
        d3.select(this).select('.node-label').attr('display', 'block');
      })
      .on('mouseout', function(event, d) {
        d3.select(this).select('circle:not(.pulse-ring)').attr('stroke', 'var(--bg-primary)').attr('stroke-width', 1.5);
        const currentK = d3.zoomTransform(svgRef.current).k;
        if (currentK <= 1.5) d3.select(this).select('.node-label').attr('display', 'none');
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        setSelectedPageId(d.page_id);
      });

    // D3 drag
    const drag = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      });
    nodeSel.call(drag);

    // Simulation
    const sim = d3.forceSimulation(simNodes)
      .force('link', d3.forceLink(simLinks).id(d => d.id).distance(80).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => 6 + ((d.alert_count ?? 0) / maxAlerts) * 14 + 6))
      .on('tick', () => {
        linkSel
          .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      });

    simRef.current = sim;
    if (!isActive) sim.stop();

    // Resize
    function onResize() {
      const { width: w, height: h } = container.getBoundingClientRect();
      svg.attr('width', w).attr('height', h);
      sim.force('center', d3.forceCenter(w / 2, h / 2));
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
        display: 'flex', gap: '8px',
        padding: '8px 12px',
        borderBottom: '1px solid var(--rule)',
        flexShrink: 0,
      }}>
        {EDGE_TYPES.map(type => (
          <button
            key={type}
            onClick={() => toggleEdgeType(type)}
            style={{
              background: edgeVisible[type] ? getCSSColor(EDGE_CSS_VARS[type]) : 'transparent',
              color: edgeVisible[type] ? 'var(--bg-primary)' : getCSSColor(EDGE_CSS_VARS[type]),
              border: `1px solid ${getCSSColor(EDGE_CSS_VARS[type])}`,
              padding: '3px 10px',
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              letterSpacing: '0.06em',
              cursor: 'pointer',
            }}
          >
            {EDGE_LABELS[type]}
          </button>
        ))}
      </div>

      {/* SVG */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <svg ref={svgRef} style={{ width: '100%', height: '100%', display: 'block', background: 'var(--bg-primary)' }} />
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
