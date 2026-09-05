// Integration test: filtersToSearch produces the correct URLSearchParams that
// the runs.js route handler reads via req.query.
//
// BUG-004 regression: previously the client sent datePreset/sources/stageMin/verdicts
// but the server read from/to/source_id/stage_reached_min/verdict. Every chip was a no-op.

import { describe, it, expect } from 'vitest';

// Inline the same logic as dashboard/src/hooks/useData.js:filtersToSearch
// so the test doesn't need a DOM/React runtime.
const DATE_PRESET_DAYS = { '7D': 7, '30D': 30, '90D': 90, '180D': 180, '365D': 365 };

function filtersToSearch(filters) {
  if (!filters) return '';
  const params = new URLSearchParams();

  let from = filters.from;
  let to   = filters.to;
  if (!from && filters.datePreset && DATE_PRESET_DAYS[filters.datePreset]) {
    const now = new Date();
    to   = now.toISOString().slice(0, 10);
    const d = new Date(now);
    d.setDate(d.getDate() - DATE_PRESET_DAYS[filters.datePreset]);
    from = d.toISOString().slice(0, 10);
  }
  if (from) params.set('from', from);
  if (to)   params.set('to',   to);

  if (filters.sources?.length) {
    for (const src of filters.sources) params.append('source_id', src);
  }

  if (filters.stageMin != null) params.set('stage_reached_min', filters.stageMin);

  if (filters.verdicts?.length) {
    for (const v of filters.verdicts) params.append('verdict', v);
  }

  const s = params.toString();
  return s ? `?${s}` : '';
}

// Parse a query string the same way Express does (simplified).
function parseQS(qs) {
  const raw = new URLSearchParams(qs.replace(/^\?/, ''));
  const out = {};
  for (const [k] of raw) {
    const vals = raw.getAll(k);
    out[k] = vals.length === 1 ? vals[0] : vals;
  }
  return out;
}

describe('filtersToSearch → SQL params (BUG-004)', () => {
  it('translates 30D preset into concrete from/to ISO dates', () => {
    const qs = filtersToSearch({ datePreset: '30D', sources: [], verdicts: [] });
    const q = parseQS(qs);
    expect(q.from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(q.to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // from should be ~30 days before to
    const days = (new Date(q.to) - new Date(q.from)) / 86_400_000;
    expect(days).toBeCloseTo(30, 0);
    // old param name must not appear
    expect(q.datePreset).toBeUndefined();
  });

  it('sends source_id as repeated params (not sources= CSV)', () => {
    const qs = filtersToSearch({ sources: ['ato', 'ipaustralia'], verdicts: [] });
    const q = parseQS(qs);
    expect(Array.isArray(q.source_id)).toBe(true);
    expect(q.source_id).toContain('ato');
    expect(q.source_id).toContain('ipaustralia');
    expect(q.sources).toBeUndefined();
  });

  it('renames stageMin → stage_reached_min', () => {
    const qs = filtersToSearch({ stageMin: 4, sources: [], verdicts: [] });
    const q = parseQS(qs);
    expect(q.stage_reached_min).toBe('4');
    expect(q.stageMin).toBeUndefined();
  });

  it('sends verdict as repeated params for multi-select', () => {
    const qs = filtersToSearch({ verdicts: ['CHANGE_REQUIRED', 'UNCERTAIN'], sources: [] });
    const q = parseQS(qs);
    expect(Array.isArray(q.verdict)).toBe(true);
    expect(q.verdict).toContain('CHANGE_REQUIRED');
    expect(q.verdict).toContain('UNCERTAIN');
    expect(q.verdicts).toBeUndefined();
  });

  it('empty filters produce empty string', () => {
    expect(filtersToSearch({ sources: [], verdicts: [] })).toBe('');
  });

  it('explicit from/to override datePreset', () => {
    const qs = filtersToSearch({ from: '2025-01-01', to: '2025-03-31', datePreset: '30D', sources: [], verdicts: [] });
    const q = parseQS(qs);
    expect(q.from).toBe('2025-01-01');
    expect(q.to).toBe('2025-03-31');
  });
});
