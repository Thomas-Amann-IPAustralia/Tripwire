import React, { useRef, useEffect, useCallback } from 'react';
import * as d3 from 'd3';
import { useSources, usePages, useRuns } from '../hooks/useData.js';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];

function getCSSColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || '#5c5a52';
}

function clusterColor(cluster) {
  const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
  return getCSSColor(`--stage-${stageIdx}`);
}

export default function BipartiteMap({ isActive }) {
  const svgRef      = useRef(null);
  const containerRef = useRef(null);

  const { data: sourcesRaw } = useSources();
  const { data: pagesRaw }   = usePages();
  const { data: runsRaw }    = useRuns();

  const sources = Array.isArray(sourcesRaw) ? sourcesRaw : (sourcesRaw?.data ?? []);
  const pages   = Array.isArray(pagesRaw?.data) ? pagesRaw.data : (pagesRaw ?? []);
  const runs    = Array.isArray(runsRaw) ? runsRaw : (runsRaw?.data ?? []);

  const draw = useCallback(() => {
    const container = containerRef.current;
    const svgEl = svgRef.current;
    if (!container || !svgEl || !sources.length || !pages.length) return;

    const { width, height } = container.getBoundingClientRect();
    const svg = d3.select(svgEl)
      .attr('width', width)
      .attr('height', height);

    svg.selectAll('*').remove();

    const margin = { top: 24, bottom: 24, left: 12, right: 12 };
    const GAP = 120;
    const leftX  = margin.left + 80;
    const rightX = width - margin.right - 80;
    const drawH  = height - margin.top - margin.bottom;

    // Build edge data: source → page, from runs with triggered_pages
    const edgeMap = new Map(); // key: `${source_id}|${page_id}` → { confidence }
    for (const run of runs) {
      if (!run.triggered_pages?.length) continue;
      const conf = run.confidence ?? 0;
      for (const pageId of run.triggered_pages) {
        const key = `${run.source_id}|${pageId}`;
        const existing = edgeMap.get(key);
        if (!existing || conf > existing.confidence) {
          edgeMap.set(key, { source_id: run.source_id, page_id: pageId, confidence: conf });
        }
      }
    }
    const edges = [...edgeMap.values()];

    // Only keep pages that appear in edges
    const connectedPageIds = new Set(edges.map(e => e.page_id));
    const connectedSourceIds = new Set(edges.map(e => e.source_id));

    const shownSources = sources.filter(s => connectedSourceIds.has(s.source_id));
    const shownPages   = pages.filter(p => connectedPageIds.has(p.page_id));

    // If no edges, show placeholder
    if (!edges.length) {
      svg.append('text')
        .attr('x', width / 2).attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-tertiary)')
        .style('font-family', '"DM Mono", monospace')
        .style('font-size', '11px')
        .text('NO SOURCE-CORPUS CONNECTIONS IN RUN HISTORY');
      return;
    }

    // Y positions
    const srcY = d3.scalePoint()
      .domain(shownSources.map(s => s.source_id))
      .range([margin.top, margin.top + drawH])
      .padding(0.5);
    const pgY = d3.scalePoint()
      .domain(shownPages.map(p => p.page_id))
      .range([margin.top, margin.top + drawH])
      .padding(0.5);

    const maxConf = Math.max(...edges.map(e => e.confidence ?? 0), 0.01);

    const g = svg.append('g');

    // Column headers
    g.append('text')
      .attr('x', leftX).attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text-tertiary)')
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .style('letter-spacing', '0.06em')
      .text('SOURCES');
    g.append('text')
      .attr('x', rightX).attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text-tertiary)')
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .style('letter-spacing', '0.06em')
      .text('IPFR PAGES');

    // Draw bezier edges
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
      .attr('stroke', getCSSColor('--stage-4'))
      .attr('stroke-width', d => 0.5 + ((d.confidence ?? 0) / maxConf) * 2.5)
      .attr('stroke-opacity', 0.3)
      .attr('class', 'bipartite-link')
      .attr('data-source', d => d.source_id)
      .attr('data-page', d => d.page_id);

    function highlight(sourceId, pageId) {
      const FADE = 0.08;
      const FULL = 0.9;
      const DEFAULT = 0.3;

      if (!sourceId && !pageId) {
        // Reset
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

    // Page nodes
    const pgNodeSel = g.append('g').attr('class', 'pg-nodes')
      .selectAll('g')
      .data(shownPages)
      .join('g')
      .attr('transform', d => `translate(${rightX},${pgY(d.page_id)})`)
      .style('cursor', 'pointer')
      .on('mouseover', (event, d) => highlight(null, d.page_id))
      .on('mouseout', () => highlight(null, null));

    pgNodeSel.append('circle')
      .attr('r', d => 4 + ((d.alert_count ?? 0) / Math.max(...pages.map(p => p.alert_count ?? 0), 1)) * 6)
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
  }, [sources, pages, runs]);

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
    <div ref={containerRef} style={{ height: '100%', width: '100%', overflow: 'hidden', position: 'relative' }}>
      <svg ref={svgRef} style={{ display: 'block', background: 'var(--bg-primary)' }} />
    </div>
  );
}
