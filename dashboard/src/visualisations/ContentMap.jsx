import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as d3 from 'd3';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];

function getCSSColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || '#5c5a52';
}

function clusterColor(cluster) {
  const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
  return getCSSColor(`--stage-${stageIdx}`);
}

function hexWithOpacity(hex, opacity) {
  hex = hex.replace('#', '');
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${opacity})`;
}

export default function ContentMap({ pages = [], isActive }) {
  const containerRef = useRef(null);
  const svgRef       = useRef(null);
  const [drillPage, setDrillPage]   = useState(null);
  const [showGapOverlay, setShowGapOverlay] = useState(false);

  const buildLayout = useCallback((width, height, pageList) => {
    if (!pageList.length) return null;
    const root = d3.hierarchy({ children: pageList.map(p => ({ ...p, value: Math.max(p.chunk_count ?? 1, 1) }) ) })
      .sum(d => d.value);
    return d3.treemap()
      .size([width, height])
      .paddingInner(2)
      .paddingOuter(4)
      (root);
  }, []);

  const buildChunkLayout = useCallback((width, height, page) => {
    const chunkCount = page.chunk_count ?? 1;
    const chunks = Array.from({ length: chunkCount }, (_, i) => ({ id: `chunk_${i}`, value: 1 }));
    const root = d3.hierarchy({ children: chunks }).sum(d => d.value);
    return d3.treemap()
      .size([width, height])
      .paddingInner(1)
      .paddingOuter(2)
      (root);
  }, []);

  function renderTreemap(pages) {
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const { width, height } = containerRef.current.getBoundingClientRect();
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

    const layout = buildLayout(width, height, pages);
    if (!layout) return;

    const warnColor = hexWithOpacity(getCSSColor('--state-warn'), 0.4);

    const cell = svg.append('g')
      .selectAll('g')
      .data(layout.leaves())
      .join('g')
      .attr('transform', d => `translate(${d.x0},${d.y0})`)
      .style('cursor', 'pointer')
      .on('click', (event, d) => setDrillPage(d.data));

    const cellW = d => d.x1 - d.x0;
    const cellH = d => d.y1 - d.y0;

    // Background fill
    cell.append('rect')
      .attr('width', d => Math.max(0, cellW(d)))
      .attr('height', d => Math.max(0, cellH(d)))
      .attr('fill', d => clusterColor(d.data.cluster))
      .attr('opacity', 0.75)
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 1);

    // Coverage gap overlay
    if (showGapOverlay) {
      cell.filter(d => !(d.data.alert_count > 0) || !(d.data.degree > 0))
        .append('rect')
        .attr('width', d => Math.max(0, cellW(d)))
        .attr('height', d => Math.max(0, cellH(d)))
        .attr('fill', warnColor)
        .attr('pointer-events', 'none');
    }

    // Labels: page_id + title
    cell.filter(d => cellW(d) > 40)
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
        const id = `<div style="font-family:'DM Mono',monospace;font-size:10px;color:var(--text-primary);line-height:1.2;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${d.data.page_id}</div>`;
        if (w < 80) return id;
        const title = `<div style="font-family:'Lora',serif;font-size:9px;color:var(--text-secondary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;margin-top:2px;">${d.data.title || ''}</div>`;
        return id + title;
      });
  }

  function renderDrilldown(page) {
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const { width, height } = containerRef.current.getBoundingClientRect();
    svg.attr('width', width).attr('height', height);

    const layout = buildChunkLayout(width, height, page);
    if (!layout) return;

    const color = clusterColor(page.cluster);

    const cell = svg.append('g')
      .selectAll('g')
      .data(layout.leaves())
      .join('g')
      .attr('transform', d => `translate(${d.x0},${d.y0})`);

    cell.append('rect')
      .attr('width', d => Math.max(0, d.x1 - d.x0))
      .attr('height', d => Math.max(0, d.y1 - d.y0))
      .attr('fill', color)
      .attr('opacity', 0.6)
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 1);

    cell.filter(d => (d.x1 - d.x0) > 30)
      .append('text')
      .attr('x', 4).attr('y', 14)
      .attr('fill', 'var(--text-primary)')
      .style('font-family', '"DM Mono", monospace')
      .style('font-size', '9px')
      .text(d => d.data.id);
  }

  // Redraw whenever key state changes
  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;
    if (drillPage) {
      renderDrilldown(drillPage);
    } else {
      renderTreemap(pages);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, drillPage, showGapOverlay]);

  // Resize observer
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => {
      if (drillPage) renderDrilldown(drillPage);
      else renderTreemap(pages);
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, drillPage, showGapOverlay]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Toolbar */}
      <div style={{
        padding: '8px 12px',
        borderBottom: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center', gap: '12px',
        flexShrink: 0,
      }}>
        {/* Breadcrumb */}
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)', letterSpacing: '0.06em', flex: 1 }}>
          {drillPage ? (
            <>
              <span
                onClick={() => setDrillPage(null)}
                style={{ cursor: 'pointer', color: 'var(--stage-4)', textDecoration: 'underline' }}
              >
                CORPUS
              </span>
              <span style={{ color: 'var(--text-tertiary)' }}> › </span>
              <span style={{ color: 'var(--text-primary)' }}>{drillPage.page_id}</span>
            </>
          ) : (
            <span style={{ color: 'var(--text-tertiary)' }}>CORPUS</span>
          )}
        </div>

        {/* Gap overlay toggle */}
        <button
          onClick={() => setShowGapOverlay(s => !s)}
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
          COVERAGE GAPS
        </button>
      </div>

      {/* Treemap */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <svg ref={svgRef} style={{ display: 'block' }} />
      </div>
    </div>
  );
}
