import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip as RechartsTooltip,
} from 'recharts';
import { StageIndicator } from '../components/StageIndicator.jsx';
import { useDashboard } from '../App.jsx';

const LABEL_W     = 148;
const FUNNEL_W    = 96;
const ROW_H       = 20;
const TICK_H      = 12;
const CHART_H     = 120;
const CHART_HDR_H = 32;
const PPD_SCROLL  = 20; // pixels-per-day when scrolling (> 30 days)
const VIRT_PAD    = 200; // px beyond viewport to render

// ---- Helpers ---------------------------------------------------------------

function resolvedDateRange(filters, runs) {
  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);

  if (filters.from && filters.to) return { from: filters.from, to: filters.to };

  const offsets = { '7D': 7, '30D': 30, '90D': 90 };
  if (filters.datePreset && offsets[filters.datePreset]) {
    const from = new Date(now);
    from.setDate(from.getDate() - offsets[filters.datePreset]);
    return { from: from.toISOString().slice(0, 10), to: todayStr };
  }

  if (!runs?.length) {
    const from = new Date(now);
    from.setDate(from.getDate() - 30);
    return { from: from.toISOString().slice(0, 10), to: todayStr };
  }

  const dates = runs.map(r => r.run_at?.slice(0, 10)).filter(Boolean).sort();
  return { from: dates[0], to: dates[dates.length - 1] };
}

function daysBetween(from, to) {
  return Math.max(1, Math.round((new Date(to) - new Date(from)) / 86_400_000) + 1);
}

function dateToX(dateStr, fromStr, ppd) {
  const days = Math.round((new Date(dateStr) - new Date(fromStr)) / 86_400_000);
  return days * ppd;
}

function computePassRates(srcRuns) {
  if (!srcRuns?.length) return Array(9).fill(0);
  const total = srcRuns.length;
  const counts = new Array(9).fill(0);
  for (const run of srcRuns) {
    const stage = run.deepest_stage ?? run.stage_reached ?? 0;
    for (let s = 0; s < stage; s++) counts[s]++;
  }
  return counts.map(c => c / total);
}

function truncate28(str) {
  return str.length > 28 ? str.slice(0, 28) : str;
}

// ---- Chart data helpers ----------------------------------------------------

