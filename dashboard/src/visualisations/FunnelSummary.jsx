import React, { useState } from 'react';
import { computeFunnelCounts, funnelFromSummary } from '../lib/dataUtils.js';
import { Card, Icon } from '../components/ui.jsx';

const STAGE_SHORT = ['Probe', 'Detect', 'Diff', 'Relevance', 'Bi-encoder', 'Cross-encoder', 'Aggregate'];
const STAGE_FULL  = [
  'Stage 1: Metadata probe',
  'Stage 2: Change detection',
  'Stage 3: Diff generation',
  'Stage 4: Relevance scoring',
  'Stage 5: Bi-encoder chunking',
  'Stage 6: Cross-encoder reranking',
  'Stage 7: Trigger aggregation',
];

const BAR_MAX_H = 96;
const PANEL_LABEL_H = 40;

// Prefers server-side `summary` (aggregated over the full run history, immune
// to the /api/runs row limit); falls back to computing from the runs list.
export default function FunnelSummary({ runs = [], summary = null, onStageClick }) {
  const [tooltip, setTooltip] = useState(null);

  const funnel = summary?.data
    ? funnelFromSummary(summary.data, summary.triggered_page_count ?? 0)
    : computeFunnelCounts(runs);
  const maxCount = Math.max(...funnel.map(d => d.count), 1);
  const totalRuns = summary?.total_runs;

  return (
    <Card
      title="Pipeline funnel"
      subtitle={`How far source checks progressed through the pipeline${totalRuns != null ? `, across ${totalRuns.toLocaleString()} source checks` : ''}. Select a stage to filter the page.`}
    >
      <div style={{
        display: 'flex',
        alignItems: 'flex-end',
        height: `${BAR_MAX_H + PANEL_LABEL_H + 24}px`,
        gap: '4px',
      }}>
        {funnel.map((d, i) => {
          const barH = Math.max(3, Math.round((d.count / maxCount) * BAR_MAX_H));
          const isPageStage = d.unit === 'pages';
          // Only show pass-through rate between same-unit consecutive stages (S1–S6)
          const prevCount = (!isPageStage && i > 0 && funnel[i - 1].unit === 'runs')
            ? funnel[i - 1].count
            : null;
          const passRate = prevCount != null && prevCount > 0
            ? ((d.count / prevCount) * 100).toFixed(1)
            : null;
          const hot = tooltip?.stage === d.stage;

          return (
            <React.Fragment key={d.stage}>
              <div
                role={isPageStage ? undefined : 'button'}
                tabIndex={isPageStage ? undefined : 0}
                aria-label={`${STAGE_FULL[i]}: ${d.count.toLocaleString()}`}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  flex: 1,
                  minWidth: 0,
                  cursor: isPageStage ? 'default' : 'pointer',
                  position: 'relative',
                  height: '100%',
                  justifyContent: 'flex-end',
                  borderRadius: 'var(--radius)',
                  paddingBottom: '2px',
                  background: hot ? 'var(--surface-hover)' : 'transparent',
                  transition: 'background 120ms ease',
                }}
                onMouseEnter={e => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  setTooltip({ stage: d.stage, count: d.count, unit: d.unit, passRate, i, x: rect.left + rect.width / 2, y: rect.top });
                }}
                onMouseLeave={() => setTooltip(null)}
                onClick={() => !isPageStage && onStageClick?.(d.stage)}
                onKeyDown={e => { if (!isPageStage && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onStageClick?.(d.stage); } }}
              >
                <div className="tabular" style={{ fontSize: '12.5px', fontWeight: 650, color: 'var(--ink)', marginBottom: '6px', lineHeight: 1 }}>
                  {d.count.toLocaleString()}
                </div>
                <div style={{
                  width: '58%',
                  maxWidth: '56px',
                  minWidth: '10px',
                  height: `${barH}px`,
                  borderRadius: '5px 5px 2px 2px',
                  background: isPageStage ? 'var(--ochre)' : `var(--stage-${d.stage})`,
                  opacity: hot ? 1 : 0.82,
                  transition: 'opacity 120ms ease',
                }} />
                <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--body)', marginTop: '8px' }}>
                  Stage {d.stage}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--muted)', textAlign: 'center', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                  {STAGE_SHORT[i]}{isPageStage ? ' (pages)' : ''}
                </div>
              </div>

              {i < funnel.length - 1 && (
                <div aria-hidden="true" style={{ color: 'var(--line-strong)', paddingBottom: `${PANEL_LABEL_H}px`, flexShrink: 0, display: 'flex' }}>
                  <Icon name="chevron" size={14} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {tooltip && (
        <div className="tw-chart-tip" style={{
          position: 'fixed',
          top: tooltip.y - 8,
          left: tooltip.x,
          transform: 'translate(-50%, -100%)',
          zIndex: 1000,
          minWidth: '180px',
        }}>
          <div style={{ fontWeight: 650, color: 'var(--ink)', marginBottom: '2px' }}>{STAGE_FULL[tooltip.i]}</div>
          <div>{tooltip.unit === 'pages' ? 'Pages triggered' : 'Count'}: <strong>{tooltip.count.toLocaleString()}</strong></div>
          {tooltip.passRate != null && <div className="tw-muted">Pass-through: {tooltip.passRate}%</div>}
          {tooltip.unit === 'pages' && <div className="tw-muted" style={{ fontSize: '11px' }}>Distinct IPFR pages in period</div>}
        </div>
      )}
    </Card>
  );
}
