import React, { useMemo } from 'react';
import { PieChart, Pie, Cell } from 'recharts';

// Hardcoded hex values — CSS vars don't resolve reliably in SVG fill attributes
const STAGE_HEX = {
  1: '#3a6b3a', 2: '#c94020', 3: '#d4a820', 4: '#4a7ab5',
  5: '#4a4a40', 6: '#7a7a70',
};
const BG_ACCENT_HEX  = '#2e2e28';
const STATE_ERROR_HEX = '#8b1a1a';
const TEXT_TERT_HEX  = '#5c5a52';
const BG_TERT_HEX    = '#242420';

function computeStageDonuts(runs) {
  return Array.from({ length: 6 }, (_, i) => {
    const stage = i + 1;
    let passed = 0, rejected = 0, errored = 0, skipped = 0;
    for (const run of runs) {
      const sr = run.stage_reached ?? 0;
      if (sr < stage) {
        skipped++;
      } else if (sr > stage) {
        passed++;
      } else if (run.outcome === 'completed') {
        passed++;
      } else if (run.outcome === 'error') {
        errored++;
      } else {
        rejected++;
      }
    }
    return { stage, passed, rejected, errored, skipped, total: runs.length };
  });
}

function StageDonut({ stage, passed, rejected, errored, skipped, total }) {
  const slices = [
    { name: 'passed',   value: passed,   color: STAGE_HEX[stage] },
    { name: 'rejected', value: rejected, color: BG_ACCENT_HEX },
    { name: 'errored',  value: errored,  color: STATE_ERROR_HEX },
    { name: 'skipped',  value: skipped,  color: TEXT_TERT_HEX },
  ].filter(d => d.value > 0);

  const chartData = slices.length > 0
    ? slices
    : [{ name: 'empty', value: 1, color: BG_TERT_HEX }];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <div style={{ position: 'relative', width: 80, height: 80 }}>
        <PieChart width={80} height={80}>
          <Pie
            data={chartData}
            cx={40} cy={40}
            innerRadius={26} outerRadius={36}
            startAngle={90} endAngle={-270}
            isAnimationActive={false}
            dataKey="value"
            strokeWidth={0}
          >
            {chartData.map((entry, idx) => (
              <Cell key={idx} fill={entry.color} />
            ))}
          </Pie>
        </PieChart>
        {/* Centre label overlaid as DOM element — reliable across browsers */}
        <div style={{
          position: 'absolute', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          fontFamily: 'var(--font-display)', fontSize: '20px',
          color: 'var(--text-secondary)',
          pointerEvents: 'none', lineHeight: 1, userSelect: 'none',
        }}>
          S{stage}
        </div>
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '9px',
        color: 'var(--text-secondary)', marginTop: '2px',
      }}>
        {total}
      </div>
    </div>
  );
}

export default function StageDonutGrid({ runs = [] }) {
  const donuts = useMemo(() => computeStageDonuts(runs), [runs]);

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
      padding: '16px', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
        letterSpacing: '0.1em', marginBottom: '12px', flexShrink: 0,
      }}>
        STAGE OUTCOMES
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '8px',
        flex: 1,
      }}>
        {donuts.map(d => (
          <StageDonut key={d.stage} {...d} />
        ))}
      </div>

      {/* Legend */}
      <div style={{
        display: 'flex', gap: '12px', marginTop: '10px',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        {[
          { label: 'passed',   color: BG_ACCENT_HEX, note: 'stage colour' },
          { label: 'rejected', color: BG_ACCENT_HEX },
          { label: 'errored',  color: STATE_ERROR_HEX },
          { label: 'skipped',  color: TEXT_TERT_HEX },
        ].map(({ label, color }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)' }}>
              {label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
