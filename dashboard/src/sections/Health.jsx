import React, { useState, useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip,
  ReferenceLine, ResponsiveContainer,
  LineChart, Line,
} from 'recharts';
import {
  useHealthSummary, useHealthRuns, useHealthIngestion, useRun,
} from '../hooks/useData.js';
import ErrorBanner from '../components/ErrorBanner.jsx';
import { PageHeader, Card, MetricCard, OutcomeBadge, Badge, Caret, Icon, Loading, EmptyState, chartAxisTick, chartTooltipStyle } from '../components/ui.jsx';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(sec) {
  if (sec == null) return '—';
  const s = Number(sec);
  if (s < 60)   return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function fmtPct(rate) {
  if (rate == null) return '—';
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

function fmtDate(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleDateString('en-AU', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch { return String(ts); }
}

function fmtTime(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleTimeString('en-AU', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch { return ''; }
}

// Bucket per-run rows into 30 daily error-rate data points
function dailyErrorRate(runs) {
  const byDay = {};
  for (const r of runs) {
    if (!r.start_time) continue;
    const day = r.start_time.slice(0, 10);
    if (!byDay[day]) byDay[day] = { checked: 0, errored: 0 };
    byDay[day].checked += r.sources_checked ?? 0;
    byDay[day].errored += r.sources_errored ?? 0;
  }
  const result = [];
  for (let i = 29; i >= 0; i--) {
    const d   = new Date(Date.now() - i * 86_400_000);
    const day = d.toISOString().slice(0, 10);
    const b   = byDay[day] ?? { checked: 0, errored: 0 };
    result.push({
      day,
      label: day.slice(5).replace('-', '/'),
      rate: b.checked > 0 ? b.errored / b.checked : null,
    });
  }
  return result;
}

// ── Shared primitives ─────────────────────────────────────────────────────────

const OutcomePill = OutcomeBadge;

// ── Row 1: Status metrics ─────────────────────────────────────────────────────

function StatusStrip({ summary, sparkData }) {
  if (!summary) return <Loading>Loading health summary…</Loading>;

  const lr      = summary.last_run;
  const llmWarn = (summary.llm_schema_failures_30d ?? 0) >= 2;
  const ceWarn  = (summary.cross_encoder_truncations_30d ?? 0) >= 3;
  const sparkPoints = sparkData.filter(d => d.rate != null);

  return (
    <div className="tw-metrics">
      <MetricCard
        label="Last run"
        icon="clock"
        small
        value={lr ? fmtDate(lr.timestamp) : '—'}
        context={lr && <>
          <span>{fmtTime(lr.timestamp)} · {fmtDuration(lr.duration_seconds)}</span>
          <OutcomePill outcome={lr.outcome} />
        </>}
      />

      <MetricCard
        label="Error rate, 30 days"
        value={fmtPct(summary.error_rate_30d)}
        tone={(summary.error_rate_30d ?? 0) >= 0.3 ? 'alert' : (summary.error_rate_30d ?? 0) >= 0.15 ? 'warn' : undefined}
        context={
          <LineChart width={110} height={28} data={sparkPoints} style={{ overflow: 'visible' }}>
            <Line type="monotone" dataKey="rate" stroke="var(--alert)" strokeWidth={1.75} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
        }
      />

      <MetricCard label="Sources monitored" icon="source" value={summary.total_sources_monitored ?? 0} />

      <MetricCard
        label="LLM schema failures"
        value={summary.llm_schema_failures_30d ?? 0}
        tone={llmWarn ? 'warn' : undefined}
        context={llmWarn ? 'Last 30 days: check the model output format' : 'Last 30 days, within normal limits'}
      />

      <MetricCard
        label="Cross-encoder truncations"
        value={summary.cross_encoder_truncations_30d ?? 0}
        tone={ceWarn ? 'warn' : undefined}
        context={ceWarn ? 'Last 30 days: inputs may be too long' : 'Last 30 days, within normal limits'}
      />
    </div>
  );
}

// ── Row 1b: Stage 8/9 activity ────────────────────────────────────────────────

function KvStrip({ title, subtitle, items }) {
  return (
    <Card title={title} subtitle={subtitle}>
      <div className="tw-kv-grid">
        {items.map(({ label, value, color }) => (
          <div key={label} className="tw-kv">
            <div className="tw-kv-label" title={label}>{label}</div>
            <div className="tw-kv-value" style={{ color: color ?? 'var(--ink)' }}>{value}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function LlmNotifyStrip({ summary }) {
  const assessments = summary?.llm_assessments_30d ?? '—';
  const changeRequired = summary?.llm_verdict_change_required_30d ?? '—';
  const uncertain = summary?.llm_verdict_uncertain_30d ?? '—';
  const noChange = summary?.llm_verdict_no_change_30d ?? '—';
  const notifications = summary?.notifications_30d ?? '—';

  // Every figure here covers the trailing 30-day window; the Assessments
  // page's total is all-time, so the window is labelled explicitly.
  return (
    <KvStrip
      title="Language model and notifications"
      subtitle="Stages 8 and 9 over the last 30 days"
      items={[
        { label: 'Assessments',        value: assessments },
        { label: 'Change required',    value: changeRequired, color: 'var(--alert)' },
        { label: 'Uncertain',          value: uncertain,      color: 'var(--warn)' },
        { label: 'No change',          value: noChange,       color: 'var(--ok)' },
        { label: 'Schema failures',    value: summary?.llm_schema_failures_30d ?? '—' },
        { label: 'Notifications sent', value: notifications },
      ]}
    />
  );
}

// ── Row 2 left: Error rate area chart ─────────────────────────────────────────

function ErrorRateChart({ data }) {
  return (
    <Card title="Daily error rate" subtitle="Share of source checks that errored each day, last 30 days">
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 6, right: 48, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="twErrFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--alert)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--alert)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis dataKey="label" tick={chartAxisTick} interval={6} axisLine={false} tickLine={false} />
          <YAxis
            domain={[0, dataMax => Math.min(1, Math.max(0.4, (dataMax || 0) * 1.25))]}
            tick={chartAxisTick}
            tickFormatter={v => `${Math.round(v * 100)}%`}
            axisLine={false}
            tickLine={false}
            width={38}
          />
          <RechartsTooltip
            contentStyle={chartTooltipStyle}
            formatter={v => [v != null ? `${(v * 100).toFixed(1)}%` : '—', 'Error rate']}
            labelStyle={{ color: 'var(--ink)', fontWeight: 650 }}
            cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1 }}
          />
          <ReferenceLine
            y={0.15}
            stroke="var(--warn)"
            strokeDasharray="4 4"
            label={{ value: 'Warning', position: 'right', style: { fontFamily: 'var(--font-sans)', fontSize: 11, fill: 'var(--warn)' } }}
          />
          <ReferenceLine
            y={0.30}
            stroke="var(--alert)"
            strokeDasharray="4 4"
            label={{ value: 'Alert', position: 'right', style: { fontFamily: 'var(--font-sans)', fontSize: 11, fill: 'var(--alert)' } }}
          />
          <Area
            type="monotone"
            dataKey="rate"
            stroke="var(--alert)"
            fill="url(#twErrFill)"
            strokeWidth={2}
            connectNulls
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
}

// ── Row 2 right: Consecutive failures list ────────────────────────────────────

function ConsecutiveFailures({ failures }) {
  const hasFails = failures && failures.length > 0;
  return (
    <Card
      noBody
      clip
      title="Consecutive source failures"
      subtitle={hasFails ? `${failures.length} source${failures.length !== 1 ? 's' : ''} failing on repeated runs` : 'Sources that failed on successive runs'}
    >
      {!hasFails ? (
        <EmptyState icon="check" compact title="All sources healthy">No source has failed on consecutive runs.</EmptyState>
      ) : (
        <div style={{ maxHeight: '236px', overflowY: 'auto' }}>
          <table className="tw-table is-compact" style={{ tableLayout: 'fixed' }}>
            <thead>
              <tr>
                <th>Source ID</th>
                <th style={{ textAlign: 'right', width: '84px' }}>Failures</th>
                <th style={{ width: '96px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {failures.map(f => {
                const critical = (f.consecutive_failures ?? 0) >= 3;
                return (
                  <tr key={f.source_id}>
                    <td style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.source_id}><span className="tw-code" style={{ fontSize: '11.5px', color: critical ? 'var(--error)' : 'var(--ink)' }}>{f.source_id}</span></td>
                    <td className="tabular" style={{ textAlign: 'right', fontWeight: 650, color: critical ? 'var(--error)' : 'var(--body)' }}>
                      {f.consecutive_failures}
                    </td>
                    <td>{critical ? <Badge tone="error" dot>Critical</Badge> : <Badge tone="warn" dot>Failing</Badge>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ── Row 3: Run log table ───────────────────────────────────────────────────────

// Per-source sub-table rendered inline when a run row is expanded
function ExpandedSourceRows({ runId }) {
  const { data: raw, isLoading } = useRun(runId);
  const rows = Array.isArray(raw?.data) ? raw.data : (Array.isArray(raw) ? raw : []);

  if (isLoading) {
    return (
      <tr className="is-sub">
        <td colSpan={8} style={{ paddingLeft: '44px' }}><Loading style={{ padding: 0 }} /></td>
      </tr>
    );
  }
  if (!rows.length) {
    return (
      <tr className="is-sub">
        <td colSpan={8} style={{ paddingLeft: '44px', color: 'var(--muted)' }}>No per-source data.</td>
      </tr>
    );
  }
  return rows.map((r, i) => (
    <tr key={i} className="is-sub">
      <td style={{ paddingLeft: '44px' }}>
        <span className="tw-code" style={{ fontSize: '11px', color: 'var(--body)' }}>{r.source_id ?? '—'}</span>
      </td>
      <td>{fmtTime(r.timestamp ?? r.run_at)}</td>
      <td>{fmtDuration(r.duration_seconds)}</td>
      <td>Stage {r.stage_reached ?? '—'}</td>
      <td className="tw-faint">—</td>
      <td className="tw-faint">—</td>
      <td>
        {r.triggered_pages && r.triggered_pages !== '[]'
          ? <Icon name="check" size={14} style={{ color: 'var(--accent)' }} title="Triggered IPFR pages" />
          : <span className="tw-faint">—</span>}
      </td>
      <td><OutcomePill outcome={r.outcome} /></td>
    </tr>
  ));
}

const PAGE_SIZE = 10;

function RunLogTable({ runs, totalRuns }) {
  const total = totalRuns ?? runs.length;
  const [page, setPage]         = useState(0);
  const [expanded, setExpanded] = useState(new Set());

  const totalPages = Math.ceil(runs.length / PAGE_SIZE);
  const pageRuns   = runs.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const toggle = (id) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <Card
      noBody
      clip
      title="Run log"
      subtitle={`${total} pipeline run${total !== 1 ? 's' : ''}${runs.length < total ? `, showing the ${runs.length} most recent` : ''}. Select a run to see each source it checked.`}
      footer={totalPages > 1 && (
        <>
          <button type="button" className="tw-btn is-sm" disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>
            <Icon name="arrowLeft" /> Previous
          </button>
          <span className="tabular">Page {page + 1} of {totalPages}</span>
          <button type="button" className="tw-btn is-sm" disabled={page >= totalPages - 1} onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}>
            Next <Icon name="arrow" />
          </button>
          <span style={{ marginLeft: 'auto' }}>{runs.length} runs in total</span>
        </>
      )}
    >
      <div className="tw-table-wrap">
        <table className="tw-table" style={{ minWidth: '760px' }}>
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Checked</th>
              <th>Changed</th>
              <th>Errored</th>
              <th>Alerts</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {pageRuns.map(r => {
              const isExp = expanded.has(r.run_id);
              return (
                <React.Fragment key={r.run_id}>
                  <tr className={`is-clickable${isExp ? ' is-expanded' : ''}`} onClick={() => toggle(r.run_id)} aria-expanded={isExp}>
                    <td>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                        <Caret open={isExp} />
                        <span className="tw-code" style={{ fontSize: '11.5px', color: 'var(--ink)' }}>{r.run_id}</span>
                      </span>
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.start_time)} · {fmtTime(r.start_time)}</td>
                    <td className="tabular">{fmtDuration(r.total_duration)}</td>
                    <td className="tabular">{r.sources_checked ?? '—'}</td>
                    <td className="tabular">{r.sources_completed ?? '—'}</td>
                    <td className="tabular" style={{ fontWeight: (r.sources_errored ?? 0) > 0 ? 650 : 400, color: (r.sources_errored ?? 0) > 0 ? 'var(--alert)' : undefined }}>
                      {r.sources_errored ?? 0}
                    </td>
                    <td className="tabular">{r.alerts_generated ?? 0}</td>
                    <td><OutcomePill outcome={r.status} /></td>
                  </tr>
                  {isExp && <ExpandedSourceRows runId={r.run_id} />}
                </React.Fragment>
              );
            })}
            {!pageRuns.length && (
              <tr>
                <td colSpan={8} style={{ color: 'var(--muted)', padding: '24px 16px', textAlign: 'center' }}>
                  No run data available.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ── Row 4: Ingestion health ───────────────────────────────────────────────────

function IngestionStrip({ ingestion }) {
  return (
    <KvStrip
      title="Corpus ingestion"
      subtitle="Most recent refresh of the IPFR corpus"
      items={[
        { label: 'Last ingestion',     value: ingestion ? `${fmtDate(ingestion.start_time)} ${fmtTime(ingestion.start_time)}` : '—' },
        { label: 'Pages ingested',     value: ingestion?.pages_ingested   ?? '—' },
        { label: 'Pages skipped',      value: ingestion?.pages_skipped    ?? '—' },
        { label: 'Stubs detected',     value: ingestion?.stubs            ?? '—' },
        { label: 'Duplicates found',   value: ingestion?.duplicates       ?? '—' },
        { label: 'Keyphrases indexed', value: ingestion?.keyphrases_total ?? '—' },
      ]}
    />
  );
}

// ── Main Health section ───────────────────────────────────────────────────────

export default function Health() {
  const { data: summaryRaw,   error: summaryError   } = useHealthSummary();
  const { data: runsRaw,      error: runsError       } = useHealthRuns();
  const { data: ingestionRaw } = useHealthIngestion();

  const summary   = summaryRaw?.data   ?? summaryRaw   ?? null;
  const ingestion = ingestionRaw?.data ?? null;

  const runs = useMemo(() => {
    if (Array.isArray(runsRaw?.data)) return runsRaw.data;
    if (Array.isArray(runsRaw))       return runsRaw;
    return [];
  }, [runsRaw]);

  const sparkData = useMemo(() => dailyErrorRate(runs), [runs]);

  return (
    <div className="tw-page">
      <PageHeader
        eyebrow="Administration · System health"
        title="System health"
        description="Whether the pipeline is running on schedule, how often sources fail to load, and the state of the IPFR corpus."
      />

      <ErrorBanner error={summaryError ?? runsError} />

      <div className="tw-stack">
        <StatusStrip summary={summary} sparkData={sparkData} />

        <LlmNotifyStrip summary={summary} />

        <div className="tw-grid-60-40">
          <ErrorRateChart data={sparkData} />
          <ConsecutiveFailures failures={summary?.sources_with_consecutive_failures} />
        </div>

        <RunLogTable runs={runs} totalRuns={runsRaw?.total} />

        <IngestionStrip ingestion={ingestion} />
      </div>
    </div>
  );
}
