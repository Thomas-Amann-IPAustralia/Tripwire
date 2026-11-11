import React, { useMemo } from 'react';
import { PieChart, Pie, Cell } from 'recharts';
import { Card } from '../components/ui.jsx';

// Theme tokens resolve in SVG fill attributes in all supported browsers, so the
// donuts follow the light and dark themes automatically.
const REJECTED_FILL = 'var(--line-strong)';
const ERROR_FILL    = 'var(--error)';
const SKIPPED_FILL  = 'var(--surface-sunken)';
const EMPTY_FILL    = 'var(--surface-sunken)';

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
    { name: 'passed',   value: passed,   color: `var(--stage-${stage})` },
    { name: 'rejected', value: rejected, color: REJECTED_FILL },
    { name: 'errored',  value: errored,  color: ERROR_FILL },
    { name: 'skipped',  value: skipped,  color: SKIPPED_FILL },
  ].filter(d => d.value > 0);

  const chartData = slices.length > 0
    ? slices
    : [{ name: 'empty', value: 1, color: EMPTY_FILL }];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }} title={`Stage ${stage}: ${passed} passed, ${rejected} rejected, ${errored} errored, ${skipped} not reached`}>
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
            paddingAngle={slices.length > 1 ? 2 : 0}
            cornerRadius={3}
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
          fontSize: '16px', fontWeight: 700,
          color: 'var(--ink)', textAlign: 'center',
          pointerEvents: 'none', lineHeight: 1, userSelect: 'none',
        }} className="tabular">
          {total > 0 ? `${Math.round((passed / total) * 100)}%` : '—'}
        </div>
      </div>
      <div style={{ fontSize: '12px', fontWeight: 650, color: 'var(--body)', marginTop: '4px' }}>
        Stage {stage}
      </div>
      <div className="tabular" style={{ fontSize: '11px', color: 'var(--muted)' }}>
        {passed} of {total} passed
      </div>
    </div>
  );
}

export default function StageDonutGrid({ runs = [] }) {
  const donuts = useMemo(() => computeStageDonuts(runs), [runs]);

  return (
    <Card title="Stage outcomes" subtitle="Share of source checks that passed each stage.">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '14px 8px' }}>
        {donuts.map(d => (
          <StageDonut key={d.stage} {...d} />
        ))}
      </div>

      <div style={{ display: 'flex', gap: '14px', marginTop: '16px', flexWrap: 'wrap' }}>
        {[
          { label: 'Passed (stage colour)', color: 'linear-gradient(135deg, var(--stage-1), var(--stage-4))' },
          { label: 'Rejected', color: REJECTED_FILL },
          { label: 'Errored',  color: ERROR_FILL },
          { label: 'Not reached', color: SKIPPED_FILL, border: true },
        ].map(({ label, color, border }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div style={{
              width: 9, height: 9, borderRadius: '50%', flexShrink: 0, background: color,
              border: border ? '1px solid var(--line-strong)' : 'none',
            }} />
            <span style={{ fontSize: '11.5px', color: 'var(--muted)' }}>{label}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
