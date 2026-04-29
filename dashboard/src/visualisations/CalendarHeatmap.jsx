import React, { useMemo, useState } from 'react';
import { scaleSequential } from 'd3-scale';
import { timeDays, timeWeeks } from 'd3-time';

// Interpolate from --bg-tertiary (#242420) to --state-alert (#c94020)
function heatInterpolator(t) {
  if (t <= 0) return '#242420';
  const r = Math.round(0x24 + (0xc9 - 0x24) * t);
  const g = Math.round(0x24 + (0x40 - 0x24) * t);
  const b = 0x20;
  return `rgb(${r},${g},${b})`;
}

const CELL = 12;
const GAP  = 2;
const STEP = CELL + GAP;
const DOW_LABEL = { 1: 'M', 3: 'W', 5: 'F' };

const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

export default function CalendarHeatmap({ runs = [], onDayClick }) {
  const [tooltip, setTooltip] = useState(null);

  // Build alert map: dateStr → { count, sources[] }
  const alertMap = useMemo(() => {
    const map = new Map();
    for (const run of runs) {
      if (run.verdict?.toUpperCase() !== 'CHANGE_REQUIRED') continue;
      const date = run.run_at?.slice(0, 10);
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

  const colorScale = useMemo(
    () => scaleSequential(heatInterpolator).domain([0, Math.max(1, maxCount)]),
    [maxCount]
  );

  const today = new Date();
  today.setHours(23, 59, 59, 999);

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--rule)',
      padding: '16px',
      overflowX: 'auto',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: '10px',
        color: 'var(--text-tertiary)',
        letterSpacing: '0.1em',
        marginBottom: '12px',
      }}>
        ALERT DENSITY — CHANGE REQUIRED · 52 WEEKS
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        {/* Day-of-week labels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: `${GAP}px`, paddingTop: '18px' }}>
          {[0, 1, 2, 3, 4, 5, 6].map(dow => (
            <div key={dow} style={{
              height: `${CELL}px`,
              width: '12px',
              display: 'flex',
              alignItems: 'center',
              fontFamily: 'var(--font-mono)',
              fontSize: '9px',
              color: 'var(--text-tertiary)',
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
                  fontFamily: 'var(--font-mono)',
                  fontSize: '9px',
                  color: 'var(--text-tertiary)',
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
                  const color   = future ? '#1a1a18' : colorScale(count);

                  return (
                    <div
                      key={dateStr}
                      title={dateStr}
                      style={{
                        width: `${CELL}px`,
                        height: `${CELL}px`,
                        background: color,
                        cursor: future ? 'default' : 'pointer',
                        outline: tooltip?.dateStr === dateStr ? '1px solid var(--rule-accent)' : 'none',
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

      {/* Tooltip */}
      {tooltip && (
        <div style={{
          position: 'fixed',
          top: tooltip.y,
          left: tooltip.x,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '8px 10px',
          zIndex: 1000,
          pointerEvents: 'none',
          minWidth: '140px',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            {tooltip.dateStr}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            {tooltip.count} alert{tooltip.count !== 1 ? 's' : ''}
          </div>
          {tooltip.sources.slice(0, 3).map(s => (
            <div key={s} style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
              {s}
            </div>
          ))}
          {tooltip.sources.length > 3 && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
              +{tooltip.sources.length - 3} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}
