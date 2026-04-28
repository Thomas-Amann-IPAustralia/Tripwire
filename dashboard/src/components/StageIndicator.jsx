import React from 'react';

// Nine coloured circles scaled by pass rate (0–1). Minimal — no interaction.
export function StageIndicator({ passRates = [] }) {
  const rates = passRates.length === 9 ? passRates : Array(9).fill(0);
  return (
    <div style={{ display: 'flex', gap: '2px', alignItems: 'flex-end' }}>
      {rates.map((rate, i) => {
        const size = Math.max(2, Math.round(rate * 10));
        return (
          <div
            key={i}
            style={{
              width: `${size}px`,
              height: `${size}px`,
              borderRadius: '50%',
              background: `var(--stage-${i + 1})`,
              opacity: rate > 0 ? 0.85 : 0.2,
              flexShrink: 0,
            }}
          />
        );
      })}
    </div>
  );
}
