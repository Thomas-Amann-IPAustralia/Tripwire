import React from 'react';

// New API: stages = [{ n, reached, status: 'passed'|'failed'|'error'|'skipped' }]
// Legacy API: passRates = [0..1, ...] (9 floats, used by TimelineSwimLane mini-funnel)
export function StageIndicator({ stages, passRates, size = 1 }) {
  const dotPx = Math.max(4, Math.round(8 * size));
  const gap   = Math.max(1, Math.round(2 * size));

  let dots;

  if (stages) {
    dots = stages.map(({ n, reached, status }) => {
      if (!reached) {
        return {
          key: n,
          style: {
            width: dotPx, height: dotPx,
            borderRadius: '50%',
            background: 'transparent',
            border: '1px solid var(--bg-accent)',
            flexShrink: 0,
          },
        };
      }
      let bg;
      let opacity = 1;
      switch (status) {
        case 'passed':
          bg = `var(--stage-${n})`;
          break;
        case 'failed':
          bg = `var(--stage-${n})`;
          opacity = 0.5;
          break;
        case 'error':
          bg = 'var(--state-error)';
          break;
        case 'skipped':
          bg = 'var(--text-tertiary)';
          opacity = 0.4;
          break;
        default:
          bg = `var(--stage-${n})`;
          opacity = 0.7;
      }
      return {
        key: n,
        style: {
          width: dotPx, height: dotPx,
          borderRadius: '50%',
          background: bg,
          opacity,
          flexShrink: 0,
        },
      };
    });
  } else {
    // Legacy passRates path — variable dot sizes for mini-funnel
    const rates = passRates?.length === 9 ? passRates : Array(9).fill(0);
    dots = rates.map((rate, i) => {
      const sz = Math.max(2, Math.round(rate * dotPx * 1.25));
      return {
        key: i + 1,
        style: {
          width: sz, height: sz,
          borderRadius: '50%',
          background: `var(--stage-${i + 1})`,
          opacity: rate > 0 ? 0.85 : 0.2,
          flexShrink: 0,
        },
      };
    });
  }

  return (
    <div style={{ display: 'flex', gap: `${gap}px`, alignItems: 'center' }}>
      {dots.map(({ key, style }) => (
        <div key={key} style={style} />
      ))}
    </div>
  );
}
