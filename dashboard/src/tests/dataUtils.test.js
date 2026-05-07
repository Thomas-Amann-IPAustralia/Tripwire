// Snapshot / unit tests for dataUtils.js
//
// BUG-005 regression: aggregateByDay keyed on run.run_at but the API returns
// run.timestamp. The calendar and sparkline were always empty.

import { describe, it, expect } from 'vitest';
import { aggregateByDay } from '../lib/dataUtils.js';

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
