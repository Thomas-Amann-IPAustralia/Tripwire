import React, { useState } from 'react';
import { computeFunnelCounts } from '../lib/dataUtils.js';

const STAGE_SHORT = ['PROBE', 'DETECT', 'DIFF', 'RELV', 'BIENC', 'CROSS', 'AGG'];
const STAGE_FULL  = [
  'Stage 1: Metadata Probe',
  'Stage 2: Change Detection',
  'Stage 3: Diff Generation',
  'Stage 4: Relevance Scoring',
  'Stage 5: Bi-Encoder Chunking',
  'Stage 6: Cross-Encoder Reranking',
  'Stage 7: Trigger Aggregation',
];

const BAR_MAX_H = 80;
const PANEL_LABEL_H = 32;

export default function FunnelSummary({ runs = [], onStageClick }) {
  const [tooltip, setTooltip] = useState(null);

  const funnel = computeFunnelCounts(runs);
  const maxCount = Math.max(...funnel.map(d => d.count), 1);

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--rule)',
      padding: '16px',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: '10px',
        color: 'var(--text-tertiary)',
        letterSpacing: '0.1em',
        marginBottom: '16px',
      }}>
        PIPELINE FUNNEL
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'flex-end',
        height: `${BAR_MAX_H + PANEL_LABEL_H + 20}px`,
      }}>
        {funnel.map((d, i) => {
          const barH = Math.max(2, Math.round((d.count / maxCount) * BAR_MAX_H));
          const isPageStage = d.unit === 'pages';
          // Only show pass-through rate between same-unit consecutive stages (S1–S6)
          const prevCount = (!isPageStage && i > 0 && funnel[i - 1].unit === 'runs')
            ? funnel[i - 1].count
            : null;
          const passRate = prevCount != null && prevCount > 0
            ? ((d.count / prevCount) * 100).toFixed(1)
            : null;

          return (
            <React.Fragment key={d.stage}>
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  flex: 1,
                  cursor: isPageStage ? 'default' : 'pointer',
                  position: 'relative',
                  height: `${BAR_MAX_H + PANEL_LABEL_H + 20}px`,
                  justifyContent: 'flex-end',
                }}
                onMouseEnter={e => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  setTooltip({ stage: d.stage, count: d.count, unit: d.unit, passRate, i, x: rect.left + rect.width / 2, y: rect.top });
                }}
                onMouseLeave={() => setTooltip(null)}
                onClick={() => !isPageStage && onStageClick?.(d.stage)}
              >
                {/* Count */}
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11px',
                  color: 'var(--text-secondary)',
                  marginBottom: '4px',
                  lineHeight: 1,
                }}>
                  {d.count}
                </div>
                {/* Bar */}
                <div style={{
                  width: '60%',
                  minWidth: '8px',
                  height: `${barH}px`,
                  background: isPageStage ? 'var(--state-warn)' : `var(--stage-${d.stage})`,
                  transition: 'opacity 120ms ease',
                  opacity: tooltip?.stage === d.stage ? 1 : 0.75,
                }} />
                {/* Stage number */}
                <div style={{
                  fontFamily: 'var(--font-display)',
                  fontSize: '10px',
                  color: 'var(--text-secondary)',
                  marginTop: '4px',
                  letterSpacing: '0.04em',
                }}>
                  S{d.stage}
                </div>
                {/* Stage short name */}
                <div style={{
                  fontFamily: 'var(--font-display)',
                  fontSize: '9px',
                  color: 'var(--text-tertiary)',
                  letterSpacing: '0.04em',
                  textAlign: 'center',
                }}>
                  {STAGE_SHORT[i]}
                </div>
                {/* Unit badge for S7 */}
                {isPageStage && (
                  <div style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '8px',
                    color: 'var(--state-warn)',
                    letterSpacing: '0.04em',
                    textAlign: 'center',
                    marginTop: '2px',
                  }}>
                    pages
                  </div>
                )}
              </div>

              {/* Separator */}
              {i < funnel.length - 1 && (
                <div style={{
                  color: 'var(--text-tertiary)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '16px',
                  paddingBottom: `${PANEL_LABEL_H + 4}px`,
                  flexShrink: 0,
                  lineHeight: 1,
                }}>
                  ›
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Fixed-position tooltip */}
      {tooltip && (
        <div style={{
          position: 'fixed',
          top: tooltip.y - 8,
          left: tooltip.x,
          transform: 'translate(-50%, -100%)',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '8px 10px',
          zIndex: 1000,
          pointerEvents: 'none',
          minWidth: '160px',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            {STAGE_FULL[tooltip.i]}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)' }}>
            {tooltip.unit === 'pages' ? 'Pages triggered' : 'Count'}: {tooltip.count}
          </div>
          {tooltip.passRate != null && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
              Pass-through: {tooltip.passRate}%
            </div>
          )}
          {tooltip.unit === 'pages' && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
              Distinct IPFR pages in period
            </div>
          )}
        </div>
      )}
    </div>
  );
}
