import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { PageDetailPanel } from './KnowledgeGraph.jsx';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];

function getCSSColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || '#5c5a52';
}

function clusterColor(cluster) {
  const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
  return getCSSColor(`--stage-${stageIdx}`);
}

// Corpus coverage treemap: cluster → page hierarchy.
//   cell size   = chunk count (how much content the page carries)
//   cell colour = topic cluster
//   ⚑ border    = outstanding CHANGE_REQUIRED alerts
//   hatching    = pages never flagged by any alert (via toggle)
export default function ContentMap({ pages = [], isActive }) {
  const containerRef = useRef(null);
  const svgRef       = useRef(null);
  const [selectedPageId, setSelectedPageId] = useState(null);
  const [showGapOverlay, setShowGapOverlay] = useState(false);
  const [tooltip, setTooltip] = useState(null);

  const draw = useCallback(() => {
    const container = containerRef.current;
    const svgEl = svgRef.current;
    if (!container || !svgEl) return;

    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();

    const { width, height } = container.getBoundingClientRect();
    svg.attr('width', width).attr('height', height);

    if (!pages.length) {
      svg.append('text')
        .attr('x', width / 2).attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-tertiary)')
        .style('font-family', '"DM Mono", monospace')
        .style('font-size', '11px')
        .text('NO CORPUS DATA');
      return;
    }

    // Group pages into clusters so the treemap reads as topic regions
    // (per the dashboard brief: level 1 clusters, level 2 pages).
    const byCluster = d3.group(pages, p => p.cluster ?? 0);
    const rootData = {
      children: [...byCluster.entries()].map(([cluster, clusterPages]) => ({
        cluster,
        children: clusterPages.map(p => ({ ...p, value: Math.max(p.chunk_count ?? 1, 1) })),
      })),
    };

    const root = d3.hierarchy(rootData).sum(d => d.value ?? 0);
    d3.treemap()
      .size([width, height])
      .paddingInner(2)
      .paddingOuter(3)
      .paddingTop(16) // room for the cluster label band
      (root);

    // Hatching pattern for never-alerted pages
    const defs = svg.append('defs');
    defs.append('pattern')
      .attr('id', 'uncovered-hatch')
      .attr('patternUnits', 'userSpaceOnUse')
      .attr('width', 6)
      .attr('height', 6)
      .attr('patternTransform', 'rotate(45)')
      .append('line')
      .attr('x1', 0).attr('y1', 0)
      .attr('x2', 0).attr('y2', 6)
      .attr('stroke', '#5c5a52')
      .attr('stroke-width', 2);

    // Cluster group frames + labels
    const clusterNodes = root.children ?? [];
    const clusterG = svg.append('g')
      .selectAll('g')
      .data(clusterNodes)
      .join('g');

    clusterG.append('rect')
      .attr('x', d => d.x0).attr('y', d => d.y0)
      .attr('width', d => Math.max(0, d.x1 - d.x0))
      .attr('height', d => Math.max(0, d.y1 - d.y0))
      .attr('fill', 'none')
      .attr('stroke', d => clusterColor(d.data.cluster))
      .attr('stroke-opacity', 0.5)
      .attr('stroke-width', 1);

    clusterG.append('text')
      .attr('x', d => d.x0 + 5)
      .attr('y', d => d.y0 + 11)
      .attr('fill', d => clusterColor(d.data.cluster))
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .style('letter-spacing', '0.06em')
      .text(d => `CLUSTER ${d.data.cluster} · ${d.children?.length ?? 0} PAGES`);

    // Page cells
    const cell = svg.append('g')
      .selectAll('g')
      .data(root.leaves())
      .join('g')
      .attr('transform', d => `translate(${d.x0},${d.y0})`)
      .style('cursor', 'pointer')
      .on('click', (event, d) => setSelectedPageId(d.data.page_id))
      .on('mousemove', (event, d) => {
        setTooltip({
          x: Math.min(event.clientX + 12, window.innerWidth - 240),
          y: event.clientY + 12,
          page: d.data,
        });
      })
      .on('mouseleave', () => setTooltip(null));

    const cellW = d => d.x1 - d.x0;
    const cellH = d => d.y1 - d.y0;

    cell.append('rect')
      .attr('width', d => Math.max(0, cellW(d)))
      .attr('height', d => Math.max(0, cellH(d)))
      .attr('fill', d => clusterColor(d.data.cluster))
      .attr('opacity', d => (d.data.alert_count ?? 0) > 0 ? 0.95 : 0.55)
      .attr('stroke', d => (d.data.alert_count ?? 0) > 0 ? getCSSColor('--state-alert') : 'var(--bg-primary)')
      .attr('stroke-width', d => (d.data.alert_count ?? 0) > 0 ? 2 : 1);

    // Never-alerted overlay (toggle)
    if (showGapOverlay) {
      cell.filter(d => (d.data.alert_count_total ?? d.data.alert_count ?? 0) === 0)
        .append('rect')
        .attr('width', d => Math.max(0, cellW(d)))
        .attr('height', d => Math.max(0, cellH(d)))
        .attr('fill', 'url(#uncovered-hatch)')
        .attr('opacity', 0.6)
        .attr('pointer-events', 'none');
    }

    // Labels: title (primary) + page_id (secondary)
    cell.filter(d => cellW(d) > 40 && cellH(d) > 18)
      .append('foreignObject')
      .attr('x', 4).attr('y', 4)
      .attr('width', d => Math.max(0, cellW(d) - 8))
      .attr('height', d => Math.max(0, cellH(d) - 8))
      .style('pointer-events', 'none')
      .append('xhtml:div')
      .style('width', '100%')
      .style('height', '100%')
      .style('overflow', 'hidden')
      .html(d => {
        const w = cellW(d);
        const flag = (d.data.alert_count ?? 0) > 0 ? '⚑ ' : '';
        const titleText = flag + (d.data.title || d.data.page_id);
        const title = `<div style="font-family:'Lora',serif;font-size:10px;color:var(--text-primary);line-height:1.2;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${titleText}</div>`;
        if (w < 80) return title;
        const id = `<div style="font-family:'DM Mono',monospace;font-size:8px;color:var(--text-secondary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;margin-top:2px;">${d.data.page_id} · ${d.data.chunk_count ?? 0} chunks</div>`;
        return title + id;
      });
  }, [pages, showGapOverlay]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(draw);
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [draw]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
      {/* Toolbar + legend */}
      <div style={{
        padding: '8px 12px',
        borderBottom: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center', gap: '14px',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '10px',
          color: 'var(--text-tertiary)', letterSpacing: '0.05em',
          display: 'flex', gap: '14px', alignItems: 'center', flexWrap: 'wrap', flex: 1,
        }}>
          <span>CELL = IPFR PAGE</span>
          <span>SIZE = CHUNK COUNT</span>
          <span>COLOUR = TOPIC CLUSTER</span>
          <span style={{ color: 'var(--state-alert)' }}>⚑ BORDER = OUTSTANDING ALERTS</span>
          {showGapOverlay && <span>HATCHED = NEVER ALERTED</span>}
        </div>

        <button
          onClick={() => setShowGapOverlay(s => !s)}
          title="Hatch pages that no alert has ever flagged — potential monitoring blind spots"
          style={{
            background: showGapOverlay ? 'var(--state-warn)' : 'transparent',
            color: showGapOverlay ? 'var(--bg-primary)' : 'var(--state-warn)',
            border: '1px solid var(--state-warn)',
            padding: '3px 10px',
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            letterSpacing: '0.06em',
            cursor: 'pointer',
          }}
        >
          SHOW UNCOVERED PAGES
        </button>
      </div>

      {/* Treemap */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <svg ref={svgRef} style={{ display: 'block' }} />
        {selectedPageId && (
          <PageDetailPanel pageId={selectedPageId} onClose={() => setSelectedPageId(null)} />
        )}
      </div>

      {/* Hover tooltip */}
      {tooltip && (
        <div style={{
          position: 'fixed',
          top: tooltip.y,
          left: tooltip.x,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '8px 10px',
          zIndex: 1000,
          pointerEvents: 'none',
          maxWidth: '220px',
        }}>
          <div style={{ fontFamily: 'var(--font-body)', fontSize: '12px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            {tooltip.page.title || tooltip.page.page_id}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            {tooltip.page.page_id} · cluster {tooltip.page.cluster ?? '—'}<br />
            {tooltip.page.chunk_count ?? 0} chunks · {tooltip.page.entity_count ?? 0} entities<br />
            <span style={{ color: (tooltip.page.alert_count ?? 0) > 0 ? 'var(--state-alert)' : 'var(--text-tertiary)' }}>
              {tooltip.page.alert_count ?? 0} outstanding alert{(tooltip.page.alert_count ?? 0) !== 1 ? 's' : ''}
            </span>
            {' '}({tooltip.page.alert_count_total ?? tooltip.page.alert_count ?? 0} all-time)
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
            click for detail
          </div>
        </div>
      )}
    </div>
  );
}
