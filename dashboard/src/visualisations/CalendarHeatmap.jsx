import React, { useMemo, useState } from 'react';
import { timeDays, timeWeeks } from 'd3-time';
import { Card } from '../components/ui.jsx';

// Five-step sequential scale from the theme tokens (--seq-0 … --seq-4), so the
// grid reads correctly in both the warm light and dark themes.
function levelFor(count, max) {
  if (!count) return 0;
  if (max <= 1) return 4;
  return Math.min(4, 1 + Math.floor(((count - 1) / (max - 1)) * 3.999));
}

const CELL = 12;
const GAP  = 3;
const STEP = CELL + GAP;
const DOW_LABEL = { 1: 'M', 3: 'W', 5: 'F' };

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

export default function CalendarHeatmap({ runs = [], onDayClick }) {
  const [tooltip, setTooltip] = useState(null);

  // Build alert map: dateStr → { count, sources[] }
  const alertMap = useMemo(() => {
    const map = new Map();
    for (const run of runs) {
      if (run.verdict?.toUpperCase() !== 'CHANGE_REQUIRED') continue;
      const date = (run.timestamp ?? run.run_at)?.slice(0, 10);
      if (!date) continue;
      if (!map.has(date)) map.set(date, { count: 0, sources: [] });
      const entry = map.get(date);
      entry.count++;
      const src = run.source_id || run.source;
      if (src && !entry.sources.includes(src)) entry.sources.push(src);
    }
    return map;
  }, [runs]);

  const maxCount = useMemo(() => {
    let m = 0;
    alertMap.forEach(v => { if (v.count > m) m = v.count; });
    return m;
  }, [alertMap]);

  // Build 52-week grid using d3-time
  const { weeks, monthLabels } = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // End: end of current week (Saturday)
    const end = new Date(today);
    end.setDate(today.getDate() + (6 - today.getDay()) + 1); // exclusive end for timeDays

    // Start: 52 weeks back from the Sunday that starts the current week
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay()); // this week's Sunday
    const start = new Date(weekStart);
    start.setDate(start.getDate() - 51 * 7);

    const allDays = timeDays(start, end);
    const weekBoundaries = timeWeeks(start, end); // one Date per Sunday

    // Group days by week
    const weekArrays = weekBoundaries.map((sun, wi) => {
      const nextSun = wi + 1 < weekBoundaries.length
        ? weekBoundaries[wi + 1]
        : end;
      return allDays.filter(d => d >= sun && d < nextSun);
    });

    // Month labels: first week index per new month
    const labels = [];
    let lastMonth = -1;
    weekArrays.forEach((week, wi) => {
      const m = week[0]?.getMonth() ?? -1;
      if (m !== -1 && m !== lastMonth) {
        labels.push({ weekIndex: wi, label: MONTHS[m] });
        lastMonth = m;
      }
    });

    return { weeks: weekArrays, monthLabels: labels };
  }, []);

  const colorFor = count => `var(--seq-${levelFor(count, maxCount)})`;

  const today = new Date();
  today.setHours(23, 59, 59, 999);

  return (
    <Card
      title="Alert density"
      subtitle="Days with change-required verdicts over the last 52 weeks, coloured within the active date filter. Select a day to filter to it."
      bodyStyle={{ overflowX: 'auto' }}
    >
      <div style={{ display: 'flex', gap: '8px' }}>
        {/* Day-of-week labels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: `${GAP}px`, paddingTop: '18px' }}>
          {[0, 1, 2, 3, 4, 5, 6].map(dow => (
            <div key={dow} style={{
              height: `${CELL}px`,
              width: '12px',
              display: 'flex',
              alignItems: 'center',
              fontSize: '10px',
              color: 'var(--muted)',
            }}>
              {DOW_LABEL[dow] ?? ''}
            </div>
          ))}
        </div>

        {/* Calendar grid */}
        <div style={{ position: 'relative' }}>
          {/* Month labels */}
          <div style={{ position: 'relative', height: '16px', marginBottom: '2px' }}>
            {monthLabels.map(({ weekIndex, label }) => (
              <span
                key={weekIndex}
                style={{
                  position: 'absolute',
                  left: `${weekIndex * STEP}px`,
                  fontSize: '10.5px',
                  color: 'var(--muted)',
                  whiteSpace: 'nowrap',
                }}
              >
                {label}
              </span>
            ))}
          </div>

          {/* Week columns */}
          <div style={{ display: 'flex', gap: `${GAP}px` }}>
            {weeks.map((week, wi) => (
              <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: `${GAP}px` }}>
                {/* Pad missing days at top (first week may start mid-week) */}
                {Array.from({ length: 7 - week.length }, (_, k) => (
                  <div key={`pad-${k}`} style={{ width: CELL, height: CELL, background: 'transparent' }} />
                ))}
                {week.map(date => {
                  const dateStr = date.toISOString().slice(0, 10);
                  const entry   = alertMap.get(dateStr);
                  const count   = entry?.count ?? 0;
                  const srcs    = entry?.sources ?? [];
                  const future  = date > today;
                  const color   = future ? 'transparent' : colorFor(count);

                  return (
                    <div
                      key={dateStr}
                      title={dateStr}
                      style={{
                        width: `${CELL}px`,
                        height: `${CELL}px`,
                        background: color,
                        borderRadius: '3px',
                        border: future ? '1px dashed var(--line)' : '1px solid color-mix(in srgb, var(--ink) 6%, transparent)',
                        cursor: future ? 'default' : 'pointer',
                        outline: tooltip?.dateStr === dateStr ? '2px solid var(--accent)' : 'none',
                        outlineOffset: '1px',
                      }}
                      onMouseEnter={e => {
                        if (future) return;
                        const rect = e.currentTarget.getBoundingClientRect();
                        setTooltip({ dateStr, count, sources: srcs, x: rect.right + 4, y: rect.top });
                      }}
                      onMouseLeave={() => setTooltip(null)}
                      onClick={() => { if (!future) onDayClick?.(dateStr); }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginTop: '14px', fontSize: '11px', color: 'var(--muted)' }}>
        Fewer
        {[0, 1, 2, 3, 4].map(l => (
          <span key={l} style={{ width: 11, height: 11, borderRadius: 3, background: `var(--seq-${l})`, border: '1px solid color-mix(in srgb, var(--ink) 6%, transparent)' }} />
        ))}
        More alerts
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div className="tw-chart-tip" style={{
          position: 'fixed',
          top: tooltip.y,
          left: tooltip.x,
          zIndex: 1000,
          minWidth: '150px',
        }}>
          <div style={{ fontWeight: 650, color: 'var(--ink)' }}>
            {new Date(tooltip.dateStr + 'T00:00:00').toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}
          </div>
          <div style={{ marginBottom: '2px' }}>
            {tooltip.count} alert{tooltip.count !== 1 ? 's' : ''}
          </div>
          {tooltip.sources.slice(0, 3).map(s => (
            <div key={s} className="tw-code" style={{ fontSize: '11px', color: 'var(--muted)' }}>{s}</div>
          ))}
          {tooltip.sources.length > 3 && (
            <div className="tw-muted" style={{ fontSize: '11px' }}>+{tooltip.sources.length - 3} more</div>
          )}
        </div>
      )}
    </Card>
  );
}
