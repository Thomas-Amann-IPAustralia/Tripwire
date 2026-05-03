import React, { useState, useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip as RechartsTooltip,
  ReferenceLine, ResponsiveContainer,
  LineChart, Line,
} from 'recharts';
import {
  useHealthSummary, useHealthRuns, useHealthIngestion, useRun,
} from '../hooks/useData.js';

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

function OutcomePill({ outcome }) {
  const MAP = {
    ok:        { color: 'var(--state-ok)',      label: 'OK'        },
    partial:   { color: 'var(--state-warn)',    label: 'PARTIAL'   },
    error:     { color: 'var(--state-alert)',   label: 'ERROR'     },
    completed: { color: 'var(--state-ok)',      label: 'COMPLETED' },
    no_change: { color: 'var(--text-tertiary)', label: 'NO CHANGE' },
    skipped:   { color: 'var(--text-tertiary)', label: 'SKIPPED'   },
  };
  const s = MAP[outcome] ?? {
    color: 'var(--text-tertiary)',
    label: String(outcome ?? '—').toUpperCase().replace(/_/g, ' '),
  };
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: '9px', letterSpacing: '0.06em',
      color: s.color, border: `1px solid ${s.color}`,
      padding: '1px 5px', whiteSpace: 'nowrap',
    }}>
      {s.label}
    </span>
  );
}

// ── Row 1: Status strip ───────────────────────────────────────────────────────

function StatCard({ label, alert = false, children }) {
  return (
    <div
      className={alert ? 'alert-pulse-border' : ''}
      style={{
        background: 'var(--bg-secondary)',
        border: alert ? '1px solid var(--state-warn)' : '1px solid var(--rule)',
        padding: '14px 16px',
        display: 'flex', flexDirection: 'column', gap: '8px',
        minWidth: 0,
      }}
    >
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '9px',
        color: 'var(--text-tertiary)', letterSpacing: '0.09em', textTransform: 'uppercase',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

function BigNum({ n, color }) {
  return (
    <span style={{
      fontFamily: 'var(--font-display)', fontSize: '28px', lineHeight: 1,
      color: color ?? 'var(--text-primary)',
    }}>
      {n ?? '—'}
    </span>
  );
}

function StatusStrip({ summary, sparkData }) {
  if (!summary) return (
    <div style={{
      padding: '20px 24px',
      fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
    }}>
      LOADING HEALTH SUMMARY…
    </div>
  );

  const lr      = summary.last_run;
  const llmWarn = (summary.llm_schema_failures_30d ?? 0) >= 2;
  const ceWarn  = (summary.cross_encoder_truncations_30d ?? 0) >= 3;
  const sparkPoints = sparkData.filter(d => d.rate != null);

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)',
      gap: '1px', background: 'var(--rule)',
      borderBottom: '1px solid var(--rule)',
    }}>
      {/* Last Run */}
      <StatCard label="Last Run">
        <BigNum n={lr ? fmtDate(lr.timestamp) : '—'} />
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          {lr && (
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-secondary)',
            }}>
              {fmtTime(lr.timestamp)} · {fmtDuration(lr.duration_seconds)}
            </span>
          )}
          {lr && <OutcomePill outcome={lr.outcome} />}
        </div>
      </StatCard>

      {/* Error Rate 30d — with mini sparkline */}
      <StatCard label="Error Rate 30d">
        <BigNum n={fmtPct(summary.error_rate_30d)} />
        <LineChart width={80} height={32} data={sparkPoints} style={{ overflow: 'visible' }}>
          <Line
            type="monotone"
            dataKey="rate"
            stroke="var(--stage-2)"
            strokeWidth={1.5}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </StatCard>

      {/* Sources Monitored */}
      <StatCard label="Sources Monitored">
        <BigNum n={summary.total_sources_monitored ?? 0} />
      </StatCard>

      {/* LLM Schema Failures */}
      <StatCard label="LLM Schema Failures 30d" alert={llmWarn}>
        <BigNum
          n={summary.llm_schema_failures_30d ?? 0}
          color={llmWarn ? 'var(--state-warn)' : undefined}
        />
      </StatCard>

      {/* Cross-Encoder Truncations */}
      <StatCard label="Cross-Encoder Truncations 30d" alert={ceWarn}>
        <BigNum
          n={summary.cross_encoder_truncations_30d ?? 0}
          color={ceWarn ? 'var(--state-warn)' : undefined}
        />
      </StatCard>
    </div>
  );
}

// ── Row 2 left: Error rate area chart ─────────────────────────────────────────

