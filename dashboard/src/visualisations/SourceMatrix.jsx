import React, { useMemo, useState } from 'react';
import { useDashboard } from '../App.jsx';

const STAGES = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const STAGE_SHORT = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'];

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
  if (reached === 0) return { bg: 'var(--bg-tertiary)', opacity: 1 };
  if (errored > 0 && errored >= passed && errored >= rejected) {
    return { bg: 'var(--state-error)', opacity: 1 };
  }
  if (passed >= rejected) return { bg: `var(--stage-${stage})`, opacity: 1 };
  return { bg: `var(--stage-${stage})`, opacity: 0.5 };
}

const CELL_W  = 22;
const CELL_H  = 18;
const BAR_W   = 8;
const NAME_W  = 130;
const LEFT_W  = BAR_W + 4 + NAME_W;

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
      return { srcId, importance, cells };
    });
  }, [sourceList, runsBySource]);

  if (matrixData.length === 0) {
    return (
      <div className="panel" style={{
        background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
        padding: '16px', minHeight: '80px', display: 'flex', alignItems: 'center',
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
          SOURCE MATRIX — no data
        </span>
      </div>
    );
  }

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
      padding: '16px', display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
        letterSpacing: '0.1em', marginBottom: '10px', flexShrink: 0,
      }}>
        SOURCE MATRIX
      </div>

      <div style={{ overflowY: 'auto', overflowX: 'auto' }}>
        {/* Column headers */}
        <div style={{
          display: 'flex', alignItems: 'center', marginBottom: '2px',
          position: 'sticky', top: 0, background: 'var(--bg-secondary)', zIndex: 1,
        }}>
          <div style={{ width: LEFT_W, flexShrink: 0 }} />
          {STAGES.map((s, i) => (
            <div key={s} style={{
              width: CELL_W, flexShrink: 0, textAlign: 'center',
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: `var(--stage-${s})`, letterSpacing: '0.06em',
            }}>
              {STAGE_SHORT[i]}
            </div>
          ))}
        </div>

        {/* Data rows */}
        {matrixData.map(({ srcId, importance, cells }) => (
          <div
            key={srcId}
            style={{
              display: 'flex', alignItems: 'center',
              height: CELL_H, cursor: 'pointer',
              background: hoveredRow === srcId ? 'rgba(46,46,40,0.6)' : 'transparent',
            }}
            onMouseEnter={() => setHoveredRow(srcId)}
            onMouseLeave={() => setHoveredRow(null)}
            onClick={() => setFilterSources([srcId])}
          >
            {/* Importance bar */}
            <div style={{
              width: BAR_W, height: CELL_H, flexShrink: 0,
              position: 'relative', overflow: 'hidden',
              background: 'var(--bg-tertiary)',
            }}>
              <div style={{
                position: 'absolute', bottom: 0, left: 0,
                width: BAR_W,
                height: `${importance * 100}%`,
                background: 'var(--stage-4)',
              }} />
            </div>

            <div style={{ width: 4, flexShrink: 0 }} />

            {/* Source name */}
            <div style={{
              width: NAME_W, flexShrink: 0, overflow: 'hidden',
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-secondary)', whiteSpace: 'nowrap',
              textOverflow: 'ellipsis', paddingRight: '6px',
            }}>
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
                  style={{ width: CELL_W, height: CELL_H, flexShrink: 0, padding: '1px' }}
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
                    outline: isHovered ? '1px solid var(--rule-accent)' : 'none',
                  }} />
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Cell tooltip */}
      {hoveredCell && (
        <div style={{
          position: 'fixed',
          top: hoveredCell.y - 8,
          left: hoveredCell.x,
          transform: 'translate(-50%, -100%)',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '8px 10px',
          zIndex: 1200,
          pointerEvents: 'none',
          minWidth: '140px',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            {hoveredCell.srcId} · S{hoveredCell.stage}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)' }}>
            Passed: {hoveredCell.cell.passed}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
            Rejected: {hoveredCell.cell.rejected}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--state-error)' }}>
            Errored: {hoveredCell.cell.errored}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
            Unreached: {hoveredCell.cell.unreached}
          </div>
        </div>
      )}
    </div>
  );
}
