import React, { useState, useMemo } from 'react';
import { useLLMReports, usePageTitles } from '../hooks/useData.js';
import ErrorBanner from '../components/ErrorBanner.jsx';
import {
  PageHeader, Card, MetricCard, VerdictBadge, VERDICTS, Segmented, SearchInput,
  Caret, Meter, Callout, EmptyState, Loading,
} from '../components/ui.jsx';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleDateString('en-AU', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch { return String(ts); }
}

function fmtTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleTimeString('en-AU', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch { return ''; }
}

function fmtPct(v) {
  if (v == null) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

// ── Confidence meter ──────────────────────────────────────────────────────────
// Confidence is the model's certainty in its own verdict, not a severity, so it
// is drawn in the neutral accent rather than a traffic-light colour.

function ConfidenceMeter({ value }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
      <Meter value={pct / 100} style={{ flex: 1, minWidth: '64px' }} label="Model confidence" />
      <span className="tabular" style={{ fontSize: '12.5px', fontWeight: 650, color: 'var(--ink)', minWidth: '36px', textAlign: 'right' }}>
        {pct}%
      </span>
    </div>
  );
}

// ── Summary metrics ───────────────────────────────────────────────────────────

function SummaryMetrics({ data }) {
  if (!data) return null;
  const { all_count, verdict_counts } = data;
  const vc = verdict_counts ?? {};
  return (
    <div className="tw-metrics" style={{ marginBottom: '18px' }}>
      <MetricCard label="Total assessments" value={all_count ?? 0} icon="inbox" context="All time" />
      <MetricCard label="Change required" value={vc.CHANGE_REQUIRED ?? 0} tone={(vc.CHANGE_REQUIRED ?? 0) > 0 ? 'alert' : undefined}
        context="IPFR content likely needs an update" />
      <MetricCard label="Uncertain" value={vc.UNCERTAIN ?? 0} tone={(vc.UNCERTAIN ?? 0) > 0 ? 'warn' : undefined}
        context="Needs a person to decide" />
      <MetricCard label="No change" value={vc.NO_CHANGE ?? 0} tone="ok" context="Content remains accurate" />
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

const VERDICT_FILTERS = ['ALL', 'CHANGE_REQUIRED', 'UNCERTAIN', 'NO_CHANGE'];

function FilterBar({ verdict, onVerdict, search, onSearch, count, hasFilter }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap',
      padding: '14px 20px', borderBottom: '1px solid var(--line-soft)',
    }}>
      <Segmented
        ariaLabel="Filter by verdict"
        value={verdict}
        onChange={onVerdict}
        options={VERDICT_FILTERS.map(v => ({
          value: v,
          label: v === 'ALL' ? 'All verdicts' : VERDICTS[v].label,
          dot: v === 'ALL' ? undefined : VERDICTS[v].color,
        }))}
      />
      <span className="tw-small tw-muted">
        {count} report{count !== 1 ? 's' : ''}{hasFilter ? ' match' : ''}
      </span>
      <SearchInput
        value={search}
        onChange={onSearch}
        placeholder="Search page title, ID or reasoning"
        width={300}
        style={{ marginLeft: 'auto' }}
      />
    </div>
  );
}

// ── Report card ───────────────────────────────────────────────────────────────