const chartTooltipStyle = {
  background: 'var(--bg-secondary)',
  border: '1px solid var(--rule)',
  fontFamily: '"DM Mono", monospace',
  fontSize: '10px',
  color: 'var(--text-secondary)',
};

function ErrorRateChart({ data }) {
  return (
    <div style={{ flex: 1, padding: '14px 16px', minWidth: 0 }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px',
        color: 'var(--text-tertiary)', letterSpacing: '0.07em', marginBottom: '10px',
      }}>
        DAILY ERROR RATE — 30D
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 4, right: 50, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fontFamily: '"DM Mono", monospace', fontSize: '9px', fill: 'var(--text-tertiary)' }}
            interval={6}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fontFamily: '"DM Mono", monospace', fontSize: '9px', fill: 'var(--text-tertiary)' }}
            tickFormatter={v => `${Math.round(v * 100)}%`}
            axisLine={false}
            tickLine={false}
            width={34}
          />
          <RechartsTooltip
            contentStyle={chartTooltipStyle}
            formatter={v => [v != null ? `${(v * 100).toFixed(1)}%` : '—', 'error rate']}
            labelStyle={{ color: 'var(--text-tertiary)' }}
            cursor={{ stroke: 'var(--rule-accent)', strokeWidth: 1 }}
          />
          <ReferenceLine
            y={0.15}
            stroke="var(--state-warn)"
            strokeDasharray="3 3"
            label={{
              value: 'WARN',
              position: 'right',
              style: { fontFamily: '"DM Mono", monospace', fontSize: '8px', fill: 'var(--state-warn)' },
            }}
          />
          <ReferenceLine
            y={0.30}
            stroke="var(--state-alert)"
            strokeDasharray="3 3"
            label={{
              value: 'ALERT',
              position: 'right',
              style: { fontFamily: '"DM Mono", monospace', fontSize: '8px', fill: 'var(--state-alert)' },
            }}
          />
          <Area
            type="monotone"
            dataKey="rate"
            stroke="var(--stage-2)"
            fill="rgba(201,64,32,0.10)"
            strokeWidth={1.5}
            connectNulls
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Row 2 right: Consecutive failures list ────────────────────────────────────

