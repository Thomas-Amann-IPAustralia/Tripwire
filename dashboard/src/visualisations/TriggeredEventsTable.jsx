import React, { useMemo, useState, useCallback } from 'react';
import { useDashboard } from '../App.jsx';
import { useFeedback } from '../hooks/useData.js';
import { StageIndicator } from '../components/StageIndicator.jsx';
import { formatRelativeTime, formatScore } from '../lib/dataUtils.js';

const PAGE_SIZE = 20;

const COLUMNS = [
  { key: 'run_id',           label: 'RUN ID',    w: '9%'  },
  { key: 'timestamp',        label: 'TIME',      w: '7%'  },
  { key: 'source_id',        label: 'SOURCE',    w: '14%' },
  { key: 'ipfr_page_id',     label: 'PAGE',      w: '9%'  },
  { key: 'stage_reached',    label: 'STAGE',     w: '6%'  },
  { key: 'verdict',          label: 'VERDICT',   w: '10%' },
  { key: 'confidence',       label: 'CONF',      w: '5%'  },
  { key: 'biencoder_max',    label: 'BI-ENC',    w: '6%'  },
  { key: 'crossencoder_score', label: 'X-ENC',   w: '6%'  },
  { key: 'graph_propagated', label: 'GRAPH',     w: '5%'  },
  { key: 'feedback',         label: 'FB',        w: '4%'  },
];

const VERDICT_STYLE = {
  CHANGE_REQUIRED: { color: 'var(--state-alert)', border: '1px solid var(--state-alert)' },
  UNCERTAIN:       { color: 'var(--state-warn)',  border: '1px solid var(--state-warn)'  },
  NO_CHANGE:       { color: 'var(--state-ok)',    border: '1px solid var(--state-ok)'    },
};

function VerdictPill({ verdict }) {
  if (!verdict) return <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>—</span>;
  const k = verdict.toUpperCase();
  const s = VERDICT_STYLE[k];
  if (!s) return <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>{k}</span>;
  return (
    <span style={{
      ...s, fontFamily: 'var(--font-mono)', fontSize: '9px',
      padding: '1px 4px', letterSpacing: '0.03em', whiteSpace: 'nowrap',
    }}>
      {k.replace(/_/g, ' ')}
    </span>
  );
}

function ConfCell({ value }) {
  if (value == null) return <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>—</span>;
  const n = Number(value);
  const c = n >= 0.7 ? 'var(--state-ok)' : n >= 0.4 ? 'var(--state-warn)' : 'var(--state-error)';
  return <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: c }}>{n.toFixed(2)}</span>;
}

