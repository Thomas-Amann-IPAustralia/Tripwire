import React, { useMemo } from 'react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { useFeedback } from '../hooks/useData.js';
import { Card, EmptyState, chartAxisTick, chartTooltipStyle } from '../components/ui.jsx';

const CATEGORIES = ['useful', 'not_significant', 'wrong_amendment', 'wrong_page'];
const CAT_COLORS = {
  useful:          'var(--ok)',
  not_significant: 'var(--info)',
  wrong_amendment: 'var(--warn)',
  wrong_page:      'var(--alert)',
};
const CAT_LABELS = {
  useful:          'Useful',
  not_significant: 'Not significant',
  wrong_amendment: 'Wrong amendment',
  wrong_page:      'Wrong page',
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

const TICK_STYLE = chartAxisTick;

export default function PrecisionTracker() {
  const { data: feedbackResponse } = useFeedback();
  const records = feedbackResponse?.data ?? [];

  const weeklyData = useMemo(() => computeWeeklyData(records), [records]);

  const title = 'Alert precision';
  const subtitle = 'Weekly breakdown of content-owner feedback on alerts, with the share rated useful.';

  if (records.length < 5) {
    return (
      <Card title={title} subtitle={subtitle}>
        <EmptyState icon="mail" compact title="Collecting feedback">
          Precision tracking begins once five or more alerts have been rated by content owners ({records.length} so far).
        </EmptyState>
      </Card>
    );
  }

  return (
    <Card title={title} subtitle={subtitle}>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={weeklyData} margin={{ top: 4, right: 40, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="3 4" vertical={false} />
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
            contentStyle={chartTooltipStyle}
            labelStyle={{ color: 'var(--ink)', fontWeight: 650 }}
            formatter={(v, name) => name === 'Precision' ? [`${Math.round(v * 100)}%`, name] : [v, name]}
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
              fillOpacity={0.22}
              strokeWidth={1.5}
              isAnimationActive={false}
            />
          ))}
          <Line
            yAxisId="prec"
            type="monotone"
            dataKey="precision"
            name="Precision"
            stroke="var(--ink)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div style={{ display: 'flex', gap: '14px', marginTop: '10px', flexWrap: 'wrap' }}>
        {CATEGORIES.map(cat => (
          <div key={cat} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div style={{ width: 9, height: 9, borderRadius: 2, background: CAT_COLORS[cat], opacity: 0.7, flexShrink: 0 }} />
            <span style={{ fontSize: '11.5px', color: 'var(--muted)' }}>{CAT_LABELS[cat]}</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <div style={{ width: 14, height: 2, background: 'var(--ink)', flexShrink: 0 }} />
          <span style={{ fontSize: '11.5px', color: 'var(--muted)' }}>Precision</span>
        </div>
      </div>
    </Card>
  );
}
