import React, { useMemo } from 'react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { useFeedback } from '../hooks/useData.js';

const CATEGORIES = ['useful', 'not_significant', 'wrong_amendment', 'wrong_page'];
const CAT_COLORS = {
  useful:          '#3a6b3a',
  not_significant: '#4a7ab5',
  wrong_amendment: '#d4a820',
  wrong_page:      '#c94020',
};
const CAT_LABELS = {
  useful:          'Useful',
  not_significant: 'Not Significant',
  wrong_amendment: 'Wrong Amendment',
  wrong_page:      'Wrong Page',
};

function weekStart(isoStr) {
  if (!isoStr) return null;
  const d = new Date(isoStr);
  if (isNaN(d)) return null;
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - d.getDay());
  return d.toISOString().slice(0, 10);
}

function computeWeeklyData(records) {
  const map = new Map();
  for (const fb of records) {
    const week = weekStart(fb.ingested_at);
    if (!week) continue;
    if (!map.has(week)) {
      map.set(week, { week, useful: 0, not_significant: 0, wrong_amendment: 0, wrong_page: 0, total: 0 });
    }
    const entry = map.get(week);
    entry.total++;
    const cat = fb.category;
    if (CATEGORIES.includes(cat)) entry[cat]++;
  }
  return Array.from(map.values())
    .sort((a, b) => a.week.localeCompare(b.week))
    .map(e => ({ ...e, precision: e.total > 0 ? e.useful / e.total : 0 }));
}

const TICK_STYLE = { fontFamily: 'DM Mono, monospace', fontSize: 9, fill: '#5c5a52' };

export default function PrecisionTracker() {
  const { data: feedbackResponse } = useFeedback();
  const records = feedbackResponse?.data ?? [];

  const weeklyData = useMemo(() => computeWeeklyData(records), [records]);

  if (records.length < 5) {
    return (
      <div className="panel" style={{
        background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
        padding: '16px', minHeight: '120px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <span style={{
          fontFamily: 'var(--font-display)', fontSize: '14px',
          color: 'var(--text-tertiary)', textAlign: 'center',
          letterSpacing: '0.05em',
        }}>
          COLLECTING FEEDBACK — PRECISION TRACKING BEGINS WITH 5+ RATED ALERTS
        </span>
      </div>
    );
  }

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
      padding: '16px',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
        letterSpacing: '0.1em', marginBottom: '12px',
      }}>
        ALERT PRECISION — WEEKLY FEEDBACK BREAKDOWN
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={weeklyData} margin={{ top: 4, right: 40, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#2e2e28" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="week"
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="count"
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={false}
            width={24}
          />
          <YAxis
            yAxisId="prec"
            orientation="right"
            tickFormatter={v => `${Math.round(v * 100)}%`}
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={false}
            domain={[0, 1]}
            width={32}
          />
          <RechartsTooltip
            contentStyle={{
              background: '#1a1a18', border: '1px solid #4a4a40',
              fontFamily: 'DM Mono, monospace', fontSize: 10, color: '#e8e2d4',
            }}
            labelStyle={{ color: '#5c5a52' }}
          />
          {CATEGORIES.map(cat => (
            <Area
              key={cat}
              yAxisId="count"
              type="monotone"
              dataKey={cat}
              name={CAT_LABELS[cat]}
              stackId="cats"
              stroke={CAT_COLORS[cat]}
              fill={CAT_COLORS[cat]}
              fillOpacity={0.3}
              strokeWidth={1}
              isAnimationActive={false}
            />
          ))}
          <Line
            yAxisId="prec"
            type="monotone"
            dataKey="precision"
            name="Precision"
            stroke="#e8e2d4"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div style={{ display: 'flex', gap: '12px', marginTop: '8px', flexWrap: 'wrap' }}>
        {CATEGORIES.map(cat => (
          <div key={cat} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: 8, height: 8, background: CAT_COLORS[cat], opacity: 0.6, flexShrink: 0 }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)' }}>
              {CAT_LABELS[cat]}
            </span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <div style={{ width: 12, height: 1.5, background: '#e8e2d4', flexShrink: 0 }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)' }}>
            Precision
          </span>
        </div>
      </div>
    </div>
  );
}