function exportTSV(rows) {
  const headers = COLUMNS.map(c => c.label).join('\t');
  const body = rows.map(r => [
    r.run_id ?? '',
    r.timestamp ?? r.run_at ?? '',
    r.source_id ?? '',
    r.ipfr_page_id ?? '',
    r.stage_reached ?? '',
    r.verdict ?? '',
    r.confidence != null ? Number(r.confidence).toFixed(2) : '',
    formatScore(r.biencoder_max),
    formatScore(r.crossencoder_score),
    r.graph_propagated ? 'Y' : 'N',
    '',
  ].join('\t')).join('\n');
  const blob = new Blob([headers + '\n' + body], { type: 'text/tab-separated-values' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `tripwire-triggered-${new Date().toISOString().slice(0, 10)}.tsv`;
  a.click();
  URL.revokeObjectURL(url);
}

const MONO = { fontFamily: 'var(--font-mono)', fontSize: '10px' };
const TH_S = { ...MONO, color: 'var(--text-tertiary)', padding: '4px 6px', textAlign: 'left', whiteSpace: 'nowrap', letterSpacing: '0.08em', cursor: 'pointer', userSelect: 'none', borderBottom: '1px solid var(--rule-accent)' };
const TD_S = { ...MONO, color: 'var(--text-secondary)', padding: '4px 6px', borderBottom: '1px solid var(--rule)', verticalAlign: 'middle' };

export default function TriggeredEventsTable({ runs = [] }) {
  const { setSelectedRunId, setDrawerOpen } = useDashboard();

  const { data: feedbackResponse } = useFeedback();
  const feedbackRecords = feedbackResponse?.data ?? [];

  const [search,  setSearch]  = useState('');
  const [page,    setPage]    = useState(0);
  const [sortKey, setSortKey] = useState('timestamp');
  const [sortDir, setSortDir] = useState('desc');

  const feedbackMap = useMemo(() => {
    const m = new Map();
    for (const fb of feedbackRecords) m.set(fb.run_id, fb);
    return m;
  }, [feedbackRecords]);

  const triggered = useMemo(
    () => runs.filter(r => (r.stage_reached ?? 0) >= 8),
    [runs],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return triggered;
    return triggered.filter(r =>
      (r.run_id        ?? '').toLowerCase().includes(q) ||
      (r.source_id     ?? '').toLowerCase().includes(q) ||
      (r.ipfr_page_id  ?? '').toLowerCase().includes(q),
    );
  }, [triggered, search]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv));
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageRows   = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const toggleSort = useCallback(key => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
    setPage(0);
  }, [sortKey]);

  return (
    <div className="panel" style={{
      background: 'var(--bg-secondary)', border: '1px solid var(--rule)',
      padding: '16px',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)', letterSpacing: '0.1em' }}>
          TRIGGERED EVENTS — STAGE ≥ 8 &nbsp;
          <span style={{ color: 'var(--text-secondary)' }}>({triggered.length})</span>
        </div>
        <button
          onClick={() => exportTSV(sorted)}
          style={{
            fontFamily: 'var(--font-mono)', fontSize: '10px',
            color: 'var(--text-tertiary)', background: 'transparent',
            border: '1px solid var(--rule)', padding: '3px 10px', cursor: 'pointer',
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--rule-accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--rule)'; }}
        >
          EXPORT TSV
        </button>
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Filter by run ID, source, page…"
        value={search}
        onChange={e => { setSearch(e.target.value); setPage(0); }}
        style={{
          width: '100%', marginBottom: '10px',
          background: 'var(--bg-tertiary)', border: '1px solid var(--rule)',
          color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', fontSize: '10px',
          padding: '5px 8px', outline: 'none',
        }}
        onFocus={e => { e.currentTarget.style.borderColor = 'var(--rule-accent)'; }}
        onBlur={e => {  e.currentTarget.style.borderColor = 'var(--rule)'; }}
      />

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  style={{ ...TH_S, width: col.w }}
                  onClick={() => toggleSort(col.key)}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span style={{ marginLeft: '3px', opacity: 0.7 }}>
                      {sortDir === 'asc' ? '↑' : '↓'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length} style={{ ...TD_S, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No triggered events in current filter
                </td>
              </tr>
            ) : pageRows.map(run => {
              const ts  = run.timestamp ?? run.run_at;
              const stg = run.stage_reached ?? 1;
              return (
                <tr
                  key={run.id ?? run.run_id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => { setSelectedRunId(run.run_id); setDrawerOpen(true); }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-tertiary)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <td style={TD_S}>{run.run_id ?? '—'}</td>
                  <td style={TD_S}>{formatRelativeTime(ts)}</td>
                  <td style={{ ...TD_S, whiteSpace: 'nowrap' }}>
                    {run.source_type && (
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: '8px',
                        padding: '1px 3px', border: '1px solid var(--rule)',
                        color: 'var(--text-tertiary)', marginRight: '4px',
                      }}>
                        {run.source_type}
                      </span>
                    )}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)' }}>
                      {(run.source_id ?? '—').slice(0, 20)}
                    </span>
                  </td>
                  <td style={TD_S}>{run.ipfr_page_id ?? '—'}</td>
                  <td style={TD_S}>
                    <StageIndicator
                      stages={[{ n: stg, reached: true, status: 'passed' }]}
                      size={1}
                    />
                  </td>
                  <td style={TD_S}><VerdictPill verdict={run.verdict} /></td>
                  <td style={TD_S}><ConfCell value={run.confidence} /></td>
                  <td style={TD_S}>{formatScore(run.biencoder_max)}</td>
                  <td style={TD_S}>{formatScore(run.crossencoder_score)}</td>
                  <td style={TD_S}>
                    {run.graph_propagated
                      ? <span style={{ color: 'var(--state-ok)' }}>✓</span>
                      : <span style={{ color: 'var(--text-tertiary)' }}>—</span>}
                  </td>
                  <td style={TD_S}>
                    {feedbackMap.has(run.run_id)
                      ? <span style={{ color: 'var(--state-ok)' }}>✓</span>
                      : <span style={{ color: 'var(--text-tertiary)' }}>○</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '10px' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
          {sorted.length === 0
            ? '0 rows'
            : `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, sorted.length)} of ${sorted.length}`}
        </span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px',
              color: page === 0 ? 'var(--text-tertiary)' : 'var(--text-secondary)',
              background: 'transparent', border: '1px solid var(--rule)',
              padding: '2px 8px', cursor: page === 0 ? 'default' : 'pointer',
            }}
          >
            PREV
          </button>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)' }}>
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px',
              color: page >= totalPages - 1 ? 'var(--text-tertiary)' : 'var(--text-secondary)',
              background: 'transparent', border: '1px solid var(--rule)',
              padding: '2px 8px', cursor: page >= totalPages - 1 ? 'default' : 'pointer',
            }}
          >
            NEXT
          </button>
        </div>
      </div>
    </div>
  );
}