function aggregateDaily(runs) {
  const map = new Map();
  for (const run of runs) {
    const d = run.run_at?.slice(0, 10);
    if (!d) continue;
    if (!map.has(d)) map.set(d, { date: d, alerts: 0, total: 0 });
    const e = map.get(d);
    e.total++;
    if (run.verdict?.toUpperCase() === 'CHANGE_REQUIRED') e.alerts++;
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function aggregateWeekly(daily) {
  const map = new Map();
  for (const d of daily) {
    const dt = new Date(d.date);
    dt.setDate(dt.getDate() - dt.getDay());
    const key = dt.toISOString().slice(0, 10);
    if (!map.has(key)) map.set(key, { date: key, alerts: 0, total: 0 });
    const e = map.get(key);
    e.alerts += d.alerts;
    e.total  += d.total;
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function aggregateMonthly(daily) {
  const map = new Map();
  for (const d of daily) {
    const key = d.date.slice(0, 7);
    if (!map.has(key)) map.set(key, { date: key, alerts: 0, total: 0 });
    const e = map.get(key);
    e.alerts += d.alerts;
    e.total  += d.total;
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
}

const chartTickStyle = { fontFamily: 'var(--font-mono)', fontSize: 9, fill: '#5c5a52' };

// ---- Component -------------------------------------------------------------

export default function TimelineSwimLane({ runs = [], sources = [] }) {
  const { filters, setDateRange, setSelectedRunId, setDrawerOpen } = useDashboard();

  const scrollRef    = useRef(null);
  const containerRef = useRef(null);

  const [scrollLeft,     setScrollLeft]     = useState(0);
  const [containerWidth, setContainerWidth] = useState(900);
  const [showAll,        setShowAll]        = useState(false);
  const [hoveredTick,    setHoveredTick]    = useState(null);
  const [zoomLevel,      setZoomLevel]      = useState('DAY');

  // Measure container
  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver(entries => {
      setContainerWidth(entries[0].contentRect.width);
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  const handleScroll = useCallback(e => {
    setScrollLeft(e.currentTarget.scrollLeft);
  }, []);

  // Time range
  const { from: fromStr, to: toStr } = useMemo(
    () => resolvedDateRange(filters, runs),
    [filters, runs],
  );

  const numDays = useMemo(() => daysBetween(fromStr, toStr), [fromStr, toStr]);

  const contentW = containerWidth - LABEL_W - FUNNEL_W;
  const shouldScroll = numDays > 30;
  const ppd = shouldScroll
    ? PPD_SCROLL
    : Math.max(1, contentW / numDays);
  const totalW = Math.ceil(numDays * ppd);

  // Source → run list
  const sourceMap = useMemo(() => {
    const m = new Map();
    for (const run of runs) {
      const src = run.source_id || run.source;
      if (!src) continue;
      if (!m.has(src)) m.set(src, []);
      m.get(src).push(run);
    }
    return m;
  }, [runs]);

  const activeSrcList = useMemo(() => {
    if (showAll) {
      const allIds = sources.map(s => s.source_id || s.id || s.label).filter(Boolean);
      const withRuns = new Set(sourceMap.keys());
      // All known sources; put those with runs first
      return [
        ...Array.from(withRuns),
        ...allIds.filter(id => !withRuns.has(id)),
      ];
    }
    return Array.from(sourceMap.keys());
  }, [showAll, sources, sourceMap]);

  // Visible window for virtualisation
  const visFromStr = useMemo(() => {
    const dayOffset = Math.floor(Math.max(0, scrollLeft - VIRT_PAD) / ppd);
    const d = new Date(fromStr);
    d.setDate(d.getDate() + dayOffset);
    return d.toISOString().slice(0, 10);
  }, [scrollLeft, ppd, fromStr]);

  const visToStr = useMemo(() => {
    const dayOffset = Math.ceil((scrollLeft + contentW + VIRT_PAD) / ppd);
    const d = new Date(fromStr);
    d.setDate(d.getDate() + dayOffset);
    const candidate = d.toISOString().slice(0, 10);
    return candidate < toStr ? candidate : toStr;
  }, [scrollLeft, ppd, fromStr, toStr, contentW]);

  // Chart data
  const daily = useMemo(() => aggregateDaily(runs), [runs]);

  const chartData = useMemo(() => {
    if (zoomLevel === 'WEEK')  return aggregateWeekly(daily);
    if (zoomLevel === 'MONTH') return aggregateMonthly(daily);
    return daily;
  }, [daily, zoomLevel]);

  // ---- Render ---------------------------------------------------------------

  const swimlaneH = activeSrcList.length * ROW_H;
  const totalPanelH = swimlaneH + CHART_HDR_H + CHART_H + 8;

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--rule)',
      padding: '16px',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.1em' }}>
          TIMELINE
        </div>
        <button
          onClick={() => setShowAll(s => !s)}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            color: 'var(--text-tertiary)',
            background: 'transparent',
            border: '1px solid var(--rule)',
            padding: '2px 8px',
            cursor: 'pointer',
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--rule-accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--rule)'; }}
        >
          {showAll ? 'SHOW ACTIVE ONLY' : 'SHOW ALL SOURCES'}
        </button>
      </div>

      <div ref={containerRef} style={{ display: 'flex' }}>

        {/* Fixed left: source labels */}
        <div style={{ width: LABEL_W, flexShrink: 0, overflow: 'hidden' }}>
          {activeSrcList.map(src => (
            <div key={src} style={{
              height: ROW_H,
              display: 'flex',
              alignItems: 'center',
              fontFamily: 'var(--font-mono)',
              fontSize: '9px',
              color: 'var(--text-secondary)',
              whiteSpace: 'nowrap',
              paddingRight: '8px',
              borderBottom: '1px solid var(--rule)',
            }}>
              {truncate28(src)}
            </div>
          ))}
          {/* Spacer aligns with chart section */}
          <div style={{ height: CHART_HDR_H + CHART_H + 8 }} />
        </div>

        {/* Scrollable centre: ticks + chart */}
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          style={{
            flex: 1,
            overflowX: shouldScroll ? 'auto' : 'hidden',
            overflowY: 'hidden',
          }}
        >
          <div style={{ width: Math.max(totalW, contentW) }}>

            {/* Track 1: swimlane rows */}
            {activeSrcList.map((src, rowIdx) => {
              const srcRuns = sourceMap.get(src) || [];
              const visible = srcRuns.filter(r => {
                const d = r.run_at?.slice(0, 10);
                return d && d >= visFromStr && d <= visToStr;
              });
              return (
                <div
                  key={src}
                  style={{
                    position: 'relative',
                    height: ROW_H,
                    borderBottom: '1px solid var(--rule)',
                  }}
                >
                  {visible.map(run => {
                    const runDate = run.run_at?.slice(0, 10);
                    if (!runDate) return null;
                    const x     = dateToX(runDate, fromStr, ppd);
                    const stage = run.deepest_stage ?? run.stage_reached ?? 1;
                    const runId = run.run_id ?? run.id ?? rowIdx;
                    return (
                      <div
                        key={`${run.run_id ?? run.id ?? Math.random()}`}
                        style={{
                          position: 'absolute',
                          left: x,
                          top: (ROW_H - TICK_H) / 2,
                          width: 2,
                          height: TICK_H,
                          background: `var(--stage-${stage})`,
                          cursor: 'pointer',
                        }}
                        onMouseEnter={e => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          setHoveredTick({ run, x: rect.right + 4, y: rect.top });
                        }}
                        onMouseLeave={() => setHoveredTick(null)}
                        onClick={() => {
                          setSelectedRunId(runId);
                          setDrawerOpen(true);
                        }}
                      />
                    );
                  })}
                </div>
              );
            })}

            {/* Track 2: daily volume chart */}
            <div style={{ borderTop: '1px solid var(--rule)', paddingTop: '8px' }}>
              <div style={{ display: 'flex', gap: '4px', marginBottom: '6px', height: CHART_HDR_H - 6 }}>
                {['DAY', 'WEEK', 'MONTH'].map(z => (
                  <button
                    key={z}
                    onClick={() => setZoomLevel(z)}
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '10px',
                      padding: '2px 6px',
                      border: '1px solid var(--rule-accent)',
                      background: zoomLevel === z ? 'var(--bg-accent)' : 'transparent',
                      color: zoomLevel === z ? 'var(--text-primary)' : 'var(--text-tertiary)',
                      cursor: 'pointer',
                    }}
                  >
                    {z}
                  </button>
                ))}
              </div>
              <ComposedChart
                width={Math.max(totalW, contentW)}
                height={CHART_H}
                data={chartData}
                margin={{ top: 4, right: 0, bottom: 4, left: 0 }}
              >
                <XAxis
                  dataKey="date"
                  tick={chartTickStyle}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis hide />
                <RechartsTooltip
                  contentStyle={{
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--rule-accent)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '10px',
                    color: 'var(--text-primary)',
                  }}
                  labelStyle={{ color: 'var(--text-tertiary)' }}
                />
                <Bar dataKey="alerts" fill="#c94020" opacity={0.7} isAnimationActive={false} />
                <Line
                  type="monotone"
                  dataKey="total"
                  stroke="#5c5a52"
                  strokeWidth={1}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </div>
          </div>
        </div>

        {/* Fixed right: mini-funnels */}
        <div style={{ width: FUNNEL_W, flexShrink: 0, overflow: 'hidden' }}>
          {activeSrcList.map(src => {
            const passRates = computePassRates(sourceMap.get(src));
            return (
              <div key={src} style={{
                height: ROW_H,
                display: 'flex',
                alignItems: 'center',
                paddingLeft: '8px',
                borderBottom: '1px solid var(--rule)',
              }}>
                <StageIndicator passRates={passRates} />
              </div>
            );
          })}
          <div style={{ height: CHART_HDR_H + CHART_H + 8 }} />
        </div>
      </div>

      {/* Tick hover popover */}
      {hoveredTick && (
        <div style={{
          position: 'fixed',
          top: hoveredTick.y,
          left: hoveredTick.x,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--rule-accent)',
          padding: '8px 10px',
          zIndex: 1000,
          pointerEvents: 'none',
          minWidth: '180px',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            Run {hoveredTick.run.run_id ?? hoveredTick.run.id ?? '—'}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', marginBottom: '2px' }}>
            {hoveredTick.run.source_id ?? hoveredTick.run.source ?? '—'}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: `var(--stage-${hoveredTick.run.deepest_stage ?? hoveredTick.run.stage_reached ?? 1})`, marginBottom: '2px' }}>
            Stage {hoveredTick.run.deepest_stage ?? hoveredTick.run.stage_reached ?? '—'}
          </div>
          {hoveredTick.run.verdict && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)', marginBottom: '2px' }}>
              {hoveredTick.run.verdict}
            </div>
          )}
          {hoveredTick.run.score != null && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
              Score: {Number(hoveredTick.run.score).toFixed(2)}
            </div>
          )}
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
            {hoveredTick.run.run_at}
          </div>
        </div>
      )}
    </div>
  );
}
