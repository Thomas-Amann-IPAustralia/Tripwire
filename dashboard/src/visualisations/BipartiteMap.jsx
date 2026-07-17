import React, { useRef, useEffect, useCallback, useMemo } from 'react';
import * as d3 from 'd3';
import { useSources, usePages, useBipartiteEdges } from '../hooks/useData.js';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];
const ROW_STEP = 16; // minimum vertical pixels per node row

function getCSSColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || '#5c5a52';
}

function clusterColor(cluster) {
  const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
  return getCSSColor(`--stage-${stageIdx}`);
}

export default function BipartiteMap({ isActive }) {
  const svgRef       = useRef(null);
  const containerRef = useRef(null);
  const scrollRef    = useRef(null);

  const { data: sourcesRaw } = useSources();
  const { data: pagesRaw }   = usePages();
  // Edges are aggregated server-side over the FULL run history — the old
  // client-side derivation from /api/runs was silently capped at 1000 rows,
  // so most influencer sources and influenced pages never appeared.
  const { data: edgesRaw }   = useBipartiteEdges();

  const sources = Array.isArray(sourcesRaw) ? sourcesRaw : (sourcesRaw?.data ?? []);
  const pages   = Array.isArray(pagesRaw?.data) ? pagesRaw.data : (pagesRaw ?? []);
  const edges   = Array.isArray(edgesRaw) ? edgesRaw : [];

  const { shownSources, shownPages } = useMemo(() => {
    const connectedPageIds   = new Set(edges.map(e => e.page_id));
    const connectedSourceIds = new Set(edges.map(e => e.source_id));

    const knownSourceIds = new Set(sources.map(s => s.source_id));
    const knownPageIds   = new Set(pages.map(p => p.page_id));

    const shownSources = [
      ...sources.filter(s => connectedSourceIds.has(s.source_id)),
      // Sources present in run history but missing from the registry still
      // deserve a row rather than vanishing.
      ...[...connectedSourceIds].filter(id => !knownSourceIds.has(id)).map(id => ({ source_id: id })),
    ];
    const shownPages = [
      ...pages.filter(p => connectedPageIds.has(p.page_id)),
      ...[...connectedPageIds].filter(id => !knownPageIds.has(id)).map(id => ({ page_id: id })),
    ];
    return { shownSources, shownPages };
  }, [sources, pages, edges]);

  const draw = useCallback(() => {
    const container = containerRef.current;
    const svgEl = svgRef.current;
    if (!container || !svgEl) return;

    const { width } = container.getBoundingClientRect();
    // Visible viewport height comes from the scroll wrapper — the inner
    // container grows with the SVG, so measuring it would feed back on itself.
    const viewH = scrollRef.current?.clientHeight ?? 400;

    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();

    if (!edges.length) {
      svg.attr('width', width).attr('height', viewH);
      svg.append('text')
        .attr('x', width / 2).attr('y', viewH / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-tertiary)')
        .style('font-family', '"DM Mono", monospace')
        .style('font-size', '11px')
        .text('NO SOURCE-CORPUS CONNECTIONS IN RUN HISTORY');
      return;
    }

    // Grow vertically to fit every node; the wrapper scrolls.
    const maxRows = Math.max(shownSources.length, shownPages.length);
    const height = Math.max(viewH, maxRows * ROW_STEP + 60);
    svg.attr('width', width).attr('height', height);

    const margin = { top: 24, bottom: 24 };
    const leftX  = 12 + 80;
    const rightX = width - 12 - 80;
    const drawH  = height - margin.top - margin.bottom;

    // Y positions
    const srcY = d3.scalePoint()
      .domain(shownSources.map(s => s.source_id))
      .range([margin.top, margin.top + drawH])
      .padding(0.5);
    const pgY = d3.scalePoint()
      .domain(shownPages.map(p => p.page_id))
      .range([margin.top, margin.top + drawH])
      .padding(0.5);

    const maxTriggers = Math.max(...edges.map(e => e.trigger_count ?? 1), 1);

    const g = svg.append('g');

    // Column headers
    for (const [x, label] of [[leftX, 'SOURCES'], [rightX, 'IPFR PAGES']]) {
      g.append('text')
        .attr('x', x).attr('y', 14)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-tertiary)')
        .style('font-family', '"DM Mono", monospace')
        .style('font-size', '9px')
        .style('letter-spacing', '0.06em')
        .text(label);
    }

    // Draw bezier edges — red-tinted when the connection produced a
    // CHANGE_REQUIRED verdict, blue for trigger-only connections.
    const linkG = g.append('g').attr('class', 'links');

    const linkSel = linkG.selectAll('path')
      .data(edges)
      .join('path')
      .attr('d', d => {
        const sy = srcY(d.source_id) ?? 0;
        const ty = pgY(d.page_id) ?? 0;
        const cx = (leftX + rightX) / 2;
        return `M${leftX},${sy} C${cx},${sy} ${cx},${ty} ${rightX},${ty}`;
      })
      .attr('fill', 'none')
      .attr('stroke', d => (d.change_required_count ?? 0) > 0
        ? getCSSColor('--state-alert')
        : getCSSColor('--stage-4'))
      .attr('stroke-width', d => 0.5 + ((d.trigger_count ?? 1) / maxTriggers) * 2.5)
      .attr('stroke-opacity', 0.3)
      .attr('class', 'bipartite-link');

    function highlight(sourceId, pageId) {
      const FADE = 0.08;
      const FULL = 0.9;
      const DEFAULT = 0.3;

      if (!sourceId && !pageId) {
        linkSel.transition().duration(200).attr('stroke-opacity', DEFAULT);
        srcNodeSel.transition().duration(200).attr('opacity', 1).attr('transform', d => `translate(${leftX},${srcY(d.source_id)})`);
        pgNodeSel.transition().duration(200).attr('opacity', 1).attr('transform', d => `translate(${rightX},${pgY(d.page_id)})`);
        return;
      }

      if (sourceId) {
        const connPages = new Set(edges.filter(e => e.source_id === sourceId).map(e => e.page_id));
        linkSel.transition().duration(200).attr('stroke-opacity', d => d.source_id === sourceId ? FULL : FADE);
        srcNodeSel.transition().duration(200)
          .attr('opacity', d => d.source_id === sourceId ? 1 : FADE)
          .attr('transform', d => {
            const s = d.source_id === sourceId ? 1.15 : 1;
            return `translate(${leftX},${srcY(d.source_id)}) scale(${s})`;
          });
        pgNodeSel.transition().duration(200)
          .attr('opacity', d => connPages.has(d.page_id) ? 1 : FADE);
      }

      if (pageId) {
        const connSources = new Set(edges.filter(e => e.page_id === pageId).map(e => e.source_id));
        linkSel.transition().duration(200).attr('stroke-opacity', d => d.page_id === pageId ? FULL : FADE);
        pgNodeSel.transition().duration(200)
          .attr('opacity', d => d.page_id === pageId ? 1 : FADE)
          .attr('transform', d => {
            const s = d.page_id === pageId ? 1.15 : 1;
            return `translate(${rightX},${pgY(d.page_id)}) scale(${s})`;
          });
        srcNodeSel.transition().duration(200)
          .attr('opacity', d => connSources.has(d.source_id) ? 1 : FADE);
      }
    }

    // Source nodes
    const srcNodeSel = g.append('g').attr('class', 'src-nodes')
      .selectAll('g')
      .data(shownSources)
      .join('g')
      .attr('transform', d => `translate(${leftX},${srcY(d.source_id)})`)
      .style('cursor', 'pointer')
      .on('mouseover', (event, d) => highlight(d.source_id, null))
      .on('mouseout', () => highlight(null, null));

    srcNodeSel.append('circle')
      .attr('r', 5)
      .attr('fill', getCSSColor('--stage-2'))
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 1);

    srcNodeSel.append('text')
      .attr('x', -10)
      .attr('dy', '0.35em')
      .attr('text-anchor', 'end')
      .attr('fill', 'var(--text-secondary)')
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .text(d => d.source_id);

    // Page nodes — sized by total trigger count across all sources
    const triggersByPage = new Map();
    for (const e of edges) {
      triggersByPage.set(e.page_id, (triggersByPage.get(e.page_id) ?? 0) + (e.trigger_count ?? 1));
    }
    const maxPageTriggers = Math.max(...triggersByPage.values(), 1);

    const pgNodeSel = g.append('g').attr('class', 'pg-nodes')
      .selectAll('g')
      .data(shownPages)
      .join('g')
      .attr('transform', d => `translate(${rightX},${pgY(d.page_id)})`)
      .style('cursor', 'pointer')
      .on('mouseover', (event, d) => highlight(null, d.page_id))
      .on('mouseout', () => highlight(null, null));

    pgNodeSel.append('circle')
      .attr('r', d => 4 + ((triggersByPage.get(d.page_id) ?? 0) / maxPageTriggers) * 6)
      .attr('fill', d => clusterColor(d.cluster))
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 1);

    pgNodeSel.append('text')
      .attr('x', 10)
      .attr('dy', '0.35em')
      .attr('fill', 'var(--text-secondary)')
      .style('font-family', '"Lora", serif')
      .style('font-size', '9px')
      .text(d => d.title || d.page_id);
  }, [shownSources, shownPages, edges]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(draw);
    ro.observe(container);
    return () => ro.disconnect();
  }, [draw]);

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        padding: '6px 12px',
        borderBottom: '1px solid var(--rule)',
        fontFamily: 'var(--font-mono)', fontSize: '10px',
        color: 'var(--text-tertiary)', letterSpacing: '0.06em',
        flexShrink: 0,
        display: 'flex', gap: '16px', alignItems: 'center', flexWrap: 'wrap',
      }}>
        <span>
          {shownSources.length} SOURCES → {shownPages.length} PAGES · {edges.length} CONNECTIONS · FULL RUN HISTORY
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 12, height: 2, background: 'var(--state-alert)', display: 'inline-block' }} />
          change required
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 12, height: 2, background: 'var(--stage-4)', display: 'inline-block' }} />
          triggered only
        </span>
      </div>
      <div
        ref={scrollRef}
        style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', position: 'relative' }}
      >
        <div ref={containerRef} style={{ minHeight: '100%', width: '100%' }}>
          <svg ref={svgRef} style={{ display: 'block', background: 'var(--bg-primary)' }} />
        </div>
      </div>
    </div>
  );
}