function ConsecutiveFailures({ failures }) {
  const hasFails = failures && failures.length > 0;
  return (
    <div style={{
      flex: 1, padding: '14px 16px',
      borderLeft: '1px solid var(--rule)',
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px',
        color: 'var(--text-tertiary)', letterSpacing: '0.07em', marginBottom: '10px',
      }}>
        CONSECUTIVE SOURCE FAILURES
      </div>

      {!hasFails ? (
        <div style={{
          height: '160px', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--font-display)', fontSize: '18px', color: 'var(--state-ok)',
          letterSpacing: '0.05em',
        }}>
          ALL SOURCES HEALTHY
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {['Source ID', 'Failures', 'Status'].map(h => (
                <th key={h} style={{
                  textAlign: 'left', padding: '5px 8px',
                  fontFamily: 'var(--font-display)', fontSize: '11px', letterSpacing: '0.07em',
                  color: 'var(--text-tertiary)', borderBottom: '1px solid var(--rule)',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {failures.map(f => {
              const critical = (f.consecutive_failures ?? 0) >= 3;
              return (
                <tr key={f.source_id}>
                  <td style={{
                    padding: '5px 8px', borderBottom: '1px solid var(--rule)',
                    fontFamily: 'var(--font-mono)', fontSize: '11px',
                    color: critical ? 'var(--state-error)' : 'var(--text-primary)',
                  }}>
                    {f.source_id}
                  </td>
                  <td style={{
                    padding: '5px 8px', borderBottom: '1px solid var(--rule)',
                    fontFamily: 'var(--font-mono)', fontSize: '11px',
                    color: critical ? 'var(--state-error)' : 'var(--text-secondary)',
                  }}>
                    {f.consecutive_failures}
                  </td>
                  <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--rule)' }}>
                    <OutcomePill outcome="error" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Row 3: Run log table ───────────────────────────────────────────────────────

const TH_STYLE = {
  textAlign: 'left', padding: '7px 10px',
  fontFamily: 'var(--font-display)', fontSize: '11px', letterSpacing: '0.07em',
  color: 'var(--text-tertiary)', borderBottom: '1px solid var(--rule)',
  whiteSpace: 'nowrap', userSelect: 'none',
};
const TD_STYLE = {
  padding: '7px 10px', borderBottom: '1px solid var(--rule)',
  fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)',
  whiteSpace: 'nowrap',
};

// Per-source sub-table rendered inline when a run row is expanded
function ExpandedSourceRows({ runId }) {
  const { data: raw, isLoading } = useRun(runId);
  const rows = Array.isArray(raw?.data) ? raw.data : (Array.isArray(raw) ? raw : []);

  const loadingTd = { ...TD_STYLE, color: 'var(--text-tertiary)', fontSize: '10px' };
  if (isLoading) {
    return (
      <tr>
        <td colSpan={8} style={{ ...loadingTd, background: 'var(--bg-accent)', paddingLeft: '32px' }}>
          LOADING…
        </td>
      </tr>
    );
  }
  if (!rows.length) {
    return (
      <tr>
        <td colSpan={8} style={{ ...loadingTd, background: 'var(--bg-accent)', paddingLeft: '32px' }}>
          No per-source data.
        </td>
      </tr>
    );
  }
  return rows.map((r, i) => (
    <tr key={i} style={{ background: 'var(--bg-accent)' }}>
      <td style={{ ...TD_STYLE, paddingLeft: '28px', color: 'var(--text-tertiary)', fontSize: '10px' }}>
        ↳ {r.source_id ?? '—'}
      </td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>
        {fmtTime(r.timestamp ?? r.run_at)}
      </td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>
        {fmtDuration(r.duration_seconds)}
      </td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>
        Stg {r.stage_reached ?? '—'}
      </td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>—</td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>—</td>
      <td style={{ ...TD_STYLE, fontSize: '10px', color: 'var(--text-tertiary)' }}>
        {r.triggered_pages && r.triggered_pages !== '[]' ? '✓' : '—'}
      </td>
      <td style={{ ...TD_STYLE }}>
        <OutcomePill outcome={r.outcome} />
      </td>
    </tr>
  ));
}

const PAGE_SIZE = 10;

const PAGER_BTN = {
  background: 'none', border: '1px solid var(--rule)',
  fontFamily: 'var(--font-mono)', fontSize: '9px', letterSpacing: '0.05em',
  color: 'var(--text-secondary)', cursor: 'pointer', padding: '3px 10px',
};

function RunLogTable({ runs }) {
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
    <div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
        letterSpacing: '0.07em', padding: '10px 16px', borderBottom: '1px solid var(--rule)',
      }}>
        RUN LOG — {runs.length} PIPELINE RUN{runs.length !== 1 ? 'S' : ''}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '700px' }}>
          <thead>
            <tr>
              <th style={TH_STYLE}>Run ID</th>
              <th style={TH_STYLE}>Start Time</th>
              <th style={TH_STYLE}>Duration</th>
              <th style={TH_STYLE}>Checked</th>
              <th style={TH_STYLE}>Changed</th>
              <th style={TH_STYLE}>Errored</th>
              <th style={TH_STYLE}>Alerts</th>
              <th style={TH_STYLE}>Status</th>
            </tr>
          </thead>
          <tbody>
            {pageRuns.map(r => {
              const isExp = expanded.has(r.run_id);
              return (
                <React.Fragment key={r.run_id}>
                  <tr
                    onClick={() => toggle(r.run_id)}
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={e => { if (!isExp) e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; }}
                    onMouseLeave={e => { if (!isExp) e.currentTarget.style.background = 'transparent'; }}
                  >
                    <td style={{ ...TD_STYLE, color: 'var(--text-primary)' }}>
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: '9px',
                        color: 'var(--text-tertiary)', marginRight: '6px',
                      }}>
                        {isExp ? '▾' : '▸'}
                      </span>
                      {r.run_id}
                    </td>
                    <td style={TD_STYLE}>{fmtDate(r.start_time)} {fmtTime(r.start_time)}</td>
                    <td style={TD_STYLE}>{fmtDuration(r.total_duration)}</td>
                    <td style={TD_STYLE}>{r.sources_checked ?? '—'}</td>
                    <td style={TD_STYLE}>{r.sources_completed ?? '—'}</td>
                    <td style={{
                      ...TD_STYLE,
                      color: (r.sources_errored ?? 0) > 0 ? 'var(--state-alert)' : 'var(--text-secondary)',
                    }}>
                      {r.sources_errored ?? 0}
                    </td>
                    <td style={TD_STYLE}>{r.alerts_generated ?? 0}</td>
                    <td style={TD_STYLE}><OutcomePill outcome={r.status} /></td>
                  </tr>
                  {isExp && <ExpandedSourceRows runId={r.run_id} />}
                </React.Fragment>
              );
            })}
            {!pageRuns.length && (
              <tr>
                <td colSpan={8} style={{ ...TD_STYLE, color: 'var(--text-tertiary)', padding: '20px 16px' }}>
                  No run data available.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px',
          padding: '8px 16px', borderTop: '1px solid var(--rule)',
          fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-tertiary)',
        }}>
          <button
            style={PAGER_BTN}
            disabled={page === 0}
            onClick={() => setPage(p => Math.max(0, p - 1))}
          >
            ‹ PREV
          </button>
          <span>{page + 1} / {totalPages}</span>
          <button
            style={PAGER_BTN}
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
          >
            NEXT ›
          </button>
          <span style={{ marginLeft: 'auto' }}>
            {runs.length} TOTAL
          </span>
        </div>
      )}
    </div>
  );
}

