import React, { useMemo, useState } from 'react';
import { useDashboard } from '../App.jsx';
import { Card, EmptyState } from '../components/ui.jsx';

// stage_reached from the server tops out at 6 (stage6_complete) — S7–S9
// columns were permanently blank. The final ⚑ column marks sources whose
// runs actually bundled IPFR pages for Stages 7–9.
const STAGES = [1, 2, 3, 4, 5, 6];
const STAGE_SHORT = ['1', '2', '3', '4', '5', '6'];

function computeCell(runs, stage) {
  let passed = 0, rejected = 0, errored = 0, unreached = 0;
  for (const run of runs) {
    const sr = run.stage_reached ?? 0;
    if (sr < stage) {
      unreached++;
    } else if (sr > stage) {
      passed++;
    } else {
      if (run.outcome === 'error') errored++;
      else if (run.outcome === 'completed') passed++;
      else rejected++;
    }
  }
  return { passed, rejected, errored, unreached };
}

function cellColor(cell, stage) {
  const { passed, rejected, errored } = cell;
  const reached = passed + rejected + errored;
  if (reached === 0) return { bg: 'var(--surface-sunken)', opacity: 1 };
  if (errored > 0 && errored >= passed && errored >= rejected) {
    return { bg: 'var(--state-error)', opacity: 1 };
  }
  if (passed >= rejected) return { bg: `var(--stage-${stage})`, opacity: 1 };
  return { bg: `var(--stage-${stage})`, opacity: 0.45 };
}

const CELL_W  = 26;
const CELL_H  = 22;
const BAR_W   = 4;
const NAME_W  = 170;
const LEFT_W  = BAR_W + 8 + NAME_W;

