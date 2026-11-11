import React, { useMemo, useState, useCallback } from 'react';
import { useDashboard } from '../App.jsx';
import { useFeedback, usePageTitles } from '../hooks/useData.js';
import { StageIndicator } from '../components/StageIndicator.jsx';
import { formatRelativeTime, formatScore } from '../lib/dataUtils.js';
import { Card, VerdictBadge, SearchInput, Badge, Icon } from '../components/ui.jsx';

const PAGE_SIZE = 20;

// `exportLabel` keeps the TSV export headers identical to earlier versions so
// any spreadsheets built on the export keep working.
const COLUMNS = [
  { key: 'run_id',             label: 'Run ID',     exportLabel: 'RUN ID',  w: '10%' },
  { key: 'timestamp',          label: 'When',       exportLabel: 'TIME',    w: '8%'  },
  { key: 'source_id',          label: 'Source',     exportLabel: 'SOURCE',  w: '14%' },
  { key: 'ipfr_page_id',       label: 'IPFR page',  exportLabel: 'PAGE',    w: '20%' },
  { key: 'stage_reached',      label: 'Stage',      exportLabel: 'STAGE',   w: '6%'  },
  { key: 'verdict',            label: 'Verdict',    exportLabel: 'VERDICT', w: '11%' },
  { key: 'confidence',         label: 'Conf.',      exportLabel: 'CONF',    w: '6%', title: 'Model confidence' },
  { key: 'biencoder_max',      label: 'Bi-enc.',    exportLabel: 'BI-ENC',  w: '6%', title: 'Bi-encoder maximum similarity' },
  { key: 'crossencoder_score', label: 'Cross-enc.', exportLabel: 'X-ENC',   w: '7%', title: 'Cross-encoder score' },
  { key: 'graph_propagated',   label: 'Graph',      exportLabel: 'GRAPH',   w: '5%', title: 'Reached through graph propagation' },
  { key: 'feedback',           label: 'Feedback',   exportLabel: 'FB',      w: '6%' },
];

function ConfCell({ value }) {
  if (value == null) return <span className="tw-faint">—</span>;
  const n = Number(value);
  return <span className="tabular" style={{ fontWeight: 600, color: 'var(--ink)' }}>{n.toFixed(2)}</span>;
}