// ── Row 4: Ingestion health strip ─────────────────────────────────────────────

function IngestionStrip({ ingestion }) {
  const stats = [
    { label: 'Last Ingestion',     value: ingestion ? `${fmtDate(ingestion.start_time)} ${fmtTime(ingestion.start_time)}` : '—' },
    { label: 'Pages Ingested',     value: ingestion?.pages_ingested   ?? '—' },
    { label: 'Pages Skipped',      value: ingestion?.pages_skipped    ?? '—' },
    { label: 'Stubs Detected',     value: ingestion?.stubs            ?? '—' },
    { label: 'Duplicates Found',   value: ingestion?.duplicates       ?? '—' },
    { label: 'Keyphrases Indexed', value: ingestion?.keyphrases_total ?? '—' },
  ];

  return (
    <div style={{
      borderTop: '1px solid var(--rule)',
      display: 'flex', alignItems: 'stretch',
      flexShrink: 0,
    }}>
      <div style={{
        padding: '10px 14px',
        borderRight: '1px solid var(--rule)',
        display: 'flex', alignItems: 'center',
        fontFamily: 'var(--font-display)', fontSize: '11px',
        letterSpacing: '0.08em', color: 'var(--text-tertiary)',
        whiteSpace: 'nowrap',
      }}>
        INGESTION HEALTH
      </div>
      <div style={{ display: 'flex', flex: 1 }}>
        {stats.map(({ label, value }) => (
          <div key={label} style={{
            flex: 1, padding: '10px 14px',
            borderRight: '1px solid var(--rule)',
            display: 'flex', flexDirection: 'column', gap: '3px',
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: '9px',
              color: 'var(--text-tertiary)', letterSpacing: '0.07em',
              textTransform: 'uppercase', whiteSpace: 'nowrap',
              overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {label}
            </div>
            <div style={{
              fontFamily: 'var(--font-display)', fontSize: '20px',
              color: 'var(--text-primary)', lineHeight: 1,
            }}>
              {value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Health section ───────────────────────────────────────────────────────

export default function Health() {
  const { data: summaryRaw }   = useHealthSummary();
  const { data: runsRaw }      = useHealthRuns();
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Section header */}
      <div style={{
        height: '80px', minHeight: '80px',
        display: 'flex', alignItems: 'flex-end',
        padding: '0 24px 12px',
        borderBottom: '1px solid var(--rule)',
        flexShrink: 0,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: '42px',
            lineHeight: 1, letterSpacing: '0.04em',
          }}>
            HEALTH
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: '11px',
            color: 'var(--text-secondary)', marginTop: '2px',
          }}>
            System Health Log
          </div>
        </div>
      </div>

      {/* Scrollable body */}
      <div style={{ flex: 1, overflowY: 'auto' }}>

        {/* Row 1: five stat cards */}
        <StatusStrip summary={summary} sparkData={sparkData} />

        {/* Row 2: area chart + consecutive failures */}
        <div style={{
          display: 'flex',
          borderBottom: '1px solid var(--rule)',
        }}>
          <ErrorRateChart data={sparkData} />
          <ConsecutiveFailures failures={summary?.sources_with_consecutive_failures} />
        </div>

        {/* Row 3: paginated run log */}
        <div style={{ borderBottom: '1px solid var(--rule)' }}>
          <RunLogTable runs={runs} />
        </div>

        {/* Row 4: ingestion health strip */}
        <IngestionStrip ingestion={ingestion} />

      </div>

      {/* alertPulse animation for warning cards */}
      <style>{`
        @keyframes alertPulse {
          0%, 100% { opacity: 1.0; }
          50%       { opacity: 0.7; }
        }
        .alert-pulse-border {
          animation: alertPulse 2s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
}