export default function SourceMatrix({ runs = [], sources = [] }) {
  const { setSources: setFilterSources } = useDashboard();
  const [hoveredCell, setHoveredCell] = useState(null);
  const [hoveredRow,  setHoveredRow]  = useState(null);

  const sourceList = useMemo(() => {
    if (sources.length > 0) return sources;
    const ids = [...new Set(runs.map(r => r.source_id).filter(Boolean))];
    return ids.map(id => ({ source_id: id, importance: 0.5 }));
  }, [sources, runs]);

  const runsBySource = useMemo(() => {
    const map = new Map();
    for (const run of runs) {
      const src = run.source_id;
      if (!src) continue;
      if (!map.has(src)) map.set(src, []);
      map.get(src).push(run);
    }
    return map;
  }, [runs]);

  const matrixData = useMemo(() => {
    return sourceList.map(src => {
      const srcId     = src.source_id ?? src.id ?? '';
      const srcRuns   = runsBySource.get(srcId) || [];
      const importance = Math.min(1, Math.max(0, parseFloat(src.importance) || 0));
      const cells     = STAGES.map(stage => computeCell(srcRuns, stage));
      const triggered = srcRuns.filter(r => (r.triggered_pages?.length ?? 0) > 0).length;
      return { srcId, importance, cells, triggered };
    });
  }, [sourceList, runsBySource]);

  const subtitle = 'Outcome per source at each stage. Bright cells passed, faded cells were mostly rejected, red cells errored. Select a source to filter.';

  if (matrixData.length === 0) {
    return (
      <Card title="Source matrix" subtitle={subtitle}>
        <EmptyState icon="grid" compact title="No data in this period" />
      </Card>
    );
  }

  return (
    <Card title="Source matrix" subtitle={subtitle} style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ overflowY: 'auto', overflowX: 'auto', maxHeight: '440px' }}>
        {/* Column headers */}
        <div style={{
          display: 'flex', alignItems: 'center', marginBottom: '2px',
          position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1, paddingBottom: '4px',
        }}>
          <div style={{ width: LEFT_W, flexShrink: 0, fontSize: '11px', color: 'var(--muted)' }}>Source · stage</div>
          {STAGES.map((s, i) => (
            <div key={s} style={{
              width: CELL_W, flexShrink: 0, textAlign: 'center',
              fontSize: '11px', fontWeight: 700,
              color: `var(--stage-${s})`,
            }}>
              {STAGE_SHORT[i]}
            </div>
          ))}
          <div title="Runs that triggered IPFR pages (Stage 7+)" style={{
            width: CELL_W, flexShrink: 0, textAlign: 'center',
            fontSize: '11px', fontWeight: 700,
            color: 'var(--alert)',
          }}>
            ⚑
          </div>
        </div>

        {/* Data rows */}
        {matrixData.map(({ srcId, importance, cells, triggered }) => (
          <div
            key={srcId}
            style={{
              display: 'flex', alignItems: 'center',
              height: CELL_H, cursor: 'pointer',
              background: hoveredRow === srcId ? 'var(--surface-hover)' : 'transparent',
              borderRadius: '4px',
            }}
            onMouseEnter={() => setHoveredRow(srcId)}
            onMouseLeave={() => setHoveredRow(null)}
            onClick={() => setFilterSources([srcId])}
          >
            {/* Importance bar */}
            <div style={{
              width: BAR_W, height: CELL_H, flexShrink: 0,
              position: 'relative', overflow: 'hidden',
              background: 'var(--surface-sunken)', borderRadius: '2px',
            }} title={`Importance ${importance.toFixed(2)}`}>
              <div style={{
                position: 'absolute', bottom: 0, left: 0,
                width: BAR_W,
                height: `${importance * 100}%`,
                background: 'var(--accent)',
              }} />
            </div>

            <div style={{ width: 8, flexShrink: 0 }} />

            {/* Source name */}
            <div style={{
              width: NAME_W, flexShrink: 0, overflow: 'hidden',
              fontFamily: 'var(--font-code)', fontSize: '10.5px',
              color: 'var(--body)', whiteSpace: 'nowrap',
              textOverflow: 'ellipsis', paddingRight: '6px',
            }} title={srcId}>
              {srcId}
            </div>

            {/* Stage cells */}
            {cells.map((cell, i) => {
              const stage = i + 1;
              const { bg, opacity } = cellColor(cell, stage);
              const isHovered = hoveredCell?.srcId === srcId && hoveredCell?.stage === stage;
              return (
                <div
                  key={stage}
                  style={{ width: CELL_W, height: CELL_H, flexShrink: 0, padding: '2px' }}
                  onMouseEnter={e => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    setHoveredCell({ srcId, stage, cell, x: rect.left + rect.width / 2, y: rect.top });
                  }}
                  onMouseLeave={() => setHoveredCell(null)}
                  onClick={e => e.stopPropagation()}
                >
                  <div style={{
                    width: '100%', height: '100%',
                    background: bg, opacity,
                    borderRadius: '4px',
                    outline: isHovered ? '2px solid var(--accent)' : 'none',
                    outlineOffset: '1px',
                  }} />
                </div>
              );
            })}

            {/* Triggered (Stage 7+) count */}
            <div
              title={triggered > 0 ? `${triggered} run${triggered !== 1 ? 's' : ''} triggered IPFR pages` : 'No triggers'}
              style={{
                width: CELL_W, height: CELL_H, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '11px', fontWeight: triggered > 0 ? 700 : 400,
                color: triggered > 0 ? 'var(--alert)' : 'var(--faint)',
              }}
            >
              {triggered > 0 ? triggered : '·'}
            </div>
          </div>
        ))}
      </div>

      {/* Cell tooltip */}
      {hoveredCell && (
        <div className="tw-chart-tip" style={{
          position: 'fixed',
          top: hoveredCell.y - 8,
          left: hoveredCell.x,
          transform: 'translate(-50%, -100%)',
          zIndex: 1200,
          minWidth: '160px',
        }}>
          <div style={{ fontWeight: 650, color: 'var(--ink)' }}>Stage {hoveredCell.stage}</div>
          <div className="tw-code" style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '4px' }}>{hoveredCell.srcId}</div>
          <div>Passed: <strong>{hoveredCell.cell.passed}</strong></div>
          <div>Rejected: <strong>{hoveredCell.cell.rejected}</strong></div>
          <div style={{ color: 'var(--error)' }}>Errored: <strong>{hoveredCell.cell.errored}</strong></div>
          <div className="tw-muted">Not reached: {hoveredCell.cell.unreached}</div>
        </div>
      )}
    </Card>
  );
}