function exportTSV(rows, pageTitles) {
  const headers = COLUMNS.flatMap(c => c.key === 'ipfr_page_id' ? ['PAGE ID', 'PAGE TITLE'] : [c.exportLabel]).join('\t');
  const body = rows.map(r => [
    r.run_id ?? '',
    r.timestamp ?? r.run_at ?? '',
    r.source_id ?? '',
    r.ipfr_page_id ?? '',
    pageTitles?.get(r.ipfr_page_id) ?? '',
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

export default function TriggeredEventsTable({ runs = [] }) {
  const { setSelectedRunId, setSelectedRowId, setDrawerOpen } = useDashboard();

  const { data: feedbackResponse } = useFeedback();
  const feedbackRecords = feedbackResponse?.data ?? [];
  const pageTitles = usePageTitles();

  const [search,  setSearch]  = useState('');
  const [page,    setPage]    = useState(0);
  const [sortKey, setSortKey] = useState('timestamp');
  const [sortDir, setSortDir] = useState('desc');

  const feedbackMap = useMemo(() => {
    const m = new Map();
    for (const fb of feedbackRecords) m.set(fb.run_id, fb);
    return m;
  }, [feedbackRecords]);

  // "Triggered" = the run bundled IPFR pages for Stage 7+ (aggregation/LLM).
  // stage_reached from the server tops out at 6 (stage6_complete), so the old
  // `stage_reached >= 8` filter matched nothing and the table was always empty.
  const triggered = useMemo(
    () => runs.filter(r => (r.triggered_pages?.length ?? 0) > 0 || r.verdict != null),
    [runs],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return triggered;
    return triggered.filter(r =>
      (r.run_id        ?? '').toLowerCase().includes(q) ||
      (r.source_id     ?? '').toLowerCase().includes(q) ||
      (r.ipfr_page_id  ?? '').toLowerCase().includes(q) ||
      (pageTitles.get(r.ipfr_page_id) ?? '').toLowerCase().includes(q),
    );
  }, [triggered, search, pageTitles]);

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
    <Card
      noBody
      clip
      title={<>Triggered events <Badge tone="accent">{triggered.length}</Badge></>}
      subtitle="Source checks that bundled IPFR pages for Stage 7 onwards. Select a row to see the full trace."
      actions={
        <>
          <SearchInput
            value={search}
            onChange={v => { setSearch(v); setPage(0); }}
            placeholder="Filter by run, source or page"
            width={240}
          />
          <button type="button" className="tw-btn is-sm" onClick={() => exportTSV(sorted, pageTitles)}>
            <Icon name="download" /> Export TSV
          </button>
        </>
      }
    >
      <div className="tw-table-wrap">
        <table className="tw-table is-compact">
          <thead>
            <tr>
              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  style={{ width: col.w, cursor: 'pointer', userSelect: 'none' }}
                  title={col.title}
                  onClick={() => toggleSort(col.key)}
                  aria-sort={sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span style={{ marginLeft: '4px', color: 'var(--accent)' }}>
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
                <td colSpan={COLUMNS.length} style={{ textAlign: 'center', padding: '28px', color: 'var(--muted)' }}>
                  No triggered events in the current filter.
                </td>
              </tr>
            ) : pageRows.map(run => {
              const ts  = run.timestamp ?? run.run_at;
              const stg = run.stage_reached ?? 1;
              return (
                <tr
                  key={run.id ?? run.run_id}
                  className="is-clickable"
                  onClick={() => { setSelectedRunId(run.run_id); setSelectedRowId(run.id ?? null); setDrawerOpen(true); }}
                >
                  <td style={{ whiteSpace: 'nowrap' }}><span className="tw-code" style={{ fontSize: '11px' }}>{run.run_id ?? '—'}</span></td>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatRelativeTime(ts)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {run.source_type && <Badge tone="muted" style={{ marginRight: '6px', fontSize: '10px' }}>{run.source_type}</Badge>}
                    <span className="tw-code" style={{ fontSize: '11px' }} title={run.source_id}>
                      {(run.source_id ?? '—').slice(0, 22)}
                    </span>
                  </td>
                  <td style={{ whiteSpace: 'normal' }}>
                    {run.ipfr_page_id ? (
                      <span title={`${pageTitles.get(run.ipfr_page_id) ?? ''} (${run.ipfr_page_id})`}>
                        <span className="tw-cell-strong">
                          {pageTitles.get(run.ipfr_page_id) ?? run.ipfr_page_id}
                        </span>
                        {pageTitles.has(run.ipfr_page_id) && (
                          <span className="tw-code" style={{ fontSize: '10.5px', color: 'var(--faint)', marginLeft: '6px', whiteSpace: 'nowrap' }}>
                            {run.ipfr_page_id}
                          </span>
                        )}
                      </span>
                    ) : '—'}
                  </td>
                  <td>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                      <StageIndicator stages={[{ n: stg, reached: true, status: 'passed' }]} size={1} />
                      <span className="tabular">{stg}</span>
                    </span>
                  </td>
                  <td>{run.verdict ? <VerdictBadge verdict={String(run.verdict).toUpperCase()} /> : <span className="tw-faint">—</span>}</td>
                  <td><ConfCell value={run.confidence} /></td>
                  <td className="tabular">{formatScore(run.biencoder_max)}</td>
                  <td className="tabular">{formatScore(run.crossencoder_score)}</td>
                  <td>
                    {run.graph_propagated
                      ? <Icon name="check" size={15} style={{ color: 'var(--ok)' }} title="Graph propagated" />
                      : <span className="tw-faint">—</span>}
                  </td>
                  <td>
                    {feedbackMap.has(run.run_id)
                      ? <Badge tone="ok">Received</Badge>
                      : <span className="tw-faint">None</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="tw-card-foot" style={{ justifyContent: 'space-between' }}>
        <span>
          {sorted.length === 0
            ? 'No rows'
            : `Showing ${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, sorted.length)} of ${sorted.length}`}
        </span>
        <div className="tw-row">
          <button type="button" className="tw-btn is-sm" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>
            <Icon name="arrowLeft" /> Previous
          </button>
          <span className="tabular">Page {page + 1} of {totalPages}</span>
          <button type="button" className="tw-btn is-sm" onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>
            Next <Icon name="arrow" />
          </button>
        </div>
      </div>
    </Card>
  );
}
