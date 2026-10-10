// Snapshot / unit tests for dataUtils.js
//
// BUG-005 regression: aggregateByDay keyed on run.run_at but the API returns
// run.timestamp. The calendar and sparkline were always empty.

import { describe, it, expect } from 'vitest';
import { aggregateByDay, funnelFromSummary } from '../lib/dataUtils.js';

// Rows as returned by the runs API (field is `timestamp`, not `run_at`).
const PIPELINE_RUNS = [
  { timestamp: '2025-04-01T02:00:00Z', verdict: 'NO_CHANGE' },
  { timestamp: '2025-04-01T02:05:00Z', verdict: 'CHANGE_REQUIRED' },
  { timestamp: '2025-04-02T02:00:00Z', verdict: 'NO_CHANGE' },
  { timestamp: '2025-04-03T02:00:00Z', verdict: 'CHANGE_REQUIRED' },
  { timestamp: '2025-04-03T02:10:00Z', verdict: 'UNCERTAIN' },
];

describe('aggregateByDay (BUG-005)', () => {
  it('produces non-zero day counts when fed real pipeline_runs rows (timestamp field)', () => {
    const days = aggregateByDay(PIPELINE_RUNS);
    expect(days.length).toBeGreaterThan(0);
    const total = days.reduce((s, d) => s + d.count, 0);
    expect(total).toBe(PIPELINE_RUNS.length);
  });

  it('groups correctly by date', () => {
    const days = aggregateByDay(PIPELINE_RUNS);
    const byDate = Object.fromEntries(days.map(d => [d.date, d.count]));
    expect(byDate['2025-04-01']).toBe(2);
    expect(byDate['2025-04-02']).toBe(1);
    expect(byDate['2025-04-03']).toBe(2);
  });

  it('returns empty array for empty input', () => {
    expect(aggregateByDay([])).toEqual([]);
    expect(aggregateByDay(null)).toEqual([]);
  });

  it('still works with legacy run_at field', () => {
    const legacyRuns = [
      { run_at: '2025-04-05T02:00:00Z' },
      { run_at: '2025-04-05T06:00:00Z' },
    ];
    const days = aggregateByDay(legacyRuns);
    expect(days.length).toBe(1);
    expect(days[0].count).toBe(2);
  });

  it('is sorted chronologically', () => {
    const days = aggregateByDay(PIPELINE_RUNS);
    for (let i = 1; i < days.length; i++) {
      expect(days[i].date >= days[i - 1].date).toBe(true);
    }
  });
});

// The funnel must not undercount past the /api/runs row limit: it now
// consumes /api/runs/summary (per-stage "deepest stage reached" totals over
// the FULL history) and converts to cumulative reached-at-least counts.
describe('funnelFromSummary', () => {
  const SUMMARY = [
    { stage: 1, total: 10665 },
    { stage: 2, total: 609 },
    { stage: 3, total: 0 },
    { stage: 4, total: 0 },
    { stage: 5, total: 159 },
    { stage: 6, total: 191 },
  ];

  it('converts deepest-stage totals into cumulative funnel counts', () => {
    const funnel = funnelFromSummary(SUMMARY, 98);
    expect(funnel[0]).toEqual({ stage: 1, count: 11624, unit: 'runs' });
    expect(funnel[1]).toEqual({ stage: 2, count: 959,   unit: 'runs' });
    expect(funnel[4]).toEqual({ stage: 5, count: 350,   unit: 'runs' });
    expect(funnel[5]).toEqual({ stage: 6, count: 191,   unit: 'runs' });
  });

  it('handles counts above the old 1000-row client cap', () => {
    const funnel = funnelFromSummary(SUMMARY, 0);
    expect(funnel[0].count).toBeGreaterThan(1000);
  });

  it('reports distinct triggered pages as the Stage 7 bar', () => {
    const funnel = funnelFromSummary(SUMMARY, 98);
    expect(funnel[6]).toEqual({ stage: 7, count: 98, unit: 'pages' });
  });

  it('is safe on empty input', () => {
    const funnel = funnelFromSummary([], 0);
    expect(funnel).toHaveLength(7);
    expect(funnel.every(d => d.count === 0)).toBe(true);
  });
});