function ReportCard({ report, pageTitle }) {
  const [expanded, setExpanded] = useState(false);
  const hasSuggestions = Array.isArray(report.suggested_changes) && report.suggested_changes.length > 0;

  return (
    <div style={{
      borderBottom: '1px solid var(--line-soft)',
      background: expanded ? 'var(--surface-subtle)' : 'transparent',
      transition: 'background 150ms ease',
    }}>
      {/* Card header: always visible */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded(e => !e)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(x => !x); } }}
        className="tw-report-row"
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 170px 140px 20px',
          gap: '20px',
          alignItems: 'center',
          padding: '16px 20px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {/* Page and run info */}
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '14.5px', fontWeight: 650, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
              {pageTitle || report.ipfr_page_id || '—'}
            </span>
            {pageTitle && report.ipfr_page_id && (
              <span className="tw-code" style={{ fontSize: '11px', color: 'var(--faint)' }}>{report.ipfr_page_id}</span>
            )}
          </div>
          <div style={{
            fontSize: '12.5px', color: 'var(--muted)', marginTop: '4px',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {report.reasoning
              ? report.reasoning.slice(0, 160) + (report.reasoning.length > 160 ? '…' : '')
              : '—'}
          </div>
          <div style={{ fontSize: '11.5px', color: 'var(--faint)', marginTop: '6px', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <span>Run <span className="tw-code">{report.run_id}</span></span>
            {report.generated_at && <span>{fmtDate(report.generated_at)} · {fmtTime(report.generated_at)}</span>}
          </div>
        </div>

        {/* Confidence */}
        <div>
          <div style={{ fontSize: '11px', color: 'var(--faint)', marginBottom: '5px' }}>Confidence</div>
          <ConfidenceMeter value={report.confidence} />
        </div>

        {/* Verdict */}
        <div><VerdictBadge verdict={report.verdict} /></div>

        <Caret open={expanded} />
      </div>

      {/* Expanded body */}
      {expanded && (
        <div style={{ padding: '0 20px 22px', display: 'flex', flexDirection: 'column', gap: '18px' }}>

          {/* Meta */}
          <div className="tw-kv-grid">
            {[
              { label: 'Verdict',           value: <VerdictBadge verdict={report.verdict} /> },
              { label: 'Confidence',        value: fmtPct(report.confidence) },
              { label: 'Model',             value: report.model ?? '—' },
              { label: 'Prompt tokens',     value: report.prompt_tokens ?? '—' },
              { label: 'Completion tokens', value: report.completion_tokens ?? '—' },
              { label: 'Total tokens',      value: report.total_tokens ?? '—' },
              { label: 'Processing time',   value: report.processing_time_seconds != null ? `${report.processing_time_seconds.toFixed(2)}s` : '—' },
              { label: 'Retries',           value: report.retries ?? 0 },
              { label: 'Generated',         value: report.generated_at ? `${fmtDate(report.generated_at)} ${fmtTime(report.generated_at)}` : '—' },
            ].map(({ label, value }) => (
              <div key={label} className="tw-kv">
                <div className="tw-kv-label">{label}</div>
                <div className="tw-kv-value" style={{ fontSize: '14px' }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Reasoning */}
          <div>
            <h3 style={{ fontSize: '13px', marginBottom: '8px' }}>Reasoning</h3>
            <div style={{
              fontSize: '14px', lineHeight: 1.7, color: 'var(--body)',
              padding: '14px 16px', background: 'var(--surface)',
              border: '1px solid var(--line)', borderRadius: 'var(--radius)',
            }}>
              {report.reasoning || '—'}
            </div>
          </div>

          {/* Suggested changes */}
          {hasSuggestions && (
            <div>
              <h3 style={{ fontSize: '13px', marginBottom: '8px', color: 'var(--alert)' }}>Suggested changes</h3>
              <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {report.suggested_changes.map((change, i) => (
                  <li key={i} style={{
                    display: 'flex', gap: '12px', alignItems: 'flex-start',
                    padding: '12px 14px', background: 'var(--alert-soft)',
                    border: '1px solid var(--alert-line)', borderRadius: 'var(--radius)',
                  }}>
                    <span className="tabular" style={{
                      width: '22px', height: '22px', flexShrink: 0, borderRadius: '50%',
                      display: 'grid', placeItems: 'center', fontSize: '11px', fontWeight: 700,
                      background: 'var(--surface)', color: 'var(--alert)', border: '1px solid var(--alert-line)',
                    }}>
                      {i + 1}
                    </span>
                    <span style={{ fontSize: '13.5px', lineHeight: 1.6, color: 'var(--ink)' }}>{change}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* No suggestions note for NO_CHANGE / UNCERTAIN */}
          {!hasSuggestions && report.verdict !== 'CHANGE_REQUIRED' && (
            report.verdict === 'NO_CHANGE'
              ? <Callout tone="ok" icon="check">No amendments required; the IPFR page content remains accurate.</Callout>
              : <Callout tone="warn" icon="help">The outcome is ambiguous, so the content owner should review this manually.</Callout>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Consider section ─────────────────────────────────────────────────────

export default function Consider() {
  const [verdictFilter, setVerdictFilter] = useState('ALL');
  const [search, setSearch] = useState('');

  const apiVerdict = verdictFilter === 'ALL' ? undefined : verdictFilter;
  const { data: raw, error, isLoading } = useLLMReports({ verdict: apiVerdict });
  const pageTitles = usePageTitles();

  const allReports = useMemo(() => {
    const base = Array.isArray(raw?.data) ? raw.data : [];
    if (!search.trim()) return base;
    const q = search.toLowerCase();
    return base.filter(r =>
      (r.ipfr_page_id ?? '').toLowerCase().includes(q) ||
      (pageTitles.get(r.ipfr_page_id) ?? '').toLowerCase().includes(q) ||
      (r.reasoning ?? '').toLowerCase().includes(q) ||
      (r.run_id ?? '').toLowerCase().includes(q) ||
      (r.suggested_changes ?? []).some(s => s.toLowerCase().includes(q))
    );
  }, [raw, search, pageTitles]);

  const hasFilter = verdictFilter !== 'ALL' || search.trim() !== '';

  return (
    <div className="tw-page">
      <PageHeader
        eyebrow="Review · Stage 8 assessments"
        title="Assessments"
        description="Every change that reached the language model, with its verdict, confidence and reasoning. Start with anything marked change required or uncertain."
      />

      <ErrorBanner error={error} />

      <SummaryMetrics data={raw} />

      <Card noBody clip>
        <FilterBar
          verdict={verdictFilter}
          onVerdict={setVerdictFilter}
          search={search}
          onSearch={setSearch}
          count={allReports.length}
          hasFilter={hasFilter}
        />

        {isLoading && <div style={{ padding: '8px 20px' }}><Loading>Loading reports…</Loading></div>}

        {!isLoading && allReports.length === 0 && (
          hasFilter
            ? <EmptyState icon="search" title="No matching reports">Try a different verdict filter or clear your search.</EmptyState>
            : <EmptyState icon="inbox" title="No assessments yet">
                Assessments are generated when the pipeline runs in live mode and sends trigger bundles to the language model. Reports are saved to <code>data/LLM Reports/</code>.
              </EmptyState>
        )}

        {!isLoading && allReports.length > 0 && allReports.map((report, i) => (
          <ReportCard
            key={report._filename ?? i}
            report={report}
            pageTitle={pageTitles.get(report.ipfr_page_id)}
          />
        ))}
      </Card>

      <style>{`
        .tw-report-row:hover { background: var(--surface-hover); }
        @media (max-width: 900px) {
          .tw-report-row { grid-template-columns: minmax(0, 1fr) 20px !important; }
          .tw-report-row > div:nth-child(2), .tw-report-row > div:nth-child(3) { display: none; }
        }
      `}</style>
    </div>
  );
}
