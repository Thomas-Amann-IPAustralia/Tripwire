import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { PageDetailPanel, CANVAS_BG } from './KnowledgeGraph.jsx';
import { Segmented } from '../components/ui.jsx';

const CLUSTER_STAGE = [1, 2, 3, 4, 5, 6, 1];

// var() / color-mix() references keep the treemap in step with the theme.
function clusterColor(cluster) {
  const stageIdx = CLUSTER_STAGE[(cluster ?? 0) % CLUSTER_STAGE.length];
  return `var(--stage-${stageIdx})`;
}

function cellFill(cluster, strong) {
  return `color-mix(in srgb, ${clusterColor(cluster)} ${strong ? 38 : 18}%, var(--surface))`;
}

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
        .style('fill', 'var(--muted)')
        .style('font-family', 'var(--font-sans)')
        .style('font-size', '13px')
        .text('No corpus data yet');
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
      .paddingInner(3)
      .paddingOuter(4)
      .paddingTop(22) // room for the cluster label band
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
      .style('stroke', 'var(--muted)')
      .attr('stroke-width', 1.5);

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
      .attr('rx', 8)
      .style('fill', d => `color-mix(in srgb, ${clusterColor(d.data.cluster)} 6%, transparent)`)
      .style('stroke', d => clusterColor(d.data.cluster))
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', 1);

    clusterG.append('text')
      .attr('x', d => d.x0 + 5)
      .attr('x', d => d.x0 + 8)
      .attr('y', d => d.y0 + 15)
      .style('fill', d => clusterColor(d.data.cluster))
      .style('font-family', 'var(--font-sans)')
      .style('font-size', '11px')
      .style('font-weight', '650')
      .text(d => `Cluster ${d.data.cluster} · ${d.children?.length ?? 0} pages`);

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
      .attr('rx', 5)
      .style('fill', d => cellFill(d.data.cluster, (d.data.alert_count ?? 0) > 0))
      .style('stroke', d => (d.data.alert_count ?? 0) > 0 ? 'var(--alert)' : `color-mix(in srgb, ${clusterColor(d.data.cluster)} 35%, var(--surface))`)
      .attr('stroke-width', d => (d.data.alert_count ?? 0) > 0 ? 2 : 1);

    // Never-alerted overlay (toggle)
    if (showGapOverlay) {
      cell.filter(d => (d.data.alert_count_total ?? d.data.alert_count ?? 0) === 0)
        .append('rect')
        .attr('width', d => Math.max(0, cellW(d)))
        .attr('height', d => Math.max(0, cellH(d)))
        .attr('rx', 5)
        .attr('fill', 'url(#uncovered-hatch)')
        .attr('opacity', 0.35)
        .attr('pointer-events', 'none');
    }

    // Labels: title (primary) + page_id (secondary)
    cell.filter(d => cellW(d) > 40 && cellH(d) > 18)
      .append('foreignObject')
      .attr('x', 6).attr('y', 5)
      .attr('width', d => Math.max(0, cellW(d) - 12))
      .attr('height', d => Math.max(0, cellH(d) - 10))
      .style('pointer-events', 'none')
      .append('xhtml:div')
      .style('width', '100%')
      .style('height', '100%')
      .style('overflow', 'hidden')
      .html(d => {
        const w = cellW(d);
        const flag = (d.data.alert_count ?? 0) > 0 ? '<span style="color:var(--alert)">● </span>' : '';
        const titleText = flag + escapeHtml(d.data.title || d.data.page_id);
        const title = `<div style="font-family:var(--font-sans);font-size:11px;font-weight:600;color:var(--ink);line-height:1.25;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${titleText}</div>`;
        if (w < 80) return title;
        const id = `<div style="font-family:var(--font-sans);font-size:10px;color:var(--muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;margin-top:2px;">${escapeHtml(d.data.page_id)} · ${d.data.chunk_count ?? 0} chunks</div>`;
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
        padding: '10px 16px',
        borderBottom: '1px solid var(--line-soft)',
        background: 'var(--surface)',
        display: 'flex', alignItems: 'center', gap: '14px',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        <div style={{
          fontSize: '12px', color: 'var(--muted)',
          display: 'flex', gap: '16px', alignItems: 'center', flexWrap: 'wrap', flex: 1,
        }}>
          <span>Each cell is an IPFR page</span>
          <span>Size shows chunk count</span>
          <span>Colour shows topic cluster</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--alert)' }}>
            <span style={{ width: 12, height: 12, borderRadius: 3, border: '2px solid var(--alert)' }} /> Outstanding alerts
          </span>
          {showGapOverlay && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: 12, height: 12, borderRadius: 3, background: 'repeating-linear-gradient(45deg, var(--muted) 0 1.5px, transparent 1.5px 4px)', opacity: 0.6 }} /> Never alerted
            </span>
          )}
        </div>

        <Segmented
          ariaLabel="Coverage overlay"
          value={showGapOverlay ? 'gaps' : 'all'}
          onChange={v => setShowGapOverlay(v === 'gaps')}
          options={[
            { value: 'all', label: 'All pages' },
            { value: 'gaps', label: 'Highlight uncovered pages', title: 'Hatch pages that no alert has ever flagged: potential monitoring blind spots' },
          ]}
        />
      </div>

      {/* Treemap */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'hidden', position: 'relative', ...CANVAS_BG }}>
        <svg ref={svgRef} style={{ display: 'block' }} />
        {selectedPageId && (
          <PageDetailPanel pageId={selectedPageId} onClose={() => setSelectedPageId(null)} />
        )}
      </div>

      {/* Hover tooltip */}
      {tooltip && (
        <div className="tw-chart-tip" style={{
          position: 'fixed',
          top: tooltip.y,
          left: tooltip.x,
          zIndex: 1000,
          maxWidth: '240px',
        }}>
          <div style={{ fontWeight: 650, color: 'var(--ink)', marginBottom: '2px' }}>
            {tooltip.page.title || tooltip.page.page_id}
          </div>
          <div style={{ fontSize: '11.5px', lineHeight: 1.6 }}>
            <span className="tw-code">{tooltip.page.page_id}</span> · cluster {tooltip.page.cluster ?? '—'}<br />
            {tooltip.page.chunk_count ?? 0} chunks · {tooltip.page.entity_count ?? 0} entities<br />
            <span style={{ color: (tooltip.page.alert_count ?? 0) > 0 ? 'var(--alert)' : 'var(--muted)', fontWeight: 600 }}>
              {tooltip.page.alert_count ?? 0} outstanding alert{(tooltip.page.alert_count ?? 0) !== 1 ? 's' : ''}
            </span>
            {' '}({tooltip.page.alert_count_total ?? tooltip.page.alert_count ?? 0} all time)
          </div>
          <div className="tw-muted" style={{ fontSize: '11px', marginTop: '4px' }}>Select for details</div>
        </div>
      )}
    </div>
  );
}
